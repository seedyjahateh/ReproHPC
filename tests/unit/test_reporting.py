import json
import re
import shutil
from pathlib import Path

import pytest
from test_tasks import execute_python_tasks

from reprohpc.config import resolve_params
from reprohpc.errors import ReproError
from reprohpc.io import read_json, write_json
from reprohpc.reporting import embedded_json, report
from reprohpc.task import aggregate


def payload(path):
    document = path.read_text(encoding="utf-8")
    match = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', document)
    return json.loads(match[1]), document


def test_report_has_exact_metrics_parameters_and_portable_assets(tmp_path):
    batches = execute_python_tasks(tmp_path, 1)
    data, document = payload(tmp_path / "report/index.html")
    assert len(data["samples"]) == len(data["previews"]) == 12
    assert data["summary"]["object_count"] == 9
    assert sum(bool(sample["qc"]) for sample in data["samples"]) == 8
    for sample in data["samples"]:
        assert sample == read_json(tmp_path / "samples" / sample["sample_id"] / "metrics.json")
    assert data["parameters"]["threshold"] == 127
    assert data["identities"]["sif_sha256"] == ["unit-test-not-an-execution-image"]
    assert not re.search(r'<(?:script|link)[^>]+(?:src|href)="https?://', document)
    assert "connect-src 'none'" in document and "@@" not in document
    for link in ("../summary/images.csv", "../summary/objects.csv", "../provenance/run.json"):
        assert link in document
    report(
        tmp_path / "summary", resolve_params(), list(reversed(batches)), tmp_path / "reverse.html"
    )
    assert (tmp_path / "reverse.html").read_bytes() == (tmp_path / "report/index.html").read_bytes()


def test_report_caps_available_previews_and_preserves_all_rows(tmp_path):
    batches = execute_python_tasks(tmp_path, 16)
    folder = batches[0] / "samples"
    # Original blank output is genuine; aliases exercise display cardinality only.
    for index in range(30):
        sid = f"alias-{index:02}"
        shutil.copytree(folder / "blank", folder / sid)
        metric = read_json(folder / sid / "metrics.json")
        metric["sample_id"] = sid
        write_json(folder / sid / "metrics.json", metric)
        if index < 5:
            (folder / sid / "preview.png").unlink()
    aggregate(batches, [path.name for path in folder.iterdir()], tmp_path / "summary")
    report(tmp_path / "summary", resolve_params(), batches, tmp_path / "capped.html")
    data, _ = payload(tmp_path / "capped.html")
    assert len(data["samples"]) == 42 and len(data["previews"]) == 24
    assert list(data["previews"])[0] == "above"
    assert "alias-00" not in data["previews"] and "alias-05" in data["previews"]
    for path in folder.glob("*/preview.png"):
        path.unlink()
    report(tmp_path / "summary", resolve_params(), batches, tmp_path / "none.html")
    assert payload(tmp_path / "none.html")[0]["previews"] == {}


@pytest.mark.parametrize("change", ["duplicate", "sample_id", "parameter_sha256", "count", "qc"])
def test_report_rejects_inconsistent_scientific_inputs(tmp_path, change):
    batches = execute_python_tasks(tmp_path, 16)
    if change == "duplicate":
        batches *= 2
    elif change in ("sample_id", "parameter_sha256"):
        path = batches[0] / "samples/blank/metrics.json"
        metric = read_json(path)
        metric[change] = "another-id" if change == "sample_id" else "f" * 64
        write_json(path, metric)
    else:
        path = tmp_path / "summary/dataset.json"
        summary = read_json(path)
        summary["object_count" if change == "count" else "qc"] = 99 if change == "count" else {}
        write_json(path, summary)
    with pytest.raises(ReproError):
        report(tmp_path / "summary", resolve_params(), batches, tmp_path / "invalid.html")
    assert not (tmp_path / "invalid.html").exists()


def test_embedded_metadata_cannot_break_out_of_script():
    value = {"text": '</script><script>alert("&")</script>\u2028\u2029@@JS@@'}
    encoded = embedded_json(value)
    assert "<" not in encoded and ">" not in encoded and "&" not in encoded
    assert json.loads(encoded) == value
    with pytest.raises(ValueError):
        embedded_json({"bad": float("nan")})


def test_report_assets_are_in_package_data():
    import tomllib

    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    declared = config["tool"]["setuptools"]["package-data"]["reprohpc"]
    for suffix in ("html", "css", "js"):
        assert f"report_assets/*.{suffix}" in declared


def test_source_snapshot_keeps_ui_sources_but_excludes_browser_installations(tmp_path):
    import tarfile

    from reprohpc.cli import source_snapshot

    root = tmp_path / "checkout"
    source_files = [
        "src/reprohpc/report_assets/index.html",
        "tests/ui/package-lock.json",
        "tests/ui/report.spec.cjs",
    ]
    generated_files = [
        "tests/ui/node_modules/large.js",
        "tests/ui/test-results/output.json",
        "tests/ui/playwright-report/index.html",
        "src/reprohpc/__pycache__/module.pyc",
        "src/reprohpc.egg-info/PKG-INFO",
    ]
    for name in source_files + generated_files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    archive = tmp_path / "source.tar.gz"
    record = source_snapshot(root, archive)
    with tarfile.open(archive) as stream:
        assert sorted(stream.getnames()) == sorted(source_files)
    assert len(record["source_archive_sha256"]) == 64
