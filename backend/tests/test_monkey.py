"""Monkey lifecycle tests use real subprocesses and SQLite, never a connected Android device."""
import asyncio
import csv
import json
from pathlib import Path
import sqlite3
import sys
import time

import pytest
from pydantic import ValidationError

from app import adb, monkey as m
from app.models import MetricsConfig, ProcessSample, SamplePayload, StartSessionRequest
from app.monitor import MonitorManager
from app.monkey_records import MonkeyRecords, connect
from app.ui_testing import UIManager


def config(**changes):
    return m.MonkeyRequest(serial="test-device", mode="none", interval_ms=1000, **changes)


@pytest.mark.parametrize("changes", [
    {"ratios": {"touch": 44}}, {"ratios": {"touch": -1}}, {"ratios": {"touch": 45.1}},
    {"duration_hours": 0}, {"duration_hours": 1.5}, {"throttle_ms": 0},
    {"mode": "whitelist", "packages": []}, {"mode": "blacklist", "packages": ["x;reboot"]},
    {"packages": ["com.example"]}, {"serial": "bad;serial"}, {"unexpected": "shell"},
])
def test_invalid_configuration_rejected(changes):
    values = {"serial": "test-device", "mode": "none", **changes}
    with pytest.raises(ValidationError):
        m.MonkeyRequest(**values)


@pytest.mark.parametrize("mode", ["none", "blacklist", "whitelist"])
def test_command_preserves_zero_ratios_and_exclusive_list_mode(mode):
    value = m.MonkeyRequest(serial="test", mode=mode, packages=[] if mode == "none" else ["com.example", "com.example"])
    args = m.monkey_arguments(value, m.remote_paths("a" * 32))
    assert args[-1] == "2147483647"
    assert "-v" not in args and "--bugreport" not in args
    assert args[args.index("--pct-trackball") + 1] == "0"
    assert sum(value.ratios.model_dump().values()) == 100
    assert ("--pkg-blacklist-file" in args) == (mode == "blacklist")
    assert ("--pkg-whitelist-file" in args) == (mode == "whitelist")
    assert len(value.packages) <= 1
    script = m.runner_script(value, m.remote_paths("a" * 32))
    assert "com.android.commands.monkey.Monkey" in script
    assert "stat -c %Y" in script and "90" in script
    assert "pkill" not in script and "killall" not in script
    assert script.count("/proc/$child/stat") >= 3  # PID reuse is checked before escalation.


def new_records(tmp_path):
    records = MonkeyRecords(tmp_path / "monkey-data")
    run = {"id": "b" * 32, "serial": "test-device", "state": "running", "created_at_ms": 0, "config": config().model_dump()}
    records.put(run)
    records.initialize(run)
    return records, run


def source_session(root, count=4, start=1000, cpu=20):
    from app.storage import SessionStore
    store = SessionStore(root)
    sid = __import__("uuid").uuid4().hex
    writer = store.create_session(sid, {"session_id": sid, "serial": "test-device", "created_at_ms": start,
                                       "duration_seconds": 3600, "interval_ms": 1000,
                                       "enabled_metrics": {"cpu": True, "memory": True, "fps": False}})
    for i in range(count):
        writer.write_sample(SamplePayload(ts_ms=start + i * 1000, cpu_total_pct=cpu,
                                         pss_kb=400, total_ram_kb=1000,
                                         processes=[ProcessSample("process.a", cpu_pct=10, pss_kb=100, uss_kb=60)]))
    writer.finish("completed")
    path = store.database_path(sid)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE session SET ended_at_ms=?", (start + count * 1000,))
    return sid, path


