"""Portable launcher: one local server, a browser, and private bundled runtimes."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser


def bundle_root() -> Path:
    return Path(__file__).resolve().parent


class InstanceLock:
    def __init__(self, path: Path):
        self.file = path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)

    def acquire(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def close(self) -> None:
        self.file.close()


def instance_id(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()


def bind_socket(port: int) -> socket.socket:
    # Pass the already-bound socket to Uvicorn, avoiding a port selection race.
    for candidate in range(port, min(port + 20, 65536)):
        sock = socket.socket()
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", candidate))
            return sock
        except OSError:
            sock.close()
    raise RuntimeError(f"端口 {port}–{min(port + 19, 65535)} 均不可用，请用 Start.cmd --port 8190。")


def create_app(root: Path):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from app.main import app

    public = root / "dist" / "public"
    if not (public / "index.html").is_file():
        raise RuntimeError("前端文件缺失，请重新完整解压运行包。")

    @app.get("/_launcher/status", include_in_schema=False)
    def launcher_status():
        return {"application": "android-car-performance-monitor", "instance": instance_id(root)}

    app.mount("/assets", StaticFiles(directory=public / "assets"), name="desktop-assets")

    # Register after API routes so unknown API calls still return a real 404.
    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith(("api/", "_launcher/")) or path in {"api", "_launcher"}:
            raise HTTPException(status_code=404)
        target = (public / path).resolve()
        if target.is_relative_to(public.resolve()) and target.is_file():
            return FileResponse(target)
        if path == "" or path == "ui":
            return FileResponse(public / "index.html")
        raise HTTPException(status_code=404)

    return app


def existing_url(root: Path, metadata: Path) -> str | None:
    try:
        port = int(json.loads(metadata.read_text(encoding="utf-8"))["port"])
        if not 1 <= port <= 65535:
            return None
        url = f"http://127.0.0.1:{port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/_launcher/status", timeout=1) as response:
            status = json.load(response)
        if status.get("instance") == instance_id(root):
            return url
    except (OSError, ValueError, KeyError):
        pass
    return None


def prepare_environment(root: Path) -> None:
    os.environ["PATH"] = os.pathsep.join([
        str(root / "runtime" / "platform-tools"),
        str(root / "runtime" / "node"),
        os.environ.get("PATH", ""),
    ])
    sys.path.insert(0, str(root / "backend"))
    # Avoid inheriting paths and package options from another Node installation.
    os.environ.pop("NODE_PATH", None)
    os.environ.pop("NODE_OPTIONS", None)
    os.chdir(root)


def check_runtime(root: Path) -> None:
    import uvicorn  # noqa: F401
    import websockets  # noqa: F401
    import multipart  # noqa: F401
    from app.main import app  # noqa: F401

    if os.name != "nt":
        return  # Native runtime checks are performed on Windows only.
    for tool, args in [("platform-tools/adb.exe", ["version"]), ("node/node.exe", ["--version"])]:
        executable = root / "runtime" / tool
        result = subprocess.run([str(executable), *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15)
        if result.returncode:
            raise RuntimeError(f"{executable.name} 无法运行：{result.stderr[:500]}")
        logging.info("%s", result.stdout.strip())
    # Load native Sharp libraries too; merely resolving Midscene misses DLL errors.
    subprocess.run([
        str(root / "runtime/node/node.exe"), "--input-type=module", "-e",
        "await import('@midscene/android'); await import('@midscene/test'); "
        "const s = (await import('sharp')).default; "
        "await s({create:{width:1,height:1,channels:3,background:'#000'}}).png().toBuffer();",
    ], cwd=root / "tools/ui-test", check=True, timeout=60)


def run(args, root: Path) -> int:
    prepare_environment(root)
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(logs / "launcher.log", encoding="utf-8"),
                                  logging.StreamHandler()])
    if args.check:
        check_runtime(root)
        create_app(root)
        print("运行环境检查通过。")
        return 0
    lock = InstanceLock(logs / "launcher.lock")
    metadata = logs / "instance.json"
    try:
        if not lock.acquire():
            for _ in range(30):
                url = existing_url(root, metadata)
                if url:
                    print(f"工具已在运行：{url}")
                    if not args.no_browser:
                        webbrowser.open(url)
                    return 0
                time.sleep(0.5)
            raise RuntimeError("另一个启动窗口正在运行，请查看该窗口的状态。")

        check_runtime(root)
        app = create_app(root)
        import uvicorn
        sock = bind_socket(args.port)
        port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                              loop="asyncio", http="h11", ws="websockets",
                                              access_log=False))
        metadata.write_text(json.dumps({"port": port}), encoding="utf-8")

        def open_when_ready():
            while not server.started and not server.should_exit:
                time.sleep(0.1)
            if server.started:
                print(f"\n工具已启动：{url}\n保留此窗口，按 Ctrl+C 停止工具。\n", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)

        threading.Thread(target=open_when_ready, daemon=True).start()
        try:
            server.run(sockets=[sock])
        finally:
            server.should_exit = True
            sock.close()
            metadata.unlink(missing_ok=True)
        return 0 if server.started else 1
    finally:
        lock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Android 车机性能监测便携版")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1–65535 范围内")
    if os.name == "nt":
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    try:
        return run(args, bundle_root())
    except KeyboardInterrupt:
        print("\n工具已停止。", flush=True)
        return 0
    except Exception as exc:
        logging.exception("启动失败：%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
