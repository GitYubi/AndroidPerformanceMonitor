"""HTML 报告生成测试。"""

from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path

from app.models import ProcessSample, SamplePayload
from app.report import generate_report
from app.storage import SessionStore


def _make_report_session(samples: int = 60) -> tuple[str, SessionStore]:
    root = Path(tempfile.mkdtemp())
    store = SessionStore(root)
    session_id = str(uuid.uuid4())
    writer = store.create_session(
        session_id,
        {
            "session_id": session_id,
            "serial": "42b86e9c",
            "created_at_ms": int(time.time() * 1000),
            "duration_seconds": 60,
            "interval_ms": 500,
            "enabled_metrics": {"cpu": True, "memory": True, "fps": True},
            "surface_layer": None,
        },
    )
    for index in range(samples):
        writer.write_sample(
            SamplePayload(
                ts_ms=1_000 * index,
                cpu_total_pct=10.0 + index % 20,
                pss_kb=100_000 + index,
                rss_kb=150_000 + index,
                fps=58.0 + (index % 5),
                app_render_fps=40.0 + (index % 30),
                app_jank_pct=5.0,
                frame_source="framestats",
                frame_count=60,
                jank_count=3,
                jank_pct=5.0,
                avg_frame_time_ms=14.0,
                p95_frame_time_ms=24.0,
                p99_frame_time_ms=40.0,
                processes=[ProcessSample("com.example.nav", 123, cpu_pct=5.0, pss_kb=100_000, rss_kb=150_000)],
            )
        )
    writer.add_event("info", "frame_source_switch", "帧率数据源切换为 framestats")
    writer.finish("completed")
    return session_id, store


def test_report_contains_overview_cards() -> None:
    session_id, store = _make_report_session()
    report = generate_report(session_id, store)
    for label in ["CPU 平均 / 峰值", "PSS 平均 / 峰值", "RSS 平均 / 峰值", "呈现 FPS 平均 / 最低", "最大丢帧率"]:
        assert label in report
    assert "Android 车机性能测试报告" in report


def test_report_contains_charts() -> None:
    session_id, store = _make_report_session()
    report = generate_report(session_id, store)
    for title in ["CPU 整体占用", "内存占用（PSS / RSS）", "呈现帧率（P）", "应用渲染帧率（R）", "逐帧 Jank", "帧耗时（avg / p95 / p99）"]:
        assert title in report
    assert "<polyline" in report  # SVG 折线


def test_report_contains_processes_and_events() -> None:
    session_id, store = _make_report_session()
    report = generate_report(session_id, store)
    assert "com.example.nav" in report
    assert "frame_source_switch" in report


def test_report_unknown_session_raises() -> None:
    _, store = _make_report_session()
    try:
        generate_report("not-exist", store)
        raise AssertionError("应抛出 ValueError")
    except ValueError:
        pass
