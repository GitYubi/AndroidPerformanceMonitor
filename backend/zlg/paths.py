"""Resources are relative to the source tree, never the shell's working directory."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return ROOT.joinpath(*parts)


def data_path(name: str) -> Path:
    default = ROOT
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            default = Path.home() / "Library" / "Application Support" / "ZCANServer"
        elif sys.platform == "win32":
            default = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ZCANServer"
    base = Path(os.environ.get("ZCAN_DATA_DIR", default)).expanduser().resolve()
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    return path
