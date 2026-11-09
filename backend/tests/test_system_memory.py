"""Kernel availability, old records, and independent low-frequency collection."""
import asyncio
import csv
import io
import json
import time
from types import SimpleNamespace
import uuid

import pytest

from app.adb import AdbError, parse_proc_meminfo
from app.models import MetricsConfig, SamplePayload, StartSessionRequest
from app.monitor import MonitorManager, RuntimeSession
from app.monkey_records import MonkeyRecords, connect
from app.report import generate_report
from app.storage import SessionStore, SYSTEM_MEMORY_COLUMNS


@pytest.mark.parametrize("output,expected", [
    ("MemTotal: 1000 kB\nMemAvailable: 250 kB\nMemFree: 10 kB\n", 75),
    ("MemTotal: 1000 kB\nMemAvailable: 0 kB\n", 100),
    ("MemTotal: 1000 kB\nMemAvailable: 1000 kB\n", 0),
    ("MemTotal: 1000 kB\nMemFree: 200 kB\nCached: 300 kB\n", None),
    ("MemAvailable: 100 kB\n", None),
    ("MemTotal: 0 kB\nMemAvailable: 0 kB\n", None),
    ("MemTotal: 1000 kB\nMemAvailable: 1200 kB\n", None),
    ("MemTotal: 1000 kB\nMemAvailable: -1 kB\n", None),
    ("MemTotal: 1000 kB\nMemAvailable: 200 MB\n", None),
    ("MemTotal: garbage kB\nMemAvailable: 200 kB\n", None),
    ("Total RAM: 1000K\nTotal PSS: 700K\nFree RAM: 200K\n", None),
    ("", None),
])
def test_proc_meminfo_uses_available_without_fallback(output, expected):
    result = parse_proc_meminfo(output)
    assert result.memory_used_pct == expected
    if expected is not None:
        assert result.mem_used_kb == result.mem_total_kb - result.mem_available_kb
    else:
        assert result.mem_used_kb is None


def session(root, *, legacy=False, timestamp=1000):
    store = SessionStore(root)
    sid = uuid.uuid4().hex
    writer = store.create_session(sid, {"session_id": sid, "serial": "memory-test", "created_at_ms": timestamp,
        "duration_seconds": 1, "interval_ms": 1000, "enabled_metrics": {"cpu": True, "memory": True, "fps": False}})
    # PSS/RAM=90% deliberately differs from the system availability metric (75%).
    writer.write_sample(SamplePayload(ts_ms=timestamp, cpu_total_pct=20, pss_kb=900, total_ram_kb=1000,
        mem_total_kb=1000, mem_available_kb=250, mem_used_kb=750, memory_used_pct=75))
    writer.finish("completed")
    with connect(store.database_path(sid)) as c:
        c.execute("UPDATE session SET ended_at_ms=?", (timestamp + 1000,))
        if legacy:
            for name in SYSTEM_MEMORY_COLUMNS:
                c.execute(f"ALTER TABLE sample DROP COLUMN {name}")
            summary = json.loads(c.execute("SELECT summary_json FROM session").fetchone()[0])
            for name in ["memory_used_pct", "memory_used", "memory_available"]:
                summary["metrics"].pop(name, None)
            c.execute("UPDATE session SET summary_json=?", (json.dumps(summary),))
    return store, sid


def test_new_rate_round_trips_through_database_summary_csv_and_report(tmp_path):
    store, sid = session(tmp_path / "data")
    point = store.get_series(sid)[0]
    assert point["memory_used_pct"] == 75
    assert point["mem_available_kb"] == 250
    assert point["pss_kb"] == 900
    summary = store.get_session(sid)["summary"]["metrics"]
    assert summary["memory_used_pct"]["average"] == 75
    assert summary["memory_used_pct"]["peak"] == 75
    assert summary["memory_available"]["average"] == 250
    rows = list(csv.DictReader(io.StringIO(store.to_csv(sid))))
    assert float(rows[0]["memory_used_pct"]) == 75
    assert int(rows[0]["mem_used_kb"]) == 750
    html = generate_report(sid, store)
    assert "系统内存占用率 平均 / 峰值" in html
    assert "75.00% / 75.00%" in html
    assert "PSS / RAM 平均 / 峰值" in html
    assert "MemTotal - MemAvailable" in html
    assert "接近系统真实占用" not in html


