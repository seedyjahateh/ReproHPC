"""Fetch T1w anatomicals from a public OpenNeuro dataset for fsl-bet-volumetry-v1.

Follows scripts/fetch_bbbc039.py. Downloads use `aws s3 sync --no-sign-request` from the
public openneuro.org bucket: no account or credentials. Every file is checked against the
requested snapshot before anything is written: its size and MD5 must equal the git-annex
key that OpenNeuro publishes for that snapshot, so bytes from a later version cannot pass
as this one. The manifest then records SHA-256, size, S3 key, S3 object version and annex
key per file.

    python scripts/fetch_openneuro.py --accession ds000001 --version 1.0.0 \
        --subjects 01 02 03 04 05 --aws "uvx --from awscli==1.46.1 aws"

If the output directory already holds a manifest (the committed state), the script runs in
reconstruct mode instead: it downloads exactly the recorded S3 object versions and requires
every SHA-256 to match, so the dataset is rebuilt from the manifest alone. Only manifests
and hashes are committed; the NIfTI files are gitignored.
"""

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reprohpc.errors import ReproError  # noqa: E402
from reprohpc.io import atomic_bytes, nifti_header, sha256, write_csv, write_json  # noqa: E402

BUCKET = "openneuro.org"
GRAPHQL = "https://openneuro.org/crn/graphql"
ANNEX_KEY = re.compile(r"^MD5E-s(?P<size>\d+)--(?P<md5>[0-9a-f]{32})(\.[A-Za-z0-9.]+)?$")
LICENSES = {"CC0": "CC0-1.0", "CC0-1.0": "CC0-1.0", "PDDL": "PDDL-1.0"}
SUPPORT = ("dataset_description.json", "CHANGES")


def snapshot(accession, version):
    """The snapshot's description and file listing, as OpenNeuro publishes them."""
    query = (
        "{ snapshot(datasetId: " + json.dumps(accession) + ", tag: " + json.dumps(version) + ")"
        " { tag description { Name License DatasetDOI Authors }"
        " files(recursive: true) { filename size id } } }"
    )
    request = urllib.request.Request(
        GRAPHQL,
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read())
    if body.get("errors") or not body.get("data", {}).get("snapshot"):
        raise ReproError(f"OpenNeuro has no snapshot {accession} {version}: {body.get('errors')}")
    data = body["data"]["snapshot"]
    if data["tag"] != version:
        raise ReproError(f"OpenNeuro returned tag {data['tag']}, not {version}")
    return data["description"], {entry["filename"]: entry for entry in data["files"]}


def aws(command, *arguments):
    argv = [*shlex.split(command), *arguments]
    response = subprocess.run(argv, capture_output=True, text=True, check=False)
    if response.returncode:
        raise ReproError(f"aws {' '.join(arguments[:2])} failed: {response.stderr.strip()}")
    return response.stdout


def md5(path):
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_against_snapshot(path, relative, listing):
    """Size and MD5 must equal the snapshot's annex key; otherwise this is not that version."""
    entry = listing.get(relative)
    if entry is None:
        raise ReproError(f"{relative} is not part of the requested snapshot")
    if path.stat().st_size != int(entry["size"]):
        raise ReproError(f"{relative}: size differs from the snapshot listing")
    match = ANNEX_KEY.match(entry["id"] or "")
    if match:
        if int(match["size"]) != int(entry["size"]) or md5(path) != match["md5"]:
            raise ReproError(
                f"{relative}: bytes differ from the snapshot's annex key {entry['id']}"
            )
        return entry["id"]
    if entry["id"] and entry["id"].startswith(("SHA256E-", "SHA256-")):
        digest = entry["id"].split("--", 1)[1].split(".", 1)[0]
        if sha256(path) != digest:
            raise ReproError(f"{relative}: bytes differ from the snapshot's annex key")
        return entry["id"]
    # Small files are stored in git, not the annex; OpenNeuro then publishes no content hash.
    return None


def object_version(command, key):
    head = json.loads(aws(command, "s3api", "head-object", "--no-sign-request",
                          "--bucket", BUCKET, "--key", key))  # fmt: skip
    return head.get("VersionId"), head.get("ETag", "").strip('"')


def fetch(args):
    description, listing = snapshot(args.accession, args.version)
    license_id = LICENSES.get(description.get("License"))
    if license_id is None:
        raise ReproError(f"Refusing unrecognised licence {description.get('License')!r}")
    wanted = [f"sub-{s}/anat/sub-{s}_T1w.nii.gz" for s in args.subjects]
    missing = [name for name in wanted if name not in listing]
    if missing:
        raise ReproError(f"Not in {args.accession} {args.version}: {missing}")
    cache = args.cache / args.accession
    includes = [part for name in (*wanted, *SUPPORT) for part in ("--include", name)]
    aws(args.aws, "s3", "sync", "--no-sign-request", f"s3://{BUCKET}/{args.accession}/",
        str(cache), "--exclude", "*", *includes)  # fmt: skip

    rows, sources = [], []
    for relative in (*wanted, *SUPPORT):
        source = cache / relative
        annex = verify_against_snapshot(source, relative, listing)
        s3_key = f"{args.accession}/{relative}"
        version_id, etag = object_version(args.aws, s3_key)
        target = args.output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        record = {
            "path": relative,
            "sha256": sha256(target),
            "size_bytes": target.stat().st_size,
            "s3_key": s3_key,
            "s3_version_id": version_id,
            "s3_etag": etag,
            "annex_key": annex,
        }
        sources.append(record)
        if relative in wanted:
            nifti_header(target)  # refuse anything the pipeline could not read
            rows.append(
                {
                    "sample_id": relative.split("/")[0],
                    **{field: record[field] for field in ("path", "sha256", "size_bytes")},
                }
            )
    return description, license_id, rows, sources


