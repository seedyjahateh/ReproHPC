"""fsl-bet-volumetry-v1 without FSL: command construction, parsing, failure handling, NIfTI I/O.

FSL itself is replaced at the subprocess boundary. What these tests establish is that the
right commands are built and that every FSL failure mode surfaces as an error; that FSL
computes correct masks is established by the real runs recorded in docs/neuroimaging.md.
"""

import gzip
import json
import struct
import subprocess
from pathlib import Path

import pytest

from reprohpc import mri
from reprohpc.errors import ReproError
from reprohpc.io import nifti_header, read_nifti_mask

CODES = {"uint8": (2, "B", 8), "int16": (4, "h", 16), "float32": (16, "f", 32)}


def write_nifti(path, voxels, dims, datatype="uint8", pixdim=(1.0, 1.0, 1.0), byteorder="<", **h):
    """Minimal single-file NIfTI-1: 348-byte header, 4 extension bytes, voxel data."""
    code, typecode, bitpix = CODES[datatype]
    header = bytearray(352)
    struct.pack_into(f"{byteorder}i", header, 0, 348)
    dim = h.get("dim", (len(dims), *dims) + (1,) * (7 - len(dims)))
    struct.pack_into(f"{byteorder}8h", header, 40, *dim)
    struct.pack_into(f"{byteorder}h", header, 70, h.get("datatype_code", code))
    struct.pack_into(f"{byteorder}h", header, 72, bitpix)
    struct.pack_into(f"{byteorder}8f", header, 76, 1.0, *pixdim, 1.0, 1.0, 1.0, 1.0)
    struct.pack_into(f"{byteorder}f", header, 108, h.get("vox_offset", 352.0))
    header[344:348] = h.get("magic", b"n+1\x00")
    data = struct.pack(f"{byteorder}{len(voxels)}{typecode}", *voxels)
    content = bytes(header) + data
    path.write_bytes(gzip.compress(content, mtime=0) if path.name.endswith(".gz") else content)
    return path


@pytest.fixture
def fsl(tmp_path, monkeypatch):
    """A fake FSLDIR with executable stubs and package metadata; nothing here runs FSL."""
    root = tmp_path / "fsl"
    (root / "bin").mkdir(parents=True)
    for name in ("bet", "bet2", "fslstats"):
        stub = root / "bin" / name
        stub.write_text("#!/bin/sh\nexit 99\n")
        stub.chmod(0o755)
    (root / "etc").mkdir()
    (root / "etc/reprohpc-fsl-build.json").write_text(
        json.dumps({"fsl_release": "6.0.7.23", "install": "minimal explicit lock"})
    )
    (root / "conda-meta").mkdir()
    for name, version in (("fsl-bet2", "2111.9"), ("fsl-avwutils", "2209.6")):
        (root / "conda-meta" / f"{name}-{version}-h0_0.json").write_text(
            json.dumps({"name": name, "version": version})
        )
    monkeypatch.setenv("FSLDIR", str(root))
    return root


