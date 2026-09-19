"""Create a versioned, seeded synthetic workload; never inflate compute with sleeps."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from reprohpc.io import atomic_bytes, read_json, sha256, write_csv, write_json  # noqa: E402
from reprohpc.provenance import write_checksums  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=256)
    parser.add_argument("--size", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if not 1 <= args.count <= 10000 or not 64 <= args.size <= 4096:
        parser.error("count must be 1–10000 and square size 64–4096")
    args.output.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(args.seed)
    rows = []
    for index in range(args.count):
        image = rng.integers(0, 35, (args.size, args.size), dtype=np.uint8)
        for _ in range(max(4, args.size // 32)):
            x, y = rng.integers(0, args.size, size=2)
            radius = int(rng.integers(4, max(5, args.size // 32)))
            cv2.circle(image, (int(x), int(y)), radius, int(rng.integers(180, 256)), -1)
        ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        assert ok
        sid = f"workload-{index:05d}"
        path = args.output / "images" / f"{sid}.png"
        atomic_bytes(path, encoded.tobytes())
        rows.append(
            {
                "sample_id": sid,
                "path": f"images/{sid}.png",
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    write_csv(args.output / "samples.csv", ["sample_id", "path", "sha256", "size_bytes"], rows)
    metadata = read_json(ROOT / "data/demo/1.0.0/dataset.json")
    metadata.update(
        dataset_id=f"synthetic-workload-{args.count}-{args.size}-{args.seed}",
        title="ReproHPC synthetic workload",
        description=f"Seed {args.seed}; {args.count} independent {args.size}x{args.size} images. Not a domain benchmark.",
        manifest_sha256=sha256(args.output / "samples.csv"),
    )
    write_json(args.output / "dataset.json", metadata)
    write_checksums(args.output)
    print(f"Generated {args.count} images; manifest {metadata['manifest_sha256']}")


if __name__ == "__main__":
    main()
