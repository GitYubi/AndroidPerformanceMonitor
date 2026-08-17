"""受限时长的异步 ADB 采样会话管理器。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .adb import (
    AdbError,
    FrameStats,
    choose_surface_layer,
    get_foreground_package,
    get_sdk_version,
    list_devices,
    list_surface_layers,
    merge_processes,
    parse_cpuinfo,
    parse_gfxinfo_summary,
    parse_meminfo,
    parse_surface_latency_stats,
    parse_top,
    run_adb,
)
from .device_logs import DeviceLogConfig, export_and_clean_device_logs
from .frame_sources import (
    FRAME_SOURCE_FRAMESTATS,
    FRAME_SOURCE_FRAMETIMELINE,
    FRAME_SOURCE_SF_LATENCY,
    SOURCE_LABELS,
    capture_frame_metrics,
)
from .models import ProcessSample, SamplePayload, StartSessionRequest
from .storage import SessionStore, SessionWriter

# 项目根目录（backend/app 的上级的上级）：设备日志默认导出到 <root>/DevicesLogs
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class RuntimeSession:
    session_id: str
    request: StartSessionRequest
    writer: SessionWriter
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    selected_surface_layer: str | None = None
    reported_error_codes: set[str] = field(default_factory=set)
    render_baselines: dict[str, tuple[int, int, float]] = field(default_factory=dict)
    sdk_version: int | None = None
    active_frame_source: str | None = None


class MonitorManager:
    """只允许单个采样会话，以限定系统负载与 SQLite 写入压力。"""

    def __init__(self, data_root: Path):
        self.store = SessionStore(data_root)
        self.active: dict[str, RuntimeSession] = {}

    async def start(self, request: StartSessionRequest) -> str:
        if self.active:
            raise ValueError("已有测试会话运行中；请先停止当前会话")
        devices = await list_devices()
        matched = next((device for device in devices if device.serial == request.serial), None)
        if matched is None:
            raise ValueError("未发现指定 ADB 设备")
        if matched.state != "device":
            raise ValueError(f"设备当前状态为 {matched.state}，请确认调试授权后重试")

        session_id = str(uuid.uuid4())
        created_at_ms = int(time.time() * 1000)
        sdk_version = await get_sdk_version(request.serial)
        metadata = {
            "session_id": session_id,
            "serial": request.serial,
            "created_at_ms": created_at_ms,
            "duration_seconds": request.duration_seconds,
            "interval_ms": request.interval_ms,
            "enabled_metrics": request.metrics.model_dump(),
            "surface_layer": request.surface_layer,
            "sdk_version": sdk_version,
            "app_version": "0.2.0",
        }
        writer = self.store.create_session(session_id, metadata)
        runtime = RuntimeSession(
            session_id=session_id,
            request=request,
            writer=writer,
            selected_surface_layer=request.surface_layer,
            sdk_version=sdk_version,
        )
        if sdk_version is None:
            writer.add_event("warning", "sdk_probe_failed", "无法读取设备 SDK 版本；帧率数据源将按未知版本探测并降级。")
        self.active[session_id] = runtime
        runtime.task = asyncio.create_task(self._run(runtime), name=f"monitor-{session_id}")
        return session_id

    async def stop(self, session_id: str) -> None:
        runtime = self.active.get(session_id)
        if runtime is None:
            session = self.store.get_session(session_id)
            if session is None:
                raise KeyError(session_id)
            return
        runtime.stop_event.set()
        if runtime.task:
            try:
                # 60s：等待采样循环结束并完成 finally 中的设备日志导出。
                await asyncio.wait_for(asyncio.shield(runtime.task), timeout=60)
            except TimeoutError:
                runtime.task.cancel()
                try:
                    await runtime.task
                except asyncio.CancelledError:
                    pass

    async def _run(self, runtime: RuntimeSession) -> None:
        started_monotonic = time.monotonic()
        deadline = started_monotonic + runtime.request.duration_seconds
        interval_seconds = runtime.request.interval_ms / 1000
        next_tick = started_monotonic
        final_state = "completed"

        try:
            while time.monotonic() < deadline:
                if runtime.stop_event.is_set():
                    final_state = "stopped"
                    break

                payload = await self._capture_once(runtime)
                runtime.writer.write_sample(payload)
                next_tick += interval_seconds
                delay = max(0.0, next_tick - time.monotonic())
                try:
                    await asyncio.wait_for(runtime.stop_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
            if runtime.stop_event.is_set():
                final_state = "stopped"
        except asyncio.CancelledError:
            final_state = "stopped"
            raise
        except Exception as exc:  # 防止单次未知实现错误泄露为悬挂会话。
            final_state = "failed"
            runtime.writer.add_event("error", "runtime_failure", str(exc))
        finally:
            if runtime.request.log_export_enabled():
                try:
                    await self._export_device_logs(runtime)
                except Exception as exc:
                    runtime.writer.add_event("warning", "log_export_failed", f"设备日志导出异常：{exc}")
            runtime.writer.finish(final_state)
            self.active.pop(runtime.session_id, None)

    async def _export_device_logs(self, runtime: RuntimeSession) -> None:
        """会话结束（停止/自然到期）后拉取并清理车机日志。"""
        config = DeviceLogConfig(
            anr=runtime.request.anr_path,
            crash=runtime.request.crash_path,
            tombstone=runtime.request.tombstone_path,
            export_root=runtime.request.log_export_root,
        )
        await export_and_clean_device_logs(
            runtime.request.serial,
            config,
            PROJECT_ROOT,
            lambda code, severity, message: runtime.writer.add_event(severity, code, message),
        )
    async def _capture_once(self, runtime: RuntimeSession) -> SamplePayload:
        statuses: dict[str, str] = {}
        cpu_total: float | None = None
        pss_kb: int | None = None
        rss_kb: int | None = None
        total_ram_kb: int | None = None
        frame_stats: FrameStats | None = None
        app_render_fps: float | None = None
        app_jank_pct: float | None = None
        process_sets: list[list[ProcessSample]] = []
        jobs: list[tuple[str, asyncio.Task[object]]] = []
        if runtime.request.metrics.cpu:
            jobs.append(("cpu", asyncio.create_task(self._capture_cpu(runtime.request.serial))))
        if runtime.request.metrics.memory:
            jobs.append(("memory", asyncio.create_task(self._capture_memory(runtime.request.serial))))
        if runtime.request.metrics.fps:
            jobs.append(("fps", asyncio.create_task(self._capture_fps(runtime))))
            jobs.append(("render", asyncio.create_task(self._capture_app_render(runtime))))
        for metric, job in jobs:
            try:
                result = await job
                statuses[metric] = "ok"
                if metric == "cpu":
                    cpu_total, processes = result  # type: ignore[misc]
                    process_sets.append(processes)
                elif metric == "memory":
                    pss_kb, rss_kb, total_ram_kb, processes = result  # type: ignore[misc]
                    process_sets.append(processes)
                elif metric == "fps":
                    frame_stats = result  # type: ignore[assignment]
                    if frame_stats is None or frame_stats.fps is None:
                        statuses[metric] = "unavailable" if frame_stats is None else "limited"
                        if frame_stats is None:
                            self._record_error_once(runtime, "fps_no_present_samples", "所选帧率数据源暂无可计算的呈现时间戳；静态画面不会持续产生新帧，请操作正在变化的界面。")
                else:
                    app_render_fps, app_jank_pct = result  # type: ignore[misc]
                    if app_render_fps is None:
                        statuses[metric] = "warming_up" if runtime.render_baselines else "unavailable"
                        if not runtime.render_baselines:
                            self._record_error_once(runtime, "render_fps_unavailable", "当前前台应用未提供可增量计算的 gfxinfo 帧统计；该指标仅支持部分 View/Canvas 渲染路径。")
            except AdbError as exc:
                statuses[metric] = "unavailable"
                self._record_error_once(runtime, f"{metric}_adb", str(exc))
            except Exception as exc:
                statuses[metric] = "error"
                self._record_error_once(runtime, f"{metric}_parse", str(exc))
        return SamplePayload(
            ts_ms=int(time.time() * 1000),
            cpu_total_pct=cpu_total,
            pss_kb=pss_kb,
            rss_kb=rss_kb,
            total_ram_kb=total_ram_kb,
            fps=frame_stats.fps if frame_stats else None,
            app_render_fps=app_render_fps,
            app_jank_pct=app_jank_pct,
            frame_source=frame_stats.source if frame_stats else None,
            frame_count=frame_stats.frame_count if frame_stats else None,
            jank_count=frame_stats.jank_count if frame_stats else None,
            jank_pct=frame_stats.jank_pct if frame_stats else None,
            avg_frame_time_ms=frame_stats.avg_frame_time_ms if frame_stats else None,
            p95_frame_time_ms=frame_stats.p95_frame_time_ms if frame_stats else None,
            p99_frame_time_ms=frame_stats.p99_frame_time_ms if frame_stats else None,
            input_latency_ms=frame_stats.input_latency_ms if frame_stats else None,
            statuses=statuses,
            processes=merge_processes(*process_sets),
        )

    async def _capture_app_render(self, runtime: RuntimeSession) -> tuple[float | None, float | None]:
        package = await get_foreground_package(runtime.request.serial)
        if not package:
            raise AdbError("无法识别当前前台应用包名")
        output = await run_adb("shell", "dumpsys", "gfxinfo", package, serial=runtime.request.serial, timeout_seconds=7)
        counters = parse_gfxinfo_summary(output)
        if counters is None:
            return None, None
        total_frames, janky_frames = counters
        now = time.monotonic()
        previous = runtime.render_baselines.get(package)
        runtime.render_baselines[package] = (total_frames, janky_frames, now)
        if previous is None:
            runtime.writer.add_event("info", "render_fps_warmup", f"开始采集 {package} 的 gfxinfo 增量帧统计")
            return None, None
        previous_total, previous_janky, previous_time = previous
        delta_frames = total_frames - previous_total
        delta_janky = janky_frames - previous_janky
        elapsed = now - previous_time
        if delta_frames <= 0 or delta_janky < 0 or elapsed <= 0:
            return None, None
        return round(delta_frames / elapsed, 2), round(delta_janky / delta_frames * 100, 2)

    def _record_error_once(self, runtime: RuntimeSession, code: str, message: str) -> None:
        if code not in runtime.reported_error_codes:
            runtime.writer.add_event("warning", code, message)
            runtime.reported_error_codes.add(code)

    async def _capture_cpu(self, serial: str) -> tuple[float | None, list[ProcessSample]]:
        top_output, cpuinfo_output = await asyncio.gather(
            run_adb("shell", "top", "-b", "-n", "1", serial=serial, timeout_seconds=6),
            run_adb("shell", "dumpsys", "cpuinfo", serial=serial, timeout_seconds=6),
        )
        total, top_processes = parse_top(top_output)
        return total, merge_processes(top_processes, parse_cpuinfo(cpuinfo_output))

    async def _capture_memory(self, serial: str) -> tuple[int | None, int | None, int | None, list[ProcessSample]]:
        output = await run_adb("shell", "dumpsys", "meminfo", serial=serial, timeout_seconds=8)
        return parse_meminfo(output)
    async def _capture_fps(self, runtime: RuntimeSession) -> FrameStats | None:
        """按版本与可用性自动选择逐帧数据源，全部失败时回退 SF --latency。"""
        package = await get_foreground_package(runtime.request.serial)
        per_frame = await capture_frame_metrics(runtime.request.serial, package, runtime.sdk_version)
        if per_frame is not None:
            self._switch_frame_source(runtime, per_frame.source)
            return per_frame
        self._switch_frame_source(runtime, FRAME_SOURCE_SF_LATENCY)
        if runtime.sdk_version is None or runtime.sdk_version >= 24:
            self._record_error_once(runtime, "frame_perframe_unavailable", "逐帧数据源（FrameTimeline / framestats）不可用或暂未产生数据，已回退为 SurfaceFlinger layer 呈现节奏采样。")
        return await self._capture_sf_latency_fallback(runtime, package)

    def _switch_frame_source(self, runtime: RuntimeSession, source: str) -> None:
        if runtime.active_frame_source == source:
            return
        previous = runtime.active_frame_source
        runtime.active_frame_source = source
        detail = f"帧率数据源切换为 {SOURCE_LABELS.get(source, source)}"
        if previous:
            detail += f"（原：{SOURCE_LABELS.get(previous, previous)}）"
        runtime.writer.add_event("info", "frame_source_switch", detail)

    async def _capture_sf_latency_fallback(self, runtime: RuntimeSession, foreground_package: str | None = None) -> FrameStats | None:
        if runtime.request.surface_layer is None:
            if foreground_package is None:
                layers, foreground_package = await asyncio.gather(
                    list_surface_layers(runtime.request.serial),
                    get_foreground_package(runtime.request.serial),
                )
            else:
                layers = await list_surface_layers(runtime.request.serial)
            candidate = choose_surface_layer(layers, foreground_package)
            if candidate is None:
                raise AdbError("未找到可用的 SurfaceFlinger layer")
            if runtime.selected_surface_layer != candidate:
                previous = runtime.selected_surface_layer
                runtime.selected_surface_layer = candidate
                detail = f"自动跟踪前台应用 layer：{candidate}"
                if previous:
                    detail += f"（已替换：{previous}）"
                runtime.writer.add_event("info", "fps_auto_layer_switch", detail)
        elif runtime.selected_surface_layer is None:
            runtime.selected_surface_layer = runtime.request.surface_layer

        output = await run_adb(
            "shell",
            "dumpsys",
            "SurfaceFlinger",
            "--latency",
            runtime.selected_surface_layer,
            serial=runtime.request.serial,
            timeout_seconds=7,
        )
        stats = parse_surface_latency_stats(output)
        if stats is not None:
            stats.layer = runtime.selected_surface_layer
        return stats
