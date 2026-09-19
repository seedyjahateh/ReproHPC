"""Actual driver signal handling and engine cache recovery."""

import csv
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_workflow import ROOT, arguments
from test_workflow import runtime as runtime_fixture

from reprohpc.cli import run
from reprohpc.io import read_json
from reprohpc.provenance import compare

runtime = runtime_fixture
pytestmark = pytest.mark.integration


def test_handled_interrupt_preserves_and_resumes(tmp_path, runtime, monkeypatch):
    monkeypatch.chdir(ROOT)
    site = tmp_path / "interrupt.config"
    site.write_text("process { withName: ANALYZE_BATCH { beforeScript = 'sleep 5' } }\n")
    output = tmp_path / "interrupted"
    argv = [
        sys.executable,
        str(ROOT / "reprohpc"),
        "run",
        "--params-file",
        str(ROOT / "params/demo.yaml"),
        "--sif",
        str(runtime[0]),
        "--sif-sha256",
        runtime[1],
        "--outdir",
        str(output),
        "--work-dir",
        str(tmp_path / "work"),
        "--launch-dir",
        str(tmp_path / "launch"),
        "--site-config",
        str(site),
        "--batch-size",
        "1",
    ]
    with (tmp_path / "launcher.log").open("w") as log:
        child = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 300
            completed = set()
            while time.monotonic() < deadline:
                trace = output / "provenance/trace.tsv"
                if trace.is_file():
                    # Snapshot complete lines: Nextflow can create the trace before
                    # writing its header, and appends while this observer reads.
                    lines = trace.read_text().splitlines(keepends=True)
                    completed = {
                        t["tag"]
                        for t in csv.DictReader(
                            [line for line in lines if line.endswith("\n")], delimiter="\t"
                        )
                        if t.get("process", "").endswith("ANALYZE_BATCH")
                        and t.get("status") == "COMPLETED"
                    }
                if len(completed) >= 2:
                    break
                assert child.poll() is None, "Driver ended before interruption checkpoint"
                time.sleep(0.5)
            assert 2 <= len(completed) < 12, "Need real completed and unfinished batches"
            interrupted_at = time.monotonic()
            child.send_signal(signal.SIGINT)
            assert child.wait(timeout=90) == 130
            cleanup_seconds = time.monotonic() - interrupted_at
            assert cleanup_seconds <= 60
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=90)
    record = read_json(output / "provenance/run.json")
    assert record["status"]["status"] == "cancelled"
    assert record["nextflow_session_id"]
    assert not (tmp_path / "launch/active.lock").exists()
    args = arguments(
        tmp_path,
        runtime,
        "recovered",
        ["--site-config", str(site), "--resume", record["nextflow_session_id"]],
    )
    assert run(args) == 0
    tasks = [
        json.loads(line)
        for line in (args.outdir / "provenance/tasks.jsonl").read_text().splitlines()
    ]
    cached_batches = {
        t["scientific_metadata"]["batch_id"]
        for t in tasks
        if t["process"].endswith("ANALYZE_BATCH") and t["status"] == "CACHED"
    }
    assert completed <= cached_batches
    assert compare(ROOT / "tests/expected/demo", args.outdir)["equivalent"]
    from reprohpc.io import write_json

    write_json(
        tmp_path / "evidence.json",
        {
            "completed_before_interrupt": sorted(completed),
            "cached_after_resume": sorted(cached_batches),
            "cleanup_seconds": cleanup_seconds,
            "exit_code": 130,
        },
    )


