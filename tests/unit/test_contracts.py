import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from reprohpc.config import load_yaml, resolve_params, validate_execution
from reprohpc.data import plan_batches, storage_estimate, validate_dataset, validate_reference
from reprohpc.errors import ReproError
from reprohpc.io import atomic_bytes, canonical, confined, read_json, sha256, write_json
from reprohpc.schema import validate
from reprohpc.science import analyze, decode, write_analysis

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/demo/1.0.0"


def test_all_default_goldens():
    expected = read_json(ROOT / "tests/expected/demo/expected.json")
    _, samples = validate_dataset(DATA / "dataset.json", DATA / "samples.csv")
    assert len(samples) == 12
    for sample in samples:
        sid = sample["sample_id"]
        mask, objects, metrics = analyze(decode(DATA / sample["path"]), sid, resolve_params())
        np.testing.assert_array_equal(
            mask, np.load(ROOT / "tests/expected/demo" / sid / "mask.npy")
        )
        assert metrics["object_count"] == expected[sid]["object_count"]
        assert sorted(obj["area_px"] for obj in objects) == expected[sid]["areas"]


def test_versioned_metadata_contracts():
    params = resolve_params()
    _, samples = validate_dataset(DATA / "dataset.json", DATA / "samples.csv")
    _, _, metrics = analyze(decode(DATA / samples[0]["path"]), samples[0]["sample_id"], params)
    for name, record in (("params", params), ("sample", samples[0]), ("metrics", metrics)):
        assert record["schema_version"] == "1.0.0"
        validate(name, record)
        for version in (None, "2.0.0"):
            invalid = dict(record)
            if version is None:
                invalid.pop("schema_version")
            else:
                invalid["schema_version"] = version
            with pytest.raises(ReproError):
                validate(name, invalid)
    with pytest.raises(ReproError, match="schema_version"):
        resolve_params({"schema_version": "2.0.0"})
    assert resolve_params(params) == params


def test_reference_batching_and_estimate():
    _, samples = validate_dataset(DATA / "dataset.json", DATA / "samples.csv")
    _, calibration = validate_reference(ROOT / "references/calibration/1.0.0/reference.json")
    assert calibration["pixel_size_um"] == 0.5
    batches = plan_batches(list(reversed(samples)), 5)
    assert [len(b["samples"]) for b in batches] == [5, 5, 2]
    assert [s for b in batches for s in b["samples"]] == samples
    assert plan_batches(samples, 5) == batches
    assert storage_estimate(samples) > sum(s["size_bytes"] for s in samples)
    assert (
        len(plan_batches([dict(samples[0], sample_id=f"a{i:05d}") for i in range(10000)], 16))
        == 625
    )


def test_stable_writes_and_preview(tmp_path):
    image = decode(DATA / "images/border.png")
    for folder in ("one", "two"):
        write_analysis(tmp_path / folder, image, "border", resolve_params(), 0.5)
    for path in (tmp_path / "one").iterdir():
        assert path.read_bytes() == (tmp_path / "two" / path.name).read_bytes()
    assert cv2.imread(str(tmp_path / "one/preview.png")).shape[:2] == (64, 64)
    write_analysis(
        tmp_path / "no-preview", image, "border", resolve_params({"write_previews": False})
    )
    assert not (tmp_path / "no-preview/preview.png").exists()


@pytest.mark.parametrize("value", ["../outside", "/absolute", "a\\b", "C:/file", "a\nb", "a\x00b"])
def test_confined_rejects_bad_paths(tmp_path, value):
    with pytest.raises(ReproError):
        confined(tmp_path, value)


def test_duplicate_yaml_and_precedence(tmp_path):
    file = tmp_path / "p.yaml"
    file.write_text("threshold: 1\nthreshold: 2\n")
    with pytest.raises(ReproError, match="Duplicate"):
        load_yaml(file)
    file.write_text("threshold: 100\n")
    assert resolve_params(load_yaml(file), {"threshold": 101})["threshold"] == 101
    with pytest.raises(ReproError):
        validate_execution({"account": "a;id"})
    with pytest.raises(ReproError):
        validate_execution({"array_size": 9, "max_inflight": 8})


def test_malformed_json_and_canonical(tmp_path):
    file = tmp_path / "bad.json"
    file.write_text('{"x": NaN}')
    with pytest.raises(ReproError):
        read_json(file)
    assert canonical({"b": 2, "a": 1}) == canonical({"a": 1, "b": 2})
    with pytest.raises(ValueError):
        canonical({"x": float("inf")})


@pytest.mark.parametrize(
    "mode", ["duplicate", "corrupt", "empty", "wrong-manifest", "extra-column"]
)
def test_invalid_datasets(tmp_path, mode):
    import shutil

    shutil.copytree(DATA, tmp_path / "data")
    folder = tmp_path / "data"
    manifest = folder / "samples.csv"
    rows = list(csv.DictReader(manifest.open()))
    if mode == "duplicate":
        rows[1]["sample_id"] = rows[0]["sample_id"]
    if mode == "corrupt":
        (folder / rows[0]["path"]).write_bytes(b"corrupt")
    if mode == "empty":
        rows = []
    if mode in ("duplicate", "empty"):
        from reprohpc.io import write_csv

        write_csv(manifest, ["sample_id", "path", "sha256", "size_bytes"], rows)
    if mode == "extra-column":
        manifest.write_text(manifest.read_text().replace("size_bytes", "size_bytes,extra"))
    metadata = json.loads((folder / "dataset.json").read_text())
    metadata["manifest_sha256"] = "0" * 64 if mode == "wrong-manifest" else sha256(manifest)
    write_json(folder / "dataset.json", metadata)
    with pytest.raises(ReproError):
        validate_dataset(folder / "dataset.json", manifest)


@pytest.mark.parametrize(
    "shape,dtype", [((10, 10, 3), np.uint8), ((10, 10), np.uint16), ((1, 4097), np.uint8)]
)
def test_unsupported_png(tmp_path, shape, dtype):
    file = tmp_path / "bad.png"
    atomic_bytes(file, cv2.imencode(".png", np.zeros(shape, dtype=dtype))[1].tobytes())
    with pytest.raises(ReproError, match="Unsupported"):
        decode(file)


def test_corrupt_png(tmp_path):
    file = tmp_path / "bad.png"
    file.write_bytes(b"not png")
    with pytest.raises(ReproError):
        decode(file)
