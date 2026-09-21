"""Build current code, export OCI, convert exact bytes to SIF in the existing lab.

Defaults build the analysis image (containers/Dockerfile -> artifacts/reprohpc.sif). The FSL
image is built with:

    python scripts/build_candidate.py --dockerfile containers/Dockerfile.fsl \
        --image reprohpc-fsl:dev --name reprohpc-fsl --discard-oci
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def execute(*args):
    return subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def in_sif(sif, *command):
    return execute(
        "docker", "exec", "-u", "researcher", "reprohpc-lab",
        "apptainer", "exec", "--cleanenv", sif, *command,
    )  # fmt: skip


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", default="containers/Dockerfile")
    parser.add_argument("--image", default="reprohpc-analysis:dev")
    parser.add_argument("--name", default="reprohpc", help="SIF and record basename")
    parser.add_argument(
        "--discard-oci",
        action="store_true",
        help="delete the exported OCI archive once the SIF exists (saves disk; not reproducible from it)",
    )
    args = parser.parse_args()
    default = args.name == "reprohpc"
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    oci = artifacts / ("analysis.oci.tar" if default else f"{args.name}.oci.tar")
    sif_name = f"{args.name}.sif"
    record_name = "candidate.json" if default else f"candidate-{args.name}.json"
    subprocess.run(
        ["docker", "build", "--provenance=false", "-t", args.image, "-f", args.dockerfile, "."],
        cwd=ROOT,
        check=True,
    )
    execute("docker", "save", "-o", str(oci), args.image)
    subprocess.run(
        [
            "docker",
            "exec",
            "reprohpc-lab",
            "apptainer",
            "build",
            "--force",
            f"/workspace/artifacts/{sif_name}",
            f"docker-archive:///workspace/artifacts/{oci.name}",
        ],
        check=True,
    )
    if args.discard_oci:
        oci.unlink()
    path = artifacts / sif_name
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    inspect = json.loads(execute("docker", "image", "inspect", args.image))[0]
    sif = f"/workspace/artifacts/{sif_name}"
    record = {
        "schema_version": "1.0.0",
        "kind": "unpublished-candidate",
        "sif_sha256": digest,
        "sif_path": f"artifacts/{sif_name}",
        "sif_size_bytes": path.stat().st_size,
        "dockerfile": args.dockerfile,
        "oci_image_id": inspect["Id"],
        # Docker's containerd image store reports the manifest digest. The classic store
        # (GitHub-hosted runners) has none until the image is pushed, so record null
        # rather than presenting the config id above as a manifest digest.
        "oci_digest": (inspect.get("Descriptor") or {}).get("digest"),
        "platform": f"{inspect['Os']}/{inspect['Architecture']}",
        "oci_repo_digests": inspect.get("RepoDigests", []),
        "runtime": execute("docker", "exec", "reprohpc-lab", "apptainer", "--version"),
        "image_python_packages": json.loads(
            in_sif(sif, "python", "-m", "pip", "list", "--format=json")
        ),
        "image_os_packages": in_sif(sif, "dpkg-query", "-W"),
    }
    # FSL packages as installed, read from conda's own metadata rather than asserted here.
    listing = in_sif(sif, "sh", "-c", "ls /opt/fsl/conda-meta/*.json 2>/dev/null || true")
    if listing:
        record["image_conda_packages"] = sorted(
            Path(line).name.removesuffix(".json") for line in listing.splitlines()
        )
    (artifacts / record_name).write_text(json.dumps(record, indent=2) + "\n")
    (artifacts / f"{sif_name}.sha256").write_text(f"{digest}  {sif_name}\n")
    print(json.dumps({k: v for k, v in record.items() if not k.startswith("image_")}, indent=2))


if __name__ == "__main__":
    main()
