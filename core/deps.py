"""Find, check and download the external tools: yt-dlp, ffmpeg/ffprobe and Deno."""

from __future__ import annotations

import codecs
import errno
import hashlib
import math
import os
import platform
import re
import shutil
import stat
import tarfile
import threading
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import APP_NAME, APP_VERSION
from .paths import IS_WINDOWS, bin_dirs, writable_bin_dir
from .process import run_quiet

TOOL_KEYS: tuple[str, ...] = ("yt-dlp", "ffmpeg", "deno")

TOOL_LABELS: dict[str, str] = {
    "yt-dlp": "yt-dlp",
    "ffmpeg": "ffmpeg + ffprobe",
    "deno": "Deno",
}

TOOL_DESCRIPTIONS: dict[str, str] = {
    "yt-dlp": "The downloader itself. Required.",
    "ffmpeg": "Merges video and audio, converts to MP3/MP4, embeds thumbnails. Strongly recommended.",
    "deno": "JavaScript runtime yt-dlp needs for full YouTube support. Recommended.",
}

GITHUB = "https://github.com"


def _exe(name: str) -> str:
    return f"{name}.exe" if IS_WINDOWS else name


def _is_arm64() -> bool:
    return platform.machine().lower() in ("arm64", "aarch64", "armv8", "armv8l")


def _candidate_names(key: str) -> list[str]:
    if key == "yt-dlp":
        if IS_WINDOWS:
            return ["yt-dlp.exe"]
        return ["yt-dlp_linux", "yt-dlp_linux_aarch64", "yt-dlp"]
    if key == "ffmpeg":
        return [_exe("ffmpeg")]
    if key == "deno":
        return [_exe("deno")]
    raise KeyError(key)


def _extra_search_dirs(key: str) -> list[Path]:
    """Common install locations that are often missing from a GUI app's PATH."""
    home = Path.home()
    if key == "deno":
        return [home / ".deno" / "bin"]
    if key == "yt-dlp" and not IS_WINDOWS:
        return [home / ".local" / "bin"]
    return []


@dataclass(frozen=True)
class ToolInfo:
    key: str
    path: Path | None = None
    version: str | None = None
    companion: Path | None = None  # ffprobe, for the ffmpeg entry
    bundled: bool = False  # lives in one of our bin/ folders

    @property
    def label(self) -> str:
        return TOOL_LABELS[self.key]

    @property
    def found(self) -> bool:
        if self.path is None:
            return False
        return self.key != "ffmpeg" or self.companion is not None


def _find_in(dirs: list[Path], names: list[str]) -> Path | None:
    for directory in dirs:
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def locate(key: str) -> ToolInfo:
    """Look in our bin/ folders first, then on PATH. Only cheap filesystem checks."""
    names = _candidate_names(key)
    own_dirs = bin_dirs()
    path = _find_in(own_dirs, names)
    bundled = path is not None
    if path is None:
        for name in names:
            found = shutil.which(name)
            if found:
                path = Path(found)
                break
    if path is None:
        path = _find_in(_extra_search_dirs(key), names)
    companion: Path | None = None
    if key == "ffmpeg" and path is not None:
        probe = path.with_name(_exe("ffprobe"))
        if probe.is_file():
            companion = probe
        else:
            which_probe = shutil.which(_exe("ffprobe"))
            companion = Path(which_probe) if which_probe else None
    return ToolInfo(key=key, path=path, companion=companion, bundled=bundled)


def probe_version(info: ToolInfo) -> str | None:
    """Ask a tool for its version. Blocking: call from a worker thread only."""
    if info.path is None:
        return None
    flag = {"yt-dlp": "--version", "ffmpeg": "-version", "deno": "--version"}[info.key]
    try:
        code, output = run_quiet([str(info.path), flag], timeout=90)
    except Exception:  # noqa: BLE001 - any failure means "version unknown"
        return None
    first = next((line.strip() for line in output.splitlines() if line.strip()), "")
    if code != 0 or not first:
        return None
    if info.key == "ffmpeg":
        match = re.search(r"version\s+(\S+)", first)
        return match.group(1) if match else first
    if info.key == "deno":
        match = re.match(r"deno\s+(\S+)", first)
        return match.group(1) if match else first
    return first


