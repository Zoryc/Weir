"""Tests for the download queue: cancel cleanup, duplicate detection and cookie arguments."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QProcess

from core.deps import ToolInfo
from core.downloader import (
    DownloadJob,
    DownloadManager,
    DownloadOptions,
    JobStatus,
    _cookie_source_for_errors,
    _norm_path,
    build_arguments,
)
from core.presets import Preset

STEM = "Some Video [abc123]"


class FakeDeps:
    """Stands in for DepsManager: no tools installed, nothing touched on disk."""

    def tool(self, key: str) -> ToolInfo:
        return ToolInfo(key)


def _options(save_dir: str, cookies: str = "") -> DownloadOptions:
    return DownloadOptions(
        save_dir=save_dir,
        playlist=False,
        embed_subs=False,
        embed_meta=False,
        extra_args=[],
        filename_template="%(title)s [%(id)s].%(ext)s",
        cookies_browser=cookies,
        rate_limit="",
    )


def _preset(audio: bool = False) -> Preset:
    if audio:
        return Preset("Audio – MP3", ["-x", "--audio-format", "mp3"])
    return Preset("Best video – MP4", ["-t", "mp4"])


@pytest.fixture
def manager(qapp) -> DownloadManager:
    return DownloadManager(FakeDeps())  # type: ignore[arg-type]


def _put(
    manager: DownloadManager,
    url: str,
    save_dir: str,
    status: JobStatus = JobStatus.WAITING,
    audio: bool = False,
    extra_args: list[str] | None = None,
) -> DownloadJob:
    """Add a job without starting yt-dlp."""
    options = _options(save_dir)
    options.extra_args = list(extra_args or [])
    job = DownloadJob(id=manager._next_id, url=url, preset=_preset(audio), options=options)
    job.status = status
    manager._next_id += 1
    manager._jobs[job.id] = job
    return job


def _touch(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"x")
    return path


def _cleanup_now(manager: DownloadManager, job: DownloadJob) -> None:
    """Run the cancel cleanup for `job` on this thread (what _start_cleanup does in the background)."""
    task = manager._prepare_cleanup(job)
    assert task is not None
    manager._cleanup_worker(task)
    assert manager._cleanups == []


def _finish_file(manager: DownloadManager, job: DownloadJob, printed_path: str) -> None:
    """Feed yt-dlp's after-move line for a finished file, as the real output parser sees it."""
    manager._handle_line(job, f"[FILE]{printed_path}\n".encode())


def _names(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()}


# -- cleanup of a single cancelled download -----------------------------------------------


