"""会话存储层测试：目录命名、反查与导入。"""

from __future__ import annotations

import asyncio
import csv
import io
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI

from app import main
from app.models import SamplePayload
from app.storage import SessionStore


def _make_store() -> tuple[SessionStore, Path]:
    root = Path(tempfile.mkdtemp())
    return SessionStore(root), root


def _create_session(store: SessionStore, serial: str, session_id: str | None = None) -> str:
    session_id = session_id or str(uuid.uuid4())
    writer = store.create_session(
        session_id,
        {
            "session_id": session_id,
            "serial": serial,
            "created_at_ms": int(time.time() * 1000),
            "duration_seconds": 60,
            "interval_ms": 1000,
            "enabled_metrics": {"cpu": True, "memory": False, "fps": False},
            "surface_layer": None,
        },
    )
    writer.write_sample(SamplePayload(ts_ms=1, cpu_total_pct=10.0))
    writer.finish("stopped")
    return session_id


def test_session_dir_name_uses_device_and_date() -> None:
    store, _ = _make_store()
    session_id = _create_session(store, "42b86e9c")
    dirs = [p.name for p in store.data_root.iterdir() if p.is_dir()]
    assert len(dirs) == 1
    assert dirs[0].startswith("42b86e9c_20")  # 设备号_yyyy_MM_DD


def test_session_dir_name_handles_network_serial_and_conflict() -> None:
    store, _ = _make_store()
    _create_session(store, "127.0.0.1:5555")
    _create_session(store, "127.0.0.1:5555")  # 同日同设备第二个会话 → 序号
    names = sorted(p.name for p in store.data_root.iterdir() if p.is_dir())
    assert names[0].startswith("127.0.0.1_5555_20")
    assert names[1] == f"{names[0]}_2"


def test_database_path_resolves_by_session_id() -> None:
    store, root = _make_store()
    session_id = _create_session(store, "42b86e9c")
    # 新版目录名不等于 session_id，仍能反查
    path = store.database_path(session_id)
    assert path.exists()
    assert path.parent.name != session_id
    session = store.get_session(session_id)
    assert session is not None
    assert session["serial"] == "42b86e9c"
    assert store.get_series(session_id, limit=10)[0]["cpu_total_pct"] == 10.0


def test_get_all_samples_is_not_limited_to_live_window() -> None:
    store, _ = _make_store()
    session_id = str(uuid.uuid4())
    writer = store.create_session(
        session_id,
        {
            "session_id": session_id,
            "serial": "full-series-device",
            "created_at_ms": 1,
            "duration_seconds": 60,
            "interval_ms": 500,
            "enabled_metrics": {"cpu": True, "memory": False, "fps": False},
            "surface_layer": None,
        },
    )
    for index in range(1005):
        writer.write_sample(SamplePayload(ts_ms=index, cpu_total_pct=float(index)))
    writer.finish("completed")

    assert len(store.get_series(session_id, limit=1000)) == 1000
    all_samples = store.get_all_samples(session_id)
    assert len(all_samples) == 1005
    assert all_samples[0]["ts_ms"] == 0
    assert all_samples[-1]["ts_ms"] == 1004

    async def download():
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(manager=SimpleNamespace(store=store))))
        response = await main.export_session(session_id, request)
        if hasattr(response, "body_iterator"):
            return "".join([chunk async for chunk in response.body_iterator])
        return response.body.decode()

    rows = list(csv.DictReader(io.StringIO(asyncio.run(download()))))
    assert [int(row["ts_ms"]) for row in rows] == list(range(1005))
    assert rows[0]["cpu_total_pct"] == "0.0" and rows[-1]["cpu_total_pct"] == "1004.0"
    assert "memory_used_pct" in rows[0]


def test_bad_session_does_not_block_lifespan_or_history(tmp_path, monkeypatch, caplog):
    root = tmp_path / "data"
    store = SessionStore(root)
    good = _create_session(store, "fixture")
    bad = root / "bad" / "monitor.db"
    bad.parent.mkdir()
    invalid = b"not a SQLite database"
    bad.write_bytes(invalid)
    monkeypatch.setattr(main, "DATA_ROOT", root)
    monkeypatch.setattr(main, "CanService", lambda: SimpleNamespace(close=lambda: None))

    async def startup():
        app = FastAPI()
        async with main.lifespan(app):
            recorded = await main.list_sessions(SimpleNamespace(app=app))
            assert [session["session_id"] for session in recorded["sessions"]] == [good]
            assert app.state.manager.store.get_session(good)["state"] == "stopped"

    asyncio.run(startup())
    assert bad.read_bytes() == invalid
    assert str(bad) in caplog.text


def test_database_path_legacy_uuid_dir_still_works() -> None:
    store, _ = _make_store()
    session_id = "legacy-uuid-dir"
    writer = store.create_session(
        session_id,
        {
            "session_id": session_id,
            "serial": "abc123",
            "created_at_ms": 1,
            "duration_seconds": 60,
            "interval_ms": 1000,
            "enabled_metrics": {"cpu": True, "memory": False, "fps": False},
            "surface_layer": None,
        },
    )
    # 模拟旧版：目录名 = session_id
    writer.finish("completed")
    assert store.database_path(session_id).exists()
    assert store.get_session(session_id) is not None


def test_import_database_uses_device_date_dir_and_conflict_new_id() -> None:
    store, root = _make_store()
    session_id = _create_session(store, "42b86e9c")
    # 复制该会话 db 作为"外部文件"导入 → 同 session_id → 冲突 → 新 id + 序号目录
    source = store.database_path(session_id).read_bytes()
    imported_id = store.import_database(source)
    assert imported_id != session_id
    imported = store.get_session(imported_id)
    assert imported is not None
    dir_names = sorted(p.name for p in store.data_root.iterdir() if p.is_dir())
    assert len(dir_names) == 2
    assert dir_names[1] == f"{dir_names[0]}_2"


