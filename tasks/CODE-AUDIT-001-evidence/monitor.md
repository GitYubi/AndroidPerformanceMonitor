# 性能监测与日志导出离线审计复现

2026-11-16，在 macOS arm64 上使用仓库现有 Python 3.12 环境。从仓库根运行下列命令。ADB 返回由内存替身提供，所有目录和 SQLite 会话位于临时目录。超时测试仅加速用户停止的 60 秒等待，保留原采样调度、实际停止入口与会话存储。

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import device_logs, fps_probe, frame_sources, monitor
from app.adb import AdbError, parse_framestats
from app.models import MetricsConfig, SamplePayload, StartSessionRequest


async def log_export():
    calls = []
    events = []
    config = device_logs.DeviceLogConfig(anr="/data/anr", crash="/data/crash")
    fixed_time = "2026_11_16-00_00_00"

    async def adb(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("shell", "ls"):
            return "saved.txt\nfailed.txt\n" if args[-1] == config.anr else ""
        if args[0] == "pull" and args[1].endswith("/failed.txt"):
            raise AdbError("synthetic pull failure")
        return ""

    with TemporaryDirectory() as directory, patch.object(device_logs, "run_adb", adb):
        root = Path(directory)
        with patch.object(device_logs.time, "strftime", return_value=fixed_time):
            async def export(preserve=False):
                return await device_logs.export_and_clean_device_logs(
                    "fake", config, root, lambda *args: events.append(args), preserve=preserve
                )

            result = await export()
            assert result["ANR"]["exported"] == 1 and result["ANR"]["failed"] == 1
            assert result["ANR"]["cleaned"] and result["Crash"]["status"] == "empty"
            assert ("shell", "rm", "-f", "/data/anr/*") in calls
            print("A17: exported=1 failed=1 cleaned=True")

            calls.clear()
            assert not (await export(preserve=True))["ANR"]["cleaned"]
            assert not any(command[:2] == ("shell", "rm") for command in calls)

            target = Path(result["ANR"]["target"])
            assert not list(target.iterdir())
            target.rmdir()
            target.write_text("path conflict", encoding="utf-8")
            calls.clear()
            try:
                await export()
            except FileExistsError:
                pass
            else:
                raise AssertionError("expected the ANR destination to fail")
            assert not any(command[-1] == config.crash for command in calls)
            print("A27: destination creation failure skips the next log kind")


async def stop_during_export():
    reached_export = asyncio.Event()

    async def stuck_export(runtime):
        reached_export.set()
        await asyncio.Event().wait()

    original_wait_for = asyncio.wait_for

    async def shorter_stop_timeout(awaitable, timeout):
        if timeout == 60:
            await original_wait_for(reached_export.wait(), 1)
            timeout = 0.01
        return await original_wait_for(awaitable, timeout)

    with TemporaryDirectory() as directory:
        manager = monitor.MonitorManager(Path(directory))
        request = StartSessionRequest(
            serial="fake", interval_ms=500, anr_path="/data/anr",
            metrics=MetricsConfig(cpu=True, memory=False, fps=False)
        )
        with (
            patch.object(monitor, "list_devices", AsyncMock(return_value=[SimpleNamespace(serial="fake", state="device")])),
            patch.object(monitor, "get_sdk_version", AsyncMock(return_value=30)),
            patch.object(manager, "_capture_once", AsyncMock(return_value=SamplePayload(ts_ms=1))),
            patch.object(manager, "_export_device_logs", stuck_export),
            patch.object(monitor.asyncio, "wait_for", shorter_stop_timeout),
        ):
            session_id = await manager.start(request)
            runtime = manager.active[session_id]
            try:
                await manager.stop(session_id)
                session = manager.store.get_session(session_id)
                assert reached_export.is_set() and runtime.task.cancelled()
                assert session["state"] == "running" and session_id in manager.active
                try:
                    await manager.start(request)
                except ValueError as exc:
                    assert "已有测试会话" in str(exc)
                else:
                    raise AssertionError("expected the stale active session to block restart")
                print("A25: stop returned, persisted=running, active=True, next start rejected")
            finally:
                runtime.writer.connection.close()


async def switch_package_during_cooldown():
    keys = (
        "Flags,IntendedVsync,Vsync,OldestInputEvent,NewestInputEvent,"
        "HandleInputStart,AnimationStart,PerformTraversalsStart,DrawStart,SyncQueued,"
        "SyncStart,IssueDrawCommandsStart,SwapBuffers,FrameCompleted"
    )
    rows = []
    for index in range(4):
        ns = 8_000_000_000 + index * 16_666_666
        rows.append(",".join(map(str, [0, ns, ns + 1_000_000, *([0] * 10), ns + 11_000_000])))
    valid = "\n".join((keys, *rows))
    assert parse_framestats(valid).frame_count == 4
    calls = []

    async def adb(*args, **kwargs):
        calls.append(args)
        return valid if args[-2] == "active.app" else ""

    cooldown = {}
    with patch.object(frame_sources, "run_adb", adb):
        assert await frame_sources.capture_frame_metrics("fake", "idle.app", 30, cooldown) is None
        assert cooldown == {"framestats": 60}
        assert await frame_sources.capture_frame_metrics("fake", "active.app", 30, cooldown) is None
        assert [args[-2] for args in calls] == ["idle.app"]
        assert cooldown == {"framestats": 59}
        active = await frame_sources.capture_frame_metrics("fake", "active.app", 30)
        assert active.frame_count == 4 and active.package == "active.app"
        print("A26: 4 valid frames skipped across package change, cooldown=59")


async def probe_two_devices():
    async def adb(*args, serial=None, **kwargs):
        count = {"one": 100, "two": 200}[serial]
        return f"Total frames rendered: {count}\nJanky frames: 0 (0%)\n"

    with (
        patch.object(fps_probe, "_probe_state", {}),
        patch.object(fps_probe, "get_foreground_package", AsyncMock(return_value="same.app")),
        patch.object(fps_probe, "get_sdk_version", AsyncMock(return_value=30)),
        patch.object(fps_probe, "run_adb", adb),
        patch.object(fps_probe, "_probe_present", AsyncMock(return_value=(False, "", False, ""))),
    ):
        first = await fps_probe.probe_status("one")
        second = await fps_probe.probe_status("two")
        assert not first["r"] and second["r"] and second["r_detail"] == "计数增量 100"
        print("A19: first probe on the second device misreports a delta of 100")


async def main():
    await log_export()
    await stop_during_export()
    await switch_package_during_cooldown()
    await probe_two_devices()


asyncio.run(main())
PY
```
