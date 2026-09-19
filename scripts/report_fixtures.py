"""Real Python scientific outputs for browser tests; not container execution evidence."""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reprohpc.config import resolve_params  # noqa: E402
from reprohpc.data import plan_batches, validate_dataset, validate_reference  # noqa: E402
from reprohpc.io import fingerprint, read_json, write_json  # noqa: E402
from reprohpc.task import aggregate, analyze_batch, report  # noqa: E402


def fixtures(output):
    output.mkdir(parents=True, exist_ok=False)
    dataset = ROOT / "data/demo/1.0.0"
    _, samples = validate_dataset(dataset / "dataset.json", dataset / "samples.csv")
    reference, calibration = validate_reference(
        ROOT / "references/calibration/1.0.0/reference.json"
    )
    params = resolve_params()
    batch = output / "demo/batch"
    spec = {
        **plan_batches(samples, 16)[0],
        "params": params,
        "calibration": calibration,
        "reference_sha256": fingerprint(reference),
        "sif_sha256": "Python-only browser fixture; no SIF executed",
    }
    analyze_batch(spec, [dataset / sample["path"] for sample in samples], batch)
    aggregate([batch], [s["sample_id"] for s in samples], output / "demo/summary")
    report(output / "demo/summary", params, [batch], output / "demo/report/index.html")
    shutil.copytree(batch / "samples", output / "demo/samples")
    # 65 aliases of a real measured blank image exercise both pagination boundaries,
    # null means, the 24-preview cap, and long IDs. This is UI fixture data only.
    for variant in ("large", "no-previews"):
        variant_root = output / variant
        variant_batch = variant_root / "batch"
        ids = [f"sample-{i:03d}-" + "x" * 50 for i in range(65)]
        for sid in ids:
            target = variant_batch / "samples" / sid
            shutil.copytree(batch / "samples/blank", target)
            metrics = read_json(target / "metrics.json")
            metrics["sample_id"] = sid
            write_json(target / "metrics.json", metrics)
            if variant == "no-previews":
                (target / "preview.png").unlink()
        write_json(variant_batch / "task.json", read_json(batch / "task.json"))
        aggregate([variant_batch], ids, variant_root / "summary")
        report(
            variant_root / "summary", params, [variant_batch], variant_root / "report/index.html"
        )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(fixtures(parser.parse_args().output))
