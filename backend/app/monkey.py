"""Managed Monkey + hourly performance capture; no random input runs in the host process."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import shlex
import shutil
import time
from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .adb import AdbError, list_devices, run_adb
from .models import MetricsConfig, StartSessionRequest
from .monkey_records import MonkeyRecords, MonkeyReportStore
from .report import ReportMetric, ReportTheme, generate_report

router = APIRouter(prefix="/api/monkey", tags=["Monkey"])
HOUR_SECONDS = 3600
HEARTBEAT_SECONDS = 15
LEASE_SECONDS = 90
LOG_LIMIT = 8 * 1024 * 1024
MIN_FREE = 128 * 1024 * 1024
TERMINAL = {"completed", "stopped", "failed", "interrupted"}
PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
RUN_ID = re.compile(r"[a-f0-9]{32}")
ROOT = Path(__file__).resolve().parents[2]


class EventRatios(BaseModel):
    model_config = ConfigDict(extra="forbid")
    touch: int = Field(default=45, ge=0, le=100, strict=True)
    motion: int = Field(default=25, ge=0, le=100, strict=True)
    pinchzoom: int = Field(default=5, ge=0, le=100, strict=True)
    appswitch: int = Field(default=10, ge=0, le=100, strict=True)
    nav: int = Field(default=5, ge=0, le=100, strict=True)
    majornav: int = Field(default=5, ge=0, le=100, strict=True)
    syskeys: int = Field(default=5, ge=0, le=100, strict=True)
    trackball: int = Field(default=0, ge=0, le=100, strict=True)
    rotation: int = Field(default=0, ge=0, le=100, strict=True)
    flip: int = Field(default=0, ge=0, le=100, strict=True)
    anyevent: int = Field(default=0, ge=0, le=100, strict=True)

    @model_validator(mode="after")
    def total(self):
        if sum(self.model_dump().values()) != 100:
            raise ValueError("Monkey 事件占比之和必须为 100%")
        return self


class MonkeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serial: str = Field(min_length=1, max_length=128)
    mode: Literal["none", "blacklist", "whitelist"] = "blacklist"
    packages: list[str] = Field(default_factory=list, max_length=1000)
    ratios: EventRatios = Field(default_factory=EventRatios)
    duration_hours: int | None = Field(default=1, ge=1, le=8760, strict=True)
    throttle_ms: int = Field(default=300, ge=50, le=10000, strict=True)
    seed: int = Field(default=42, ge=0, le=2147483647, strict=True)
    interval_ms: int = Field(default=2000, ge=1000, le=5000, strict=True)
    memory_cycle_skip: int = Field(default=5, ge=1, le=60, strict=True)
    fps: bool = False
    ignore_crashes: bool = True
    ignore_timeouts: bool = True

    @field_validator("serial")
    @classmethod
    def serial_safe(cls, value):
        return StartSessionRequest.serial_is_safe(value)

    @field_validator("packages")
    @classmethod
    def package_names(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if len(value) > 255 or not PACKAGE.fullmatch(value):
                raise ValueError(f"无效的 Android 包名：{value[:80]}")
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def list_mode(self):
        if self.mode == "none" and self.packages:
            raise ValueError("无名单模式不能携带包名单")
        if self.mode != "none" and not self.packages:
            raise ValueError("请选择或填写至少一个包名")
        return self


def remote_paths(run_id: str) -> dict[str, str]:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("无效的 Monkey 任务 ID")
    base = "/data/local/tmp/acpm-monkey-" + run_id
    return {"script": base + ".sh", "packages": base + ".packages", "pid": base + ".pid", "lease": base + ".lease"}


def monkey_arguments(config: MonkeyRequest, paths: dict[str, str]) -> list[str]:
    args = ["--throttle", str(config.throttle_ms), "-s", str(config.seed), "--ignore-security-exceptions"]
    if config.ignore_crashes:
        args.append("--ignore-crashes")
    if config.ignore_timeouts:
        args.append("--ignore-timeouts")
    for name, percent in config.ratios.model_dump().items():
        args.extend(["--pct-" + name, str(percent)])
    if config.mode != "none":
        args.extend(["--pkg-" + config.mode + "-file", paths["packages"]])
    # Large event budget + elapsed-time supervisor. No verbose per-event logging.
    return [*args, "2147483647"]


def runner_script(config: MonkeyRequest, paths: dict[str, str]) -> str:
    command = shlex.join(["app_process", "/system/bin", "com.android.commands.monkey.Monkey", *monkey_arguments(config, paths)])
    duration = config.duration_hours * HOUR_SECONDS if config.duration_hours else 0
    return f"""#!/system/bin/sh
