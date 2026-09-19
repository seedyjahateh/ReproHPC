"""Run inventories, integrity verification, task lineage, and comparison."""

import csv
import json
import math
import mimetypes
import platform
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from .config import science_params
from .errors import ReproError
from .io import (
    atomic_bytes,
    canonical,
    confined,
    csv_value,
    fingerprint,
    read_json,
    read_mask,
    sha256,
    write_json,
)
from .metadata import write_public_metadata
from .schema import validate

# Recorded media types must not depend on the verifying host. The module-level mimetypes
# functions consult platform registries (Windows can map .csv to application/vnd.ms-excel);
# a fresh MimeTypes instance uses only Python's built-in table.
MEDIA_TYPES = mimetypes.MimeTypes()


def now():
    return datetime.now(UTC).isoformat()


def platform_info():
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "hostname": platform.node(),
    }


def inventory(root, *, scientific=False):
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if scientific and relative.split("/")[0] not in ("samples", "summary"):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ReproError(f"Published artifact is a symlink/escape: {relative}", 5)
        entries.append(
            {
                "path": relative,
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
                "media_type": MEDIA_TYPES.guess_type(path.name)[0] or "application/octet-stream",
            }
        )
    return entries


def link_outputs(root, tasks):
    """Link every scientific artifact to its successful producing attempt."""
    successful = [t for t in tasks if t["status"] in ("COMPLETED", "CACHED")]
    entries = inventory(root, scientific=True)
    for entry in entries:
        parts = Path(entry["path"]).parts
        sample = parts[1] if parts[0] == "samples" else None
        producers = [
            t
            for t in successful
            if (sample in t["sample_ids"] if sample else t["process"].endswith("AGGREGATE"))
        ]
        if len(producers) != 1:
            raise ReproError(f"Artifact must have exactly one producer: {entry['path']}", 5)
        producer = producers[0]
        entry.update(
            schema_version="1.0.0",
            scope={"sample_id": sample, "kind": "sample" if sample else "dataset"},
            role={
                ".npy": "segmentation_mask",
                ".png": "preview",
                ".csv": "measurements",
                ".json": "metrics",
            }[Path(entry["path"]).suffix],
            schema="docs/data-dictionary.md#scientific-files",
            producing_task={
                "task_id": producer["task_id"],
                "attempt": producer["attempt"],
                "hash": producer["hash"],
                "origin_run_id": producer["origin_run_id"],
            },
        )
        validate("output", entry)
    return entries


def write_checksums(root):
    _write_checksums(root, {})


def _write_checksums(root, pending):
    entries = {entry["path"]: entry["sha256"] for entry in inventory(root)}
    entries.update({path: fingerprint(value) for path, value in pending.items()})
    atomic_bytes(
        root / "checksums.sha256",
        "".join(
            f"{digest}  {path}\n"
            for path, digest in sorted(entries.items())
            if path != "checksums.sha256"
        ).encode(),
    )


def finalize_run(root, run):
    """Verify the complete package before atomically publishing its success marker.

    Only the two terminal metadata records are staged in memory. Their canonical
    bytes are covered by the final checksum manifest; all other artifacts are
    read from disk by the same verifier used for public packages. A failure or
    process termination before the last rename leaves the old non-success status.
    The caller owns this isolated run directory throughout finalization.
    Public JSON-LD metadata is written first, so a run directory and its export
    both resolve every artifact linked from the report.
    """
    write_public_metadata(root, run)
    pending = {"provenance/run.json": run, "status.json": run["status"]}
    _write_checksums(root, pending)
    _verify_run(root, pending)
    write_json(root / "provenance/run.json", run)
    write_json(root / "status.json", run["status"])


def verify_run(root):
    return _verify_run(root, {})


