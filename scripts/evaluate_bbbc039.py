"""Compare a completed BBBC039 run against the published expert masks.

Agreement is reported separately for the tuning half (every second sample_id, used to
choose threshold and min_area_px) and the held-out half, which no parameter choice saw.
Counts come from `summary/images.csv`; ground truth comes from the BBBC039 masks.

    python scripts/evaluate_bbbc039.py --run /scratch/bbbc039-run/run

demo-cv-v1 is a global threshold with connected components and no splitting step, so
touching nuclei merge into one object. Under-counting on dense fields is expected.
"""

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reprohpc.io import read_json, write_json  # noqa: E402


def agreement(pairs):
    """pairs: (observed, expected) nuclei counts for one split."""
    errors = [observed - expected for observed, expected in pairs]
    ratios = [observed / expected for observed, expected in pairs if expected]
    absolute = sorted(abs(e) for e in errors)
    return {
        "images": len(pairs),
        "expected_nuclei": sum(expected for _, expected in pairs),
        "observed_objects": sum(observed for observed, _ in pairs),
        "median_signed_error": statistics.median(errors),
        "median_absolute_error": statistics.median(absolute),
        "median_ratio_observed_over_expected": round(statistics.median(ratios), 4),
        "within_10_percent": round(100 * sum(abs(r - 1) <= 0.10 for r in ratios) / len(ratios), 1),
        "images_undercounted": sum(e < 0 for e in errors),
        "images_overcounted": sum(e > 0 for e in errors),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--ground-truth", type=Path, default=ROOT / "data/bbbc039/ground-truth-1.0.0.json"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    truth = read_json(args.ground_truth)["samples"]
    with (args.run / "summary/images.csv").open(encoding="utf-8", newline="") as stream:
        observed = {
            row["sample_id"]: (int(row["object_count"]), float(row["foreground_fraction"]))
            for row in csv.DictReader(stream)
        }
    missing = sorted(set(observed) - set(truth))
    if missing:
        raise SystemExit(f"No ground truth for: {missing[:5]}")

    ids = sorted(observed)
    splits = {"tuning": ids[::2], "held_out": ids[1::2]}
    parameters = read_json(args.run / "provenance/params.resolved.json")
    record = {
        "kind": "bbbc039-ground-truth-agreement",
        "run_id": read_json(args.run / "status.json")["run_id"],
        "dataset": read_json(args.run / "provenance/dataset.json")["title"],
        "parameters": {
            key: parameters[key]
            for key in ("threshold", "min_area_px", "gaussian_kernel", "connectivity")
        },
        "metric": "nuclei per image: connected components of the published mask vs objects reported by demo-cv-v1",
        "limitation": "No instance matching or splitting of touching nuclei; area agreement is reported separately.",
        "splits": {},
    }
    for name, members in splits.items():
        pairs = [(observed[sid][0], truth[sid]["nuclei"]) for sid in members]
        result = agreement(pairs)
        total_px = 520 * 696
        area_ratios = [
            (observed[sid][1] * total_px) / truth[sid]["foreground_px"]
            for sid in members
            if truth[sid]["foreground_px"]
        ]
        result["median_foreground_area_ratio"] = round(statistics.median(area_ratios), 4)
        record["splits"][name] = result

    print(json.dumps(record, indent=2))
    if args.output:
        write_json(args.output, record)


if __name__ == "__main__":
    main()
