"""DICOM ingest: fetch a public sample series, de-identify it, convert it to NIfTI.

The series comes from neurolabusc/dcm_qa_nih (BSD-2-Clause), a dataset published to validate
dcm2niix, pinned here to one commit with a per-file SHA-256 manifest. De-identification and
conversion both run inside the FSL image; afterwards this script re-reads every produced byte
on the host and fails if any original identifier survives, so the container's own verdict is
not the only check.

    python scripts/ingest_dicom.py --sif /scratch/reprohpc-fsl-candidate-4.sif \
        --output /scratch/dicom-ingest

No patient data is involved: the series is a scanner QA acquisition whose patient identity is
a placeholder ("Test^Regression" / "DEV"), though it does carry real institution, device and
date identifiers, which is what makes it a useful de-identification test.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reprohpc.errors import ReproError  # noqa: E402
from reprohpc.io import read_json, sha256, write_json  # noqa: E402

REPOSITORY = "neurolabusc/dcm_qa_nih"
COMMIT = "6a11dc671ac6a0631585d59c840e7ff364494943"
SERIES = "In/20180918Si/mr_0003"
MANIFEST = ROOT / "data/dicom-sample/dcm_qa_nih/sources.json"
# Values the public series carries and that must not appear in any output byte.
WATCHED = (
    "PatientName", "PatientID", "InstitutionName", "InstitutionAddress",
    "InstitutionalDepartmentName", "StationName", "DeviceSerialNumber", "StudyDate",
    "StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "FrameOfReferenceUID",
)  # fmt: skip
MIN_SCAN = 5


def fetch(cache: Path):
    """Download the pinned series, verifying against the committed manifest when present."""
    listing = json.loads(
        urllib.request.urlopen(
            f"https://api.github.com/repos/{REPOSITORY}/git/trees/{COMMIT}?recursive=1", timeout=120
        ).read()
    )
    wanted = sorted(
        entry["path"]
        for entry in listing["tree"]
        if entry["type"] == "blob"
        and entry["path"].startswith(SERIES)
        and entry["path"].endswith(".dcm")
    )
    if not wanted:
        raise ReproError(f"No DICOM files under {SERIES} at {COMMIT[:12]}")
    recorded = (
        {entry["path"]: entry for entry in read_json(MANIFEST)["files"]}
        if MANIFEST.is_file()
        else {}
    )
    files = []
    for relative in wanted:
        target = cache / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            url = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{relative}"
            with urllib.request.urlopen(url, timeout=120) as response:
                target.write_bytes(response.read())
        digest = sha256(target)
        if relative in recorded and recorded[relative]["sha256"] != digest:
            raise ReproError(f"{relative}: bytes differ from the committed manifest")
        files.append({"path": relative, "sha256": digest, "size_bytes": target.stat().st_size})
    if not recorded:
        write_json(
            MANIFEST,
            {
                "schema_version": "1.0.0",
                "repository": f"https://github.com/{REPOSITORY}",
                "commit": COMMIT,
                "license": "BSD-2-Clause",
                "description": (
                    "Scanner QA series published to validate dcm2niix. Patient identity is a "
                    "placeholder; institution, device and dates are real. No patient data."
                ),
                "files": files,
            },
        )
    return cache / SERIES, files


def originals(source: Path):
    """The identifying values present in the source, read with pydicom if available."""
    try:
        import pydicom
    except ImportError:
        return {}
    data = pydicom.dcmread(next(source.glob("*.dcm")))
    found = {}
    for name in WATCHED:
        if name in data:
            value = str(data[name].value).strip()
            if len(value) >= MIN_SCAN:
                found[name] = value
    return found


def in_container(sif: str, *command):
    argv = ["docker", "exec", "-u", "researcher", "reprohpc-lab", "apptainer", "exec",
            "--cleanenv", "-B", "/scratch,/workspace", sif, *command]  # fmt: skip
    response = subprocess.run(argv, capture_output=True, text=True, check=False)
    if response.returncode:
        raise ReproError(f"container command failed: {response.stderr.strip()[-1500:]}")
    return response.stdout


def scan_outputs(paths, values):
    """Independent host-side check: no watched value may appear in any produced byte."""
    leaks = []
    for path in paths:
        blob = path.read_bytes()
        for name, value in values.items():
            if value.encode() in blob:
                leaks.append(f"{path.name}: {name}")
    return leaks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Container paths stay strings: Path() would rewrite them with backslashes on Windows.
    parser.add_argument("--sif", required=True, help="container path to the FSL SIF")
    parser.add_argument("--output", required=True, help="container path, e.g. /scratch/...")
    parser.add_argument("--cache", type=Path, default=ROOT / ".tools/dcm_qa_nih")
    parser.add_argument("--host-output", type=Path, help="where --output is visible on this host")
    parser.add_argument(
        "--keys",
        default=None,
        help="container path for the re-identification key; keep it off any shared or "
        "version-controlled path (default: alongside --output)",
    )
    args = parser.parse_args()
    keys = args.keys or f"{args.output}-keys"

    source, files = fetch(args.cache.resolve())
    values = originals(source)
    if not values:
        raise SystemExit("Install pydicom on the host to record the source identifiers")
    source_in_container = "/workspace/" + str(source.resolve().relative_to(ROOT)).replace("\\", "/")
    result = json.loads(
        in_container(
            args.sif,
            "python",
            "-m",
            "reprohpc.deident",
            "--input",
            source_in_container,
            "--output",
            f"{args.output}/deidentified",
            "--mapping",
            f"{keys}/mapping.json",
            "--audit",
            f"{keys}/audit.json",
            "--convert",
            f"{args.output}/nifti",
        )  # fmt: skip
    )
    host_output = args.host_output or args.output
    produced = sorted(p for p in host_output.rglob("*") if p.is_file())
    leaks = scan_outputs(produced, values)
    record = {
        "kind": "dicom-deidentification-ingest",
        "source": {
            "repository": f"https://github.com/{REPOSITORY}",
            "commit": COMMIT,
            "series": SERIES,
            "license": "BSD-2-Clause",
            "files": files,
        },
        "source_identifiers_watched": sorted(values),
        "result": result,
        "outputs": [str(p.relative_to(host_output)) for p in produced],
        "independent_host_scan": {
            "files_scanned": len(produced),
            "leaks": leaks,
            "clean": not leaks,
        },
    }
    print(
        json.dumps(
            {k: record[k] for k in ("source_identifiers_watched", "independent_host_scan")},
            indent=2,
        )
    )
    write_json(host_output.parent / f"{host_output.name}-evidence.json", record)
    if leaks:
        raise SystemExit(f"Identifiers survived into the outputs: {leaks}")
    if not any(re.search(r"\.nii\.gz$", name) for name in record["outputs"]):
        raise SystemExit("No NIfTI was produced")


if __name__ == "__main__":
    main()
