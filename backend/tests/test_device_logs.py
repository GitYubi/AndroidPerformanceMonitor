import asyncio
import errno
from hashlib import sha256
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from app import device_logs
from app.adb import AdbError, run_adb
from app.models import StartSessionRequest
from app.monitor import MonitorManager

DEFAULT_CRASH_PATH = "/data/system/dropbox"


def test_adb_can_write_relative_to_its_own_working_directory(tmp_path, monkeypatch):
    working_dir = tmp_path / "临时 日志"
    working_dir.mkdir()
    monkeypatch.setattr("app.adb.shutil.which", lambda *_: sys.executable)
    asyncio.run(run_adb(
        "-c", "from pathlib import Path; Path('log').write_bytes(b'data')", cwd=working_dir,
    ))
    assert (working_dir / "log").read_bytes() == b"data"


@pytest.mark.parametrize("copy_fails", [False, True])
def test_windows_monkey_log_export_uses_system_staging(tmp_path, monkeypatch, copy_fails):
    destination = tmp_path / "工作" / "北汽 B31CS-C09" / "Windows x64" / "monkey-data" / "task" / "device-logs.zip"
    destination.parent.mkdir(parents=True)
    temporary_root = tmp_path / "用户 Temp"
    temporary_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(temporary_root))
    monkeypatch.setattr(device_logs, "_WINDOWS", True)
    remote_file = f"{DEFAULT_CRASH_PATH}/crash.log"
    content = b"crash fixture"
    cleanup = []

    async def adb(operation, *args, **kwargs):
        if operation == "shell":
            if args[0] == "-T":
                with ZipFile(destination) as zip_file:
                    assert zip_file.read("Crash/crash.log") == content
                cleanup.append(kwargs["input_data"])
                return "1\n"
            return f"{remote_file}\0identity\0" if shlex.split(args[0])[-1] == DEFAULT_CRASH_PATH else ""
        assert operation == "pull"
        source, local = args
        # Simulate ADB failing to stat the installation's absolute path on Windows.
        if local != "logs":
            raise AdbError("cannot create file/directory: No such file or directory")
        working_dir = Path(kwargs["cwd"])
        assert working_dir.parent == temporary_root and working_dir.is_dir()
        leaf = working_dir / local
        assert not leaf.exists()
        assert source in {"/data/anr", DEFAULT_CRASH_PATH, "/data/tombstones"}
        leaf.mkdir()
        if source == DEFAULT_CRASH_PATH:
            (leaf / "crash.log").write_bytes(content)
        return ""

    rename = device_logs.os.rename
    copytree = device_logs.shutil.copytree

    def rename_across_drives(source, target, *args, **kwargs):
        if Path(source).is_relative_to(temporary_root):
            raise OSError(errno.EXDEV, "different drive")
        return rename(source, target, *args, **kwargs)

    def copy_to_report(source, target, *args, **kwargs):
        if copy_fails and (Path(source) / "crash.log").exists():
            # A failed copy can leave a partial file in report staging.
            Path(target).mkdir()
            (Path(target) / "crash.log").write_bytes(b"partial")
            raise OSError("report drive full")
        return copytree(source, target, *args, **kwargs)

    monkeypatch.setattr(device_logs, "run_adb", adb)
    monkeypatch.setattr(device_logs.os, "rename", rename_across_drives)
    monkeypatch.setattr(device_logs.shutil, "copytree", copy_to_report)
    results = asyncio.run(device_logs.export_device_logs_zip("fixture", destination, preserve=False))
    assert results["ANR"]["status"] == results["Tombstone"]["status"] == "empty"
    if copy_fails:
        assert results["Crash"]["status"] == "error"
        assert "report drive full" in results["Crash"]["detail"]
        assert not destination.exists() and not cleanup
    else:
        assert results["Crash"]["removed"] == 1
        assert cleanup == [f"{remote_file}\0identity\0{sha256(content).hexdigest()}\0".encode()]
    assert list(temporary_root.iterdir()) == []
    assert list(destination.parent.iterdir()) == ([destination] if not copy_fails else [])


