"""Recorded author command walkthrough; not an independent novice usability study."""

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from reprohpc.archive import extract  # noqa: E402
from reprohpc.io import read_json, sha256, write_json  # noqa: E402
from reprohpc.provenance import verify_run  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    sif = args.sif.resolve()
    started = time.monotonic()
    records = []

    def execute(label, argv, expected=0):
        begin = time.monotonic()
        result = subprocess.run(
            [sys.executable, *map(str, argv)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
        (args.output / f"{label}.log").write_text(result.stdout + result.stderr)
        record = {
            "episode": label,
            "argv": list(map(str, argv)),
            "exit_code": result.returncode,
            "elapsed_seconds": time.monotonic() - begin,
        }
        records.append(record)
        print(json.dumps(record), flush=True)
        assert result.returncode == expected, f"{label}: inspect {args.output / (label + '.log')}"
        return result.stdout

    execute("doctor", [ROOT / "reprohpc", "doctor", "--profile", "local"])
    common = [
        ROOT / "reprohpc",
        "run",
        "--profile",
        "local",
        "--params-file",
        "params/demo.yaml",
        "--sif",
        sif,
        "--sif-sha256",
        sha256(sif),
        "--work-dir",
        args.output / "work",
        "--launch-dir",
        args.output / "launch",
    ]
    execute("first", [*common, "--outdir", args.output / "first"])
    execute("verify", [ROOT / "reprohpc", "verify", "--run", args.output / "first"])
    execute(
        "compare",
        [
            ROOT / "reprohpc",
            "compare",
            "--expected",
            "tests/expected/demo",
            "--actual",
            args.output / "first",
        ],
    )
    blank = read_json(args.output / "first/samples/blank/metrics.json")
    assert (
        blank["object_count"] == 0
        and blank["mean_area_px"] is None
        and blank["qc"] == ["NO_OBJECTS"]
    )
    assert len(list((args.output / "first/samples").iterdir())) == 12
    changed = execute(
        "change-science", [*common, "--threshold", "200", "--outdir", args.output / "changed"]
    )
    assert read_json(args.output / "changed/provenance/params.resolved.json")["threshold"] == 200
    execute(
        "expected-difference",
        [
            ROOT / "reprohpc",
            "compare",
            "--expected",
            "tests/expected/demo",
            "--actual",
            args.output / "changed",
        ],
        expected=5,
    )
    # Execute the actual printed command, through the same interpreter. No shell.
    command = next(
        line.removeprefix("Resume: ")
        for line in changed.splitlines()
        if line.startswith("Resume: ")
    )
    execute("resume", shlex.split(command))
    tasks = [
        json.loads(line)
        for line in (args.output / "changed-resumed/provenance/tasks.jsonl")
        .read_text()
        .splitlines()
    ]
    analysis = [task for task in tasks if task["process"].endswith("ANALYZE_BATCH")]
    assert analysis and all(task["status"] == "CACHED" for task in analysis)
    execute(
        "export",
        [
            ROOT / "reprohpc",
            "export",
            "--run",
            args.output / "first",
            "--output",
            args.output / "first.tar.gz",
        ],
    )
    extract(args.output / "first.tar.gz", args.output / "public")
    assert verify_run(args.output / "public")["verified"]
    total = time.monotonic() - started
    first = next(item["elapsed_seconds"] for item in records if item["episode"] == "first")
    evidence = {
        "kind": "script-assisted-author-command-walkthrough",
        "independent_novice_study": False,
        "sif_sha256": sha256(sif),
        "elapsed_seconds": total,
        "first_run_seconds": first,
        "commands": records,
        "verified": True,
        "gates": {
            "prepared_first_run_under_5_minutes": first < 300,
            "command_walkthrough_under_30_minutes": total < 1800,
        },
    }
    write_json(args.output / "evidence.json", evidence)
    print(json.dumps(evidence), flush=True)
    if not all(evidence["gates"].values()):
        raise SystemExit(
            "Commands passed, but PRD timing targets were not met; inspect evidence.json"
        )


if __name__ == "__main__":
    main()
