# 会话存储、报告及服务生命周期审计复现

在仓库根执行。使用临时目录、SQLite、HTTP 处理函数和替身。本组命令不启动 HTTP 服务、不执行报告内脚本，不访问真实日志或设备。

```sh
PYTHONPATH=backend ZCAN_BACKEND=simulation backend/.venv/bin/python -B - <<'PY'
import asyncio
from contextlib import closing
import csv
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import main, report
from app.models import ProcessSample, SamplePayload
from app.storage import SessionStore


def make_session(store, session_id, points=1):
    writer = store.create_session(session_id, {
        "session_id": session_id, "serial": "synthetic-device", "created_at_ms": 1000,
        "duration_seconds": 60, "interval_ms": 500, "enabled_metrics": {"cpu": True},
    })
    for index in range(points):
        writer.write_sample(SamplePayload(
            ts_ms=index, cpu_total_pct=20, processes=[ProcessSample("sample.app", 12, cpu_pct=5)],
        ))
    writer.finish("completed")
    return store.database_path(session_id)


class MarkupProbe(HTMLParser):
    def __init__(self):
        super().__init__()
        self.script_ids = []

    def handle_starttag(self, tag, attributes):
        if tag == "script":
            self.script_ids.append(dict(attributes).get("id"))


async def report_and_storage(root):
    source_store = SessionStore(root / "source")
    source = make_session(source_store, "external-sample", points=1005)
    rows = list(csv.DictReader(io.StringIO(source_store.to_csv("external-sample"))))
    assert len(rows) == 1000 and rows[0]["ts_ms"] == "5" and rows[-1]["ts_ms"] == "1004"
    assert len(source_store.get_all_samples("external-sample")) == 1005
    assert report._svg_line_chart("gap", [("s", [1, None, 3], "black")], "x").count("<polyline") == 1
    print("A01/A02: CSV 1000/1005 rows; SVG creates a single polyline across the missing sample")

    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("UPDATE process_sample SET pid=?", ('<script id="audit-pid"></script>',))
        connection.execute("UPDATE session SET summary_json=?", (
            json.dumps({"sample_count": '<script id="audit-summary"></script>'}),))
    store = SessionStore(root / "imported")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(manager=SimpleNamespace(store=store))))
    imported_id = store.import_database(source.read_bytes())
    response = await main.session_report(imported_id, request, download=0, metrics=["cpu"])
    parsed = MarkupProbe()
    parsed.feed(response.body.decode())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert {"audit-pid", "audit-summary"} <= set(parsed.script_ids)
    assert "content-security-policy" not in response.headers
    print("A34: imported arbitrary text produces unescaped script tags in the HTML API response")

    source = make_session(source_store, "legacy-session")
    payload = source.read_bytes()
    path = source.parent
    (path / "metadata.json").unlink()
    path.rename(source_store.data_root / "legacy-session")
    assert source_store.get_session("legacy-session") is not None
    result = source_store.import_database(payload, "a" * 32, "different project")
    assert result == "legacy-session"
    assert source_store.get_session(result)["project_id"] == "default"
    assert not source_store.list_sessions(project_id="a" * 32)
    print("A37: imported UUID shadows against the direct legacy path and disappears from its new project")


async def broken_session_at_startup(root):
    store = SessionStore(root / "startup")
    make_session(store, "healthy")
    broken = store.data_root / "broken"
    broken.mkdir()
    (broken / "monitor.db").write_bytes(b"synthetic invalid SQLite file")
    try:
        store.list_sessions(limit=None)
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("expected the bad database to break the complete history listing")
    fake_app = SimpleNamespace(state=SimpleNamespace())
    with patch.object(main, "DATA_ROOT", store.data_root), patch.object(main, "CanService"):
        try:
            async with main.lifespan(fake_app):
                raise AssertionError("unexpected successful startup")
        except sqlite3.DatabaseError:
            pass
    print("A36: one invalid monitor.db aborts listing and the actual application lifespan startup")


async def shutdown_with_can_failure(root):
    fake_app = SimpleNamespace(state=SimpleNamespace())
    can = SimpleNamespace(close=Mock(side_effect=RuntimeError("synthetic close failure")))
    monitor = SimpleNamespace(store=SimpleNamespace(recover_interrupted_sessions=Mock()), close=AsyncMock())
    ui = SimpleNamespace(close=AsyncMock())
    monkey = SimpleNamespace(recover=AsyncMock(), close=AsyncMock())
    with (
        patch.object(main, "DATA_ROOT", root / "shutdown"),
        patch.object(main, "CanService", return_value=can),
        patch.object(main, "MonitorManager", return_value=monitor),
        patch.object(main, "InteractionTraceManager"),
        patch.object(main, "UIManager", return_value=ui),
        patch.object(main, "MapStore"),
        patch.object(main, "MonkeyManager", return_value=monkey),
    ):
        try:
            async with main.lifespan(fake_app):
                assert fake_app.state.can is can
        except RuntimeError as exc:
            assert "synthetic close failure" in str(exc)
        else:
            raise AssertionError("expected CAN shutdown error")
    for manager in (monkey, monitor, ui):
        manager.close.assert_not_awaited()
    print("A35: CAN shutdown exception skips Monkey, performance and UI shutdown")


with TemporaryDirectory() as directory, patch.dict(os.environ, {"ZCAN_DATA_DIR": directory}):
    root = Path(directory)
    asyncio.run(report_and_storage(root))
    asyncio.run(broken_session_at_startup(root))
    asyncio.run(shutdown_with_can_failure(root))
PY
```
