"""HTML 性能测试报告生成。

生成自包含 HTML（内嵌 SVG 折线图，离线可打开），数据来源可以是当前会话
或任意历史会话。报告内容：

- 总览卡片：CPU / PSS / RSS / 呈现 FPS / 丢帧率 的平均与峰值（用户指定项）
- 补充总览：应用渲染 FPS、逐帧 Jank、P95 帧耗时、数据源分布、事件统计
- 全周期折线图：CPU、PSS/RSS、呈现 FPS、应用渲染 FPS、逐帧 Jank、帧耗时
- 进程排行（CPU / PSS / RSS Top-N）与会话事件列表
"""

from __future__ import annotations

import html
import statistics
import time
from typing import Any

from .storage import SessionStore

# 单图最多渲染点数（抽稀目标）
_CHART_MAX_POINTS = 600
_CHART_WIDTH = 900
_CHART_HEIGHT = 200

_STATE_LABELS = {
    "completed": "完成",
    "stopped": "停止",
    "interrupted": "中断",
    "failed": "失败",
    "running": "运行中",
}


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _mi(value: int | float | None) -> float | None:
    return None if value is None else value / 1024


def _escape(value: Any) -> str:
    return html.escape(str(value))


def _downsample(values: list[float | None], max_points: int = _CHART_MAX_POINTS) -> list[float | None]:
    """等间隔抽稀，保留 None（缺测）位置。"""
    if len(values) <= max_points:
        return values
    step = len(values) / max_points
    result: list[float | None] = []
    for index in range(max_points):
        source = int(index * step)
        result.append(values[source] if source < len(values) else None)
    return result


def _svg_line_chart(title: str, series: list[tuple[str, list[float | None], str]], unit: str, description: str = "") -> str:
    """生成单张 SVG 折线图；series = [(名称, 值序列, 颜色), ...]。"""
    all_values = [value for _, values, _ in series for value in values if value is not None]
    if not all_values:
        note = f'<p class="meaning">{_escape(description)}</p>' if description else ""
        return f'<div class="chart"><h3>{_escape(title)}</h3><p class="empty">无有效数据</p>{note}</div>'
    y_min = min(all_values)
    y_max = max(all_values)
    if y_min == y_max:
        pad = max(abs(y_min) * 0.1, 1.0)
        y_min, y_max = y_min - pad, y_max + pad
    span = y_max - y_min

    def x(value_index: int, length: int) -> float:
        return 44 + value_index * (900 - 48) / max(1, length - 1)

    def y(value: float) -> float:
        return 12 + (_CHART_HEIGHT - 24) * (1 - (value - y_min) / span)

    length = max(len(values) for _, values, _ in series)
    polylines = ""
    for name, values, color in series:
        points = []
        for index, value in enumerate(values):
            if value is not None:
                points.append(f"{x(index, length):.1f},{y(value):.1f}")
        if points:
            polylines += (
                f'<polyline fill="none" stroke="{color}" stroke-width="1.5" '
                f'points="{" ".join(points)}"/>'
                f'<text x="44" y="{y(values[0] or y_min) - 6}" font-size="10" fill="{color}">{_escape(name)}</text>'
            )
    grid = ""
    for tick in range(5):
        ratio = tick / 4
        grid_y = 12 + (_CHART_HEIGHT - 24) * ratio
        value = y_max - span * ratio
        grid += (
            f'<line x1="44" y1="{grid_y:.1f}" x2="900" y2="{grid_y:.1f}" stroke="#334155" stroke-width="0.5"/>'
            f'<text x="4" y="{grid_y + 3:.1f}" font-size="9" fill="#94a3b8">{value:.1f}</text>'
        )
    note = f'<p class="meaning">{_escape(description)}</p>' if description else ""
    return (
        f'<div class="chart"><h3>{_escape(title)} <span class="unit">{_escape(unit)}</span></h3>'
        f'<svg viewBox="0 0 {_CHART_WIDTH} {_CHART_HEIGHT}" preserveAspectRatio="xMidYMid meet" '
        f'width="100%" height="auto" xmlns="http://www.w3.org/2000/svg">'
        f'{grid}{polylines}'
        f'<line x1="44" y1="{_CHART_HEIGHT - 12}" x2="900" y2="{_CHART_HEIGHT - 12}" stroke="#475569" stroke-width="1"/>'
        f'</svg>{note}</div>'
    )


def _overview_card(label: str, value_text: str, sub: str = "") -> str:
    return (
        f'<div class="card"><div class="card-label">{_escape(label)}</div>'
        f'<div class="card-value">{_escape(value_text)}</div>'
        f'<div class="card-sub">{_escape(sub)}</div></div>'
    )


