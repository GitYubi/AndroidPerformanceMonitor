"""Windows SDK loading and pointer-safe ZCAN signatures from zlgcan.h."""
import ctypes as ct
import os
import platform
from pathlib import Path
import sys
from zlg.paths import resource_path

# Keep directory cookies alive: vendor DLLs also load dependencies after startup.
_dll_directories = {}


def load_vendor_library(folder: str, filename: str):
    if platform.system() != "Windows":
        raise RuntimeError(
            "本项目附带的周立功 SDK 仅支持 Windows x64，无法在 macOS 上直接连接硬件。"
            "请在 Windows 运行硬件服务；Mac 可使用 DBC 浏览和 CANClient 网络客户端。"
        )
    if ct.sizeof(ct.c_void_p) != 8 or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("周立功 DLL 需要 Windows x64 和 64 位 x86 Python 3.12。")
    directory = (Path(sys.executable).parent / folder if getattr(sys, "frozen", False)
                 else resource_path(folder))
    library = directory / filename
    if not library.is_file():
        raise FileNotFoundError(f"找不到周立功 SDK：{library}。请将完整 {folder} 放到程序所在目录。")
    for path in (directory, directory / "kerneldlls"):
        if path.is_dir() and path not in _dll_directories:
            _dll_directories[path] = os.add_dll_directory(str(path))
    try:
        return ct.WinDLL(str(library))
    except OSError as exc:
        raise OSError(f"无法加载 {library}。请检查 x64 驱动、SDK 依赖和 VC++ 运行库：{exc}") from exc


def configure_zcan(dll):
    """Use void* for both device and channel handles to avoid x64 truncation."""
    p, u = ct.c_void_p, ct.c_uint
    signatures = {
        "ZCAN_OpenDevice": (p, [u, u, u]),
        "ZCAN_InitCAN": (p, [p, u, p]),
        "GetIProperty": (p, [p]),
        "ReleaseIProperty": (u, [p]),
        "ZCAN_GetReceiveNum": (u, [p, ct.c_ubyte]),
    }
    for name in ("CloseDevice", "IsDeviceOnLine", "StartCAN", "ResetCAN", "ClearBuffer"):
        signatures[f"ZCAN_{name}"] = (u, [p])
    for name in ("GetDeviceInf", "ReadChannelErrInfo", "ReadChannelStatus"):
        signatures[f"ZCAN_{name}"] = (u, [p, p])
    for name in ("Transmit", "TransmitFD", "TransmitData"):
        signatures[f"ZCAN_{name}"] = (u, [p, p, u])
    for name in ("Receive", "ReceiveFD", "ReceiveData"):
        signatures[f"ZCAN_{name}"] = (u, [p, p, u, ct.c_int])
    for name, (result, arguments) in signatures.items():
        function = getattr(dll, name)
        function.restype = result
        function.argtypes = arguments
