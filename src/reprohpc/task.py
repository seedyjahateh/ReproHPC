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
    a = subs.add_parser("analyze")
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