class DepsManager(QObject):
    """Keeps the latest known state of the three tools. Version checks run off the UI thread."""

    checked = Signal()  # a version check finished
    updated = Signal()  # a tool was found again right away (e.g. just installed); version may be unknown
    _results = Signal(int, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        clean_stale_downloads(bin_dirs())  # leftovers of an install that was interrupted
        self.tools: dict[str, ToolInfo] = {key: locate(key) for key in TOOL_KEYS}
        self.checking = False
        self.has_checked = False
        self._generation = 0
        self._results.connect(self._apply_results)

    def refresh(self) -> None:
        self._generation += 1
        generation = self._generation
        self.checking = True

        def work() -> None:
            results: dict[str, ToolInfo] = {}
            for key in TOOL_KEYS:
                info = locate(key)
                results[key] = replace(info, version=probe_version(info))
            self._results.emit(generation, results)

        threading.Thread(target=work, name="deps-check", daemon=True).start()

    def _apply_results(self, generation: int, results: object) -> None:
        if generation != self._generation or not isinstance(results, dict):
            return
        self.tools = results
        self.checking = False
        self.has_checked = True
        self.checked.emit()

    def relocate(self, key: str) -> ToolInfo:
        """Find a tool again immediately (cheap file checks), e.g. right after installing it,
        so the UI can show it as present without waiting for the slower version check."""
        info = locate(key)
        self.tools[key] = info  # version unknown until the next refresh()
        self.updated.emit()
        return info

    def tool(self, key: str) -> ToolInfo:
        info = self.tools.get(key)
        if info is None or (info.path is not None and not info.path.exists()):
            info = locate(key)
            self.tools[key] = info
        return info

    def missing(self) -> list[str]:
        return [key for key in TOOL_KEYS if not self.tool(key).found]


# --------------------------------------------------------------------------- installer


_MB = 1024 * 1024


@dataclass(frozen=True)
class Asset:
    key: str
    url: str
    filename: str
    checksum_url: str | None
    kind: str  # "binary" | "zip" | "tar.xz"
    targets: dict[str, str]  # file name inside the archive (or "" for binary) -> name in bin/
    approx_size: str
    size_estimate: int = 0  # bytes; used for the free-space check when the server doesn't say
    # The checksum file is published for this one asset (e.g. Deno's "<asset>.sha256sum"),
    # so a lone hash in it may be trusted even if the file name next to it differs.
    single_file_checksum: bool = False


def asset_for(key: str) -> Asset:
    arm = _is_arm64()
    if key == "yt-dlp":
        if IS_WINDOWS:
            name = "yt-dlp_arm64.exe" if arm else "yt-dlp.exe"
            target = "yt-dlp.exe"
        else:
            name = "yt-dlp_linux_aarch64" if arm else "yt-dlp_linux"
            target = name
        base = f"{GITHUB}/yt-dlp/yt-dlp/releases/latest/download"
        return Asset(key, f"{base}/{name}", name, f"{base}/SHA2-256SUMS", "binary", {"": target},
                     "~25–40 MB", size_estimate=45 * _MB)
    if key == "ffmpeg":
        if IS_WINDOWS:
            name = f"ffmpeg-master-latest-{'winarm64' if arm else 'win64'}-gpl.zip"
            kind = "zip"
            size = "~200 MB"
            estimate = 210 * _MB
        else:
            name = f"ffmpeg-master-latest-{'linuxarm64' if arm else 'linux64'}-gpl.tar.xz"
            kind = "tar.xz"
            size = "~150 MB"
            estimate = 160 * _MB
        base = f"{GITHUB}/yt-dlp/FFmpeg-Builds/releases/download/latest"
        targets = {_exe("ffmpeg"): _exe("ffmpeg"), _exe("ffprobe"): _exe("ffprobe")}
        return Asset(key, f"{base}/{name}", name, f"{base}/checksums.sha256", kind, targets, size,
                     size_estimate=estimate)
    if key == "deno":
        triple = "aarch64" if arm else "x86_64"
        triple += "-pc-windows-msvc" if IS_WINDOWS else "-unknown-linux-gnu"
        name = f"deno-{triple}.zip"
        base = f"{GITHUB}/denoland/deno/releases/latest/download"
        return Asset(key, f"{base}/{name}", name, f"{base}/{name}.sha256sum", "zip",
                     {_exe("deno"): _exe("deno")}, "~45 MB", size_estimate=50 * _MB,
                     single_file_checksum=True)
    raise KeyError(key)


_HEX64 = re.compile(r"\b([0-9a-fA-F]{64})\b")


def _names_file(text: str, filename: str) -> bool:
    """True if text (what follows a hash, or a PowerShell Path value) names filename."""
    name = text.strip().lstrip("*").strip()
    return name == filename or name.endswith(("/" + filename, "\\" + filename))


def find_checksum(text: str, filename: str, single_file: bool = False) -> str | None:
    """Find the SHA-256 for filename in a checksum file.

    Understands GNU lines ("<hash>  <name>" or "<hash> *<name>") and PowerShell's
    Get-FileHash list ("Hash : <HASH>" followed by "Path : ...\\<name>"). Only when
    single_file is True (a checksum file published for this one asset, like Deno's
    "<asset>.sha256sum") is a lone hash accepted without its file name: in a shared
    checksum file that would let any other file's hash through.
    """
    pending: str | None = None  # PowerShell: a hash waiting for its Path line
    for line in text.splitlines():
        label, sep, value = line.partition(":")
        label = label.strip().lower()
        if sep and label == "hash":
            match = _HEX64.fullmatch(value.strip())
            pending = match.group(1).lower() if match else None
            continue
        if sep and label == "path":
            if pending is not None and _names_file(value, filename):
                return pending
            pending = None
            continue
        match = _HEX64.search(line)
        if match and _names_file(line[match.end():], filename):
            return match.group(1).lower()
    if single_file:
        hashes = {found.lower() for found in _HEX64.findall(text)}
        if len(hashes) == 1:
            return hashes.pop()
    return None


def _decode_text(data: bytes) -> str:
    """Decode a small text file that may be UTF-8 or (from Windows PowerShell) UTF-16."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def unverified_message(key: str) -> str:
    return f"Couldn't verify {TOOL_LABELS[key]} right now. Please try again in a few minutes."


# -- free space and write errors ---------------------------------------------

SPACE_FACTOR = 2.5  # download + unpacked files + the copy being replaced, roughly

_DISK_FULL_ERRNOS = {errno.ENOSPC} | ({errno.EDQUOT} if hasattr(errno, "EDQUOT") else set())
_DISK_FULL_WINERRORS = {39, 112}  # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL
_IN_USE_ERRNOS = {errno.EACCES, errno.EPERM, errno.EBUSY} | (
    {errno.ETXTBSY} if hasattr(errno, "ETXTBSY") else set()
)
_IN_USE_WINERRORS = {5, 32, 33}  # access denied, sharing violation, lock violation

IN_USE_MESSAGE = "Couldn't write the tools folder — is Weir already running or the file in use?"


def space_needed(size: int) -> int:
    """Free bytes to ask for before downloading a file of size bytes."""
    return int(max(size, 0) * SPACE_FACTOR)


def _drive_label(directory: Path) -> str:
    """'C:' on Windows; the mount point (e.g. '/' or '/home') on Linux."""
    if IS_WINDOWS:
        return directory.drive or str(directory)
    path = os.path.abspath(directory)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def not_enough_space_message(directory: Path, needed: int) -> str:
    megabytes = max(1, math.ceil(needed / _MB))
    return f"Not enough free space on {_drive_label(directory)} (needs about {megabytes} MB)."


def check_free_space(directory: Path, size: int) -> str | None:
    """A message for the user if directory's disk can't hold a download of size bytes,
    else None. If the free space can't be read, carry on: write errors are still caught."""
    needed = space_needed(size)
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return None
    if free >= needed:
        return None
    return not_enough_space_message(directory, needed)


def friendly_os_error(exc: OSError, directory: Path, needed: int) -> str | None:
    """A plain message for a full disk or a locked/in-use file, else None."""
    winerror = getattr(exc, "winerror", None)
    if exc.errno in _DISK_FULL_ERRNOS or winerror in _DISK_FULL_WINERRORS:
        return not_enough_space_message(directory, needed)
    if exc.errno in _IN_USE_ERRNOS or winerror in _IN_USE_WINERRORS:
        return IN_USE_MESSAGE
    return None


def clean_stale_downloads(directories: Iterable[Path], min_age: float = 120.0) -> list[Path]:
    """Delete leftovers of interrupted installs ('.<asset>.download' and '<tool>.new') from
    our bin folders. Files changed in the last min_age seconds are kept, in case another
    Weir window is installing right now. Returns what was removed."""
    removed: list[Path] = []
    now = time.time()
    for directory in directories:
        try:
            with os.scandir(directory) as scan:
                entries = list(scan)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if not ((name.startswith(".") and name.endswith(".download")) or name.endswith(".new")):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if now - entry.stat(follow_symlinks=False).st_mtime < min_age:
                    continue
                os.unlink(entry.path)
            except OSError:
                continue
            removed.append(Path(entry.path))
    return removed


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _make_executable(path: Path) -> None:
    if not IS_WINDOWS:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_member(source: BinaryIO, destination: Path) -> None:
    """Write to '<name>.new' first, so a failure (e.g. a full disk) never leaves a
    half-written tool in place; the temporary file is removed if anything goes wrong."""
    temp = destination.with_name(destination.name + ".new")
    try:
        with open(temp, "wb") as out:
            shutil.copyfileobj(source, out, length=1024 * 1024)
        _make_executable(temp)
        os.replace(temp, destination)
    except BaseException:
        _remove_quietly(temp)
        raise


def install_downloaded_file(asset: Asset, downloaded: Path, bin_dir: Path) -> list[Path]:
    """Move or extract a downloaded asset into bin_dir. Blocking: run in a worker thread."""
    installed: list[Path] = []
    if asset.kind == "binary":
        target = bin_dir / asset.targets[""]
        _make_executable(downloaded)
        os.replace(downloaded, target)
        return [target]

    wanted = dict(asset.targets)
    if asset.kind == "zip":
        with zipfile.ZipFile(downloaded) as archive:
            for member in archive.infolist():
                base = member.filename.rsplit("/", 1)[-1]
                if member.is_dir() or base not in wanted:
                    continue
                target = bin_dir / wanted.pop(base)
                with archive.open(member) as source:
                    _copy_member(source, target)
                installed.append(target)
                if not wanted:
                    break
    elif asset.kind == "tar.xz":
        with tarfile.open(downloaded, mode="r:xz") as archive:
            for member in archive:
                base = member.name.rsplit("/", 1)[-1]
                if not member.isfile() or base not in wanted:
                    continue
                source = archive.extractfile(member)
                if source is None:
                    continue
                target = bin_dir / wanted.pop(base)
                with source:
                    _copy_member(source, target)
                installed.append(target)
                if not wanted:
                    break
    else:
        raise ValueError(f"Unknown archive type: {asset.kind}")

    if wanted:
        raise RuntimeError(f"The download didn't contain: {', '.join(sorted(wanted))}")
    try:
        downloaded.unlink()
    except OSError:
        pass
    return installed


def _human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


class DependencyInstaller(QObject):
    """Downloads tools into bin/ with progress. Network I/O is asynchronous (Qt);
    checksum verification is incremental; unpacking runs in a worker thread.

    Fails closed: a tool whose published SHA-256 can't be fetched or found is not installed."""

    progress = Signal(str, float, float)  # key, received bytes, total bytes (0 = unknown)
    message = Signal(str)
    stage = Signal(str, str)  # key, "downloading" | "installing"
    tool_finished = Signal(str, bool, str)  # key, ok, message
    all_finished = Signal(bool)  # True if every requested tool installed
    _unpacked = Signal(str, bool, str)

    CHECKSUM_ATTEMPTS = 2  # the checksum file is asked for twice before giving up
    CHECKSUM_RETRY_DELAY_MS = 1500

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._queue: list[str] = []
        self._current: Asset | None = None
        self._reply: QNetworkReply | None = None
        self._file: BinaryIO | None = None
        self._temp_path: Path | None = None
        self._bin_dir: Path | None = None
        self._hasher: Any = None  # hashlib sha256 object while downloading
        self._expected_hash: str | None = None
        self._checksum_attempts = 0
        self._written = 0  # bytes written to the temp file
        self._needed = 0  # free bytes the current download asks for
        self._space_checked = False
        self._write_error: str | None = None  # set when saving the download failed
        self._all_ok = True
        self._cancelled = False
        self._busy = False
        self._unpacked.connect(self._on_unpacked)

    @property
    def busy(self) -> bool:
        return self._busy

    def install(self, keys: list[str]) -> None:
        if self._busy:
            return
        self._queue = [k for k in TOOL_KEYS if k in keys]
        if not self._queue:
            self.all_finished.emit(True)
            return
        self._busy = True
        self._all_ok = True
        self._cancelled = False
        self._next()

    def cancel(self) -> None:
        if not self._busy:
            return
        self._cancelled = True
        self._queue.clear()
        if self._reply is not None:
            self._reply.abort()

    # -- internals ---------------------------------------------------------

    def _request(self, url: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f"{APP_NAME}/{APP_VERSION}")
        request.setTransferTimeout(60_000)
        return request

    def _next(self) -> None:
        if self._cancelled or not self._queue:
            self._busy = False
            self.all_finished.emit(self._all_ok and not self._cancelled)
            return
        key = self._queue.pop(0)
        try:
            self._bin_dir = writable_bin_dir()
        except OSError as exc:
            self._fail(key, f"Can't create the tools folder: {exc}")
            return
        self._current = asset_for(key)
        self.stage.emit(key, "downloading")
        self._expected_hash = None
        self._checksum_attempts = 0
        if not self._current.checksum_url:
            self._fail(key, unverified_message(key))  # never install a tool we can't check
            return
        self.message.emit(f"Preparing {TOOL_LABELS[key]}…")
        self._fetch_checksum()

    def _fetch_checksum(self) -> None:
        asset = self._current
        if asset is None or not asset.checksum_url:
            return
        if self._cancelled:
            self._fail(asset.key, "Cancelled.")
            return
        self._checksum_attempts += 1
        reply = self._nam.get(self._request(asset.checksum_url))
        self._reply = reply
        reply.finished.connect(lambda r=reply: self._on_checksum(r))

    def _on_checksum(self, reply: QNetworkReply) -> None:
        self._reply = None
        reply.deleteLater()
        asset = self._current
        if asset is None:
            return
        if self._cancelled:
            self._fail(asset.key, "Cancelled.")
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        failed = reply.error() != QNetworkReply.NetworkError.NoError or (
            isinstance(status, int) and status >= 400
        )
        if failed:
            if self._checksum_attempts < self.CHECKSUM_ATTEMPTS:
                QTimer.singleShot(self.CHECKSUM_RETRY_DELAY_MS, self, self._fetch_checksum)
            else:
                self._fail(asset.key, unverified_message(asset.key))
            return
        text = _decode_text(bytes(reply.readAll().data()))
        self._expected_hash = find_checksum(text, asset.filename, asset.single_file_checksum)
        if self._expected_hash is None:
            self._fail(asset.key, unverified_message(asset.key))
            return
        self._start_download()

    def _start_download(self) -> None:
        asset = self._current
        assert asset is not None and self._bin_dir is not None
        self._temp_path = self._bin_dir / f".{asset.filename}.download"
        self._written = 0
        self._needed = space_needed(asset.size_estimate)
        self._space_checked = False
        self._write_error = None
        try:
            self._file = open(self._temp_path, "wb")
        except OSError as exc:
            text = friendly_os_error(exc, self._bin_dir, self._needed)
            self._fail(asset.key, text or f"Can't write to {self._bin_dir}: {exc}")
            return
        self._hasher = hashlib.sha256()
        self.message.emit(f"Downloading {TOOL_LABELS[asset.key]} ({asset.approx_size})…")
        reply = self._nam.get(self._request(asset.url))
        self._reply = reply
        reply.readyRead.connect(lambda r=reply: self._on_ready_read(r))
        reply.downloadProgress.connect(self._on_progress)
        reply.finished.connect(lambda r=reply: self._on_download_finished(r))

    @staticmethod
    def _content_length(reply: QNetworkReply) -> int:
        value = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
        try:
            return max(int(value), 0) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    def _on_ready_read(self, reply: QNetworkReply) -> None:
        if self._write_error is not None or self._file is None or self._hasher is None:
            return
        if not self._space_checked:
            # First bytes of the real file: check the disk before writing anything.
            self._space_checked = True
            asset = self._current
            assert asset is not None and self._bin_dir is not None
            size = self._content_length(reply) or asset.size_estimate
            self._needed = space_needed(size)
            problem = check_free_space(self._bin_dir, size)
            if problem is not None:
                self._abort_download(reply, problem)
                return
        data = bytes(reply.readAll().data())
        if not data:
            return
        try:
            self._file.write(data)
        except OSError as exc:
            self._abort_download(reply, self._write_failure(exc))
            return
        self._written += len(data)
        self._hasher.update(data)

    def _write_failure(self, exc: OSError) -> str:
        assert self._bin_dir is not None
        text = friendly_os_error(exc, self._bin_dir, self._needed)
        return text or f"Couldn't save the download: {exc.strerror or exc}"

    def _abort_download(self, reply: QNetworkReply, text: str) -> None:
        """Stop a download that can't be saved; _on_download_finished reports text.
        Must be the last thing a slot does: abort() may finish the reply right away."""
        self._write_error = text
        self._close_temp(delete=True)
        if reply.isRunning():
            reply.abort()

    def _on_progress(self, received: int, total: int) -> None:
        asset = self._current
        if asset is None:
            return
        self.progress.emit(asset.key, float(received), float(max(total, 0)))
        if total > 0:
            self.message.emit(
                f"Downloading {TOOL_LABELS[asset.key]}… {_human_size(received)} of {_human_size(total)}"
            )

    def _close_temp(self, delete: bool) -> OSError | None:
        """Close (and maybe delete) the temp file. Returns the error if closing failed,
        which means the last buffered bytes never reached the disk."""
        error: OSError | None = None
        if self._file is not None:
            try:
                self._file.close()
            except OSError as exc:
                error = exc
            self._file = None
        if delete and self._temp_path is not None:
            _remove_quietly(self._temp_path)
        return error

    def _saved_completely(self) -> bool:
        assert self._temp_path is not None
        try:
            return self._temp_path.stat().st_size == self._written
        except OSError:
            return False

    def _on_download_finished(self, reply: QNetworkReply) -> None:
        self._on_ready_read(reply)
        self._reply = None
        reply.deleteLater()
        asset = self._current
        assert asset is not None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if self._cancelled:
            self._close_temp(delete=True)
            self._fail(asset.key, "Cancelled.")
            return
        if self._write_error is not None:
            self._close_temp(delete=True)
            self._fail(asset.key, self._write_error)
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._close_temp(delete=True)
            self._fail(asset.key, f"Download failed: {reply.errorString()}")
            return
        if isinstance(status, int) and status >= 400:
            self._close_temp(delete=True)
            self._fail(asset.key, f"Download failed (HTTP {status}).")
            return
        close_error = self._close_temp(delete=False)
        if close_error is not None or not self._saved_completely():
            self._close_temp(delete=True)
            assert self._bin_dir is not None
            if close_error is not None:
                text = self._write_failure(close_error)
            else:
                text = (f"Couldn't save {TOOL_LABELS[asset.key]} completely. Please check there's "
                        f"enough free space on {_drive_label(self._bin_dir)} and try again.")
            self._fail(asset.key, text)
            return
        if not self._expected_hash:
            self._close_temp(delete=True)
            self._fail(asset.key, unverified_message(asset.key))
            return
        digest = self._hasher.hexdigest() if self._hasher is not None else ""
        if digest != self._expected_hash:
            self._close_temp(delete=True)
            self._fail(asset.key, "The downloaded file is damaged (checksum mismatch). Please try again.")
            return

        self.message.emit(f"Installing {TOOL_LABELS[asset.key]}…")
        self.stage.emit(asset.key, "installing")
        temp_path = self._temp_path
        bin_dir = self._bin_dir
        needed = space_needed(self._written)
        assert temp_path is not None and bin_dir is not None

        def work() -> None:
            try:
                installed = install_downloaded_file(asset, temp_path, bin_dir)
                names = ", ".join(p.name for p in installed)
                self._unpacked.emit(asset.key, True, f"Installed {names} in {bin_dir}")
            except Exception as exc:  # noqa: BLE001 - report any failure to the user
                _remove_quietly(temp_path)
                text = friendly_os_error(exc, bin_dir, needed) if isinstance(exc, OSError) else None
                self._unpacked.emit(asset.key, False, text or f"Couldn't install: {exc}")

        threading.Thread(target=work, name=f"install-{asset.key}", daemon=True).start()

    def _on_unpacked(self, key: str, ok: bool, text: str) -> None:
        if not ok:
            self._fail(key, text)
            return
        self.tool_finished.emit(key, True, text)
        self._next()

    def _fail(self, key: str, text: str) -> None:
        self._all_ok = False
        self.tool_finished.emit(key, False, text)
        self._next()


def describe(info: ToolInfo) -> str:
    """One-line status for dialogs."""
    if not info.found:
        if info.key == "ffmpeg" and info.path is not None:
            return "ffprobe is missing"
        return "Not found"
    version = info.version or "version unknown"
    where = "in the app's tools folder" if info.bundled else str(info.path.parent if info.path else "")
    return f"{version} — {where}"

