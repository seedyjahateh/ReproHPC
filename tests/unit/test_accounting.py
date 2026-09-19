"""Accounting response doubles test ID reuse, not scheduler integration."""

import subprocess
from types import SimpleNamespace

import pytest

from reprohpc.io import read_json, write_json
from reprohpc.provenance import collect_accounting


def task(tmp_path, native_id, status="COMPLETED"):
    work = tmp_path / native_id
    work.mkdir()
    write_json(work / ".reprohpc-origin.json", {"run_id": "unit-origin"})
    return {"native_id": native_id, "status": status, "origin_work_dir": str(work), "usage": {}}


def test_accounting_survives_cache_and_native_id_reuse(tmp_path, monkeypatch):
    original = task(tmp_path, "123_0")
    records = "123_0|COMPLETED|0:0|9|1|512M||00:00:08|124|10|\n"
    monkeypatch.setattr(
        "reprohpc.provenance.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=records, stderr=""),
    )
    collect_accounting([original], tmp_path)
    saved = read_json(tmp_path / "123_0/.reprohpc-origin.json")["slurm_accounting"]
    assert saved["records"] == [records.strip().split("|")]
    original["status"] = "CACHED"
    original["usage"] = {}

    def do_not_query(*args, **kwargs):
        pytest.fail("A cached native ID must never be queried again")

    monkeypatch.setattr("reprohpc.provenance.subprocess.run", do_not_query)
    collect_accounting([original], tmp_path)
    assert original["usage"]["slurm_records"] == saved["records"]
    assert original["usage"]["accounting_captured_at"] == saved["captured_at"]
    assert original["usage"]["accounting_scope"] == "origin_run"
    assert original["usage"]["accounting_unavailable_reason"] is None


def test_mixed_cache_only_queries_current_invocation(tmp_path, monkeypatch):
    cached = task(tmp_path, "5", "CACHED")
    current = task(tmp_path, "8")

    def query(command, **kwargs):
        assert command[command.index("-j") + 1] == "8"
        return SimpleNamespace(
            returncode=0, stdout="8|COMPLETED|0:0|1|1|512M||00:00:01|8|10|\n", stderr=""
        )

    monkeypatch.setattr("reprohpc.provenance.subprocess.run", query)
    collect_accounting([cached, current], tmp_path)
    assert cached["usage"]["slurm_records"] == []
    assert cached["usage"]["accounting_unavailable_reason"] == "origin_accounting_not_recorded"
    assert cached["usage"]["accounting_captured_at"] is None
    assert current["usage"]["slurm_records"][0][0] == "8"


@pytest.mark.parametrize("failure", ["delayed", "denied", "missing", "timeout"])
def test_accounting_unavailable_is_explicit(tmp_path, monkeypatch, failure):
    record = task(tmp_path, "7")

    def query(*args, **kwargs):
        if failure == "missing":
            raise FileNotFoundError("sacct unavailable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("sacct", 15)
        return SimpleNamespace(returncode=int(failure == "denied"), stdout="", stderr="denied")

    monkeypatch.setattr("reprohpc.provenance.subprocess.run", query)
    collect_accounting([record], tmp_path, timeout=0)
    assert record["usage"]["slurm_records"] == []
    assert record["usage"]["accounting_unavailable_reason"]
    assert read_json(tmp_path / "7/.reprohpc-origin.json")["slurm_accounting"]["unavailable_reason"]
