import base64
import csv
from pathlib import Path

import pytest

from reprohpc.config import resolve_params
from reprohpc.data import plan_batches, validate_dataset, validate_reference
from reprohpc.errors import ReproError
from reprohpc.io import canonical, fingerprint, read_json
from reprohpc.provenance import compare, inventory
from reprohpc.task import aggregate, analyze_batch, main, report, validate_task

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/demo/1.0.0"
REF = ROOT / "references/calibration/1.0.0/reference.json"


def execute_python_tasks(output, batch_size):
    validate_task(DATA / "dataset.json", DATA / "samples.csv", REF, output / "validated.json")
    _, samples = validate_dataset(DATA / "dataset.json", DATA / "samples.csv")
    ref, calibration = validate_reference(REF)
    batches = []
    for batch in plan_batches(samples, batch_size):
        spec = {
            **batch,
            "params": resolve_params(),
            "calibration": calibration,
            "reference_sha256": fingerprint(ref),
            "sif_sha256": "unit-test-not-an-execution-image",
        }
        folder = output / "batches" / batch["batch_id"]
        analyze_batch(spec, [DATA / s["path"] for s in batch["samples"]], folder)
        batches.append(folder)
    aggregate(batches, [s["sample_id"] for s in samples], output / "summary")
    report(output / "summary", resolve_params(), batches, output / "report/index.html")
    import shutil

    for batch in batches:
        for sample in (batch / "samples").iterdir():
            shutil.copytree(sample, output / "samples" / sample.name)
    return batches


def test_python_task_pipeline_and_batch_invariance(tmp_path):
    execute_python_tasks(tmp_path / "one", 1)
    execute_python_tasks(tmp_path / "sixteen", 16)
    assert compare(ROOT / "tests/expected/demo", tmp_path / "one")["equivalent"]
    assert compare(tmp_path / "one", tmp_path / "sixteen", exact=True)["equivalent"]
    summary = read_json(tmp_path / "one/summary/dataset.json")
    assert summary["processed_samples"] == 12
    document = (tmp_path / "one/report/index.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in document and "<script src=" not in document
    assert len(list(csv.DictReader((tmp_path / "one/summary/images.csv").open()))) == 12


def test_aggregate_rejects_missing_and_duplicate(tmp_path):
    batches = execute_python_tasks(tmp_path / "run", 16)
    with pytest.raises(ReproError, match="Duplicate"):
        aggregate(batches + batches, [], tmp_path / "out")
    with pytest.raises(ReproError, match="Output IDs differ"):
        aggregate(batches, ["absent"], tmp_path / "out")


def test_task_parser_and_failures(tmp_path):
    args = [
        "validate",
        "--dataset",
        str(DATA / "dataset.json"),
        "--manifest",
        str(DATA / "samples.csv"),
        "--reference",
        str(REF),
        "--output",
        str(tmp_path / "validated.json"),
    ]
    assert main(args) == 0
    spec = {"params": resolve_params(), "samples": [{}, {}]}
    encoded = base64.b64encode(canonical(spec)).decode()
    assert (
        main(["analyze", "--spec-b64", encoded, "--output", str(tmp_path / "out"), "one.png"]) == 4
    )


def test_comparison_detects_scientific_changes(tmp_path):
    execute_python_tasks(tmp_path / "run", 16)
    from reprohpc.io import write_json

    path = tmp_path / "run/samples/blank/metrics.json"
    changed = read_json(path)
    changed["object_count"] = 10
    write_json(path, changed)
    with pytest.raises(ReproError, match="counts differ"):
        compare(ROOT / "tests/expected/demo", tmp_path / "run")
    assert all(e["sha256"] for e in inventory(tmp_path / "run", scientific=True))
