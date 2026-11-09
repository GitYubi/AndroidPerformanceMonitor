"""HTML 性能测试报告生成。

生成自包含 HTML（内嵌 SVG 折线图，离线可打开），数据来源可以是当前会话
或任意历史会话。报告内容：

- 总览卡片：CPU / PSS / 呈现 FPS / 丢帧率 的平均与峰值（用户指定项）
- 补充总览：应用渲染 FPS、逐帧 Jank、P95 帧耗时、数据源分布、事件统计
- 全周期折线图：CPU、PSS、呈现 FPS、应用渲染 FPS、逐帧 Jank、帧耗时
- 进程排行（CPU / PSS Top-N）、各应用 USS 趋势与会话事件列表
"""

from __future__ import annotations

import html
import statistics
import time
from typing import Any, Literal, get_args

from .storage import SessionStore

ReportMetric = Literal["cpu", "memory", "process_memory", "fps", "frames", "events"]
ReportTheme = Literal["light", "dark"]


_REPORT_PALETTES = {
    "dark": {
        "report-page": "#0f172a",
        "report-text": "#e2e8f0",
        "report-panel": "#1e293b",
        "report-border": "#334155",
        "report-accent": "#7dd3fc",
        "report-muted": "#94a3b8",
        "report-note": "#64748b",
        "report-value": "#cbd5e1",
        "report-axis": "#475569",
        "report-warning": "#fbbf24",
        "report-error": "#f87171",
        "series-cpu": "#39d6d3",
        "series-memory": "#34d399",
        "series-pss": "#88d66c",
        "series-fps": "#f4b942",
        "series-render": "#7dd3fc",
        "series-jank": "#fb7185",
        "series-frame-avg": "#94a3b8",
        "series-frame-p95": "#f472b6",
        "series-frame-p99": "#f87171",
    },
    "light": {
        "report-page": "#f4f7fb",
        "report-text": "#0f172a",
        "report-panel": "#fff",
        "report-border": "#cbd5e1",
        "report-accent": "#0369a1",
        "report-muted": "#475569",
        "report-note": "#475569",
        "report-value": "#1e293b",
        "report-axis": "#64748b",
        "report-warning": "#92400e",
        "report-error": "#b91c1c",
        "series-cpu": "#0e7490",
        "series-memory": "#047857",
        "series-pss": "#3f6212",
        "series-fps": "#92400e",
        "series-render": "#0369a1",
        "series-jank": "#be123c",
        "series-frame-avg": "#475569",
        "series-frame-p95": "#a21caf",
        "series-frame-p99": "#b91c1c",
    },
}

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
            f'<line x1="44" y1="{grid_y:.1f}" x2="900" y2="{grid_y:.1f}" stroke="var(--report-border)" stroke-width="0.5"/>'
            f'<text x="4" y="{grid_y + 3:.1f}" font-size="9" fill="var(--report-muted)">{value:.1f}</text>'
        )
    note = f'<p class="meaning">{_escape(description)}</p>' if description else ""
    return (
        f'<div class="chart"><h3>{_escape(title)} <span class="unit">{_escape(unit)}</span></h3>'
        f'<svg viewBox="0 0 {_CHART_WIDTH} {_CHART_HEIGHT}" preserveAspectRatio="xMidYMid meet" '
        f'width="100%" height="auto" xmlns="http://www.w3.org/2000/svg">'
        f'{grid}{polylines}'
        f'<line x1="44" y1="{_CHART_HEIGHT - 12}" x2="900" y2="{_CHART_HEIGHT - 12}" stroke="var(--report-axis)" stroke-width="1"/>'
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


def _build_app_uss_trends(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将同一应用的冒号子进程按快照求和，并计算简单线性趋势。"""
    grouped: dict[str, dict[int, int]] = {}
    for row in rows:
        process_name = str(row["process_name"])
        app_name = process_name.split(":", 1)[0]
        ts_ms = int(row["ts_ms"])
        snapshots = grouped.setdefault(app_name, {})
        snapshots[ts_ms] = snapshots.get(ts_ms, 0) + int(row["uss_kb"])

    trends: list[dict[str, Any]] = []
    for app_name, snapshots in grouped.items():
        points = sorted(snapshots.items())
        if len(points) < 2:
            continue
        start_ts = points[0][0]
        xs = [(ts - start_ts) / 60000 for ts, _ in points]
        values_mib = [value / 1024 for _, value in points]
        x_mean = statistics.fmean(xs)
        y_mean = statistics.fmean(values_mib)
        denominator = sum((value - x_mean) ** 2 for value in xs)
        slope = 0.0 if denominator == 0 else sum(
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(xs, values_mib)
        ) / denominator
        delta = values_mib[-1] - values_mib[0]
        duration_minutes = xs[-1]
        if len(points) < 5 or duration_minutes < 5:
            assessment = "样本不足"
        elif slope >= 0.5 and delta >= 5:
            assessment = "持续增长信号"
        elif slope <= -0.5 and delta <= -5:
            assessment = "下降"
        else:
            assessment = "基本稳定 / 波动"
        trends.append({
            "app_name": app_name,
            "values": values_mib,
            "samples": len(values_mib),
            "duration_minutes": duration_minutes,
            "average": statistics.fmean(values_mib),
            "peak": max(values_mib),
            "delta": delta,
            "slope": slope,
            "assessment": assessment,
        })
    return sorted(trends, key=lambda item: (item["slope"], item["delta"]), reverse=True)


def generate_report(
    session_id: str,
    store: SessionStore,
    *,
    metrics: list[ReportMetric] | None = None,
    theme: ReportTheme = "dark",
) -> str:
    """生成会话的性能测试报告 HTML。"""
    session = store.get_session(session_id)
    if session is None:
        raise ValueError("会话不存在")
    summary = session.get("summary", {})
    included = set(get_args(ReportMetric) if metrics is None else metrics)
    samples = store.get_all_samples(session_id)
    events = store.get_events(session_id, limit=200) if "events" in included else []
    processes_cpu = store.get_processes(session_id, "cpu", 10) if "cpu" in included else []
    processes_pss = store.get_processes(session_id, "pss", 10) if "process_memory" in included else []
    app_uss_trends = (
        _build_app_uss_trends(store.get_process_uss_series(session_id))
        if "process_memory" in included else []
    )

    # ---------- 折线图数据（按时间正序） ----------
    def series_of(key: str, convert=None) -> list[float | None]:
        values: list[float | None] = []
        for sample in samples:
            value = sample.get(key)
            values.append(convert(value) if value is not None and convert else value)
        return values

    cpu_series = series_of("cpu_total_pct")
    pss_series = series_of("pss_kb", convert=_mi)
    present_fps_series = series_of("fps")
    render_fps_series = series_of("app_render_fps")
    jank_series = series_of("jank_pct")
    avg_frame_series = series_of("avg_frame_time_ms")
    p95_frame_series = series_of("p95_frame_time_ms")
    p99_frame_series = series_of("p99_frame_time_ms")

    # PSS accounting is retained separately from kernel availability accounting.
    pss_percent_series: list[float | None] = []
    last_total_ram: int | None = None
    for sample in samples:
        if sample.get("total_ram_kb"):
            last_total_ram = sample["total_ram_kb"]
        pss = sample.get("pss_kb")
        pss_percent_series.append((pss / last_total_ram * 100) if (pss is not None and last_total_ram) else None)
    pss_percent_values = [value for value in pss_percent_series if value is not None]
    system_memory_series = series_of("memory_used_pct")

    # ---------- 总览 ----------
    cpu = _summary_metric(summary, "cpu")
    pss = _summary_metric(summary, "memory_pss")
    system_memory = _summary_metric(summary, "memory_used_pct")
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
    palette = _REPORT_PALETTES[theme]
    palette_css = ";".join(f"--{key}:{value}" for key, value in palette.items())

    parts.append(
        f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>性能测试报告 · {_escape(session.get("serial", ""))}</title>
<style>
:root,.wrap{{{palette_css};color-scheme:{theme};}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--report-page);color:var(--report-text);margin:0;padding:32px 16px;}}
.wrap{{max-width:960px;margin:0 auto;}}
header{{border-bottom:1px solid var(--report-border);padding-bottom:16px;margin-bottom:24px;}}
h1{{font-size:20px;margin:0 0 8px;}} h2{{font-size:15px;color:var(--report-accent);margin:28px 0 12px;}} h3{{font-size:13px;margin:0 0 8px;color:var(--report-text);}}
.meta{{color:var(--report-muted);font-size:12px;line-height:1.8;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;}}
.card{{background:var(--report-panel);border:1px solid var(--report-border);border-radius:8px;padding:12px 14px;}}
.card-label{{color:var(--report-muted);font-size:11px;}} .card-value{{font-size:18px;font-weight:600;margin:4px 0;}}
.card-sub{{color:var(--report-note);font-size:10px;}} .unit{{color:var(--report-note);font-size:10px;font-weight:400;}}
.chart{{background:var(--report-panel);border:1px solid var(--report-border);border-radius:8px;padding:12px 14px;margin-bottom:14px;}}
.uss-link{{color:var(--report-accent);text-decoration:none;}} .uss-link:hover{{text-decoration:underline;}}
.uss-chart{{background:var(--report-panel);border:1px solid var(--report-border);border-radius:8px;margin-bottom:10px;scroll-margin-top:16px;}}
.uss-chart summary{{cursor:pointer;color:var(--report-accent);padding:12px 14px;font-size:13px;}}
.uss-chart .chart{{border:0;margin:0;padding-top:0;}}
.empty{{color:var(--report-note);font-size:12px;}}
.meaning{{color:var(--report-note);font-size:10px;line-height:1.6;margin:6px 0 0;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--report-panel);}}
th{{color:var(--report-muted);font-weight:500;}} td{{color:var(--report-value);}}
.section-title{{display:flex;justify-content:space-between;align-items:center;}}
.badge{{background:var(--report-page);border:1px solid var(--report-border);color:var(--report-muted);border-radius:4px;padding:2px 8px;font-size:11px;}}
.event{{padding:4px 0;font-size:12px;color:var(--report-muted);}} .event .code{{color:var(--report-value);font-family:monospace;}}
.warn{{color:var(--report-warning);}} .err{{color:var(--report-error);}}
footer{{margin-top:32px;border-top:1px solid var(--report-border);padding-top:12px;color:var(--report-note);font-size:11px;}}
.capture-btn{{position:fixed;top:16px;right:16px;background:var(--report-panel);border:1px solid var(--report-border);color:var(--report-accent);border-radius:8px;padding:8px 14px;font-size:12px;cursor:pointer;z-index:10;font-family:inherit;}}
.capture-btn:hover{{background:var(--report-border);}}
</style></head><body>
<button class="capture-btn" data-capture-btn onclick="capturePNG()">保存为图片 (PNG)</button>
<div class="wrap">
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
    if "cpu" in included:
        parts.append(_overview_card("CPU 平均 / 峰值", f'{_fmt(cpu.get("average"))}% / {_fmt(cpu.get("peak"))}%', f"整机 CPU 利用率（多核归一 0-100%）；峰值为已采样值的最高值。{cpu.get('valid_count', 0)} 个有效样本"))
    if "process_memory" in included:
        parts.append(_overview_card("PSS 平均 / 峰值", f'{_fmt(_mi(pss.get("average")))} / {_fmt(_mi(pss.get("peak")))} MiB', "按进程查询的 PSS 总和。PSS=共享内存按比例分摊后的进程占用，更接近应用实际内存开销"))
    if "memory" in included:
        parts.append(_overview_card("系统内存占用率 平均 / 峰值", f'{_fmt(system_memory.get("average"))}% / {_fmt(system_memory.get("peak"))}%', "(MemTotal - MemAvailable) / MemTotal × 100%，来自同一份 /proc/meminfo 快照；MemAvailable 包含可回收缓存的可用内存估计。未采集时显示 —，不使用 PSS / MemFree 替代。"))
    pss_percent_summary = _summary_metric(summary, "memory_pss_pct")
    if "process_memory" in included and pss_percent_values:
        pss_average = pss_percent_summary.get("average") if pss_percent_summary.get("average") is not None else sum(pss_percent_values) / len(pss_percent_values)
        pss_peak = pss_percent_summary.get("peak") if pss_percent_summary.get("peak") is not None else max(pss_percent_values)
        parts.append(_overview_card(
            "PSS / RAM 平均 / 峰值",
            f'{_fmt(pss_average)}% / {_fmt(pss_peak)}%',
            "已采到进程的 PSS 总和占 Total RAM 的比例，用于进程内存分析，不等于系统内存占用率。",
        ))
    if "fps" in included:
        parts.append(_overview_card("呈现 FPS 平均 / 最低", f'{_fmt(fps.get("average"))} / {_fmt(fps.get("peak"))}', "屏幕呈现节奏（帧送上屏的速率）；平均=均值，最低=周期内最差表现。来自 FrameTimeline / framestats / SF latency 中最优可用源"))
    if "frames" in included:
        parts.append(_overview_card("最大丢帧率", f'{_fmt(peak_jank)}%', "窗口内卡顿帧占比的峰值（帧耗时超过两倍帧间隔）；逐帧口径，无则用 gfxinfo 计数口径"))
    if "fps" in included:
        parts.append(_overview_card("应用渲染 FPS 平均 / 最低", f'{_fmt(render_fps.get("average"))} / {_fmt(render_fps.get("peak"))}', "应用主动渲染帧率（gfxinfo 计数器增量）；静态界面不重绘时回落 0 属正常，非卡顿"))
    if "frames" in included:
        parts.append(_overview_card("逐帧 Jank 平均 / 峰值", f'{_fmt(frame_jank.get("average"))}% / {_fmt(frame_jank.get("peak"))}%', "窗口内帧耗时超过两倍帧间隔的帧占比；越高说明掉帧越频繁"))
        parts.append(_overview_card("逐帧 P95 帧耗时", f'{_fmt(frame_p95.get("average"))} ms', "帧耗时分布的 95 分位：95% 的帧在此耗时以内。60Hz 下超过 16.7ms 即可能掉帧"))
    if "events" in included:
        parts.append(_overview_card("警告 / 错误事件", str(warning_count), "采样与日志导出过程中的异常记录数（权限不足、解析失败、数据源降级等）"))
    if "fps" in included:
        parts.append(_overview_card("帧率数据源", " / ".join(f"{k}×{v}" for k, v in sorted(source_counts.items())) or "无可用帧率数据", ("按图表抽稀采样点统计" if getattr(store, "report_notice", None) else "按采样点统计") + "实际使用的数据源（FrameTimeline / framestats / SF latency）"))
    parts.append("</div>")

    # ---------- 折线图 ----------
    if included - {"events"}:
        parts.append('<h2>测试周期曲线</h2>')
        if getattr(store, "report_notice", None):
            parts.append('<p class="meaning">长时间曲线可能抽稀。完整采样见合并 CSV 和 SQLite。</p>')
        if samples:
            if "cpu" in included:
                parts.append(_svg_line_chart("CPU 整体占用", [("CPU", _downsample(cpu_series), "var(--series-cpu)")], "%", "整机 CPU 利用率（多核归一），来自 /proc/stat 差值；设备不支持时回退 top。"))
            if "memory" in included:
                parts.append(_svg_line_chart("系统内存占用率（基于 MemAvailable）", [("系统内存率", _downsample(system_memory_series), "var(--series-memory)")], "%", "(MemTotal - MemAvailable) / MemTotal × 100%；缺失时不绘制有效曲线。"))
            if "process_memory" in included:
                parts.append(_svg_line_chart("内存占用（PSS）", [("PSS", _downsample(pss_series), "var(--series-pss)")], "MiB", "按已采到进程加总的 PSS，共享内存按比例分摊，用于分析进程内存开销。"))
                parts.append(_svg_line_chart("进程 PSS / RAM 比例", [("PSS / RAM", _downsample(pss_percent_series), "var(--series-pss)")], "%", "PSS 占 Total RAM 的比例，不等同于系统内存占用率。"))
            if "fps" in included:
                parts.append(_svg_line_chart("呈现帧率（P）", [("呈现 FPS", _downsample(present_fps_series), "var(--series-fps)")], "fps", "屏幕呈现节奏（帧送上屏速率）。低于刷新率（60Hz）且持续时说明显示链路吃紧。"))
                parts.append(_svg_line_chart("应用渲染帧率（R）", [("渲染 FPS", _downsample(render_fps_series), "var(--series-render)")], "fps", "应用主动渲染帧率（gfxinfo 计数器增量）。静态界面回落 0 属正常；操作时持续偏低才是渲染性能问题。"))
            if "frames" in included:
                parts.append(_svg_line_chart("逐帧 Jank", [("Jank %", _downsample(jank_series), "var(--series-jank)")], "%", "每秒窗口内帧耗时超过两倍帧间隔（约 33ms @60Hz）的占比。尖峰对应卡顿时刻。"))
                parts.append(_svg_line_chart("帧耗时（avg / p95 / p99）", [("avg", _downsample(avg_frame_series), "var(--series-frame-avg)"), ("p95", _downsample(p95_frame_series), "var(--series-frame-p95)"), ("p99", _downsample(p99_frame_series), "var(--series-frame-p99)")], "ms", "每秒窗口内帧耗时均值与分位数。60Hz 下帧耗时超过 16.7ms 意味着错过 vsync、可能出现掉帧；p95/p99 衡量尾延迟。"))
        else:
            parts.append('<p class="empty">该会话没有采样数据。</p>')

    # ---------- 进程排行 ----------
    def process_table(title: str, rows: list[dict[str, Any]], unit: str, scale: float = 1.0) -> str:
        # get_processes 的 PSS 为 KiB，显示 MiB 时需除以 1024。
        def scaled(value: float | None) -> float | None:
            return None if value is None else value / scale

        body = "".join(
            f"<tr><td>{_escape(row['process_name'])}</td><td>{row.get('pid') or '—'}</td>"
            f"<td>{_fmt(scaled(row.get('average')), 1)} {unit}</td><td>{_fmt(scaled(row.get('peak')), 1)} {unit}</td>"
            f"<td>{row.get('samples', 0)}</td></tr>"
            for row in rows
        )
        return (
            f'<div class="chart"><h3>{_escape(title)}</h3>'
            "<table><tr><th>进程</th><th>PID</th><th>平均</th><th>峰值</th><th>样本数</th></tr>"
            f"{body}</table></div>"
        )

    if processes_cpu or processes_pss:
        parts.append('<h2>进程排行</h2>')
        if processes_cpu:
            parts.append(process_table("进程 CPU 占用排行", processes_cpu, "%", scale=1))
        if processes_pss:
            parts.append(process_table("进程 PSS 占用排行", processes_pss, "MiB", scale=1024))

    # ---------- 各应用 USS 趋势 ----------
    if app_uss_trends:
        parts.append('<h2>各应用 USS 内存趋势</h2>')
        parts.append('<div class="chart"><p class="meaning">USS 为应用独占物理内存。至少 5 分钟、5 个快照，且线性趋势不低于 +0.5 MiB/min、首尾增长不低于 5 MiB 时标记为持续增长信号。缓存、页面切换、GC 和进程重启都会造成波动，不能仅凭该信号判定内存泄漏。</p><table>'
                     '<tr><th>应用</th><th>平均</th><th>峰值</th><th>首尾变化</th><th>趋势</th><th>判断</th></tr>'
                     + "".join(
                         f"<tr><td><a class=\"uss-link\" href=\"#uss-{index}\" onclick=\"openUssTrend(event, 'uss-{index}')\">{_escape(item['app_name'])}</a></td><td>{_fmt(item['average'], 1)} MiB</td>"
                         f"<td>{_fmt(item['peak'], 1)} MiB</td><td>{item['delta']:+.1f} MiB</td>"
                         f"<td>{item['slope']:+.2f} MiB/min</td><td>{_escape(item['assessment'])}</td></tr>"
                         for index, item in enumerate(app_uss_trends)
                     ) + '</table></div>')
        for index, item in enumerate(app_uss_trends):
            parts.append(f'<details class="uss-chart" id="uss-{index}"><summary>{_escape(item["app_name"])} · USS 折线图</summary>')
            parts.append(_svg_line_chart(
                f"{item['app_name']} · USS",
                [("USS", _downsample(item["values"]), "var(--series-fps)")],
                "MiB",
                f"{item['samples']} 个真实内存快照；线性趋势 {item['slope']:+.2f} MiB/min，首尾变化 {item['delta']:+.1f} MiB。",
            ))
            parts.append('</details>')

    # ---------- 事件 ----------
    if events:
        parts.append('<h2>会话事件</h2><div class="chart">')
        for event in events[:100]:
            tone = "err" if event["severity"] == "error" else "warn" if event["severity"] == "warning" else ""
            parts.append(
                f'<div class="event"><span class="code">{_escape(event["code"])}</span> '
                f'<span class="{tone}">[{_escape(event["severity"])}]</span> '
                f'{_escape(event["message"])}'
                f' <span style="color:var(--report-axis)">{time.strftime("%H:%M:%S", time.localtime(event["ts_ms"] / 1000))}</span></div>'
            )
        parts.append("</div>")

    # ---------- 各项指标说明 ----------
    meanings = [
        ("cpu", "CPU 整体", "%", "整机 CPU 利用率，多核总容量归一到 0-100%", "/proc/stat 差值；不支持时回退 top"),
        ("memory", "系统内存占用率", "%", "(MemTotal - MemAvailable) / MemTotal × 100%；总内存为内核可管理内存，不以 PSS 或 MemFree 替代", "cat /proc/meminfo · MemTotal / MemAvailable"),
        ("process_memory", "PSS", "MiB", "已采到进程 PSS 总和；共享内存按比例分摊，用于进程内存分析，不等于系统已用内存", "dumpsys meminfo · TOTAL PSS BY PROCESS"),
        ("process_memory", "USS", "MiB", "应用独占物理内存（Private Dirty + Private Clean）；持续增长可作为泄漏排查线索，但不是泄漏结论", "dumpsys meminfo -a --local · 进程 TOTAL"),
        ("fps", "呈现 FPS", "fps", "帧送上屏幕的呈现节奏；低于刷新率且持续说明显示链路吃紧", "FrameTimeline / framestats / SF latency 最优可用源"),
        ("fps", "应用渲染 FPS", "fps", "应用主动渲染帧率；静态界面回落 0 属正常", "gfxinfo 计数器增量"),
        ("frames", "逐帧 Jank", "%", "窗口内帧耗时超过两倍帧间隔的帧占比；越高掉帧越频繁", "framestats / FrameTimeline 逐帧时间戳"),
        ("frames", "P95 / P99 帧耗时", "ms", "帧耗时分布分位数；60Hz 下超 16.7ms 意味着错过 vsync、可能掉帧", "逐帧时间戳计算"),
        ("frames", "丢帧率", "%", "窗口内卡顿帧占比峰值，衡量最差表现", "逐帧口径优先，回退 gfxinfo 计数"),
        ("events", "警告 / 错误事件", "次", "采样与日志导出过程的异常记录数（权限、解析、降级等）", "会话事件表"),
    ]
    parts.append('<h2>测试关键字说明</h2><div class="chart"><table>'
                 "<tr><th>指标</th><th>单位</th><th>含义</th><th>数据来源</th></tr>"
                 + "".join(
                     f"<tr><td>{_escape(name)}</td><td>{_escape(unit)}</td><td>{_escape(meaning)}</td><td>{_escape(source)}</td></tr>"
                     for group, name, unit, meaning, source in meanings if group in included
                 )
                 + "</table></div>")

    parts.append(
        f'<footer>生成时间：{time.strftime("%Y-%m-%d %H:%M:%S")} ｜ 会话 {_escape(session_id)} ｜ Android 车机性能监测工具</footer>'
        "</div>"
        "<script>"
        "function openUssTrend(event,id){"
        "var chart=document.getElementById(id);if(!chart)return;"
        "event.preventDefault();chart.open=true;chart.scrollIntoView({behavior:'smooth',block:'start'});"
        "}"
        "function capturePNG(){"
        "var wrap=document.querySelector('.wrap');"
        "var width=wrap.scrollWidth,height=wrap.scrollHeight;"
        "var cssText='';"
        "for(var i=0;i<document.styleSheets.length;i++){try{var rules=document.styleSheets[i].cssRules;"
        "for(var j=0;j<rules.length;j++){cssText+=rules[j].cssText;}}catch(e){}}"
        "var clone=wrap.cloneNode(true);"
        "var inherited=getComputedStyle(wrap);clone.style.color=inherited.color;clone.style.fontFamily=inherited.fontFamily;"
        "var btn=clone.querySelector('[data-capture-btn]');if(btn){btn.remove();}"
        "var svg='<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"'+width+'\" height=\"'+height+'\">'"
        "+'<foreignObject width=\"100%\" height=\"100%\">'"
        "+'<div xmlns=\"http://www.w3.org/1999/xhtml\"><style>'+cssText+'</style>'+clone.outerHTML+'</div>'"
        "+'</foreignObject></svg>';"
        "var img=new Image();"
        "img.onload=function(){"
        "var canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;"
        "var ctx=canvas.getContext('2d');"
        "ctx.fillStyle=getComputedStyle(document.body).backgroundColor;ctx.fillRect(0,0,width,height);"
        "ctx.drawImage(img,0,0);"
        "canvas.toBlob(function(blob){"
        "var a=document.createElement('a');a.href=URL.createObjectURL(blob);"
        "a.download='report_'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.png';"
        "a.click();"
        "},'image/png');"
        "};"
        "img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);"
        "}"
        "</script>"
        "</body></html>"
    )
    return "".join(parts)
