"""每会话 SQLite 数据库及有限窗口查询实现。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
import csv
import io
import json
import logging
import math
import re
import sqlite3
from tempfile import TemporaryDirectory
from threading import RLock
import time
import uuid
from pathlib import Path
from typing import Any

from .models import SamplePayload
from .record_cleanup import DELETE_STAGE_PREFIX

SYSTEM_MEMORY_COLUMNS = {"mem_total_kb": "INTEGER", "mem_available_kb": "INTEGER",
                         "mem_used_kb": "INTEGER", "memory_used_pct": "REAL"}
_SAMPLE_COLUMNS = (
    "ts_ms", "cpu_total_pct", "pss_kb", "rss_kb", "total_ram_kb",
    "mem_total_kb", "mem_available_kb", "mem_used_kb", "memory_used_pct",
    "app_render_fps", "app_jank_pct", "fps", "frame_source", "frame_count",
    "jank_count", "jank_pct", "avg_frame_time_ms", "p95_frame_time_ms",
    "p99_frame_time_ms", "input_latency_ms",
)
_REQUIRED_SAMPLE_COLUMNS = {"ts_ms", "cpu_total_pct", "pss_kb", "rss_kb", "fps"}
MAX_SESSION_IMPORT_BYTES = 256 * 1024 * 1024
_IMPORT_STAGE_PREFIX = ".import-"
_HIDDEN_SESSION_PREFIXES = (DELETE_STAGE_PREFIX, _IMPORT_STAGE_PREFIX)
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _sample_select(connection: sqlite3.Connection) -> str:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(sample)")}
    fields = [
        name if name in columns or name in _REQUIRED_SAMPLE_COLUMNS else f"NULL AS {name}"
        for name in _SAMPLE_COLUMNS
    ]
    return f"SELECT {', '.join(fields)} FROM sample"


def ensure_system_memory_columns(connection: sqlite3.Connection) -> None:
    """Upgrade owned Monkey aggregate databases; historical values remain NULL."""
    existing = {row[1] for row in connection.execute("PRAGMA table_info(sample)")}
    for name, kind in SYSTEM_MEMORY_COLUMNS.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE sample ADD COLUMN {name} {kind}")


class SessionWriter:
    """运行中会话的单写入器；只保留 SQLite 连接，不缓存历史采样。"""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.connection = _connect(database_path)

    def write_sample(self, payload: SamplePayload) -> None:
        cursor = self.connection.execute(
            """
            INSERT INTO sample (ts_ms, cpu_total_pct, pss_kb, rss_kb, total_ram_kb,
                                mem_total_kb, mem_available_kb, mem_used_kb, memory_used_pct, fps, app_render_fps,
                                app_jank_pct, frame_source, frame_count, jank_count, jank_pct,
                                avg_frame_time_ms, p95_frame_time_ms, p99_frame_time_ms, input_latency_ms,
                                raw_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.ts_ms,
                payload.cpu_total_pct,
                payload.pss_kb,
                payload.rss_kb,
                payload.total_ram_kb,
                payload.mem_total_kb,
                payload.mem_available_kb,
                payload.mem_used_kb,
                payload.memory_used_pct,
                payload.fps,
                payload.app_render_fps,
                payload.app_jank_pct,
                payload.frame_source,
                payload.frame_count,
                payload.jank_count,
                payload.jank_pct,
                payload.avg_frame_time_ms,
                payload.p95_frame_time_ms,
                payload.p99_frame_time_ms,
                payload.input_latency_ms,
                json.dumps(payload.statuses or {}, ensure_ascii=False),
            ),
        )
        sample_id = cursor.lastrowid
        rows = [
            (sample_id, item.process_name, item.pid, item.cpu_pct, item.pss_kb, item.rss_kb, item.uss_kb)
            for item in payload.processes or []
            if item.cpu_pct is not None or item.pss_kb is not None or item.rss_kb is not None or item.uss_kb is not None
        ]
        if rows:
            self.connection.executemany(
                """
                INSERT INTO process_sample (sample_id, process_name, pid, cpu_pct, pss_kb, rss_kb, uss_kb)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        self.connection.commit()

    def add_event(self, severity: str, code: str, message: str) -> None:
        self.connection.execute(
            "INSERT INTO event (ts_ms, severity, code, message) VALUES (?, ?, ?, ?)",
            (int(time.time() * 1000), severity, code, message[:1000]),
        )
        self.connection.commit()

    def finish(self, state: str) -> dict[str, Any]:
        try:
            summary = _calculate_summary(self.connection)
            ended_at_ms = int(time.time() * 1000)
            self.connection.execute(
                "UPDATE session SET state = ?, ended_at_ms = ?, summary_json = ? WHERE id = 1",
                (state, ended_at_ms, json.dumps(summary, ensure_ascii=False)),
            )
            self.connection.commit()
            return summary
        finally:
            self.connection.close()


def _connect(database_path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, check_same_thread=check_same_thread)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=3000")
        return connection
    except BaseException:
        connection.close()
        raise


def query_process_rss_growth(connection: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    """Rank positive changes between successive recorded RSS snapshots."""
    rows = connection.execute("""
        WITH snapshots AS (
            SELECT sample_id, process_name, pid, MAX(rss_kb) AS rss_kb,
                   DENSE_RANK() OVER (ORDER BY sample_id) AS snapshot
            FROM process_sample
            WHERE pid IS NOT NULL AND rss_kb > 0
            GROUP BY sample_id, process_name, pid
        ), previous AS (
            SELECT *,
                   LAG(snapshot) OVER process_order AS prior_snapshot,
                   LAG(rss_kb) OVER process_order AS prior_rss
            FROM snapshots
            WINDOW process_order AS (PARTITION BY process_name, pid ORDER BY snapshot)
        ), changes AS (
            SELECT process_name, pid,
                   rss_kb - CASE WHEN prior_snapshot = snapshot-1 THEN prior_rss ELSE 0 END AS growth
            FROM previous WHERE snapshot > 1
        )
        SELECT process_name, pid, ROUND(AVG(growth), 2) AS average, MAX(growth) AS peak,
               COUNT(*) AS samples
        FROM changes WHERE growth > 0 GROUP BY process_name, pid
        ORDER BY peak DESC, process_name, pid LIMIT ?
    """, (limit,))
    return [dict(row) for row in rows]


def _read_session(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM session WHERE id = 1").fetchone()
    if row is None:
        return None
    result = dict(row)
    result.setdefault("project_id", "default")
    result.setdefault("project_name", "默认项目")
    result["enabled_metrics"] = json.loads(result.pop("enabled_metrics_json"))
    result["summary"] = json.loads(result.pop("summary_json") or "{}")
    return result


def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in columns


def _calculate_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    summary: dict[str, Any] = {"sample_count": 0, "metrics": {}}
    summary["sample_count"] = int(connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0])
    definitions = {
        "cpu": ("cpu_total_pct", "pct"),
        "memory_pss": ("pss_kb", "kb"),
        "memory_rss": ("rss_kb", "kb"),
        "memory_used_pct": ("memory_used_pct", "pct"),
        "memory_used": ("mem_used_kb", "kb"),
        "memory_available": ("mem_available_kb", "kb"),
        "fps": ("fps", "fps"),
        "app_render_fps": ("app_render_fps", "fps"),
        "app_jank_pct": ("app_jank_pct", "pct"),
        "frame_jank_pct": ("jank_pct", "pct"),
        "frame_p95": ("p95_frame_time_ms", "ms"),
        "frame_p99": ("p99_frame_time_ms", "ms"),
        "frame_avg": ("avg_frame_time_ms", "ms"),
    }
    for key, (column, unit) in definitions.items():
        if not _column_exists(connection, "sample", column):
            continue
        aggregate = "MIN" if key in {"fps", "app_render_fps"} else "MAX"
        row = connection.execute(
            f"SELECT AVG({column}) AS average, {aggregate}({column}) AS peak, COUNT({column}) AS valid_count FROM sample"
        ).fetchone()
        summary["metrics"][key] = {
            "average": round(row["average"], 2) if row["average"] is not None else None,
            "peak": round(row["peak"], 2) if row["peak"] is not None else None,
            "valid_count": row["valid_count"],
            "unit": unit,
        }
    return summary


class SessionStore:
    """会话数据库目录的唯一入口，避免全局大内存数据结构。"""

    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.data_root.mkdir(parents=True, exist_ok=True)
        self._directory_lock = RLock()
        self._directory_stamp: int | None = None
        self._session_dirs: dict[str, Path] = {}

    def _session_dir_name(self, serial: str) -> str:
        """会话目录名：设备号_日期（同设备同日多会话追加 _2/_3 序号）。

        设备序列号中的非文件系统安全字符（如网络 adb 的冒号）替换为下划线。
        """
        device = re.sub(r"[^A-Za-z0-9_.-]", "_", serial)
        base = f"{device}_{time.strftime('%Y_%m_%d')}"
        dir_name = base
        counter = 2
        while (self.data_root / dir_name).exists():
            dir_name = f"{base}_{counter}"
            counter += 1
        return dir_name

    def _find_session_dir(self, session_id: str) -> Path | None:
        """会话根目录发生变化时重新读取元数据，稳定时复用目录索引。"""
        with self._directory_lock:
            stamp = self.data_root.stat().st_mtime_ns
            cached = self._session_dirs.get(session_id)
            if stamp == self._directory_stamp and (cached is None or (cached / "monitor.db").is_file()):
                return cached
            sessions = {}
            for path in self.data_root.glob("*/metadata.json"):
                if path.parent.name.startswith(_HIDDEN_SESSION_PREFIXES) or not (path.parent / "monitor.db").is_file():
                    continue
                try:
                    meta = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(meta, dict) and isinstance(meta.get("session_id"), str):
                    sessions.setdefault(meta["session_id"], path.parent)
            self._session_dirs = sessions
            self._directory_stamp = stamp
            return sessions.get(session_id)

    def create_session(self, session_id: str, metadata: dict[str, Any]) -> SessionWriter:
        session_dir = self.data_root / self._session_dir_name(metadata["serial"])
        session_dir.mkdir(parents=True, exist_ok=False)
        database_path = session_dir / "monitor.db"
        connection = _connect(database_path)
        connection.executescript(
            """
            CREATE TABLE session (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                session_id TEXT NOT NULL,
                serial TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT 'default',
                project_name TEXT NOT NULL DEFAULT '默认项目',
                state TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                started_at_ms INTEGER NOT NULL,
                ended_at_ms INTEGER,
                duration_seconds INTEGER NOT NULL,
                interval_ms INTEGER NOT NULL,
                enabled_metrics_json TEXT NOT NULL,
                surface_layer TEXT,
                summary_json TEXT
            );
            CREATE TABLE sample (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                cpu_total_pct REAL,
                pss_kb INTEGER,
                rss_kb INTEGER,
                total_ram_kb INTEGER,
                mem_total_kb INTEGER,
                mem_available_kb INTEGER,
                mem_used_kb INTEGER,
                memory_used_pct REAL,
                fps REAL,
                app_render_fps REAL,
                app_jank_pct REAL,
                frame_source TEXT,
                frame_count INTEGER,
                jank_count INTEGER,
                jank_pct REAL,
                avg_frame_time_ms REAL,
                p95_frame_time_ms REAL,
                p99_frame_time_ms REAL,
                input_latency_ms REAL,
                raw_status TEXT NOT NULL
            );
            CREATE TABLE process_sample (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id INTEGER NOT NULL REFERENCES sample(id) ON DELETE CASCADE,
                process_name TEXT NOT NULL,
                pid INTEGER,
                cpu_pct REAL,
                pss_kb INTEGER,
                rss_kb INTEGER,
                uss_kb INTEGER
            );
            CREATE INDEX idx_process_sample_name ON process_sample(process_name);
            CREATE INDEX idx_sample_ts ON sample(ts_ms);
            CREATE TABLE event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                severity TEXT NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO session (
              id, session_id, serial, project_id, project_name, state, created_at_ms, started_at_ms,
              duration_seconds, interval_ms, enabled_metrics_json, surface_layer
            ) VALUES (1, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                metadata["serial"],
                metadata.get("project_id", "default"),
                metadata.get("project_name", "默认项目"),
                metadata["created_at_ms"],
                metadata["created_at_ms"],
                metadata["duration_seconds"],
                metadata["interval_ms"],
                json.dumps(metadata["enabled_metrics"], ensure_ascii=False),
                metadata.get("surface_layer"),
            ),
        )
        connection.commit()
        connection.close()
        (session_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._directory_lock:
            self._directory_stamp = None
        return SessionWriter(database_path)

    def database_path(self, session_id: str) -> Path:
        """定位会话数据库；兼容旧版 UUID 目录名与新版 设备_日期 目录名。"""
        if not _SESSION_ID.fullmatch(session_id):
            return self.data_root / ".missing-session" / "monitor.db"
        direct = self.data_root / session_id / "monitor.db"
        if direct.is_file():
            return direct
        dir_path = self._find_session_dir(session_id)
        return (dir_path / "monitor.db") if dir_path is not None else direct

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        path = self.database_path(session_id)
        if not path.exists():
            return None
        with closing(_connect(path)) as connection:
            return _read_session(connection)

    def _read_series(self, session_id: str, limit: int | None) -> list[dict[str, Any]]:
        path = self.database_path(session_id)
        if not path.exists():
            return []
        connection = _connect(path)
        try:
            select = _sample_select(connection)
            if limit is None:
                rows = connection.execute(f"{select} ORDER BY id ASC").fetchall()
                return [dict(row) for row in rows]
            rows = connection.execute(
                f"{select} ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]
        finally:
            connection.close()

    def get_series(self, session_id: str, limit: int = 180) -> list[dict[str, Any]]:
        return self._read_series(session_id, limit)

    def get_all_samples(self, session_id: str) -> list[dict[str, Any]]:
        """全量采样点（报告生成用，不做行数限制）。"""
        return self._read_series(session_id, None)

    def get_processes(self, session_id: str, metric: str, limit: int = 10) -> list[dict[str, Any]]:
        column = {"cpu": "cpu_pct", "pss": "pss_kb", "rss": "rss_kb"}.get(metric)
        if column is None and metric != "rss_growth":
            raise ValueError("metric 必须为 cpu、pss、rss 或 rss_growth")
        path = self.database_path(session_id)
        if not path.exists():
            return []
        limit = max(1, min(limit, 100))
        connection = _connect(path)
        try:
            if metric == "rss_growth":
                return query_process_rss_growth(connection, limit)
            rows = connection.execute(
                f"""
                SELECT process_name, MAX(pid) AS pid,
                       ROUND(AVG({column}), 2) AS average,
                       ROUND(MAX({column}), 2) AS peak,
                       COUNT({column}) AS samples
                FROM process_sample
                WHERE {column} IS NOT NULL
                GROUP BY process_name
                ORDER BY average DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def get_process_uss_series(self, session_id: str) -> list[dict[str, Any]]:
        """返回真实内存快照时刻的应用 USS；旧会话没有该列时返回空列表。"""
        path = self.database_path(session_id)
        if not path.exists():
            return []
        connection = _connect(path)
        try:
            if not _column_exists(connection, "process_sample", "uss_kb"):
                return []
            rows = connection.execute(
                """
                SELECT sample.ts_ms, process_sample.process_name, process_sample.pid, process_sample.uss_kb
                FROM process_sample
                JOIN sample ON sample.id = process_sample.sample_id
                WHERE process_sample.uss_kb IS NOT NULL OR process_sample.pss_kb IS NOT NULL
                ORDER BY sample.ts_ms ASC, process_sample.process_name ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def get_events(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        path = self.database_path(session_id)
        if not path.exists():
            return []
        connection = _connect(path)
        try:
            rows = connection.execute(
                "SELECT ts_ms, severity, code, message FROM event ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def to_csv(self, session_id: str) -> str:
        return "".join(self.iter_csv(session_id))

    def iter_csv(self, session_id: str) -> Iterator[str]:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(_SAMPLE_COLUMNS)
        yield output.getvalue()
        path = self.database_path(session_id)
        if not path.exists():
            return
        # StreamingResponse advances this serial generator in worker threads.
        with closing(_connect(path, check_same_thread=False)) as connection:
            cursor = connection.execute(f"{_sample_select(connection)} ORDER BY id ASC")
            while rows := cursor.fetchmany(256):
                output.seek(0)
                output.truncate(0)
                writer.writerows(rows)
                yield output.getvalue()

    def recover_interrupted_sessions(self) -> int:
        """服务异常退出后，保留样本并将遗留 running 会话标记为 interrupted。"""

        recovered = 0
        for path in self.data_root.glob("*/monitor.db"):
            if path.parent.name.startswith(_HIDDEN_SESSION_PREFIXES):
                continue
            try:
                with closing(_connect(path)) as connection:
                    row = connection.execute("SELECT state FROM session WHERE id = 1").fetchone()
                    if row and row["state"] == "running":
                        connection.execute(
                            "UPDATE session SET state = 'interrupted', ended_at_ms = ? WHERE id = 1",
                            (int(time.time() * 1000),),
                        )
                        connection.commit()
                        recovered += 1
            except (sqlite3.Error, OSError) as exc:
                logging.getLogger(__name__).warning("Could not recover session database %s: %s", path, exc)
        return recovered

    def list_sessions(self, limit: int | None = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        """按修改时间倒序列出本地全部历史会话（含 running）。"""
        sessions: list[dict[str, Any]] = []
        paths = []
        for path in self.data_root.glob("*/monitor.db"):
            if path.parent.name.startswith(_HIDDEN_SESSION_PREFIXES):
                continue
            try:
                paths.append((path.stat().st_mtime, path))
            except OSError as exc:
                logging.getLogger(__name__).warning("Could not inspect session database %s: %s", path, exc)
        for _, path in sorted(paths, reverse=True):
            try:
                with closing(_connect(path)) as connection:
                    session = _read_session(connection)
            except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
                logging.getLogger(__name__).warning("Could not list session database %s: %s", path, exc)
                continue
            if session is not None and (project_id is None or session["project_id"] == project_id):
                session["dir_name"] = path.parent.name
                sessions.append(session)
            if limit is not None and len(sessions) >= limit:
                break
        return sessions

    def import_stage(self):
        return TemporaryDirectory(prefix=_IMPORT_STAGE_PREFIX, dir=self.data_root)

    def import_database(self, data: bytes, project_id: str = "default", project_name: str = "默认项目") -> str:
        """兼容已经读取字节的调用方，使用与上传相同的发布路径。"""
        if not data or len(data) > MAX_SESSION_IMPORT_BYTES:
            raise ValueError("会话数据库为空或超过 256 MiB")
        with self.import_stage() as directory:
            stage = Path(directory)
            (stage / "monitor.db").write_bytes(data)
            return self.publish_import(stage, project_id, project_name)

    def publish_import(self, stage: Path, project_id: str, project_name: str) -> str:
        """在同一文件系统内验证及迁移后，原子发布会话。"""
        if stage.parent.resolve() != self.data_root.resolve() or not stage.name.startswith(_IMPORT_STAGE_PREFIX):
            raise ValueError("导入暂存目录无效")
        database = stage / "monitor.db"
        if not database.is_file() or not 0 < database.stat().st_size <= MAX_SESSION_IMPORT_BYTES:
            raise ValueError("会话数据库为空或超过 256 MiB")
        try:
            with closing(_connect(database)) as connection:
                session = _validate_import(connection)
                session_id = session["session_id"]
                with self._directory_lock:
                    while self.database_path(session_id).is_file():
                        session_id = str(uuid.uuid4())
                    with connection:
                        for name in ("project_id", "project_name"):
                            if not _column_exists(connection, "session", name):
                                connection.execute(f"ALTER TABLE session ADD COLUMN {name} TEXT")
                        connection.execute(
                            "UPDATE session SET session_id=?, project_id=?, project_name=? WHERE id=1",
                            (session_id, project_id, project_name),
                        )
                    session = _validate_import(connection)
                    if (session["session_id"], session["project_id"], session["project_name"]) != (
                            session_id, project_id, project_name):
                        raise ValueError("导入后会话标识或项目不一致")
                    connection.close()
                    meta = {key: session.get(key) for key in (
                        "session_id", "project_id", "project_name", "serial", "created_at_ms", "started_at_ms",
                        "ended_at_ms", "state", "duration_seconds", "interval_ms", "enabled_metrics", "surface_layer",
                    )}
                    (stage / "metadata.json").write_text(
                        json.dumps(dict(meta, imported=True), ensure_ascii=False, indent=2), encoding="utf-8",
                    )
                    stage.rename(self.data_root / self._session_dir_name(session["serial"] or "unknown"))
                    self._directory_stamp = None
            return session_id
        except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError) as exc:
            raise ValueError(f"不是有效的会话数据库：{exc}") from exc


def _validate_import(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {
        "session": {"id", "session_id", "serial", "state", "created_at_ms", "started_at_ms", "ended_at_ms",
                    "duration_seconds", "interval_ms", "enabled_metrics_json", "summary_json"},
        "sample": {"id"} | _REQUIRED_SAMPLE_COLUMNS,
        "process_sample": {"sample_id", "process_name", "pid", "cpu_pct", "pss_kb", "rss_kb"},
        "event": {"id", "ts_ms", "severity", "code", "message"},
    }
    columns = {}
    for table, fields in required.items():
        if table not in tables:
            raise ValueError(f"缺少 {table} 表")
        columns[table] = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if missing := fields - columns[table]:
            raise ValueError(f"{table} 缺少字段：{', '.join(sorted(missing))}")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("SQLite 完整性检查失败")

    session = _read_session(connection)
    if session is None:
        raise ValueError("会话数据库缺少 session 记录")
    if not isinstance(session["session_id"], str) or not _SESSION_ID.fullmatch(session["session_id"]):
        raise ValueError("会话标识无效")
    if any(not isinstance(session[key], str) for key in ("serial", "state")):
        raise ValueError("会话设备或状态无效")
    if any(type(session[key]) is not int for key in ("created_at_ms", "started_at_ms", "duration_seconds", "interval_ms")):
        raise ValueError("会话时长或时间字段必须为整数")
    if session["ended_at_ms"] is not None and type(session["ended_at_ms"]) is not int:
        raise ValueError("会话结束时间必须为整数或空值")
    if not isinstance(session["enabled_metrics"], dict):
        raise ValueError("会话采集配置必须是 JSON 对象")
    summary = session["summary"]
    if not isinstance(summary, dict):
        raise ValueError("会话摘要必须是 JSON 对象")
    sample_count = summary.get("sample_count", 0)
    if type(sample_count) is not int or sample_count < 0:
        raise ValueError("会话摘要的样本数必须是非负整数")
    metrics = summary.get("metrics", {})
    if not isinstance(metrics, dict) or any(
        not isinstance(metric, dict) or any(
            type(value) not in (int, float) or not math.isfinite(value)
            for key in ("average", "peak") if (value := metric.get(key)) is not None
        ) for metric in metrics.values()
    ):
        raise ValueError("会话摘要的指标必须为有效数值")

    numeric = set(_SAMPLE_COLUMNS) - {"ts_ms", "frame_source"}
    sample_types = ["typeof(id) <> 'integer'", "typeof(ts_ms) <> 'integer'"]
    for key in sorted(numeric & columns["sample"]):
        sample_types.append(f"typeof({key}) NOT IN ('integer','real','null') OR "
                            f"ABS(CAST({key} AS REAL)) > 1.7976931348623157e308")
    if connection.execute(f"SELECT 1 FROM sample WHERE {' OR '.join(sample_types)} LIMIT 1").fetchone():
        raise ValueError("采样时间或指标存在无效数值")
    process_types = ["typeof(sample_id) <> 'integer'", "typeof(process_name) <> 'text'"]
    for key in sorted({"pid", "uss_kb", "pss_kb", "rss_kb"} & columns["process_sample"]):
        process_types.append(f"typeof({key}) NOT IN ('integer','null')")
    process_types.append("typeof(cpu_pct) NOT IN ('integer','real','null') OR "
                         "ABS(CAST(cpu_pct AS REAL)) > 1.7976931348623157e308")
    if connection.execute(f"SELECT 1 FROM process_sample WHERE {' OR '.join(process_types)} LIMIT 1").fetchone():
        raise ValueError("进程 PID 或指标存在无效数值")
    return session
