"""Create immutable synthetic images and independent default-profile goldens."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from reference_oracle import reference  # noqa: E402

from reprohpc.io import atomic_bytes, sha256, write_csv, write_json  # noqa: E402


def generate(root):
    data = root / "data/demo/1.0.0"
    expected = root / "tests/expected/demo"
    images = {}
    images["blank"] = np.zeros((64, 64), np.uint8)
    images["full"] = np.full((64, 64), 255, np.uint8)
    images["equality"] = np.full((64, 64), 127, np.uint8)
    images["above"] = np.full((64, 64), 128, np.uint8)
    for key in ("square", "two", "touching", "border", "small", "gradient", "noise", "diagonal"):
        images[key] = np.zeros((64, 64), np.uint8)
    images["square"][16:32, 16:32] = 255
    images["two"][8:24, 8:24] = 255
    images["two"][40:56, 40:56] = 255
    images["touching"][16:32, 16:32] = 255
    images["touching"][24:40, 24:40] = 255
    images["border"][:16, :16] = 255
    images["small"][10:12, 10:12] = 255
    images["gradient"][:] = np.arange(64, dtype=np.uint8)[None, :] * 4
    # Bimodal, seeded sparse defects stay well away from uncertain rounding thresholds.
    images["noise"][:] = (
        np.random.default_rng(42).choice([0, 255], size=(64, 64), p=[0.95, 0.05]).astype(np.uint8)
    )
    images["diagonal"][8:24, 8:24] = 255
    images["diagonal"][24:40, 24:40] = 255
    rows, goldens = [], {}
    for name, image in sorted(images.items()):
        encoded = cv2.imencode(".png", image)[1]
        path = data / "images" / f"{name}.png"
        atomic_bytes(path, encoded.tobytes())
        rows.append(
            {
                "sample_id": name,
                "path": f"images/{name}.png",
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
        mask, components = reference(image)
        destination = expected / name
        destination.mkdir(parents=True, exist_ok=True)
        np.save(destination / "mask.npy", mask, allow_pickle=False)
        goldens[name] = {
            "object_count": len(components),
            "foreground_px": int(mask.sum()),
            "areas": sorted(map(len, components)),
        }
    write_csv(data / "samples.csv", ["sample_id", "path", "sha256", "size_bytes"], rows)
    write_json(
        data / "dataset.json",
        {
            "schema_version": "1.0.0",
            "dataset_id": "demo",
            "version": "1.0.0",
            "title": "ReproHPC synthetic geometry demo",
            "description": "Twelve generated, non-human grayscale fixtures; engineering validation only.",
            "creators": ["ReproHPC contributors"],
            "license": "CC0-1.0",
            "source": "https://github.com/seedyjahateh/ReproHPC",
            "created": "2026-09-12",
            "manifest_sha256": sha256(data / "samples.csv"),
            "modality": "synthetic grayscale imaging",
            "species": "not_applicable",
            "access": "public",
            "doi": None,
        },
    )
    ref = root / "references/calibration/1.0.0"
    write_json(
        ref / "calibration.json", {"schema_version": "1.0.0", "pixel_size_um": 0.5, "unit": "um"}
    )
    calibration = ref / "calibration.json"
    write_json(
        ref / "reference.json",
        {
            "schema_version": "1.0.0",
            "reference_id": "calibration",
            "version": "1.0.0",
            "source": "https://github.com/seedyjahateh/ReproHPC",
            "license": "CC0-1.0",
            "calibration": {
                "path": "calibration.json",
                "sha256": sha256(calibration),
                "size_bytes": calibration.stat().st_size,
            },
        },
    )
    write_json(expected / "expected.json", goldens)
    for directory in (data, ref):
        entries = sorted(
            p for p in directory.rglob("*") if p.is_file() and p.name != "checksums.sha256"
        )
        atomic_bytes(
            directory / "checksums.sha256",
            "".join(
                f"{sha256(p)}  {p.relative_to(directory).as_posix()}\n" for p in entries
            ).encode(),
        )


if __name__ == "__main__":
    generate(ROOT)