def test_hard_kill_manual_cleanup_retains_origin(tmp_path, runtime, monkeypatch):
    monkeypatch.chdir(ROOT)
    site = tmp_path / "hard-kill.config"
    site.write_text("process { withName: ANALYZE_BATCH { beforeScript = 'sleep 5' } }\n")
    output = tmp_path / "killed"
    argv = [
        sys.executable,
        str(ROOT / "reprohpc"),
        "run",
        "--params-file",
        str(ROOT / "params/demo.yaml"),
        "--sif",
        str(runtime[0]),
        "--sif-sha256",
        runtime[1],
        "--outdir",
        str(output),
        "--work-dir",
        str(tmp_path / "work"),
        "--launch-dir",
        str(tmp_path / "launch"),
        "--site-config",
        str(site),
        "--batch-size",
        "1",
    ]
    engine_pid = None
    with (tmp_path / "launcher.log").open("w") as log:
        child = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 300
            completed = set()
            while time.monotonic() < deadline:
                trace = output / "provenance/trace.tsv"
                if trace.exists():
                    # Snapshot complete lines: Nextflow can create the trace before
                    # writing its header, and appends while this observer reads.
                    lines = trace.read_text().splitlines(keepends=True)
                    completed = {
                        t["tag"]
                        for t in csv.DictReader(
                            [line for line in lines if line.endswith("\n")], delimiter="\t"
                        )
                        if t.get("process", "").endswith("ANALYZE_BATCH")
                        and t.get("status") == "COMPLETED"
                    }
                if len(completed) >= 2:
                    break
                assert child.poll() is None
                time.sleep(0.5)
            assert 2 <= len(completed) < 12
            driver = (output / "logs/driver.log").read_text()
            engine_pid = int(re.search(r"Process: (\d+)@", driver).group(1))
            assert os.getpgid(engine_pid) == engine_pid
            assert Path(f"/proc/{engine_pid}").stat().st_uid == os.getuid()
            assert b"nextflow" in Path(f"/proc/{engine_pid}/cmdline").read_bytes()
            session = re.search(r"Session UUID: ([0-9a-f-]{36})", driver).group(1)
            origin = read_json(output / "status.json")["run_id"]
            child.kill()
            assert child.wait(timeout=10) == -signal.SIGKILL
            # Explicit operator recovery of this test's orphaned engine only.
            os.killpg(engine_pid, signal.SIGTERM)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                stat = Path(f"/proc/{engine_pid}/stat")
                if not stat.exists() or stat.read_text().split(")", 1)[1].split()[0] == "Z":
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("Orphan engine did not stop within 60 seconds")
            lock = tmp_path / "launch/active.lock"
            assert int(lock.read_text()) == child.pid
            lock.unlink()
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=90)
    args = arguments(
        tmp_path, runtime, "recovered", ["--site-config", str(site), "--resume", session]
    )
    assert run(args) == 0
    tasks = [
        json.loads(line)
        for line in (args.outdir / "provenance/tasks.jsonl").read_text().splitlines()
    ]
    cached = [
        t for t in tasks if t["process"].endswith("ANALYZE_BATCH") and t["status"] == "CACHED"
    ]
    assert completed <= {t["scientific_metadata"]["batch_id"] for t in cached}
    assert all(t["origin_run_id"] == origin for t in cached)
    assert compare(ROOT / "tests/expected/demo", args.outdir)["equivalent"]


@pytest.mark.parametrize(
    "fault,expected_code",
    [
        ("rm -- result/task.json", 4),
        ("rm -- result/samples/*/mask.npy", 4),
        ("find result/samples -name mask.npy -exec truncate -s 1 {} +", 5),
    ],
)
def test_missing_output_cannot_be_success(tmp_path, runtime, monkeypatch, fault, expected_code):
    monkeypatch.chdir(ROOT)
    site = tmp_path / "missing.config"
    # These are fixed, task-relative paths in disposable work dirs created by this test.
    site.write_text("process { withName: ANALYZE_BATCH { afterScript = '" + fault + "' } }\n")
    args = arguments(
        tmp_path, runtime, "missing", ["--site-config", str(site), "--batch-size", "16"]
    )
    assert run(args) == expected_code
    status = read_json(args.outdir / "status.json")
    assert status["status"] != "success" and status["exit_code"] == expected_code
    if expected_code == 5:
        assert status["status"] == "finalization-failed" and status["message"]
