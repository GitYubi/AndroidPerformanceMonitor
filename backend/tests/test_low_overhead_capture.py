import asyncio
import time
from types import SimpleNamespace

from app.adb import AdbError, parse_proc_stat_cpu
from app.models import MetricsConfig, StartSessionRequest
from app.monitor import MonitorManager, RuntimeSession
import app.monitor as monitor_module


def make_runtime():
    return RuntimeSession("test", StartSessionRequest(serial="test", metrics=MetricsConfig()),
                          SimpleNamespace(add_event=lambda *args: None), foreground_package="example.app")


def test_proc_stat_counts_iowait_as_idle_without_double_counting_guest():
    assert parse_proc_stat_cpu("cpu 100 20 30 400 50 6 7 8 90 10\n") == (621, 450)
    assert parse_proc_stat_cpu("cpu broken\n") is None


def test_cpu_uses_deltas_and_falls_back_when_proc_is_unavailable(tmp_path, monkeypatch):
    async def scenario():
        manager = MonitorManager(tmp_path)
        runtime = make_runtime()
        calls = []
        stats = iter(["cpu 10 0 10 80 0 0 0 0\n", "cpu 20 0 20 160 0 0 0 0\n"])
        async def adb(*args, **kwargs):
            calls.append(args)
            if args[-1] == "/proc/stat":
                return next(stats)
            if args[-1] == "cpuinfo":
                return "  2.8% 222/system_server: 2.1% user + 0.6% kernel"
            raise AssertionError(args)
        monkeypatch.setattr(monitor_module, "run_adb", adb)
        first, processes = await manager._capture_cpu(runtime)
        second, _ = await manager._capture_cpu(runtime)
        assert first is None and second == 20.0
        assert processes[0].process_name == "system_server"
        assert not any("top" in call for call in calls)

        async def denied(*args, **kwargs):
            calls.append(args)
            if args[-1] == "/proc/stat":
                raise AdbError("permission denied")
            if "top" in args:
                return "400%cpu  40%user  0%nice  0%sys  360%idle\n"
            return ""
        monkeypatch.setattr(monitor_module, "run_adb", denied)
        runtime = make_runtime()
        assert (await manager._capture_cpu(runtime))[0] == 10.0
        assert runtime.cpu_proc_supported is False
        calls.clear()
        await manager._capture_cpu(runtime)
        assert len(calls) == 1 and "top" in calls[0]
    asyncio.run(scenario())


def test_render_reuses_framestats_summary_and_falls_back(tmp_path, monkeypatch):
    async def scenario():
        manager = MonitorManager(tmp_path)
        runtime = make_runtime()
        runtime.render_baselines["example.app"] = (100, 10, time.monotonic() - 1)
        calls = []
        async def adb(*args, **kwargs):
            calls.append(args)
            return "Total frames rendered: 120\nJanky frames: 12 (10%)\n"
        monkeypatch.setattr(monitor_module, "run_adb", adb)
        task = asyncio.create_task(asyncio.sleep(0))
        value, jank = await manager._capture_app_render(runtime, task, ["Total frames rendered: 120\nJanky frames: 12 (10%)\n"])
        assert value is not None and jank == 10 and not calls
        runtime.render_baselines["example.app"] = (100, 10, time.monotonic() - 1)
        task = asyncio.create_task(asyncio.sleep(0))
        value, jank = await manager._capture_app_render(runtime, task, ["no summary"])
        assert value is not None and jank == 10 and len(calls) == 1
    asyncio.run(scenario())
