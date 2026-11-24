"""测试结束后导出车机 ANR / Crash / Tombstone 日志，可选清理源文件。

- 导出位置：默认项目根目录/DevicesLogs/<结束时间>/ANR|Crash|Tombstone/
  结束时间格式 yyyy_MM_DD-hh_mm_ss；可通过 log_export_root 覆盖根目录。
- Crash 留空使用默认目录；ANR 和 Tombstone 在独立性能监控中留空不导出。
- 默认保留原件。启用清理时，仅删除已保存且仍匹配的源文件。
- 路径不可读/不存在/无文件均只写会话事件，不中断其他类型的导出。
"""

from __future__ import annotations

import asyncio
from hashlib import file_digest
import json
import os
import re
import shlex
import shutil
from tempfile import TemporaryDirectory
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from zipfile import ZipFile

from .adb import AdbError, run_adb

LOG_KIND_ANR = "ANR"
LOG_KIND_CRASH = "Crash"
LOG_KIND_TOMBSTONE = "Tombstone"
DEFAULT_CRASH_PATH = "/data/system/dropbox"
DEFAULT_LOG_DIRECTORIES = (
    (LOG_KIND_ANR, "/data/anr"),
    (LOG_KIND_CRASH, DEFAULT_CRASH_PATH),
    (LOG_KIND_TOMBSTONE, "/data/tombstones"),
)
STANDARD_DIRECTORIES = {path for _, path in DEFAULT_LOG_DIRECTORIES} | {"/data/dropbox"}
DIRECT_PULL_TIMEOUT_SECONDS = 45
_WINDOWS = os.name == "nt"

# 设备端路径只允许常规路径字符，避免 adb shell 拼接出注入。
_DEVICE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_FILE_IDENTITY = "%d:%i:%s:%y:%z"
_REMOVE_MATCHING_FILE = f"""
[ -f "$1" ] && [ ! -L "$1" ] &&
digest=$(sha256sum < "$1") && [ "${{digest%% *}}" = "$3" ] &&
[ "$(stat -c {_FILE_IDENTITY} "$1")" = "$2" ] &&
[ ! -L "$1" ] && rm -f -- "$1"
"""
_SNAPSHOT_FILES = f"""
find "$1" -type f -exec sh -c '
    for file do
        identity=$(stat -c {_FILE_IDENTITY} "$file") || continue
        printf "%s\\000%s\\000" "$file" "$identity"
    done
' acpm-stat {{}} +
"""
_REMOVE_SNAPSHOT_FILES = """
remove_verified() {
""" + _REMOVE_MATCHING_FILE + """
}
removed=0
while IFS= read -r -d '' file && IFS= read -r -d '' identity && IFS= read -r -d '' digest; do
    if [ "$(realpath "$file")" = "$file" ] && remove_verified "$file" "$identity" "$digest"; then
        removed=$((removed + 1))
    fi
done
printf "%s\\n" "$removed"
"""

# 事件回调：(code, severity, message)
EventSink = Callable[[str, str, str], None]


def _crash_source(path: str | None) -> str:
    return (path or "").strip() or DEFAULT_CRASH_PATH


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
        pairs.append((LOG_KIND_CRASH, _crash_source(self.crash)))
        if self.tombstone:
            pairs.append((LOG_KIND_TOMBSTONE, self.tombstone))
        return pairs


async def _list_files(serial: str, device_path: str) -> list[str]:
    """列出设备路径下的一级文件（目录项过滤掉）。"""
    command = shlex.join(["find", device_path, "-mindepth", "1", "-maxdepth", "1", "-type", "f", "-print0"])
    output = await run_adb("shell", command, serial=serial, timeout_seconds=10)
    return [path.rsplit("/", 1)[-1] for path in output.split("\0") if path]


def _local_log_files(directory: Path) -> Iterator[Path]:
    return (path for path in directory.rglob("*") if not path.is_symlink() and path.is_file())


async def _pull_to_staging(serial: str, source: str, local: Path, timeout_seconds: float) -> None:
    if not _WINDOWS:
        await run_adb("pull", source, str(local), serial=serial, timeout_seconds=timeout_seconds)
        return

    # Keep the installation path out of Windows ADB's local path handling.
    # Only the parent should exist so ADB does not append the remote basename.
    with TemporaryDirectory(prefix="acpm-") as temporary:
        await run_adb("pull", source, local.name, serial=serial, timeout_seconds=timeout_seconds, cwd=temporary)
        pulled = Path(temporary) / local.name
        if not pulled.exists():
            return  # ADB can succeed without creating a destination for an empty directory.
        # The system temporary directory and the report directory can be on different drives.
        move = asyncio.create_task(asyncio.to_thread(shutil.move, pulled, local))
        try:
            await asyncio.shield(move)
        except asyncio.CancelledError:
            # Do not remove either staging directory while a cross-drive copy is running.
            await asyncio.gather(move, return_exceptions=True)
            raise


