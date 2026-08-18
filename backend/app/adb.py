"""受控执行 ADB 命令并解析 Android 诊断输出。

所有命令通过 create_subprocess_exec 执行，绝不经由 shell 插值外部输入。
"""

from __future__ import annotations

import asyncio
import math
import re
import shutil
import statistics
from dataclasses import dataclass
from typing import Iterable

from .models import ProcessSample


class AdbError(RuntimeError):
    """ADB 调用、权限或命令输出无法使用时抛出的可恢复错误。"""


@dataclass(slots=True)
class AdbDevice:
    serial: str
    state: str
    model: str | None = None
    product: str | None = None
    device: str | None = None
    android_version: str | None = None


@dataclass(slots=True)
class FrameStats:
    """一个采样周期内从某个数据源解析出的逐帧统计。

    所有字段都允许为 None：某个数据源不提供该维度时保持缺省，
    由上层按“哪个维度有值用哪个”合并。
    """

    source: str = ""
    fps: float | None = None
    frame_count: int | None = None
    jank_count: int | None = None
    jank_pct: float | None = None
    avg_frame_time_ms: float | None = None
    p95_frame_time_ms: float | None = None
    p99_frame_time_ms: float | None = None
    input_latency_ms: float | None = None
    refresh_period_ns: int | None = None
    package: str | None = None
    layer: str | None = None


async def get_sdk_version(serial: str) -> int | None:
    """读取设备 SDK 版本（API level），用于选择可用数据源。"""
    try:
        output = await run_adb("shell", "getprop", "ro.build.version.sdk", serial=serial, timeout_seconds=5)
        return int(output.strip())
    except (AdbError, ValueError):
        return None


async def run_adb(
    *arguments: str,
    serial: str | None = None,
    timeout_seconds: float = 6.0,
) -> str:
    """运行单个 ADB 命令，并为卡住的设备调用建立硬超时。"""

    adb_path = shutil.which("adb")
    if not adb_path:
        raise AdbError("未找到 adb；请安装 Android SDK Platform-Tools 并加入 PATH")

    command = [adb_path]
    if serial:
        command.extend(["-s", serial])
    command.extend(arguments)

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.communicate()
        raise AdbError(f"ADB 命令超时（{timeout_seconds:.0f}s）") from exc
    except OSError as exc:
        raise AdbError(f"无法启动 ADB：{exc}") from exc

    output = stdout.decode("utf-8", errors="replace")
    error = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise AdbError(error or output.strip() or f"ADB 返回 {process.returncode}")
    return output


async def list_devices() -> list[AdbDevice]:
    """返回 adb devices -l 中的设备，含 offline/unauthorized 状态。"""

    output = await run_adb("devices", "-l", timeout_seconds=5)
    devices: list[AdbDevice] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        properties = dict(token.split(":", 1) for token in tokens[2:] if ":" in token)
        devices.append(
            AdbDevice(
                serial=tokens[0],
                state=tokens[1],
                model=properties.get("model"),
                product=properties.get("product"),
                device=properties.get("device"),
            )
        )
    return devices


async def enrich_device(device: AdbDevice) -> AdbDevice:
    """读取可选的版本属性；失败时仍返回已发现的设备。"""

    if device.state != "device":
        return device
    try:
        device.android_version = (await run_adb("shell", "getprop", "ro.build.version.release", serial=device.serial)).strip()
    except AdbError:
        pass
    return device


def _float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _int(value: str) -> int | None:
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None


