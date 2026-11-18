"""Kernel availability, old records, and independent low-frequency collection."""
import asyncio
import csv
import io
import json
import time
from types import SimpleNamespace
import uuid

import pytest

from app.adb import AdbError, parse_proc_meminfo, parse_process_rss
from app.models import MetricsConfig, ProcessSample, SamplePayload, StartSessionRequest
from app.monitor import MonitorManager, RuntimeSession
from app.monkey_records import MonkeyRecords, MonkeyReportStore, connect
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


def test_parse_android_process_rss_keeps_all_valid_processes():
    output = """  PID    RSS NAME
    1  16864 init
    2      0 [kthreadd]
    3      - system_hidden
 1842 743656 system_server
 2400  54000 a process with spaces
 bad   56000 malformed
    """
    assert parse_process_rss(output) == [
        ProcessSample("init", 1, rss_kb=16864),
        ProcessSample("system_server", 1842, rss_kb=743656),
        ProcessSample("a process with spaces", 2400, rss_kb=54000),
    ]
    assert parse_process_rss("unexpected ps usage\n1 16864 init") == []


@pytest.mark.parametrize("cpu_interval,legacy_skip,base", [(500, None, 10), (2000, None, 10), (1000, 4, 4)])
def test_memory_lanes_have_independent_cadence_and_write_once(tmp_path, monkeypatch, cpu_interval, legacy_skip, base):
    async def scenario():
        manager = MonitorManager(tmp_path)
        clock = SimpleNamespace(now=0)
        monkeypatch.setattr("app.monitor.time", SimpleNamespace(monotonic=lambda: clock.now, time=time.time))
        observed = {"system": [], "pss": [], "rss": []}

        async def system(_serial):
            observed["system"].append(clock.now)
            return parse_proc_meminfo("MemTotal: 1000 kB\nMemAvailable: 900 kB")

        async def full(_runtime):
            observed["pss"].append(clock.now)
            return (100, 120, 1000, [ProcessSample("app", 2, pss_kb=100, rss_kb=120, uss_kb=5)])

        async def lightweight(_serial):
            observed["rss"].append(clock.now)
            return [ProcessSample("app", 2, rss_kb=130), ProcessSample("native", 3, rss_kb=50)]

        monkeypatch.setattr(manager, "_capture_system_memory", system)
        monkeypatch.setattr(manager, "_capture_memory", full)
        monkeypatch.setattr(manager, "_capture_process_rss", lightweight)
        request = StartSessionRequest(serial="test", interval_ms=cpu_interval, memory_cycle_skip=legacy_skip,
                                      metrics=MetricsConfig(cpu=False, fps=False))
        runtime = RuntimeSession("test", request, SimpleNamespace(add_event=lambda *_: None))
        points = []
        for tick_ms in range(0, base * 4 * 1000 + 1, cpu_interval):
            clock.now = tick_ms / 1000
            points.append(await manager._capture_once(runtime))
            runtime.sample_cycle += 1
            await asyncio.sleep(0)
        assert observed == {
            "system": [i * base for i in range(5)],
            "pss": [0, base * 3],
            "rss": [((i * base + base / 2) * 1000 + cpu_interval - 1) // cpu_interval * cpu_interval / 1000
                    for i in range(4)],
        }
        assert sum(p.memory_used_pct == 10 for p in points) == len(points) - 1
        assert sum(any(row.pss_kb == 100 for row in p.processes) for p in points) == 2
        assert sum(any(row.rss_kb == 130 for row in p.processes) for p in points) == 4
        assert all(row.rss_kb != 120 for p in points for row in p.processes)
        assert all(p.rss_kb == 120 for p in points[1:])
    asyncio.run(scenario())


