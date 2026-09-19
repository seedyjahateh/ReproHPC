"""Arithmetic tests use artificial timings, never claimed as scaling evidence."""

import importlib.util
from pathlib import Path

import pytest

from reprohpc.io import write_json

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("benchmark", ROOT / "scripts/benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_three_repetition_medians_and_efficiency():
    timings = {1: [90, 100, 150], 2: [70, 50, 45], 4: [35, 25, 26]}
    trials = [
        {"workers": workers, "analysis_seconds": seconds, "workflow_seconds": seconds + 20}
        for workers, values in timings.items()
        for seconds in values
    ]
    summary = benchmark.summarize(trials)
    assert [row["analysis_median_seconds"] for row in summary] == [100, 50, 26]
    assert summary[2]["speedup"] == pytest.approx(100 / 26)
    assert summary[2]["efficiency"] == pytest.approx(100 / 104)
    assert summary[0]["analysis_min_seconds"] == 90
    assert summary[0]["analysis_max_seconds"] == 150
    assert summary[2]["workflow_median_seconds"] == 46
    with pytest.raises(ValueError, match="three"):
        benchmark.summarize(trials[:-1])


def test_accounting_does_not_double_count_parent_and_batch(tmp_path):
    path = tmp_path / "tasks.jsonl"
    write_json(
        path,
        {
            "process": "ANALYZE_BATCH",
            "requested": {"memory": "1048576"},
            "usage": {
                "slurm_records": [
                    ["123_0", "COMPLETED", "0:0", "20", "1", "1M", "900K", "00:00:15"],
                    ["123_0.batch", "COMPLETED", "0:0", "20", "1", "1M", "512K", "00:00:10"],
                ]
            },
        },
    )
    values = benchmark.accounting_efficiency(path)
    assert values == [{"cpu_efficiency": 0.5, "rss_fraction": 0.5, "reason": None}]
    write_json(
        path,
        {
            "process": "ANALYZE_BATCH",
            "requested": {"memory": "1048576"},
            "usage": {"slurm_records": []},
        },
    )
    values = benchmark.accounting_efficiency(path)
    assert values[0]["cpu_efficiency"] is None and values[0]["reason"]


def test_trace_stage_includes_launch_and_queue_wait(tmp_path):
    path = tmp_path / "trace.tsv"
    path.write_text(
        "process\tstatus\tsubmit\tstart\tcomplete\trealtime\n"
        "ANALYZE_BATCH\tCOMPLETED\t1000\t2000\t6000\t4000\n"
        "ANALYZE_BATCH\tCOMPLETED\t2000\t4000\t7000\t3000\n"
    )
    measured = benchmark.trace_measurements(path)
    assert measured["analysis_seconds"] == 6
    assert measured["queue_wait_task_seconds"] == 3
    assert measured["task_seconds_by_stage"] == {"ANALYZE_BATCH": 7}
    path.write_text(path.read_text().replace("COMPLETED", "CACHED", 1))
    with pytest.raises(ValueError, match="uncached"):
        benchmark.trace_measurements(path)