def parse_top(output: str) -> tuple[float | None, list[ProcessSample]]:
    """容忍 Toybox/procps 风格 top 输出，提取总 CPU 与进程 CPU。"""

    total_cpu: float | None = None
    capacity_idle_match = re.search(
        r"(?:^|\s)([\d.]+)%\s*cpu\b.*?([\d.]+)%\s*idle\b",
        output,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if capacity_idle_match:
        capacity = _float(capacity_idle_match.group(1))
        idle = _float(capacity_idle_match.group(2))
        if capacity is not None and idle is not None:
            total_cpu = round(max(0.0, capacity - idle) / capacity * 100, 2) if capacity > 0 else None

    if total_cpu is None:
        total_match = re.search(r"(?:^|\s)([\d.]+)%\s*cpu\b", output, flags=re.IGNORECASE | re.MULTILINE)
        if total_match:
            total_cpu = _float(total_match.group(1))

    if total_cpu is None:
        linux_match = re.search(
            r"%?Cpu\(s\):\s*([\d.]+)\s*us,\s*([\d.]+)\s*sy(?:,\s*([\d.]+)\s*ni)?",
            output,
            flags=re.IGNORECASE,
        )
        if linux_match:
            total_cpu = sum(_float(value) or 0.0 for value in linux_match.groups())

    processes: list[ProcessSample] = []
    for line in output.splitlines():
        if not re.match(r"^\s*\d+\s+", line):
            continue
        tokens = line.split()
        if len(tokens) < 3:
            continue
        pid = _int(tokens[0])
        percentages = [match.group(1) for match in re.finditer(r"(?<![\w.])([\d.]+)%", line)]
        if pid is None or not percentages:
            continue
        cpu_pct = _float(percentages[0])
        name = tokens[-1]
        if name in {"top", "<unknown>"}:
            continue
        processes.append(ProcessSample(process_name=name, pid=pid, cpu_pct=cpu_pct))
    return total_cpu, processes


def parse_cpuinfo(output: str) -> list[ProcessSample]:
    """解析 dumpsys cpuinfo 的进程占用行，用于补全 top 的进程视图。"""

    processes: list[ProcessSample] = []
    pattern = re.compile(r"^\s*([\d.]+)%\s+(\d+)/([^:\s]+)", flags=re.MULTILINE)
    for cpu_value, pid_value, process_name in pattern.findall(output):
        cpu = _float(cpu_value)
        pid = _int(pid_value)
        if cpu is not None:
            processes.append(ProcessSample(process_name=process_name, pid=pid, cpu_pct=cpu))
    return processes


def merge_processes(*collections: Iterable[ProcessSample]) -> list[ProcessSample]:
    """按 PID/进程名合并多源采样，保留各指标中较可信的非空值。"""

    merged: dict[tuple[int | None, str], ProcessSample] = {}
    for collection in collections:
        for item in collection:
            key = (item.pid, item.process_name)
            existing = merged.get(key)
            if existing is None:
                merged[key] = ProcessSample(
                    process_name=item.process_name,
                    pid=item.pid,
                    cpu_pct=item.cpu_pct,
                    pss_kb=item.pss_kb,
                    rss_kb=item.rss_kb,
                )
                continue
            if item.cpu_pct is not None:
                existing.cpu_pct = item.cpu_pct if existing.cpu_pct is None else max(existing.cpu_pct, item.cpu_pct)
            existing.pss_kb = item.pss_kb if item.pss_kb is not None else existing.pss_kb
            existing.rss_kb = item.rss_kb if item.rss_kb is not None else existing.rss_kb
    return list(merged.values())


def parse_meminfo(output: str) -> tuple[int | None, int | None, list[ProcessSample]]:
    """解析 dumpsys meminfo 的 Total PSS/RSS by process 段落。"""
    total_ram_match = re.search(r"^\s*Total RAM:\s*([\d,]+)K\b", output, flags=re.IGNORECASE | re.MULTILINE)
    total_ram_kb = _int(total_ram_match.group(1)) if total_ram_match else None

    process_map: dict[tuple[int | None, str], ProcessSample] = {}
    section: str | None = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if "TOTAL PSS BY PROCESS" in upper:
            section = "pss"
            continue
        if "TOTAL RSS BY PROCESS" in upper:
            section = "rss"
            continue
        if line.endswith(":") and "BY PROCESS" not in upper:
            section = None
        if section is None:
            continue

        match = re.match(r"^([\d,]+)K:\s+(.+)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        amount = _int(match.group(1))
        label = match.group(2)
        pid_match = re.search(r"\(pid\s+(\d+)", label, flags=re.IGNORECASE)
        pid = _int(pid_match.group(1)) if pid_match else None
        name = re.sub(r"\s*\(pid\s+\d+.*?\)\s*$", "", label, flags=re.IGNORECASE).strip()
        if not name or amount is None:
            continue
        key = (pid, name)
        item = process_map.setdefault(key, ProcessSample(process_name=name, pid=pid))
        if section == "pss":
            item.pss_kb = amount
        else:
            item.rss_kb = amount

    processes = list(process_map.values())
    pss_total = sum(item.pss_kb or 0 for item in processes) or None
    rss_total = sum(item.rss_kb or 0 for item in processes) or None
    return pss_total, rss_total, total_ram_kb, processes


def parse_surface_latency(output: str) -> float | None:
    """根据 SurfaceFlinger present 时间戳计算最近窗口的显示帧率估计。"""

    rows: list[list[int]] = []
    for line in output.splitlines():
        tokens = line.split()
        if not tokens or not all(token.lstrip("-").isdigit() for token in tokens):
            continue
        values = [int(token) for token in tokens]
        if len(values) >= 1:
            rows.append(values)

    if len(rows) < 3:
        return None
    refresh_period = rows[0][0]
    timestamps = [(row[1] if len(row) >= 2 and row[1] > 0 else row[-1]) for row in rows[1:] if (row[1] if len(row) >= 2 and row[1] > 0 else row[-1]) > 0 and (row[1] if len(row) >= 2 and row[1] > 0 else row[-1]) < 9_000_000_000_000_000_000]
    timestamps = sorted(set(timestamps))
    if len(timestamps) < 2:
        return None
    deltas = [right - left for left, right in zip(timestamps, timestamps[1:]) if 1_000_000 <= right - left <= 1_000_000_000]
    if not deltas:
        return None
    median_delta = statistics.median(deltas[-30:])
    fps = 1_000_000_000 / median_delta
    if refresh_period > 0:
        max_display_fps = 1_000_000_000 / refresh_period * 1.2
        fps = min(fps, max_display_fps)
    return round(fps, 2)


def parse_surface_latency_stats(output: str) -> FrameStats | None:
    """``dumpsys SurfaceFlinger --latency <layer>`` 的增强统计版。

    在原有呈现 FPS 基础上额外给出刷新周期、窗口内帧数以及
    “呈现间隔超过两倍刷新周期”的掉帧计数。
    """

    rows: list[list[int]] = []
    for line in output.splitlines():
        tokens = line.split()
        if not tokens or not all(token.lstrip("-").isdigit() for token in tokens):
            continue
        values = [int(token) for token in tokens]
        if len(values) >= 1:
            rows.append(values)
    if len(rows) < 3:
        return None
    refresh_period = rows[0][0] or 16_666_666
    timestamps = sorted(
        {
            (row[1] if len(row) >= 2 and row[1] > 0 else row[-1])
            for row in rows[1:]
            if (row[1] if len(row) >= 2 and row[1] > 0 else row[-1]) > 0
        }
    )
    deltas = [right - left for left, right in zip(timestamps, timestamps[1:]) if 1_000_000 <= right - left <= 1_000_000_000]
    if not deltas:
        return None
    fps = 1_000_000_000 / statistics.median(deltas[-30:])
    fps = min(fps, 1_000_000_000 / refresh_period * 1.2)
    jank_count = sum(1 for delta in deltas if delta > 2 * refresh_period)
    return FrameStats(
        source="sf_latency",
        fps=round(fps, 2),
        frame_count=len(timestamps),
        jank_count=jank_count,
        jank_pct=round(jank_count / len(timestamps) * 100, 2) if jank_count else 0.0,
        refresh_period_ns=refresh_period,
    )


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    rank = (len(sorted_values) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[int(rank)]
    fraction = rank - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _fps_from_present_ns(timestamps_ns: list[int], refresh_period_ns: int | None) -> float | None:
    """由递增的呈现时间戳（ns）估算呈现 FPS；沿用 1ms–1s 间隔过滤与刷新率限幅。"""
    if len(timestamps_ns) < 2:
        return None
    deltas = [right - left for left, right in zip(timestamps_ns, timestamps_ns[1:]) if 1_000_000 <= right - left <= 1_000_000_000]
    if not deltas:
        return None
    fps = 1_000_000_000 / statistics.median(deltas[-30:])
    if refresh_period_ns:
        fps = min(fps, 1_000_000_000 / refresh_period_ns * 1.2)
    return round(fps, 2)


def parse_framestats(output: str, refresh_period_ns: int | None = None) -> FrameStats | None:
    """解析 ``dumpsys gfxinfo <pkg> framestats``（Android 7 / API 24+）。

    输出为“列头 + 逐帧逗号分隔时间戳（ns）”。按列头名索引取值，
    因此兼容 Android 7–12 各版本列数差异（DisplayPresentTime 等在
    较新版本才出现）。帧耗时 = FrameCompleted − IntendedVsync，
    卡顿判定 = 帧耗时超过两倍帧间隔（与 gfxinfo 的 Janky frames 口径一致）。
    """

    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if header is None:
            if stripped.startswith("Flags"):
                header = [token.strip() for token in stripped.split(",")]
            continue
        if not stripped or not stripped[0].isdigit():
            continue
        rows.append(stripped.split(","))
    if not header or not rows:
        return None
    index = {name: i for i, name in enumerate(header)}
    if "FrameCompleted" not in index:
        return None

    def column(row: list[str], name: str) -> int | None:
        i = index.get(name)
        if i is None or i >= len(row):
            return None
        token = row[i].strip()
        if not token or token == "-1" or not token.lstrip("-").isdigit():
            return None
        return int(token)

    period = refresh_period_ns or 16_666_666
    frame_times: list[int] = []
    present_times: list[int] = []
    input_latencies: list[int] = []
    jank_count = 0
    for row in rows:
        intended = column(row, "IntendedVsync")
        completed = column(row, "FrameCompleted")
        if intended is not None and completed is not None and completed > intended:
            frame_times.append(completed - intended)
            interval = column(row, "FrameInterval") or period
            if completed - intended > 2 * interval:
                jank_count += 1
        present = column(row, "DisplayPresentTime") or completed
        if present is not None and present > 0:
            present_times.append(present)
        newest_input = column(row, "NewestInputEvent")
        handle_input = column(row, "HandleInputStart")
        if newest_input is not None and handle_input is not None and handle_input > newest_input:
            input_latencies.append(handle_input - newest_input)

    if not frame_times:
        return None
    frame_times_ms = [value / 1_000_000 for value in frame_times]
    fps = _fps_from_present_ns(present_times, period)
    return FrameStats(
        source="framestats",
        fps=fps,
        frame_count=len(frame_times),
        jank_count=jank_count,
        jank_pct=round(jank_count / len(frame_times) * 100, 2),
        avg_frame_time_ms=round(sum(frame_times_ms) / len(frame_times_ms), 2),
        p95_frame_time_ms=round(_percentile(sorted(frame_times_ms), 0.95), 2),
        p99_frame_time_ms=round(_percentile(sorted(frame_times_ms), 0.99), 2),
        input_latency_ms=round(sum(input_latencies) / len(input_latencies) / 1_000_000, 2) if input_latencies else None,
        refresh_period_ns=period,
    )


_JANK_TYPE_ANY = re.compile(r"^Jank Type\s*:\s*(\S+)\s*$")

# AOSP JankInfo.h 中明确的卡顿类型。部分 OEM（如三星）会把所有帧标记为
# "Unknown jank"（该字段在其固件中不可用），此类不计入卡顿，避免 jank 恒为
# 100% 的误导；此时 jank 信息应回退到 framestats 的帧耗时计算。
_KNOWN_JANK_TYPES = {
    "AppDeadlineMissed",
    "BufferStuffing",
    "SurfaceFlingerCpuDeadlineMissed",
    "SurfaceFlingerGpuDeadlineMissed",
    "SurfaceFlingerScheduling",
    "SurfaceFlingerStuffing",
    "DisplayHAL",
    "PredictionError",
    "Dropped",
}


def parse_frame_timeline(output: str) -> FrameStats | None:
    """解析 ``dumpsys SurfaceFlinger --frametimeline -all``（Android 12 / API 31+）。

    AOSP 输出按“Display Frame N”分节，每节给出 Vsync Period（ms）、
    显示帧级 Jank Type，以及 Expected/Actual 三列（Start/End/Present，
    单位为相对首帧的 ms）。

    兼容性：部分 OEM（三星）不填充 Actual Present time（全部为 0.00），
    此时退回到 Actual End time 估算帧节奏（相邻帧间隔与呈现节奏一致）；
    其 Jank Type 一律为 "Unknown jank"，不计入卡顿。
    """

    refresh_period_ns: int | None = None
    present_times_ms: list[float] = []
    end_times_ms: list[float] = []
    durations_ms: list[float] = []
    jank_count = 0
    in_display_frame = False
    section_janky = False

    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Display Frame"):
            in_display_frame = True
            section_janky = False
            continue
        if not in_display_frame:
            continue
        jank_match = _JANK_TYPE_ANY.match(stripped)
        if jank_match:
            if jank_match.group(1) in _KNOWN_JANK_TYPES and not section_janky:
                jank_count += 1
                section_janky = True
            continue
        vsync_match = re.match(r"^Vsync Period:\s*([\d.]+)\s*$", stripped)
        if vsync_match:
            refresh_period_ns = round(float(vsync_match.group(1)) * 1_000_000)
            continue
        # 显示帧级时间表无缩进，而 Surface 帧级表带 4 空格缩进。
        if line.startswith("Actual") and "|" in line:
            tokens = [token.strip() for token in line.split("|")]
            if len(tokens) >= 4:
                try:
                    start, end, present = (float(tokens[1]), float(tokens[2]), float(tokens[3]))
                except ValueError:
                    continue
                if present > 0:
                    present_times_ms.append(present)
                if end > 0:
                    end_times_ms.append(end)
                if end > start and end > 0:
                    durations_ms.append(end - start)
            continue

    timestamps_ms = present_times_ms if len(present_times_ms) >= 2 else end_times_ms
    if not timestamps_ms:
        return None
    timestamps_ns = [round(value * 1_000_000) for value in timestamps_ms]
    fps = _fps_from_present_ns(timestamps_ns, refresh_period_ns)
    if durations_ms:
        avg = round(sum(durations_ms) / len(durations_ms), 2)
        p95 = round(_percentile(sorted(durations_ms), 0.95), 2)
        p99 = round(_percentile(sorted(durations_ms), 0.99), 2)
    else:
        avg = p95 = p99 = None
    return FrameStats(
        source="frametimeline",
        fps=fps,
        frame_count=len(timestamps_ms),
        jank_count=jank_count,
        jank_pct=round(jank_count / len(timestamps_ms) * 100, 2) if timestamps_ms else None,
        avg_frame_time_ms=avg,
        p95_frame_time_ms=p95,
        p99_frame_time_ms=p99,
        refresh_period_ns=refresh_period_ns,
    )


def parse_gfxinfo_summary(output: str) -> tuple[int, int] | None:
    """从 dumpsys gfxinfo 输出解析累计渲染帧数。

    gfxinfo 按进程分段（``** Graphics info for pid N [pkg] **``），多进程应用
    （车机 HMI 常见）会输出多段；旧实现用 re.search 固定取第一段，可能取到
    不活跃进程导致计数器不增长。这里解析所有段，取累计帧数最大的进程
    （= 渲染最活跃的进程）作为增量基准。
    """
    sections = re.split(r"\*\* Graphics info for pid \d+", output)
    best: tuple[int, int] | None = None
    for section in sections:
        total_match = re.search(r"Total frames rendered:\s*(\d+)", section, re.IGNORECASE)
        janky_match = re.search(r"Janky frames:\s*(\d+)", section, re.IGNORECASE)
        if total_match and janky_match:
            candidate = (int(total_match.group(1)), int(janky_match.group(1)))
            if best is None or candidate[0] > best[0]:
                best = candidate
    return best
async def list_surface_layers(serial: str) -> list[str]:
    """列出可供 --latency 测试的候选 layer 名称。"""

    output = await run_adb("shell", "dumpsys", "SurfaceFlinger", "--list", serial=serial, timeout_seconds=7)
    return [line.strip() for line in output.splitlines() if line.strip()]



# Android 12L/13+ 的 dumpsys activity activities 中 mResumedActivity 已改名
# 为 topResumedActivity（分隔符也从 ":" 变为 "="），这里两种格式都兼容。
_FOREGROUND_PACKAGE_RE = re.compile(
    r"^\s*(?:mResumedActivity|topResumedActivity)\s*[:=]\s*(?:ActivityRecord\{[^}]*\s+)?u\d+\s+([A-Za-z0-9_.]+)/",
    flags=re.MULTILINE,
)


def extract_foreground_package(output: str) -> str | None:
    """从 dumpsys activity activities 输出解析前台包名（纯函数，可单测）。"""
    match = _FOREGROUND_PACKAGE_RE.search(output)
    return match.group(1) if match else None


async def get_foreground_package(serial: str) -> str | None:
    """Return the package name of the current resumed activity when available."""
    output = await run_adb("shell", "dumpsys", "activity", "activities", serial=serial, timeout_seconds=5)
    return extract_foreground_package(output)


def choose_surface_layer(layers: list[str], foreground_package: str | None = None) -> str | None:
    """Prefer a layer belonging to the foreground app; fall back to a safe heuristic."""
    if not layers:
        return None

    def score(layer: str) -> int:
        value = layer.lower()
        if any(token in value for token in ("root", "container", "displayarea", "task=", "leaf:", "activityrecord", "wallpaper", "statusbar", "navigationbar", "imecontainer", "windowtoken")):
            return -100
        result = 0
        if re.match(r"^[0-9a-f]{6,}\s+", value):
            result -= 60

        if foreground_package and foreground_package.lower() in value:
            result += 200
        if "surfaceview" in value or "textureview" in value:
            result += 40
        if "com." in value:
            result += 30
        if "#1" in value:
            result += 20
        return result

    return max(layers, key=score)
