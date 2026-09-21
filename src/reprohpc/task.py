"""Container task entry points. Nextflow alone owns execution and retries."""

import argparse
import base64
import csv
import json
import os
import sys
import time
from pathlib import Path

from .config import resolve_params, science_params
from .data import check_file, validate_dataset, validate_reference
from .errors import ReproError
from .io import fingerprint, read_json, write_csv, write_json
from .reporting import report
from .schema import validate
from .science import IMAGE_FIELDS, OBJECT_FIELDS, decode, write_analysis


def validate_task(dataset, manifest, reference, destination):
    meta, samples = validate_dataset(dataset, manifest)
    ref, calibration = validate_reference(reference)
    write_json(
        destination,
        {
            "schema_version": "1.0.0",
            "dataset": meta,
            "samples": samples,
            "reference": ref,
            "calibration": calibration,
            "data_root": str(dataset.parent.resolve()),
        },
    )


def analyze_batch(spec, images, output):
    params = resolve_params(spec["params"])
    samples = spec["samples"]
    if len(images) != len(samples):
        raise ReproError("Staged image count does not match batch specification", 4)
    started = time.time()
    for sample, path in zip(samples, images, strict=True):
        check_file(path, sample)
        write_analysis(
            output / "samples" / sample["sample_id"],
            decode(path),
            sample["sample_id"],
            params,
            spec["calibration"]["pixel_size_um"],
        )
    write_json(
        output / "task.json",
        {
            "schema_version": "1.0.0",
            "batch_id": spec["batch_id"],
            "sample_ids": [s["sample_id"] for s in samples],
            "inputs": samples,
            "parameter_sha256": fingerprint(science_params(params)),
            "reference_sha256": spec["reference_sha256"],
            "sif_sha256": spec["sif_sha256"],
            "started_epoch": started,
            "finished_epoch": time.time(),
            "threads": 1,
            "opencv_optimized": False,
            "opencl": False,
            "array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID") or None,
            "array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID") or None,
            "job_id": os.environ.get("SLURM_JOB_ID") or None,
            "uid": os.getuid() if hasattr(os, "getuid") else None,
            "observed_threads": len(list(Path("/proc/self/task").iterdir()))
            if Path("/proc/self/task").exists()
            else None,
        },
    )


def mri_batch(spec, images, output):
    """fsl-bet-volumetry-v1 for one batch; task.json carries the same provenance as analyze."""
    from .mri import ALGORITHM, analyze_subject, fsl_identity

    params = resolve_params(spec["params"])
    if params["algorithm"] != ALGORITHM:
        raise ReproError(f"mri requires algorithm {ALGORITHM}", 2)
    samples = spec["samples"]
    if len(images) != len(samples):
        raise ReproError("Staged image count does not match batch specification", 4)
    identity = fsl_identity()
    started = time.time()
    for sample, path in zip(samples, images, strict=True):
        check_file(path, sample)
        analyze_subject(path, sample["sample_id"], params, output / "samples" / sample["sample_id"])
    write_json(
        output / "task.json",
        {
            "schema_version": "1.0.0",
            "routine": ALGORITHM,
            "batch_id": spec["batch_id"],
            "sample_ids": [s["sample_id"] for s in samples],
            "inputs": samples,
            "parameter_sha256": fingerprint(science_params(params)),
            "reference_sha256": spec["reference_sha256"],
            "sif_sha256": spec["sif_sha256"],
            "fsl": identity,
            "started_epoch": started,
            "finished_epoch": time.time(),
            "threads": 1,
            "array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID") or None,
            "array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID") or None,
            "job_id": os.environ.get("SLURM_JOB_ID") or None,
            "uid": os.getuid() if hasattr(os, "getuid") else None,
            "observed_threads": len(list(Path("/proc/self/task").iterdir()))
            if Path("/proc/self/task").exists()
            else None,
        },
    )


MRI_FIELDS = [
    "sample_id",
    "dim_x",
    "dim_y",
    "dim_z",
    "voxel_x_mm",
    "voxel_y_mm",
    "voxel_z_mm",
    "brain_voxels",
    "brain_volume_mm3",
    "qc",
    "parameter_sha256",
]


def mri_row(metric):
    """One subjects.csv row from a validated metrics record; the verifier rebuilds it too."""
    return {
        "sample_id": metric["sample_id"],
        **{f"dim_{a}": v for a, v in zip("xyz", metric["dims"], strict=True)},
        **{f"voxel_{a}_mm": v for a, v in zip("xyz", metric["voxel_size_mm"], strict=True)},
        "brain_voxels": metric["brain_voxels"],
        "brain_volume_mm3": metric["brain_volume_mm3"],
        "qc": metric["qc"],
        "parameter_sha256": metric["parameter_sha256"],
    }


def is_mri(batch_dirs):
    from .mri import ALGORITHM

    routines = {read_json(folder / "task.json").get("routine") for folder in batch_dirs}
    if len(routines) != 1:
        raise ReproError("Batches were produced by different routines", 4)
    return routines.pop() == ALGORITHM


