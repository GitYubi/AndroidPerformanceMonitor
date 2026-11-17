# Android 模拟器验证

用户于 2026-11-16 提供 Android 模拟器。验证固定使用 `emulator-5554`，检查在线清单和模拟器属性。设备仅进行状态读取、采样、控件树和截图观察，以及独立临时目录中的日志演示。保留统计和结果，截图与设备内容无需写入仓库。全程没有真实车机、CAN 或云模型调用。

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from app import device_logs
from app.adb import AdbError, get_sdk_version, list_devices, run_adb
from app.models import StartSessionRequest
from app.monitor import MonitorManager
from app.ui_map import Device


async def check_emulator(serial, host):
    devices = await list_devices()
    assert any(device.serial == serial and device.state == "device" for device in devices)
    assert (await run_adb("shell", "getprop", "ro.kernel.qemu", serial=serial)).strip() == "1"
    sdk = await get_sdk_version(serial)
    assert sdk is not None
    print(f"device={serial}, sdk={sdk}, emulator=True", flush=True)

    manager = MonitorManager(host / "performance")
    session_id = await manager.start(StartSessionRequest(
        serial=serial, duration_seconds=4, interval_ms=500, memory_cycle_skip=1,
    ))
    await asyncio.wait_for(asyncio.shield(manager.active[session_id].task), timeout=40)
    session = manager.store.get_session(session_id)
    samples = manager.store.get_all_samples(session_id)
    cpu = sum(sample["cpu_total_pct"] is not None for sample in samples)
    memory = sum(sample["memory_used_pct"] is not None for sample in samples)
    presented = sum(sample["fps"] is not None for sample in samples)
    assert session["state"] == "completed" and len(samples) > 1
    assert cpu and memory, (cpu, memory, manager.store.get_events(session_id))
    print(f"monitor: state={session['state']}, samples={len(samples)}, "
          f"cpu={cpu}, system_memory={memory}, presented_fps={presented}", flush=True)

    observation = await Device(serial).observe()
    assert observation["serial"] == serial and observation["nodes"]
    assert observation["width"] > 0 and observation["height"] > 0
    assert observation["image"].startswith("data:image/png;base64,")
    print(f"UI observation: nodes={len(observation['nodes'])}, "
          f"pixels={observation['width']}x{observation['height']}, rotation={observation['rotation']}", flush=True)

    remote = f"/data/local/tmp/codex-audit-{uuid4().hex}"
    await run_adb("shell", "mkdir", remote, serial=serial)
    try:
        await run_adb("shell", f"printf audit-good > {remote}/good.txt", serial=serial)
        await run_adb("shell", f"printf audit-failed > {remote}/failed.txt", serial=serial)

        async def inject_pull_failure(*arguments, **kwargs):
            if arguments[:2] == ("pull", f"{remote}/failed.txt"):
                raise AdbError("synthetic failure for one isolated fixture")
            return await run_adb(*arguments, **kwargs)

        config = device_logs.DeviceLogConfig(anr=remote)
        with patch.object(device_logs, "run_adb", inject_pull_failure):
            preserved = (await device_logs.export_and_clean_device_logs(
                serial, config, host / "preserved", lambda *_: None, preserve=True,
            ))["ANR"]
            before_clean = (await run_adb("shell", "ls", "-A", remote, serial=serial)).splitlines()
            assert sorted(before_clean) == ["failed.txt", "good.txt"]
            assert not preserved["cleaned"] and preserved["failed"] == 1
            cleaned = (await device_logs.export_and_clean_device_logs(
                serial, config, host / "cleaned", lambda *_: None,
            ))["ANR"]
            final_list = (await run_adb("shell", "ls", "-A", remote, serial=serial)).splitlines()
            assert cleaned["cleaned"] and cleaned["exported"] == cleaned["failed"] == 1
            assert not final_list and (Path(cleaned["target"]) / "good.txt").read_text() == "audit-good"
            assert not (Path(cleaned["target"]) / "failed.txt").exists()
        print("A17: real emulator ls/pull/shell; one pull intentionally fails; preserve retains both "
              "fixtures, normal mode removes both, including the failed one", flush=True)
    finally:
        leftovers = (await run_adb("shell", "ls", "-A", remote, serial=serial)).splitlines()
        for fixture in ("good.txt", "failed.txt"):
            if fixture in leftovers:
                await run_adb("shell", "rm", f"{remote}/{fixture}", serial=serial)
        await run_adb("shell", "rmdir", remote, serial=serial)


