import asyncio
import json
import struct
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
import pytest

from app import interaction, main, ui_stream


def test_cancelled_interaction_reaps_adb(tmp_path, monkeypatch):
    async def scenario():
        communicating = asyncio.Event()

        async def communicate():
            communicating.set()
            await asyncio.Event().wait()

        process = SimpleNamespace(communicate=communicate, returncode=None, kill=Mock())
        monkeypatch.setattr(interaction.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        manager = interaction.InteractionTraceManager(tmp_path)
        task = asyncio.create_task(manager._adb("fixture", "shell", "perfetto"))
        await asyncio.wait_for(communicating.wait(), 1)
        task.cancel()
        # Let the cleanup's second communicate() finish after kill.
        async def reaped():
            assert process.kill.called
            return b"", b""
        process.communicate = AsyncMock(side_effect=reaped)
        with pytest.raises(asyncio.CancelledError):
            await task
        process.kill.assert_called_once()
        process.communicate.assert_awaited_once()

    asyncio.run(scenario())


def test_close_cancels_immediately_queued_diagnostic(tmp_path, monkeypatch):
    async def scenario():
        manager = interaction.InteractionTraceManager(tmp_path)
        capture = AsyncMock()
        monkeypatch.setattr(manager, "_adb", capture)
        ident = await manager.start("fixture", 5)
        await manager.close()
        assert not manager.active
        assert manager.get(ident)["state"] == "interrupted"
        recorded, = json.loads((manager.data_dir / "manifest.json").read_text())
        assert recorded["interaction_id"] == ident and recorded["state"] == "interrupted"
        capture.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "config", "empty", "cancel"])
def test_perfetto_uses_stdin_and_cleans_trace_files(tmp_path, monkeypatch, outcome):
    async def scenario():
        manager = interaction.InteractionTraceManager(tmp_path)
        calls = []
        recording = asyncio.Event()

        async def adb(serial, *args, timeout_seconds, input_data=None):
            assert serial == "fixture"
            calls.append(args)
            if args[:3] == ("shell", "-T", "perfetto"):
                assert args[3:7] == ("--txt", "-c", "-", "-o")
                assert args[-1].startswith("/data/misc/perfetto-traces/interaction_")
                assert b"duration_ms: 5000" in input_data
                recording.set()
                if outcome == "config":
                    raise interaction.InteractionError("test config rejected")
                if outcome == "cancel":
                    await asyncio.Event().wait()
            elif args[0] == "pull":
                Path(args[-1]).write_bytes(b"" if outcome == "empty" else b"trace-packet")
            return ""

        monkeypatch.setattr(manager, "_adb", adb)
        ident = await manager.start("fixture", 5)
        task = manager.active[ident].task
        if outcome == "cancel":
            await recording.wait()
            await manager.close()
        else:
            await task
        assert calls[-1] == ("shell", "rm", "-f", calls[0][-1])
        assert not list(manager.data_dir.glob("*.part"))
        assert not list(manager.data_dir.glob("*.pbtxt"))
        result = manager.get(ident)
        assert result["state"] == {"success": "completed", "cancel": "interrupted"}.get(outcome, "failed")
        assert result["trace_ready"] == (outcome == "success")
        assert not manager.active

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["interactions", "monkey", "monitor", "ui", "can"])
def test_failed_shutdown_still_closes_remaining_managers(tmp_path, monkeypatch, failure):
    calls = []

    def can_close():
        calls.append("can")
        if failure == "can":
            raise RuntimeError("fixture manager close failure")

    def close(name):
        async def record():
            calls.append(name)
            if failure == name:
                raise RuntimeError("fixture manager close failure")
        return record

    monkeypatch.setattr(main, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(main, "CanService", lambda: SimpleNamespace(close=can_close))
    monkeypatch.setattr(main, "MonitorManager", lambda _: SimpleNamespace(
        store=SimpleNamespace(recover_interrupted_sessions=Mock()), close=close("monitor"),
    ))
    monkeypatch.setattr(main, "UIManager", lambda *_: SimpleNamespace(close=close("ui")))
    monkeypatch.setattr(main, "MonkeyManager", lambda *_: SimpleNamespace(close=close("monkey"), recover=AsyncMock()))
    monkeypatch.setattr(main, "InteractionTraceManager", lambda _: SimpleNamespace(close=close("interactions")))

    async def scenario():
        with pytest.raises(RuntimeError, match="fixture manager close failure"):
            async with main.lifespan(FastAPI()):
                pass

    asyncio.run(scenario())
    assert set(calls) == {"interactions", "monkey", "monitor", "ui", "can"}
    assert len(calls) == 5
    assert calls.index("monkey") < calls.index("monitor")
    assert calls.index("ui") < calls.index("can")


@pytest.mark.parametrize("phase", ["timeout", "disconnect", "ready"])
def test_scrcpy_handshake_releases_device(tmp_path, monkeypatch, phase):
    async def scenario():
        header_wait = asyncio.Event()
        received_metadata = False

        async def video_read(count):
            nonlocal received_metadata
            if count in (1, 64):
                return bytes(count)
            assert count == 12
            header_wait.set()
            if phase == "disconnect":
                raise asyncio.IncompleteReadError(partial=b"short", expected=12)
            if phase == "ready" and not received_metadata:
                received_metadata = True
                return struct.pack(">III", 0x68323634, 800, 600)
            await asyncio.Event().wait()

        async def device_command(*args):
            return b"54321\n" if args[:2] == ("forward", "tcp:0") else b""

        device = SimpleNamespace(command=AsyncMock(side_effect=device_command))
        process = SimpleNamespace(stdout=SimpleNamespace(read=AsyncMock(return_value=b"")),
                                  returncode=None, terminate=Mock(), wait=AsyncMock())
        lock = asyncio.Lock()
        ui = SimpleNamespace(device_lock=lambda _: lock, has_active_device=lambda _: False)
        ws = SimpleNamespace(headers={"origin": "http://127.0.0.1:8090"},
                             app=SimpleNamespace(state=SimpleNamespace(ui=ui, ui_drafts={})),
                             accept=AsyncMock(), receive_json=AsyncMock(side_effect=[{}, {"type": "stop"}]),
                             send_json=AsyncMock(), close=AsyncMock())
        writers = [Mock(), Mock()]
        server = tmp_path / "scrcpy"
        server.touch()
        monkeypatch.setattr(ui_stream, "SERVER", server)
        monkeypatch.setattr(ui_stream, "Device", lambda _: device)
        monkeypatch.setattr(ui_stream, "HANDSHAKE_TIMEOUT", 0.02, raising=False)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        monkeypatch.setattr(asyncio, "open_connection", AsyncMock(side_effect=[
            (SimpleNamespace(readexactly=video_read), writers[0]), (Mock(), writers[1]),
        ]))
        task = asyncio.create_task(ui_stream.stream(ws, "fixture"))
        await asyncio.wait_for(header_wait.wait(), 1)
        await asyncio.wait_for(task, 0.5)
        assert not lock.locked()
        ws.close.assert_awaited_once()
        process.terminate.assert_called_once()
        assert all(writer.close.called for writer in writers)
        assert any(call.args == ("forward", "--remove", "tcp:54321") for call in device.command.call_args_list)
        assert any(call.args[:3] == ("shell", "rm", "-f") for call in device.command.call_args_list)
        payload = ws.send_json.call_args.args[0]
        if phase == "ready":
            assert payload["ready"] and payload["width"] == 800 and payload["height"] == 600
        else:
            assert "error" in payload

    asyncio.run(scenario())
