"""Disk-backed Monkey records. SQL copies and cursor CSV exports never load a run into RAM."""
from __future__ import annotations

import csv
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from .storage import SessionStore, ensure_system_memory_columns

CSV_FIELDS = ["ts_ms", "cpu_total_pct", "pss_kb", "rss_kb", "total_ram_kb", "fps",
              "app_render_fps", "jank_pct", "frame_count", "jank_count", "frame_source",
              "mem_total_kb", "mem_available_kb", "mem_used_kb", "memory_used_pct"]


class MonkeyReportStore(SessionStore):
    """Read the merged database through the ordinary report interface with bounded charts."""

    report_notice = "Monkey 全程曲线及部分进程 USS 快照做抽稀，可能遗漏短时峰值；完整记录见合并 CSV 和 SQLite。CPU、系统内存与 PSS 汇总使用全部有效样本。"

    def __init__(self, database: Path):
        self.database = database

    def database_path(self, session_id: str) -> Path:
        return self.database

    def get_session(self, session_id: str) -> dict | None:
        session = super().get_session(session_id)
        if session is None:
            return None
        metrics = session.get("summary", {}).get("metrics", {})
        for metric in metrics.values():
            metric["valid_count"] = metric.get("valid_samples", 0)
        # Monkey's summary peak is MAX for all metrics; the report labels FPS as minimum.
        with connect(self.database) as c:
            columns = {row["name"] for row in c.execute("PRAGMA table_info(sample)")}
            for key, column, aggregate in [("fps", "fps", "MIN"), ("app_render_fps", "app_render_fps", "MIN"),
                                           ("app_jank_pct", "app_jank_pct", "MAX"), ("frame_jank_pct", "jank_pct", "MAX"),
                                           ("frame_p95", "p95_frame_time_ms", "MAX")]:
                if column not in columns:
                    continue
                row = c.execute(f"SELECT AVG({column}) AS average, {aggregate}({column}) AS peak, COUNT({column}) AS valid_count FROM sample").fetchone()
                if key == "fps" and key in metrics:
                    metrics[key]["peak"] = row["peak"]
                else:
                    metrics[key] = dict(row)
        return session

    def get_all_samples(self, session_id: str) -> list[dict]:
        with connect(self.database) as c:
            count = c.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
            stride = max(1, (count + 5999) // 6000)
            rows = c.execute("""WITH numbered AS (SELECT sample.*, ROW_NUMBER() OVER (ORDER BY id) AS rn FROM sample)
                SELECT * FROM numbered WHERE (rn - 1) % ? = 0 OR rn = ? ORDER BY rn""", (stride, count))
            return [dict(row) for row in rows]

    def get_process_uss_series(self, session_id: str) -> list[dict]:
        with connect(self.database) as c:
            columns = {row["name"] for row in c.execute("PRAGMA table_info(process_sample)")}
            if "uss_kb" not in columns:
                return []
            rows = c.execute("""WITH top_process AS (
                SELECT process_name FROM process_sample WHERE uss_kb IS NOT NULL
                GROUP BY process_name ORDER BY AVG(uss_kb) DESC LIMIT 15
            ), ranked AS (
                SELECT s.ts_ms, p.process_name, p.pid, p.uss_kb,
                  ROW_NUMBER() OVER (PARTITION BY p.process_name ORDER BY s.ts_ms, s.id) AS rn,
                  COUNT(*) OVER (PARTITION BY p.process_name) AS total
                FROM process_sample p JOIN sample s ON s.id=p.sample_id
                WHERE p.uss_kb IS NOT NULL AND p.process_name IN (SELECT process_name FROM top_process)
            ) SELECT ts_ms, process_name, pid, uss_kb FROM ranked
              WHERE rn=1 OR rn=total OR rn % MAX(1, (total+599)/600)=0
              ORDER BY ts_ms, process_name""")
            return [dict(row) for row in rows]


@contextmanager
def connect(path: Path):
    c = sqlite3.connect(path, timeout=10, uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA cache_size=-2048")
    c.execute("PRAGMA temp_store=FILE")
    try:
        with c:
            yield c
    finally:
        c.close()


class MonkeyRecords:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.index = root / "jobs.db"
        with connect(self.index) as c:
            c.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, state TEXT NOT NULL, created_at INTEGER NOT NULL, body TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at DESC)")

    def put(self, run: dict) -> None:
        with connect(self.index) as c:
            c.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?)",
                      (run["id"], run["state"], run["created_at_ms"], json.dumps(run, ensure_ascii=False)))

    def get(self, run_id: str) -> dict:
        with connect(self.index) as c:
            row = c.execute("SELECT body FROM jobs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return json.loads(row[0])

    def runs(self, limit: int = 50, offset: int = 0, unfinished: bool = False, brief: bool = False) -> list[dict]:
        with connect(self.index) as c:
            where = "WHERE state IN ('starting','running','stopping')" if unfinished else ""
            body = "json_remove(body,'$.config')" if brief else "body"
            rows = c.execute(f"SELECT {body} FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                             (min(limit, 50), offset)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def directory(self, run_id: str) -> Path:
        # IDs are looked up before any path is formed; no arbitrary filesystem paths.
        self.get(run_id)
        return self.root / run_id

    def database(self, run_id: str) -> Path:
        return self.directory(run_id) / "combined" / "monitor.db"

    def initialize(self, run: dict) -> None:
        directory = self.root / run["id"]
        directory.mkdir(exist_ok=False)
        store = SessionStore(directory)
        writer = store.create_session(run["id"], {
            "session_id": run["id"],
            "serial": run["serial"], "created_at_ms": run["created_at_ms"],
            "duration_seconds": (run["config"]["duration_hours"] or 0) * 3600,
            "interval_ms": run["config"]["interval_ms"],
            "enabled_metrics": {"cpu": True, "memory": True, "fps": run["config"]["fps"]},
        })
        actual = writer.database_path.parent
        writer.connection.close()
        actual.rename(directory / "combined")
        with connect(directory / "combined/monitor.db") as c:
            c.execute("ALTER TABLE sample ADD COLUMN segment_no INTEGER")
            c.execute("CREATE TABLE monkey_segment(number INTEGER PRIMARY KEY, session_id TEXT UNIQUE, started_at_ms INTEGER, ended_at_ms INTEGER, sample_count INTEGER, state TEXT)")
            c.execute("CREATE INDEX idx_process_sample_sample ON process_sample(sample_id)")

    def segments(self, run_id: str, offset: int = 0) -> list[dict]:
        with connect(self.database(run_id)) as c:
            rows = c.execute("SELECT * FROM monkey_segment ORDER BY number DESC LIMIT 24 OFFSET ?", (offset,)).fetchall()
        return [dict(row) for row in rows]

    def started(self, run_id: str, at_ms: int) -> None:
        with connect(self.database(run_id)) as c:
            c.execute("UPDATE session SET started_at_ms=? WHERE id=1", (at_ms,))

    def archive(self, run_id: str, session_id: str, source: Path) -> int:
        """Idempotent, transactional merge. Original rows/IDs are not changed."""
        database = self.database(run_id)
        with connect(database) as c:
            ensure_system_memory_columns(c)
            c.execute("ATTACH DATABASE ? AS source", (source.resolve().as_uri() + "?mode=ro",))
            existing = c.execute("SELECT number FROM monkey_segment WHERE session_id=?", (session_id,)).fetchone()
            if existing:
                number = existing[0]
            else:
                number = c.execute("SELECT COALESCE(MAX(number),0)+1 FROM monkey_segment").fetchone()[0]
                offset = c.execute("SELECT COALESCE(MAX(id),0) FROM sample").fetchone()[0]
                columns = [row["name"] for row in c.execute("PRAGMA source.table_info(sample)") if row["name"] != "id"]
                names = ",".join(columns)
                c.execute(f"INSERT INTO sample(id,{names},segment_no) SELECT id+?,{names},? FROM source.sample ORDER BY id", (offset, number))
                c.execute("""INSERT INTO process_sample(sample_id,process_name,pid,cpu_pct,pss_kb,rss_kb,uss_kb)
                             SELECT sample_id+?,process_name,pid,cpu_pct,pss_kb,rss_kb,uss_kb FROM source.process_sample""", (offset,))
                c.execute("INSERT INTO event(ts_ms,severity,code,message) SELECT ts_ms,severity,code,message FROM source.event")
                session = c.execute("SELECT * FROM source.session WHERE id=1").fetchone()
                count = c.execute("SELECT COUNT(*) FROM source.sample").fetchone()[0]
                c.execute("INSERT INTO monkey_segment VALUES(?,?,?,?,?,?)", (number, session_id, session["started_at_ms"], session["ended_at_ms"], count, session["state"]))
        # Export all rows using a cursor, including an incomplete final hour.
        destination = database.parent.parent / f"hour-{number:04d}.csv"
        temp = destination.with_suffix(".tmp")
        with connect(source) as c, temp.open("w", newline="", encoding="utf-8-sig") as out:
            columns = {row["name"] for row in c.execute("PRAGMA table_info(sample)")}
            select = ",".join(name if name in columns else f"NULL AS {name}" for name in CSV_FIELDS)
            fields = [*CSV_FIELDS, "memory_pss_pct"]
            writer = csv.writer(out)
            writer.writerow(fields)
            for row in c.execute(f"SELECT {select}, CASE WHEN total_ram_kb>0 THEN 100.0*pss_kb/total_ram_kb END FROM sample ORDER BY id"):
                writer.writerow(tuple(row))
        temp.replace(destination)
        return number

    def summarize(self, run_id: str, state: str, ended_at_ms: int) -> dict:
        """Time weights use actual sample timestamps; gaps >3 intervals are excluded."""
        database = self.database(run_id)
        with connect(database) as c:
            ensure_system_memory_columns(c)
            session = c.execute("SELECT * FROM session WHERE id=1").fetchone()
            interval = session["interval_ms"]
            prefix = """WITH timed AS (
                SELECT s.*, MIN(MAX(0,COALESCE(LEAD(ts_ms) OVER (PARTITION BY segment_no ORDER BY id),m.ended_at_ms)-ts_ms), ?) AS weight_ms,
                  CASE WHEN total_ram_kb>0 THEN 100.0*pss_kb/total_ram_kb END AS memory_pss_pct
                FROM sample s JOIN monkey_segment m ON s.segment_no=m.number
            ) """
            metrics = {}
            for name, column, unit in [("cpu", "cpu_total_pct", "%"), ("memory_pss_pct", "memory_pss_pct", "%"),
                                       ("memory_used_pct", "memory_used_pct", "%"), ("memory_used", "mem_used_kb", "KiB"),
                                       ("memory_available", "mem_available_kb", "KiB"),
                                       ("memory_pss", "pss_kb", "KiB"), ("memory_rss", "rss_kb", "KiB"), ("fps", "fps", "FPS")]:
                row = c.execute(prefix + f"""SELECT COUNT({column}) AS valid_samples, MAX({column}) AS peak,
                    SUM(CASE WHEN {column} IS NOT NULL THEN weight_ms ELSE 0 END) AS observed_ms,
                    1.0*SUM({column}*weight_ms)/NULLIF(SUM(CASE WHEN {column} IS NOT NULL THEN weight_ms ELSE 0 END),0) AS average
                    FROM timed""", (interval * 3,)).fetchone()
                metrics[name] = {**dict(row), "unit": unit}
                if unit == "%":
                    bins = []
                    for lower, upper in [(0, 20), (20, 40), (40, 60), (60, 80), (80, None)]:
                        condition = f"{column}>=?" + (f" AND {column}<?" if upper else "")
                        params = (interval * 3, lower, upper) if upper else (interval * 3, lower)
                        weight = c.execute(prefix + f"SELECT COALESCE(SUM(weight_ms),0) FROM timed WHERE {condition}", params).fetchone()[0]
                        bins.append({"lower": lower, "upper": upper, "observed_ms": weight,
                                     "percent": 100 * weight / row["observed_ms"] if row["observed_ms"] else None})
                    metrics[name]["distribution"] = bins
            processes = [dict(row) for row in c.execute("""SELECT p.process_name, COUNT(p.cpu_pct) AS cpu_samples,
                AVG(p.cpu_pct) AS cpu_average_pct, MAX(p.cpu_pct) AS cpu_peak_pct,
                100.0*SUM(p.cpu_pct)/NULLIF((SELECT SUM(cpu_pct) FROM process_sample),0) AS cpu_sampled_share_pct,
                AVG(p.pss_kb) AS pss_average_kb, MAX(p.pss_kb) AS pss_peak_kb,
                AVG(CASE WHEN s.total_ram_kb>0 THEN 100.0*p.pss_kb/s.total_ram_kb END) AS pss_ram_average_pct,
                AVG(p.uss_kb) AS uss_average_kb
                FROM process_sample p JOIN sample s ON s.id=p.sample_id GROUP BY p.process_name
                ORDER BY SUM(COALESCE(p.cpu_pct,0)) DESC LIMIT 50""")]
            summary = {"state": state, "sample_count": c.execute("SELECT COUNT(*) FROM sample").fetchone()[0],
                       "segment_count": c.execute("SELECT COUNT(*) FROM monkey_segment").fetchone()[0],
                       "started_at_ms": session["started_at_ms"], "ended_at_ms": ended_at_ms,
                       "metrics": metrics, "processes": processes,
                       "definitions": {"averages": "按实际采样间隔加权，单点最多计入3个采样间隔；丢失数据不按0处理。",
                                       "memory_used_pct": "系统内存占用率 = (MemTotal - MemAvailable) / MemTotal × 100%；同一份 /proc/meminfo 快照，包含可回收缓存的可用内存估计。缺失或无效时不使用 PSS / MemFree 替代。",
                                       "memory_pss_pct": "PSS / 物理RAM，不等同于系统已用内存率。",
                                       "cpu_sampled_share_pct": "该进程CPU采样值之和 / 全部已采到进程CPU值之和，不是整机CPU容量占比。",
                                       "processes": "仅展示CPU采样累计值最高的50个进程；完整明细保留在SQLite。"}}
            c.execute("UPDATE session SET state=?, ended_at_ms=?, summary_json=? WHERE id=1", (state, ended_at_ms, json.dumps(summary, ensure_ascii=False)))
        with connect(database) as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        destination = database.parent.parent / "summary.json"
        temp = destination.with_suffix(".tmp")
        temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(destination)
        self.export_combined_csv(run_id)
        return summary

    def export_combined_csv(self, run_id: str) -> Path:
        """Stream every archived sample into one CSV without holding the run in memory."""
        database = self.database(run_id)
        destination = database.parent.parent / "combined.csv"
        temporary = None
        try:
            with connect(database) as c, tempfile.NamedTemporaryFile(
                mode="w", newline="", encoding="utf-8-sig", dir=destination.parent,
                prefix=".combined-", suffix=".tmp", delete=False,
            ) as out:
                temporary = Path(out.name)
                columns = {row["name"] for row in c.execute("PRAGMA table_info(sample)")}
                select = ",".join(name if name in columns else f"NULL AS {name}" for name in CSV_FIELDS)
                segment = "segment_no" if "segment_no" in columns else "NULL AS segment_no"
                writer = csv.writer(out)
                writer.writerow(["segment_no", *CSV_FIELDS, "memory_pss_pct"])
                for row in c.execute(f"SELECT {segment},{select}, CASE WHEN total_ram_kb>0 THEN 100.0*pss_kb/total_ram_kb END FROM sample ORDER BY id"):
                    writer.writerow(tuple(row))
            temporary.replace(destination)
            return destination
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def tail(self, run_id: str) -> str:
        path = self.directory(run_id) / "monkey.log"
        if not path.exists():
            return ""
        with path.open("rb") as file:
            file.seek(max(0, path.stat().st_size - 8192))
            return file.read(8192).decode("utf-8", errors="replace")
