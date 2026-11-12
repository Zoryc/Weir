"""Filesystem locations used by the app (config, bundled resources, tool folder)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from . import APP_NAME

IS_WINDOWS: bool = sys.platform == "win32"
IS_LINUX: bool = sys.platform.startswith("linux")


def is_frozen() -> bool:
    """True when running from a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Folder that contains the executable (frozen) or the project root (source)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Folder that holds bundled read-only data files (PyInstaller's _internal)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else app_dir()


def _standard_dir(location: QStandardPaths.StandardLocation, fallback: Path) -> Path:
    loc = QStandardPaths.writableLocation(location)
    path = Path(loc) if loc else fallback
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def config_dir() -> Path:
    """Per-user settings folder (%LOCALAPPDATA%\\Weir or ~/.config/Weir)."""
    return _standard_dir(
        QStandardPaths.StandardLocation.AppConfigLocation, Path.home() / ".config" / APP_NAME
    )


def user_data_dir() -> Path:
    """Per-user data folder, used for tools when the app folder is read-only."""
    return _standard_dir(
        QStandardPaths.StandardLocation.AppLocalDataLocation,
        Path.home() / ".local" / "share" / APP_NAME,
    )


def default_download_dir() -> Path:
    loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return Path(loc) if loc else Path.home() / "Downloads"


def _is_writable_dir(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-test-", delete=True):
            pass
        return True
    except OSError:
        return False


def app_bin_dir() -> Path:
    return app_dir() / "bin"


def bin_dirs() -> list[Path]:
    """Folders searched for tools before the system PATH, in priority order."""
    dirs: list[Path] = []
    for candidate in (app_bin_dir(), user_data_dir() / "bin"):
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs


def writable_bin_dir() -> Path:
    """Where downloaded tools are stored: the app's own bin/ if writable, else a per-user one."""
    primary = app_bin_dir()
    if _is_writable_dir(primary):
        return primary
    fallback = user_data_dir() / "bin"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def sanitize_inherited_environment() -> None:
    """Stop our PyInstaller runtime settings from leaking into launched programs.

    A frozen Linux build points LD_LIBRARY_PATH at its bundled Qt/OpenSSL libraries;
    system programs (ffmpeg, xdg-open, file managers) can crash if they inherit it.
    The dynamic loader reads the variable only at process start, so changing it now
    affects children only.
    """
    if not is_frozen():
        return
    if IS_LINUX:
        original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is not None:
            os.environ["LD_LIBRARY_PATH"] = original
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