async def pull_device_log_directory(serial: str, source: str, target_dir: Path) -> dict[str, object]:
    """Copy one log directory via ADB sync without running a device shell or deleting source logs."""
    if not _DEVICE_PATH_RE.fullmatch(source):
        return {"status": "invalid_path", "detail": "设备路径包含非法字符"}
    try:
        if target_dir.exists() and (not target_dir.is_dir() or any(target_dir.iterdir())):
            raise OSError(f"本地导出目录已存在且不可替换：{target_dir}")
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".log-pull-", dir=target_dir.parent) as temporary:
            local = Path(temporary) / "logs"
            await _pull_to_staging(serial, source, local, DIRECT_PULL_TIMEOUT_SECONDS)
            if not local.is_dir():
                raise OSError("设备路径不是日志目录")
            count = sum(1 for _ in _local_log_files(local))
            if count == 0:
                return {"status": "empty"}
            if target_dir.is_dir():
                target_dir.rmdir()
            local.replace(target_dir)
        return {"status": "exported", "exported": count, "failed": 0,
                "target": str(target_dir), "cleaned": False, "removed": 0}
    except (AdbError, OSError) as exc:
        return {"status": "error", "detail": str(exc)}


async def _snapshot_files(serial: str, source: str) -> dict[str, str]:
    command = shlex.join(["sh", "-c", _SNAPSHOT_FILES, "acpm-snapshot", source])
    try:
        output = await run_adb("shell", command, serial=serial, timeout_seconds=8)
    except AdbError:
        return {}
    entries = output.split("\0")
    return {file: identity for file, identity in zip(entries[::2], entries[1::2])
            if file.startswith(source + "/") and "\ufffd" not in file and identity}


async def _pull_log_directory(
    serial: str, source: str, target_dir: Path, *, preserve: bool,
) -> tuple[dict[str, object], dict[str, str]]:
    if not _DEVICE_PATH_RE.fullmatch(source):
        return {"source": source, "status": "invalid_path", "detail": "设备路径包含非法字符"}, {}
    try:
        async with asyncio.timeout(DIRECT_PULL_TIMEOUT_SECONDS):
            snapshot = {} if preserve else await _snapshot_files(serial, source)
            result = await pull_device_log_directory(serial, source, target_dir)
    except TimeoutError:
        return {"source": source, "status": "error", "detail": "拉取设备日志超时"}, {}
    return {**result, "source": source}, snapshot


def _cleanup_payload(entries, source: str, snapshot: dict[str, str]) -> bytes:
    payload = bytearray()
    for name, open_copy in entries:
        remote = f"{source}/{name}"
        if remote in snapshot:
            with open_copy() as copy:
                digest = file_digest(copy, "sha256").hexdigest()
            payload.extend(f"{remote}\0{snapshot[remote]}\0{digest}\0".encode())
    return bytes(payload)


def _directory_cleanup_payload(directory: Path, source: str, snapshot: dict[str, str]) -> bytes:
    return _cleanup_payload(
        ((path.relative_to(directory).as_posix(), lambda path=path: path.open("rb"))
         for path in _local_log_files(directory)),
        source, snapshot,
    )


async def _remove_exported_files(serial: str, result: dict[str, object], payload: bytes) -> None:
    removed = 0
    if payload:
        try:
            output = await run_adb("shell", "-T", _REMOVE_SNAPSHOT_FILES, serial=serial, timeout_seconds=12,
                                   input_data=payload)
            reported = int(output.strip())
            if 0 <= reported <= payload.count(b"\0") // 3:
                removed = reported
        except (AdbError, ValueError):
            pass
    pending = int(result["exported"]) - removed
    result.update(removed=removed, cleanup_pending=pending, cleaned=pending == 0)


async def _export_directory(
    serial: str, kind: str, source: str, target_dir: Path, on_event: EventSink,
    *, preserve: bool = True,
) -> dict[str, object]:
    result, snapshot = await _pull_log_directory(serial, source, target_dir, preserve=preserve)
    code = f"log_export_{kind.lower()}"
    if result["status"] == "exported":
        cleanup = "车机原始日志已保留。"
        if not preserve:
            try:
                payload = await asyncio.to_thread(_directory_cleanup_payload, target_dir, str(result["source"]), snapshot)
            except OSError:
                payload = b""
            await _remove_exported_files(serial, result, payload)
            cleanup = f"已清理 {result['removed']} 个源文件。"
            if result["cleanup_pending"]:
                on_event(code, "warning", f"{kind}：{result['cleanup_pending']} 个源文件未确认清理。")
        on_event(code, "info",
                 f"{kind}：已从 {result['source']} 拉取 {result['exported']} 个文件到 {target_dir}，{cleanup}")
    elif result["status"] == "empty":
        on_event(code, "info", f"{kind}：{result['source']} 下无日志文件。")
    else:
        on_event(code, "warning", f"{kind}：无法拉取 {source}（{result.get('detail', '')}）。")
    return result


