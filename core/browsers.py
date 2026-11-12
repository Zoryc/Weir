"""Web browsers yt-dlp can read cookies from (--cookies-from-browser), and which of them
are set up on this computer.

A browser counts as installed when its profile folder is there: that is where yt-dlp looks
for the cookies, so a browser that was never started has none to offer either. Firefox
also needs a profile with a cookies database, otherwise yt-dlp stops with "could not find
firefox cookies database". Plain Python (no Qt), so the settings can use it too.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

# yt-dlp's --cookies-from-browser keys, in the order Weir suggests them.
BROWSERS: tuple[tuple[str, str], ...] = (
    ("firefox", "Firefox"),
    ("chrome", "Chrome"),
    ("edge", "Edge"),
    ("brave", "Brave"),
    ("chromium", "Chromium"),
    ("vivaldi", "Vivaldi"),
    ("opera", "Opera"),
)
BROWSER_LABELS: dict[str, str] = dict(BROWSERS)

# On Windows these encrypt their cookies so that only the browser itself can read them
# ("app-bound" encryption): yt-dlp fails with DPAPI or decryption errors, and closing the
# browser doesn't help. They are still listed, but never suggested there.
WINDOWS_LOCKED: frozenset[str] = frozenset({"chrome", "edge", "brave"})

# A cookie setting may hold "file:<path to cookies.txt>" instead of a browser key
# (the same prefix as core.downloader.COOKIES_FILE_PREFIX, which turns it into --cookies).
COOKIES_FILE_PREFIX = "file:"

FIREFOX_COOKIES_DB = "cookies.sqlite"

# The system whose folders and rules apply by default (tests may change it).
PLATFORM: str = sys.platform


def browser_label(key: str) -> str:
    """The name to show, e.g. Firefox for "firefox". Unknown keys are capitalised."""
    return BROWSER_LABELS.get(key.lower(), key[:1].upper() + key[1:])


def is_cookies_file(value: str) -> bool:
    return value.startswith(COOKIES_FILE_PREFIX)


def cookies_file_path(value: str) -> str:
    """The path in a "file:<path>" setting ("" for anything else)."""
    return value[len(COOKIES_FILE_PREFIX):].strip() if is_cookies_file(value) else ""


def valid_cookie_setting(value: str) -> bool:
    """True for "" (no cookies), a known browser key or "file:<path>"."""
    if is_cookies_file(value):
        return bool(cookies_file_path(value))
    return value == "" or value in BROWSER_LABELS


def is_network_path(path: str, platform: str | None = None) -> bool:
    """A UNC path (\\\\server\\share\\…) or, on Windows, a file on a mapped network drive.
    Checking whether such a file is there can hang for seconds when the network is slow or
    gone, so the UI doesn't."""
    if path.startswith(("\\\\", "//")):
        return True
    if (platform or PLATFORM) != "win32" or len(path) < 2 or path[1] != ":":
        return False
    try:
        import ctypes

        kernel32: Any = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # DRIVE_REMOTE. Answered from the drive mapping, without asking the network.
        return bool(kernel32.GetDriveTypeW(f"{path[0]}:\\") == 4)
    except (AttributeError, OSError):
        return False


def cookies_locked(key: str, platform: str | None = None) -> bool:
    """True when yt-dlp usually can't read this browser's cookies on this system."""
    return (platform or PLATFORM) == "win32" and key in WINDOWS_LOCKED


def profile_dirs(key: str, *, platform: str | None = None, env: Mapping[str, str] | None = None,
                 home: Path | None = None) -> list[Path]:
    """Where yt-dlp looks for a browser's profiles (the same places as yt-dlp itself)."""
    platform = platform or PLATFORM
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    if platform == "win32":
        roaming = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData" / "Roaming"
        local = Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else home / "AppData" / "Local"
        dirs = {
            "firefox": [
                roaming / "Mozilla" / "Firefox" / "Profiles",
                # Firefox from the Microsoft Store
                local / "Packages" / "Mozilla.Firefox_n80bbvh6b1yt2" / "LocalCache" / "Roaming"
                / "Mozilla" / "Firefox" / "Profiles",
            ],
            "chrome": [local / "Google" / "Chrome" / "User Data"],
            "edge": [local / "Microsoft" / "Edge" / "User Data"],
            "brave": [local / "BraveSoftware" / "Brave-Browser" / "User Data"],
            "chromium": [local / "Chromium" / "User Data"],
            "vivaldi": [local / "Vivaldi" / "User Data"],
            "opera": [roaming / "Opera Software" / "Opera Stable"],
        }
    else:
        config = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
        dirs = {
            "firefox": [
                home / ".mozilla" / "firefox",
                config / "mozilla" / "firefox",  # newer Firefox follows the XDG folders
                home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
                home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
                home / ".var" / "app" / "org.mozilla.firefox" / "config" / "mozilla" / "firefox",
            ],
            "chrome": [config / "google-chrome"],
            "edge": [config / "microsoft-edge"],
            "brave": [config / "BraveSoftware" / "Brave-Browser"],
            "chromium": [config / "chromium"],
            "vivaldi": [config / "vivaldi"],
            "opera": [config / "opera"],
        }
    return dirs.get(key, [])


def _has_firefox_cookies(root: Path) -> bool:
    """A cookies database where yt-dlp looks for one: in root itself, in a profile folder
    directly inside it, or in a profile folder inside root/Profiles."""
    if os.path.isfile(root / FIREFOX_COOKIES_DB):
        return True
    return _subfolder_has_cookies(root) or _subfolder_has_cookies(root / "Profiles")


def _subfolder_has_cookies(folder: Path) -> bool:
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if entry.is_dir() and os.path.isfile(Path(entry.path) / FIREFOX_COOKIES_DB):
                        return True
                except OSError:
                    continue
    except OSError:
        pass
    return False


def is_installed(key: str, *, platform: str | None = None, env: Mapping[str, str] | None = None,
                 home: Path | None = None) -> bool:
    for folder in profile_dirs(key, platform=platform, env=env, home=home):
        if not os.path.isdir(folder):
            continue
        if key != "firefox" or _has_firefox_cookies(folder):
            return True
    return False


def installed_browsers(*, platform: str | None = None, env: Mapping[str, str] | None = None,
                       home: Path | None = None) -> list[tuple[str, str]]:
    """(key, label) of every browser set up on this computer, in the order of BROWSERS.
    A handful of folder checks: quick enough for the UI thread."""
    return [(key, label) for key, label in BROWSERS
            if is_installed(key, platform=platform, env=env, home=home)]


def suggest_browser(installed: Iterable[str], *, skip: str = "",
                    platform: str | None = None) -> str | None:
    """The browser to read cookies from: the first installed one in the order of BROWSERS
    (Firefox first), leaving out `skip` (the one that just failed) and, on Windows, the
    browsers whose cookies yt-dlp can't read there. None when nothing is left."""
    available = set(installed)
    for key, _label in BROWSERS:
        if key in available and key != skip and not cookies_locked(key, platform):
            return key
    return None
