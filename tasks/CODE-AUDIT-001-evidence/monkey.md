# Monkey 终止与重启归档证据

仓库根运行。两项使用临时数据库和进程替身，既不启动设备端 Monkey，也不调用 CAN。

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import sqlite3

from app.models import SamplePayload
from app.monitor import MonitorManager
from app.monkey import MonkeyManager, MonkeyRequest, Runtime


def setup(root):
    monitor = MonitorManager(root / "data")
    ui = SimpleNamespace(external_busy={})
    manager = MonkeyManager(root / "monkey", monitor, ui)
    config = MonkeyRequest(serial="fake", mode="none", duration_hours=None)
    runtime = Runtime("a" * 32, config)
    run = {
        "id": runtime.id, "created_at_ms": 1000, "state": "starting",
        "serial": config.serial, "config": config.model_dump(), "current_session_id": None,
        "pending_session_id": None, "segment_count": 0,
    }
    manager.store.put(run)
    manager.store.initialize(run)
    return manager, monitor, ui, runtime, run


async def failed_exit_write(root):
    manager, monitor, ui, runtime, run = setup(root)
    runtime.process = SimpleNamespace(wait=AsyncMock(return_value=0))
    manager.active[runtime.id] = runtime
    monitor.reserved_by = runtime.id
    ui.external_busy[runtime.config.serial] = runtime.id
    put = manager.store.put

    def put_with_failure(value):
        if value["state"] == "stopping":
            raise sqlite3.OperationalError("synthetic disk write failure")
        put(value)

    with (
        patch.object(manager, "launch", AsyncMock()),
        patch.object(manager, "heartbeat", AsyncMock()),
        patch.object(manager, "start_segment", AsyncMock(side_effect=RuntimeError("sampling failed"))),
        patch.object(manager, "cleanup", AsyncMock()) as cleanup,
        patch.object(manager.store, "put", side_effect=put_with_failure),
    ):
        try:
            await manager.execute(runtime)
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("expected index write failure")
        cleanup.assert_not_awaited()
    assert runtime.cleanup_task is None and runtime.stop.is_set()
    assert manager.active[runtime.id] is runtime and monitor.reserved_by == runtime.id
    assert ui.external_busy[runtime.config.serial] == runtime.id
    assert manager.store.get(runtime.id)["state"] == "running"
    print("A42: writing the stopping state fails; cleanup and ownership release are skipped")


async def failed_recovery(root):
    manager, monitor, _, runtime, run = setup(root)
    session_id = "first-segment"
    writer = monitor.store.create_session(session_id, {
        "session_id": session_id, "serial": run["serial"], "created_at_ms": 1000,
        "duration_seconds": 60, "interval_ms": 1000, "enabled_metrics": {"cpu": True},
    })
    writer.write_sample(SamplePayload(ts_ms=1000, cpu_total_pct=12))
    writer.finish("interrupted")
    run.update(state="running", current_session_id=session_id)
    manager.store.put(run)
    with (
        patch.object(manager, "cleanup", AsyncMock()),
        patch.object(manager.store, "archive", side_effect=sqlite3.OperationalError("synthetic temporary failure")),
    ):
        await manager.recover()
    saved = manager.store.get(runtime.id)
    assert saved["state"] == "interrupted" and saved["current_session_id"] is None
    assert saved["segment_count"] == 0 and "部分归档待恢复" in saved["cleanup_warning"]
    assert monitor.store.database_path(session_id).is_file()
    with (
        patch.object(manager, "cleanup", AsyncMock()),
        patch.object(manager.store, "archive", wraps=manager.store.archive) as archive,
    ):
        await manager.recover()
        archive.assert_not_called()
    assert manager.store.segments(runtime.id) == []
    print("A43: original segment survives but the next recovery cannot find it")


async def checks(root):
    await failed_exit_write(root / "exit")
    await failed_recovery(root / "recover")


with TemporaryDirectory() as directory:
    asyncio.run(checks(Path(directory)))
PY
```