def _write_log_zip(staging: Path, destination: Path, results: dict[str, dict[str, object]]) -> None:
    temporary_zip = staging / destination.name
    with ZipFile(temporary_zip, "w") as archive:
        for kind, result in results.items():
            if result["status"] == "exported":
                for path in _local_log_files(staging / kind):
                    archive.write(path, path.relative_to(staging).as_posix())
        archive.writestr("manifest.json", json.dumps(results, ensure_ascii=False, indent=2))
    temporary_zip.replace(destination)


def _zip_cleanup_payloads(destination: Path, results, snapshots) -> dict[str, bytes]:
    with ZipFile(destination) as archive:
        filenames = archive.namelist()
        return {
            kind: _cleanup_payload(
                ((name.split("/", 1)[1], lambda name=name: archive.open(name))
                 for name in filenames if name.startswith(f"{kind}/")),
                str(results[kind]["source"]), snapshot,
            ) for kind, snapshot in snapshots.items()
        }


async def export_device_logs_zip(
    serial: str, destination: Path, *, preserve: bool = True, crash_path: str | None = None,
) -> dict[str, dict[str, object]]:
    with TemporaryDirectory(prefix=".device-logs-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        results = {}
        snapshots = {}
        for kind, source in DEFAULT_LOG_DIRECTORIES:
            if kind == LOG_KIND_CRASH:
                source = _crash_source(crash_path).rstrip("/")
                if source and not source.startswith("/"):
                    source = f"./{source}"
            pulled, snapshot = await _pull_log_directory(serial, source, staging / kind, preserve=preserve)
            results[kind] = {key: pulled[key] for key in ("source", "status", "exported", "detail") if key in pulled}
            if pulled["status"] == "exported":
                results[kind]["cleanup_requested"] = not preserve
                if not preserve:
                    snapshots[kind] = snapshot
        if any(result["status"] == "exported" for result in results.values()):
            await asyncio.to_thread(_write_log_zip, staging, destination, results)
            if snapshots:
                payloads = await asyncio.to_thread(_zip_cleanup_payloads, destination, results, snapshots)
                for kind, payload in payloads.items():
                    await _remove_exported_files(serial, results[kind], payload)
        return results


async def export_and_clean_device_logs(
    serial: str,
    config: DeviceLogConfig,
    project_root: Path,
    on_event: EventSink,
    preserve: bool = True,
) -> dict[str, object]:
    """执行全部已配置类型的日志导出与清理，返回各类型结果摘要。"""
    results: dict[str, object] = {}
    pairs = config.enabled_pairs()

    export_root = Path(config.export_root).expanduser() if config.export_root else project_root / "DevicesLogs"
    folder_name = time.strftime("%Y_%m_%d-%H_%M_%S")
    standard = [(kind, path.rstrip("/")) for kind, path in pairs if path.rstrip("/") in STANDARD_DIRECTORIES]
    direct = await asyncio.gather(*(
        _export_directory(serial, kind, path, export_root / folder_name / kind, on_event, preserve=preserve)
        for kind, path in standard
    ))
    results.update((kind, result) for (kind, _), result in zip(standard, direct))

    for kind, device_path in pairs:
        if kind in results:
            continue
        code = f"log_export_{kind.lower()}"
        if not _DEVICE_PATH_RE.fullmatch(device_path):
            on_event(code, "warning", f"{kind} 设备路径包含非法字符，已跳过导出：{device_path}")
            results[kind] = {"status": "invalid_path"}
            continue
        if not device_path.startswith("/"):
            device_path = f"./{device_path}"
        target_dir = export_root / folder_name / kind
        try:
            files = await _list_files(serial, device_path)
        except AdbError:
            results[kind] = await _export_directory(serial, kind, device_path, target_dir, on_event)
            continue
        if not files:
            results[kind] = await _export_directory(serial, kind, device_path, target_dir, on_event)
            continue

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
                    await _pull_to_staging(serial, source, local, 120)
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
            fallback = await _export_directory(serial, kind, device_path, target_dir, on_event)
            if fallback["status"] != "exported":
                on_event(code, "warning", f"{kind}：{device_path} 下 {len(files)} 个文件全部拉取失败：{'；'.join(failed_files[:3])}")
            results[kind] = fallback
    return results
