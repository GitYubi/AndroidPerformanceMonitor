# Windows 便携版审计证据

## macOS 离线复现

2026-11-16，在 macOS arm64 使用项目 Python 环境。下列检查在临时目录使用合成文件，PE 只有审核器所读的头部。当前发布物只读核对，既有 ZIP 和校验文件留在本地 `release/`。

```sh
PYTHONPATH=backend backend/.venv/bin/python -B - <<'PY'
import hashlib
from pathlib import Path
import shutil
import struct
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from tools.windows import build
from zlg import native

required = [
    "Start.cmd", "launcher.py", "dist/public/index.html",
    "runtime/python/python.exe", "runtime/node/node.exe",
    "runtime/platform-tools/adb.exe",
    "runtime/python/site-packages/uiautomator2/__init__.py",
    "backend/vendor/scrcpy/scrcpy-server-v3.3.4",
    "backend/config/Example.dbc", "backend/app/ui_element_capture.py",
    "tools/ui-test/node_modules/@midscene/android/package.json",
    "tools/ui-test/node_modules/@midscene/test/package.json",
    "tools/ui-test/node_modules/@img/sharp-win32-x64/package.json",
    "tools/ui-test/node_modules/@esbuild/win32-x64/esbuild.exe",
]

with TemporaryDirectory() as directory:
    root = Path(directory)
    stage = root / "stage"
    for entry in required:
        file = stage / entry
        file.parent.mkdir(parents=True, exist_ok=True)
        if file.suffix == ".exe":
            dos = bytearray(64)
            dos[:2] = b"MZ"
            struct.pack_into("<I", dos, 60, 64)
            machine = 0x14C if file.name == "adb.exe" else 0x8664
            file.write_bytes(dos + b"PE\0\0" + struct.pack("<H", machine))
        else:
            file.touch()
    source = root / "app"
    source.mkdir()
    for name in (".env.local", "session.sqlite3", "old.py.orig"):
        (source / name).write_text("synthetic fixture", encoding="utf-8")
    destination = stage / "backend/app"
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak"))
    build.audit_bundle(stage)
    assert all((destination / name).is_file() for name in (".env.local", "session.sqlite3", "old.py.orig"))
    negative_control = destination / ".env"
    negative_control.touch()
    try:
        build.audit_bundle(stage)
    except RuntimeError as exc:
        assert "Unexpected host data/binary" in str(exc)
    else:
        raise AssertionError("the stage should reject .env")
    print("A28: .env.local and .sqlite3 survive the copy and pass validation; .env rejected")

    sdk_directory = root / "zlgcan_x64"
    with (
        patch.object(native.platform, "system", return_value="Windows"),
        patch.object(native.platform, "machine", return_value="AMD64"),
        patch.object(native, "resource_path", return_value=sdk_directory),
    ):
        try:
            native.load_vendor_library("zlgcan_x64", "zlgcan.dll")
        except FileNotFoundError as exc:
            assert str(sdk_directory / "zlgcan.dll") in str(exc)
        else:
            raise AssertionError("the SDK file is absent")
    print("A23: missing SDK is detected on the loader path (platform detection simulated)")

path = Path("release/AndroidCarMonitor-Windows-x64.zip")
if path.is_file():
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert digest == path.with_suffix(".zip.sha256").read_text().split()[0]
    with ZipFile(path) as package:
        base = "AndroidCarMonitor/"
        names = set(package.namelist())
        for entry in ("backend/can_adapters/hirain.py", "backend/zlgcan_x64/zlgcan.dll", "backend/app/models.py.orig"):
            print(entry, "present" if base + entry in names else "absent")
        for entry in ("backend/app/can_service.py", "backend/zlg/zlgcan.py"):
            print(entry, "matches worktree" if package.read(base + entry) == Path(entry).read_bytes()
                  else "differs from worktree")
        print("archive SHA-256", digest)
PY
```

本机可用下列命令对已有 ZIP 的解压目录、HTTP 路由、端口回退和退出执行烟测。它使用 macOS 的 Python，Windows PE 和 SDK 均未被执行。

```sh
ZCAN_BACKEND=simulation backend/.venv/bin/python -B tools/windows/smoke.py release/AndroidCarMonitor-Windows-x64.zip
```

## V04 恢复步骤

本轮按用户调整后的范围执行步骤 1–2 的通用启动烟测。步骤 3–4 保留为原任务的 SDK 与台架计划。

