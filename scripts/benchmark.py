"""Measured strong-scaling protocol. Qualified results require an idle approved allocation."""

import argparse
import csv
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reprohpc.cli import parser as launcher_parser  # noqa: E402
from reprohpc.cli import run  # noqa: E402
from reprohpc.config import load_yaml, resolve_params  # noqa: E402
from reprohpc.data import validate_dataset, validate_reference  # noqa: E402
from reprohpc.io import fingerprint, read_json, sha256, write_csv, write_json  # noqa: E402
from reprohpc.provenance import platform_info  # noqa: E402


def trace_measurements(path):
    with path.open() as stream:
        tasks = list(csv.DictReader(stream, delimiter="\t"))
    analysis = [t for t in tasks if t["process"].endswith("ANALYZE_BATCH")]
    if not analysis or any(t["status"] != "COMPLETED" for t in analysis):
        raise ValueError("Benchmark requires only uncached, successful analysis tasks")
    stage = (
        max(int(t["complete"]) for t in analysis) - min(int(t["submit"]) for t in analysis)
    ) / 1000
    return {
        "analysis_seconds": stage,
        "queue_wait_task_seconds": sum(
            (int(t["start"]) - int(t["submit"])) / 1000 for t in analysis
        ),
        "task_seconds_by_stage": {
            stage: sum(int(t["realtime"]) / 1000 for t in tasks if t["process"] == stage)
            for stage in sorted({t["process"] for t in tasks})
        },
    }


def summarize(trials):
    grouped = {c: [t for t in trials if t["workers"] == c] for c in (1, 2, 4)}
    if any(len(values) != 3 for values in grouped.values()):
        raise ValueError("Exactly three measured trials per worker budget are required")
    medians = {
        c: statistics.median(t["analysis_seconds"] for t in values) for c, values in grouped.items()
    }
    return [
        {
            "workers": c,
            "analysis_median_seconds": medians[c],
            "analysis_min_seconds": min(t["analysis_seconds"] for t in grouped[c]),
            "analysis_max_seconds": max(t["analysis_seconds"] for t in grouped[c]),
            "workflow_median_seconds": statistics.median(t["workflow_seconds"] for t in grouped[c]),
            "speedup": medians[1] / medians[c],
            "efficiency": medians[1] / medians[c] / c,
        }
        for c in (1, 2, 4)
    ]


def accounting_efficiency(path):
    """Use .batch CPU/RSS once per allocation, never add parent and step memory."""
    measurements = []
    for line in path.read_text().splitlines():
        task = json.loads(line)
        if not task["process"].endswith("ANALYZE_BATCH"):
            continue
        records = task["usage"].get("slurm_records", [])
        batch = next((r for r in records if r[0].endswith(".batch")), None)
        if not batch or not batch[6] or not batch[7] or float(batch[3]) <= 0:
            measurements.append(
                {"cpu_efficiency": None, "rss_fraction": None, "reason": "missing batch accounting"}
            )
            continue
        cpu = 0.0
        for part in batch[7].split(":"):
            cpu = cpu * 60 + float(part)
        # collect_accounting requests --units=K; Slurm may retain a K suffix.
        rss_bytes = float(batch[6].removesuffix("K")) * 1024
        measurements.append(
            {
                "cpu_efficiency": cpu / (float(batch[3]) * float(batch[4])),
                "rss_fraction": rss_bytes / int(task["requested"]["memory"]),
                "reason": None,
            }
        )
    return measurements


