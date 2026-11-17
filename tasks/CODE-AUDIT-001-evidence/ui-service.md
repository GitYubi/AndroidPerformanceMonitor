# UI 执行与项目存储证据

从仓库根运行。使用临时 SQLite、合成 PNG、同步屏障和执行器替身，无外部模型、设备或 CAN 测试。

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
import asyncio
from io import BytesIO
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from app import ui_testing as ui
from app.models import SamplePayload
from app.storage import SessionStore


def png(color):
    output = BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format="PNG")
    return output.getvalue()


def perf_session(root, project_id="default", samples=()):
    store = SessionStore(root)
    writer = store.create_session("session", {
        "session_id": "session", "serial": "fake", "project_id": project_id,
        "created_at_ms": 1000, "duration_seconds": 60, "interval_ms": 1000,
        "enabled_metrics": {"cpu": True},
    })
    for stamp, cpu in samples:
        writer.write_sample(SamplePayload(ts_ms=stamp, cpu_total_pct=cpu))
    writer.finish("completed")
    return store


async def image_swap(root):
    manager = ui.UIManager(root)
    started = asyncio.Event()
    resume = asyncio.Event()
    original, replacement = png("blue"), png("red")
    before = manager.store.images.save("default", "same image", "roi", original)
    asset_key = f"default/{before['id']}"
    reads = []

    async def worker(_run_id, job, *_):
        path = Path(job["baselineFiles"][asset_key])
        reads.append(path.read_bytes())
        started.set()
        await resume.wait()
        reads.append(path.read_bytes())

    request = ui.RunRequest.model_validate({
        "serial": "fake", "testCase": {"name": "assert using PNG", "steps": [{
            "id": "shot", "type": "image_assert", "prompt": "find PNG",
            "options": {"baselineId": before["id"], "baselineProjectId": "default"},
        }]},
    })
    with (
        patch.object(ui, "list_devices", AsyncMock(return_value=[SimpleNamespace(serial="fake", state="device")])),
        patch.object(manager, "execute", side_effect=worker),
    ):
        run = await manager.start(request)
        await asyncio.wait_for(started.wait(), 1)
        async with manager.lock:
            after = await asyncio.to_thread(manager.store.images.save, "default", "same image", "roi", replacement)
        resume.set()
        await asyncio.wait_for(manager.tasks.pop(run["id"]), 1)
    assert before["id"] == after["id"] and reads == [original, replacement]
    print("A44: same baseline ID changes bytes during a scheduled run")


async def execution_cleanup(root):
    manager = ui.UIManager(root)
    run = await manager.start(ui.RunRequest.model_validate({
        "serial": "", "testCase": {"name": "delay", "steps": [{
            "id": "delay", "type": "delay", "prompt": "wait", "options": {"durationMs": 1},
        }]},
    }))
    run_id = run["id"]
    manager.cancelled.add(run_id)
    with patch.object(manager.store, "events", side_effect=sqlite3.OperationalError("synthetic read failure")):
        try:
            await manager.tasks[run_id]
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("expected event read to stop finalization")
    assert manager.has_active_device("") and manager.tasks[run_id].done()
    assert manager.store.get("runs", run_id)["state"] == "queued"
    print("A45: UI task finished with an exception but stays in the active registry")


def repeated_metrics(root):
    store = perf_session(root, samples=[(1000, 10), (2000, 95), (3000, 15)])
    run = {"performanceSessionId": "session", "testCase": {"steps": [{"id": "loop-step"}]}}
    events = [
        {"type": "step", "stepId": "loop-step", "state": state, "time": stamp}
        for state, stamp in [("running", 990), ("passed", 1010), ("running", 2990), ("passed", 3010)]
    ]
    point, = ui.step_metrics(run, events, store)
    assert point["sampleCount"] == 3 and point["cpuPeak"] == 95
    print("A46: repeated step includes a CPU peak sampled between iterations")


async def project_delete_cleanup(root):
    manager = ui.UIManager(root / "ui")
    project = manager.store.images.create_project("temporary")
    store = perf_session(root / "perf", project_id=project["id"])
    directory = store.database_path("session").parent
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        ui=manager, manager=SimpleNamespace(store=store, active={}),
    )))
    remove = shutil.rmtree

    def refuse_one(path, *args, **kwargs):
        if Path(path) == directory:
            raise PermissionError("synthetic delete failure")
        return remove(path, *args, **kwargs)

    with patch.object(ui.shutil, "rmtree", side_effect=refuse_one):
        result = await ui.delete_ui_project(project["id"], request)
    assert result["deleted"] and result["cleanupErrors"] == [str(directory)]
    assert project["id"] not in {row["id"] for row in manager.store.images.projects()}
    assert store.get_session("session")["project_id"] == project["id"]
    print("A16: project deletion commits, but a failed directory cleanup leaves a readable orphan session")


async def checks(root):
    await image_swap(root / "images")
    await execution_cleanup(root / "cleanup")
    repeated_metrics(root / "metrics")
    await project_delete_cleanup(root / "delete")


with TemporaryDirectory() as directory:
    asyncio.run(checks(Path(directory)))
PY
```
