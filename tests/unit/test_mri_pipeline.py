"""fsl-bet-volumetry-v1 through validation, task, aggregate, report, finalize, verify, export,
compare. FSL is replaced by a fake that honours bet's and fslstats' file contract, so this runs
in CI without FSL; it proves the plumbing, not BET's segmentation.
"""

import csv
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_mri import write_nifti

from reprohpc import mri
from reprohpc.archive import export_run, extract
from reprohpc.config import resolve_params, science_params
from reprohpc.data import plan_batches, validate_dataset, validate_reference
from reprohpc.errors import ReproError
from reprohpc.io import fingerprint, read_json, read_nifti_mask, sha256, write_csv, write_json
from reprohpc.provenance import (
    compare,
    finalize_run,
    link_outputs,
    now,
    platform_info,
    verify_run,
    write_checksums,
    write_tasks,
)
from reprohpc.reporting import check_report_links
from reprohpc.task import aggregate, mri_batch, report, validate_task

DIMS = (6, 5, 4)
# A known brain per subject: the fake BET keeps voxels whose intensity is at least this.
CUTOFF = 100


def fake_fsl(argv, **kwargs):
    """Minimal stand-in for bet/bet2/fslstats with the same inputs, outputs and stdout."""
    name = Path(argv[0]).name
    if name == "bet2":
        return subprocess.CompletedProcess(
            argv, 1, "Part of FSL (ID: test)\nBET (Brain Extraction Tool) v2.1\n", ""
        )
    if name == "bet":
        image, root = Path(argv[1]), argv[2]
        header = read_json_header(image)
        values = header["voxels"]
        write_nifti(Path(root + "_mask.nii.gz"), [int(v >= CUTOFF) for v in values], DIMS)
        return subprocess.CompletedProcess(argv, 0, "", "")
    mask = read_nifti_mask(Path(argv[1]))
    if argv[2] == "-V":
        volume = mask.nonzero * 1.0 * 1.0 * 1.0
        return subprocess.CompletedProcess(argv, 0, f"{mask.nonzero} {volume:.6f} \n", "")
    return subprocess.CompletedProcess(argv, 0, "1 2 1 2 1 2 0 1\n", "")


def read_json_header(path):
    import gzip
    import struct

    raw = gzip.decompress(path.read_bytes())
    count = DIMS[0] * DIMS[1] * DIMS[2]
    return {"voxels": struct.unpack(f"<{count}h", raw[352 : 352 + 2 * count])}


