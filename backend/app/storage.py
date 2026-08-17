"""每会话 SQLite 数据库及有限窗口查询实现。"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import SamplePayload


class SessionWriter:
    """运行中会话的单写入器；只保留 SQLite 连接，不缓存历史采样。"""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.connection = _connect(database_path)

    def write_sample(self, payload: SamplePayload) -> None:
        cursor = self.connection.execute(
            """
            INSERT INTO sample (ts_ms, cpu_total_pct, pss_kb, rss_kb, total_ram_kb, fps, app_render_fps,
                                app_jank_pct, frame_source, frame_count, jank_count, jank_pct,
                                avg_frame_time_ms, p95_frame_time_ms, p99_frame_time_ms, input_latency_ms,
                                raw_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.ts_ms,
                payload.cpu_total_pct,
                payload.pss_kb,
                payload.rss_kb,
                payload.total_ram_kb,
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
            (sample_id, item.process_name, item.pid, item.cpu_pct, item.pss_kb, item.rss_kb)
            for item in payload.processes or []
            if item.cpu_pct is not None or item.pss_kb is not None or item.rss_kb is not None
        ]
        if rows:
            self.connection.executemany(
                """
                INSERT INTO process_sample (sample_id, process_name, pid, cpu_pct, pss_kb, rss_kb)
                VALUES (?, ?, ?, ?, ?, ?)
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
        summary = _calculate_summary(self.connection)
        ended_at_ms = int(time.time() * 1000)
        self.connection.execute(
            "UPDATE session SET state = ?, ended_at_ms = ?, summary_json = ? WHERE id = 1",
            (state, ended_at_ms, json.dumps(summary, ensure_ascii=False)),
        )
        self.connection.commit()
        self.connection.close()
        return summary


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=3000")
    return connection


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

    def create_session(self, session_id: str, metadata: dict[str, Any]) -> SessionWriter:
        session_dir = self.data_root / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        database_path = session_dir / "monitor.db"
        connection = _connect(database_path)
        connection.executescript(
            """
            CREATE TABLE session (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                session_id TEXT NOT NULL,
                serial TEXT NOT NULL,
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
                rss_kb INTEGER
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
              id, session_id, serial, state, created_at_ms, started_at_ms,
              duration_seconds, interval_ms, enabled_metrics_json, surface_layer
            ) VALUES (1, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                metadata["serial"],
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
        return SessionWriter(database_path)

    def database_path(self, session_id: str) -> Path:
        return self.data_root / session_id / "monitor.db"

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        path = self.database_path(session_id)
        if not path.exists():
            return None
        connection = _connect(path)
        try:
            row = connection.execute("SELECT * FROM session WHERE id = 1").fetchone()
            if row is None:
                return None
            result = dict(row)
            result["enabled_metrics"] = json.loads(result.pop("enabled_metrics_json"))
            result["summary"] = json.loads(result.pop("summary_json") or "{}")
            return result
        finally:
            connection.close()

    def get_series(self, session_id: str, limit: int = 180) -> list[dict[str, Any]]:
        path = self.database_path(session_id)
        if not path.exists():
            return []
        connection = _connect(path)
        try:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(sample)").fetchall()}
            column = lambda name: name if name in columns else f"NULL AS {name}"  # noqa: E731
            rows = connection.execute(
                f"""SELECT ts_ms, cpu_total_pct, pss_kb, rss_kb, {column("total_ram_kb")},
                           {column("app_render_fps")}, {column("app_jank_pct")}, fps,
                           {column("frame_source")}, {column("frame_count")}, {column("jank_count")},
                           {column("jank_pct")}, {column("avg_frame_time_ms")}, {column("p95_frame_time_ms")},
                           {column("p99_frame_time_ms")}, {column("input_latency_ms")}
                    FROM sample ORDER BY id DESC LIMIT ?""",
                (max(1, min(limit, 1000)),),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]
        finally:
            connection.close()
    def get_processes(self, session_id: str, metric: str, limit: int = 10) -> list[dict[str, Any]]:
        column = {"cpu": "cpu_pct", "pss": "pss_kb", "rss": "rss_kb"}.get(metric)
        if column is None:
            raise ValueError("metric 必须为 cpu、pss 或 rss")
        path = self.database_path(session_id)
        if not path.exists():
            return []
        connection = _connect(path)
        try:
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
                (max(1, min(limit, 100)),),
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
        rows = self.get_series(session_id, limit=100_000)
        output = io.StringIO()
        fieldnames = [
            "ts_ms", "cpu_total_pct", "pss_kb", "rss_kb", "total_ram_kb",
            "app_render_fps", "app_jank_pct", "fps",
            "frame_source", "frame_count", "jank_count", "jank_pct",
            "avg_frame_time_ms", "p95_frame_time_ms", "p99_frame_time_ms", "input_latency_ms",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    def recover_interrupted_sessions(self) -> int:
        """服务异常退出后，保留样本并将遗留 running 会话标记为 interrupted。"""

        recovered = 0
        for path in self.data_root.glob("*/monitor.db"):
            connection = _connect(path)
            try:
                row = connection.execute("SELECT state FROM session WHERE id = 1").fetchone()
                if row and row["state"] == "running":
                    connection.execute(
                        "UPDATE session SET state = 'interrupted', ended_at_ms = ? WHERE id = 1",
                        (int(time.time() * 1000),),
                    )
                    connection.commit()
                    recovered += 1
            finally:
                connection.close()
        return recovered