def reconstruct(args):
    """Rebuild the dataset from the committed manifest: exact object versions, exact hashes."""
    sources = json.loads((args.output / "sources.json").read_text())["files"]
    for record in sources:
        target = args.output / record["path"]
        if target.is_file() and sha256(target) == record["sha256"]:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".partial")
        arguments = ["s3api", "get-object", "--no-sign-request", "--bucket", BUCKET,
                     "--key", record["s3_key"]]  # fmt: skip
        if record["s3_version_id"]:
            arguments += ["--version-id", record["s3_version_id"]]
        aws(args.aws, *arguments, str(temporary))
        if sha256(temporary) != record["sha256"]:
            temporary.unlink()
            raise ReproError(f"{record['path']}: downloaded bytes differ from the manifest")
        temporary.replace(target)
    print(f"reconstructed {len(sources)} files in {args.output}, all SHA-256 verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accession", default="ds000001")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--subjects", nargs="+", default=["01", "02", "03", "04", "05"])
    parser.add_argument("--cache", type=Path, default=ROOT / ".tools/openneuro")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reference", type=Path, default=ROOT / "references/openneuro/1.0.0")
    parser.add_argument("--aws", default="aws", help="command that runs the AWS CLI")
    args = parser.parse_args()
    if not re.fullmatch(r"ds\d{6}", args.accession):
        raise SystemExit(f"Not an OpenNeuro accession: {args.accession}")
    if not all(re.fullmatch(r"[A-Za-z0-9]+", s) for s in args.subjects):
        raise SystemExit("Subject labels must be alphanumeric")
    args.output = args.output or ROOT / "data/openneuro" / args.accession / args.version
    if (args.output / "sources.json").is_file():
        reconstruct(args)
        return

    description, license_id, rows, sources = fetch(args)
    rows.sort(key=lambda row: row["sample_id"])
    write_csv(args.output / "samples.csv", ["sample_id", "path", "sha256", "size_bytes"], rows)
    write_json(
        args.output / "sources.json",
        {
            "schema_version": "1.0.0",
            "accession": args.accession,
            "version": args.version,
            "bucket": BUCKET,
            "verification": (
                "Each file's size and MD5 equal the git-annex key OpenNeuro publishes for this "
                "snapshot; files stored in git carry no published hash and were size-checked."
            ),
            "files": sources,
        },
    )
    doi = description.get("DatasetDOI")
    write_json(
        args.output / "dataset.json",
        {
            "schema_version": "1.0.0",
            "dataset_id": args.accession,
            "version": args.version,
            "title": f"{description['Name']} ({args.accession}), T1w anatomicals",
            "description": (
                f"T1-weighted anatomical MRI of {len(rows)} subjects "
                f"({', '.join(r['sample_id'] for r in rows)}) from OpenNeuro {args.accession} "
                f"version {args.version}, unmodified. Defaced by the dataset's publishers."
            ),
            "creators": description.get("Authors") or ["OpenNeuro contributors"],
            "license": license_id,
            "source": f"https://openneuro.org/datasets/{args.accession}/versions/{args.version}",
            "created": "2026-09-21",
            "manifest_sha256": sha256(args.output / "samples.csv"),
            "modality": "MRI, T1-weighted anatomical",
            "species": "Homo sapiens",
            "access": "public",
            "doi": doi.removeprefix("doi:").removeprefix("https://doi.org/") if doi else None,
        },
    )
    # Volumes use each image's own voxel size from its NIfTI header; no external calibration.
    calibration = args.reference / "calibration.json"
    write_json(calibration, {"schema_version": "1.0.0", "pixel_size_um": None, "unit": "um"})
    write_json(
        args.reference / "reference.json",
        {
            "schema_version": "1.0.0",
            "reference_id": "openneuro-header-geometry",
            "version": "1.0.0",
            "source": "https://nifti.nimh.nih.gov/nifti-1",
            "license": "CC0-1.0",
            "calibration": {
                "path": "calibration.json",
                "sha256": sha256(calibration),
                "size_bytes": calibration.stat().st_size,
            },
        },
    )
    files = sorted(
        p for p in args.output.rglob("*") if p.is_file() and p.name != "checksums.sha256"
    )
    atomic_bytes(
        args.output / "checksums.sha256",
        "".join(f"{sha256(p)}  {p.relative_to(args.output).as_posix()}\n" for p in files).encode(),
    )
    print(f"{len(rows)} T1w images from {args.accession} {args.version} -> {args.output}")


if __name__ == "__main__":
    main()