def _summary_metric(summary: dict[str, Any], key: str) -> dict[str, Any]:
    return summary.get("metrics", {}).get(key, {})


def generate_report(session_id: str, store: SessionStore) -> str:
    """生成会话的性能测试报告 HTML。"""
    session = store.get_session(session_id)
    if session is None:
        raise ValueError("会话不存在")
    summary = session.get("summary", {})
    metrics = summary.get("metrics", {})
    samples = store.get_all_samples(session_id)
    events = store.get_events(session_id, limit=200)
    processes_cpu = store.get_processes(session_id, "cpu", 10)
    processes_pss = store.get_processes(session_id, "pss", 10)
    processes_rss = store.get_processes(session_id, "rss", 10)

    # ---------- 折线图数据（按时间正序） ----------
    def series_of(key: str, convert=None) -> list[float | None]:
        values: list[float | None] = []
        for sample in samples:
            value = sample.get(key)
            values.append(convert(value) if value is not None and convert else value)
        return values

    cpu_series = series_of("cpu_total_pct")
    pss_series = series_of("pss_kb", convert=_mi)
    rss_series = series_of("rss_kb", convert=_mi)
    present_fps_series = series_of("fps")
    render_fps_series = series_of("app_render_fps")
    jank_series = series_of("jank_pct")
    avg_frame_series = series_of("avg_frame_time_ms")
    p95_frame_series = series_of("p95_frame_time_ms")
    p99_frame_series = series_of("p99_frame_time_ms")

    # ---------- 总览 ----------
    cpu = _summary_metric(summary, "cpu")
    pss = _summary_metric(summary, "memory_pss")
    rss = _summary_metric(summary, "memory_rss")
    fps = _summary_metric(summary, "fps")
    render_fps = _summary_metric(summary, "app_render_fps")
    frame_jank = _summary_metric(summary, "frame_jank_pct")
    legacy_jank = _summary_metric(summary, "app_jank_pct")
    frame_p95 = _summary_metric(summary, "frame_p95")
    peak_jank = frame_jank.get("peak") if frame_jank.get("peak") is not None else legacy_jank.get("peak")

    # 数据源分布（按 frame_source 字段统计）
    source_counts: dict[str, int] = {}
    for sample in samples:
        source = sample.get("frame_source")
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1

    warning_count = sum(1 for event in events if event["severity"] in {"warning", "error"})
    duration_min = session.get("duration_seconds", 0) / 60
    started = session.get("started_at_ms") or session.get("created_at_ms") or 0
    ended = session.get("ended_at_ms")
    if ended and started:
        duration_min = (ended - started) / 60000

    # ---------- 拼装 HTML ----------
    state = _STATE_LABELS.get(session.get("state", ""), session.get("state", "未知"))
    parts: list[str] = []

    parts.append(
        f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>性能测试报告 · {_escape(session.get("serial", ""))}</title>
<style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:32px 16px;}}
.wrap{{max-width:960px;margin:0 auto;}}
header{{border-bottom:1px solid #334155;padding-bottom:16px;margin-bottom:24px;}}
h1{{font-size:20px;margin:0 0 8px;}} h2{{font-size:15px;color:#7dd3fc;margin:28px 0 12px;}} h3{{font-size:13px;margin:0 0 8px;color:#e2e8f0;}}
.meta{{color:#94a3b8;font-size:12px;line-height:1.8;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px 14px;}}
.card-label{{color:#94a3b8;font-size:11px;}} .card-value{{font-size:18px;font-weight:600;margin:4px 0;}}
.card-sub{{color:#64748b;font-size:10px;}} .unit{{color:#64748b;font-size:10px;font-weight:400;}}
.chart{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px 14px;margin-bottom:14px;}}
.empty{{color:#64748b;font-size:12px;}}
.meaning{{color:#64748b;font-size:10px;line-height:1.6;margin:6px 0 0;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #1e293b;}}
th{{color:#94a3b8;font-weight:500;}} td{{color:#cbd5e1;}}
.section-title{{display:flex;justify-content:space-between;align-items:center;}}
.badge{{background:#0f172a;border:1px solid #334155;color:#94a3b8;border-radius:4px;padding:2px 8px;font-size:11px;}}
.event{{padding:4px 0;font-size:12px;color:#94a3b8;}} .event .code{{color:#cbd5e1;font-family:monospace;}}
.warn{{color:#fbbf24;}} .err{{color:#f87171;}}
footer{{margin-top:32px;border-top:1px solid #334155;padding-top:12px;color:#64748b;font-size:11px;}}
</style></head><body><div class="wrap">
<header>
<h1>Android 车机性能测试报告</h1>
<div class="meta">
设备：{_escape(session.get("serial", ""))} ｜ 会话状态：{_escape(state)} ｜
开始：{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime((started or 0) / 1000))} ｜
周期：{duration_min:.1f} 分钟 ｜ 间隔：{(session.get("interval_ms") or 0) / 1000:.1f}s ｜
样本：{summary.get("sample_count", len(samples))} 点
</div></header>"""
    )

    # ---------- 总览卡片 ----------
    parts.append('<h2>测试总览</h2><div class="grid">')
    parts.append(_overview_card("CPU 平均 / 峰值", f'{_fmt(cpu.get("average"))}% / {_fmt(cpu.get("peak"))}%', f"整机 CPU 利用率（多核归一 0-100%）；平均=周期内每秒采样的均值，峰值=最高值。{cpu.get('valid_count', 0)} 个有效样本"))
    parts.append(_overview_card("PSS 平均 / 峰值", f'{_fmt(_mi(pss.get("average")))} / {_fmt(_mi(pss.get("peak")))} MiB', "按进程 PSS 加总。PSS=共享内存按比例分摊后的进程占用，更接近应用实际内存开销"))
    parts.append(_overview_card("RSS 平均 / 峰值", f'{_fmt(_mi(rss.get("average")))} / {_fmt(_mi(rss.get("peak")))} MiB', "按进程 RSS 加总。RSS=进程独占的物理内存（不摊共享），通常 ≥ PSS"))
    parts.append(_overview_card("呈现 FPS 平均 / 最低", f'{_fmt(fps.get("average"))} / {_fmt(fps.get("peak"))}', "屏幕呈现节奏（帧送上屏的速率）；平均=均值，最低=周期内最差表现。来自 FrameTimeline / framestats / SF latency 中最优可用源"))
    parts.append(_overview_card("最大丢帧率", f'{_fmt(peak_jank)}%', "窗口内卡顿帧占比的峰值（帧耗时超过两倍帧间隔）；逐帧口径，无则用 gfxinfo 计数口径"))
    parts.append(_overview_card("应用渲染 FPS 平均 / 最低", f'{_fmt(render_fps.get("average"))} / {_fmt(render_fps.get("peak"))}', "应用主动渲染帧率（gfxinfo 计数器增量）；静态界面不重绘时回落 0 属正常，非卡顿"))
    parts.append(_overview_card("逐帧 Jank 平均 / 峰值", f'{_fmt(frame_jank.get("average"))}% / {_fmt(frame_jank.get("peak"))}%', "窗口内帧耗时超过两倍帧间隔的帧占比；越高说明掉帧越频繁"))
    parts.append(_overview_card("逐帧 P95 帧耗时", f'{_fmt(frame_p95.get("average"))} ms', "帧耗时分布的 95 分位：95% 的帧在此耗时以内。60Hz 下超过 16.7ms 即可能掉帧"))
    parts.append(_overview_card("警告 / 错误事件", str(warning_count), "采样与日志导出过程中的异常记录数（权限不足、解析失败、数据源降级等）"))
    parts.append(_overview_card("帧率数据源", " / ".join(f"{k}×{v}" for k, v in sorted(source_counts.items())) or "未启用帧率", "按采样点统计实际使用的数据源（FrameTimeline / framestats / SF latency）"))
    parts.append("</div>")

    # ---------- 折线图 ----------
    parts.append('<h2>测试周期曲线</h2>')
    if samples:
        parts.append(_svg_line_chart("CPU 整体占用", [("CPU", _downsample(cpu_series), "#39d6d3")], "%", "整机 CPU 利用率（多核归一），来源 top 总体行。接近 100% 说明系统资源吃紧。"))
        parts.append(_svg_line_chart("内存占用（PSS / RSS）", [("PSS", _downsample(pss_series), "#88d66c"), ("RSS", _downsample(rss_series), "#34d399")], "MiB", "按进程加总的内存占用；PSS 摊共享内存、RSS 为独占物理内存。"))
        parts.append(_svg_line_chart("呈现帧率（P）", [("呈现 FPS", _downsample(present_fps_series), "#f4b942")], "fps", "屏幕呈现节奏（帧送上屏速率）。低于刷新率（60Hz）且持续时说明显示链路吃紧。"))
        parts.append(_svg_line_chart("应用渲染帧率（R）", [("渲染 FPS", _downsample(render_fps_series), "#7dd3fc")], "fps", "应用主动渲染帧率（gfxinfo 计数器增量）。静态界面回落 0 属正常；操作时持续偏低才是渲染性能问题。"))
        parts.append(_svg_line_chart("逐帧 Jank", [("Jank %", _downsample(jank_series), "#fb7185")], "%", "每秒窗口内帧耗时超过两倍帧间隔（约 33ms @60Hz）的占比。尖峰对应卡顿时刻。"))
        parts.append(_svg_line_chart("帧耗时（avg / p95 / p99）", [("avg", _downsample(avg_frame_series), "#94a3b8"), ("p95", _downsample(p95_frame_series), "#f472b6"), ("p99", _downsample(p99_frame_series), "#f87171")], "ms", "每秒窗口内帧耗时均值与分位数。60Hz 下帧耗时超过 16.7ms 意味着错过 vsync、可能出现掉帧；p95/p99 衡量尾延迟。"))
    else:
        parts.append('<p class="empty">该会话没有采样数据。</p>')

    # ---------- 进程排行 ----------
    def process_table(title: str, rows: list[dict[str, Any]], unit: str) -> str:
        body = "".join(
            f"<tr><td>{_escape(row['process_name'])}</td><td>{row.get('pid') or '—'}</td>"
            f"<td>{_fmt(row.get('average'), 1)} {unit}</td><td>{_fmt(row.get('peak'), 1)} {unit}</td>"
            f"<td>{row.get('samples', 0)}</td></tr>"
            for row in rows
        )
        return (
            f'<div class="chart"><h3>{_escape(title)}</h3>'
            "<table><tr><th>进程</th><th>PID</th><th>平均</th><th>峰值</th><th>样本数</th></tr>"
            f"{body}</table></div>"
        )

    if processes_cpu or processes_pss or processes_rss:
        parts.append('<h2>进程排行</h2>')
        if processes_cpu:
            parts.append(process_table("进程 CPU 占用排行", processes_cpu, "%"))
        if processes_pss:
            parts.append(process_table("进程 PSS 占用排行", processes_pss, "MiB"))
        if processes_rss:
            parts.append(process_table("进程 RSS 占用排行", processes_rss, "MiB"))

    # ---------- 事件 ----------
    if events:
        parts.append('<h2>会话事件</h2><div class="chart">')
        for event in events[:100]:
            tone = "err" if event["severity"] == "error" else "warn" if event["severity"] == "warning" else ""
            parts.append(
                f'<div class="event"><span class="code">{_escape(event["code"])}</span> '
                f'<span class="{tone}">[{_escape(event["severity"])}]</span> '
                f'{_escape(event["message"])}'
                f' <span style="color:#475569">{time.strftime("%H:%M:%S", time.localtime(event["ts_ms"] / 1000))}</span></div>'
            )
        parts.append("</div>")

    # ---------- 指标口径说明 ----------
    meanings = [
        ("CPU 整体", "%", "整机 CPU 利用率，多核总容量归一到 0-100%", "top 总体行（总容量 − idle）"),
        ("PSS", "MiB", "按进程 PSS 加总；共享内存按比例分摊，接近应用实际内存开销", "dumpsys meminfo"),
        ("RSS", "MiB", "按进程 RSS 加总；进程独占物理内存，不摊共享，通常 ≥ PSS", "dumpsys meminfo"),
        ("呈现 FPS", "fps", "帧送上屏幕的呈现节奏；低于刷新率且持续说明显示链路吃紧", "FrameTimeline / framestats / SF latency 最优可用源"),
        ("应用渲染 FPS", "fps", "应用主动渲染帧率；静态界面回落 0 属正常", "gfxinfo 计数器增量"),
        ("逐帧 Jank", "%", "窗口内帧耗时超过两倍帧间隔的帧占比；越高掉帧越频繁", "framestats / FrameTimeline 逐帧时间戳"),
        ("P95 / P99 帧耗时", "ms", "帧耗时分布分位数；60Hz 下超 16.7ms 意味着错过 vsync、可能掉帧", "逐帧时间戳计算"),
        ("丢帧率", "%", "窗口内卡顿帧占比峰值，衡量最差表现", "逐帧口径优先，回退 gfxinfo 计数"),
        ("警告 / 错误事件", "次", "采样与日志导出过程的异常记录数（权限、解析、降级等）", "会话事件表"),
    ]
    parts.append('<h2>指标口径说明</h2><div class="chart"><table>'
                 "<tr><th>指标</th><th>单位</th><th>含义</th><th>数据来源</th></tr>"
                 + "".join(
                     f"<tr><td>{_escape(name)}</td><td>{_escape(unit)}</td><td>{_escape(meaning)}</td><td>{_escape(source)}</td></tr>"
                     for name, unit, meaning, source in meanings
                 )
                 + "</table></div>")

    parts.append(
        f'<footer>生成时间：{time.strftime("%Y-%m-%d %H:%M:%S")} ｜ 会话 {_escape(session_id)} ｜ Android 车机性能监测工具</footer>'
        "</div></body></html>"
    )
    return "".join(parts)
