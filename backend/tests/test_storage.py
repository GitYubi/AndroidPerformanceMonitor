"""会话存储层测试：目录命名、反查与导入。"""

from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path

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
