"""Exercise the final ZIP in a fresh directory with spaces and Chinese characters.

Windows uses the bundled interpreter; other hosts use the project's native venv.
The latter checks server/launcher behavior, not Windows runtime execution.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url):
    with HTTP.open(url, timeout=10) as response:
        return response.read()


def smoke(archive: Path):
    with tempfile.TemporaryDirectory(prefix="车机 portable ") as directory:
        temp = Path(directory)
        with zipfile.ZipFile(archive) as package:
            assert package.testzip() is None
            package.extractall(temp)
        root = temp / "AndroidCarMonitor"
        python = root / "runtime/python/python.exe" if os.name == "nt" else ROOT / "backend/.venv/bin/python"
        command = [str(python), "-X", "utf8", str(root / "launcher.py")]
        clean_env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "NODE_PATH", "NODE_OPTIONS"}}
        check = subprocess.run([*command, "--check"], capture_output=True, encoding="utf-8", env=clean_env, timeout=90)
        assert check.returncode == 0, check.stdout + check.stderr
        # Hold the preferred port to verify automatic fallback and same-origin APIs.
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
            with (temp / "smoke.log").open("wb") as log:
                process = subprocess.Popen([*command, "--no-browser", "--port", str(port)],
                                           env=clean_env, stdout=log, stderr=log, **options)
                try:
                    url = None
                    for _ in range(600):
                        if process.poll() is not None:
                            raise AssertionError((temp / "smoke.log").read_text(encoding="utf-8"))
                        try:
                            chosen = json.loads((root / "logs/instance.json").read_text())["port"]
                            candidate = f"http://127.0.0.1:{chosen}"
                            if json.loads(fetch(candidate + "/_launcher/status"))["application"] == "android-car-performance-monitor":
                                url = candidate
                                break
                        except (OSError, ValueError, KeyError):
                            time.sleep(0.1)
                    assert url, "Server did not become ready"
                    assert chosen != port, "Port fallback did not run"
                    page = fetch(url + "/").decode()
                    assert fetch(url + "/ui").decode() == page
                    assert "/umami" not in page
                    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page)
                    assert len(assets) >= 2
                    for asset in assets:
                        content = fetch(url + asset)
                        assert content
                        if asset.endswith(".js"):
                            assert b"window.location.origin" in content
                    sessions = json.loads(fetch(url + "/api/sessions"))
                    assert sessions == {"sessions": []}, sessions
                    assert json.loads(fetch(url + "/api/ui/runs")) == []
                    assert b"<html" in fetch(url + "/fps-probe").lower()
                    for path in ["/api/does-not-exist", "/assets/missing.js", "/launcher.py", "/../使用说明.txt", "/%2e%2e/launcher.py"]:
                        try:
                            fetch(url + urllib.parse.quote(path, safe="/%"))
                        except urllib.error.HTTPError as exc:
                            assert exc.code == 404, (path, exc.code)
                        else:
                            raise AssertionError(f"Unexpectedly served {path}")
                    again = subprocess.run([*command, "--no-browser", "--port", str(port)],
                                           capture_output=True, encoding="utf-8", env=clean_env, timeout=25)
                    assert again.returncode == 0, again.stdout + again.stderr
                    assert url in again.stdout
                    assert process.poll() is None
                    assert (root / "backend/data").is_dir()
                    assert (root / "backend/ui-data").is_dir()
                    print("PASS: ZIP, clean launch, Chinese/spaced path, port fallback, frontend, APIs, 404s, duplicate launch, data paths")
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                        try:
                            process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    assert not (root / "logs/instance.json").exists(), "Instance metadata was not cleaned up"
        print("PASS: shutdown and metadata cleanup")
        if os.name != "nt":
            print("Windows executables were NOT executed on this host.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", type=Path, default=ROOT / "release/AndroidCarMonitor-Windows-x64.zip")
    smoke(parser.parse_args().archive.resolve())