def _verify_run(root, pending):
    def record(relative):
        return pending[relative] if relative in pending else read_json(root / relative)

    status = validate("status", record("status.json"))
    if status["status"] != "success" or status["exit_code"] != 0:
        raise ReproError("Run is not verified success", 5)
    declared = {}
    try:
        for line in (root / "checksums.sha256").read_text().splitlines():
            digest, relative = line.split("  ", 1)
            if relative in declared:
                raise ReproError(f"Duplicate checksum entry: {relative}", 5)
            path = confined(root, relative)
            valid = (
                fingerprint(pending[relative]) == digest
                if relative in pending
                else path.is_file() and sha256(path) == digest
            )
            if path.is_symlink() or not valid:
                raise ReproError(f"Artifact integrity failure: {relative}", 5)
            declared[relative] = digest
    except (OSError, ValueError, ReproError) as exc:
        raise ReproError(f"Invalid checksum manifest: {exc}", 5) from exc
    actual = (
        {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} | set(pending)
    ) - {"checksums.sha256"}
    if actual != set(declared):
        raise ReproError(
            f"Package inventory differs: extra={sorted(actual - set(declared))}, missing={sorted(set(declared) - actual)}",
            5,
        )
    expected = read_json(root / "provenance/outputs.json")
    tasks = [
        validate("task", json.loads(line))
        for line in (root / "provenance/tasks.jsonl").read_text().splitlines()
    ]
    if expected != link_outputs(root, tasks):
        raise ReproError("Scientific artifact inventory differs", 5)
    run = validate("run", record("provenance/run.json"))
    if run["status"] != status or run["run_id"] != status["run_id"]:
        raise ReproError("Run/status identities differ", 5)
    if sha256(root / "provenance/samples.csv") != run["dataset"]["manifest_sha256"]:
        raise ReproError("Archived input manifest differs from dataset identity", 5)
    validate_lineage(root, run, tasks)
    validate_scientific(root)
    return {"verified": True, "files": len(actual), "run_id": status["run_id"]}


def validate_lineage(root, run, tasks):
    """Check relationships, beyond each document's structural schema."""
    specification = validate("analysis", read_json(root / "provenance/analysis.json"))
    reference = validate("reference", read_json(root / "provenance/reference.json"))
    calibration = validate("calibration", read_json(root / "provenance/calibration.json"))
    if (
        reference != run["reference"]
        or specification["reference"] != reference
        or specification["calibration"] != calibration
        or sha256(root / "provenance/calibration.json") != reference["calibration"]["sha256"]
        or specification["science"] != science_params(run["params"])
        or specification["sif_sha256"] != run["software"]["sif_sha256"]
        or specification["source_tree_sha256"] != run["software"]["source_tree_sha256"]
    ):
        raise ReproError("Scientific specification differs from run identities", 5)
    samples = {s["sample_id"]: s for s in specification["samples"]}
    with (root / "provenance/samples.csv").open(newline="") as stream:
        manifest = list(csv.DictReader(stream))
    if len(samples) != len(manifest) or any(
        row["sample_id"] not in samples
        or any(str(samples[row["sample_id"]][key]) != value for key, value in row.items())
        for row in manifest
    ):
        raise ReproError("Scientific inputs differ from archived manifest", 5)
    seen = []
    for task in tasks:
        if not all(
            task["requested"].get(key) not in (None, "", "-") for key in ("cpus", "memory", "time")
        ):
            raise ReproError("Task requested resources are incomplete", 5)
        if task["status"] not in ("COMPLETED", "CACHED") or not task["process"].endswith(
            "ANALYZE_BATCH"
        ):
            continue
        metadata = task["scientific_metadata"]
        if (
            not metadata
            or metadata["sample_ids"] != task["sample_ids"]
            or metadata["inputs"] != [samples[sid] for sid in task["sample_ids"]]
            or metadata["parameter_sha256"] != fingerprint(specification["science"])
            or metadata["reference_sha256"]
            != fingerprint({"reference": reference, "calibration": calibration})
            or metadata["sif_sha256"] != specification["sif_sha256"]
            or not task["origin_run_id"]
        ):
            raise ReproError("Task scientific lineage is inconsistent", 5)
        for sid in task["sample_ids"]:
            metric = read_json(root / "samples" / sid / "metrics.json")
            if metric["parameter_sha256"] != metadata["parameter_sha256"]:
                raise ReproError(f"Measurement parameter lineage differs for {sid}", 5)
        seen.extend(task["sample_ids"])
    if len(seen) != len(set(seen)) or set(seen) != set(samples):
        raise ReproError("Scientific sample producers are incomplete or duplicated", 5)


