"""Android 11 真实输入事件驱动的 Perfetto 交互诊断。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .adb import AdbError, run_adb

TRACE_CONFIG = """buffers {{
  size_kb: 32768
  fill_policy: RING_BUFFER
}}
duration_ms: {duration_ms}
data_sources {{
  config {{
    name: "linux.ftrace"
    ftrace_config {{
      atrace_categories: "input"
      atrace_categories: "gfx"
      atrace_categories: "view"
      atrace_categories: "wm"
      atrace_categories: "sched"
      atrace_categories: "freq"
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_wakeup"
      ftrace_events: "power/cpu_frequency"
    }}
  }}
}}
"""


class InteractionError(RuntimeError):
    pass


@dataclass(slots=True)
class InteractionRuntime:
    interaction_id: str
    serial: str
    duration_seconds: int
    state: str = "queued"
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    trace_path: Path | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = None

    def serializable(self) -> dict[str, object]:
        return {
            "interaction_id": self.interaction_id,
            "serial": self.serial,
            "duration_seconds": self.duration_seconds,
            "state": self.state,
            "created_at_ms": self.created_at_ms,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "trace_ready": self.trace_path is not None and self.trace_path.exists(),
            "trace_file": self.trace_path.name if self.trace_path else None,
            "error": self.error,
            "mode": "android11_input_frame_lifecycle",
            "notice": "请在录制期间直接操作车机；工具不会注入点击或滑动。",
        }


class InteractionTraceManager:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir / "interaction-traces"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.active: dict[str, InteractionRuntime] = {}
        self.history: dict[str, InteractionRuntime] = {}

    async def start(self, serial: str, duration_seconds: int) -> str:
        if not 5 <= duration_seconds <= 60:
            raise ValueError("交互诊断时长必须在 5 到 60 秒之间")
        if self.has_active_device(serial):
            raise ValueError("该车机已有正在运行的交互诊断")
        interaction_id = str(uuid.uuid4())
        runtime = InteractionRuntime(interaction_id=interaction_id, serial=serial, duration_seconds=duration_seconds)
        self.active[interaction_id] = runtime
        self.history[interaction_id] = runtime
        runtime.task = asyncio.create_task(self._capture(runtime), name=f"interaction-{interaction_id}")
        return interaction_id

    def has_active_device(self, serial: str) -> bool:
        return any(item.serial == serial for item in self.active.values())

    def get(self, interaction_id: str) -> dict[str, object] | None:
        runtime = self.history.get(interaction_id)
        return runtime.serializable() if runtime else None

    def trace_file(self, interaction_id: str) -> Path | None:
        runtime = self.history.get(interaction_id)
        if runtime and runtime.trace_path and runtime.trace_path.exists():
            return runtime.trace_path
        return None

    async def _adb(self, serial: str, *args: str, timeout_seconds: float = 15.0,
                   input_data: bytes | None = None) -> str:
        try:
            return await run_adb(*args, serial=serial, timeout_seconds=timeout_seconds, input_data=input_data)
        except AdbError as exc:
            raise InteractionError(str(exc)) from exc

    async def _capture(self, runtime: InteractionRuntime) -> None:
        token = runtime.interaction_id.replace("-", "")
        trace_path = self.data_dir / f"{token}.pftrace"
        partial = trace_path.with_suffix(".pftrace.part")
        device_trace = f"/data/misc/perfetto-traces/interaction_{token}.pftrace"
        attempted = False
        try:
            runtime.state = "preparing"
            config = TRACE_CONFIG.format(duration_ms=runtime.duration_seconds * 1000).encode("utf-8")
            runtime.state = "recording"
            runtime.started_at_ms = int(time.time() * 1000)
            attempted = True
            await self._adb(
                runtime.serial,
                "shell",
                "-T",
                "perfetto",
                "--txt",
                "-c",
                "-",
                "-o",
                device_trace,
                timeout_seconds=runtime.duration_seconds + 30,
                input_data=config,
            )
            runtime.state = "pulling_trace"
            await self._adb(
                runtime.serial,
                "pull",
                device_trace,
                str(partial),
                timeout_seconds=90,
            )
            if partial.stat().st_size == 0:
                raise InteractionError("Perfetto 未生成有效 trace")
            partial.replace(trace_path)
            runtime.trace_path = trace_path
            runtime.state = "completed"
        except asyncio.CancelledError:
            runtime.state = "interrupted"
            runtime.error = "后端关闭，交互诊断已中断"
            raise
        except asyncio.TimeoutError:
            runtime.state = "failed"
            runtime.error = "ADB 或 Perfetto 命令未在限定时间内结束，已自动终止本次诊断"
        except (InteractionError, OSError, KeyError, ValueError) as exc:
            runtime.state = "failed"
            runtime.error = str(exc) or "Perfetto 交互诊断失败"
        finally:
            try:
                if attempted:
                    try:
                        await self._adb(runtime.serial, "shell", "rm", "-f", device_trace, timeout_seconds=5)
                    except (InteractionError, OSError):
                        note = f"无法确认设备临时 trace 已清理，请检查 {device_trace}"
                        runtime.error = f"{runtime.error}。{note}" if runtime.error else note
            finally:
                try:
                    partial.unlink(missing_ok=True)
                except OSError as exc:
                    note = f"无法清理本机未完成的 trace：{exc}"
                    runtime.error = f"{runtime.error}。{note}" if runtime.error else note
                runtime.ended_at_ms = int(time.time() * 1000)
                self.active.pop(runtime.interaction_id, None)

    async def close(self) -> None:
        runtimes = list(self.active.values())
        tasks = [runtime.task for runtime in runtimes if runtime.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for runtime in runtimes:
            if self.active.pop(runtime.interaction_id, None) is not None:
                # Cancellation can precede the task's first execution.
                runtime.state = "interrupted"
                runtime.error = "后端关闭，交互诊断已中断"
                runtime.ended_at_ms = int(time.time() * 1000)
        if self.history:
            self.save_manifest()

    def save_manifest(self) -> None:
        manifest = self.data_dir / "manifest.json"
        previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
        records = {item["interaction_id"]: item for item in previous}
        records.update((item.interaction_id, item.serializable()) for item in self.history.values())
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(list(records.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest)
