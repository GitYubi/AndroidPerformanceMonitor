# UI 地图回放与父页删除

从仓库根运行。复现使用合成控件树和临时地图 SQLite，模拟点击只记录参数。

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app import ui_map as maps


def observation(key, checkable=False, destination=False):
    value = "Destination" if destination else "Settings"
    xml = (f'<hierarchy rotation="0"><node package="demo" class="android.widget.Button" '
           f'resource-id="demo:id/entry" text="{value}" checkable="{str(checkable).lower()}" '
           'clickable="true" enabled="true" bounds="[0,0][20,20]"/></hierarchy>')
    return {**maps.parse_tree(xml), "id": key, "serial": "fake", "width": 100, "height": 100}


async def replay(root):
    db = maps.MapStore(root)
    original = observation("source-before")
    changed = observation("source-after", checkable=True)
    target = observation("destination", destination=True)
    assert maps.identity(original) == maps.identity(changed)
    graph = db.graph("fake")
    graph["pages"] = [
        {"id": "parent", "name": "Parent", "signature": original["signature"], "identity": maps.identity(original)},
        {"id": "child", "name": "Child", "signature": target["signature"], "identity": maps.identity(target),
         "parentPageId": "parent", "parentEdgeId": "link", "navigationStatus": "recorded"},
    ]
    graph["edges"].append({"id": "link", "from": "parent", "to": "child", "status": "verified",
                          "locator": maps.locator(original, original["nodes"][0])})
    db.put("fake", graph)
    lock = asyncio.Lock()
    ui = SimpleNamespace(device_lock=lambda _: lock, has_active_device=lambda _: False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ui=ui, ui_map=db)))
    after = observation("after-tap", checkable=True)
    fake = SimpleNamespace(observe=AsyncMock(side_effect=[changed, after]), tap=AsyncMock())
    with patch.object(maps, "Device", return_value=fake):
        try:
            await maps.execute(maps.ExecuteRequest(serial="fake", targetId="child", requestId="one"), request)
        except HTTPException as exc:
            assert exc.status_code == 409 and "路径失效" in exc.detail
        else:
            raise AssertionError("the toggle stayed on the source page")
    assert fake.tap.await_count == 1 and fake.tap.call_args.args[0]["checkable"]
    print("A47: existing navigation edge replay clicks the now-checkable control before rejecting the destination")

    graph = await maps.edit(maps.EditRequest(serial="fake", id="parent", action="delete"), request)
    children = [page for page in graph["pages"] if page["id"] == "child"]
    assert children[0]["parentPageId"] == "parent"
    assert children[0]["parentEdgeId"] == "link" and not graph["edges"]
    graph = await maps.edit(maps.EditRequest(serial="fake", id="parent", action="restore"), request)
    assert {"parent", "child"} == {page["id"] for page in graph["pages"]}
    print("A48: removing a parent leaves a dangling child reference; restoring it repairs this example")


with TemporaryDirectory() as directory:
    asyncio.run(replay(Path(directory)))
PY
```
