"""受限时长的异步 ADB 采样会话管理器。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .adb import (
    AdbError,
    choose_surface_layer,
    get_foreground_package,
    list_devices,
    list_surface_layers,
    merge_processes,
    parse_cpuinfo,
    parse_gfxinfo_summary,
    parse_meminfo,
    parse_surface_latency,
    parse_top,
    run_adb,
)
from .models import ProcessSample, SamplePayload, StartSessionRequest
from .storage import SessionStore, SessionWriter


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
        metadata = {
            "session_id": session_id,
            "serial": request.serial,
            "created_at_ms": created_at_ms,
            "duration_seconds": request.duration_seconds,
            "interval_ms": request.interval_ms,
            "enabled_metrics": request.metrics.model_dump(),
            "surface_layer": request.surface_layer,
            "app_version": "0.1.0",
        }
        writer = self.store.create_session(session_id, metadata)
        runtime = RuntimeSession(
            session_id=session_id,
            request=request,
            writer=writer,
            selected_surface_layer=request.surface_layer,
        )
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
                await asyncio.wait_for(asyncio.shield(runtime.task), timeout=10)
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
            runtime.writer.finish(final_state)
            self.active.pop(runtime.session_id, None)
    async def _capture_once(self, runtime: RuntimeSession) -> SamplePayload:
        statuses: dict[str, str] = {}
        cpu_total: float | None = None
        pss_kb: int | None = None
        rss_kb: int | None = None
        total_ram_kb: int | None = None
        fps: float | None = None
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
                    fps = result  # type: ignore[assignment]
                    if fps is None:
                        statuses[metric] = "unavailable"
                        self._record_error_once(runtime, "fps_no_present_samples", "所选 SurfaceFlinger layer 暂无可计算的 present 时间戳；静态画面不会持续产生新帧，请选择正在变化的应用 layer。")
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
            fps=fps,
            app_render_fps=app_render_fps,
            app_jank_pct=app_jank_pct,
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
    async def _capture_fps(self, runtime: RuntimeSession) -> float | None:
        if runtime.request.surface_layer is None:
            layers, foreground_package = await asyncio.gather(
                list_surface_layers(runtime.request.serial),
                get_foreground_package(runtime.request.serial),
            )
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
        return parse_surface_latency(output)