class Recorder:
    """Stands in for subprocess.run; replies from a queue and records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            reply = reply(argv)
        code, stdout, stderr = reply
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


def test_bet_command_is_explicit_and_mask_only(fsl, tmp_path):
    argv = mri.bet_command(tmp_path / "t1.nii.gz", tmp_path / "out/brain", 0.35)
    assert argv == [
        str(fsl / "bin/bet"),
        str(tmp_path / "t1.nii.gz"),
        str(tmp_path / "out/brain"),
        "-f",
        "0.35",
        "-m",
        "-n",
    ]


@pytest.mark.parametrize("frac", [0, 1, -0.1, 1.5])
def test_bet_command_rejects_fraction_outside_open_interval(fsl, tmp_path, frac):
    with pytest.raises(ReproError, match="bet_frac"):
        mri.bet_command(tmp_path / "t1.nii.gz", tmp_path / "brain", frac)


def test_missing_fsl_is_an_error_not_a_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("FSLDIR", raising=False)
    with pytest.raises(ReproError, match="FSLDIR is not set"):
        mri.tool("bet")
    monkeypatch.setenv("FSLDIR", str(tmp_path))
    with pytest.raises(ReproError, match="not found"):
        mri.tool("bet")


def test_run_uses_fixed_environment_and_returns_stdout(fsl, monkeypatch):
    recorder = Recorder((0, "12 34.5\n", ""))
    monkeypatch.setattr(mri.subprocess, "run", recorder)
    assert mri.run([str(fsl / "bin/fslstats"), "mask.nii.gz", "-V"]) == "12 34.5\n"
    argv, kwargs = recorder.calls[0]
    assert argv == [str(fsl / "bin/fslstats"), "mask.nii.gz", "-V"]
    assert kwargs["capture_output"] and kwargs["check"] is False and kwargs["timeout"]
    env = kwargs["env"]
    assert env["FSLDIR"] == str(fsl) and env["FSLOUTPUTTYPE"] == "NIFTI_GZ"
    assert env["OMP_NUM_THREADS"] == "1" and env["LC_ALL"] == "C"
    assert env["PATH"].startswith(f"{fsl}/bin:")


@pytest.mark.parametrize(
    "reply, message",
    [
        ((1, "", "Error: cannot open image"), "exit code 1: Error: cannot open image"),
        ((139, "partial output", ""), "exit code 139: partial output"),
        ((2, "", ""), "exit code 2: no output"),
        (subprocess.TimeoutExpired("bet", 1), "exceeded"),
        (OSError("exec format error"), "Cannot execute"),
    ],
)
def test_run_fails_loudly_on_every_fsl_failure(fsl, monkeypatch, reply, message):
    monkeypatch.setattr(mri.subprocess, "run", Recorder(reply))
    with pytest.raises(ReproError, match=message) as caught:
        mri.run([str(fsl / "bin/bet"), "in", "out"])
    assert caught.value.code == 4


@pytest.mark.parametrize(
    "stdout, expected",
    [("1234567 1234567.000000 \n", (1234567, 1234567.0)), ("0 0.000000\n", (0, 0.0))],
)
def test_volumes_parse_fslstats_exactly(fsl, monkeypatch, stdout, expected):
    recorder = Recorder((0, stdout, ""))
    monkeypatch.setattr(mri.subprocess, "run", recorder)
    assert mri.volumes(Path("mask.nii.gz")) == expected
    assert recorder.calls[0][0][1:] == ["mask.nii.gz", "-V"]


@pytest.mark.parametrize("stdout", ["", "12", "12 34 56", "twelve 34", "-1 2.0"])
def test_volumes_reject_unexpected_output(fsl, monkeypatch, stdout):
    monkeypatch.setattr(mri.subprocess, "run", Recorder((0, stdout, "")))
    with pytest.raises(ReproError, match="fslstats"):
        mri.volumes(Path("mask.nii.gz"))


def test_bounding_box_parse_and_rejection(fsl, monkeypatch):
    monkeypatch.setattr(
        mri.subprocess, "run", Recorder((0, "3 10 4 20 5 30 0 1\n", ""), (0, "1 2 3\n", ""))
    )
    assert mri.bounding_box(Path("m")) == (3, 10, 4, 20, 5, 30, 0, 1)
    with pytest.raises(ReproError, match="fslstats -w"):
        mri.bounding_box(Path("m"))


@pytest.mark.parametrize(
    "voxels, volume, box, expected",
    [
        (0, 0.0, (0, 0, 0, 0, 0, 0, 0, 1), ["EMPTY_MASK"]),
        (1_200_000, 1_200_000.0, (10, 100, 10, 100, 10, 100, 0, 1), []),
        (10, 10.0, (10, 100, 10, 100, 10, 100, 0, 1), ["VOLUME_OUT_OF_RANGE"]),
        (1_200_000, 1_200_000.0, (0, 100, 10, 100, 10, 100, 0, 1), ["MASK_AT_FOV_EDGE"]),
        (1_200_000, 1_200_000.0, (10, 100, 10, 100, 10, 246, 0, 1), ["MASK_AT_FOV_EDGE"]),
        (
            9,
            3_000_000.0,
            (0, 256, 0, 256, 0, 256, 0, 1),
            ["VOLUME_OUT_OF_RANGE", "MASK_AT_FOV_EDGE"],
        ),
    ],
)
def test_qc_flags_are_observations_in_fixed_order(voxels, volume, box, expected):
    assert mri.qc_mask(voxels, volume, box, (256, 256, 256)) == expected


def test_skull_strip_requires_the_mask_bet_claims_to_write(fsl, tmp_path, monkeypatch):
    def writes_mask(argv):
        Path(argv[2] + "_mask.nii.gz").write_bytes(b"mask")
        return 0, "", ""

    monkeypatch.setattr(mri.subprocess, "run", Recorder(writes_mask, (0, "", "")))
    mask = mri.skull_strip(tmp_path / "t1.nii.gz", tmp_path / "a", 0.4)
    assert mask == tmp_path / "a" / "brain_mask.nii.gz" and mask.read_bytes() == b"mask"
    with pytest.raises(ReproError, match="wrote no mask"):
        mri.skull_strip(tmp_path / "t1.nii.gz", tmp_path / "b", 0.4)


BANNER = "\nPart of FSL (ID: 2412.6-dirty)\nBET (Brain Extraction Tool) v2.1 - FMRIB\n\nUsage: ..."


def test_fsl_identity_reads_installed_packages(fsl, monkeypatch):
    # bet2 prints usage and exits non-zero without arguments; the banner is still read.
    monkeypatch.setattr(mri.subprocess, "run", Recorder((1, BANNER, "")))
    identity = mri.fsl_identity()
    assert identity["release"] == "6.0.7.23"
    assert identity["packages"] == {"fsl-bet2": "2111.9", "fsl-avwutils": "2209.6"}
    assert identity["version_string"] == "FSL 6.0.7.23 (fsl-bet2 2111.9, fsl-avwutils 2209.6)"
    assert identity["bet2_banner"] == [
        "Part of FSL (ID: 2412.6-dirty)",
        "BET (Brain Extraction Tool) v2.1 - FMRIB",
    ]


def test_missing_bet2_banner_is_an_error(fsl, monkeypatch):
    monkeypatch.setattr(mri.subprocess, "run", Recorder((1, "Usage: ...", "")))
    with pytest.raises(ReproError, match="no version banner"):
        mri.bet2_banner()


@pytest.mark.parametrize("damage", ["missing-package", "duplicate-package", "no-build-record"])
def test_fsl_identity_refuses_to_guess(fsl, damage):
    if damage == "missing-package":
        next((fsl / "conda-meta").glob("fsl-bet2-*.json")).unlink()
    elif damage == "duplicate-package":
        (fsl / "conda-meta/fsl-bet2-2111.8-h0_0.json").write_text('{"version": "2111.8"}')
    else:
        (fsl / "etc/reprohpc-fsl-build.json").unlink()
    with pytest.raises(ReproError):
        mri.fsl_identity()


def test_analyze_subject_records_fsl_measurements(fsl, tmp_path, monkeypatch):
    image = write_nifti(tmp_path / "t1.nii.gz", [0] * 24, (2, 3, 4), "int16", (1.0, 1.2, 0.9))

    def writes_mask(argv):
        Path(argv[2] + "_mask.nii.gz").write_bytes(b"mask")
        return 0, "", ""

    monkeypatch.setattr(
        mri.subprocess,
        "run",
        Recorder(writes_mask, (0, "20 21.600000\n", ""), (0, "1 1 1 1 1 1 0 1\n", "")),
    )
    params = {"algorithm": mri.ALGORITHM, "bet_frac": 0.4}
    metrics = mri.analyze_subject(image, "sub-01", params, tmp_path / "out")
    assert metrics["dims"] == [2, 3, 4]
    assert metrics["voxel_size_mm"] == pytest.approx([1.0, 1.2, 0.9])
    assert (metrics["brain_voxels"], metrics["brain_volume_mm3"]) == (20, 21.6)
    # A 2x3x4 image cannot hold a mask clear of its faces: x 1+1 reaches dimension 2.
    assert metrics["qc"] == ["VOLUME_OUT_OF_RANGE", "MASK_AT_FOV_EDGE"]
    assert json.loads((tmp_path / "out/metrics.json").read_text()) == metrics


@pytest.mark.parametrize("datatype", ["uint8", "int16", "float32"])
@pytest.mark.parametrize("byteorder", ["<", ">"])
@pytest.mark.parametrize("suffix", [".nii.gz", ".nii"])
def test_nifti_reader_counts_and_hashes_voxels(tmp_path, datatype, byteorder, suffix):
    voxels = [0, 1, 0, 1, 1, 0, 0, 0] * 3
    path = write_nifti(tmp_path / f"m{suffix}", voxels, (2, 3, 4), datatype, byteorder=byteorder)
    header = nifti_header(path)
    assert (header.dims, header.datatype, header.voxels) == ((2, 3, 4), datatype, 24)
    mask = read_nifti_mask(path)
    assert mask.nonzero == 9
    # The voxel hash ignores gzip framing: the same voxels uncompressed hash identically.
    other = write_nifti(tmp_path / "copy.nii", voxels, (2, 3, 4), datatype, byteorder=byteorder)
    assert read_nifti_mask(other).voxel_sha256 == mask.voxel_sha256


@pytest.mark.parametrize(
    "change, message",
    [
        ({"magic": b"ni1\x00"}, "magic"),
        ({"dim": (4, 2, 3, 4, 2, 1, 1, 1)}, "one 3-D volume"),
        ({"dim": (2, 2, 3, 1, 1, 1, 1, 1)}, "one 3-D volume"),
        ({"datatype_code": 128}, "datatype"),
        ({"vox_offset": 100.0}, "vox_offset"),
    ],
)
def test_nifti_reader_rejects_unsupported_files(tmp_path, change, message):
    path = write_nifti(tmp_path / "bad.nii.gz", [0] * 24, (2, 3, 4), **change)
    with pytest.raises(ReproError, match=message):
        read_nifti_mask(path)


def test_nifti_reader_rejects_truncation_and_non_nifti(tmp_path):
    path = write_nifti(tmp_path / "m.nii", [1] * 24, (2, 3, 4))
    path.write_bytes(path.read_bytes()[:-5])
    with pytest.raises(ReproError, match="Truncated"):
        read_nifti_mask(path)
    (tmp_path / "x.nii.gz").write_bytes(gzip.compress(b"\x00" * 400))
    with pytest.raises(ReproError, match="sizeof_hdr"):
        nifti_header(tmp_path / "x.nii.gz")
