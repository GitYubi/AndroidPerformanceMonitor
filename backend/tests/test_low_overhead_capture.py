import asyncio
import shlex
import time
from types import SimpleNamespace

from app.models import SamplePayload
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


def test_surface_latency_preserves_layer_as_one_remote_argument(tmp_path, monkeypatch):
    commands = []

    async def adb(*args, **kwargs):
        commands.append(shlex.split(" ".join(args[1:])))
        return "16666666\n0 1000000000 0\n0 1050000000 0\n"

    monkeypatch.setattr(monitor_module, "run_adb", adb)
    manager = MonitorManager(tmp_path)
    layers = ["SurfaceView", "SurfaceView (app's layer) #2", "layer; printf S1_MARKER"]
    for layer in layers:
        request = StartSessionRequest(serial="fixture", surface_layer=layer)
        runtime = RuntimeSession("fixture", request, None)
        stats = asyncio.run(manager._capture_sf_latency_fallback(runtime))
        assert stats.layer == layer
    assert commands == [["dumpsys", "SurfaceFlinger", "--latency", layer] for layer in layers]


def test_monitor_can_restart_after_stop_times_out_exporting_logs(tmp_path, monkeypatch):
    async def scenario():
        manager = MonitorManager(tmp_path)
        reached_export = asyncio.Event()
        request = StartSessionRequest(serial="fixture")

        async def devices():
            return [SimpleNamespace(serial=request.serial, state="device")]

        async def sdk(_):
            return 30

        async def capture(_):
            return SamplePayload(ts_ms=1, cpu_total_pct=10)

        async def export(_):
            reached_export.set()
            await asyncio.Event().wait()

        wait_for = asyncio.wait_for

        async def faster_timeout(awaitable, timeout):
            if timeout == 60:
                await wait_for(reached_export.wait(), 1)
                timeout = 0.01
            return await wait_for(awaitable, timeout)

        monkeypatch.setattr(monitor_module, "list_devices", devices)
        monkeypatch.setattr(monitor_module, "get_sdk_version", sdk)
        monkeypatch.setattr(manager, "_capture_once", capture)
        monkeypatch.setattr(manager, "_export_device_logs", export)
        monkeypatch.setattr(asyncio, "wait_for", faster_timeout)
        session = await manager.start(request)
        runtime = manager.active[session]
        try:
            await manager.stop(session)
            assert manager.store.get_session(session)["state"] == "stopped"
            assert any(event["code"] == "log_export_cancelled" for event in manager.store.get_events(session))
            assert session not in manager.active
            next_session = await manager.start(request.model_copy(update={"anr_path": None}))
            await manager.stop(next_session)
            assert manager.store.get_session(next_session)["state"] == "stopped"
        finally:
            runtime.writer.connection.close()

    asyncio.run(scenario())


def test_fps_probe_keeps_independent_device_counters(monkeypatch):
    from unittest.mock import AsyncMock
    from app import fps_probe

    async def scenario():
        counts = {"first": iter([100, 108]), "second": iter([200, 204])}

        async def adb(*_, serial, **kwargs):
            return f"Total frames rendered: {next(counts[serial])}\nJanky frames: 0 (0%)"

        monkeypatch.setattr(fps_probe, "_probe_state", {})
        monkeypatch.setattr(fps_probe, "get_foreground_package", AsyncMock(return_value="same.app"))
        monkeypatch.setattr(fps_probe, "get_sdk_version", AsyncMock(return_value=30))
        monkeypatch.setattr(fps_probe, "_probe_present", AsyncMock(return_value=(False, "", False, "")))
        monkeypatch.setattr(fps_probe, "run_adb", adb)
        for name in counts:
            assert not (await fps_probe.probe_status(name))["r"]
        assert (await fps_probe.probe_status("first"))["r_detail"] == "计数增量 8"
        assert (await fps_probe.probe_status("second"))["r_detail"] == "计数增量 4"

    asyncio.run(scenario())


def test_framestats_cooldown_does_not_follow_to_next_app(monkeypatch):
    from app import frame_sources

    async def scenario():
        calls = []

        async def adb(*args, **kwargs):
            calls.append(args[-2])
            return args[-2]

        stats = monitor_module.FrameStats(source="framestats", frame_count=4)
        monkeypatch.setattr(frame_sources, "run_adb", adb)
        monkeypatch.setattr(frame_sources, "parse_framestats", lambda output: stats if output == "active.app" else None)
        cooldown = {}
        assert await frame_sources.capture_frame_metrics("fixture", "idle.app", 30, cooldown) is None
        assert await frame_sources.capture_frame_metrics("fixture", "active.app", 30, cooldown) is stats
        assert await frame_sources.capture_frame_metrics("fixture", "idle.app", 30, cooldown) is None
        assert calls == ["idle.app", "active.app"]
        assert cooldown == {("framestats", "idle.app"): 59}

    asyncio.run(scenario())
