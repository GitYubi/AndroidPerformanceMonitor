"""多数据源逐帧帧率采集：能力探测 + 按版本自动降级。

目标车机 Android 版本可能从 10 到 12 不等，单一数据源不可靠。
本模块按 SDK 版本与实时探测结果决定使用哪个逐帧数据源，采样过程中
某个源失败时自动回退到下一优先级，保证在任意版本上都有可用读数：

| 优先级 | 数据源                    | Android 版本   | 提供维度                                   |
| ------ | ------------------------- | -------------- | ------------------------------------------ |
| 1      | FrameTimeline             | 12+ (API 31+)  | 显示帧呈现节奏 + 卡顿分类（含 SF/App 原因）|
| 2      | gfxinfo framestats        | 7+  (API 24+)  | 应用逐帧渲染/呈现时间戳 + 输入延迟          |
| 3      | SurfaceFlinger --latency  | 4.2+ (API 17+) | 单 layer 呈现节奏（全版本兜底）             |
| 4      | gfxinfo 累计计数器        | 4.2+ (API 17+) | 渲染 FPS / Jank 增量（最后兜底，monitor 内）|
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .adb import (
    AdbError,
    FrameStats,
    get_foreground_package,
    get_sdk_version,
    parse_frame_timeline,
    parse_framestats,
    run_adb,
)

FRAME_SOURCE_FRAMETIMELINE = "frametimeline"
FRAME_SOURCE_FRAMESTATS = "framestats"
FRAME_SOURCE_SF_LATENCY = "sf_latency"
FRAME_SOURCE_GFXINFO = "gfxinfo"

MIN_SDK_FRAMETIMELINE = 31  # Android 12
MIN_SDK_FRAMESTATS = 24  # Android 7

SOURCE_LABELS: dict[str, str] = {
    FRAME_SOURCE_FRAMETIMELINE: "FrameTimeline（Android 12+）",
    FRAME_SOURCE_FRAMESTATS: "gfxinfo framestats（Android 7+）",
    FRAME_SOURCE_SF_LATENCY: "SurfaceFlinger --latency（全版本）",
    FRAME_SOURCE_GFXINFO: "gfxinfo 累计计数器（全版本）",
}


@dataclass(slots=True)
class FrameCapabilities:
    """设备上各帧率数据源的可用性与推荐选择。"""

    sdk_version: int | None
    android_release: str | None
    foreground_package: str | None
    sources: dict[str, bool] = field(default_factory=dict)
    recommended_source: str = FRAME_SOURCE_SF_LATENCY
    notes: list[str] = field(default_factory=list)

    def serializable(self) -> dict[str, object]:
        return asdict(self)


def source_priority(sdk_version: int | None, preferred: str | None = None) -> list[str]:
    """按 SDK 版本生成数据源尝试顺序；preferred 优先。

    未知 SDK 版本时按“两者都尝试”处理（探测失败不会比不探测更糟）。
    """
    order: list[str] = []
    if sdk_version is None or sdk_version >= MIN_SDK_FRAMETIMELINE:
        order.append(FRAME_SOURCE_FRAMETIMELINE)
    if sdk_version is None or sdk_version >= MIN_SDK_FRAMESTATS:
        order.append(FRAME_SOURCE_FRAMESTATS)
    order.append(FRAME_SOURCE_SF_LATENCY)
    if preferred and preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    return order


def pick_recommended_source(sdk_version: int | None, sources: dict[str, bool]) -> str:
    if (sdk_version is None or sdk_version >= MIN_SDK_FRAMETIMELINE) and sources.get(FRAME_SOURCE_FRAMETIMELINE):
        return FRAME_SOURCE_FRAMETIMELINE
    if (sdk_version is None or sdk_version >= MIN_SDK_FRAMESTATS) and sources.get(FRAME_SOURCE_FRAMESTATS):
        return FRAME_SOURCE_FRAMESTATS
    return FRAME_SOURCE_SF_LATENCY


async def probe_frame_capabilities(serial: str) -> FrameCapabilities:
    """只读探测车机支持的帧率数据源，供界面展示与用户确认。"""

    notes: list[str] = []
    sdk_version = await get_sdk_version(serial)
    try:
        release = (await run_adb("shell", "getprop", "ro.build.version.release", serial=serial, timeout_seconds=5)).strip() or None
    except AdbError:
        release = None
    try:
        package = await get_foreground_package(serial)
    except AdbError:
        package = None

    sources: dict[str, bool] = {
        FRAME_SOURCE_FRAMETIMELINE: False,
        FRAME_SOURCE_FRAMESTATS: False,
        FRAME_SOURCE_SF_LATENCY: True,
    }

    if sdk_version is None or sdk_version >= MIN_SDK_FRAMETIMELINE:
        try:
            output = await run_adb("shell", "dumpsys", "SurfaceFlinger", "--frametimeline", "-all", serial=serial, timeout_seconds=8)
            sources[FRAME_SOURCE_FRAMETIMELINE] = parse_frame_timeline(output) is not None
            if not sources[FRAME_SOURCE_FRAMETIMELINE]:
                notes.append("FrameTimeline 命令可用但暂无可解析的显示帧（静止画面或厂商裁剪），采样时将自动回退。")
        except AdbError as exc:
            notes.append(f"FrameTimeline 探测失败：{exc}")

    if package:
        try:
            output = await run_adb("shell", "dumpsys", "gfxinfo", package, "framestats", serial=serial, timeout_seconds=8)
            sources[FRAME_SOURCE_FRAMESTATS] = parse_framestats(output) is not None
            if not sources[FRAME_SOURCE_FRAMESTATS]:
                notes.append("framestats 命令可用但前台应用暂无逐帧数据（应用静止或走非 View 渲染路径）。")
        except AdbError as exc:
            notes.append(f"framestats 探测失败：{exc}")
    else:
        notes.append("无法识别前台应用包名；framestats 需在前台应用运行时可探测。")

    return FrameCapabilities(
        sdk_version=sdk_version,
        android_release=release,
        foreground_package=package,
        sources=sources,
        recommended_source=pick_recommended_source(sdk_version, sources),
        notes=notes,
    )


# 数据源失败冷却周期数：解析失败后暂停尝试（如 60 周期 ≈ 30s @0.5s），
# 冷却到期自动复测一次，避免每周期白跑已确认不可用的源（慢车机尤其浪费）。
FRAME_SOURCE_COOLDOWN_CYCLES = 60


async def capture_frame_metrics(
    serial: str,
    package: str | None,
    sdk_version: int | None,
    cooldowns: dict[str, int] | None = None,
) -> FrameStats | None:
    """按优先级尝试逐帧数据源，返回第一个成功解析的结果；全部失败返回 None。

    SurfaceFlinger --latency 与 gfxinfo 计数器属于 monitor 的兜底链路，
    不在此处尝试（前者需要会话内 layer 选择状态）。

    cooldowns：源 → 剩余冷却周期数。冷却中的源直接跳过；解析失败/异常时
    设置冷却；成功时清除冷却。None 表示不做冷却（巡检等单次调用场景）。
    """

    for source in source_priority(sdk_version):
        if cooldowns and cooldowns.get(source, 0) > 0:
            cooldowns[source] -= 1
            continue
        try:
            if source == FRAME_SOURCE_FRAMETIMELINE:
                output = await run_adb("shell", "dumpsys", "SurfaceFlinger", "--frametimeline", "-all", serial=serial, timeout_seconds=7)
                stats = parse_frame_timeline(output)
            elif source == FRAME_SOURCE_FRAMESTATS:
                if not package:
                    continue
                output = await run_adb("shell", "dumpsys", "gfxinfo", package, "framestats", serial=serial, timeout_seconds=7)
                stats = parse_framestats(output)
            else:
                stats = None
        except AdbError:
            stats = None
        if stats is not None:
            if cooldowns is not None:
                cooldowns.pop(source, None)
            stats.package = package
            return stats
        if cooldowns is not None and source != FRAME_SOURCE_SF_LATENCY:
            cooldowns[source] = FRAME_SOURCE_COOLDOWN_CYCLES
    return None
