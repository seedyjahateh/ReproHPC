"""Fail-fast launcher contracts with explicit prerequisite doubles, no scheduler claims."""

import shutil
from pathlib import Path

import pytest

from reprohpc import cli
from reprohpc.config import EXECUTION, resolve_params, validate_execution
from reprohpc.errors import ReproError
from reprohpc.io import sha256

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "value",
    [
        {"unknown": 1},
        {"partition": "x;bad"},
        {"array_size": True},
        {"max_inflight": 0},
        {"array_size": 9, "max_inflight": 8},
        {"analysis_memory": "0 GB"},
        {"analysis_time": "forever"},
    ],
)
def test_invalid_execution_settings(value):
    with pytest.raises(ReproError) as error:
        validate_execution(value)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "key,value",
    [
        ("algorithm", "unknown"),
        ("connectivity", True),
        ("connectivity", 6),
        ("write_previews", "yes"),
        ("gaussian_sigma", float("nan")),
    ],
)
def test_invalid_scientific_settings(key, value):
    with pytest.raises(ReproError):
        resolve_params({key: value})


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    sif = tmp_path / "protocol-sif"
    sif.write_bytes(b"unit preflight only, not a container")
    args = cli.parser().parse_args(
        [
            "run",
            "--params-file",
            str(ROOT / "params/demo.yaml"),
            "--outdir",
            str(tmp_path / "out"),
            "--work-dir",
            str(tmp_path / "work"),
            "--launch-dir",
            str(tmp_path / "launch"),
            "--sif",
            str(sif),
            "--sif-sha256",
            sha256(sif),
        ]
    )
    params = resolve_params(cli.load_yaml(args.params_file), base=ROOT)
    monkeypatch.setattr(
        cli, "doctor", lambda profile: {"errors": [], "slurm_config": "MaxArraySize = 2"}
    )
    monkeypatch.setattr(
        cli, "resolve_site", lambda args, root: (params, EXECUTION.copy(), [], "local", "")
    )

    def must_not_execute(*args):
        raise AssertionError("Preflight failure must submit zero workflow tasks")

    monkeypatch.setattr(cli, "_execute", must_not_execute)
    return args


@pytest.mark.parametrize(
    "mode,match",
    [
        ("sif", "SIF missing"),
        ("output", "Output already exists"),
        ("disk", "Insufficient storage"),
        ("resume", "session cache is missing"),
        ("lock", "Driver lock exists"),
        ("array", "MaxArraySize"),
    ],
)
def test_preflight_rejects_before_engine(invocation, monkeypatch, mode, match):
    args = invocation
    if mode == "sif":
        args.sif_sha256 = "bad"
    elif mode == "output":
        args.outdir.mkdir()
    elif mode == "disk":
        monkeypatch.setattr(shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(100, 99, 1))
    elif mode == "resume":
        args.resume = "missing-session"
    elif mode == "lock":
        args.launch_dir.mkdir()
        (args.launch_dir / "active.lock").write_text("123\n")
    elif mode == "array":
        args.profile = "slurm"
    with pytest.raises(ReproError, match=match):
        cli.run(args)
    if mode != "output":
        assert not args.outdir.exists()


def test_command_preserves_failure_message():
    import sys

    with pytest.raises(ReproError, match="specific failure"):
        cli.command_output(
            [sys.executable, "-c", "import sys; sys.stderr.write('specific failure'); sys.exit(7)"]
        )