with TemporaryDirectory(prefix="android-code-audit-") as directory:
    asyncio.run(check_emulator("emulator-5554", Path(directory)))
PY
```

2026-11-16 实跑退出 0。实际环境为 `emulator-5554`、Android SDK 30、`ro.kernel.qemu=1`。会话自然结束并持久化 8 个采样点，其中 CPU 7、系统内存率 7、呈现帧率 8。UI 观察确认 24 个控件、1080×1920 PNG、旋转值 0。

A17 的部分拉取失败由夹具注入，成功拉取、保留和目录通配删除均经模拟器真实 ADB 执行。保留模式维持两个样本，默认清理在其中一个失败时仍删除两者；失败样本没有本地副本。用例退出时清除了本次唯一临时目录。

## SurfaceFlinger 设备端参数与 Perfetto 临时产物

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import monitor
from app.adb import AdbError, parse_surface_latency_stats, run_adb
from app.interaction import InteractionTraceManager
from app.models import StartSessionRequest


async def verify(serial, root):
    assert (await run_adb("shell", "getprop", "ro.kernel.qemu", serial=serial)).strip() == "1"
    seen = []
    marker = "AUDIT_LAYER_MARKER"

    async def capture(*arguments, **kwargs):
        output = await run_adb(*arguments, **kwargs)
        seen.append(output)
        return output

    request = StartSessionRequest(serial=serial, surface_layer=f"unused-layer; printf {marker}")
    runtime = monitor.RuntimeSession("audit", request, None, selected_surface_layer=request.surface_layer)
    manager = monitor.MonitorManager(root / "monitor")
    with patch.object(monitor, "run_adb", capture):
        await manager._capture_sf_latency_fallback(runtime)
    assert len(seen) == 1 and marker in seen[0]
    print("A38: a harmless printf in the layer argument executed in the emulator shell", flush=True)

    lines = ["16666666", "0 1000000000 0", "0 1050000000 0"]
    with_invalid = parse_surface_latency_stats("\n".join(lines + ["0 9223372036854775807 0"]))
    clean = parse_surface_latency_stats("\n".join(lines))
    assert (clean.frame_count, clean.jank_count, clean.jank_pct) == (2, 1, 50.0)
    assert (with_invalid.frame_count, with_invalid.jank_count, with_invalid.jank_pct) == (3, 1, 33.33)
    print("A39: adding one sentinel present timestamp changes Jank 50.0% -> 33.33%", flush=True)

    trace = InteractionTraceManager(root / "perfetto")
    interaction_id = await trace.start(serial, 5)
    token = interaction_id.replace("-", "")
    remote_paths = [f"/data/local/tmp/interaction_{token}.{extension}" for extension in ("pbtxt", "pftrace")]
    task = next(task for task in asyncio.all_tasks() if task.get_name() == f"interaction-{interaction_id}")
    try:
        await asyncio.wait_for(task, 60)
        state = trace.get(interaction_id)
        assert state["state"] == "failed" and not state["trace_ready"]
        assert "Could not open" in state["error"] and "Permission denied" in state["error"]
        assert remote_paths[0] in state["error"]
        await run_adb("shell", "test", "-f", remote_paths[0], serial=serial)
        print("A40: Perfetto could not open the pushed configuration (Permission denied); "
              "the configuration remained on the emulator after task failure", flush=True)
    finally:
        for path in remote_paths:
            try:
                await run_adb("shell", "test", "-f", path, serial=serial)
            except AdbError:
                continue
            await run_adb("shell", "rm", path, serial=serial)


with TemporaryDirectory(prefix="android-code-audit-") as directory:
    asyncio.run(verify("emulator-5554", Path(directory)))
PY
```
