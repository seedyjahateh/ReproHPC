"""Fetch the real BBBC039 nuclei images and convert them to the project's input contract.

Source: Broad Bioimage Benchmark Collection, image set BBBC039v1 (CC0). The upstream
archives are pinned by SHA-256. Conversion is a fixed, dataset-wide linear map from the
12-bit sensor range to 8 bits (>> 4); no per-image adaptation, so the result is
deterministic and free of any tuning on the data. Ground-truth nuclei counts derived from
the published masks are written outside the immutable dataset directory.

    python scripts/fetch_bbbc039.py --cache .tools/bbbc039

Citation requested by the collection:
    "We used image set BBBC039v1 Caicedo et al. 2018, available from the Broad Bioimage
    Benchmark Collection [Ljosa et al., Nature Methods, 2012]."
"""

import argparse
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from reprohpc.errors import ReproError  # noqa: E402
from reprohpc.io import atomic_bytes, sha256, write_csv, write_json  # noqa: E402

BASE = "https://data.broadinstitute.org/bbbc/BBBC039"
ARCHIVES = {
    "images": {
        "sha256": "6f30a5d4fe38c928ded972704f085975f8dc0d65d9aa366df00e5a9d449fddd7",
        "size_bytes": 77915748,
    },
    "masks": {
        "sha256": "f9e6043d8ca56344a4886f96a700d804d6ee982f31e2b2cd3194af2a053c2710",
        "size_bytes": 2753811,
    },
}
# 12-bit camera range to 8 bits. Fixed for every image; never fitted to the data.
SOURCE_BITS = 12
SHIFT = SOURCE_BITS - 8


def download(cache: Path, name: str) -> Path:
    artifact = ARCHIVES[name]
    path = cache / f"{name}.zip"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".partial")
        url = f"{BASE}/{name}.zip"
        with urllib.request.urlopen(url, timeout=300) as response, temporary.open("wb") as out:
            if not response.geturl().startswith("https://"):
                raise ReproError("Download redirected away from HTTPS")
            while block := response.read(1024 * 1024):
                out.write(block)
        temporary.replace(path)
    if sha256(path) != artifact["sha256"] or path.stat().st_size != artifact["size_bytes"]:
        raise ReproError(f"{name}.zip does not match the pinned upstream checksum")
    return path


def entries(archive: Path, suffix: str):
    stream = zipfile.ZipFile(archive)
    names = sorted(
        n
        for n in stream.namelist()
        if n.lower().endswith(suffix) and "__MACOSX" not in n and not n.endswith("/")
    )
    return stream, names


def sample_id(filename: str) -> str:
    # IXMtest_A02_s1_w1<GUID>.tif -> IXMtest_A02_s1_<first 8 GUID characters>. Well and site
    # alone are not unique (two fields were acquired twice), and the full GUID exceeds the
    # 64-character sample_id contract. The original filename is retained in the ground truth.
    plate, well, site, acquisition = Path(filename).stem.split("_", 3)
    return f"{plate}_{well}_{site}_{acquisition.removeprefix('w1')[:8]}"