def test_legacy_records_leave_system_memory_unrecorded(tmp_path):
    store, sid = session(tmp_path / "data", legacy=True)
    point = store.get_series(sid)[0]
    assert point["memory_used_pct"] is None and point["mem_available_kb"] is None
    assert point["pss_kb"] == 900
    assert list(csv.DictReader(io.StringIO(store.to_csv(sid))))[0]["memory_used_pct"] == ""
    html = generate_report(sid, store)
    assert "系统内存占用率 平均 / 峰值" in html
    assert "75.00% / 75.00%" not in html


def test_monkey_merges_old_and_new_segments_without_inventing_history(tmp_path):
    records = MonkeyRecords(tmp_path / "monkey-data")
    run = {"id": "c" * 32, "serial": "memory-test", "state": "running", "created_at_ms": 1000,
           "config": {"duration_hours": 1, "interval_ms": 1000, "fps": False}}
    records.put(run)
    records.initialize(run)
    # An existing aggregate created by the previous portable version.
    with connect(records.database(run["id"])) as c:
        for name in SYSTEM_MEMORY_COLUMNS:
            c.execute(f"ALTER TABLE sample DROP COLUMN {name}")
    for old, timestamp in [(True, 1000), (False, 3000)]:
        source, sid = session(tmp_path / "data", legacy=old, timestamp=timestamp)
        records.archive(run["id"], sid, source.database_path(sid))
    summary = records.summarize(run["id"], "completed", 4000)
    metric = summary["metrics"]["memory_used_pct"]
    assert summary["sample_count"] == 2
    assert metric["valid_samples"] == 1
    assert metric["average"] == 75 and metric["peak"] == 75
    assert metric["observed_ms"] == 1000
    assert sum(bin["percent"] for bin in metric["distribution"]) == pytest.approx(100)
    assert summary["metrics"]["memory_pss_pct"]["average"] == 90
    for number, expected in [(1, ""), (2, "75.0")]:
        with (records.directory(run["id"]) / f"hour-{number:04d}.csv").open(encoding="utf-8-sig") as file:
            row = next(csv.DictReader(file))
        assert row["memory_used_pct"] == expected


@pytest.mark.parametrize("unavailable", [False, True])
def test_system_memory_does_not_wait_for_slow_pss_and_expires(tmp_path, monkeypatch, unavailable):
    async def scenario():
        monitor = MonitorManager(tmp_path / "data")
        events = []
        runtime = RuntimeSession("memory", StartSessionRequest(serial="test", memory_cycle_skip=5,
            metrics=MetricsConfig(cpu=False, memory=True, fps=False)), SimpleNamespace(add_event=lambda *args: events.append(args)))
        blocked = asyncio.Event()
        async def slow_pss(_):
            await blocked.wait()
        async def proc(_):
            if unavailable:
                raise AdbError("permission denied")
            return parse_proc_meminfo("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
        monkeypatch.setattr(monitor, "_capture_memory", slow_pss)
        monkeypatch.setattr(monitor, "_capture_system_memory", proc)
        try:
            await monitor._capture_once(runtime)
            await asyncio.sleep(0)
            runtime.sample_cycle = 1
            point = await asyncio.wait_for(monitor._capture_once(runtime), 0.5)
            assert point.memory_used_pct == (None if unavailable else 75)
            assert point.pss_kb is None and not runtime.memory_task.done()
            if unavailable:
                assert point.statuses["system_memory"] == "unavailable" and events
                runtime.memory_result = (900, None, 1000, [])
                runtime.memory_updated_monotonic = time.monotonic()
                point = await monitor._capture_once(runtime)
                assert point.pss_kb == 900 and point.memory_used_pct is None
            else:
                runtime.system_memory_updated_monotonic = time.monotonic() - 200
                assert (await monitor._capture_once(runtime)).memory_used_pct is None
            assert sum(not task.done() for task in runtime.inflight) <= 1
        finally:
            tasks = list(runtime.inflight)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())