set -u
touch {paths['lease']}
started=$(date +%s)
CLASSPATH=/system/framework/monkey.jar {command} &
child=$!
stamp=$(awk '{{print $22}}' /proc/$child/stat 2>/dev/null)
printf '%s %s\\n' "$child" "$stamp" > {paths['pid']}
echo __ACPM_MONKEY_PID__=$child
(
  while kill -0 "$child" 2>/dev/null; do
    sleep 5
    modified=$(stat -c %Y {paths['lease']} 2>/dev/null) || modified=0
    now=$(date +%s)
    if [ $((now-modified)) -ge {LEASE_SECONDS} ] || {{ [ {duration} -gt 0 ] && [ $((now-started)) -ge {duration} ]; }}; then
      echo __ACPM_DEVICE_GUARD_STOP__
      kill "$child" 2>/dev/null
      sleep 2
      current=$(awk '{{print $22}}' /proc/$child/stat 2>/dev/null)
      [ "$current" != "$stamp" ] || kill -9 "$child" 2>/dev/null
      break
    fi
  done
) &
watcher=$!
cleanup() {{
  kill "$watcher" 2>/dev/null || true
  current=$(awk '{{print $22}}' /proc/$child/stat 2>/dev/null)
  if [ -n "$stamp" ] && [ "$current" = "$stamp" ]; then
    kill "$child" 2>/dev/null || true
    sleep 1
    current=$(awk '{{print $22}}' /proc/$child/stat 2>/dev/null)
    [ "$current" != "$stamp" ] || kill -9 "$child" 2>/dev/null || true
  fi
  rm -f {shlex.join(list(paths.values()))}
}}
trap cleanup EXIT
trap 'exit 143' HUP INT TERM
wait "$child"
code=$?
echo __ACPM_MONKEY_EXIT__=$code
exit "$code"
"""


@dataclass
class Runtime:
    id: str
    config: MonkeyRequest
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    process: asyncio.subprocess.Process | None = None
    reader: asyncio.Task | None = None
    heartbeat: asyncio.Task | None = None
    interrupted: bool = False
    failure: str | None = None
    tail: bytes = b""
    received_pid: bool = False
    sampling_task: asyncio.Task | None = None
    entered: bool = False
    expired: bool = False
    timer: asyncio.Task | None = None
    cleanup_task: asyncio.Task | None = None


class MonkeyManager:
    def __init__(self, root: Path, monitor, ui, interactions=None):
        self.store = MonkeyRecords(root)
        self.monitor = monitor
        self.ui = ui
        self.interactions = interactions
        self.active: dict[str, Runtime] = {}
        self.lock = asyncio.Lock()

    async def start(self, config: MonkeyRequest) -> dict:
        if self.ui.device_lock(config.serial).locked():
            raise ValueError("设备正在进行界面操作或画面控制，请先结束操作")
        async with self.lock, self.ui.device_lock(config.serial):
            if self.active or self.ui.has_active_device(config.serial):
                raise ValueError("设备已有 Monkey、UI 用例或操作任务，请先停止")
            if self.interactions and any(item.serial == config.serial and item.state in {"queued", "recording"} for item in self.interactions.active.values()):
                raise ValueError("设备正在录制交互诊断，请等待录制结束")
            if shutil.disk_usage(self.store.root).free < 512 * 1024 * 1024:
                raise ValueError("存储剩余空间不足 512 MiB，请先清理测试记录")
            run_id = uuid.uuid4().hex
            self.monitor.reserve(run_id)
            try:
                devices = await list_devices()
                if not any(d.serial == config.serial and d.state == "device" for d in devices):
                    raise ValueError("设备未连接或未授权")
                existing = await run_adb("shell", "pidof com.android.commands.monkey com.android.commands.monkey.Monkey || true", serial=config.serial)
                if existing.strip():
                    raise ValueError("设备已有其他 Monkey 进程，请先结束该进程")
                if config.mode == "whitelist":
                    installed = await run_adb("shell", "pm", "list", "packages", serial=config.serial)
                    names = {line.removeprefix("package:").strip() for line in installed.splitlines()}
                    missing = set(config.packages) - names
                    if missing:
                        raise ValueError("白名单中存在未安装应用：" + "、".join(sorted(missing)[:10]))
                run = {"id": run_id, "serial": config.serial, "state": "starting", "config": config.model_dump(),
                       "created_at_ms": int(time.time() * 1000), "started_at_ms": None, "ended_at_ms": None,
                       "current_session_id": None, "pending_session_id": None, "segment_count": 0,
                       "message": "正在准备设备端 Monkey", "cleanup_warning": None}
                self.store.put(run)
                self.store.initialize(run)
                runtime = Runtime(run_id, config)
                self.active[run_id] = runtime
                self.ui.external_busy[config.serial] = run_id
                runtime.task = asyncio.create_task(self.execute(runtime), name="monkey-" + run_id)
                return run
            except BaseException:
                if "run" in locals():
                    run.update(state="failed", ended_at_ms=int(time.time() * 1000), message="任务初始化失败")
                    self.store.put(run)
                self.monitor.reserved_by = None
                raise

    async def launch(self, runtime: Runtime) -> None:
        paths = remote_paths(runtime.id)
        root = self.store.directory(runtime.id)
        script = root / "runner.sh"
        script.write_text(runner_script(runtime.config, paths), encoding="utf-8", newline="\n")
        packages = root / "packages.txt"
        packages.write_text("\n".join(runtime.config.packages) + "\n", encoding="utf-8", newline="\n")
        await run_adb("shell", "test -r /system/framework/monkey.jar && command -v app_process && command -v awk && stat -c %Y /data/local/tmp", serial=runtime.config.serial)
        await run_adb("push", str(script), paths["script"], serial=runtime.config.serial, timeout_seconds=15)
        if runtime.config.mode != "none":
            await run_adb("push", str(packages), paths["packages"], serial=runtime.config.serial, timeout_seconds=15)
        adb = shutil.which("adb")
        if not adb:
            raise AdbError("未找到 adb")
        runtime.process = await asyncio.create_subprocess_exec(adb, "-s", runtime.config.serial, "shell", "sh", paths["script"],
                                                               stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                               stderr=asyncio.subprocess.STDOUT, limit=32768)
        runtime.reader = asyncio.create_task(self.read_output(runtime))
        await asyncio.wait_for(runtime.ready.wait(), 20)
        if not runtime.received_pid or runtime.process.returncode is not None:
            raise ValueError("Monkey 启动失败：" + runtime.tail.decode(errors="replace")[-1000:])

    async def read_output(self, runtime: Runtime) -> None:
        path = self.store.directory(runtime.id) / "monkey.log"
        try:
            with path.open("ab") as log:
                size = path.stat().st_size
                while chunk := await runtime.process.stdout.read(8192):
                    if size + len(chunk) > LOG_LIMIT:
                        log.close()
                        path.replace(path.with_suffix(".log.1"))
                        log = path.open("wb")
                        size = 0
                    log.write(chunk)
                    log.flush()
                    size += len(chunk)
                    runtime.tail = (runtime.tail + chunk)[-8192:]
                    if re.search(rb"__ACPM_MONKEY_PID__=[1-9][0-9]*\s", runtime.tail):
                        runtime.received_pid = True
                        runtime.ready.set()
        except Exception as exc:
            runtime.failure = f"Monkey 输出记录失败：{exc}"
            runtime.stop.set()
            self.stop_input(runtime)
        finally:
            if "log" in locals():
                log.close()
            # Wake failed startup promptly, including a command exiting without a PID.
            runtime.ready.set()

    def stop_input(self, runtime: Runtime) -> None:
        if runtime.cleanup_task is None:
            runtime.cleanup_task = asyncio.create_task(self.cleanup(runtime.id, runtime.config.serial, runtime.process))

    async def deadline(self, runtime: Runtime, seconds: float) -> None:
        await asyncio.sleep(seconds)
        runtime.expired = True
        runtime.stop.set()
        self.stop_input(runtime)

    async def heartbeat(self, runtime: Runtime) -> None:
        while not runtime.stop.is_set():
            try:
                if shutil.disk_usage(self.store.root).free < MIN_FREE:
                    raise ValueError("磁盘剩余不足 128 MiB，已停止以保护已采集记录")
                await run_adb("shell", "touch", remote_paths(runtime.id)["lease"], serial=runtime.config.serial)
                await asyncio.wait_for(runtime.stop.wait(), HEARTBEAT_SECONDS)
            except TimeoutError:
                continue
            except Exception as exc:
                runtime.failure = f"设备守护/存储检查失败：{exc}"
                runtime.stop.set()
                self.stop_input(runtime)

    async def start_segment(self, runtime: Runtime, seconds: float) -> str:
        config = runtime.config
        session_id = await self.monitor.start(StartSessionRequest(
            serial=config.serial, duration_seconds=max(1, min(3600, math.ceil(seconds))),
            interval_ms=config.interval_ms, memory_cycle_skip=config.memory_cycle_skip,
            metrics=MetricsConfig(cpu=True, memory=True, fps=config.fps)), owner=runtime.id)
        run = self.store.get(runtime.id)
        run["current_session_id"] = session_id
        self.store.put(run)
        runtime.sampling_task = self.monitor.active[session_id].task
        return session_id

    async def archive(self, runtime: Runtime, session_id: str) -> None:
        path = self.monitor.store.database_path(session_id)
        number = await asyncio.to_thread(self.store.archive, runtime.id, session_id, path)
        run = self.store.get(runtime.id)
        run.update(segment_count=number, pending_session_id=None)
        if run["current_session_id"] == session_id:
            run["current_session_id"] = None
        self.store.put(run)

    async def execute(self, runtime: Runtime) -> None:
        runtime.entered = True
        wait_process = stop_wait = None
        current = None
        state = "failed"
        message = ""
        try:
            if runtime.stop.is_set():
                state = "interrupted" if runtime.interrupted else "stopped"
                return
            await self.launch(runtime)
            if runtime.stop.is_set():
                state = "stopped"
                return
            started = time.monotonic()
            deadline = started + runtime.config.duration_hours * HOUR_SECONDS if runtime.config.duration_hours else math.inf
            run = self.store.get(runtime.id)
            run.update(state="running", started_at_ms=int(time.time() * 1000), message="Monkey 与性能采样运行中")
            self.store.put(run)
            self.store.started(runtime.id, run["started_at_ms"])
            runtime.heartbeat = asyncio.create_task(self.heartbeat(runtime))
            if runtime.config.duration_hours:
                runtime.timer = asyncio.create_task(self.deadline(runtime, max(0, deadline - time.monotonic())))
            wait_process = asyncio.create_task(runtime.process.wait())
            stop_wait = asyncio.create_task(runtime.stop.wait())
            current = await self.start_segment(runtime, min(HOUR_SECONDS, deadline - time.monotonic()))
            while True:
                task = runtime.sampling_task
                remaining = deadline - time.monotonic()
                done, _ = await asyncio.wait({wait_process, stop_wait, task}, timeout=max(0, remaining), return_when=asyncio.FIRST_COMPLETED)
                if runtime.failure:
                    raise ValueError(runtime.failure)
                if stop_wait in done or runtime.stop.is_set():
                    state = "completed" if runtime.expired else "interrupted" if runtime.interrupted else "stopped"
                    break
                if time.monotonic() >= deadline:
                    state = "completed"
                    break
                if wait_process in done:
                    raise ValueError("Monkey 在计划结束前退出：" + runtime.tail.decode(errors="replace")[-1500:])
                if task in done:
                    await task
                    session = self.monitor.store.get_session(current)
                    if session["state"] != "completed":
                        raise ValueError("性能采样异常结束，已同步停止 Monkey")
                    previous = current
                    run = self.store.get(runtime.id)
                    run.update(pending_session_id=previous, current_session_id=None)
                    self.store.put(run)
                    # Start the next capture before CSV/SQL work; export runs in a worker thread.
                    current = await self.start_segment(runtime, min(HOUR_SECONDS, deadline - time.monotonic()))
                    await self.archive(runtime, previous)
        except asyncio.CancelledError:
            state, message = ("interrupted", "后端关闭，任务已中断") if runtime.interrupted else ("stopped", "任务已停止")
        except Exception as exc:
            if runtime.stop.is_set() and not runtime.failure:
                state = "completed" if runtime.expired else "interrupted" if runtime.interrupted else "stopped"
            else:
                state, message = "failed", str(exc)[:2000]
        finally:
            runtime.stop.set()
            cleanup_warning = None
            run = self.store.get(runtime.id)
            run.update(state="stopping", message=message or "正在停止 Monkey 并归档记录")
            self.store.put(run)
            try:
                self.stop_input(runtime)
                await runtime.cleanup_task
            except Exception as exc:
                cleanup_warning = f"设备端清理未确认：{exc}；租约守护会在约{LEASE_SECONDS}秒内停止输入"
            for pending in [runtime.heartbeat, runtime.timer, wait_process, stop_wait]:
                if pending:
                    pending.cancel()
            await asyncio.gather(*(t for t in [runtime.heartbeat, runtime.timer, wait_process, stop_wait] if t), return_exceptions=True)
            try:
                if current:
                    await self.monitor.stop(current, owner=runtime.id)
                # Crash-safe pending pointer also covers an interrupted hourly export.
                saved = self.store.get(runtime.id)
                for session_id in dict.fromkeys([saved.get("pending_session_id"), saved.get("current_session_id")]):
                    if session_id:
                        await self.archive(runtime, session_id)
                ended = int(time.time() * 1000)
                summary = await asyncio.to_thread(self.store.summarize, runtime.id, state, ended)
                run["segment_count"] = summary["segment_count"]
            except Exception as exc:
                state, message = "failed", f"归档未完成，原始性能记录仍保留：{exc}"
            finally:
                run = self.store.get(runtime.id)
                if cleanup_warning:
                    run["cleanup_warning"] = cleanup_warning
                run.update(state=state, ended_at_ms=int(time.time() * 1000), current_session_id=None,
                           message=message or {"completed": "已完成并合并记录", "stopped": "已停止并合并记录", "interrupted": "已中断并保留记录"}.get(state, "执行失败"))
                self.store.put(run)
                self.monitor.reserved_by = None
                self.ui.external_busy.pop(runtime.config.serial, None)
                self.active.pop(runtime.id, None)

    async def cleanup(self, run_id: str, serial: str, process=None) -> None:
        paths = remote_paths(run_id)
        script = f"""if [ -f {paths['pid']} ]; then
