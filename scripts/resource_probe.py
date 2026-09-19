"""Run maximum-image and streaming-table probes inside the candidate SIF."""

import argparse
import resource
import time
from pathlib import Path

import numpy as np

from reprohpc.config import resolve_params
from reprohpc.io import fingerprint, write_csv, write_json
from reprohpc.science import OBJECT_FIELDS, write_analysis
from reprohpc.task import aggregate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=["max-image", "million-rows"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    params = resolve_params({"write_previews": False})
    if args.case == "max-image":
        image = np.zeros((4096, 4096), dtype=np.uint8)
        image[32:-32, 32:-32] = 255
        write_analysis(args.output / "sample", image, "maximum", params, 0.5)
        rows = None
    else:
        folder = args.output / "batch/samples/large"
        folder.mkdir(parents=True)
        rows = 1_000_000
        write_json(
            folder / "metrics.json",
            {
                "schema_version": "1.0.0",
                "sample_id": "large",
                "width": 4096,
                "height": 4096,
                "object_count": rows,
                "foreground_fraction": rows / 4096**2,
                "mean_area_px": 1.0,
                "qc": [],
                "parameter_sha256": fingerprint(params),
            },
        )

        def records():
            for index in range(rows):
                yield dict(
                    zip(
                        OBJECT_FIELDS,
                        [
                            "large",
                            index + 1,
                            1,
                            float(index % 4096),
                            float(index // 4096),
                            200.0,
                            False,
                            0.25,
                        ],
                        strict=True,
                    )
                )

        write_csv(folder / "objects.csv", OBJECT_FIELDS, records())
        aggregate([args.output / "batch"], ["large"], args.output / "summary")
        with (args.output / "summary/objects.csv").open() as stream:
            assert sum(1 for _ in stream) == rows + 1
    evidence = {
        "case": args.case,
        "rows": rows,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "platform_units": "Linux ru_maxrss KiB",
        "baked_package": __import__("reprohpc").__file__,
    }
    write_json(args.output / "evidence.json", evidence)
    print(evidence)


if __name__ == "__main__":
    main()
