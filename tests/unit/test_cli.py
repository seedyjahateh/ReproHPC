from pathlib import Path

import pytest

from reprohpc.cli import command_output, doctor, main, parser, source_snapshot
from reprohpc.errors import ReproError

ROOT = Path(__file__).resolve().parents[2]


def test_cli_parameter_types():
    args = parser().parse_args(
        [
            "run",
            "--params-file",
            "x",
            "--outdir",
            "y",
            "--sif",
            "z",
            "--sif-sha256",
            "a" * 64,
            "--threshold",
            "2",
            "--no-write-previews",
        ]
    )
    assert args.threshold == 2 and args.write_previews is False
    with pytest.raises(SystemExit):
        parser().parse_args(["run", "--threshhold", "2"])


def test_doctor_reports_missing_tools(monkeypatch):
    monkeypatch.setenv("PATH", "")
    result = doctor("slurm")
    assert not result["ready"]
    assert any("nextflow" in e for e in result["errors"])


def test_command_failure():
    with pytest.raises(ReproError, match="Could not execute"):
        command_output(["this-command-does-not-exist-reprohpc"])


def test_cli_missing_release_returns_nonzero(tmp_path):
    assert main(["prepare", "--release-lock", str(tmp_path / "missing.json")]) == 3
    assert main(["verify", "--run", str(tmp_path / "missing")]) == 3


def test_source_snapshot(tmp_path):
    import tarfile

    identity = source_snapshot(ROOT, tmp_path / "source.tar.gz")
    assert len(identity["source_tree_sha256"]) == 64
    assert len(identity["source_archive_sha256"]) == 64
    assert (tmp_path / "source.tar.gz.sha256").exists()
    with tarfile.open(tmp_path / "source.tar.gz") as archive:
        assert {"README.md", "PRD.md", "TASKS.md"} <= set(archive.getnames())