def test_hourly_csv_is_complete_and_merge_is_idempotent(tmp_path):
    records, run = new_records(tmp_path)
    sid, source = source_session(tmp_path / "data", count=1507)
    assert records.archive(run["id"], sid, source) == 1
    assert records.archive(run["id"], sid, source) == 1
    second, path = source_session(tmp_path / "data", count=3, start=2_000_000, cpu=40)
    assert records.archive(run["id"], second, path) == 2
    with (records.directory(run["id"]) / "hour-0001.csv").open(encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 1507
    assert float(rows[-1]["memory_pss_pct"]) == 40
    with connect(records.database(run["id"])) as c:
        assert c.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 1510
        assert c.execute("SELECT COUNT(*) FROM process_sample").fetchone()[0] == 1510
        assert c.execute("SELECT COUNT(*) FROM process_sample p LEFT JOIN sample s ON p.sample_id=s.id WHERE s.id IS NULL").fetchone()[0] == 0
    summary = records.summarize(run["id"], "completed", 2_003_000)
    with (records.directory(run["id"]) / "combined.csv").open(encoding="utf-8-sig") as file:
        merged = list(csv.DictReader(file))
    assert len(merged) == 1510
    assert merged[0]["segment_no"] == "1" and merged[-1]["segment_no"] == "2"
    assert float(merged[-1]["cpu_total_pct"]) == 40
    assert summary["sample_count"] == 1510
    assert summary["segment_count"] == 2
    assert summary["metrics"]["cpu"]["average"] == pytest.approx((1507 * 20 + 3 * 40) / 1510)
    assert summary["metrics"]["cpu"]["observed_ms"] == 1510_000
    assert summary["metrics"]["memory_pss_pct"]["average"] == 40
    assert summary["processes"][0]["cpu_sampled_share_pct"] == 100
    assert summary["processes"][0]["pss_ram_average_pct"] == 10
    assert sum(bin["percent"] for bin in summary["metrics"]["cpu"]["distribution"]) == pytest.approx(100)
    assert len(records.runs(brief=True)) == 1 and "config" not in records.runs(brief=True)[0]


def test_missing_samples_and_large_time_gaps_do_not_become_zeroes(tmp_path):
    records, run = new_records(tmp_path)
    sid, path = source_session(tmp_path / "data", count=3, cpu=20)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE sample SET cpu_total_pct=NULL WHERE id=2")
        c.execute("UPDATE sample SET ts_ms=12000,cpu_total_pct=80 WHERE id=3")
        c.execute("UPDATE session SET ended_at_ms=13000")
    records.archive(run["id"], sid, path)
    summary = records.summarize(run["id"], "stopped", 13000)
    assert summary["metrics"]["cpu"]["average"] == 50
    assert summary["metrics"]["cpu"]["valid_samples"] == 2
    assert summary["metrics"]["cpu"]["observed_ms"] == 2000
    assert summary["metrics"]["memory_pss"]["observed_ms"] == 5000


FAKE_ADB = r'''
import json, os, sys, time
from pathlib import Path
root = Path(__file__).parent
args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as file: file.write(json.dumps(args)+'\n')
if 'devices' in args:
 print('List of devices attached\ntest-device device model:Fake')
elif 'list' in args and 'packages' in args:
 print('package:com.example')
elif 'getprop' in args:
 print('30')
elif len(args)>4 and args[3]=='sh' and args[4].startswith('/data/local/tmp/acpm-monkey-'):
 if (root/'reject-launch').exists():
  print('Unknown option --pct-rotation',flush=True);sys.exit(1)
 print('__ACPM_MONKEY_PID__=1234',flush=True)
 if (root/'flood').exists():
  for _ in range(30): print('x'*8191,flush=True)
 if (root/'early-exit').exists(): sys.exit(0)
 time.sleep(120)
elif 'touch' in args and (root/'disconnect').exists():
 print('device offline',file=sys.stderr);sys.exit(1)
elif 'hang' in args:
 time.sleep(120)
'''


@pytest.fixture
def fake_adb(tmp_path, monkeypatch):
    script = tmp_path / "fake-adb.py"
    script.write_text(FAKE_ADB)
    original = asyncio.create_subprocess_exec
    original_which = m.shutil.which
    children = []
    async def spawn(*args, **kwargs):
        if args[0] == "fake-adb":
            process = await original(sys.executable, str(script), *args[1:], **kwargs)
            children.append(process)
            return process
        return await original(*args, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(m.shutil, "which", lambda name: "fake-adb" if name == "adb" else original_which(name))
    monkeypatch.setattr(m, "HOUR_SECONDS", 1)
    monkeypatch.setattr(m, "HEARTBEAT_SECONDS", 0.1)
    async def capture(self, runtime):
        return SamplePayload(ts_ms=int(time.time() * 1000), cpu_total_pct=25, pss_kb=500, total_ram_kb=1000,
                             processes=[ProcessSample("com.example", cpu_pct=12, pss_kb=250)])
    monkeypatch.setattr(MonitorManager, "_capture_once", capture)
    return tmp_path, children


def managers(tmp_path):
    monitor = MonitorManager(tmp_path / "data")
    ui = UIManager(tmp_path / "ui-data", monitor)
    monitor.ui_manager = ui
    return m.MonkeyManager(tmp_path / "monkey-data", monitor, ui), monitor, ui


async def settled(manager, run, timeout=8):
    runtime = manager.active[run["id"]]
    await asyncio.wait_for(runtime.task, timeout)
    return manager.store.get(run["id"])


async def running(manager, run):
    for _ in range(300):
        if manager.store.get(run["id"])["current_session_id"]:
            return
        if not manager.active:
            raise AssertionError(manager.store.get(run["id"]))
        await asyncio.sleep(0.01)
    raise AssertionError("never started")


def test_multi_hour_run_rolls_exports_and_merges_without_reports(fake_adb):
    tmp, children = fake_adb
    async def scenario():
        manager, monitor, ui = managers(tmp)
        run = await manager.start(config(duration_hours=3))
        result = await settled(manager, run)
        assert result["state"] == "completed", result
        assert result["segment_count"] >= 2
        summary = json.loads((manager.store.directory(run["id"]) / "summary.json").read_text())
        assert summary["sample_count"] >= 3
        assert summary["metrics"]["memory_pss_pct"]["average"] == 50
        assert len(list(manager.store.directory(run["id"]).glob("hour-*.csv"))) == result["segment_count"]
        assert not list(manager.store.directory(run["id"]).rglob("*.html"))
        assert b"\r" not in (manager.store.directory(run["id"]) / "runner.sh").read_bytes()
        assert not monitor.active and monitor.reserved_by is None
        assert not ui.external_busy
        assert children and all(child.returncode is not None for child in children)
    asyncio.run(scenario())


def test_continuous_stop_preserves_partial_and_blocks_other_tasks(fake_adb):
    tmp, children = fake_adb
    async def scenario():
        manager, monitor, ui = managers(tmp)
        run = await manager.start(config(duration_hours=None))
        await running(manager, run)
        with pytest.raises(ValueError, match="Monkey"):
            await manager.start(config())
        with pytest.raises(ValueError, match="Monkey"):
            await monitor.start(StartSessionRequest(serial="other"))
        with pytest.raises(ValueError, match="Monkey"):
            await monitor.stop(manager.store.get(run["id"])["current_session_id"])
        assert ui.has_active_device("test-device")
        manager.stop(run["id"])
        result = await settled(manager, run)
        assert result["state"] == "stopped", result
        assert result["segment_count"] == 1
        with (manager.store.directory(run["id"]) / "combined.csv").open(encoding="utf-8-sig") as file:
            samples = list(csv.DictReader(file))
        with connect(manager.store.database(run["id"])) as c:
            assert len(samples) == c.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
        assert children and all(child.returncode is not None for child in children)
        assert not monitor.active and not ui.external_busy
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["reject-launch", "early-exit", "disconnect"])
def test_failure_paths_release_ownership_and_keep_records(fake_adb, failure):
    tmp, children = fake_adb
    (tmp / failure).touch()
    async def scenario():
        manager, monitor, ui = managers(tmp)
        run = await manager.start(config(duration_hours=None))
        result = await settled(manager, run)
        assert result["state"] == "failed", result
        assert not monitor.active and monitor.reserved_by is None and not ui.external_busy
        assert children and all(child.returncode is not None for child in children)
    asyncio.run(scenario())


def test_shutdown_and_recovery_archive_without_resuming_inputs(fake_adb):
    tmp, children = fake_adb
    async def scenario():
        manager, monitor, ui = managers(tmp)
        run = await manager.start(config(duration_hours=None))
        await running(manager, run)
        await manager.close()
        assert manager.store.get(run["id"])["state"] == "interrupted"
        # Emulate an abrupt service exit with an unarchived, already recovered source DB.
        old = manager.store.get(run["id"])
        sid, source = source_session(tmp / "data", count=2)
        old.update(state="running", current_session_id=sid)
        manager.store.put(old)
        fresh, _, _ = managers(tmp)
        await fresh.recover()
        recovered = fresh.store.get(run["id"])
        assert recovered["state"] == "interrupted"
        assert recovered["segment_count"] >= 2
        assert (fresh.store.directory(run["id"]) / "combined.csv").is_file()
        assert fresh.active == {}
        assert children and all(child.returncode is not None for child in children)
    asyncio.run(scenario())


def test_combined_csv_download_for_legacy_run_and_running_guard(tmp_path):
    from types import SimpleNamespace
    from fastapi import HTTPException

    records, run = new_records(tmp_path)
    sid, source = source_session(tmp_path / "data", count=2)
    records.archive(run["id"], sid, source)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(monkey=SimpleNamespace(store=records))))

    async def scenario():
        with pytest.raises(HTTPException) as error:
            await m.download(run["id"], "combined.csv", request)
        assert error.value.status_code == 409
        run["state"] = "stopped"
        records.put(run)
        response = await m.download(run["id"], "combined.csv", request)
        assert Path(response.path).is_file()
        with Path(response.path).open(encoding="utf-8-sig") as file:
            assert len(list(csv.DictReader(file))) == 2
    asyncio.run(scenario())


