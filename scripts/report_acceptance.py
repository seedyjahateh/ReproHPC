"""Create a genuine container-generated UI preview and verify its exported artifacts."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reprohpc.archive import export_run, extract  # noqa: E402
from reprohpc.io import read_json, sha256, write_json  # noqa: E402
from reprohpc.provenance import compare, verify_run  # noqa: E402
from reprohpc.reporting import check_report_links  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--export-directory", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    digest = sha256(args.sif)
    command = [
        sys.executable,
        str(ROOT / "reprohpc"),
        "run",
        "--profile",
        "local",
        "--params-file",
        str(ROOT / "params/demo.yaml"),
        "--sif",
        str(args.sif),
        "--sif-sha256",
        digest,
        "--batch-size",
        "4",
        "--outdir",
        str(args.output / "run"),
        "--work-dir",
        str(args.output / "work"),
        "--launch-dir",
        str(args.output / "launch"),
    ]
    with (args.output / "workflow.log").open("w") as log:
        subprocess.run(
            command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600
        )
    verified = verify_run(args.output / "run")
    comparison = compare(ROOT / "tests/expected/demo", args.output / "run", exact=True)
    archive = args.output / "results.tar.gz"
    export_run(args.output / "run", archive)
    extract(archive, args.export_directory)
    exported = verify_run(args.export_directory)
    html_path = args.export_directory / "report/index.html"
    # Every report link, including per-sample downloads, must resolve in both packages.
    run_links = check_report_links(args.output / "run")
    export_links = check_report_links(args.export_directory)
    assert run_links == export_links, "Run and export report links differ"
    # Compare all baked source/assets, including the UI, to the current checkout.
    inspection = """import hashlib,json,pathlib,reprohpc
root=pathlib.Path(reprohpc.__file__).parent
print(json.dumps({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}))
"""
    baked = json.loads(
        subprocess.check_output(
            ["apptainer", "exec", "--cleanenv", str(args.sif), "python", "-c", inspection],
            text=True,
        )
    )
    local = {
        path.relative_to(ROOT / "src/reprohpc").as_posix(): sha256(path)
        for path in sorted((ROOT / "src/reprohpc").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert baked == local, "Baked package differs from current source/assets"
    record = {
        "kind": "actual-nextflow-apptainer-report-acceptance",
        "sif_sha256": digest,
        "command": command,
        "elapsed_seconds": time.monotonic() - started,
        "run_id": read_json(args.output / "run/provenance/run.json")["run_id"],
        "verification": verified,
        "comparison": comparison,
        "export_verification": exported,
        "report_sha256": sha256(html_path),
        "report_bytes": html_path.stat().st_size,
        "report_links_verified": export_links,
        "export_directory": str(args.export_directory),
        "baked_source_verified": True,
        "baked_source_files": baked,
    }
    write_json(args.output / "evidence.json", record)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