@pytest.mark.parametrize("windows", [False, True])
def test_log_export_cleans_only_verified_originals(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(device_logs, "_WINDOWS", windows)
    folder = "/data/local/tmp/logs"
    good = f"{folder}/app's log.txt"
    failed = f"{folder}/failed.txt"
    replaced = f"{folder}/replaced.txt"
    corrupt = f"{folder}/corrupt-copy.txt"
    new = f"{folder}/new.txt"
    device = {path: (index, path.encode()) for index, path in enumerate((good, failed, replaced, corrupt))}

    async def adb(operation, *args, **kwargs):
        if operation == "pull":
            source, destination = args
            if source == failed:
                raise AdbError("fixture pull failed")
            if source == good:
                device[new] = (99, b"new")
            assert Path(destination).is_absolute() != windows
            destination = Path(kwargs.get("cwd", "")) / destination
            if destination.is_dir():
                destination /= Path(source).name
            destination.write_bytes(b"bad copy" if source == corrupt else device[source][1])
            if source == replaced:
                # Different identity, even though the replacement has the same bytes.
                device[source] = (100, device[source][1])
            return ""

        assert operation == "shell"
        command = shlex.split(" ".join(args))
        if command[0] == "ls":
            return "\n".join(Path(path).name for path in device)
        if command[0] == "find":
            return "\0".join(device) + "\0"
        if command[0] == "stat":
            return f"{device[command[-1]][0]}\n"
        if command[0] == "rm":
            device.clear()  # the previous cleanup expanded a directory-wide glob
            return ""
        assert command[:2] == ["sh", "-c"], command
        source, identity, digest = command[-3:]
        if (str(device[source][0]), sha256(device[source][1]).hexdigest()) != (identity, digest):
            raise AdbError("source differs from the export")
        del device[source]
        return ""

    monkeypatch.setattr(device_logs, "run_adb", adb)
    result = asyncio.run(device_logs.export_and_clean_device_logs(
        "fixture", device_logs.DeviceLogConfig(crash=folder), tmp_path, lambda *_: None, preserve=False,
    ))["Crash"]
    assert set(device) == {failed, replaced, corrupt, new}
    assert (Path(result["target"]) / Path(good).name).read_bytes() == good.encode()
    assert not (Path(result["target"]) / Path(failed).name).exists()
    assert result["exported"] == 3 and result["failed"] == 1
    assert result["cleaned"] is False and result["removed"] == 1


def test_log_export_continues_other_types_when_destination_is_blocked(tmp_path, monkeypatch):
    folder = tmp_path / "DevicesLogs" / "fixture"
    folder.mkdir(parents=True)
    (folder / "ANR").write_text("a file already occupies the destination")
    monkeypatch.setattr(device_logs.time, "strftime", lambda _: "fixture")

    async def adb(operation, *args, **kwargs):
        if operation == "shell":
            command = shlex.split(" ".join(args))
            directory = command[1] if command[0] == "find" else command[-1]
            return directory + "/log\0" if command[0] == "find" else "log\n"
        assert args[0] == "/data/crash/log"
        (Path(kwargs.get("cwd", "")) / args[1]).write_text("crash fixture")
        return ""

    monkeypatch.setattr(device_logs, "run_adb", adb)
    config = device_logs.DeviceLogConfig(anr="/data/anr", crash="/data/crash")
    events = []
    result = asyncio.run(device_logs.export_and_clean_device_logs(
        "fixture", config, tmp_path, lambda *event: events.append(event), preserve=True,
    ))
    assert result["ANR"]["status"] == "error"
    assert any(code == "log_export_anr" and severity == "warning" for code, severity, _ in events)
    assert result["Crash"]["exported"] == 1
    assert (folder / "Crash" / "log").read_text() == "crash fixture"


def test_monitor_log_export_uses_default_crash_path_and_keeps_successful_types(tmp_path, monkeypatch):
    calls = []

    async def adb(operation, source, destination=None, **kwargs):
        assert kwargs["serial"] == "fixture"
        if operation == "shell":
            if source == "-T":
                fields = kwargs["input_data"].decode().rstrip("\0").split("\0")
                remote, identity, digest = fields
                assert identity == "identity"
                assert digest == sha256(remote.rsplit("/", 1)[0].encode()).hexdigest()
                assert list(tmp_path.glob("DevicesLogs/*/*/fixture.log"))
                return "1\n"
            remote = shlex.split(source)[-1]
            if remote == "/data/tombstones":
                return f"{remote}/fixture.log\0identity\0"
            raise AdbError("unavailable")
        assert operation == "pull"
        calls.append(source)
        if source == "/data/anr":
            raise AdbError("unavailable")
        local = Path(kwargs.get("cwd", "")) / destination
        local.mkdir()
        (local / "fixture.log").write_text(source)
        return ""

    monkeypatch.setattr(device_logs, "run_adb", adb)
    config = device_logs.DeviceLogConfig(anr="/data/anr", tombstone="/data/tombstones")
    result = asyncio.run(device_logs.export_and_clean_device_logs(
        "fixture", config, tmp_path, lambda *_: None, preserve=False,
    ))
    assert sorted(calls) == sorted(("/data/anr", DEFAULT_CRASH_PATH, "/data/tombstones"))
    assert result["ANR"]["status"] == "error"
    for kind in ("Crash", "Tombstone"):
        log = result[kind]
        assert log["status"] == "exported"
        assert log["removed"] == (1 if kind == "Tombstone" else 0)
        assert log["cleanup_pending"] == (0 if kind == "Tombstone" else 1)
        assert (Path(log["target"]) / "fixture.log").read_text() == log["source"]


@pytest.mark.parametrize("exporter", ["monkey", "performance"])
@pytest.mark.parametrize("path, failed", [("/data/dropbox", False), ("/data/dropbox", True), ("/data/vendor/crash", True)])
def test_crash_export_uses_only_the_configured_path_even_if_empty_or_unreadable(
    tmp_path, monkeypatch, exporter, path, failed,
):
    pulled = []

    async def adb(operation, source, *args, **kwargs):
        if operation == "shell":
            assert shlex.split(source)[1] == path  # Performance's custom directory listing.
            return ""
        assert operation == "pull"
        if source in {"/data/anr", "/data/tombstones"}:
            raise AdbError("absent")
        pulled.append(source)
        if failed:
            raise AdbError("unreadable")
        (Path(kwargs.get("cwd", "")) / args[0]).mkdir()
        return ""

    monkeypatch.setattr(device_logs, "run_adb", adb)
    if exporter == "monkey":
        export = device_logs.export_device_logs_zip(
            "fixture", tmp_path / "device-logs.zip", preserve=True, crash_path=path,
        )
    else:
        export = device_logs.export_and_clean_device_logs(
            "fixture", device_logs.DeviceLogConfig(crash=path), tmp_path, lambda *_: None, preserve=True,
        )
    crash = asyncio.run(export)["Crash"]
    assert pulled == [path]
    assert crash["status"] == ("error" if failed else "empty")


@pytest.mark.parametrize("find_available", [False, True])
def test_custom_log_path_falls_back_to_read_only_directory_pull(tmp_path, monkeypatch, find_available):
    source = "/data/local/tmp/custom-logs"

    async def adb(operation, *args, **kwargs):
        if operation == "shell":
            if args[0].startswith("find ") and find_available:
                return f"{source}/trace.log\0"
            if args[0].startswith(("find ", "stat ")):
                raise AdbError("shell cannot enumerate or stat files")
            pytest.fail(f"unexpected shell operation: {args}")
        if args[0] != source:
            raise AdbError("individual pull failed")
        local = Path(kwargs.get("cwd", "")) / args[1]
        local.mkdir()
        (local / "trace.log").write_text("fixture")
        return ""

    monkeypatch.setattr(device_logs, "run_adb", adb)
    result = asyncio.run(device_logs.export_and_clean_device_logs(
        "fixture", device_logs.DeviceLogConfig(crash=source), tmp_path, lambda *_: None, preserve=False,
    ))["Crash"]
    assert result["status"] == "exported" and result["removed"] == 0
    assert (Path(result["target"]) / "trace.log").read_text() == "fixture"


def test_archive_failure_does_not_delete_source_logs(tmp_path, monkeypatch):
    async def adb(operation, *args, **kwargs):
        assert kwargs.get("input_data") is None
        if operation == "shell":
            source = shlex.split(args[0])[-1]
            return f"{source}/trace.log\0identity\0"
        assert operation == "pull"
        local = Path(kwargs.get("cwd", "")) / args[1]
        local.mkdir()
        (local / "trace.log").write_text("fixture")
        return ""

    def archive(*args):
        raise OSError("fixture archive write failed")

    monkeypatch.setattr(device_logs, "run_adb", adb)
    monkeypatch.setattr(device_logs, "_write_log_zip", archive)
    with pytest.raises(OSError, match="fixture archive write failed"):
        asyncio.run(device_logs.export_device_logs_zip("fixture", tmp_path / "device-logs.zip", preserve=False))
    assert not (tmp_path / "device-logs.zip").exists()


@pytest.mark.parametrize(
    "delete, ui_active, protected, expected_preserve",
    [(None, False, False, True), (False, False, False, True), (True, False, False, False),
     (True, True, False, True), (True, False, True, True)],
)
def test_performance_export_obeys_session_cleanup_setting(tmp_path, monkeypatch, delete, ui_active, protected, expected_preserve):
    commands = []
    events = []

    async def adb(operation, *args, **kwargs):
        commands.append(operation)
        if operation == "shell":
            assert not expected_preserve
            if args[0] == "-T":
                assert list(tmp_path.glob("*/Crash/trace.log"))
                return "1"
            return f"{DEFAULT_CRASH_PATH}/trace.log\0identity\0"
        assert operation == "pull"
        local = Path(kwargs.get("cwd", "")) / args[1]
        local.mkdir()
        (local / "trace.log").write_text("fixture")
        return ""

    monkeypatch.setattr(device_logs, "run_adb", adb)
    options = {} if delete is None else {"delete_device_logs": delete}
    request = StartSessionRequest(serial="fixture", log_export_root=str(tmp_path), **options)
    manager = MonitorManager(tmp_path / "sessions")
    if ui_active:
        manager.ui_manager = SimpleNamespace(device_lock=lambda _: asyncio.Lock(), has_active_device=lambda _: True)
    runtime = SimpleNamespace(
        request=request, preserve_device_logs=protected,
        writer=SimpleNamespace(add_event=lambda *event: events.append(event)),
    )
    asyncio.run(manager._export_device_logs(runtime))
    assert commands == (["pull"] if expected_preserve else ["shell", "pull", "shell"])
    assert events[0][:2] == ("info", "log_cleanup_policy")
    assert ("保留车机" in events[0][2]) is expected_preserve
    assert len(list(tmp_path.glob("*/Crash/trace.log"))) == 1
