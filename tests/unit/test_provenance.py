import csv
from pathlib import Path

import pytest

from reprohpc.archive import export_run, extract
from reprohpc.errors import ReproError
from reprohpc.io import fingerprint, read_json, write_json
from reprohpc.provenance import (
    _equivalent,
    collect_tasks,
    finalize_run,
    link_outputs,
    now,
    platform_info,
    validate_scientific,
    verify_run,
    write_checksums,
    write_tasks,
)
from reprohpc.task import aggregate, analyze_batch, report, validate_task

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def unit_package(tmp_path):
    """Real Python-produced results with explicitly unit-only execution metadata.

    This tests the verifier/exporter contracts, not Nextflow or Slurm execution.
    """
    import shutil

    from reprohpc.config import resolve_params, science_params
    from reprohpc.data import validate_dataset, validate_reference

    data = ROOT / "data/demo/1.0.0"
    ref = ROOT / "references/calibration/1.0.0/reference.json"
    root = tmp_path / "run"
    (root / "provenance").mkdir(parents=True)
    dataset, samples = validate_dataset(data / "dataset.json", data / "samples.csv")
    reference, calibration = validate_reference(ref)
    params = resolve_params()
    validate_task(data / "dataset.json", data / "samples.csv", ref, tmp_path / "validated.json")
    batch = tmp_path / "batch"
    analyze_batch(
        {
            "params": params,
            "samples": samples,
            "batch_id": "unit-batch",
            "calibration": calibration,
            "reference_sha256": fingerprint({"reference": reference, "calibration": calibration}),
            "sif_sha256": fingerprint("unit-only SIF identity"),
        },
        [data / s["path"] for s in samples],
        batch,
    )
    shutil.copytree(batch / "samples", root / "samples")
    aggregate([batch], [s["sample_id"] for s in samples], root / "summary")
    report(root / "summary", params, [batch], root / "report/index.html")
    shutil.copy2(data / "samples.csv", root / "provenance/samples.csv")
    for name, value in (
        ("dataset", dataset),
        ("reference", reference),
        ("calibration", calibration),
    ):
        write_json(root / "provenance" / (name + ".json"), value)
    write_json(root / "provenance/params.resolved.json", params)
    status = {
        "schema_version": "1.0.0",
        "run_id": "unit-fixture",
        "status": "success",
        "exit_code": 0,
        "message": "unit-only execution metadata",
    }
    record = {
        "schema_version": "1.0.0",
        "run_id": "unit-fixture",
        "nextflow_session_id": None,
        "resumed_from": None,
        "started": now(),
        "finished": now(),
        "analysis_fingerprint": fingerprint(params),
        "command": ["unit-test"],
        "params": params,
        "execution": {"profile": "unit-only"},
        "software": {
            "unit_only": True,
            "sif_sha256": fingerprint("unit-only SIF identity"),
            "source_tree_sha256": fingerprint("unit-only source identity"),
        },
        "platform": platform_info(),
        "dataset": dataset,
        "reference": reference,
        "status": status,
    }
    write_json(
        root / "provenance/analysis.json",
        {
            "schema_version": "1.0.0",
            "algorithm": "demo-cv-v1",
            "source_tree_sha256": record["software"]["source_tree_sha256"],
            "sif_sha256": record["software"]["sif_sha256"],
            "science": science_params(params),
            "samples": samples,
            "reference": reference,
            "calibration": calibration,
        },
    )
    write_json(root / "status.json", status)
    write_json(root / "provenance/run.json", record)
    tasks = [
        {
            "schema_version": "1.0.0",
            "process": process,
            "task_id": process,
            "hash": "unit-only",
            "status": "COMPLETED",
            "attempt": 1,
            "native_id": None,
            "sample_ids": [s["sample_id"] for s in samples] if process == "ANALYZE_BATCH" else [],
            "requested": {"cpus": "1", "memory": "2147483648", "time": "600000"},
            "usage": {},
            "exit_code": 0,
            "origin_work_dir": "unit-only",
            "scientific_metadata": read_json(batch / "task.json")
            if process == "ANALYZE_BATCH"
            else None,
            "origin_run_id": "unit-fixture",
            "origin_session_id": None,
            "origin_task_id": process,
            "logs": [],
        }
        for process in ("ANALYZE_BATCH", "AGGREGATE")
    ]
    write_tasks(root, tasks)
    write_json(root / "provenance/outputs.json", link_outputs(root, tasks))
    write_checksums(root)
    return root


def prepare_finalization(root):
    run = read_json(root / "provenance/run.json")
    (root / "provenance/run.json").unlink()
    write_json(root / "status.json", {**run["status"], "status": "running", "exit_code": None})
    return run


