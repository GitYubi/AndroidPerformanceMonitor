"""Assemble an offline Windows x64 ZIP from macOS/Linux/Windows.

Build prerequisites: Python 3.12+, Node 22+, pnpm, npm and internet access.
Only official Windows binaries and win_amd64 wheels enter the package.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools/windows"
CACHE = ROOT / "build/windows-downloads"
STAGING = ROOT / "build/windows-stage/AndroidCarMonitor"
RELEASE = ROOT / "release/AndroidCarMonitor-Windows-x64.zip"
RUNTIMES = [
    ("python-3.12.10-embed-amd64.zip",
     "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
     "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"),
    ("node-v22.23.3-win-x64.zip",
     "https://nodejs.org/dist/v22.23.3/node-v22.23.3-win-x64.zip",
     "2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71"),
    # The fixed Google release URL and checksum are recorded, never a latest URL.
    ("platform-tools_r37.0.1-win.zip",
     "https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip",
     "45f4d63113e895ebde0c90f194099a4676b6ac653bd28d54314a9e022bbc1a99"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download(runtime) -> Path:
    name, url, checksum = runtime
    path = CACHE / name
    if path.is_file() and sha256(path) == checksum:
        return path
    temp = path.with_suffix(".download")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temp.open("wb") as out:
                shutil.copyfileobj(response, out)
            if sha256(temp) != checksum:
                raise RuntimeError(f"SHA-256 mismatch: {name}")
            temp.replace(path)
            return path
        except Exception:
            temp.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def command(args: list[str], cwd: Path = ROOT, env=None) -> None:
    print("Running:", " ".join(args), flush=True)
    # .cmd executables require cmd.exe on a Windows build host.
    if os.name == "nt" and args[0] in {"npm", "pnpm"}:
        args = ["cmd.exe", "/d", "/c", *args]
    subprocess.run(args, cwd=cwd, env=env, check=True)


def extract(path: Path, destination: Path, prefix: str = "") -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if not member.filename.startswith(prefix) or member.is_dir():
                continue
            relative = member.filename[len(prefix):]
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError(f"Unsafe ZIP path: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def audit_bundle(root: Path) -> None:
    for name in ["Start.cmd", "launcher.py", "dist/public/index.html",
                 "runtime/python/python.exe", "runtime/node/node.exe",
                 "runtime/platform-tools/adb.exe",
                 "backend/vendor/scrcpy/scrcpy-server-v3.3.4",
                 "tools/ui-test/node_modules/@midscene/android/package.json",
                 "tools/ui-test/node_modules/@midscene/test/package.json",
                 "tools/ui-test/node_modules/@img/sharp-win32-x64/package.json",
                 "tools/ui-test/node_modules/@esbuild/win32-x64/esbuild.exe"]:
        if not (root / name).is_file():
            raise RuntimeError(f"Required bundle file missing: {name}")
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise RuntimeError(f"Symlink would break on Windows: {path}")
        if path.name in {".env", "settings.json", ".DS_Store"} or path.suffix in {".db", ".sqlite", ".dylib", ".so"}:
            raise RuntimeError(f"Unexpected host data/binary: {path}")
        if path.suffix.lower() in {".exe", ".dll", ".pyd", ".node"}:
            with path.open("rb") as file:
                header = file.read(64)
                if header[:2] != b"MZ":
                    raise RuntimeError(f"Not a Windows executable: {path}")
                file.seek(struct.unpack_from("<I", header, 60)[0])
                pe = file.read(6)
                machine = struct.unpack_from("<H", pe, 4)[0]
                # Google's official ADB distribution is x86, supported by Win x64.
                allowed = {0x14C} if path.is_relative_to(root / "runtime/platform-tools") else {0x8664}
                if pe[:4] != b"PE\0\0" or machine not in allowed:
                    raise RuntimeError(f"Unexpected Windows PE architecture: {path}")


def build() -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError("Use Python 3.12+ on the build host")
    CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        archives = list(executor.map(download, RUNTIMES))
    env = os.environ.copy()
    env["VITE_BACKEND_URL"] = "same-origin"
    command(["pnpm", "exec", "vite", "build"], env=env)
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)
    for name in ["launcher.py", "Start.cmd", "使用说明.txt"]:
        shutil.copy2(SOURCE / name, STAGING / name)
    # UTF-8 + CRLF for cmd.exe and Windows text editors.
    for name in ["Start.cmd", "使用说明.txt"]:
        path = STAGING / name
        path.write_bytes(path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    shutil.copytree(ROOT / "backend/app", STAGING / "backend/app",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak"))
    shutil.copytree(ROOT / "backend/vendor", STAGING / "backend/vendor")
    shutil.copytree(ROOT / "dist/public", STAGING / "dist/public",
                    ignore=shutil.ignore_patterns("__manus__", ".gitkeep"))
    index = STAGING / "dist/public/index.html"
    # Optional development analytics is not part of the offline application.
    import re
    html = re.sub(r'<script\b[^>]*src="[^"]*/umami"[^>]*>\s*</script>', "", index.read_text())
    index.write_text(html, encoding="utf-8")
    runner = STAGING / "tools/ui-test"
    runner.mkdir(parents=True)
    for path in (ROOT / "tools/ui-test").iterdir():
        if path.name in {"package.json", "package-lock.json"} or (path.suffix == ".mjs" and not path.name.endswith(".test.mjs")):
            shutil.copy2(path, runner / path.name)
    command(["npm", "ci", "--os=win32", "--cpu=x64", "--ignore-scripts", "--omit=dev",
             "--no-bin-links", "--no-audit", "--no-fund"], cwd=runner)
    # Some public npm tarballs ship development .env files; none are runtime config.
    for path in runner.rglob(".env"):
        path.unlink()
    python = STAGING / "runtime/python"
    extract(archives[0], python)
    extract(archives[1], STAGING / "runtime/node", "node-v22.23.3-win-x64/")
    extract(archives[2], STAGING / "runtime/platform-tools", "platform-tools/")
    (python / "python312._pth").write_text(
        "python312.zip\n.\nsite-packages\n../..\n../../backend\nimport site\n", encoding="utf-8")
    command([sys.executable, "-m", "pip", "install", "--platform", "win_amd64",
             "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
             "--only-binary=:all:", "--ignore-installed", "--no-compile", "--target", str(python / "site-packages"),
             "-r", str(SOURCE / "requirements.txt")])
    for directory in ["bin", "Scripts"]:
        shutil.rmtree(python / "site-packages" / directory, ignore_errors=True)
    # Preserve frontend dependency licenses as well as licenses shipped with runtimes.
    notices = ["Third-party components: Python, Node.js, Android Platform-Tools, scrcpy.\n"
               "Runtime licenses are included in runtime/, backend/vendor/, and dependency directories.\n"]
    for path in sorted((ROOT / "node_modules/.pnpm").rglob("*")):
        if path.is_file() and path.name.upper().startswith(("LICENSE", "NOTICE")) and path.stat().st_size < 500_000:
            notices.append(f"\n--- {path.relative_to(ROOT / 'node_modules')} ---\n" + path.read_text(errors="replace"))
    (STAGING / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")
    manifest = {"platform": "win32-x64", "python": "3.12.10", "node": "22.23.3",
                "runtimes": [{"file": name, "url": url, "sha256": digest} for name, url, digest in RUNTIMES],
                "files": {str(p.relative_to(STAGING)).replace(os.sep, "/"): sha256(p)
                          for p in sorted(STAGING.rglob("*")) if p.is_file()}}
    (STAGING / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_bundle(STAGING)
    RELEASE.parent.mkdir(exist_ok=True)
    temp = RELEASE.with_suffix(".tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(STAGING.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(STAGING.parent).as_posix())
    temp.replace(RELEASE)
    RELEASE.with_suffix(".zip.sha256").write_text(f"{sha256(RELEASE)}  {RELEASE.name}\n", encoding="ascii")
    print(f"Created {RELEASE} ({RELEASE.stat().st_size / 1024**2:.1f} MiB)", flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