def test_rss_failure_does_not_restart_full_query_and_pending_rss_is_cancelled(tmp_path, monkeypatch):
    async def scenario():
        manager = MonitorManager(tmp_path)
        request = StartSessionRequest(serial="test", interval_ms=500, memory_cycle_skip=1,
                                      duration_seconds=30, metrics=MetricsConfig(cpu=False, fps=False))
        clock = SimpleNamespace(now=0)
        monkeypatch.setattr("app.monitor.time", SimpleNamespace(monotonic=lambda: clock.now, time=time.time))
        calls = []
        cancelled = asyncio.Event()

        async def adb(*args, **kwargs):
            calls.append(args)
            if args[1] == "ps":
                if calls.count(args) == 1:
                    raise AdbError("unsupported output")
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            if args[-1] == "/proc/meminfo":
                return "MemTotal: 1000 kB\nMemAvailable: 900 kB\n"
            return "Total PSS by process:\n 100K: app (pid 8)\nTotal RAM: 1000K\n"

        monkeypatch.setattr("app.monitor.run_adb", adb)
        events = []
        runtime = RuntimeSession("test", request, SimpleNamespace(
            add_event=lambda *args: events.append(args), finish=lambda *_: None))
        for pos in (0, .25, .5, .75, 1):
            clock.now = pos
            await manager._capture_once(runtime)
            await asyncio.sleep(0)
        assert sum(call[1] == "dumpsys" for call in calls) == 1
        assert sum(call[1] == "ps" for call in calls) == 2
        assert any(e[1] == "process_rss_unavailable" for e in events)
        assert runtime.process_rss_task is not None and not runtime.process_rss_task.done()
        runtime.stop_event.set()
        await manager._run(runtime)
        assert cancelled.is_set()
    asyncio.run(scenario())


def test_rss_growth_survives_slice_archive_and_keeps_existing_memory_metrics(tmp_path):
    store = SessionStore(tmp_path / "session-data")
    sid = uuid.uuid4().hex
    writer = store.create_session(sid, {"session_id": sid, "serial": "test", "created_at_ms": 1000,
        "duration_seconds": 30, "interval_ms": 1000, "enabled_metrics": {"cpu": True, "memory": True, "fps": False}})
    writer.write_sample(SamplePayload(ts_ms=1000, processes=[
        ProcessSample("resident", 1, rss_kb=30720), ProcessSample("background", 2, rss_kb=45000)
    ]))
    writer.write_sample(SamplePayload(ts_ms=6000, pss_kb=25000, total_ram_kb=100000, memory_used_pct=75,
        processes=[ProcessSample("resident", 1, pss_kb=25000, uss_kb=300)]))
    writer.write_sample(SamplePayload(ts_ms=11000, processes=[
        ProcessSample("resident", 1, rss_kb=32768), ProcessSample("short-lived", 3, rss_kb=25000)
    ]))
    writer.write_sample(SamplePayload(ts_ms=21000, processes=[ProcessSample("resident", 1, rss_kb=32000)]))
    writer.finish("completed")

    growth = store.get_processes(sid, "rss_growth")
    assert growth == [
        {"process_name": "short-lived", "pid": 3, "average": 25000, "peak": 25000, "samples": 1},
        {"process_name": "resident", "pid": 1, "average": 2048, "peak": 2048, "samples": 1},
    ]
    assert store.get_processes(sid, "rss")[0]["process_name"] == "background"
    assert store.get_processes(sid, "pss")[0]["process_name"] == "resident"
    assert store.get_process_uss_series(sid)[0]["uss_kb"] == 300
    assert "进程 RSS 新增 / 增长排行" in generate_report(sid, store)

    records = MonkeyRecords(tmp_path / "monkey-data")
    run_id = "b" * 32
    run = {"id": run_id, "serial": "test", "state": "running", "created_at_ms": 1000,
           "config": {"duration_hours": 1, "interval_ms": 1000, "fps": False}}
    records.put(run)
    records.initialize(run)
    records.archive(run_id, sid, store.database_path(sid))
    summary = records.summarize(run_id, "completed", 22000)
    assert summary["rss_growth_processes"] == growth
    assert summary["metrics"]["memory_used_pct"]["average"] == 75
    assert summary["metrics"]["memory_pss"]["peak"] == 25000
    assert summary["metrics"]["memory_rss"]["valid_samples"] == 0
    report = generate_report(run_id, MonkeyReportStore(records.database(run_id)))
    assert "short-lived" in report and "进程 PSS 占用排行" in report


def test_cancelling_an_adb_command_during_spawn_reaps_the_child(monkeypatch):
    from app.adb import run_adb

    async def scenario():
        creating = asyncio.Event()
        spawn_complete = asyncio.Event()
        reaped = asyncio.Event()

        class Child:
            returncode = None

            def kill(self):
                self.returncode = -9

            async def communicate(self):
                reaped.set()
                return b"", b""

        async def spawn(*args, **kwargs):
            creating.set()
            await spawn_complete.wait()
            return Child()

        monkeypatch.setattr("app.adb.shutil.which", lambda *_: "test-adb")
        monkeypatch.setattr("app.adb.asyncio.create_subprocess_exec", spawn)
        task = asyncio.create_task(run_adb("shell", "ps"))
        await creating.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        spawn_complete.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reaped.is_set()

    asyncio.run(scenario())
