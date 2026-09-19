"""Read installed notices from the actual SIF, retaining their bytes and hashes."""

import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sif", required=True, help="Path inside the disposable lab")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    code = """
import hashlib, importlib.metadata, json, pathlib
packages = []
notices = {}
for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
    paths = []
    for name in distribution.files or []:
        path = pathlib.Path(distribution.locate_file(name))
        if path.is_file() and path.name.lower().startswith(('license', 'copying', 'notice')):
            data = path.read_bytes()
            notices[str(path)] = {'sha256': hashlib.sha256(data).hexdigest(), 'text': data.decode('utf-8', errors='replace')}
            paths.append(str(path))
    packages.append({'name': distribution.metadata['Name'], 'version': distribution.version,
                     'license_expression': distribution.metadata.get('License-Expression'), 'notice_paths': paths})
for path in sorted(pathlib.Path('/usr/share/doc').glob('*/copyright')):
    if path.is_file():
        data = path.read_bytes()
        notices[str(path)] = {'sha256': hashlib.sha256(data).hexdigest(), 'text': data.decode('utf-8', errors='replace')}
print(json.dumps({'python_packages': packages, 'notices': notices}, ensure_ascii=False))
"""
    command = [
        "docker",
        "exec",
        "-u",
        "researcher",
        "reprohpc-lab",
        "apptainer",
        "exec",
        "--cleanenv",
        args.sif,
        "python",
        "-c",
        code,
    ]
    record = json.loads(subprocess.check_output(command, text=True, encoding="utf-8"))
    record["sif_sha256"] = subprocess.check_output(
        ["docker", "exec", "reprohpc-lab", "sha256sum", args.sif], text=True
    ).split()[0]
    record["kind"] = "installed-license-inventory; not legal approval"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"Recorded {len(record['python_packages'])} Python packages and {len(record['notices'])} notices"
    )


if __name__ == "__main__":
    main()
