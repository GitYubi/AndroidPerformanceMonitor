# ADB 子进程和 scrcpy 协议生命周期替身

在仓库根运行。仅传入进程、套接字、WebSocket 替身，用来区分容许的关闭条件和取消路径。无真实设备动作。

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import interaction, ui_stream


async def trace_cancellation():
    communicating = asyncio.Event()

    async def blocked():
        communicating.set()
        await asyncio.Event().wait()

    process = SimpleNamespace(communicate=blocked, returncode=None, kill=Mock(), wait=AsyncMock())
    with TemporaryDirectory() as root, patch.object(
        interaction.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    ):
        manager = interaction.InteractionTraceManager(Path(root))
        task = asyncio.create_task(manager._adb("fake", "shell", "perfetto"))
        await asyncio.wait_for(communicating.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        process.kill.assert_not_called()
        process.wait.assert_not_awaited()
    print("A11: cancellation leaves the in-flight fake ADB subprocess unreaped")


async def stream_handshake():
    header_wait = asyncio.Event()

    async def staged_video(count):
        if count in (1, 64):
            return bytes(count)
        assert count == 12
        header_wait.set()
        await asyncio.Event().wait()

    async def adb(*args):
        return b"54321\n" if args[:2] == ("forward", "tcp:0") else b""

    device = SimpleNamespace(command=AsyncMock(side_effect=adb))
    proc = SimpleNamespace(stdout=SimpleNamespace(read=AsyncMock(return_value=b"")),
                           returncode=None, terminate=Mock(), wait=AsyncMock(return_value=0))
    lock = asyncio.Lock()
    ui = SimpleNamespace(device_lock=lambda _: lock, has_active_device=lambda _: False)
    ws = SimpleNamespace(headers={"origin": "http://127.0.0.1:8090"},
                         app=SimpleNamespace(state=SimpleNamespace(ui=ui, ui_drafts={})),
                         accept=AsyncMock(), receive_json=AsyncMock(return_value={}),
                         send_json=AsyncMock(), close=AsyncMock())
    pipes = [(SimpleNamespace(readexactly=staged_video), Mock()), (Mock(), Mock())]
    with (
        patch.object(ui_stream, "Device", return_value=device),
        patch.object(ui_stream.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)),
        patch.object(ui_stream.asyncio, "open_connection", AsyncMock(side_effect=pipes)),
    ):
        task = asyncio.create_task(ui_stream.stream(ws, "fake"))
        try:
            await asyncio.wait_for(header_wait.wait(), 1)
            assert lock.locked() and not task.done()
            assert ws.receive_json.await_count == 1
            ws.close.assert_not_awaited()
            print("A41: partial scrcpy handshake holds the lock before the WebSocket input loop")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not lock.locked()


async def run():
    await trace_cancellation()
    await stream_handshake()


asyncio.run(run())
PY
```
