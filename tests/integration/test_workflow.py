"""Actual Nextflow + SIF acceptance; never silently replace the runtime."""

import json
import os
from pathlib import Path

import pytest

from reprohpc.cli import parser, run
from reprohpc.io import read_json, sha256
from reprohpc.provenance import compare, verify_run

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def runtime():
    value = os.environ.get("REPROHPC_SIF")
    if not value:
        pytest.skip("REPROHPC_SIF is required for actual Apptainer integration; no mocked fallback")
    sif = Path(value).resolve()
    assert sif.is_file(), "Configured integration SIF must exist"
    return sif, sha256(sif)


def arguments(tmp_path, runtime, name, extra=()):
    sif, digest = runtime
    return parser().parse_args(
        [
            "run",
            "--params-file",
            str(ROOT / "params/demo.yaml"),
            "--outdir",
            str(tmp_path / name),
            "--work-dir",
            str(tmp_path / "work"),
            "--launch-dir",
            str(tmp_path / "launch"),
            "--sif",
            str(sif),
            "--sif-sha256",
            digest,
            "--batch-size",
            "1",
            *extra,
        ]
    )


def test_real_workflow_repeat_resume_and_export(tmp_path, runtime, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert run(arguments(tmp_path, runtime, "first")) == 0
    assert compare(ROOT / "tests/expected/demo", tmp_path / "first")["equivalent"]
    assert verify_run(tmp_path / "first")["verified"]
    first = read_json(tmp_path / "first/provenance/run.json")
    assert (
        run(arguments(tmp_path, runtime, "resumed", ["--resume", first["nextflow_session_id"]]))
        == 0
    )
    tasks = [
        json.loads(line)
        for line in (tmp_path / "resumed/provenance/tasks.jsonl").read_text().splitlines()
    ]
    analysis = [task for task in tasks if task["process"].endswith("ANALYZE_BATCH")]
    assert len(analysis) == 12 and all(task["status"] == "CACHED" for task in analysis)
    assert compare(tmp_path / "first", tmp_path / "resumed", exact=True)["equivalent"]
    damaged = (
        Path(analysis[0]["origin_work_dir"])
        / "result/samples"
        / analysis[0]["sample_ids"][0]
        / "mask.npy"
    )
    assert damaged.resolve().is_relative_to((tmp_path / "work").resolve())
    damaged.unlink()
    assert (
        run(arguments(tmp_path, runtime, "repaired", ["--resume", first["nextflow_session_id"]]))
        == 0
    )
    repaired = [
        json.loads(line)
        for line in (tmp_path / "repaired/provenance/tasks.jsonl").read_text().splitlines()
    ]
    states = [t["status"] for t in repaired if t["process"].endswith("ANALYZE_BATCH")]
    assert states.count("COMPLETED") == 1 and states.count("CACHED") == 11
    assert compare(tmp_path / "first", tmp_path / "repaired", exact=True)["equivalent"]
    assert run(arguments(tmp_path, runtime, "independent")) == 0
    assert compare(tmp_path / "first", tmp_path / "independent", exact=True)["equivalent"]
    from reprohpc.archive import export_run, extract

    destination = tmp_path / "public.tar.gz"
    export_run(tmp_path / "first", destination)
    extract(destination, tmp_path / "public")
    assert verify_run(tmp_path / "public")["verified"]


def test_real_parameter_invalidation(tmp_path, runtime, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert run(arguments(tmp_path, runtime, "first")) == 0
    session = read_json(tmp_path / "first/provenance/run.json")["nextflow_session_id"]
    assert (
        run(arguments(tmp_path, runtime, "changed", ["--resume", session, "--threshold", "200"]))
        == 0
    )
    tasks = [
        json.loads(line)
        for line in (tmp_path / "changed/provenance/tasks.jsonl").read_text().splitlines()
    ]
    assert all(t["status"] == "COMPLETED" for t in tasks if t["process"].endswith("ANALYZE_BATCH"))


@pytest.mark.parametrize("fault,expected", [("temporary", 0), ("deterministic", 4)])
def test_real_retry_policy(tmp_path, runtime, monkeypatch, fault, expected):
    monkeypatch.chdir(ROOT)
    site = tmp_path / "fault.config"
    expression = "task.attempt == 1 ? 'exit 75' : ''" if fault == "temporary" else "'exit 23'"
    site.write_text(
        "process { withName: ANALYZE_BATCH { beforeScript = { " + expression + " } } }\n"
    )
    args = arguments(tmp_path, runtime, "fault", ["--site-config", str(site), "--batch-size", "16"])
    assert run(args) == expected
    status = read_json(args.outdir / "status.json")
    assert status["status"] == ("success" if expected == 0 else "failed")
    tasks = [
        json.loads(line)
        for line in (args.outdir / "provenance/tasks.jsonl").read_text().splitlines()
    ]
    analysis = sorted(
        (t for t in tasks if t["process"].endswith("ANALYZE_BATCH")), key=lambda t: t["attempt"]
    )
    if fault == "temporary":
        assert [t["attempt"] for t in analysis] == [1, 2]
        assert [t["exit_code"] for t in analysis] == [75, 0]
        assert compare(ROOT / "tests/expected/demo", args.outdir)["equivalent"]
    else:
        assert len(analysis) == 1 and analysis[0]["exit_code"] == 23
        assert not (args.outdir / "summary").exists()
