"""Tool installer: checksums fail closed, free-space and write errors, stale temp files."""

from __future__ import annotations

import errno
import hashlib
import io
import os
import shutil
import sys
import threading
import time
import zipfile
from collections import namedtuple
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import core.deps as deps
from core.deps import (
    IN_USE_MESSAGE,
    Asset,
    DependencyInstaller,
    asset_for,
    check_free_space,
    clean_stale_downloads,
    find_checksum,
    friendly_os_error,
    install_downloaded_file,
    space_needed,
    unverified_message,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "0123456789abcdef" * 4

DiskUsage = namedtuple("DiskUsage", "total used free")


# --------------------------------------------------------------------------- find_checksum


def test_gnu_checksum_file_picks_the_exact_name() -> None:
    text = f"{HASH_A}  yt-dlp\n{HASH_B}  yt-dlp.exe\n{HASH_C}  yt-dlp_linux\n"
    assert find_checksum(text, "yt-dlp.exe") == HASH_B
    assert find_checksum(text, "yt-dlp") == HASH_A
    assert find_checksum(text, "yt-dlp_linux") == HASH_C


def test_gnu_binary_marker_and_paths() -> None:
    text = f"{HASH_A.upper()} *dist/tool.zip\r\n{HASH_B}  ./other.zip\r\n"
    assert find_checksum(text, "tool.zip") == HASH_A
    assert find_checksum(text, "other.zip") == HASH_B


def test_powershell_get_filehash_list() -> None:
    # Exactly how Deno publishes the Windows checksum (Format-List, CRLF, upper-case hash).
    text = (
        "\r\nAlgorithm : SHA256\r\n"
        f"Hash      : {HASH_C.upper()}\r\n"
        "Path      : C:\\a\\deno\\deno\\target\\release\\deno-x86_64-pc-windows-msvc.zip\r\n\r\n"
    )
    assert find_checksum(text, "deno-x86_64-pc-windows-msvc.zip") == HASH_C
    assert find_checksum(text, "deno-aarch64-pc-windows-msvc.zip") is None


def test_powershell_list_with_several_files() -> None:
    text = (
        f"Algorithm : SHA256\nHash      : {HASH_A}\nPath      : C:\\out\\one.zip\n\n"
        f"Algorithm : SHA256\nHash      : {HASH_B}\nPath      : C:\\out\\two.zip\n"
    )
    assert find_checksum(text, "two.zip") == HASH_B
    assert find_checksum(text, "one.zip") == HASH_A
    assert find_checksum(text, "three.zip") is None


def test_shared_checksum_file_without_a_match_does_not_fall_back() -> None:
    assert find_checksum(f"{HASH_A}  ffmpeg-win64.zip\n{HASH_B}  ffmpeg-linux64.tar.xz\n",
                         "ffmpeg-winarm64.zip") is None
    # A lone hash for some other file must not be accepted from a shared checksum file.
    assert find_checksum(f"{HASH_A}  something-else.zip\n", "tool.zip") is None
    assert find_checksum(HASH_A, "tool.zip") is None
    assert find_checksum("", "tool.zip") is None


def test_single_file_checksum_accepts_a_lone_hash() -> None:
    assert find_checksum(f"{HASH_A}\n", "tool.zip", single_file=True) == HASH_A
    assert find_checksum(f"{HASH_A}  target/release/tool.zip\n", "tool.zip", single_file=True) == HASH_A
    assert find_checksum(f"{HASH_A}  a.zip\n{HASH_B}  b.zip\n", "tool.zip", single_file=True) is None


def test_checksum_text_in_utf16_is_decoded() -> None:
    text = f"Hash      : {HASH_A}\r\nPath      : C:\\x\\tool.zip\r\n"
    for encoded in (text.encode("utf-16"), ("\ufeff" + text).encode("utf-8"), text.encode("utf-8")):
        assert find_checksum(deps._decode_text(encoded), "tool.zip") == HASH_A


def test_only_deno_uses_a_single_file_checksum() -> None:
    for key in deps.TOOL_KEYS:
        asset = asset_for(key)
        assert asset.checksum_url
        assert asset.size_estimate > 0
        assert asset.single_file_checksum == (key == "deno")


# --------------------------------------------------------------------------- disk space and errors


def test_check_free_space(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    size = 100 * 1024 * 1024
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: DiskUsage(0, 0, space_needed(size)))
    assert check_free_space(tmp_path, size) is None

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: DiskUsage(0, 0, size * 2))
    text = check_free_space(tmp_path, size)
    assert text is not None
    assert text.startswith("Not enough free space on ")
    assert "(needs about 250 MB)" in text

    def broken(_p: Any) -> Any:
        raise OSError("can't tell")

    monkeypatch.setattr(shutil, "disk_usage", broken)
    assert check_free_space(tmp_path, size) is None  # unknown: carry on, writes are still checked


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters are Windows-only")
def test_space_message_names_the_drive() -> None:
    assert deps.not_enough_space_message(Path("D:/Weir/bin"), 5 * 1024 * 1024) == (
        "Not enough free space on D: (needs about 5 MB)."
    )