def validate_scientific(root):
    with (root / "provenance/samples.csv").open(encoding="utf-8", newline="") as stream:
        ids = [row["sample_id"] for row in csv.DictReader(stream)]
    folders = [p.name for p in (root / "samples").iterdir()]
    if len(ids) != len(set(ids)) or set(ids) != set(folders):
        raise ReproError("Published sample set differs from input manifest", 5)
    total = 0
    expected_images = []
    for sid in sorted(ids):
        folder = root / "samples" / sid
        metric = validate("metrics", read_json(folder / "metrics.json"))
        if metric["sample_id"] != sid:
            raise ReproError(f"Metrics identity differs for {sid}", 5)
        expected_images.append(
            {key: str(csv_value(value)) for key, value in metric.items() if key != "schema_version"}
        )
        mask = read_mask(folder / "mask.npy")
        if mask.shape != (metric["height"], metric["width"]):
            raise ReproError(f"Invalid mask for {sid}", 5)
        with (folder / "objects.csv").open(encoding="utf-8", newline="") as stream:
            count = 0
            area = 0
            for row in csv.DictReader(stream):
                count += 1
                if row["sample_id"] != sid or int(row["object_id"]) != count:
                    raise ReproError(f"Invalid object identity for {sid}", 5)
                area += int(row["area_px"])
                for field in ("centroid_x_px", "centroid_y_px", "mean_intensity", "area_um2"):
                    if row[field] and not math.isfinite(float(row[field])):
                        raise ReproError(f"Non-finite object measurement for {sid}", 5)
        if count != metric["object_count"] or area != mask.foreground:
            raise ReproError(f"Mask/measurement inconsistency for {sid}", 5)
        total += count
    summary = validate("summary", read_json(root / "summary/dataset.json"))
    if (
        summary["processed_samples"] != len(ids)
        or summary["expected_samples"] != len(ids)
        or summary["object_count"] != total
    ):
        raise ReproError("Aggregate counts differ from individual samples", 5)
    with (root / "summary/images.csv").open(newline="") as stream:
        if list(csv.DictReader(stream)) != expected_images:
            raise ReproError("Aggregate image measurements differ from individual samples", 5)
    with (root / "summary/objects.csv").open(newline="") as stream:
        combined = iter(csv.DictReader(stream))
        for sid in sorted(ids):
            with (root / "samples" / sid / "objects.csv").open(newline="") as individual:
                for expected in csv.DictReader(individual):
                    if next(combined, None) != expected:
                        raise ReproError(
                            "Aggregate object measurements differ from individual samples", 5
                        )
        if next(combined, None) is not None:
            raise ReproError("Aggregate contains additional object measurements", 5)


