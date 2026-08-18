"""FPS 数据链路巡检：被动检测当前前台界面的 R / P / Jank 三个参数是否可测。

与主采样器不同，巡检不做自动操作：用户自己打开界面并操作，
工具实时判定三个指标的数据链路是否可用：

- R（应用渲染）：gfxinfo 计数器在该界面操作时是否增长（增量判定）
- P（呈现帧率）：逐帧数据源（FrameTimeline / framestats）是否产出呈现 FPS
- Jank（逐帧卡顿）：逐帧数据源是否有帧数据（frame_count > 0）

页面由 GET /fps-probe 直接返回（自包含 HTML + 轮询），不依赖前端构建。
"""

from __future__ import annotations

import asyncio

from .adb import (
    AdbError,
    choose_surface_layer,
    get_foreground_package,
    get_sdk_version,
    list_surface_layers,
    parse_gfxinfo_summary,
    parse_surface_latency_stats,
    run_adb,
)
from .frame_sources import capture_frame_metrics, SOURCE_LABELS

# 巡检状态：按前台包名跟踪 gfxinfo 计数基线，用于 R 线增量判定。
# 全局单设备场景（工具本就是单主机/单车机），包名变化时自动重置。
_probe_state: dict[str, object] = {}
_probe_lock = asyncio.Lock()


async def _probe_present(serial: str, package: str, sdk_version: int | None) -> tuple[bool, str, bool, str]:
    """按主工具同款降级链检测 P（呈现 FPS）与 J（逐帧 Jank）可测性。

    先试逐帧源（FrameTimeline / framestats），无结果时降级到
    SurfaceFlinger --latency（与 monitor._capture_fps 一致），
    保证非 View 渲染应用（如 mediacenter）也能判定 P 可用。
    返回 (p_ok, p_detail, jank_ok, jank_detail)。
    """
    stats = await capture_frame_metrics(serial, package, sdk_version)
    if stats is not None:
        source_label = SOURCE_LABELS.get(stats.source, stats.source)
        p_ok = stats.fps is not None
        p_detail = f"{stats.fps} fps @ {source_label}" if p_ok else f"无呈现时间戳 @ {source_label}"
        jank_ok = bool(stats.frame_count and stats.frame_count > 0)
        jank_detail = f"{stats.frame_count} 帧 / {stats.jank_pct}% jank @ {source_label}" if jank_ok else "无逐帧数据"
        return p_ok, p_detail, jank_ok, jank_detail

    # 降级：SF --latency 兜底（同主工具）
    try:
        layers = await list_surface_layers(serial)
        layer = choose_surface_layer(layers, package)
        if layer is None:
            return False, "逐帧源不可用且未找到可测 layer", False, "逐帧源不可用"
        output = await run_adb("shell", "dumpsys", "SurfaceFlinger", "--latency", layer, serial=serial, timeout_seconds=7)
        latency_stats = parse_surface_latency_stats(output)
    except AdbError:
        latency_stats = None
        layer = None
    if latency_stats is not None and latency_stats.fps is not None:
        p_detail = f"{latency_stats.fps} fps @ sf_latency（{layer}）"
        jank_ok = latency_stats.jank_pct is not None
        jank_detail = f"{latency_stats.jank_pct}% jank @ sf_latency" if jank_ok else "sf_latency 无 jank 数据"
        return True, p_detail, jank_ok, jank_detail
    return False, "逐帧源与 SF latency 均无可用数据", False, "无可用数据源"


async def probe_status(serial: str) -> dict[str, object]:
    """检测当前前台界面的 R / P / Jank 可测状态。"""
    async with _probe_lock:
        try:
            package = await get_foreground_package(serial)
        except AdbError as exc:
            return {"package": None, "r": False, "p": False, "jank": False, "note": f"ADB 不可用：{exc}"}
        if package is None:
            return {"package": None, "r": False, "p": False, "jank": False, "note": "无法识别前台应用（请先打开一个界面）"}

        if _probe_state.get("package") != package:
            _probe_state.clear()
            _probe_state["package"] = package

        # ---------- R：gfxinfo 计数器增量 ----------
        r_ok = False
        r_detail = "等待操作触发渲染…"
        try:
            counters = parse_gfxinfo_summary(
                await run_adb("shell", "dumpsys", "gfxinfo", package, serial=serial, timeout_seconds=10)
            )
        except AdbError as exc:
            counters = None
            r_detail = f"gfxinfo 命令失败：{exc}"
        if counters is not None:
            previous = _probe_state.get("counter")
            if isinstance(previous, int) and counters[0] > previous:
                r_ok = True
                r_detail = f"计数增量 {counters[0] - previous}"
            else:
                r_detail = f"计数未增长（当前 {counters[0]}）——该界面渲染可能不经过 gfxinfo，请操作后确认"
            _probe_state["counter"] = counters[0]
        else:
            r_detail = "gfxinfo 无计数段（应用从未产生 HWUI 帧）"

        # ---------- P / Jank：逐帧源 → SF latency 兜底 ----------
        try:
            sdk_version = await get_sdk_version(serial)
            p_ok, p_detail, jank_ok, jank_detail = await _probe_present(serial, package, sdk_version)
        except AdbError:
            p_ok, jank_ok = False, False
            p_detail = jank_detail = "检测失败（ADB 不可用）"

        return {
            "package": package,
            "r": r_ok,
            "p": p_ok,
            "jank": jank_ok,
            "r_detail": r_detail,
            "p_detail": p_detail,
            "jank_detail": jank_detail,
            "note": "",
        }


PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>FPS 数据链路巡检</title>
<style>
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",monospace;background:#0f172a;color:#e2e8f0;margin:0;padding:24px;display:flex;justify-content:center;}
.wrap{width:640px;max-width:100%;}
h1{font-size:16px;margin:0 0 4px;color:#7dd3fc;}
.sub{color:#64748b;font-size:11px;margin-bottom:16px;}
.card{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px;margin-bottom:12px;}
.row{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #1e293b;}
.row:last-child{border-bottom:none;}
.name{width:120px;color:#94a3b8;font-size:13px;}
.status{width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;flex-shrink:0;}
.ok{background:#052e16;color:#4ade80;border:2px solid #4ade80;}
.fail{background:#450a0a;color:#f87171;border:2px solid #f87171;}
.detail{color:#64748b;font-size:11px;margin-top:2px;}
.pkg{font-size:18px;font-weight:600;color:#e2e8f0;word-break:break-all;}
.pkg-label{color:#94a3b8;font-size:11px;margin-bottom:4px;}
select{background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:6px 10px;font-size:12px;font-family:monospace;}
.note{color:#fbbf24;font-size:11px;margin-top:10px;}
</style></head><body><div class="wrap">
<h1>FPS 数据链路巡检</h1>
<div class="sub">打开目标界面并操作（滑动/点击触发渲染），观察 R / P / Jank 是否可测。不做自动操作。</div>
<div class="card">
  <div class="pkg-label">目标设备</div>
  <select id="serial"></select>
</div>
<div class="card">
  <div class="pkg-label">当前前台包名</div>
  <div class="pkg" id="pkg">—</div>
  <div class="note" id="note"></div>
</div>
<div class="card" id="rows">
  <div class="row"><div class="name">R · 应用渲染</div><div class="status fail" id="r">✗</div><div><div style="font-size:13px;color:#cbd5e1;">gfxinfo 计数增量</div><div class="detail" id="r-detail">—</div></div></div>
  <div class="row"><div class="name">P · 呈现帧率</div><div class="status fail" id="p">✗</div><div><div style="font-size:13px;color:#cbd5e1;">呈现时间戳 → FPS</div><div class="detail" id="p-detail">—</div></div></div>
  <div class="row"><div class="name">J · 逐帧 Jank</div><div class="status fail" id="j">✗</div><div><div style="font-size:13px;color:#cbd5e1;">逐帧数据源帧数</div><div class="detail" id="j-detail">—</div></div></div>
</div>
</div>
<script>
const API = location.origin;
let current = "";
async function loadDevices() {
  try {
    const list = await (await fetch(API + "/api/devices")).json();
    const sel = document.getElementById("serial");
    sel.innerHTML = "";
    for (const d of list.filter(d => d.state === "device")) {
      const opt = document.createElement("option");
      opt.value = d.serial;
      opt.text = (d.model || d.serial) + " · " + d.serial;
      sel.appendChild(opt);
    }
    if (list.some(d => d.state === "device") && !current) current = sel.value;
  } catch (e) { document.getElementById("note").textContent = "无法连接后端"; }
}
async function poll() {
  const serial = document.getElementById("serial").value;
  if (!serial) return;
  try {
    const r = await (await fetch(API + "/api/fps-probe/status?serial=" + encodeURIComponent(serial))).json();
    document.getElementById("pkg").textContent = r.package || "（未识别前台应用）";
    document.getElementById("note").textContent = r.note || "";
    set("r", r.r, r.r_detail); set("p", r.p, r.p_detail); set("j", r.jank, r.jank_detail);
  } catch (e) { /* ignore */ }
}
function set(id, ok, detail) {
  const el = document.getElementById(id);
  el.textContent = ok ? "✓" : "✗";
  el.className = "status " + (ok ? "ok" : "fail");
  document.getElementById(id + "-detail").textContent = detail || "—";
}
document.getElementById("serial").addEventListener("change", e => { current = e.target.value; poll(); });
loadDevices();
setInterval(poll, 1000);
</script></body></html>"""