def test_success_is_published_only_after_complete_verification(unit_package, monkeypatch):
    import reprohpc.provenance as provenance

    run = prepare_finalization(unit_package)
    verified = False
    writes = []
    original_validate = provenance.validate_scientific

    def observe_validation(root):
        nonlocal verified
        original_validate(root)
        verified = True

    def observe_write(path, value):
        assert verified, "Terminal records must follow full scientific validation"
        with pytest.raises(ReproError, match="not verified success"):
            verify_run(unit_package)
        writes.append(path.relative_to(unit_package).as_posix())
        write_json(path, value)

    monkeypatch.setattr(provenance, "validate_scientific", observe_validation)
    monkeypatch.setattr(provenance, "write_json", observe_write)
    finalize_run(unit_package, run)
    assert writes == ["provenance/run.json", "status.json"]
    assert verify_run(unit_package)["verified"]


@pytest.mark.parametrize("stage", ["checksums", "verification", "run", "status"])
def test_finalization_failure_never_publishes_success(unit_package, monkeypatch, stage):
    import reprohpc.provenance as provenance

    run = prepare_finalization(unit_package)
    if stage == "verification":
        # Real corruption is still rejected when terminal metadata is pending.
        (unit_package / "samples/blank/mask.npy").write_bytes(b"corrupt")
    elif stage == "checksums":

        def fail_manifest(*args):
            raise OSError("Injected manifest write failure")

        monkeypatch.setattr(provenance, "atomic_bytes", fail_manifest)
    else:
        failed_path = "provenance/run.json" if stage == "run" else "status.json"

        def fail_terminal(path, value):
            if path.relative_to(unit_package).as_posix() == failed_path:
                raise OSError("Injected terminal write failure")
            write_json(path, value)

        monkeypatch.setattr(provenance, "write_json", fail_terminal)
    with pytest.raises((ReproError, OSError)):
        finalize_run(unit_package, run)
    assert read_json(unit_package / "status.json")["status"] == "running"
    with pytest.raises(ReproError, match="not verified success"):
        verify_run(unit_package)


def test_verified_export_roundtrip(unit_package, tmp_path):
    assert verify_run(unit_package)["verified"]
    output = tmp_path / "public.tar.gz"
    assert export_run(unit_package, output)["sha256"]
    extract(output, tmp_path / "public")
    assert verify_run(tmp_path / "public")["verified"]
    assert "hostname" not in read_json(tmp_path / "public/provenance/run.json")["platform"]
    graph = read_json(tmp_path / "public/metadata.jsonld")["@graph"]
    entities = {entry["@id"]: entry for entry in graph}
    assert entities["#analysis"]["instrument"] == {"@id": "#software"}
    assert entities["#analysis"]["result"] == {"@id": "#results"}
    dictionary = read_json(tmp_path / "public/dictionary/1.0.0.jsonld")
    terms = {term["@id"] for term in dictionary["hasDefinedTerm"]}
    for variable in entities["#results"]["variableMeasured"]:
        path, fragment = variable["propertyID"].split("#")
        assert (tmp_path / "public" / path).is_file()
        assert "#" + fragment in terms and variable["unitText"]
    for distribution in entities["#results"]["distribution"]:
        from reprohpc.io import sha256

        assert sha256(tmp_path / "public" / distribution["contentUrl"]) == distribution["sha256"]
    with pytest.raises(ReproError, match="exists"):
        export_run(unit_package, output)


def test_recorded_media_types_do_not_depend_on_host_registry(unit_package, monkeypatch):
    import mimetypes

    from reprohpc.provenance import inventory

    # Windows registries can associate .csv with a spreadsheet application.
    monkeypatch.setattr(
        mimetypes, "guess_type", lambda *args, **kwargs: ("application/x-host", None)
    )
    types = {
        Path(e["path"]).suffix: e["media_type"] for e in inventory(unit_package, scientific=True)
    }
    assert types == {
        ".csv": "text/csv",
        ".json": "application/json",
        ".npy": "application/octet-stream",
        ".png": "image/png",
    }
    assert verify_run(unit_package)["verified"]


def test_report_links_resolve_in_finalized_run_and_export(unit_package, tmp_path):
    from reprohpc.reporting import check_report_links

    run = prepare_finalization(unit_package)
    finalize_run(unit_package, run)
    assert verify_run(unit_package)["verified"]
    targets = check_report_links(unit_package)
    for expected in (
        "metadata.jsonld",
        "provenance/run.json",
        "provenance/params.resolved.json",
        "summary/objects.csv",
        "samples/square/mask.npy",
    ):
        assert expected in targets
    assert len([t for t in targets if t.startswith("samples/")]) == 12 * 3
    export_run(unit_package, tmp_path / "public.tar.gz")
    extract(tmp_path / "public.tar.gz", tmp_path / "public")
    assert check_report_links(tmp_path / "public") == targets
    (tmp_path / "public/samples/blank/mask.npy").unlink()
    (tmp_path / "public/metadata.jsonld").unlink()
    with pytest.raises(ReproError, match=r"metadata\.jsonld.*samples/blank/mask\.npy"):
        check_report_links(tmp_path / "public")


