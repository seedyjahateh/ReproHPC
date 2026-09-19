"""Immutable dataset and reference validation and deterministic batch planning."""

import csv
import re

from .errors import ReproError
from .io import confined, image_header, read_json, sha256
from .schema import validate


def validate_reference(path):
    reference = validate("reference", read_json(path))
    entry = reference["calibration"]
    calibration_path = confined(path.parent, entry["path"])
    check_file(calibration_path, entry)
    return reference, validate("calibration", read_json(calibration_path))


def check_file(path, record):
    if not path.is_file():
        raise ReproError(f"Missing input: {path}")
    if path.stat().st_size != record["size_bytes"] or sha256(path) != record["sha256"]:
        raise ReproError(
            f"Checksum/size mismatch: {path}; restore locked bytes or publish a new dataset version"
        )


def validate_dataset(dataset_path, manifest_path, *, preflight=False):
    if not preflight:
        from .science import decode

    dataset = validate("dataset", read_json(dataset_path))
    if sha256(manifest_path) != dataset["manifest_sha256"]:
        raise ReproError("Manifest checksum does not match dataset.json")
    samples, ids, paths = [], set(), set()
    with manifest_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["sample_id", "path", "sha256", "size_bytes"]:
            raise ReproError("Manifest columns must be sample_id,path,sha256,size_bytes")
        for row in reader:
            if None in row or None in row.values():
                raise ReproError("Malformed manifest row")
            sample_id = row["sample_id"]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", sample_id) or sample_id in ids:
                raise ReproError(f"Invalid or duplicate sample_id: {sample_id!r}")
            path = confined(dataset_path.parent, row["path"])
            if path in paths:
                raise ReproError(f"Duplicate input path: {path}")
            try:
                row["size_bytes"] = int(row["size_bytes"])
            except ValueError as exc:
                raise ReproError(f"Invalid size_bytes for {sample_id}") from exc
            try:
                if preflight:
                    width, height = image_header(path)
                else:
                    check_file(path, row)
                    image = decode(path)
                    height, width = image.shape
                    del image
            except (OSError, ReproError) as exc:
                raise ReproError(f"Sample {sample_id}: {exc}") from exc
            row.update(
                schema_version="1.0.0", width=width, height=height, dtype="uint8", channels=1
            )
            validate("sample", row)
            samples.append(row)
            ids.add(sample_id)
            paths.add(path)
            if len(samples) > 10000:
                raise ReproError("Dataset exceeds 10,000-image support envelope")
    if not samples:
        raise ReproError("Manifest is empty")
    return dataset, sorted(samples, key=lambda row: row["sample_id"])


def plan_batches(samples, size):
    if type(size) is not int or not 1 <= size <= 64:
        raise ReproError("batch_size must be 1-64", 2)
    samples = sorted(samples, key=lambda row: row["sample_id"])
    return [
        {"batch_id": f"batch-{index // size:06d}", "samples": samples[index : index + size]}
        for index in range(0, len(samples), size)
    ]


def storage_estimate(samples, previews=True):
    # Input + SIF allowance plus work/result copies: bool masks, uint32 labels,
    # float buffers and worst-case retained object rows (min_area=1).
    decoded = sum(s["width"] * s["height"] for s in samples)
    return int(
        (decoded * 220 + len(samples) * (1024 * 1024 if previews else 0) + 2 * 1024**3) * 1.2
    )