def test_list_sessions_includes_dir_name() -> None:
    store, _ = _make_store()
    _create_session(store, "42b86e9c")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["dir_name"].startswith("42b86e9c_20")
    assert sessions[0]["summary"]["sample_count"] == 1


def test_projects_are_saved_filtered_and_legacy_databases_import(tmp_path):
    import sqlite3
    store = SessionStore(tmp_path / 'data')
    project_id = 'a' * 32
    project_session = str(uuid.uuid4())
    writer = store.create_session(project_session, {
        'session_id': project_session, 'serial': 'dev', 'created_at_ms': 1,
        'duration_seconds': 60, 'interval_ms': 1000, 'project_id': project_id,
        'project_name': '车型 A', 'enabled_metrics': {'cpu': True, 'memory': False, 'fps': False},
    })
    writer.finish('completed')
    old_session = _create_session(store, 'old')
    old_path = store.database_path(old_session)
    with sqlite3.connect(old_path) as conn:
        conn.execute('ALTER TABLE session DROP COLUMN project_id')
        conn.execute('ALTER TABLE session DROP COLUMN project_name')
    assert store.get_session(old_session)['project_id'] == 'default'
    assert [s['session_id'] for s in store.list_sessions(project_id=project_id)] == [project_session]
    assert [s['session_id'] for s in store.list_sessions(project_id='default')] == [old_session]
    imported = store.import_database(old_path.read_bytes(), project_id, '车型 A')
    assert store.get_session(imported)['project_id'] == project_id
    assert store.get_session(imported)['project_name'] == '车型 A'
    assert store.get_session(old_session)['project_id'] == 'default'
    from app.report import generate_report
    assert '车型 A' in generate_report(project_session, store, metrics=[])


def test_session_path_cache_avoids_repeat_metadata_reads_and_refreshes(tmp_path, monkeypatch):
    import shutil
    store = SessionStore(tmp_path)
    first = _create_session(store, "first")
    path = store.database_path(first)
    real_read = Path.read_text
    reads = []

    def tracked_read(self, *args, **kwargs):
        if self.name == "metadata.json":
            reads.append(self)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read)
    for _ in range(5):
        assert store.database_path(first) == path
        assert not store.database_path("missing").exists()
    assert not reads
    second = _create_session(store, "second")
    assert store.database_path(second).exists()
    assert len(reads) == 2
    shutil.rmtree(path.parent)
    assert not store.database_path(first).exists()
    assert SessionStore(tmp_path).database_path(second) == store.database_path(second)


def test_import_conflicts_with_legacy_direct_directory(tmp_path):
    source = SessionStore(tmp_path / "source")
    first = _create_session(source, "fixture")
    raw = source.database_path(first).read_bytes()
    destination = SessionStore(tmp_path / "dest")
    legacy = destination.data_root / first
    legacy.mkdir()
    (legacy / "monitor.db").write_bytes(raw)
    assert destination.database_path(first).parent == legacy
    replacement = destination.import_database(raw, "new-project", "New")
    assert replacement != first
    assert destination.get_session(first)["project_id"] == "default"
    assert destination.get_session(replacement)["project_id"] == "new-project"
    assert [item["session_id"] for item in destination.list_sessions(project_id="new-project")] == [replacement]


def test_invalid_or_migration_failed_import_leaves_no_published_directory(tmp_path):
    from contextlib import closing
    import sqlite3
    import pytest

    source = SessionStore(tmp_path / "source")
    destination = SessionStore(tmp_path / "dest")
    mutations = [
        "ALTER TABLE sample DROP COLUMN fps",
        "UPDATE session SET enabled_metrics_json='bad JSON'",
        "CREATE TRIGGER no_import BEFORE UPDATE ON session BEGIN SELECT RAISE(ABORT, 'migration fixture'); END",
    ]
    for mutation in mutations:
        first = _create_session(source, "fixture")
        path = source.database_path(first)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(mutation)
        with pytest.raises(ValueError):
            destination.import_database(path.read_bytes())
        assert not list(destination.data_root.iterdir())
        assert not destination.list_sessions()


def test_import_stream_limit_and_cleanup_without_size_header(tmp_path, monkeypatch):
    from fastapi import HTTPException, UploadFile
    import pytest
    from app import storage
    from app.ui_testing import UIManager

    source = SessionStore(tmp_path / "source")
    first = _create_session(source, "fixture")
    raw = source.database_path(first).read_bytes()
    limit = len(raw) + 1
    monkeypatch.setattr(storage, "MAX_SESSION_IMPORT_BYTES", limit)
    monkeypatch.setattr(main, "MAX_SESSION_IMPORT_BYTES", limit)

    async def exercise():
        dest = SessionStore(tmp_path / "dest")
        ui = UIManager(tmp_path / "ui")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(manager=SimpleNamespace(store=dest), ui=ui)))

        for data in (raw, raw + b"\0"):
            file = UploadFile(filename="monitor.db", file=io.BytesIO(data), size=None)
            assert (await main.import_session(request, file, "default"))["serial"] == "fixture"
        for size in (None, limit + 1):
            file = UploadFile(filename="monitor.db", file=io.BytesIO(raw + b"\0\0"), size=size)
            with pytest.raises(HTTPException) as error:
                await main.import_session(request, file, "default")
            assert error.value.status_code == 413
        assert len(list(dest.data_root.iterdir())) == 2

    asyncio.run(exercise())
