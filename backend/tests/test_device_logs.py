import asyncio
from hashlib import sha256
from pathlib import Path
import shlex

from app import device_logs
from app.adb import AdbError


def test_log_export_cleans_only_verified_originals(tmp_path, monkeypatch):
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
            destination = Path(destination)
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
        "fixture", device_logs.DeviceLogConfig(anr=folder), tmp_path, lambda *_: None,
    ))["ANR"]
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
        Path(args[1]).write_text("crash fixture")
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
