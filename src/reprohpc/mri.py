"""fsl-bet-volumetry-v1: FSL brain extraction and brain volume. FSL computes; Python orchestrates.

Every scientific number comes from an FSL tool: `bet` produces the brain mask and `fslstats`
measures it. This module only builds explicit command lines, runs them with a fixed
environment, parses their output strictly, and fails loudly on any non-zero exit.
"""

import json
import os
import subprocess
from pathlib import Path

from .config import MRI_ALGORITHM, science_params
from .errors import ReproError
from .io import fingerprint, nifti_header, write_json

ALGORITHM = MRI_ALGORITHM
MASK_NAME = "brain_mask.nii.gz"
# Plausible adult brain volume, in mm3, for an observational QC flag only; not a diagnosis.
ADULT_BRAIN_MM3 = (800_000.0, 2_000_000.0)
FSL_PACKAGES = ("fsl-bet2", "fsl-avwutils")
TIMEOUT_SECONDS = 1800


def fsl_dir() -> Path:
    value = os.environ.get("FSLDIR")
    if not value:
        raise ReproError("FSLDIR is not set; run inside the FSL image", 4)
    return Path(value)


def tool(name: str) -> Path:
    path = fsl_dir() / "bin" / name
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ReproError(f"FSL tool not found or not executable: {path}", 4)
    return path


def fsl_environment() -> dict:
    """A fixed environment: FSL location, output type, and single-threaded, C-locale tools."""
    root = str(fsl_dir())
    return {
        "FSLDIR": root,
        "FSLOUTPUTTYPE": "NIFTI_GZ",
        "FSLMULTIFILEQUIT": "TRUE",
        "PATH": f"{root}/bin:/usr/bin:/bin",
        "LC_ALL": "C",
        "TZ": "UTC",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    }


