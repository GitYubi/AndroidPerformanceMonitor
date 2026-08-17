"""测试结束后导出并清理车机 ANR / Crash / Tombstone 日志。

- 导出位置：默认项目根目录/DevicesLogs/<结束时间>/ANR|Crash|Tombstone/
  结束时间格式 yyyy_MM_DD-hh_mm_ss；可通过 log_export_root 覆盖根目录。
- 只处理用户配置了路径的类型；路径为空则不动作（前端提示）。
- 导出成功后删除车机内对应日志文件但保留目录（rm -f <path>/*）。
- 路径不可读/不存在/无文件均只写会话事件，不中断其他类型的导出。
"""

from __future__ import annotations

import re
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
    output = await run_adb("shell", "ls", "-A", "-p", device_path, serial=serial, timeout_seconds=10)
    return [line.strip().rstrip("/") for line in output.splitlines() if line.strip() and not line.strip().endswith("/")]


async def export_and_clean_device_logs(
    serial: str,
    config: DeviceLogConfig,
    project_root: Path,
    on_event: EventSink,
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
        if not _DEVICE_PATH_RE.match(device_path):
            on_event(code, "warning", f"{kind} 设备路径包含非法字符，已跳过导出：{device_path}")
            results[kind] = {"status": "invalid_path"}
            continue
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
        target_dir.mkdir(parents=True, exist_ok=True)
        exported = 0
        failed_files: list[str] = []
        for name in files:
            try:
                await run_adb("pull", f"{device_path}/{name}", str(target_dir), serial=serial, timeout_seconds=120)
                exported += 1
            except AdbError as exc:
                failed_files.append(f"{name}（{exc}）")
        if exported:
            try:
                await run_adb("shell", "rm", "-f", f"{device_path}/*", serial=serial, timeout_seconds=30)
                cleaned = True
            except AdbError as exc:
                cleaned = False
                on_event(code, "warning", f"{kind}：日志已导出但车机清理失败：{exc}")
            message = (
                f"{kind}：已导出 {exported} 个文件到 {target_dir}；"
                + ("车机日志已清理（保留目录）。" if cleaned else "车机日志清理失败。")
                + (f" 失败 {len(failed_files)} 个：" + "；".join(failed_files[:3]) if failed_files else "")
            )
            on_event(code, "info", message)
            results[kind] = {"status": "exported", "exported": exported, "failed": len(failed_files), "target": str(target_dir), "cleaned": cleaned}
        else:
            on_event(code, "warning", f"{kind}：{device_path} 下 {len(files)} 个文件全部拉取失败：{'；'.join(failed_files[:3])}")
            results[kind] = {"status": "pull_failed", "files": len(files)}
    return results