def aggregate_mri(locations, expected_ids, output):
    from .mri import ALGORITHM

    voxels = 0
    qc = {}
    rows = []
    for sid in sorted(locations):
        metric = validate("mri_metrics", read_json(locations[sid] / "metrics.json"))
        if metric["sample_id"] != sid:
            raise ReproError(f"Wrong metrics identity: {sid}", 4)
        if not (locations[sid] / "brain_mask.nii.gz").is_file():
            raise ReproError(f"Missing brain mask for {sid}", 4)
        voxels += metric["brain_voxels"]
        for code in metric["qc"]:
            qc[code] = qc.get(code, 0) + 1
        rows.append(mri_row(metric))
    write_csv(output / "subjects.csv", MRI_FIELDS, rows)
    write_json(
        output / "dataset.json",
        {
            "schema_version": "1.0.0",
            "algorithm": ALGORITHM,
            "expected_samples": len(expected_ids),
            "processed_samples": len(locations),
            "brain_voxels": voxels,
            "qc": qc,
        },
    )


def aggregate(batch_dirs, expected_ids, output):
    locations = {}
    for folder in batch_dirs:
        for sample in sorted((folder / "samples").iterdir()):
            if sample.name in locations:
                raise ReproError(f"Duplicate output sample: {sample.name}", 4)
            locations[sample.name] = sample
    if set(locations) != set(expected_ids) or len(set(expected_ids)) != len(expected_ids):
        raise ReproError(
            f"Output IDs differ: missing={sorted(set(expected_ids) - set(locations))}, extra={sorted(set(locations) - set(expected_ids))}",
            4,
        )
    if is_mri(batch_dirs):
        aggregate_mri(locations, expected_ids, output)
        return
    count = 0
    qc = {}
    metrics = []
    for sid in sorted(locations):
        metric = validate("metrics", read_json(locations[sid] / "metrics.json"))
        if metric["sample_id"] != sid:
            raise ReproError(f"Wrong metrics identity: {sid}", 4)
        count += metric["object_count"]
        for code in metric["qc"]:
            qc[code] = qc.get(code, 0) + 1
        metrics.append(metric)
    write_csv(
        output / "images.csv",
        IMAGE_FIELDS,
        ({key: metric[key] for key in IMAGE_FIELDS} for metric in metrics),
    )

    def rows():
        for sid in sorted(locations):
            with (locations[sid] / "objects.csv").open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != OBJECT_FIELDS:
                    raise ReproError(f"Malformed object table for {sid}", 4)
                seen = 0
                for row in reader:
                    seen += 1
                    if row["sample_id"] != sid or int(row["object_id"]) != seen:
                        raise ReproError(f"Invalid object identity/order for {sid}", 4)
                    yield row
                if seen != read_json(locations[sid] / "metrics.json")["object_count"]:
                    raise ReproError(f"Object count mismatch for {sid}", 4)

    write_csv(output / "objects.csv", OBJECT_FIELDS, rows())
    write_json(
        output / "dataset.json",
        {
            "schema_version": "1.0.0",
            "expected_samples": len(expected_ids),
            "processed_samples": len(locations),
            "object_count": count,
            "qc": qc,
        },
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    v = subs.add_parser("validate")
    for key in ("dataset", "manifest", "reference", "output"):
        v.add_argument(f"--{key}", type=Path, required=True)
    for name in ("analyze", "mri"):
        a = subs.add_parser(name)
        a.add_argument("--spec-b64", required=True)
        a.add_argument("--output", type=Path, required=True)
        a.add_argument("images", nargs="+", type=Path)
    g = subs.add_parser("aggregate")
    g.add_argument("--expected", type=Path, required=True)
    g.add_argument("--output", type=Path, required=True)
    g.add_argument("batches", nargs="+", type=Path)
    r = subs.add_parser("report")
    r.add_argument("--summary", type=Path, required=True)
    r.add_argument("--params-b64", required=True)
    r.add_argument("--output", type=Path, required=True)
    r.add_argument("batches", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            validate_task(args.dataset, args.manifest, args.reference, args.output)
        elif args.command == "analyze":
            analyze_batch(json.loads(base64.b64decode(args.spec_b64)), args.images, args.output)
        elif args.command == "mri":
            mri_batch(json.loads(base64.b64decode(args.spec_b64)), args.images, args.output)
        elif args.command == "aggregate":
            aggregate(
                args.batches,
                [s["sample_id"] for s in read_json(args.expected)["samples"]],
                args.output,
            )
        else:
            report(
                args.summary,
                json.loads(base64.b64decode(args.params_b64)),
                args.batches,
                args.output,
            )
    except ReproError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except OSError as exc:
        import errno

        print(f"I/O failure: {exc}", file=sys.stderr)
        return 75 if exc.errno in (errno.EAGAIN, errno.ESTALE, errno.ETIMEDOUT) else 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
