#!/usr/bin/env python3
"""FPS 可测性巡检工具。

遍历车机各界面（应用），逐个检测该界面的 FPS 数据链路是否可用，
并给出原因分类，用于排查"哪些界面拿不到 FPS 信息"。

用法（在测试主机上，使用 backend 的 venv）：

    # 指定界面（包名，逗号分隔）
    backend/.venv/bin/python tools/fps_probe.py --serial 42b86e9c \
        --packages com.baic.icc.launcher,com.android.settings

    # 自动扫描车机 HMI 相关包（含 baic/icc/adayo/launcher/settings/media 等关键词）
    backend/.venv/bin/python tools/fps_probe.py --serial 42b86e9c --auto

    # JSON 输出（便于接入其他脚本）
    backend/.venv/bin/python tools/fps_probe.py --serial 42b86e9c --packages ... --json

每个界面检测流程：monkey 启动 → 等待成为前台 → 读 gfxinfo 计数基线 →
滑动 3 次触发渲染 → 读计数增量与 framestats → 判定结论。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

# 复用后端解析与 ADB 执行逻辑，保证与主工具口径一致
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.adb import (  # noqa: E402
    AdbError,
    get_foreground_package,
    parse_framestats,
    parse_gfxinfo_summary,
    run_adb,
)

# 自动扫描时的包名关键词（车机 HMI 常见）
_AUTO_KEYWORDS = ("baic", "icc", "adayo", "launcher", "settings", "media", "navi", "music", "video", "weather")

_CONCLUSION_OK = "可测（R 线可用）"
_CONCLUSION_NO_COUNTER = "计数器不增长（纯合成动画 / SurfaceView / 静态界面，R 线拿不到）"
_CONCLUSION_NO_DATA = "gfxinfo 与 framestats 均无数据（可能权限受限或非 HWUI 渲染）"
_CONCLUSION_LAUNCH_FAILED = "启动失败或未成为前台"
_CONCLUSION_GFXINFO_MISSING = "gfxinfo 无计数段（应用从未产生 HWUI 帧）"


async def _wait_foreground(serial: str, package: str, timeout_seconds: float) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            foreground = await get_foreground_package(serial)
        except AdbError:
            foreground = None
        if foreground == package:
            return foreground
        await asyncio.sleep(0.8)
    return foreground


async def _screen_size(serial: str) -> tuple[int, int]:
    output = await run_adb("shell", "wm", "size", serial=serial, timeout_seconds=5)
    match = None
    for line in output.splitlines():
        if "Physical size" in line:
            part = line.split(":", 1)[1].strip().split("x")
            match = (int(part[0]), int(part[1]))
            break
    return match or (1920, 1080)


async def _swipe(serial: str, width: int, height: int, times: int = 3) -> None:
    for _ in range(times):
        try:
            await run_adb(
                "shell", "input", "swipe",
                str(int(width * 0.75)), str(int(height * 0.6)),
                str(int(width * 0.25)), str(int(height * 0.6)),
                "300",
                serial=serial, timeout_seconds=10,
            )
        except AdbError:
            pass
        await asyncio.sleep(0.5)


async def _launch_package(serial: str, package: str) -> str | None:
    """启动应用主界面；依次尝试 LAUNCHER monkey → 普通 monkey → am start。

    返回 None 表示全部尝试失败（或返回简短错误信息）。
    """
    attempts = [
        ("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"),
        ("shell", "monkey", "-p", package, "1"),
        ("shell", "am", "start", "-n", f"{package}/.MainActivity"),
    ]
    for attempt in attempts:
        try:
            await run_adb(*attempt, serial=serial, timeout_seconds=15)
            return None
        except AdbError:
            continue
    return "无法通过 monkey/am 启动（可能无 LAUNCHER 入口）"


async def probe_interface(serial: str, package: str, wait_seconds: float = 6.0) -> dict[str, object]:
    """检测单个界面的 FPS 数据链路可用性。"""
    result: dict[str, object] = {"package": package}
    launch_error = await _launch_package(serial, package)
    if launch_error:
        result["conclusion"] = f"启动命令失败：{launch_error}"
        return result

    # 2. 等待成为前台
    foreground = await _wait_foreground(serial, package, wait_seconds)
    result["foreground"] = foreground
    if foreground != package:
        result["conclusion"] = _CONCLUSION_LAUNCH_FAILED
        return result

    # 3. 计数基线
    try:
        baseline = parse_gfxinfo_summary(await run_adb("shell", "dumpsys", "gfxinfo", package, serial=serial, timeout_seconds=10))
    except AdbError as exc:
        result["conclusion"] = f"gfxinfo 命令失败：{exc}"
        return result
    result["counter_baseline"] = baseline[0] if baseline else None
    if baseline is None:
        result["conclusion"] = _CONCLUSION_GFXINFO_MISSING
        return result

    # 4. 滑动触发渲染
    width, height = await _screen_size(serial)
    await _swipe(serial, width, height, times=3)

    # 5. 计数增量 + framestats
    try:
        after = parse_gfxinfo_summary(await run_adb("shell", "dumpsys", "gfxinfo", package, serial=serial, timeout_seconds=10))
    except AdbError:
        after = None
    delta = (after[0] - baseline[0]) if (after and baseline) else None
    result["counter_after"] = after[0] if after else None
    result["counter_delta"] = delta

    fs_before = await _framestats_hash_and_data(serial, package)
    await _swipe(serial, width, height, times=3)
    fs_after = await _framestats_hash_and_data(serial, package)
    result["framestats"] = {
        "has_data": fs_after["has_data"],
        "updates_on_swipe": fs_before["hash"] != fs_after["hash"],
    }

    # 6. 判定
    if delta is not None and delta > 0:
        result["conclusion"] = _CONCLUSION_OK
    elif fs_after["has_data"]:
        result["conclusion"] = _CONCLUSION_NO_COUNTER
    else:
        result["conclusion"] = _CONCLUSION_NO_DATA
    return result


async def _framestats_hash_and_data(serial: str, package: str) -> dict[str, object]:
    try:
        output = await run_adb("shell", "dumpsys", "gfxinfo", package, "framestats", serial=serial, timeout_seconds=10)
    except AdbError:
        return {"has_data": False, "hash": None}
    stats = parse_framestats(output)
    return {"has_data": stats is not None, "hash": hashlib.sha1(output.encode("utf-8", "replace")).hexdigest()[:12]}


async def list_candidate_packages(serial: str, keywords: tuple[str, ...]) -> list[str]:
    """扫描已安装包中命中关键词的车机 HMI 应用（含系统应用）。"""
    output = await run_adb("shell", "pm", "list", "packages", serial=serial, timeout_seconds=15)
    packages = [line.replace("package:", "").strip() for line in output.splitlines() if line.startswith("package:")]
    matched = sorted({pkg for pkg in packages if any(keyword in pkg.lower() for keyword in keywords)})
    # 补充系统 launcher / settings
    for pkg in ("com.android.launcher3", "com.android.settings"):
        if pkg in packages and pkg not in matched:
            matched.append(pkg)
    return matched


async def main() -> None:
    parser = argparse.ArgumentParser(description="车机 FPS 可测性巡检")
    parser.add_argument("--serial", required=True, help="ADB 设备序列号")
    parser.add_argument("--packages", help="待检测包名列表，逗号分隔")
    parser.add_argument("--auto", action="store_true", help="自动扫描车机 HMI 相关包")
    parser.add_argument("--wait", type=float, default=6.0, help="每界面等待前台时间（秒）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if args.packages:
        packages = [pkg.strip() for pkg in args.packages.split(",") if pkg.strip()]
    elif args.auto:
        packages = await list_candidate_packages(args.serial, _AUTO_KEYWORDS)
        print(f"自动扫描到 {len(packages)} 个候选界面：{', '.join(packages[:20])}" + (" ..." if len(packages) > 20 else ""), file=sys.stderr)
    else:
        parser.error("请提供 --packages 或 --auto")

    results = []
    for package in packages:
        print(f"\n→ 检测 {package} ...", file=sys.stderr, flush=True)
        result = await probe_interface(args.serial, package, args.wait)
        results.append(result)
        print(f"  结论：{result['conclusion']}", file=sys.stderr)

    # 回到桌面，避免停在最后一个界面
    try:
        await run_adb("shell", "input", "keyevent", "3", serial=args.serial, timeout_seconds=5)
    except AdbError:
        pass

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print("\n=== 巡检汇总 ===")
    print(f"设备：{args.serial}｜共 {len(results)} 个界面")
    for result in results:
        mark = "✓" if result["conclusion"] == _CONCLUSION_OK else "✗"
        detail = ""
        if result["conclusion"] != _CONCLUSION_OK and result.get("counter_delta") is not None:
            detail = f"（计数增量 {result['counter_delta']}，framestats 数据 {'有' if result.get('framestats', {}).get('has_data') else '无'}，滑动时{'更新' if result.get('framestats', {}).get('updates_on_swipe') else '不更新'}）"
        elif result["conclusion"] == _CONCLUSION_OK:
            detail = f"（计数增量 {result['counter_delta']}）"
        print(f"{mark} {result['package']:45s} {result['conclusion']}{detail}")


if __name__ == "__main__":
    asyncio.run(main())