def plot_scaling(summary, destination):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    workers = [r["workers"] for r in summary]
    figure, axes = plt.subplots(1, 2, figsize=(8, 3.5), layout="constrained")
    axes[0].plot(workers, [r["speedup"] for r in summary], "o-", label="Measured median")
    axes[0].plot(workers, workers, "--", color="gray", label="Ideal")
    axes[0].set(xlabel="Allocated workers", ylabel="Strong-scaling speedup", xticks=workers)
    axes[0].legend()
    axes[1].plot(workers, [r["efficiency"] for r in summary], "o-")
    axes[1].axhline(0.7, color="gray", linestyle="--", label="Four-worker target")
    axes[1].set(xlabel="Allocated workers", ylabel="Scaling efficiency", xticks=workers)
    axes[1].legend()
    figure.savefig(destination, dpi=180)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--params-file", type=Path, required=True)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--sif-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site-config", type=Path)
    parser.add_argument("--account", default="research")
    parser.add_argument("--partition", default="demo")
    parser.add_argument(
        "--memory-rationale", type=Path, help="Reviewed site-minimum/worst-case sizing explanation"
    )
    parser.add_argument(
        "--dedicated-allocation",
        action="store_true",
        help="Attest that no competing workloads use the measured allocation",
    )
    parser.add_argument(
        "--diagnostic", action="store_true", help="Record nonqualifying measurements explicitly"
    )
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Benchmark requires Linux and real Slurm")
    memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    hardware_ok = os.cpu_count() >= 8 and memory >= 16 * 1024**3
    if not args.diagnostic and (not hardware_ok or not args.dedicated_allocation):
        parser.error(
            "Qualified benchmark requires >=8 CPUs, >=16 GiB RAM, and an idle dedicated allocation"
        )
    try:
        import matplotlib  # noqa: F401 - check the chart dependency before expensive trials
    except ImportError:
        parser.error("Install hashed requirements-benchmark.lock before measuring")
    if sha256(args.sif) != args.sif_sha256:
        parser.error("SIF checksum differs")
    params = resolve_params(load_yaml(args.params_file), base=Path.cwd())
    dataset, samples = validate_dataset(
        Path(params["dataset"]), Path(params["input_manifest"]), preflight=True
    )
    if len(samples) < 256:
        parser.error("At least 256 real images are required")
    reference, calibration = validate_reference(Path(params["reference"]))
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    identity = {
        "platform": platform_info(),
        "logical_cpus": os.cpu_count(),
        "memory_bytes": memory,
        "sif_sha256": args.sif_sha256,
        "dataset": dataset,
        "reference": reference,
        "params": params,
        "dedicated_allocation": args.dedicated_allocation,
        "diagnostic": args.diagnostic,
        "cache_condition": "prepared files; no privileged cache flush",
    }
    write_json(args.output / "environment.json", identity)
    spec = {
        "params": params,
        "samples": samples,
        "batch_id": "serial-baseline",
        "calibration": calibration,
        "reference_sha256": fingerprint(reference),
        "sif_sha256": args.sif_sha256,
    }
    write_json(args.output / "serial-spec.json", spec)
    # Direct serial baseline runs in an actual one-CPU Slurm allocation. This helper
    # measures Apptainer startup plus identical per-image checks and output writes.
    serial = []
    for repeat in range(3):
        destination = args.output / f"serial-{repeat}"
        command = [
            "sbatch",
            "--parsable",
            "--wait",
            f"--partition={args.partition}",
            f"--account={args.account}",
            "--cpus-per-task=1",
            "--mem=2G",
            "--time=02:00:00",
            f"--output={args.output / f'serial-{repeat}.log'}",
            str(ROOT / "scripts/serial_baseline.sh"),
            str(args.sif.resolve()),
            str(args.output / "serial-spec.json"),
            str(Path(params["dataset"]).parent),
            str(destination),
        ]
        job = subprocess.check_output(command, text=True).strip()
        measured = read_json(destination / "timing.json")
        measured["job_id"] = job
        serial.append(measured)
        write_json(args.output / "serial-trials.json", serial)
    serial_median = statistics.median(t["elapsed_seconds"] for t in serial)
    if serial_median < 120 and not args.diagnostic:
        raise SystemExit(
            "Serial workload <120 seconds; increase real dataset size before scaling trials"
        )
    order = [1, 2, 4] * 3
    random.Random(2026).shuffle(order)
    write_json(args.output / "trial-order.json", order)
    trials = []
    for index, workers in enumerate(order):
        destination = args.output / f"trial-{index}-c{workers}"
        argv = [
            "run",
            "--profile",
            "slurm",
            "--params-file",
            str(args.params_file.resolve()),
            "--sif",
            str(args.sif.resolve()),
            "--sif-sha256",
            args.sif_sha256,
            "--outdir",
            str(destination),
            "--work-dir",
            str(args.output / f"work-{index}"),
            "--launch-dir",
            str(args.output / f"launch-{index}"),
            "--account",
            args.account,
            "--partition",
            args.partition,
            "--qos",
            f"bench{workers}",
            "--array-size",
            str(min(4, workers)),
            "--max-inflight",
            str(workers),
        ]
        if args.site_config:
            argv += ["--site-config", str(args.site_config.resolve())]
        started = time.monotonic()
        if run(launcher_parser().parse_args(argv)):
            raise SystemExit(f"Trial {index} failed; preserve diagnostics, do not report speedup")
        trial = {
            "trial": index,
            "workers": workers,
            "workflow_seconds": time.monotonic() - started,
            **trace_measurements(destination / "provenance/trace.tsv"),
        }
        trials.append(trial)
        write_json(args.output / "trials.json", trials)
    summary = summarize(trials)
    resource_measurements = [
        m
        for index, workers in enumerate(order)
        for m in accounting_efficiency(
            args.output / f"trial-{index}-c{workers}/provenance/tasks.jsonl"
        )
    ]
    write_json(args.output / "resource-measurements.json", resource_measurements)
    available = [m for m in resource_measurements if m["cpu_efficiency"] is not None]
    qualified = (
        hardware_ok and args.dedicated_allocation and serial_median >= 120 and not args.diagnostic
    )
    gates = {
        "qualified_environment_and_workload": qualified,
        "four_worker_speedup": summary[2]["speedup"] >= 2.8,
        "end_to_end_benefit": summary[2]["workflow_median_seconds"]
        < summary[0]["workflow_median_seconds"],
        "overhead_ratio": summary[0]["analysis_median_seconds"] / serial_median,
        "median_cpu_efficiency": statistics.median(m["cpu_efficiency"] for m in available)
        if available
        else None,
        "fraction_tasks_below_80_percent_memory": sum(m["rss_fraction"] <= 0.8 for m in available)
        / len(available)
        if available
        else None,
        "median_memory_fraction": statistics.median(m["rss_fraction"] for m in available)
        if available
        else None,
        "accounting_complete": len(available) == len(resource_measurements),
    }
    rationale = args.memory_rationale.read_text().strip() if args.memory_rationale else ""
    gates["memory_sizing_rationale"] = rationale or None
    gates["resource_targets"] = bool(
        available
        and gates["accounting_complete"]
        and gates["median_cpu_efficiency"] >= 0.7
        and gates["fraction_tasks_below_80_percent_memory"] >= 0.95
        and (gates["median_memory_fraction"] >= 0.2 or rationale)
    )
    write_csv(args.output / "scaling.csv", list(summary[0]), summary)
    write_json(args.output / "summary.json", {"measurements": summary, "gates": gates})
    plot_scaling(summary, args.output / "scaling.png")
    print(json.dumps(gates, indent=2))
    return (
        0
        if qualified
        and gates["four_worker_speedup"]
        and gates["end_to_end_benefit"]
        and gates["overhead_ratio"] <= 1.25
        and gates["resource_targets"]
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
