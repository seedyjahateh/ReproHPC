"""Build current code, export OCI, convert exact bytes to SIF in the existing lab."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def execute(*args):
    return subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    subprocess.run(
        [
            "docker",
            "build",
            "--provenance=false",
            "-t",
            "reprohpc-analysis:dev",
            "-f",
            "containers/Dockerfile",
            ".",
        ],
        cwd=ROOT,
        check=True,
    )
    execute("docker", "save", "-o", str(artifacts / "analysis.oci.tar"), "reprohpc-analysis:dev")
    subprocess.run(
        [
            "docker",
            "exec",
            "reprohpc-lab",
            "apptainer",
            "build",
            "--force",
            "/workspace/artifacts/reprohpc.sif",
            "docker-archive:///workspace/artifacts/analysis.oci.tar",
        ],
        check=True,
    )
    path = artifacts / "reprohpc.sif"
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    inspect = json.loads(execute("docker", "image", "inspect", "reprohpc-analysis:dev"))[0]
    record = {
        "schema_version": "1.0.0",
        "kind": "unpublished-candidate",
        "sif_sha256": digest,
        "sif_path": "artifacts/reprohpc.sif",
        "oci_image_id": inspect["Id"],
        # Docker's containerd image store reports the manifest digest. The classic store
        # (GitHub-hosted runners) has none until the image is pushed, so record null
        # rather than presenting the config id above as a manifest digest.
        "oci_digest": (inspect.get("Descriptor") or {}).get("digest"),
        "platform": f"{inspect['Os']}/{inspect['Architecture']}",
        "oci_repo_digests": inspect.get("RepoDigests", []),
        "runtime": execute("docker", "exec", "reprohpc-lab", "apptainer", "--version"),
        "image_python_packages": json.loads(
            execute(
                "docker",
                "exec",
                "-u",
                "researcher",
                "reprohpc-lab",
                "apptainer",
                "exec",
                "--cleanenv",
                "/workspace/artifacts/reprohpc.sif",
                "python",
                "-m",
                "pip",
                "list",
                "--format=json",
            )
        ),
        "image_os_packages": execute(
            "docker",
            "exec",
            "-u",
            "researcher",
            "reprohpc-lab",
            "apptainer",
            "exec",
            "--cleanenv",
            "/workspace/artifacts/reprohpc.sif",
            "dpkg-query",
            "-W",
        ),
    }
    (artifacts / "candidate.json").write_text(json.dumps(record, indent=2) + "\n")
    (artifacts / "reprohpc.sif.sha256").write_text(f"{digest}  reprohpc.sif\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