@pytest.fixture
def mri_run(tmp_path, monkeypatch):
    root = tmp_path / "fsl"
    (root / "bin").mkdir(parents=True)
    for name in ("bet", "bet2", "fslstats"):
        (root / "bin" / name).write_text("#!/bin/sh\n")
        (root / "bin" / name).chmod(0o755)
    (root / "etc").mkdir()
    (root / "etc/reprohpc-fsl-build.json").write_text(
        json.dumps({"fsl_release": "6.0.7.23", "install": "unit-test fake"})
    )
    (root / "conda-meta").mkdir()
    for name in mri.FSL_PACKAGES:
        (root / "conda-meta" / f"{name}-1.0-h0_0.json").write_text('{"version": "1.0"}')
    monkeypatch.setenv("FSLDIR", str(root))
    monkeypatch.setattr(mri.subprocess, "run", fake_fsl)

    data = tmp_path / "data"
    rows = []
    for index, sid in enumerate(("sub-01", "sub-02", "sub-03")):
        count = DIMS[0] * DIMS[1] * DIMS[2]
        voxels = [200 if (i + index) % 3 else 10 for i in range(count)]
        (data / sid / "anat").mkdir(parents=True)
        path = write_nifti(data / sid / "anat" / f"{sid}_T1w.nii.gz", voxels, DIMS, "int16")
        rows.append(
            {
                "sample_id": sid,
                "path": f"{sid}/anat/{sid}_T1w.nii.gz",
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    write_csv(data / "samples.csv", ["sample_id", "path", "sha256", "size_bytes"], rows)
    write_json(
        data / "dataset.json",
        {
            "schema_version": "1.0.0",
            "dataset_id": "unit-mri",
            "version": "1.0.0",
            "title": "Synthetic NIfTI unit fixture",
            "description": "Three generated volumes; unit tests only.",
            "creators": ["ReproHPC contributors"],
            "license": "CC0-1.0",
            "source": "https://github.com/seedyjahateh/ReproHPC",
            "created": "2026-09-21",
            "manifest_sha256": sha256(data / "samples.csv"),
            "modality": "synthetic",
            "species": "not_applicable",
            "access": "public",
            "doi": None,
        },
    )
    reference = tmp_path / "reference"
    write_json(
        reference / "calibration.json",
        {"schema_version": "1.0.0", "pixel_size_um": None, "unit": "um"},
    )
    write_json(
        reference / "reference.json",
        {
            "schema_version": "1.0.0",
            "reference_id": "unit-geometry",
            "version": "1.0.0",
            "source": "https://nifti.nimh.nih.gov/nifti-1",
            "license": "CC0-1.0",
            "calibration": {
                "path": "calibration.json",
                "sha256": sha256(reference / "calibration.json"),
                "size_bytes": (reference / "calibration.json").stat().st_size,
            },
        },
    )

    params = resolve_params({"algorithm": mri.ALGORITHM, "bet_frac": 0.4, "batch_size": 2})
    dataset, samples = validate_dataset(data / "dataset.json", data / "samples.csv")
    ref, calibration = validate_reference(reference / "reference.json")
    validate_task(
        data / "dataset.json",
        data / "samples.csv",
        reference / "reference.json",
        tmp_path / "validated.json",
    )
    sif = fingerprint("unit-only SIF identity")
    batches = []
    for batch in plan_batches(samples, params["batch_size"]):
        spec = {
            **batch,
            "params": params,
            "calibration": calibration,
            "reference_sha256": fingerprint({"reference": ref, "calibration": calibration}),
            "sif_sha256": sif,
        }
        folder = tmp_path / "batches" / batch["batch_id"]
        mri_batch(spec, [data / s["path"] for s in batch["samples"]], folder)
        batches.append(folder)

    run = tmp_path / "run"
    (run / "provenance").mkdir(parents=True)
    aggregate(batches, [s["sample_id"] for s in samples], run / "summary")
    science = {**science_params(params), "write_previews": False, "batch_size": 2}
    report(run / "summary", science, batches, run / "report/index.html")
    for folder in batches:
        for sample in (folder / "samples").iterdir():
            shutil.copytree(sample, run / "samples" / sample.name)
    shutil.copy2(data / "samples.csv", run / "provenance/samples.csv")
    for name, value in (("dataset", dataset), ("reference", ref), ("calibration", calibration)):
        write_json(run / "provenance" / f"{name}.json", value)
    write_json(run / "provenance/params.resolved.json", params)
    write_json(
        run / "provenance/analysis.json",
        {
            "schema_version": "1.0.0",
            "algorithm": mri.ALGORITHM,
            "source_tree_sha256": fingerprint("unit-only source"),
            "sif_sha256": sif,
            "science": science_params(params),
            "samples": samples,
            "reference": ref,
            "calibration": calibration,
        },
    )
    status = {
        "schema_version": "1.0.0",
        "run_id": "unit-mri",
        "status": "success",
        "exit_code": 0,
        "message": "",
    }
    record = {
        "schema_version": "1.0.0",
        "run_id": "unit-mri",
        "nextflow_session_id": None,
        "resumed_from": None,
        "started": now(),
        "finished": now(),
        "analysis_fingerprint": fingerprint(params),
        "command": ["unit-test"],
        "params": params,
        "execution": {"profile": "unit-only"},
        "software": {"sif_sha256": sif, "source_tree_sha256": fingerprint("unit-only source")},
        "platform": platform_info(),
        "dataset": dataset,
        "reference": ref,
        "status": status,
    }
    tasks = [
        {
            "schema_version": "1.0.0",
            "process": process,
            "task_id": task_id,
            "hash": "unit-only",
            "status": "COMPLETED",
            "attempt": 1,
            "native_id": None,
            "sample_ids": ids,
            "requested": {"cpus": "1", "memory": "2147483648", "time": "600000"},
            "usage": {},
            "exit_code": 0,
            "origin_work_dir": "unit-only",
            "scientific_metadata": metadata,
            "origin_run_id": "unit-mri",
            "origin_session_id": None,
            "origin_task_id": task_id,
            "logs": [],
        }
        for process, task_id, ids, metadata in [
            *(
                (
                    "MRI_BATCH",
                    f.name,
                    read_json(f / "task.json")["sample_ids"],
                    read_json(f / "task.json"),
                )
                for f in batches
            ),
            ("AGGREGATE", "aggregate", [], None),
        ]
    ]
    write_tasks(run, tasks)
    write_json(run / "provenance/outputs.json", link_outputs(run, tasks))
    write_json(run / "status.json", {**status, "status": "running", "exit_code": None})
    write_checksums(run)
    finalize_run(run, record)
    return run


def test_mri_run_finalizes_verifies_and_exports(mri_run, tmp_path):
    assert verify_run(mri_run)["verified"]
    metrics = read_json(mri_run / "samples/sub-01/metrics.json")
    assert metrics["dims"] == list(DIMS) and metrics["brain_voxels"] == 80
    task = read_json(next((mri_run.parent / "batches").iterdir()) / "task.json")
    assert task["routine"] == mri.ALGORITHM
    assert task["fsl"]["version_string"] == "FSL 6.0.7.23 (fsl-bet2 1.0, fsl-avwutils 1.0)"
    with (mri_run / "summary/subjects.csv").open() as stream:
        assert [row["sample_id"] for row in csv.DictReader(stream)] == [
            "sub-01",
            "sub-02",
            "sub-03",
        ]
    graph = read_json(mri_run / "metadata.jsonld")["@graph"]
    measured = {v["name"] for v in graph[0]["variableMeasured"]}
    assert measured == {"brain_voxels", "brain_volume_mm3"}
    links = check_report_links(mri_run)
    assert "samples/sub-02/brain_mask.nii.gz" in links and "summary/subjects.csv" in links
    export_run(mri_run, tmp_path / "public.tar.gz")
    extract(tmp_path / "public.tar.gz", tmp_path / "public")
    assert verify_run(tmp_path / "public")["verified"]
    assert check_report_links(tmp_path / "public") == links


def test_mri_golden_compare_is_exact_and_detects_changes(mri_run, tmp_path):
    subjects = {}
    for folder in sorted((mri_run / "samples").iterdir()):
        metric = read_json(folder / "metrics.json")
        mask = read_nifti_mask(folder / "brain_mask.nii.gz")
        subjects[folder.name] = {
            "dims": metric["dims"],
            "brain_voxels": metric["brain_voxels"],
            "brain_volume_mm3": metric["brain_volume_mm3"],
            "mask_voxel_sha256": mask.voxel_sha256,
        }
    golden = tmp_path / "golden"
    write_json(golden / "expected.json", {"algorithm": mri.ALGORITHM, "subjects": subjects})
    result = compare(golden, mri_run, exact=True)
    assert result["equivalent"] and result["max_absolute_difference_by_column"] == {
        "brain_volume_mm3": 0.0
    }
    # A single flipped voxel is caught by both the golden and the verifier.
    mask_path = mri_run / "samples/sub-02/brain_mask.nii.gz"
    write_nifti(mask_path, [1] + [0] * (DIMS[0] * DIMS[1] * DIMS[2] - 1), DIMS)
    with pytest.raises(ReproError, match="sub-02: mask voxels differ"):
        compare(golden, mri_run, exact=True)
    with pytest.raises(ReproError):
        verify_run(mri_run)


def test_two_mri_runs_compare_by_voxel_content(mri_run, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(mri_run, copy)
    assert compare(mri_run, copy)["equivalent"]
    metrics_path = copy / "samples/sub-03/metrics.json"
    metric = read_json(metrics_path)
    metric["brain_volume_mm3"] += 0.5
    write_json(metrics_path, metric)
    with pytest.raises(ReproError, match="sub-03/metrics.json"):
        compare(mri_run, copy)


def test_metrics_that_disagree_with_the_mask_fail_verification(mri_run):
    metrics_path = mri_run / "samples/sub-01/metrics.json"
    metric = read_json(metrics_path)
    metric["brain_voxels"] += 1
    write_json(metrics_path, metric)
    # Rewrite every recorded hash consistently, as a forger would: only re-reading the mask's
    # voxels can still expose the edited count.
    tasks = [
        json.loads(line) for line in (mri_run / "provenance/tasks.jsonl").read_text().splitlines()
    ]
    write_json(mri_run / "provenance/outputs.json", link_outputs(mri_run, tasks))
    write_checksums(mri_run)
    with pytest.raises(ReproError, match="Brain mask disagrees with its metrics for sub-01"):
        verify_run(mri_run)


def test_mri_parameters_are_validated_separately_from_images():
    params = resolve_params({"algorithm": mri.ALGORITHM, "bet_frac": 0.3})
    assert science_params(params) == {"algorithm": mri.ALGORITHM, "bet_frac": 0.3}
    assert params["batch_size"] == 1
    with pytest.raises(ReproError, match="Unknown parameter"):
        resolve_params({"algorithm": mri.ALGORITHM, "threshold": 127})
    for bad in (0, 1, 1.5, "0.4", True):
        with pytest.raises(ReproError, match="bet_frac"):
            resolve_params({"algorithm": mri.ALGORITHM, "bet_frac": bad})
    with pytest.raises(ReproError, match="Unsupported algorithm"):
        resolve_params({"algorithm": "fsl-something-else"})


@pytest.mark.parametrize(
    "supplied",
    [{}, {"algorithm": mri.ALGORITHM, "bet_frac": 0.45, "batch_size": 3}],
    ids=["demo-cv-v1", "fsl-bet-volumetry-v1"],
)
def test_engine_science_is_accepted_by_the_task_side_validator(supplied):
    """Nextflow passes the launcher's science block to tasks, which call resolve_params on it.
    A key the routine does not accept (write_previews for MRI) made every real task fail."""
    from reprohpc.cli import engine_science

    params = resolve_params(supplied)
    science = engine_science(params)
    assert resolve_params(science) == {k: v for k, v in params.items() if k in science} | {
        "schema_version": "1.0.0"
    }


def test_mixed_nifti_and_png_manifests_are_rejected():
    from reprohpc.data import sample_kind

    assert sample_kind([{"path": "a.nii.gz"}, {"path": "b.nii"}]) == "nifti"
    with pytest.raises(ReproError, match="mixes"):
        sample_kind([{"path": "a.nii.gz"}, {"path": "b.png"}])