def collect_tasks(root, run_id=None, session_id=None):
    trace = root / "provenance/trace.tsv"
    records = []
    if not trace.exists():
        return records
    with trace.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            work = Path(row["workdir"])
            metadata = (
                read_json(work / "result/task.json")
                if (work / "result/task.json").is_file()
                else None
            )
            task = {
                "schema_version": "1.0.0",
                "process": row["process"],
                "task_id": row["task_id"],
                "hash": row["hash"],
                "status": row["status"],
                "attempt": int(row.get("attempt") or 1),
                "native_id": None if row.get("native_id") in (None, "-") else row["native_id"],
                "sample_ids": metadata["sample_ids"] if metadata else [],
                "requested": {key: row.get(key) for key in ("cpus", "memory", "time")},
                "usage": {
                    key: None if row.get(key) in (None, "-") else row[key]
                    for key in ("peak_rss", "%cpu", "realtime", "start", "complete", "submit")
                },
                "exit_code": None if row.get("exit") in (None, "-") else int(row["exit"]),
                "origin_work_dir": str(work),
                "scientific_metadata": metadata,
                "origin_run_id": None,
                "origin_session_id": None,
                "origin_task_id": None,
                "logs": [],
            }
            origin_path = work / ".reprohpc-origin.json"
            if row["status"] != "CACHED" and run_id:
                write_json(
                    origin_path,
                    {"run_id": run_id, "session_id": session_id, "task_id": row["task_id"]},
                )
            origin = read_json(origin_path) if origin_path.exists() else {}
            if not origin:
                # The engine writes its configured environment into the immutable
                # task wrapper before launch. Recover the original invocation even
                # if that driver was hard-killed before provenance finalization.
                wrapper = work / ".command.run"
                if wrapper.is_file():
                    match = re.search(
                        r"REPROHPC_RUN_ID[^\n]*?([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",
                        wrapper.read_text(),
                    )
                    if match:
                        origin = {
                            "run_id": match.group(1),
                            "session_id": session_id,
                            "task_id": row["task_id"],
                        }
            task.update(
                origin_run_id=origin.get("run_id"),
                origin_session_id=origin.get("session_id"),
                origin_task_id=origin.get("task_id"),
            )
            task["usage"]["unavailable_reason"] = (
                "not_available" if task["usage"]["peak_rss"] is None else None
            )
            validate("task", task)
            destination = root / "logs/tasks" / f"{task['task_id']}-{task['attempt']}"
            destination.mkdir(parents=True, exist_ok=True)
            for filename in (
                ".command.sh",
                ".command.out",
                ".command.err",
                ".command.log",
                ".exitcode",
            ):
                if (work / filename).is_file():
                    shutil.copy2(work / filename, destination / filename.lstrip("."))
                    task["logs"].append(
                        (destination / filename.lstrip(".")).relative_to(root).as_posix()
                    )
            records.append(task)
    return records