def test_friendly_os_error(tmp_path: Path) -> None:
    full = friendly_os_error(OSError(errno.ENOSPC, "No space left on device"), tmp_path, 3 * 1024 * 1024)
    assert full is not None and full.startswith("Not enough free space on ")
    assert "(needs about 3 MB)" in full
    assert friendly_os_error(PermissionError(errno.EACCES, "Access is denied"), tmp_path, 0) == IN_USE_MESSAGE
    assert friendly_os_error(OSError(errno.EBUSY, "Busy"), tmp_path, 0) == IN_USE_MESSAGE
    assert friendly_os_error(FileNotFoundError(errno.ENOENT, "Gone"), tmp_path, 0) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows error codes")
def test_friendly_os_error_windows_codes(tmp_path: Path) -> None:
    assert friendly_os_error(OSError(None, "Sharing violation", None, 32), tmp_path, 0) == IN_USE_MESSAGE
    disk_full = friendly_os_error(OSError(None, "Disk full", None, 112), tmp_path, 1024 * 1024)
    assert disk_full is not None and disk_full.startswith("Not enough free space on ")


def test_unpacking_on_a_full_disk_leaves_no_partial_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive_path = tmp_path / "tool.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("pkg/tool.exe", b"new tool")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool.exe").write_bytes(b"old tool")

    def full_disk(source: Any, out: Any, length: int = 0) -> None:
        out.write(b"half")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shutil, "copyfileobj", full_disk)
    asset = Asset("deno", "http://x/tool.zip", "tool.zip", "http://x/sum", "zip",
                  {"tool.exe": "tool.exe"}, "1 KB")
    with pytest.raises(OSError):
        install_downloaded_file(asset, archive_path, bin_dir)
    assert (bin_dir / "tool.exe").read_bytes() == b"old tool"
    assert not (bin_dir / "tool.exe.new").exists()


# --------------------------------------------------------------------------- stale temp files


def _make_old(path: Path, data: bytes = b"x") -> Path:
    path.write_bytes(data)
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def test_clean_stale_downloads(tmp_path: Path) -> None:
    stale = [_make_old(tmp_path / ".yt-dlp.exe.download"), _make_old(tmp_path / "ffmpeg.exe.new")]
    keep = [_make_old(tmp_path / "yt-dlp.exe"), _make_old(tmp_path / "notes.download")]
    fresh = tmp_path / ".deno.zip.download"
    fresh.write_bytes(b"still downloading in another window")
    (tmp_path / "folder.new").mkdir()

    removed = clean_stale_downloads([tmp_path, tmp_path / "missing"])
    assert sorted(removed) == sorted(stale)
    assert not any(p.exists() for p in stale)
    assert all(p.exists() for p in keep)
    assert fresh.exists()
    assert (tmp_path / "folder.new").is_dir()

    assert clean_stale_downloads([tmp_path], min_age=0) == [fresh]