read pid stamp < {paths['pid']}
case "$pid" in ''|*[!0-9]*) exit 1;; esac
current=$(awk '{{print $22}}' /proc/$pid/stat 2>/dev/null)
if [ -n "$stamp" ] && [ "$current" = "$stamp" ]; then
  kill "$pid" 2>/dev/null || true
  sleep 1
  current=$(awk '{{print $22}}' /proc/$pid/stat 2>/dev/null)
  [ "$current" != "$stamp" ] || kill -9 "$pid" 2>/dev/null || true
fi
fi
rm -f {shlex.join(list(paths.values()))}"""
        try:
            await run_adb("shell", shlex.join(["sh", "-c", script]), serial=serial, timeout_seconds=8)
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            runtime = self.active.get(run_id)
            if runtime and runtime.reader:
                await asyncio.gather(runtime.reader, return_exceptions=True)

    def stop(self, run_id: str) -> dict:
        run = self.store.get(run_id)
        runtime = self.active.get(run_id)
        if runtime:
            runtime.stop.set()
            if run["state"] == "starting" and runtime.entered:
                runtime.task.cancel()
            elif run["state"] != "starting":
                self.stop_input(runtime)
            run.update(state="stopping", message="正在停止并归档，原始记录会保留")
            self.store.put(run)
        return run

    async def close(self) -> None:
        tasks = []
        for runtime in list(self.active.values()):
            runtime.interrupted = True
            runtime.stop.set()
            if runtime.entered:
                if self.store.get(runtime.id)["state"] == "starting":
                    runtime.task.cancel()
                else:
                    self.stop_input(runtime)
            tasks.append(runtime.task)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def recover(self) -> None:
        # At most one active capture is allowed. No unbounded list of old samples.
        for run in self.store.runs(unfinished=True):
            try:
                await self.cleanup(run["id"], run["serial"])
            except Exception as exc:
                run["cleanup_warning"] = f"重启后设备端清理未确认：{exc}；租约将自动到期"
            try:
                for session_id in dict.fromkeys([run.get("pending_session_id"), run.get("current_session_id")]):
                    if session_id and self.monitor.store.database_path(session_id).is_file():
                        await asyncio.to_thread(self.store.archive, run["id"], session_id, self.monitor.store.database_path(session_id))
                summary = await asyncio.to_thread(self.store.summarize, run["id"], "interrupted", int(time.time() * 1000))
                run["segment_count"] = summary["segment_count"]
            except Exception as exc:
                run["cleanup_warning"] = f"部分归档待恢复，原始数据保留：{exc}"
            run.update(state="interrupted", ended_at_ms=int(time.time() * 1000), current_session_id=None,
                       message="服务重启，已中断；不会自动重新执行 Monkey")
            self.store.put(run)


def manager(request: Request) -> MonkeyManager:
    return request.app.state.monkey


@router.get("/defaults")
async def defaults():
    path = ROOT / "tools/monkey/android_system_blacklist.txt"
    packages = [line.strip() for line in path.read_text().splitlines() if PACKAGE.fullmatch(line.strip())] if path.exists() else []
    return {"ratios": EventRatios().model_dump(), "blacklist": packages}


@router.get("/runs")
async def runs(request: Request, offset: int = Query(default=0, ge=0)):
    return manager(request).store.runs(offset=offset, brief=True)


@router.post("/runs", status_code=202)
async def start(config: MonkeyRequest, request: Request):
    try:
        return await manager(request).start(config)
    except (AdbError, ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/runs/{run_id}")
async def detail(run_id: str, request: Request, offset: int = Query(default=0, ge=0)):
    try:
        store = manager(request).store
        run = store.get(run_id)
        segments = store.segments(run_id, offset)
        for segment in segments:
            segment["csv_ready"] = (store.directory(run_id) / f"hour-{segment['number']:04d}.csv").is_file()
        return {**run, "segments": segments, "log_tail": store.tail(run_id)}
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, "Monkey 任务不存在") from exc


@router.post("/runs/{run_id}/stop", status_code=202)
async def stop(run_id: str, request: Request):
    try:
        return manager(request).stop(run_id)
    except KeyError as exc:
        raise HTTPException(404, "Monkey 任务不存在") from exc


@router.get("/runs/{run_id}/files/{name}")
async def download(run_id: str, name: str, request: Request):
    try:
        store = manager(request).store
        run = store.get(run_id)
        if name in {"combined.db", "combined.csv"}:
            if run["state"] not in TERMINAL:
                raise HTTPException(409, "停止并合并完成后可下载总记录")
            path = store.database(run_id) if name == "combined.db" else store.directory(run_id) / name
            if name == "combined.csv" and not path.is_file() and store.database(run_id).is_file():
                path = await asyncio.to_thread(store.export_combined_csv, run_id)
        elif name == "summary.json":
            path = store.directory(run_id) / name
        elif re.fullmatch(r"hour-[0-9]{4,6}\.csv", name):
            path = store.directory(run_id) / name
        else:
            raise HTTPException(404, "文件不存在")
        if not path.is_file():
            raise HTTPException(404, "文件尚未生成")
        return FileResponse(path, filename=f"monkey-{run_id[:8]}-{name}")
    except KeyError as exc:
        raise HTTPException(404, "Monkey 任务不存在") from exc


@router.get("/runs/{run_id}/summary")
async def summary(run_id: str, request: Request):
    import json
    try:
        store = manager(request).store
        path = store.directory(run_id) / "summary.json"
        if not path.exists():
            raise HTTPException(409, "汇总在任务结束归档后生成")
        return json.loads(path.read_text(encoding="utf-8"))
    except KeyError as exc:
        raise HTTPException(404, "Monkey 任务不存在") from exc


@router.get("/runs/{run_id}/report", response_class=HTMLResponse)
async def report(
    run_id: str,
    request: Request,
    metrics: Annotated[list[ReportMetric] | None, Query()] = None,
    theme: ReportTheme = "dark",
    download: Annotated[int, Query(ge=0, le=1)] = 0,
):
    try:
        store = manager(request).store
        run = store.get(run_id)
        if run["state"] not in TERMINAL:
            raise HTTPException(409, "停止并合并完成后可生成全程性能图")
        database = store.database(run_id)
        if not database.is_file() or not run.get("segment_count"):
            raise HTTPException(404, "没有可用于生成图表的性能记录")
        content = await asyncio.to_thread(
            generate_report, run_id, MonkeyReportStore(database), metrics=metrics, theme=theme
        )
        headers = {"Content-Disposition": f'attachment; filename="monkey-{run_id[:8]}-report.html"'} if download else None
        return HTMLResponse(content, headers=headers)
    except KeyError as exc:
        raise HTTPException(404, "Monkey 任务不存在") from exc
