"""Download verified setup artifacts and build only the disposable lab image."""

import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    context = ROOT / ".tools/lab-build"
    context.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / "environment.lock.json").read_text())
    for artifact in lock["assets"]:
        destination = context / artifact["name"]
        existing = ROOT / ".tools" / artifact["name"]
        if not destination.exists():
            if existing.exists():
                shutil.copyfile(existing, destination)
            else:
                urllib.request.urlretrieve(artifact["url"], destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != artifact["sha256"]:
            raise SystemExit(f"Checksum mismatch: {artifact['name']}")
    shutil.copyfile(ROOT / "ops/slurm/Dockerfile", context / "Dockerfile")
    shutil.copyfile(ROOT / "requirements-dev.lock", context / "requirements-dev.lock")
    shutil.copyfile(ROOT / "requirements-build.lock", context / "requirements-build.lock")
    subprocess.run(["docker", "build", "-t", "reprohpc-lab:dev", str(context)], check=True)


if __name__ == "__main__":
    main()