def test_deps_manager_cleans_up_on_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qapp) -> None:
    leftover = _make_old(tmp_path / ".ffmpeg.zip.download")
    monkeypatch.setattr(deps, "bin_dirs", lambda: [tmp_path])
    deps.DepsManager()
    assert not leftover.exists()


# --------------------------------------------------------------------------- installer, local server


class _Server:
    """A tiny HTTP server on 127.0.0.1. routes: path -> list of (status, body, send_length);
    each request takes the next entry (the last one repeats)."""

    def __init__(self) -> None:
        self.routes: dict[str, list[tuple[int, bytes, bool]]] = {}
        self.hits: dict[str, int] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                count = server.hits.get(self.path, 0)
                server.hits[self.path] = count + 1
                answers = server.routes.get(self.path) or [(404, b"not found", True)]
                status, body, send_length = answers[min(count, len(answers) - 1)]
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                if send_length:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass  # the client gave up (e.g. not enough space)

            def log_message(self, *_args: Any) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[_Server]:
    srv = _Server()
    yield srv
    srv.close()


TOOL_BODY = os.urandom(2 * 1024 * 1024)
TOOL_HASH = hashlib.sha256(TOOL_BODY).hexdigest()


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: _Server):
    """Point the installer at the local server and a temporary bin folder."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    asset = Asset("yt-dlp", f"{server.base}/tool.bin", "tool.bin", f"{server.base}/SUMS", "binary",
                  {"": "yt-dlp-test.bin"}, "2 MB", size_estimate=10 * 1024 * 1024)
    monkeypatch.setattr(deps, "asset_for", lambda key: asset)
    monkeypatch.setattr(deps, "writable_bin_dir", lambda: bin_dir)
    monkeypatch.setattr(DependencyInstaller, "CHECKSUM_RETRY_DELAY_MS", 0)
    server.routes["/tool.bin"] = [(200, TOOL_BODY, True)]
    return server, bin_dir


def _run(keys: list[str]) -> tuple[list[tuple[str, bool, str]], list[bool]]:
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

    installer = DependencyInstaller()
    results: list[tuple[str, bool, str]] = []
    done: list[bool] = []
    loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    installer.tool_finished.connect(lambda key, ok, text: results.append((key, ok, text)))
    installer.all_finished.connect(lambda ok: (done.append(ok), loop.quit()))
    timeout.start(20_000)
    installer.install(keys)
    if not done:
        loop.exec()
    timeout.stop()
    busy = installer.busy
    # Let the finished network replies (deleteLater) go while the installer still exists,
    # so nothing is torn down from inside a reply's destructor.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del installer
    assert done, "the installer never finished"
    assert not busy
    return results, done


def _leftovers(bin_dir: Path) -> list[str]:
    return sorted(p.name for p in bin_dir.iterdir())


def test_installs_a_verified_tool(setup, qapp) -> None:
    server, bin_dir = setup
    server.routes["/SUMS"] = [(200, f"{HASH_A}  other.bin\n{TOOL_HASH}  tool.bin\n".encode(), True)]
    results, done = _run(["yt-dlp"])
    assert done == [True]
    assert results[0][:2] == ("yt-dlp", True)
    assert (bin_dir / "yt-dlp-test.bin").read_bytes() == TOOL_BODY
    assert _leftovers(bin_dir) == ["yt-dlp-test.bin"]


def test_checksum_request_is_retried_once(setup, qapp) -> None:
    server, bin_dir = setup
    server.routes["/SUMS"] = [(500, b"oops", True), (200, f"{TOOL_HASH}  tool.bin\n".encode(), True)]
    results, done = _run(["yt-dlp"])
    assert done == [True]
    assert server.hits["/SUMS"] == 2
    assert (bin_dir / "yt-dlp-test.bin").read_bytes() == TOOL_BODY


def test_unreachable_checksum_fails_closed(setup, qapp) -> None:
    server, bin_dir = setup
    server.routes["/SUMS"] = [(404, b"not found", True)]
    results, done = _run(["yt-dlp"])
    assert done == [False]
    assert results == [("yt-dlp", False, unverified_message("yt-dlp"))]
    assert results[0][2] == "Couldn't verify yt-dlp right now. Please try again in a few minutes."
    assert server.hits["/SUMS"] == 2  # tried twice
    assert "/tool.bin" not in server.hits  # the tool itself was never downloaded
    assert _leftovers(bin_dir) == []


def test_checksum_file_without_the_asset_fails_closed(setup, qapp) -> None:
    server, bin_dir = setup
    # One hash, but for a different file: must not be taken as tool.bin's.
    server.routes["/SUMS"] = [(200, f"{TOOL_HASH}  other.bin\n".encode(), True)]
    results, done = _run(["yt-dlp"])
    assert done == [False]
    assert results == [("yt-dlp", False, unverified_message("yt-dlp"))]
    assert "/tool.bin" not in server.hits
    assert _leftovers(bin_dir) == []


def test_checksum_mismatch_installs_nothing(setup, qapp) -> None:
    server, bin_dir = setup
    server.routes["/SUMS"] = [(200, f"{HASH_A}  tool.bin\n".encode(), True)]
    results, done = _run(["yt-dlp"])
    assert done == [False]
    assert results[0][1] is False and "damaged" in results[0][2]
    assert _leftovers(bin_dir) == []


def test_not_enough_space_uses_content_length(setup, qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    server, bin_dir = setup
    server.routes["/SUMS"] = [(200, f"{TOOL_HASH}  tool.bin\n".encode(), True)]
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: DiskUsage(0, 0, 1024 * 1024))
    results, done = _run(["yt-dlp"])
    assert done == [False]
    key, ok, text = results[0]
    assert not ok
    assert text.startswith("Not enough free space on ")
    assert "(needs about 5 MB)" in text  # 2.5 x the 2 MB the server announced
    assert _leftovers(bin_dir) == []


def test_not_enough_space_uses_estimate_without_length(
    setup, qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, bin_dir = setup
    server.routes["/SUMS"] = [(200, f"{TOOL_HASH}  tool.bin\n".encode(), True)]
    server.routes["/tool.bin"] = [(200, TOOL_BODY, False)]  # no Content-Length
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: DiskUsage(0, 0, 20 * 1024 * 1024))
    results, done = _run(["yt-dlp"])
    assert done == [False]
    assert "(needs about 25 MB)" in results[0][2]  # 2.5 x the 10 MB estimate
    assert _leftovers(bin_dir) == []


class _FailingFile(io.BytesIO):
    """A temp file whose write (or close) fails like a full disk or a locked file."""

    def __init__(self, path: Path, error: OSError, on_close: bool = False) -> None:
        super().__init__()
        path.write_bytes(b"")  # the real temp file exists, as it would
        self.error = error
        self.on_close = on_close

    def write(self, data: Any) -> int:
        if not self.on_close:
            raise self.error
        return super().write(data)

    def close(self) -> None:
        if self.on_close and not self.closed:
            super().close()
            raise self.error
        super().close()


@pytest.mark.parametrize(
    ("error", "on_close", "expected"),
    [
        (OSError(errno.ENOSPC, "No space left on device"), False, "Not enough free space on "),
        (OSError(errno.ENOSPC, "No space left on device"), True, "Not enough free space on "),
        (PermissionError(errno.EACCES, "Access is denied"), False, IN_USE_MESSAGE),
    ],
)
def test_write_errors_are_reported_plainly(
    setup, qapp, monkeypatch: pytest.MonkeyPatch, error: OSError, on_close: bool, expected: str
) -> None:
    server, bin_dir = setup
    server.routes["/SUMS"] = [(200, f"{TOOL_HASH}  tool.bin\n".encode(), True)]

    def fake_open(path: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        return _FailingFile(Path(path), error, on_close)

    monkeypatch.setattr(deps, "open", fake_open, raising=False)
    results, done = _run(["yt-dlp"])
    assert done == [False]
    key, ok, text = results[0]
    assert not ok
    assert text.startswith(expected)
    assert _leftovers(bin_dir) == []  # temp file removed, no tool installed