def run(argv: list[str]) -> str:
    """Run one FSL command; return stdout. Any failure raises with the tool's own message."""
    name = Path(argv[0]).name
    try:
        response = subprocess.run(
            [str(part) for part in argv],
            capture_output=True,
            text=True,
            env=fsl_environment(),
            check=False,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReproError(f"{name} exceeded {TIMEOUT_SECONDS} s", 4) from exc
    except OSError as exc:
        raise ReproError(f"Cannot execute {name}: {exc}", 4) from exc
    if response.returncode != 0:
        detail = (response.stderr.strip() or response.stdout.strip() or "no output")[-2000:]
        raise ReproError(f"{name} failed with exit code {response.returncode}: {detail}", 4)
    return response.stdout


def bet_command(image: Path, output_root: Path, frac: float) -> list[str]:
    """BET with an explicit fractional intensity threshold, writing only the binary mask.

    -m writes <output_root>_mask; -n suppresses the skull-stripped image, which this
    routine does not publish.
    """
    if not 0 < frac < 1:
        raise ReproError(f"bet_frac must be in (0, 1), got {frac!r}", 2)
    return [str(tool("bet")), str(image), str(output_root), "-f", repr(float(frac)), "-m", "-n"]


def skull_strip(image: Path, directory: Path, frac: float) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    root = directory / MASK_NAME.removesuffix("_mask.nii.gz")
    run(bet_command(image, root, frac))
    mask = directory / MASK_NAME
    if not mask.is_file():
        raise ReproError(f"bet reported success but wrote no mask: {mask}", 4)
    return mask


def volumes(mask: Path) -> tuple[int, float]:
    """Non-zero voxel count and volume in mm3, exactly as `fslstats -V` reports them."""
    fields = run([str(tool("fslstats")), str(mask), "-V"]).split()
    if len(fields) != 2:
        raise ReproError(f"Unexpected fslstats -V output: {fields!r}", 4)
    try:
        voxels, volume = int(fields[0]), float(fields[1])
    except ValueError as exc:
        raise ReproError(f"Unparseable fslstats -V output: {fields!r}", 4) from exc
    if voxels < 0 or volume < 0:
        raise ReproError(f"Negative fslstats -V output: {fields!r}", 4)
    return voxels, volume


def bounding_box(mask: Path) -> tuple[int, ...]:
    """`fslstats -w`: xmin xsize ymin ysize zmin zsize tmin tsize of non-zero voxels."""
    fields = run([str(tool("fslstats")), str(mask), "-w"]).split()
    try:
        box = tuple(int(value) for value in fields)
    except ValueError as exc:
        raise ReproError(f"Unparseable fslstats -w output: {fields!r}", 4) from exc
    if len(box) != 8:
        raise ReproError(f"Unexpected fslstats -w output: {fields!r}", 4)
    return box


def qc_mask(voxels: int, volume_mm3: float, box: tuple[int, ...], dims: tuple[int, ...]) -> list:
    """Observational flags, as with the image routine's QC. None of them is a diagnosis."""
    qc = []
    if voxels == 0:
        return ["EMPTY_MASK"]
    low, high = ADULT_BRAIN_MM3
    if not low <= volume_mm3 <= high:
        qc.append("VOLUME_OUT_OF_RANGE")
    touches = any(
        box[2 * axis] == 0 or box[2 * axis] + box[2 * axis + 1] >= dims[axis] for axis in range(3)
    )
    if touches:
        qc.append("MASK_AT_FOV_EDGE")
    return qc


def fsl_identity() -> dict:
    """FSL release and tool package versions, read from the image rather than asserted."""
    root = fsl_dir()
    try:
        build = json.loads((root / "etc/reprohpc-fsl-build.json").read_text())
    except (OSError, ValueError) as exc:
        raise ReproError(f"FSL build record unreadable: {exc}", 4) from exc
    packages = {}
    for name in FSL_PACKAGES:
        found = sorted((root / "conda-meta").glob(f"{name}-[0-9]*.json"))
        if len(found) != 1:
            raise ReproError(f"Expected one installed {name} package, found {len(found)}", 4)
        try:
            packages[name] = json.loads(found[0].read_text())["version"]
        except (OSError, ValueError, KeyError) as exc:
            raise ReproError(f"Package record unreadable for {name}: {exc}", 4) from exc
    release = build["fsl_release"]
    detail = ", ".join(f"{name} {version}" for name, version in packages.items())
    return {
        "release": release,
        "install": build["install"],
        "packages": packages,
        "version_string": f"FSL {release} ({detail})",
        "bet2_banner": bet2_banner(),
    }


def bet2_banner() -> list[str]:
    """The identity bet2 prints about itself, verbatim; FSL 6.0.7.23 prints an internal build
    id (for example "Part of FSL (ID: 2412.6-dirty)"), not the release number."""
    try:
        response = subprocess.run(
            [str(tool("bet2"))],
            capture_output=True,
            text=True,
            env=fsl_environment(),
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReproError(f"Cannot execute bet2 for its version banner: {exc}", 4) from exc
    lines = [line.strip() for line in (response.stdout + response.stderr).splitlines()]
    banner = [line for line in lines if line.startswith(("Part of FSL", "BET "))]
    if not banner:
        raise ReproError("bet2 printed no version banner", 4)
    return banner


def analyze_subject(image: Path, sample_id: str, params: dict, directory: Path) -> dict:
    header = nifti_header(image)
    mask = skull_strip(image, directory, params["bet_frac"])
    voxels, volume_mm3 = volumes(mask)
    box = bounding_box(mask)
    metrics = {
        "schema_version": "1.0.0",
        "sample_id": sample_id,
        "dims": list(header.dims),
        "voxel_size_mm": list(header.voxel_size_mm),
        "brain_voxels": voxels,
        "brain_volume_mm3": volume_mm3,
        "qc": qc_mask(voxels, volume_mm3, box, header.dims),
        "parameter_sha256": fingerprint(science_params(params)),
    }
    write_json(directory / "metrics.json", metrics)
    return metrics
