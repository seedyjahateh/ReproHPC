"""Synthetic gate fixtures exercise rejection logic, never publication evidence."""

import importlib.util
from pathlib import Path

import pytest

from reprohpc.archive import validate_release
from reprohpc.config import resolve_params
from reprohpc.errors import ReproError
from reprohpc.io import fingerprint, sha256, write_json

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_gate", ROOT / "scripts/validate_release.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.fixture
def lock_fixture(tmp_path):
    """Well-formed identifiers here are unit-only, never resolving DOI evidence."""
    lock = {
        "schema_version": "1.0.0",
        "version": "unit-fixture",
        "source_commit": "abcdef0123456789" * 2 + "abcdef01",
        "software_doi": "10.99999/unit-software",
        "data_doi": "10.99999/unit-data",
        "nextflow_version": "25.10.4",
        "nextflow_sha256": fingerprint("unit-nextflow"),
        "oci_digest": "sha256:" + fingerprint("unit-oci"),
        "platform": "linux/amd64",
        "environment": {
            "python": "3.12.3",
            "java_vendor": "Ubuntu",
            "java_build": "21.0.12+8-1~24.04",
            "apptainer": "1.5.3",
            "slurm": "23.11.4",
            "docker_base_digest": "sha256:" + fingerprint("unit-base"),
            "requirements_sha256": fingerprint("unit-science-lock"),
            "host_requirements_sha256": fingerprint("unit-host-lock"),
            "build_requirements_sha256": fingerprint("unit-build-lock"),
            "python_packages": [{"name": "numpy", "version": "2.2.6"}],
            "build_tools": {"pip": "25.2", "setuptools": "80.9.0", "wheel": "0.45.1"},
        },
        "params": resolve_params(),
    }
    for kind in ("source", "sif", "data", "reference", "expected"):
        lock[kind] = {
            "url": f"https://unit.invalid/{kind}",
            "size_bytes": 1,
            "sha256": fingerprint(kind),
        }
    path = tmp_path / "unit-lock.json"
    write_json(path, lock)
    return path, lock


def test_complete_lock_structure(lock_fixture):
    path, lock = lock_fixture
    assert validate_release(path) == lock


@pytest.mark.parametrize(
    "fault", ["mutable-build", "missing-pin", "placeholder", "same-doi", "unknown-param", "sandbox"]
)
def test_release_lock_rejects_incomplete_identity(lock_fixture, fault):
    path, lock = lock_fixture
    if fault == "mutable-build":
        lock["environment"]["build_tools"]["pip"] = "latest"
    elif fault == "missing-pin":
        del lock["environment"]["java_build"]
    elif fault == "placeholder":
        lock["sif"]["sha256"] = "0" * 64
    elif fault == "same-doi":
        lock["data_doi"] = lock["software_doi"]
    elif fault == "unknown-param":
        lock["params"]["threshhold"] = 127
    else:
        lock["data"]["url"] = "https://sandbox.zenodo.org/records/123"
    write_json(path, lock)
    with pytest.raises(ReproError):
        validate_release(path)


@pytest.mark.parametrize(
    "fault", [None, "tamper", "coverage", "skip", "no-tests", "benchmark", "raw"]
)
def test_quality_gate_reads_evidence_files(tmp_path, fault):
    files = {
        "coverage": '<coverage line-rate="0.90" branch-rate="0.80"/>',
        "tests": '<testsuites><testsuite tests="10" failures="0" errors="0" skipped="0"/></testsuites>',
        "slurm": "123_0|COMPLETED|0:0|unit-only accounting fixture\n",
    }
    if fault == "coverage":
        files["coverage"] = files["coverage"].replace("0.90", "0.50")
    elif fault == "skip":
        files["tests"] = files["tests"].replace('skipped="0"', 'skipped="1"')
    elif fault == "no-tests":
        files["tests"] = "<testsuites/>"
    artifacts = {}
    for kind, content in files.items():
        path = tmp_path / (kind + ".txt")
        path.write_text(content)
        artifacts[kind] = {"path": path.name, "sha256": sha256(path)}
    path = tmp_path / "benchmark.json"
    write_json(
        path,
        {
            "gates": {
                "qualified_environment_and_workload": fault != "benchmark",
                "four_worker_speedup": True,
                "end_to_end_benefit": True,
                "resource_targets": True,
                "accounting_complete": True,
                "overhead_ratio": 1.1,
            }
        },
    )
    artifacts["benchmark"] = {"path": path.name, "sha256": sha256(path)}
    artifacts["slurm_raw"] = [] if fault == "raw" else [artifacts.pop("slurm")]
    if fault == "tamper":
        (tmp_path / artifacts["coverage"]["path"]).write_text("modified after recording hash")
    if fault:
        with pytest.raises(ValueError):
            gate.validate_quality({"artifacts": artifacts}, tmp_path)
    else:
        gate.validate_quality({"artifacts": artifacts}, tmp_path)
