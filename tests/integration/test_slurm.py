"""Real Slurm acceptance, enabled only on an explicitly provisioned test site."""

import csv
import json
import os
import shutil
import subprocess
import time
from collections import Counter

import pytest
from test_workflow import ROOT, arguments
from test_workflow import runtime as runtime_fixture

from reprohpc.cli import run
from reprohpc.io import read_json, sha256, write_csv, write_json
from reprohpc.provenance import compare, verify_run

pytestmark = [pytest.mark.integration, pytest.mark.slurm]
runtime = runtime_fixture


@pytest.fixture(autouse=True)
def require_site():
    if os.environ.get("REPROHPC_SLURM") != "1":
        pytest.skip("Set REPROHPC_SLURM=1 only on the disposable/approved acceptance site")


def command(*argv):
    return subprocess.check_output(argv, text=True, timeout=30).strip()


def wait_accounting(job, expected, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = command(
            "sacct",
            "-n",
            "-P",
            "-j",
            job,
            "--format=JobID,State,ExitCode,MaxRSS,ReqMem,AllocCPUS,TimelimitRaw",
        )
        records = [line.split("|") for line in text.splitlines()]
        if any(row[0] == job and row[1] == expected for row in records):
            return text
        if any(
            row[0] == job
            and row[1] in ("COMPLETED", "FAILED", "OUT_OF_MEMORY", "TIMEOUT")
            and row[1] != expected
            for row in records
        ):
            raise AssertionError(f"Unexpected accounting: {text}")
        time.sleep(2)
    raise AssertionError(f"Accounting did not show {expected} for {job}: {text}")


@pytest.mark.parametrize("fault,state", [("oom", "OUT_OF_MEMORY"), ("timeout", "TIMEOUT")])
def test_enforced_limits(tmp_path, fault, state):
    script = tmp_path / f"{fault}.sh"
    # Fault drills intentionally exceed limits; they are never performance workloads.
    body = "exec python -c 'x=bytearray(512*1024*1024)'" if fault == "oom" else "exec sleep 120"
    script.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
    job = command(
        "sbatch",
        "--parsable",
        "--partition=demo",
        "--account=research",
        "--cpus-per-task=1",
        "--mem=128M",
        "--time=00:01:00",
        f"--output={tmp_path / 'job.log'}",
        str(script),
    ).split(";")[0]
    try:
        accounting = wait_accounting(job, state)
        assert not command("squeue", "-h", "-j", job)
        (tmp_path / "accounting.tsv").write_text(accounting + "\n")
        write_json(
            tmp_path / "evidence.json",
            {"fault": fault, "job_id": job, "state": state, "real_scheduler": True},
        )
    finally:
        subprocess.run(["scancel", job], capture_output=True, check=False, timeout=15)


def test_ten_batches_native_arrays_and_accounting(tmp_path, runtime, monkeypatch):
    monkeypatch.chdir(ROOT)
    dataset = tmp_path / "data"
    shutil.copytree(ROOT / "data/demo/1.0.0", dataset)
    with (dataset / "samples.csv").open() as stream:
        rows = list(csv.DictReader(stream))[:10]
    write_csv(dataset / "samples.csv", list(rows[0]), rows)
    descriptor = read_json(dataset / "dataset.json")
    descriptor["manifest_sha256"] = sha256(dataset / "samples.csv")
    descriptor["description"] += " Acceptance subset: ten real image files."
    write_json(dataset / "dataset.json", descriptor)
    args = arguments(
        tmp_path,
        runtime,
        "arrays",
        [
            "--profile",
            "slurm",
            "--account",
            "research",
            "--analysis-memory",
            "512 MB",
            "--dataset",
            str(dataset / "dataset.json"),
            "--input-manifest",
            str(dataset / "samples.csv"),
        ],
    )
    assert run(args) == 0
    assert verify_run(args.outdir)["verified"]
    tasks = [
        json.loads(line)
        for line in (args.outdir / "provenance/tasks.jsonl").read_text().splitlines()
    ]
    analysis = [t for t in tasks if t["process"].endswith("ANALYZE_BATCH")]
    assert len(analysis) == 10
    groups = Counter(t["native_id"].split("_")[0] for t in analysis)
    assert sorted(groups.values()) == [2, 4, 4]
    assert all("_" in t["native_id"] for t in analysis)
    for task in analysis:
        assert int(task["requested"]["cpus"]) == 1
        assert int(task["requested"]["memory"]) == 512 * 1024**2
        metadata = task["scientific_metadata"]
        assert metadata["uid"] == os.getuid() != 0
        assert metadata["observed_threads"] == metadata["threads"] == 1
        assert f"{metadata['array_job_id']}_{metadata['array_task_id']}" == task["native_id"]
        records = task["usage"]["slurm_records"]
        assert any(row[0].endswith(".batch") and row[6] for row in records), records
        parent = next(row for row in records if row[0] == task["native_id"])
        assert parent[1] == "COMPLETED" and parent[4] == "1" and parent[9] == "10"
        for log in task["logs"]:
            if log.endswith("command.sh"):
                assert "sbatch" not in (args.outdir / log).read_text()
    write_json(
        tmp_path / "arrays-evidence.json",
        {
            "sif_sha256": runtime[1],
            "array_sizes": sorted(groups.values()),
            "job_groups": dict(groups),
            "analysis_tasks": len(analysis),
            "accounting": True,
            "resource_requests": True,
        },
    )
    resumed = arguments(
        tmp_path,
        runtime,
        "arrays-resumed",
        [
            "--profile",
            "slurm",
            "--account",
            "research",
            "--analysis-memory",
            "512 MB",
            "--dataset",
            str(dataset / "dataset.json"),
            "--input-manifest",
            str(dataset / "samples.csv"),
            "--resume",
            read_json(args.outdir / "provenance/run.json")["nextflow_session_id"],
        ],
    )
    assert run(resumed) == 0
    cached = [
        json.loads(line)
        for line in (resumed.outdir / "provenance/tasks.jsonl").read_text().splitlines()
        if json.loads(line)["process"].endswith("ANALYZE_BATCH")
    ]
    previous = {t["hash"]: t for t in analysis}
    assert len(cached) == len(previous)
    for task in cached:
        assert task["status"] == "CACHED"
        origin = previous[task["hash"]]
        assert task["usage"]["accounting_scope"] == "origin_run"
        assert task["usage"]["slurm_records"] == origin["usage"]["slurm_records"]
        assert task["usage"]["accounting_captured_at"] == origin["usage"]["accounting_captured_at"]
        assert task["origin_run_id"] == origin["origin_run_id"]
    queried = {
        line.split("|", 1)[0].split(".")[0]
        for line in (resumed.outdir / "provenance/sacct.tsv").read_text().splitlines()
    }
    assert not queried.intersection(t["native_id"] for t in cached)


def test_array_failure_retries_only_one_scalar_task(tmp_path, runtime, monkeypatch):
    monkeypatch.chdir(ROOT)
    site = tmp_path / "retry.config"
    site.write_text(
        "process { withName: ANALYZE_BATCH { beforeScript = { "
        "task.tag == 'batch-000003' && task.attempt == 1 ? 'exit 75' : '' } } }\n"
    )
    args = arguments(
        tmp_path,
        runtime,
        "retry",
        [
            "--profile",
            "slurm",
            "--account",
            "research",
            "--analysis-memory",
            "512 MB",
            "--site-config",
            str(site),
        ],
    )
    assert run(args) == 0
    tasks = [
        json.loads(line)
        for line in (args.outdir / "provenance/tasks.jsonl").read_text().splitlines()
    ]
    analysis = [t for t in tasks if t["process"].endswith("ANALYZE_BATCH")]
    failed = [t for t in analysis if t["exit_code"] == 75]
    retried = [t for t in analysis if t["attempt"] == 2]
    assert len(analysis) == 13 and len(failed) == len(retried) == 1
    assert "_" in failed[0]["native_id"] and "_" not in retried[0]["native_id"]
    assert retried[0]["status"] == "COMPLETED"
    assert retried[0]["scientific_metadata"]["batch_id"] == "batch-000003"
    assert verify_run(args.outdir)["verified"]
    write_json(
        tmp_path / "evidence.json",
        {
            "retry_individual": True,
            "failed_native_id": failed[0]["native_id"],
            "retried_native_id": retried[0]["native_id"],
            "sif_sha256": runtime[1],
        },
    )


@pytest.mark.parametrize("fault,state", [("oom", "OUT_OF_MEMORY"), ("timeout", "TIMEOUT")])
def test_workflow_limit_failure_and_corrected_resume(tmp_path, runtime, monkeypatch, fault, state):
    """Exceed a real allocation, then change only its limits before resume."""
    monkeypatch.chdir(ROOT)
    site = tmp_path / "limits.config"
    # The fault is stable across resume; larger resources allow the same command
    # and scientific code to finish. Only this disposable job allocates/sleeps.
    injected = 'python -c "x=bytearray(512*1024*1024)"' if fault == "oom" else "sleep 120"
    # Nextflow normally requests USR2 before the time limit, yielding FAILED
    # before Slurm records TIMEOUT. For this enforcement drill only, request a
    # harmless CONT notification so the scheduler reaches its actual hard limit.
    signal_override = (
        "clusterOptions = '--account=research --signal=B:CONT@1'; " if fault == "timeout" else ""
    )
    site.write_text(
        "process { withName: ANALYZE_BATCH { " + signal_override + "beforeScript = { "
        "task.tag == 'batch-000000' ? '" + injected + "' : 'sleep 8' } } }\n"
    )
    common = [
        "--profile",
        "slurm",
        "--account",
        "research",
        "--site-config",
        str(site),
        "--batch-size",
        "4",
    ]
    limits = ["--analysis-memory", "128 MB"] if fault == "oom" else ["--analysis-time", "1 min"]
    args = arguments(tmp_path, runtime, "failed", [*common, *limits])
    assert run(args) == 4
    failed = read_json(args.outdir / "provenance/run.json")
    assert failed["status"]["status"] == "failed"
    tasks = [
        json.loads(line)
        for line in (args.outdir / "provenance/tasks.jsonl").read_text().splitlines()
    ]
    analysis = [t for t in tasks if t["process"].endswith("ANALYZE_BATCH")]
    assert any(any(row[1] == state for row in t["usage"]["slurm_records"]) for t in analysis), tasks
    ids = sorted({t["native_id"].split("_")[0] for t in tasks if t["native_id"]})
    # Only this run's allocation IDs are queried. A missing purged job is also terminal.
    queue = subprocess.run(["squeue", "-h", "-j", ",".join(ids)], capture_output=True, text=True)
    assert not queue.stdout.strip(), queue.stdout
    correction = ["--analysis-memory", "2 GB"] if fault == "oom" else ["--analysis-time", "3 min"]
    resumed = arguments(
        tmp_path,
        runtime,
        "corrected",
        [*common, *correction, "--resume", failed["nextflow_session_id"]],
    )
    assert run(resumed) == 0
    assert verify_run(resumed.outdir)["verified"]
    assert compare(ROOT / "tests/expected/demo", resumed.outdir)["equivalent"]
    write_json(
        tmp_path / "evidence.json",
        {
            "fault": fault,
            "state": state,
            "sif_sha256": runtime[1],
            "session_id": failed["nextflow_session_id"],
            "failed_exit_code": 4,
            "corrected_exit_code": 0,
            "remaining_jobs": [],
        },
    )