def test_cleanup_removes_partials_and_new_files_but_keeps_old_ones(manager: DownloadManager, tmp_path: Path) -> None:
    _touch(tmp_path, f"{STEM}.jpg")  # was there before the download started
    _touch(tmp_path, "Unrelated.mp4")
    job = _put(manager, "https://example.com/v", str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(job, str(tmp_path / f"{STEM}.mp4"))
    for name in (
        f"{STEM}.f137.mp4.part",
        f"{STEM}.f140.m4a.ytdl",
        f"{STEM}.f137.mp4.part-Frag12",
        f"{STEM}.temp.mp4",
        f"{STEM}.f140.m4a",  # finished stream, never merged
    ):
        _touch(tmp_path, name)

    _cleanup_now(manager, job)

    assert _names(tmp_path) == {f"{STEM}.jpg", "Unrelated.mp4"}
    assert any("Removed 5 unfinished file(s)." in line for line in job.log)
    assert not any("Kept files shared" in line for line in job.log)


def test_cleanup_keeps_items_that_finished(manager: DownloadManager, tmp_path: Path) -> None:
    job = _put(manager, "https://example.com/list", str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(job, str(tmp_path / "First [a1].mp4"))
    first = _touch(tmp_path, "First [a1].mp4")
    _touch(tmp_path, "First [a1].en.vtt")
    job.files.append(str(first))
    manager._remember_destination(job, str(tmp_path / "Second [b2].mp4"))
    _touch(tmp_path, "Second [b2].f137.mp4.part")

    _cleanup_now(manager, job)

    assert _names(tmp_path) == {"First [a1].mp4", "First [a1].en.vtt"}


# -- two downloads sharing a file name ---------------------------------------------------


def test_cancel_keeps_running_download_with_same_stem(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    audio = _put(manager, url, str(tmp_path), JobStatus.DOWNLOADING, audio=True)
    audio.title = "Some Video"
    audio.process = QProcess()  # still running
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    manager._remember_destination(audio, str(tmp_path / f"{STEM}.mp3"))
    _touch(tmp_path, f"{STEM}.f251.webm.part")  # could be either download's
    _touch(tmp_path, f"{STEM}.mp3")  # the audio download's output, being written

    try:
        _cleanup_now(manager, video)
    finally:
        audio.process = None

    assert _names(tmp_path) == {f"{STEM}.f251.webm.part", f"{STEM}.mp3"}
    assert any(f"Kept files shared with download #{audio.id}" in line for line in video.log)
    assert any("Some Video" in line for line in video.log if "Kept files" in line)


def test_cancel_keeps_finished_download_with_same_stem(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    # The MP3 download of the same link finishes after the video started.
    audio = _put(manager, url, str(tmp_path), JobStatus.DONE, audio=True)
    manager._remember_destination(audio, str(tmp_path / f"{STEM}.mp3"))
    audio.files.append(str(_touch(tmp_path, f"{STEM}.mp3")))
    _touch(tmp_path, f"{STEM}.webp")  # side file written by the audio download
    _touch(tmp_path, f"{STEM}.f137.mp4.part")  # the cancelled video's own partial file

    _cleanup_now(manager, video)

    assert _names(tmp_path) == {f"{STEM}.mp3", f"{STEM}.webp"}
    assert any(f"Kept files shared with download #{audio.id}" in line for line in video.log)


def test_cancel_keeps_partials_of_failed_download(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    first = _put(manager, url, str(tmp_path), JobStatus.ERROR)
    manager._remember_destination(first, str(tmp_path / f"{STEM}.mp4"))
    second = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(second, str(tmp_path / f"{STEM}.mp4"))
    _touch(tmp_path, f"{STEM}.f137.mp4.part")  # Retry on the failed one can resume from it

    _cleanup_now(manager, second)

    assert _names(tmp_path) == {f"{STEM}.f137.mp4.part"}


def test_cancel_ignores_other_cancelled_download_but_keeps_its_finished_files(
    manager: DownloadManager, tmp_path: Path
) -> None:
    url = "https://example.com/v"
    earlier = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(earlier, str(tmp_path / f"{STEM}.mp3"))
    later = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(later, str(tmp_path / f"{STEM}.mp4"))
    earlier.files.append(str(_touch(tmp_path, f"{STEM}.mp3")))
    _touch(tmp_path, f"{STEM}.f137.mp4.part")

    _cleanup_now(manager, later)

    assert _names(tmp_path) == {f"{STEM}.mp3"}


def test_other_download_in_another_folder_does_not_protect(manager: DownloadManager, tmp_path: Path) -> None:
    here = tmp_path / "here"
    elsewhere = tmp_path / "elsewhere"
    here.mkdir()
    elsewhere.mkdir()
    url = "https://example.com/v"
    other = _put(manager, url, str(elsewhere), JobStatus.DOWNLOADING)
    other.process = QProcess()
    manager._remember_destination(other, str(elsewhere / f"{STEM}.mp4"))
    job = _put(manager, url, str(here), JobStatus.CANCELLED)
    manager._remember_destination(job, str(here / f"{STEM}.mp4"))
    _touch(here, f"{STEM}.f137.mp4.part")
    _touch(elsewhere, f"{STEM}.f137.mp4.part")

    try:
        _cleanup_now(manager, job)
    finally:
        other.process = None

    assert _names(here) == set()
    assert _names(elsewhere) == {f"{STEM}.f137.mp4.part"}
    assert not any("Kept files shared" in line for line in job.log)


def test_start_cleanup_runs_in_background(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    audio = _put(manager, url, str(tmp_path), JobStatus.DONE, audio=True)
    audio.files.append(str(_touch(tmp_path, f"{STEM}.mp3")))
    _touch(tmp_path, f"{STEM}.f137.mp4.part")
    stopped: list[bool] = []
    manager.stopped.connect(lambda: stopped.append(True))

    manager._start_cleanup(video)
    deadline = time.monotonic() + 10
    while manager._pending_cleanups and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    assert manager._pending_cleanups == 0
    assert manager._cleanups == []
    assert stopped
    assert _names(tmp_path) == {f"{STEM}.mp3"}


# -- files of downloads that left the list, or were retried --------------------------------


def _video_running_then_cancelled(manager: DownloadManager, url: str, folder: Path) -> DownloadJob:
    video = _put(manager, url, str(folder), JobStatus.DOWNLOADING)
    video.process = QProcess()  # "Clear finished" must not remove it yet
    manager._remember_destination(video, str(folder / f"{STEM}.mp4"))
    return video


def _mark_cancelled(video: DownloadJob) -> None:
    video.process = None
    video.status = JobStatus.CANCELLED


def test_cancel_keeps_file_of_download_cleared_from_list(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _video_running_then_cancelled(manager, url, tmp_path)
    # The MP3 of the same link finishes after the video announced its file name...
    audio = _put(manager, url, str(tmp_path), JobStatus.DOWNLOADING, audio=True)
    manager._remember_destination(audio, str(tmp_path / f"{STEM}.mp3"))
    _finish_file(manager, audio, str(_touch(tmp_path, f"{STEM}.mp3")))
    audio.status = JobStatus.DONE
    # ...the user clicks "Clear finished", then cancels the video.
    manager.clear_finished()
    assert manager.get(audio.id) is None and manager.get(video.id) is video
    _mark_cancelled(video)
    _touch(tmp_path, f"{STEM}.f137.mp4.part")

    _cleanup_now(manager, video)

    assert _names(tmp_path) == {f"{STEM}.mp3"}
    assert any(f"Kept files shared with download #{audio.id}." in line for line in video.log)


def test_cancel_keeps_file_of_download_retried_since(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _video_running_then_cancelled(manager, url, tmp_path)
    audio = _put(manager, url, str(tmp_path), JobStatus.DOWNLOADING, audio=True)
    manager._remember_destination(audio, str(tmp_path / f"{STEM}.mp3"))
    _finish_file(manager, audio, str(_touch(tmp_path, f"{STEM}.mp3")))
    audio.status = JobStatus.DONE
    _mark_cancelled(video)
    manager.retry(audio.id, audio.options)  # forgets its files (and fails at once: no yt-dlp here)
    assert audio.files == [] and audio._destinations == []

    _cleanup_now(manager, video)

    assert _names(tmp_path) == {f"{STEM}.mp3"}


def test_relative_file_paths_are_taken_from_the_save_folder(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    manager._remember_destination(video, "Other [x9].mp4")  # relative [DEST] too
    _touch(tmp_path, "Other [x9].mp4")
    _finish_file(manager, video, "Other [x9].mp4")  # this item of the cancelled list finished
    audio = _put(manager, url, str(tmp_path), JobStatus.DONE, audio=True)
    _touch(tmp_path, f"{STEM}.mp3")
    _finish_file(manager, audio, f"{STEM}.mp3")  # relative: must not be looked up in the working folder

    task = manager._prepare_cleanup(video)
    assert task is not None
    assert task.finals == [_norm_path(str(tmp_path / "Other [x9].mp4"))]
    assert _norm_path(str(tmp_path / f"{STEM}.mp3")) in task.protected_paths
    manager._cleanup_worker(task)

    assert _names(tmp_path) == {f"{STEM}.mp3", "Other [x9].mp4"}


# -- what a finished download protects ----------------------------------------------------


def test_finished_download_does_not_protect_cancelled_leftovers(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    audio = _put(manager, url, str(tmp_path), JobStatus.DONE, audio=True)
    manager._remember_destination(audio, str(tmp_path / f"{STEM}.mp3"))
    _finish_file(manager, audio, str(_touch(tmp_path, f"{STEM}.mp3")))
    _touch(tmp_path, f"{STEM}.en.vtt")  # side file of the finished download: kept
    for leftover in (f"{STEM}.f137.mp4", f"{STEM}.f140.m4a", f"{STEM}.mp4", f"{STEM}.webm"):
        _touch(tmp_path, leftover)  # big media files of the cancelled video

    _cleanup_now(manager, video)

    assert _names(tmp_path) == {f"{STEM}.mp3", f"{STEM}.en.vtt"}


def test_finished_download_with_keep_flag_protects_all_its_files(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    audio = _put(manager, url, str(tmp_path), JobStatus.DONE, audio=True, extra_args=["-k"])
    manager._remember_destination(audio, str(tmp_path / f"{STEM}.mp3"))
    _finish_file(manager, audio, str(_touch(tmp_path, f"{STEM}.mp3")))
    _touch(tmp_path, f"{STEM}.webm")  # the original audio, kept because of -k
    _touch(tmp_path, f"{STEM}.f137.mp4.part")
    manager.remove(audio.id)  # the protection outlives the job

    _cleanup_now(manager, video)

    assert _names(tmp_path) == {f"{STEM}.mp3", f"{STEM}.webm"}


# -- downloads that start while a cleanup runs --------------------------------------------


def test_cleanup_keeps_files_announced_after_it_started(manager: DownloadManager, tmp_path: Path) -> None:
    url = "https://example.com/v"
    video = _put(manager, url, str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    task = manager._prepare_cleanup(video)
    assert task is not None
    # A waiting duplicate starts (or the cancelled one is retried) before the worker gets there.
    other = _put(manager, url, str(tmp_path), JobStatus.DOWNLOADING, audio=True)
    manager._remember_destination(other, str(tmp_path / f"{STEM}.mp3"))
    _touch(tmp_path, f"{STEM}.f251.webm.part")

    manager._cleanup_worker(task)

    assert _names(tmp_path) == {f"{STEM}.f251.webm.part"}
    assert any(f"Kept files shared with download #{other.id}" in line for line in video.log)
    assert manager._cleanups == []


def test_cleanup_keeps_files_of_its_own_retry(manager: DownloadManager, tmp_path: Path) -> None:
    video = _put(manager, "https://example.com/v", str(tmp_path), JobStatus.CANCELLED)
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    task = manager._prepare_cleanup(video)
    assert task is not None
    video.reset()
    video.status = JobStatus.DOWNLOADING
    manager._remember_destination(video, str(tmp_path / f"{STEM}.mp4"))
    _touch(tmp_path, f"{STEM}.f137.mp4.part")  # written by the new run

    manager._cleanup_worker(task)

    assert _names(tmp_path) == {f"{STEM}.f137.mp4.part"}
    assert not any("Kept files shared" in line for line in video.log)


# -- duplicate detection -----------------------------------------------------------------


def test_find_active_matches_waiting_and_running(manager: DownloadManager, tmp_path: Path) -> None:
    folder = str(tmp_path)
    waiting = _put(manager, "https://example.com/watch?v=abc", folder)
    assert manager.find_active("  https://example.com/watch?v=abc/ \n", folder) == waiting.id
    assert manager.find_active("https://example.com/watch?v=abc", folder + "/") == waiting.id

    waiting.status = JobStatus.DOWNLOADING
    waiting.process = QProcess()
    try:
        assert manager.find_active("https://example.com/watch?v=abc", folder) == waiting.id
        waiting.cancel_requested = True  # being cancelled: adding it again is fine
        assert manager.find_active("https://example.com/watch?v=abc", folder) is None
    finally:
        waiting.process = None


def test_find_active_ignores_finished_other_links_and_other_folders(
    manager: DownloadManager, tmp_path: Path
) -> None:
    folder = str(tmp_path)
    for status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED):
        _put(manager, "https://example.com/a", folder, status)
    _put(manager, "https://example.com/b", folder)
    _put(manager, "https://example.com/a", str(tmp_path / "other"))

    assert manager.find_active("https://example.com/a", folder) is None
    assert manager.find_active("https://example.com/c", folder) is None


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows paths ignore case")
def test_find_active_folder_case_insensitive_on_windows(manager: DownloadManager, tmp_path: Path) -> None:
    job = _put(manager, "https://example.com/a", str(tmp_path))
    assert manager.find_active("https://example.com/a", str(tmp_path).upper()) == job.id


def test_same_link_with_another_preset_is_not_a_duplicate(manager: DownloadManager, tmp_path: Path) -> None:
    folder = str(tmp_path)
    video = _put(manager, "https://example.com/a", folder)
    assert manager.find_active("https://example.com/a", folder, _preset()) == video.id
    assert manager.find_active("https://example.com/a", folder, _preset(audio=True)) is None


def test_same_link_into_the_same_folder_takes_turns(manager: DownloadManager, tmp_path: Path) -> None:
    folder = str(tmp_path)
    video = _put(manager, "https://example.com/a", folder, JobStatus.DOWNLOADING)
    audio = _put(manager, "https://example.com/a", folder, audio=True)
    elsewhere = _put(manager, "https://example.com/a", str(tmp_path / "other"), audio=True)
    other = _put(manager, "https://example.com/b", folder)
    video.process = QProcess()
    try:
        assert manager._target_busy(audio)
        assert not manager._target_busy(video)
        assert not manager._target_busy(elsewhere)
        manager.set_max_parallel(3)  # schedules: yt-dlp is missing, so started jobs fail at once
        assert audio.status == JobStatus.WAITING, "waits for the MP4 of the same video"
        assert elsewhere.status == JobStatus.ERROR
        assert other.status == JobStatus.ERROR
    finally:
        video.process = None
    assert not manager._target_busy(audio)


# -- cookies ------------------------------------------------------------------------------


def _args_for(cookies: str, tmp_path: Path) -> list[str]:
    job = DownloadJob(id=1, url="https://example.com/v", preset=_preset(), options=_options(str(tmp_path), cookies))
    return build_arguments(job, FakeDeps())  # type: ignore[arg-type]


def test_cookies_file_argument(tmp_path: Path) -> None:
    cookies = str(tmp_path / "my cookies.txt")
    args = _args_for(f"file:{cookies}", tmp_path)
    assert args[args.index("--cookies") + 1] == cookies
    assert "--cookies-from-browser" not in args
    assert args[-2:] == ["--", "https://example.com/v"]


def test_cookies_from_browser_argument(tmp_path: Path) -> None:
    args = _args_for("firefox", tmp_path)
    assert args[args.index("--cookies-from-browser") + 1] == "firefox"
    assert "--cookies" not in args


@pytest.mark.parametrize("value", ["", "file:", "file:   "])
def test_no_cookies_argument(value: str, tmp_path: Path) -> None:
    args = _args_for(value, tmp_path)
    assert "--cookies" not in args
    assert "--cookies-from-browser" not in args


def test_cookie_source_for_errors() -> None:
    assert _cookie_source_for_errors("file:C:/Users/me/cookies.txt") == "C:/Users/me/cookies.txt"
    assert _cookie_source_for_errors("firefox") == "firefox"
    assert _cookie_source_for_errors("") == ""


def test_sign_in_hint_for_cookies_file_does_not_talk_about_browsers(
    manager: DownloadManager, tmp_path: Path
) -> None:
    job = _put(manager, "https://example.com/v", str(tmp_path), JobStatus.DOWNLOADING)
    job.options.cookies_browser = f"file:{tmp_path / 'Cookies.txt'}"
    job.process = QProcess()  # never started: nothing left to read
    job.log.append("ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies for the authentication.")

    manager._on_finished(job.id, 1, QProcess.ExitStatus.NormalExit)

    assert job.status == JobStatus.ERROR
    assert job.error is not None
    assert "file:" not in job.error.hint.lower()
    assert "Firefox" not in job.error.hint
