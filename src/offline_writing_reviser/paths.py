from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIRECTORY_NAME = "OfflineWritingReviser"


def app_data_dir() -> Path:
    """Return the per-user directory used for configuration and logs."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_DIRECTORY_NAME
    return Path.home() / "AppData" / "Local" / APP_DIRECTORY_NAME


def resource_path(relative_path: str | Path) -> Path:
    """Resolve a bundled PyInstaller resource or a source-tree resource."""
    relative = Path(relative_path)
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / relative
    return Path(__file__).resolve().parent / relative


def executable_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(sys.executable).resolve()