def collect_accounting(tasks, root, timeout=60):
    # Native IDs can be reused after a scheduler reset or retention interval.
    # A cached task belongs to its original run: never query its old ID anew.
    executed = [task for task in tasks if task["status"] != "CACHED"]
    for task in tasks:
        if task["status"] != "CACHED":
            continue
        path = Path(task["origin_work_dir"]) / ".reprohpc-origin.json"
        origin = read_json(path) if path.is_file() else {}
        saved = origin.get("slurm_accounting", {})
        task["usage"].update(
            slurm_records=saved.get("records", []),
            accounting_unavailable_reason=saved.get("unavailable_reason")
            if saved
            else "origin_accounting_not_recorded",
            accounting_scope="origin_run",
            accounting_captured_at=saved.get("captured_at"),
        )
    ids = sorted({str(task["native_id"]) for task in executed if task["native_id"]})
    if not ids:
        return
    command = [
        "sacct",
        "-n",
        "-P",
        "-j",
        ",".join(ids),
        "--units=K",
        "--format=JobID,State,ExitCode,ElapsedRaw,AllocCPUS,ReqMem,MaxRSS,TotalCPU,JobIDRaw,TimelimitRaw",
    ]
    deadline = time.monotonic() + timeout
    text = ""
    reason = None
    while True:
        try:
            response = subprocess.run(
                command, text=True, capture_output=True, check=False, timeout=15
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason = f"accounting_query_failed: {exc}"
            break
        text = response.stdout
        reason = (
            "accounting_delayed"
            if response.returncode == 0
            else (response.stderr.strip() or f"sacct_exit_{response.returncode}")
        )
        observed = {line.split("|", 1)[0].split(".")[0] for line in text.splitlines()}
        if response.returncode != 0 or set(ids) <= observed or time.monotonic() >= deadline:
            break
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    atomic_bytes(root / "provenance/sacct.tsv", text.encode())
    captured_at = now()
    for task in executed:
        records = [
            line.split("|")
            for line in text.splitlines()
            if line.split("|", 1)[0].split(".")[0] == str(task["native_id"])
        ]
        unavailable = None if records else reason
        task["usage"].update(
            slurm_records=records,
            accounting_unavailable_reason=unavailable,
            accounting_scope="current_run",
            accounting_captured_at=captured_at,
        )
        path = Path(task["origin_work_dir"]) / ".reprohpc-origin.json"
        if path.is_file():
            origin = read_json(path)
            origin["slurm_accounting"] = {
                "records": records,
                "unavailable_reason": unavailable,
                "captured_at": captured_at,
            }
            write_json(path, origin)


def compare(expected, actual, *, exact=False):
    """Compare stored oracle masks/counts or two complete scientific result trees."""
    differences = []
    maximum_differences = {}
    oracle = expected / "expected.json"
    if oracle.is_file():
        goldens = read_json(oracle)
        actual_samples = actual / "samples"
        if set(goldens) != {p.name for p in actual_samples.iterdir()}:
            differences.append("sample set differs")
        for sid, golden in goldens.items():
            try:
                mask = read_mask(actual_samples / sid / "mask.npy")
                if mask != read_mask(expected / sid / "mask.npy"):
                    differences.append(f"{sid}: mask differs")
                metric = read_json(actual_samples / sid / "metrics.json")
                if (
                    metric["object_count"] != golden["object_count"]
                    or mask.foreground != golden["foreground_px"]
                ):
                    differences.append(f"{sid}: counts differ")
                with (actual_samples / sid / "objects.csv").open() as stream:
                    if (
                        sorted(int(row["area_px"]) for row in csv.DictReader(stream))
                        != golden["areas"]
                    ):
                        differences.append(f"{sid}: areas differ")
            except (OSError, ValueError, ReproError) as exc:
                differences.append(f"{sid}: {exc}")
    else:
        left = inventory(expected, scientific=True)
        right = inventory(actual, scientific=True)
        if [e["path"] for e in left] != [e["path"] for e in right]:
            differences.append("scientific file sets differ")
        for entry in left:
            name = entry["path"]
            a, b = expected / name, actual / name
            if not b.exists():
                differences.append(f"missing {name}")
                continue
            if exact or a.suffix == ".png":
                equal = a.read_bytes() == b.read_bytes()
            elif a.suffix == ".npy":
                equal = read_mask(a) == read_mask(b)
            elif a.suffix == ".json":
                equal = _equivalent(read_json(a), read_json(b), maxima=maximum_differences)
            else:
                with a.open() as aa, b.open() as bb:
                    equal = _equivalent(
                        list(csv.DictReader(aa)),
                        list(csv.DictReader(bb)),
                        maxima=maximum_differences,
                    )
            if not equal:
                differences.append(name)
    if differences:
        raise ReproError("Scientific comparison failed: " + "; ".join(differences), 5)
    return {
        "equivalent": True,
        "mode": "exact" if exact else "tolerance",
        "rtol": 1e-6,
        "atol": 1e-8,
        "max_absolute_difference_by_column": dict(sorted(maximum_differences.items())),
    }


def _equivalent(a, b, key="", maxima=None):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equivalent(a[k], b[k], k, maxima) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            _equivalent(x, y, key, maxima) for x, y in zip(a, b, strict=True)
        )
    floating = {
        "centroid_x_px",
        "centroid_y_px",
        "mean_intensity",
        "area_um2",
        "foreground_fraction",
        "mean_area_px",
    }
    if key in floating and a not in (None, "") and b not in (None, ""):
        try:
            av, bv = float(a), float(b)
            if maxima is not None and math.isfinite(av) and math.isfinite(bv):
                maxima[key] = max(maxima.get(key, 0.0), abs(bv - av))
            return bool(
                math.isfinite(av) and math.isfinite(bv) and abs(bv - av) <= 1e-8 + 1e-6 * abs(av)
            )
        except (TypeError, ValueError):
            return False
    return a == b


def write_tasks(root, tasks):
    atomic_bytes(root / "provenance/tasks.jsonl", b"".join(canonical(t) for t in tasks))