def test_monkey_report_uses_merged_records_and_supports_png(tmp_path):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.monkey_records import MonkeyReportStore

    records, run = new_records(tmp_path)
    first, source = source_session(tmp_path / "data", count=1200, cpu=20)
    second, source2 = source_session(tmp_path / "data", count=3, start=2_000_000, cpu=40)
    records.archive(run["id"], first, source)
    records.archive(run["id"], second, source2)
    records.summarize(run["id"], "stopped", 2_003_000)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(monkey=SimpleNamespace(store=records))))

    async def scenario():
        with pytest.raises(HTTPException) as error:
            await m.report(run["id"], request)
        assert error.value.status_code == 409
        run.update(state="stopped", segment_count=2)
        records.put(run)
        response = await m.report(run["id"], request)
        html = response.body.decode()
        assert "1203 点" in html
        assert "CPU 整体占用" in html and "系统内存占用率" in html
        assert "保存为图片 (PNG)" in html and "capturePNG()" in html
        assert "抽稀" in html and "process.a" in html
        samples = MonkeyReportStore(records.database(run["id"])).get_all_samples(run["id"])
        assert samples[0]["cpu_total_pct"] == 20 and samples[-1]["cpu_total_pct"] == 40
    asyncio.run(scenario())


def test_log_and_tail_remain_bounded(fake_adb, monkeypatch):
    tmp, _ = fake_adb
    (tmp / "flood").touch()
    monkeypatch.setattr(m, "LOG_LIMIT", 16384)
    async def scenario():
        manager, _, _ = managers(tmp)
        run = await manager.start(config(duration_hours=None))
        await running(manager, run)
        await asyncio.sleep(0.1)
        manager.stop(run["id"])
        await settled(manager, run)
        logs = list(manager.store.directory(run["id"]).glob("monkey.log*"))
        assert len(logs) <= 2 and sum(path.stat().st_size for path in logs) <= 32768
        assert len(manager.store.tail(run["id"]).encode()) <= 8192
    asyncio.run(scenario())


