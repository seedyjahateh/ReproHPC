"""Real content mutation and SIF identity tests, including paths containing spaces."""

import csv
import json
import shutil
import subprocess

import cv2
import numpy as np
import pytest
from test_workflow import ROOT, arguments
from test_workflow import runtime as runtime_fixture

from reprohpc.cli import run
from reprohpc.errors import ReproError
from reprohpc.io import atomic_bytes, read_json, sha256, write_csv, write_json
from reprohpc.provenance import compare

runtime = runtime_fixture
pytestmark = pytest.mark.integration


def states(output):
    return [
        json.loads(line)
        for line in (output / "provenance/tasks.jsonl").read_text().splitlines()
        if json.loads(line)["process"].endswith("ANALYZE_BATCH")
    ]


def test_content_reference_and_sif_invalidation(tmp_path, runtime, monkeypatch):
    monkeypatch.chdir(ROOT)
    data = tmp_path / "inputs with spaces"
    ref = tmp_path / "reference with spaces"
    shutil.copytree(ROOT / "data/demo/1.0.0", data)
    shutil.copytree(ROOT / "references/calibration/1.0.0", ref)
    with (data / "samples.csv").open() as stream:
        rows = list(csv.DictReader(stream))[:3]
    original = data / rows[0]["path"]
    renamed = original.with_name("above with spaces.png")
    original.rename(renamed)
    rows[0]["path"] = renamed.relative_to(data).as_posix()

    def update_manifest():
        write_csv(data / "samples.csv", list(rows[0]), rows)
        meta = read_json(data / "dataset.json")
        meta.update(manifest_sha256=sha256(data / "samples.csv"), version="2.0.0")
        write_json(data / "dataset.json", meta)

    update_manifest()
    base = [
        "--dataset",
        str(data / "dataset.json"),
        "--input-manifest",
        str(data / "samples.csv"),
        "--reference",
        str(ref / "reference.json"),
    ]
    args = arguments(tmp_path, runtime, "first run", base)
    args.work_dir = tmp_path / "work with spaces"
    args.launch_dir = tmp_path / "launch with spaces"
    assert run(args) == 0
    session = read_json(args.outdir / "provenance/run.json")["nextflow_session_id"]

    def invocation(name, selected_runtime=runtime):
        next_args = arguments(tmp_path, selected_runtime, name, [*base, "--resume", session])
        next_args.work_dir, next_args.launch_dir = args.work_dir, args.launch_dir
        return next_args

    ok, encoded = cv2.imencode(".png", np.full((64, 64), 255, np.uint8))
    assert ok
    atomic_bytes(renamed, encoded.tobytes())
    rows[0].update(sha256=sha256(renamed), size_bytes=renamed.stat().st_size)
    update_manifest()
    changed = invocation("changed input")
    assert run(changed) == 0
    changes = states(changed.outdir)
    assert sum(t["status"] == "CACHED" for t in changes) == 2
    executed = [t for t in changes if t["status"] == "COMPLETED"]
    assert len(executed) == 1 and executed[0]["sample_ids"] == [rows[0]["sample_id"]]

    calibration = read_json(ref / "calibration.json")
    calibration["pixel_size_um"] = 0.75
    write_json(ref / "calibration.json", calibration)
    reference = read_json(ref / "reference.json")
    reference["version"] = "2.0.0"
    reference["calibration"].update(
        sha256=sha256(ref / "calibration.json"),
        size_bytes=(ref / "calibration.json").stat().st_size,
    )
    write_json(ref / "reference.json", reference)
    recalibrated = invocation("changed reference")
    assert run(recalibrated) == 0
    assert all(t["status"] == "COMPLETED" for t in states(recalibrated.outdir))

    alternative = tmp_path / "candidate with metadata.sif"
    shutil.copy2(runtime[0], alternative)
    identity = tmp_path / "additional-object.json"
    write_json(
        identity, {"purpose": "SIF identity invalidation acceptance; root filesystem unchanged"}
    )
    subprocess.run(
        ["apptainer", "sif", "add", str(alternative), str(identity), "--datatype", "6"], check=True
    )
    alternative_digest = sha256(alternative)
    assert alternative_digest != runtime[1]
    with pytest.raises(ReproError, match="checksum differs"):
        run(invocation("wrong SIF lock", (alternative, runtime[1])))
    rebuilt = invocation("changed SIF", (alternative, alternative_digest))
    assert run(rebuilt) == 0
    assert all(t["status"] == "COMPLETED" for t in states(rebuilt.outdir))
    assert compare(recalibrated.outdir, rebuilt.outdir, exact=True)["equivalent"]

    # Valid PNG bytes with an unchanged lock must fail before any analysis tasks.
    ok, encoded = cv2.imencode(".png", np.full((64, 64), 200, np.uint8))
    assert ok
    atomic_bytes(renamed, encoded.tobytes())
    corrupt = invocation("corrupt input")
    assert run(corrupt) == 3
    assert states(corrupt.outdir) == []