1. 在干净的 Windows x64 主机或虚拟机准备按审计基线生成的便携包，记录包 SHA-256、Windows 构建号及使用的代码状态。旧 ZIP 只能作为旧版本的对照。
2. 在含空格和中文的临时目录解压。运行 `Start.cmd --check`、`Start.cmd --no-browser` 以及 `py -3.12 tools/windows/smoke.py ZIP路径`，记录 bundled Python、Node、ADB、Sharp、端口回退、资源加载和退出日志。
3. 在无 SDK 和装有已授权、版本明确的 x64 SDK 两个干净目录分别核对 SDK 找不到时的实际错误，以及依赖满足后的 DLL 装载和导出符号设置。按 `backend/zlg/native.py` 的非冻结加载路径，将审计副本的 SDK 放在 `backend/zlgcan_x64`，可以用包内 Python 调用 `load_vendor_library` 和 `configure_zcan` 验证加载，无需发送 CAN 帧。检查设备发现接口无适配或驱动时的实际 HTTP 响应。
4. 保留错误输出、版本与路径；真实设备打开、收发和资源释放曾计划在隔离台架验证，目前已按用户确认移出范围。

2026-11-16 收尾核对：本机为 Darwin arm64，检查 PATH 中的 Wine、QEMU、VirtualBox、Parallels 和 VMware 命令及本机应用目录，未发现可用 Windows 环境。旧发布说明保留 2026-10-12 实机验证确认。2026-11-12 发布快照对应 3a0a8bd，标注没有原生运行验证，不能代替 V04。


## 当前版本的验收包

2026-11-16，macOS arm64。在隔离目录以当前工作树的构建输入运行原有打包脚本。复用仓库已有前端工具和三个校验通过的官方 Windows 运行时缓存。Windows 依赖按原脚本只装入临时打包目录。

- 本机产物：[AndroidCarMonitor-Windows-x64.zip](../../build/code-audit-windows-ld5h4mnb/AndroidCarMonitor-Windows-x64.zip)，148,758,191 字节，SHA-256 为 `313eab6bcca6991edfa73af85b839895ef0b9a545efb8fbd5607d6fa2a0f47c1`。
- 审计基线含未提交的工作树内容。打包后验证 ZIP 和旁置校验文件一致，18,989 个清单条目哈希匹配，其中 54 个 Git 枚举的后端、Node 执行器或 vendor 输入与工作树哈希一致。既有发布包保留。
- 使用拒绝连接的 ADB 替身，在 macOS 运行 `backend/.venv/bin/python -B tools/windows/smoke.py <该 ZIP 路径>`，退出 0。通过中文及空格路径、端口回退、资源和路由、同源 API、重复启动、数据路径、退出及状态清理。Windows exe、DLL 和 .node 文件仅完成打包和静态检查。

## 原生验收步骤

在干净 Windows x64 主机或虚拟机把上述 ZIP 和仓库中的 [smoke.py](../../tools/windows/smoke.py) 放到同一测试目录。从该目录运行 PowerShell，记录系统信息、SHA-256 和以下两条检查的实际输出与退出码。脚本会用包内 Python，在临时目录启动和退出服务。测试无需连接车机或 CAN。

```powershell
$zip = (Resolve-Path .\AndroidCarMonitor-Windows-x64.zip).Path
$smoke = (Resolve-Path .\smoke.py).Path
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture
if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne "313eab6bcca6991edfa73af85b839895ef0b9a545efb8fbd5607d6fa2a0f47c1") {
  throw "审计包 SHA-256 不匹配"
}
$checkDir = Join-Path $env:TEMP ("便携审计 " + [guid]::NewGuid().ToString("N"))
Expand-Archive -LiteralPath $zip -DestinationPath $checkDir
Push-Location (Join-Path $checkDir "AndroidCarMonitor")
try {
  cmd.exe /d /c "Start.cmd --check"
  if ($LASTEXITCODE -ne 0) { throw "原生自检失败：$LASTEXITCODE" }
  & .\runtime\python\python.exe -X utf8 $smoke $zip
  if ($LASTEXITCODE -ne 0) { throw "原生烟测失败：$LASTEXITCODE" }
} finally {
  Pop-Location
}
```

验收预期：原生自检通过，烟测报告启动、端口回退、前端与 API、路径隔离和退出清理均通过。将输出与 ZIP 的 SHA-256 填入 V04。ZIP 在本地忽略的 build 目录，换主机前需一并转移。
