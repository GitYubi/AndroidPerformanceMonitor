"""HTML 报告生成测试。"""

from __future__ import annotations

import asyncio
from contextlib import closing
import sqlite3
import tempfile
import time
import uuid
from xml.etree import ElementTree
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.main import session_report
from app.models import ProcessSample, SamplePayload
from app.report import _build_app_uss_trends, _chart_indices, _svg_line_chart, generate_report
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
                processes=[ProcessSample("com.example.nav:renderer" if index % 2 else "com.example.nav", 123, cpu_pct=5.0, pss_kb=100_000, rss_kb=150_000, uss_kb=60_000 + index * 100)],
            )
        )
    writer.add_event("info", "frame_source_switch", "帧率数据源切换为 framestats")
    writer.finish("completed")
    return session_id, store


def test_report_contains_overview_cards() -> None:
    session_id, store = _make_report_session()
    report = generate_report(session_id, store)
    for label in ["CPU 平均 / 峰值", "PSS 平均 / 峰值", "呈现 FPS 平均 / 最低", "最大丢帧率"]:
        assert label in report
    assert "RSS 平均 / 峰值" not in report
    assert "Android 车机性能测试报告" in report


def test_report_contains_charts() -> None:
    session_id, store = _make_report_session()
    report = generate_report(session_id, store)
    for title in ["CPU 整体占用", "内存占用（PSS）", "呈现帧率（P）", "应用渲染帧率（R）", "逐帧 Jank", "帧耗时（avg / p95 / p99）", "各应用 USS 内存趋势", "com.example.nav · USS"]:
        assert title in report
    assert "<polyline" in report  # SVG 折线


def test_report_chart_breaks_lines_at_missing_points():
    chart = _svg_line_chart("样本", [("样本", [0, 1, None, 4, 5, None, 8, None], "black")], "%")
    root = ElementTree.fromstring(chart)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    polylines = root.findall(".//svg:polyline", ns)
    assert [len(line.attrib["points"].split()) for line in polylines] == [2, 2]
    assert len(root.findall(".//svg:circle", ns)) == 1
    assert "无有效数据" in _svg_line_chart("空", [("样本", [None], "black")], "%")


def test_uss_uses_snapshot_times_and_keeps_missing_and_restart_gaps():
    stamps = [0, 1000, 9000, 10000, 14000, 30000, 60000]
    values = [1, 2, None, 3, 4, 5, 6]
    rows = [
        dict(ts_ms=stamp, process_name="demo", pid=40 if i < 5 else 41,
             uss_kb=value * 1024 if value is not None else None)
        for i, (stamp, value) in enumerate(zip(stamps, values))
    ]
    trend, = _build_app_uss_trends(rows)
    assert trend["times_ms"] == [0, 1000, 9000, 10000, 14000, 30000, 30000, 60000]
    assert trend["values"] == [1, 2, None, 3, 4, None, 5, 6]
    assert trend["samples"] == 6 and trend["duration_minutes"] == 1
    chart = _svg_line_chart("USS", [("USS", trend["values"], "black")],
                            "MiB", times_ms=trend["times_ms"])
    root = ElementTree.fromstring(chart)
    lines = root.findall(".//{http://www.w3.org/2000/svg}polyline")
    starts = [float(line.attrib["points"].split()[0].split(",")[0]) for line in lines]
    assert starts == [44.0, 186.0, 470.0]
    assert "60.0 s" in chart


def test_long_charts_share_selected_indices_and_keep_extremes():
    cpu = [2] * 2049
    cpu[199] = 200
    cpu[1001] = -20
    other = [1] * 2049
    other[700] = 10
    other[1743] = -100
    series = [("cpu", cpu, "black"), ("other", other, "white")]
    selected = _chart_indices(series, len(cpu))
    assert len(selected) <= 600
    assert {0, 199, 700, 1001, 1743, 2048} <= selected
    chart = _svg_line_chart("long", series, "%", times_ms=list(range(len(cpu))))
    root = ElementTree.fromstring(chart)
    lines = root.findall(".//{http://www.w3.org/2000/svg}polyline")
    x_axes = [[point.split(",")[0] for point in line.attrib["points"].split()] for line in lines]
    assert x_axes[0] == x_axes[1]
    assert x_axes[0][-1] == "896.0"


@pytest.mark.parametrize(("statement", "payload"), [
    ("UPDATE session SET summary_json=?", '{"sample_count":"<script id=summary></script>"}'),
    ("UPDATE process_sample SET pid=?", "<script id=pid></script>"),
])
def test_import_rejects_unsafe_report_fields(tmp_path, statement, payload):
    session_id, source = _make_report_session(samples=1)
    path = source.database_path(session_id)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(statement, (payload,))

    destination = SessionStore(tmp_path)
    with pytest.raises(ValueError):
        destination.import_database(path.read_bytes())
    assert not list(tmp_path.iterdir())


def test_report_escapes_legacy_unsafe_fields():
    session_id, store = _make_report_session(samples=1)
    path = store.database_path(session_id)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE session SET summary_json=?", ('{"sample_count":"<script id=summary></script>"}',))
        connection.execute("UPDATE process_sample SET pid=?", ("<script id=pid></script>",))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(manager=SimpleNamespace(store=store))))
    response = asyncio.run(session_report(session_id, request, download=0, metrics=["cpu"]))

    class ScriptProbe(HTMLParser):
        scripts = []

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.scripts.append(dict(attrs).get("id"))

    parser = ScriptProbe()
    parser.feed(response.body.decode())
    assert parser.scripts == [None]  # the report's built-in capture script


def test_uss_links_point_to_initially_collapsed_charts() -> None:
    session_id, store = _make_report_session()
    report = generate_report(session_id, store)

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
            self.details = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "a" and attributes.get("class") == "uss-link":
                self.links.append(attributes)
            if tag == "details" and attributes.get("class") == "uss-chart":
                self.details.append(attributes)

    parser = Parser()
    parser.feed(report)
    assert len(parser.links) == len(parser.details) == 1
    assert parser.links[0]["href"] == "#" + parser.details[0]["id"]
    assert "open" not in parser.details[0]
    assert "chart.open=true" in report and "chart.scrollIntoView" in report


def test_report_contains_processes_and_events() -> None:
    session_id, store = _make_report_session()
    report = generate_report(session_id, store)
    assert "com.example.nav" in report
    assert "frame_source_switch" in report


def test_selected_metrics_filter_summary_charts_rankings_and_event_details() -> None:
    session_id, store = _make_report_session()
    report = generate_report(session_id, store, metrics=["cpu", "memory"], theme="light")

    for title in ("CPU 平均 / 峰值", "系统内存占用率 平均 / 峰值", "CPU 整体占用", "进程 CPU 占用排行"):
        assert title in report
    for title in ("PSS 平均 / 峰值", "进程 PSS 占用排行", "各应用 USS 内存趋势",
                  "呈现帧率（P）", "帧耗时（avg / p95 / p99）", "frame_source_switch", "<td>USS</td>"):
        assert title not in report
    assert "color-scheme:light" in report


def test_report_unknown_session_raises() -> None:
    _, store = _make_report_session()
    try:
        generate_report("not-exist", store)
        raise AssertionError("应抛出 ValueError")
    except ValueError:
        pass
