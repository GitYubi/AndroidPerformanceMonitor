"""测试结束后导出并清理车机 ANR / Crash / Tombstone 日志。

- 导出位置：默认项目根目录/DevicesLogs/<结束时间>/ANR|Crash|Tombstone/
  结束时间格式 yyyy_MM_DD-hh_mm_ss；可通过 log_export_root 覆盖根目录。
- 只处理用户配置了路径的类型；路径为空则不动作（前端提示）。
- 仅清理已导出且仍匹配的源文件，保留目录。
- 路径不可读/不存在/无文件均只写会话事件，不中断其他类型的导出。
"""

from __future__ import annotations

from hashlib import file_digest
import os
import re
import shlex
from tempfile import TemporaryDirectory
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adb import AdbError, run_adb

LOG_KIND_ANR = "ANR"
LOG_KIND_CRASH = "Crash"
LOG_KIND_TOMBSTONE = "Tombstone"

# 设备端路径只允许常规路径字符，避免 adb shell 拼接出注入。
_DEVICE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_FILE_IDENTITY = "%d:%i:%s:%y:%z"
_REMOVE_MATCHING_FILE = f"""
[ -f "$1" ] && [ ! -L "$1" ] &&
digest=$(sha256sum < "$1") && [ "${{digest%% *}}" = "$3" ] &&
[ "$(stat -c {_FILE_IDENTITY} "$1")" = "$2" ] &&
[ ! -L "$1" ] && rm -f -- "$1"
"""

# 事件回调：(code, severity, message)
EventSink = Callable[[str, str, str], None]


@dataclass(slots=True)
class DeviceLogConfig:
    anr: str | None = None
    crash: str | None = None
    tombstone: str | None = None
    export_root: str | None = None  # 空 → 项目根目录/DevicesLogs

    def enabled_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.anr:
            pairs.append((LOG_KIND_ANR, self.anr))
        if self.crash:
            pairs.append((LOG_KIND_CRASH, self.crash))
        if self.tombstone:
            pairs.append((LOG_KIND_TOMBSTONE, self.tombstone))
        return pairs


async def _list_files(serial: str, device_path: str) -> list[str]:
    """列出设备路径下的一级文件（目录项过滤掉）。"""
    command = shlex.join(["find", device_path, "-mindepth", "1", "-maxdepth", "1", "-type", "f", "-print0"])
    output = await run_adb("shell", command, serial=serial, timeout_seconds=10)
    return [path.rsplit("/", 1)[-1] for path in output.split("\0") if path]


async def export_and_clean_device_logs(
    serial: str,
    config: DeviceLogConfig,
    project_root: Path,
    on_event: EventSink,
    preserve: bool = False,
) -> dict[str, object]:
    """执行全部已配置类型的日志导出与清理，返回各类型结果摘要。"""
    results: dict[str, object] = {}
    pairs = config.enabled_pairs()
    if not pairs:
        return results

    export_root = Path(config.export_root).expanduser() if config.export_root else project_root / "DevicesLogs"
    folder_name = time.strftime("%Y_%m_%d-%H_%M_%S")

    for kind, device_path in pairs:
        code = f"log_export_{kind.lower()}"
        if not _DEVICE_PATH_RE.fullmatch(device_path):
            on_event(code, "warning", f"{kind} 设备路径包含非法字符，已跳过导出：{device_path}")
            results[kind] = {"status": "invalid_path"}
            continue
        if not device_path.startswith("/"):
            device_path = f"./{device_path}"
        try:
            files = await _list_files(serial, device_path)
        except AdbError as exc:
            on_event(code, "warning", f"{kind}：无法读取 {device_path}（{exc}），已跳过导出。")
            results[kind] = {"status": "error", "detail": str(exc)}
            continue
        if not files:
            on_event(code, "info", f"{kind}：{device_path} 下无日志文件，跳过导出。")
            results[kind] = {"status": "empty"}
            continue

        target_dir = export_root / folder_name / kind
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            on_event(code, "warning", f"{kind}：无法创建导出目录 {target_dir}（{exc}），已保留设备原始日志。")
            results[kind] = {"status": "error", "detail": str(exc)}
            continue
        exported = 0
        removed = 0
        failed_files: list[str] = []
        retained_files: list[str] = []
        for name in files:
            if Path(name).name != name or name in {".", ".."} or "\ufffd" in name or (os.name == "nt" and ":" in name):
                failed_files.append(f"{name}（文件名无法安全保存到本机）")
                continue
            source = f"{device_path.rstrip('/')}/{name}"
            identity = ""
            if not preserve:
                try:
                    identity = (await run_adb(
                        "shell", shlex.join(["stat", "-c", _FILE_IDENTITY, source]),
                        serial=serial, timeout_seconds=10,
                    )).strip()
                except AdbError:
                    pass  # Exporting is still useful. Deletion requires the original identity.
            try:
                with TemporaryDirectory(prefix=".pull-", dir=target_dir) as temporary:
                    local = Path(temporary) / "log"
                    await run_adb("pull", source, str(local), serial=serial, timeout_seconds=120)
                    digest = ""
                    if identity:
                        with local.open("rb") as copy:
                            digest = file_digest(copy, "sha256").hexdigest()
                    local.replace(target_dir / name)
                exported += 1
            except (AdbError, OSError) as exc:
                failed_files.append(f"{name}（{exc}）")
                continue
            if not preserve:
                if not identity:
                    retained_files.append(name)
                    continue
                try:
                    command = shlex.join(["sh", "-c", _REMOVE_MATCHING_FILE, "acpm-log", source, identity, digest])
                    await run_adb("shell", command, serial=serial, timeout_seconds=30)
                    removed += 1
                except AdbError:
                    retained_files.append(name)
        if exported:
            message = f"{kind}：已导出 {exported} 个文件到 {target_dir}；"
            message += "保留车机原始日志。" if preserve else f"已清理 {removed} 个已核验源文件。"
            if failed_files:
                message += f" 拉取失败 {len(failed_files)} 个：" + "；".join(failed_files[:3])
            if retained_files:
                on_event(code, "warning", f"{kind}：{len(retained_files)} 个源文件未能确认清理：" + "；".join(retained_files[:3]))
            on_event(code, "info", message)
            results[kind] = {
                "status": "exported", "exported": exported, "failed": len(failed_files), "target": str(target_dir),
                "cleaned": removed == len(files), "removed": removed,
            }
        else:
            on_event(code, "warning", f"{kind}：{device_path} 下 {len(files)} 个文件全部拉取失败：{'；'.join(failed_files[:3])}")
            results[kind] = {"status": "pull_failed", "files": len(files)}
    return results
