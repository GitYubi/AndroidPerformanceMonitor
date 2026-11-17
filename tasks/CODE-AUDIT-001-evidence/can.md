# CAN 服务离线审计复现

在仓库根使用已有 Python 环境运行。仅使用 CAN 回环和内存替身，ZLG 数据目录在导入服务前指向临时目录。没有真实 CAN 设备或驱动调用。

```sh
PYTHONPATH=backend ZCAN_BACKEND=simulation backend/.venv/bin/python -B - <<'PY'
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from can import Message


with TemporaryDirectory() as directory, patch.dict("os.environ", {"ZCAN_DATA_DIR": directory}):
    from app.can_service import CanService, ROOT
    from can_adapters.hirain import HirainCanBackend
    from tools import can_bench

    service = CanService()
    service.open("simulation", str(ROOT / "config" / "Example.dbc"), [0])
    device = service.device
    key = (0, "TestButtons")
    try:
        service.send_msg_by_name(*key, {"PressA": 0}, can_fd=False, cycle_time=20)
        manual = device.thread_messages[key]
        assert manual["func"].is_alive()
        options = {"dbcKey": service.dbc_key, "channel": key[0], "message": key[1],
                   "signals": {"PressA": 1}, "cycleMs": 30}
        session = service.prepare_ui_run([("can_send_loop", options)])
        owned = {}
        service.execute_ui_step("can_send_loop", options, session, owned)
        assert owned[key] is not manual
        assert owned[key]["func"] is manual["func"]
        assert not service.stop_ui_sends(session, owned)
        assert key not in device.thread_messages
        print("A29: UI updates the manual loop; UI cleanup removes it")

        service.send_msg_by_name(*key, {"PressA": 0}, can_fd=False, cycle_time=20)
        manual = device.thread_messages[key]
        session = service.prepare_ui_run([("can_send", options)])
        assert "已发送 1 帧" in service.execute_ui_step("can_send", options, session, {})
        assert not manual["func"].is_alive()
        assert device.thread_messages[key]["func"].remaining == 0
        print("A29: UI one-shot replaces the already active manual loop")

        with patch.object(device._backend, "close", side_effect=RuntimeError("synthetic close failure")):
            try:
                service.close()
            except RuntimeError as exc:
                assert "synthetic close failure" in str(exc)
            else:
                raise AssertionError("expected a close failure")
            assert service.status()["open"] and service.stop.is_set()
            assert not service.worker.is_alive()
            print("A15: manual close failure leaves open=True with the receiver stopped")
    finally:
        service.close()

    class BusyBus:
        def __init__(self, arbitration_id):
            self.message = Message(arbitration_id=arbitration_id, is_extended_id=False)
            self.reads = 0

        def recv(self, timeout):
            self.reads += 1
            return self.message

    left, right = BusyBus(0x100), BusyBus(0x101)
    adapter = object.__new__(HirainCanBackend)
    adapter._buses = {0: left, 1: right}
    for _ in range(10):
        frames = adapter.poll(8)
        assert {frame.channel for frame in frames} == {0}
    assert left.reads == 80 and right.reads == 0
    print("A30: first busy channel uses the full budget, second channel read count=0")

    class ReachedOpen(Exception):
        pass

    with patch.object(can_bench, "open_backend", side_effect=ReachedOpen):
        for duration in (float("inf"), float("nan")):
            options = Namespace(confirm_safe=True, duration=duration, channel=0,
                                scenario="single10", base_id=0x100, listener_interface=None,
                                listener_channel=None, backend="simulation", device_id=None,
                                output=Path(directory) / "bench")
            try:
                can_bench.run(options)
            except ReachedOpen:
                pass
            else:
                raise AssertionError("expected a non-finite duration to reach backend open")
    print("A31: invalid inf and NaN durations reach the backend-open path")
PY
```

## 历史 CAN 台架验证步骤（本轮排除）

1. 确认物理隔离台架、适配器/驱动版本、通道波特率、终端及具体获准的 ID、帧格式、测试时长。保留独立监听器的时间戳来源和通道映射。现有 ZLG 文档中的旧版本通道 0 回显可用于对照。
2. 先实测只读发现和实际通道映射，再测试普通打开、关闭、拔线及重新连接。用独立监听确认关闭后无后续测试帧，同时保存 API 状态、设备日志、监听记录，核对 A15。
3. 使用获准的合成流量验证两个通道都有 RX 记录，并分别测试序号、录制及 A30 的高负载边界。A29 用同通道同报文和不同通道的对照，比较 UI 启停前后的手动发送状态和独立监听。
4. 对周期精度、更新、触发及 BLF，分别记录主机提交、设备回显和独立接收。量化的是对应运行版本、设备和观察窗口。

## Windows 通道配置与旧诊断入口替身

```sh
PYTHONPATH=backend ZCAN_BACKEND=simulation backend/.venv/bin/python -B - <<'PY'
import os
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from can import Message

with TemporaryDirectory() as directory, patch.dict(os.environ, {"ZCAN_DATA_DIR": directory}):
    from zlg.zlgcan import WindowsCanBackend, zlgCan
    from zlg.paths import ROOT

    sdk = Mock()
    sdk.OpenDevice.return_value = 42
    sdk.SetValue.side_effect = lambda _ptr, path, _value: 0 if path.endswith("canfd_abit_baud_rate") else 1
    sdk.InitCAN.return_value = 43
    sdk.ClearBuffer.return_value = 1
    sdk.StartCAN.return_value = 1
    sdk.CloseDevice.return_value = 1
    with patch("zlg.zlgcan.ZCAN", return_value=sdk):
        native = WindowsCanBackend(0)
        try:
            native.open_channel(0)
            assert native.channels == (0,)
            sdk.StartCAN.assert_called_once_with(43)
            print("A33: SDK rejects nominal baud rate; channel still starts")
        finally:
            native.close()

    device = zlgCan(0, str(ROOT / "config" / "Example.dbc"))
    try:
        device.chn_open(0)
        device._backend.supports_diagnostics = True
        frame = Message(channel=0, arbitration_id=0x7b3, is_extended_id=False, data=bytes(range(8)))
        with patch.object(device._backend, "poll", return_value=[frame]):
            try:
                device.diag_config_analysis_list(0)
            except TypeError as exc:
                assert "list indices" in str(exc)
            else:
                raise AssertionError("diagnostic parsing should reveal the current list/dict mismatch")
        with patch.object(device, "_send") as transmitted:
            try:
                device.diag_seng_configs(0, [0] * 200)
            except TypeError as exc:
                assert "missing 1 required positional argument" in str(exc)
            else:
                raise AssertionError("diagnostic sequencing calls diag_send with the old signature")
            transmitted.assert_not_called()
        print("A32: diagnostic read expects a dict and write invokes the old signature; no transmission")
    finally:
        device.close()
PY
```