@pytest.mark.parametrize("mode", ["corrupt", "extra", "missing", "not-success", "inventory"])
def test_verify_rejects_broken_package(unit_package, mode):
    if mode == "corrupt":
        (unit_package / "samples/blank/mask.npy").write_bytes(b"bad")
    elif mode == "extra":
        (unit_package / "extra.txt").write_text("undeclared")
    elif mode == "missing":
        (unit_package / "samples/blank/metrics.json").unlink()
    elif mode == "not-success":
        value = read_json(unit_package / "status.json")
        value["status"] = "failed"
        write_json(unit_package / "status.json", value)
    else:
        write_json(unit_package / "provenance/outputs.json", [])
        write_checksums(unit_package)
    with pytest.raises(ReproError):
        verify_run(unit_package)


def test_semantic_validation_detects_forged_counts(unit_package):
    value = read_json(unit_package / "summary/dataset.json")
    value["object_count"] += 1
    write_json(unit_package / "summary/dataset.json", value)
    with pytest.raises(ReproError, match="Aggregate counts"):
        validate_scientific(unit_package)


@pytest.mark.parametrize("table", ["images.csv", "objects.csv"])
def test_aggregate_rows_must_match_individual_results(unit_package, table):
    path = unit_package / "summary" / table
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], *lines[2:]]) + "\n")
    with pytest.raises(ReproError, match="Aggregate"):
        validate_scientific(unit_package)


def test_trace_parsing_and_diagnostics(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".command.out").write_text("test output")
    (tmp_path / "provenance").mkdir()
    with (tmp_path / "provenance/trace.tsv").open("w", newline="") as stream:
        fields = [
            "task_id",
            "process",
            "hash",
            "status",
            "attempt",
            "native_id",
            "cpus",
            "memory",
            "time",
            "exit",
            "workdir",
        ]
        writer = csv.DictWriter(stream, fields, delimiter="\t")
        writer.writeheader()
        writer.writerow(
            dict(
                zip(
                    fields,
                    [
                        "1",
                        "VALIDATE_DATASET",
                        "ab/1234",
                        "COMPLETED",
                        "1",
                        "-",
                        "1",
                        "1024",
                        "60",
                        "0",
                        str(work),
                    ],
                    strict=True,
                )
            )
        )
    tasks = collect_tasks(tmp_path)
    assert tasks[0]["native_id"] is None
    assert tasks[0]["usage"]["peak_rss"] is None
    write_tasks(tmp_path, tasks)
    assert (tmp_path / "logs/tasks/1-1/command.out").read_text() == "test output"


def test_float_tolerance_is_only_for_measurements():
    assert _equivalent({"mean_intensity": 1.0}, {"mean_intensity": 1.0000001})
    assert not _equivalent({"object_count": 100}, {"object_count": 101})
    assert not _equivalent({"mean_intensity": 1.0}, {"mean_intensity": float("nan")})
    assert not _equivalent({"mean_area_px": None}, {"mean_area_px": 0})


@pytest.mark.parametrize(
    "mode", ["reference", "input", "parameter", "producer", "resources", "measurement"]
)
def test_lineage_rejects_consistent_checksums_with_broken_relationship(unit_package, mode):
    import json

    if mode in ("reference", "input"):
        path = unit_package / "provenance/analysis.json"
        spec = read_json(path)
        if mode == "reference":
            spec["reference"]["version"] = "different-version"
        else:
            spec["samples"][0]["sha256"] = fingerprint("different-input")
        write_json(path, spec)
    elif mode == "measurement":
        path = unit_package / "samples/blank/metrics.json"
        metric = read_json(path)
        metric["parameter_sha256"] = fingerprint("different measurement parameters")
        write_json(path, metric)
        tasks = [
            json.loads(line)
            for line in (unit_package / "provenance/tasks.jsonl").read_text().splitlines()
        ]
        write_json(unit_package / "provenance/outputs.json", link_outputs(unit_package, tasks))
    else:
        path = unit_package / "provenance/tasks.jsonl"
        tasks = [json.loads(line) for line in path.read_text().splitlines()]
        if mode == "parameter":
            tasks[0]["scientific_metadata"]["parameter_sha256"] = fingerprint("different-science")
        elif mode == "producer":
            tasks[0]["origin_run_id"] = None
        else:
            tasks[0]["requested"]["cpus"] = None
        write_tasks(unit_package, tasks)
        write_json(unit_package / "provenance/outputs.json", link_outputs(unit_package, tasks))
    write_checksums(unit_package)
    with pytest.raises(ReproError, match="specification|inputs|lineage|resources"):
        verify_run(unit_package)