def convert(cache: Path, output: Path, limit: int | None):
    images_zip, tifs = entries(download(cache, "images"), ".tif")
    masks_zip, masks = entries(download(cache, "masks"), ".png")
    by_id = {}
    for name in tifs:
        identifier = sample_id(name)
        if identifier in by_id:
            raise ReproError(f"Ambiguous sample_id derived from {name}")
        by_id[identifier] = name
    mask_by_id = {sample_id(name): name for name in masks}
    if set(by_id) != set(mask_by_id):
        raise ReproError("Images and ground-truth masks do not correspond one to one")
    selected = sorted(by_id)[: limit or len(by_id)]

    rows, truth = [], {}
    for identifier in selected:
        raw = cv2.imdecode(
            np.frombuffer(images_zip.read(by_id[identifier]), np.uint8), cv2.IMREAD_UNCHANGED
        )
        if raw is None or raw.dtype != np.uint16 or raw.ndim != 2:
            raise ReproError(f"Unexpected source image for {identifier}")
        if int(raw.max()) >= 1 << SOURCE_BITS:
            raise ReproError(f"{identifier} exceeds the declared {SOURCE_BITS}-bit source range")
        eight_bit = (raw >> SHIFT).astype(np.uint8)
        success, encoded = cv2.imencode(".png", eight_bit)
        if not success:
            raise ReproError(f"Could not encode {identifier}")
        path = output / "images" / f"{identifier}.png"
        atomic_bytes(path, encoded.tobytes())
        rows.append(
            {
                "sample_id": identifier,
                "path": f"images/{identifier}.png",
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
        # Published masks are RGBA; the blue channel holds class codes (0 = background).
        mask = cv2.imdecode(
            np.frombuffer(masks_zip.read(mask_by_id[identifier]), np.uint8), cv2.IMREAD_UNCHANGED
        )
        foreground = (mask[:, :, 2] > 0).astype(np.uint8)
        components, _ = cv2.connectedComponents(foreground, connectivity=8)
        truth[identifier] = {
            "nuclei": int(components - 1),
            "foreground_px": int(foreground.sum()),
            "source_image": Path(by_id[identifier]).name,
            "source_mask": Path(mask_by_id[identifier]).name,
        }
    return rows, truth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / ".tools/bbbc039")
    parser.add_argument("--output", type=Path, default=ROOT / "data/bbbc039/1.0.0")
    parser.add_argument("--limit", type=int, help="convert only the first N images by sample_id")
    parser.add_argument("--reference", type=Path, default=ROOT / "references/bbbc039/1.0.0")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite {args.output}; remove it or choose --output")

    rows, truth = convert(args.cache.resolve(), args.output, args.limit)
    write_csv(
        args.output / "samples.csv",
        ["sample_id", "path", "sha256", "size_bytes"],
        sorted(rows, key=lambda r: r["sample_id"]),
    )
    write_json(
        args.output / "dataset.json",
        {
            "schema_version": "1.0.0",
            "dataset_id": "bbbc039",
            "version": "1.0.0",
            "title": "BBBC039v1 U2OS nuclei, 8-bit conversion",
            "description": (
                f"Real Hoechst-stained U2OS cell nuclei from BBBC039v1, converted from 16-bit TIFF "
                f"to 8-bit grayscale PNG by a fixed {SOURCE_BITS}-to-8-bit right shift of {SHIFT}. "
                "Images are unmodified otherwise; no cropping, filtering, or per-image scaling."
            ),
            "creators": ["Caicedo et al. 2018", "Broad Bioimage Benchmark Collection"],
            "license": "CC0-1.0",
            "source": "https://bbbc.broadinstitute.org/BBBC039",
            "created": "2026-09-17",
            "manifest_sha256": sha256(args.output / "samples.csv"),
            "modality": "fluorescence microscopy, Hoechst nuclear stain",
            "species": "Homo sapiens (U2OS osteosarcoma cell line)",
            "access": "public",
            "doi": None,
        },
    )
    write_json(
        args.output.parent / f"ground-truth-{args.output.name}.json",
        {
            "schema_version": "1.0.0",
            "description": (
                "Nuclei counts and foreground pixels derived from the published BBBC039v1 masks: "
                "8-connected components of the non-background class. Reference data for "
                "evaluation only; never an input to analysis."
            ),
            "source": f"{BASE}/masks.zip",
            "source_sha256": ARCHIVES["masks"]["sha256"],
            "samples": truth,
        },
    )
    # BBBC039 publishes no pixel size that this project has verified, so calibration is
    # explicitly absent and area_um2 stays null rather than carrying an invented scale.
    calibration = args.reference / "calibration.json"
    write_json(calibration, {"schema_version": "1.0.0", "pixel_size_um": None, "unit": "um"})
    write_json(
        args.reference / "reference.json",
        {
            "schema_version": "1.0.0",
            "reference_id": "bbbc039-uncalibrated",
            "version": "1.0.0",
            "source": "https://bbbc.broadinstitute.org/BBBC039",
            "license": "CC0-1.0",
            "calibration": {
                "path": "calibration.json",
                "sha256": sha256(calibration),
                "size_bytes": calibration.stat().st_size,
            },
        },
    )
    directory = args.output
    files = sorted(p for p in directory.rglob("*") if p.is_file() and p.name != "checksums.sha256")
    atomic_bytes(
        directory / "checksums.sha256",
        "".join(f"{sha256(p)}  {p.relative_to(directory).as_posix()}\n" for p in files).encode(),
    )
    print(f"{len(rows)} images -> {args.output}")
    print(f"ground truth -> {args.output.parent / f'ground-truth-{args.output.name}.json'}")


if __name__ == "__main__":
    main()
