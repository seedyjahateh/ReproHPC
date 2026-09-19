"""Fail closed on absent public identities, mismatched candidate, or missing real evidence."""

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from reprohpc.archive import validate_release  # noqa: E402
from reprohpc.io import confined, sha256  # noqa: E402


def evidence_file(entry, root):
    """Evidence manifest binds each reviewable raw artifact by relative path/hash."""
    path = confined(root, entry["path"])
    if not path.is_file() or sha256(path) != entry["sha256"]:
        raise ValueError(f"Missing or modified release evidence: {entry['path']}")
    return path


def validate_quality(evidence, root):
    artifacts = evidence["artifacts"]
    coverage = ET.parse(evidence_file(artifacts["coverage"], root)).getroot()
    if float(coverage.attrib["line-rate"]) < 0.85 or float(coverage.attrib["branch-rate"]) < 0.75:
        raise ValueError("Release coverage is below PRD thresholds")
    tests = ET.parse(evidence_file(artifacts["tests"], root)).getroot()
    suites = [tests] if tests.tag == "testsuite" else list(tests.iter("testsuite"))
    if not suites or sum(int(s.attrib.get("tests", 0)) for s in suites) == 0:
        raise ValueError("Release has no executed tests")
    if any(int(s.attrib.get(key, 0)) for s in suites for key in ("failures", "errors", "skipped")):
        raise ValueError("Full release test suite has failures, errors, or skipped integrations")
    summary = json.loads(evidence_file(artifacts["benchmark"], root).read_text())
    gates = summary["gates"]
    if (
        any(
            gates.get(key) is not True
            for key in (
                "qualified_environment_and_workload",
                "four_worker_speedup",
                "end_to_end_benefit",
                "resource_targets",
                "accounting_complete",
            )
        )
        or not 0 < gates.get("overhead_ratio", float("inf")) <= 1.25
    ):
        raise ValueError("Qualified benchmark does not meet PRD release gates")
    if not artifacts.get("slurm_raw"):
        raise ValueError("Raw scheduler evidence is missing")
    for entry in artifacts["slurm_raw"]:
        evidence_file(entry, root)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    lock = validate_release(args.lock)
    evidence = json.loads(args.evidence.read_text())
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if revision != lock["source_commit"]:
        raise SystemExit("Release lock does not identify the current commit")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise SystemExit("Release requires a clean checkout")
    required = [
        "array_groups_4_4_2",
        "accounting",
        "resource_requests",
        "retry_individual",
        "cancel_cleanup",
        "oom_enforced",
        "time_limit",
        "offline",
        "goldens",
        "resume",
    ]
    if (
        evidence.get("sif_sha256") != lock["sif"]["sha256"]
        or evidence.get("source_commit") != revision
    ):
        raise SystemExit("Slurm evidence is not for the exact candidate")
    if any(evidence.get(key) is not True for key in required):
        raise SystemExit("Real Slurm acceptance evidence incomplete")
    validate_quality(evidence, args.evidence.resolve().parent)
    print("Release identities, tests, coverage, benchmark, and exact-candidate Slurm gates passed")


if __name__ == "__main__":
    main()