def test_cancelled_adb_command_reaps_host_subprocess(fake_adb):
    _, children = fake_adb
    async def scenario():
        task = asyncio.create_task(adb.run_adb("hang", timeout_seconds=120))
        while not children:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(child.returncode is not None for child in children)
    asyncio.run(scenario())


def test_immediate_stop_before_task_entry_does_not_start_device_input(fake_adb):
    tmp, children = fake_adb
    async def scenario():
        manager, monitor, ui = managers(tmp)
        run = await manager.start(config(duration_hours=None))
        manager.stop(run["id"])
        result = await settled(manager, run)
        assert result["state"] == "stopped"
        assert result["segment_count"] == 0
        assert monitor.reserved_by is None and not ui.external_busy
        calls = [json.loads(line) for line in (tmp / "calls.jsonl").read_text().splitlines()]
        assert not any(len(args) > 4 and args[3] == "sh" and args[4].startswith("/data/local/tmp/acpm-monkey-") for args in calls)
        assert all(child.returncode is not None for child in children)
    asyncio.run(scenario())


def test_disk_guard_stops_input_and_preserves_partial(fake_adb, monkeypatch):
    from types import SimpleNamespace
    tmp, children = fake_adb
    async def scenario():
        manager, monitor, ui = managers(tmp)
        run = await manager.start(config(duration_hours=None))
        await running(manager, run)
        monkeypatch.setattr(m.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
        result = await settled(manager, run)
        assert result["state"] == "failed"
        assert "128 MiB" in result["message"]
        assert result["segment_count"] == 1
        assert not monitor.active and monitor.reserved_by is None and not ui.external_busy
        assert all(child.returncode is not None for child in children)
    asyncio.run(scenario())


def test_expired_memory_is_missing_and_cpu_memory_mode_skips_foreground_probe(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.monitor import RuntimeSession
    async def unexpected_probe(*_):
        raise AssertionError("CPU/memory-only mode must not dump the foreground activity")
    monkeypatch.setattr("app.monitor.get_foreground_package", unexpected_probe)
    async def scenario():
        monitor = MonitorManager(tmp_path / "data")
        request = StartSessionRequest(serial="test", interval_ms=1000, metrics=MetricsConfig(cpu=False, memory=True, fps=False), memory_cycle_skip=60)
        runtime = RuntimeSession("test", request, SimpleNamespace(add_event=lambda *_: None), sample_cycle=1)
        runtime.memory_result = (100, 150, 1000, [])
        runtime.memory_updated_monotonic = time.monotonic() - 200
        assert (await monitor._capture_once(runtime)).pss_kb is None
        runtime.memory_updated_monotonic = time.monotonic()
        assert (await monitor._capture_once(runtime)).pss_kb == 100
    asyncio.run(scenario())
