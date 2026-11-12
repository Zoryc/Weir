"""Tests for the download queue: cancel cleanup, duplicate detection and cookie arguments."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QProcess

from core import downloader
from core.deps import ToolInfo
from core.downloader import (
    DownloadJob,
    DownloadManager,
    DownloadOptions,
    JobStatus,
    _cookie_source_for_errors,
    _link_key,
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
    playlist: bool = False,
) -> DownloadJob:
    """Add a job without starting yt-dlp."""
    options = _options(save_dir)
    options.extra_args = list(extra_args or [])
    options.playlist = playlist
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


# -- link keys ----------------------------------------------------------------------------

VIDEO_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "link",
    [
        f"https://youtu.be/{VIDEO_ID}?si=BBB",
        f"youtu.be/{VIDEO_ID}?t=42",
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"HTTPS://WWW.YouTube.com/watch?feature=share&v={VIDEO_ID}&t=1m2s#comments",
        f"http://m.youtube.com/watch?v={VIDEO_ID}&pp=xyz&index=3",
        f"https://music.youtube.com/watch?v={VIDEO_ID}&si=x",
        f"https://youtube.com/shorts/{VIDEO_ID}?feature=share",
        f"https://www.youtube.com/live/{VIDEO_ID}/",
        f"https://www.youtube.com/embed/{VIDEO_ID}?start=30",
        f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
    ],
)
def test_link_key_same_youtube_video(link: str) -> None:
    assert _link_key(link) == _link_key(f"https://youtu.be/{VIDEO_ID}?si=AAA")


def test_link_key_keeps_youtube_playlist() -> None:
    single = _link_key(f"https://www.youtube.com/watch?v={VIDEO_ID}")
    in_list = _link_key(f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL123&index=2&si=x")
    assert in_list != single  # with "Download whole playlist" on, this is the whole playlist
    assert in_list == _link_key(f"https://youtu.be/{VIDEO_ID}?list=PL123&si=y")
    assert in_list != _link_key(f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL456")
    # Turn-taking ignores list=: both links write this video's files.
    assert _link_key(f"https://youtu.be/{VIDEO_ID}?list=PL123", keep_list=False) == single
    assert _link_key("https://www.youtube.com/playlist?list=PL123&si=x") == _link_key(
        "https://youtube.com/playlist?list=PL123"
    )
    assert _link_key("https://www.youtube.com/playlist?list=PL123") != _link_key(
        "https://www.youtube.com/playlist?list=PL456"
    )
    # A playlist player, not a video called "videoseries".
    assert _link_key("https://www.youtube.com/embed/videoseries?list=PL123", keep_list=False) != _link_key(
        "https://www.youtube.com/embed/videoseries?list=PL456", keep_list=False
    )


def test_link_key_different_youtube_videos_stay_different() -> None:
    assert _link_key("https://youtu.be/aaaaaaaaaaa") != _link_key("https://youtu.be/bbbbbbbbbbb")
    assert _link_key("https://youtu.be/aaaaaaaaaaa") != _link_key("https://youtu.be/AAAAAAAAAAA")
    assert _link_key("https://www.youtube.com/watch?v=aaaaaaaaaaa") != _link_key(
        "https://www.youtube.com/shorts/bbbbbbbbbbb"
    )


def test_link_key_other_sites() -> None:
    base = _link_key("https://vimeo.com/123456")
    assert base == _link_key("https://www.Vimeo.com/123456/?utm_source=x&fbclid=y#t=10")
    assert base == _link_key("vimeo.com/123456")
    assert base == _link_key("https://vimeo.com/123456#t=1m20s")  # only a start time
    assert base != _link_key("https://vimeo.com/654321")
    # Real query parameters pick the video on many sites: they must keep links apart.
    assert _link_key("https://example.com/watch?id=1") != _link_key("https://example.com/watch?id=2")
    assert _link_key("https://example.com/play?id=1&page=2") == _link_key(
        "https://example.com/play?page=2&gclid=z&id=1"
    )
    assert _link_key("https://example.com/Video") != _link_key("https://example.com/video")
    assert _link_key("https://example.com/a") != _link_key("https://example.org/a")


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://music.163.com/#/song?id=550136151", "https://music.163.com/#/song?id=2755669231"),
        ("https://music.163.com/#/song?id=550136151", "https://music.163.com/#/album?id=220780"),
        ("https://radiko.jp/#!/live/QRR", "https://radiko.jp/#!/live/TBS"),
        ("https://www.nicovideo.jp/mylist/#/27411728", "https://www.nicovideo.jp/mylist/#/12345678"),
        ("https://www.cbsnews.com/embed/video/#abc123", "https://www.cbsnews.com/embed/video/#def456"),
    ],
)
def test_link_key_keeps_fragments_that_pick_the_video(first: str, second: str) -> None:
    # Hash-routed sites put the song or station after "#"; only a #t= start time is dropped.
    assert _link_key(first) != _link_key(second)
    assert _link_key(first, keep_list=False) != _link_key(second, keep_list=False)
    assert _link_key(first) == _link_key(first.replace("https://www.", "http://").rstrip("/") + "/")


def test_hash_routed_links_are_not_duplicates(manager: DownloadManager, tmp_path: Path) -> None:
    folder = str(tmp_path)
    song = _put(manager, "https://music.163.com/#/song?id=550136151", folder)
    assert manager.find_active("https://music.163.com/#/song?id=2755669231", folder, _preset()) is None
    assert manager.find_active("https://music.163.com/#/song?id=550136151", folder, _preset()) == song.id
    qrr = _put(manager, "https://radiko.jp/#!/live/QRR", folder, JobStatus.DOWNLOADING)
    tbs = _put(manager, "https://radiko.jp/#!/live/TBS", folder, audio=True)
    qrr.process = QProcess()
    try:
        assert not manager._target_busy(tbs), "another station must not wait for a live stream"
    finally:
        qrr.process = None


def test_youtube_live_stream_embeds_of_different_channels_differ(
    manager: DownloadManager, tmp_path: Path
) -> None:
    # "live_stream" is 11 characters like a video id, but these links pick a channel.
    first = "https://www.youtube.com/embed/live_stream?channel=UC2_KI6RB__jGdlnK6dvFEZA"
    second = "https://www.youtube.com/embed/live_stream?channel=UCSJ4gkVC6NrvII8umztf0Ow"
    assert _link_key(first) != _link_key(second)
    assert _link_key(first, keep_list=False) != _link_key(second, keep_list=False)
    assert _link_key(first) == _link_key(first.replace("www.", "") + "&si=x")
    folder = str(tmp_path)
    running = _put(manager, first, folder, JobStatus.DOWNLOADING)
    audio = _put(manager, second, folder, audio=True)
    running.process = QProcess()
    try:
        assert manager.find_active(first, folder, _preset()) == running.id
        assert manager.find_active(second, folder, _preset()) is None
        assert not manager._target_busy(audio), "must not wait behind another channel's stream"
    finally:
        running.process = None


def test_find_active_works_out_each_link_key_once(
    manager: DownloadManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pasting n links checks each against every queued job; parsing all their links on each
    # check took n²/2 parses (seconds for a thousand links, on the UI thread).
    calls = 0
    real_parse_link = downloader._parse_link

    def counting_parse_link(url: str) -> downloader._Link:
        nonlocal calls
        calls += 1
        return real_parse_link(url)

    monkeypatch.setattr(downloader, "_parse_link", counting_parse_link)
    folder = str(tmp_path)
    links = [f"https://youtu.be/{index:011d}?si=share{index}" for index in range(300)]
    for link in links:  # what the window does for each pasted link
        assert manager.find_active(link, folder, _preset()) is None
        _put(manager, link, folder)
    assert calls <= 2 * len(links)
    calls = 0
    for job in list(manager._jobs.values()):  # retrying them all
        assert manager.find_active(job.url, folder, job.preset) == job.id
    assert calls <= 2 * len(links)
    calls = 0
    running = next(iter(manager._jobs.values()))
    running.process = QProcess()
    try:
        for job in manager._jobs.values():  # what _schedule asks for each waiting job
            manager._target_busy(job)
    finally:
        running.process = None
    assert calls == 0, "taking turns reuses the parsed links"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a link",
        VIDEO_ID,
        "ytsearch:cats",
        "http://[::1",
        "https://ex＃ample.com/video",  # urlsplit refuses this host
        "https://example.com:99999/x",
        "https://例え.jp/動画?v=ü#ä",
        "https://youtu.be/",
        "https://www.youtube.com/watch?v=",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/shorts/",
        "\x00\n\t",
        "://",
        "?&=&",
    ],
)
def test_link_key_never_raises(text: str) -> None:
    assert isinstance(_link_key(text), str)
    assert isinstance(_link_key(text, keep_list=False), str)


def test_link_key_bare_text_is_compared_as_typed() -> None:
    assert _link_key(VIDEO_ID) != _link_key(VIDEO_ID.lower())  # YouTube ids are case-sensitive
    assert _link_key("ytsearch:Cats") != _link_key("ytsearch:dogs")


def test_find_active_matches_another_share_link_of_the_same_video(
    manager: DownloadManager, tmp_path: Path
) -> None:
    folder = str(tmp_path)
    first = _put(manager, f"https://youtu.be/{VIDEO_ID}?si=AAA", folder)
    assert manager.find_active(f"https://youtu.be/{VIDEO_ID}?si=BBB", folder, _preset()) == first.id
    assert manager.find_active(f"https://www.youtube.com/watch?v={VIDEO_ID}", folder) == first.id
    # Playlist off, a link into a playlist fetches only its video; on, the whole playlist.
    in_list = f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL1"
    assert manager.find_active(in_list, folder) == first.id
    assert manager.find_active(in_list, folder, playlist=True) is None
    assert manager.find_active("https://youtu.be/aaaaaaaaaaa?si=AAA", folder) is None


def test_another_share_link_of_the_same_video_takes_turns(manager: DownloadManager, tmp_path: Path) -> None:
    folder = str(tmp_path)
    running = _put(manager, f"https://youtu.be/{VIDEO_ID}?si=AAA", folder, JobStatus.DOWNLOADING)
    same = _put(manager, f"https://youtu.be/{VIDEO_ID}?si=BBB", folder)
    shorts = _put(manager, f"https://www.youtube.com/shorts/{VIDEO_ID}", folder, audio=True)
    in_list = _put(manager, f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL1", folder)
    other = _put(manager, "https://youtu.be/aaaaaaaaaaa?si=AAA", folder)
    running.process = QProcess()
    try:
        assert manager._target_busy(same)
        assert manager._target_busy(shorts)
        assert manager._target_busy(in_list)
        assert not manager._target_busy(other)
        manager.set_max_parallel(3)  # schedules: yt-dlp is missing, so started jobs fail at once
        assert same.status == JobStatus.WAITING, "waits for the other copy of the same video"
        assert shorts.status == JobStatus.WAITING
        assert in_list.status == JobStatus.WAITING
        assert other.status == JobStatus.ERROR
    finally:
        running.process = None


# -- playlists ----------------------------------------------------------------------------

PLAYLIST = "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
IN_PLAYLIST = f"https://www.youtube.com/watch?v=AAAAAAAAAAA&list={PLAYLIST}"


@pytest.mark.parametrize(
    "again",
    [
        IN_PLAYLIST,
        IN_PLAYLIST + "&si=share2",
        f"https://youtu.be/AAAAAAAAAAA?list={PLAYLIST}&si=share3",
    ],
)
def test_whole_playlist_is_not_a_duplicate_of_its_video_queued_alone(
    manager: DownloadManager, tmp_path: Path, again: str
) -> None:
    folder = str(tmp_path)
    alone = _put(manager, IN_PLAYLIST, folder)  # “Download whole playlist” off: video A only
    assert manager.find_active(again, folder, _preset()) == alone.id
    assert manager.find_active(again, folder, _preset(), playlist=True) is None, "must be queued"
    whole = _put(manager, again, folder, playlist=True)
    assert manager.find_active(IN_PLAYLIST, folder, _preset(), playlist=True) == whole.id
    assert manager.find_active(IN_PLAYLIST, folder, _preset()) == alone.id


def test_what_a_job_fetches_follows_its_playlist_mode(manager: DownloadManager, tmp_path: Path) -> None:
    folder = str(tmp_path)
    job = _put(manager, IN_PLAYLIST, folder)
    assert manager.find_active(IN_PLAYLIST, folder) == job.id
    job.options.playlist = True  # a retry may bring new options (DownloadManager.retry)
    assert manager.find_active(IN_PLAYLIST, folder, playlist=True) == job.id
    assert manager.find_active(IN_PLAYLIST, folder) is None


@pytest.mark.parametrize(
    "same_playlist",
    [
        f"https://www.youtube.com/watch?v=BBBBBBBBBBB&list={PLAYLIST}&index=2",
        f"https://youtu.be/BBBBBBBBBBB?list={PLAYLIST}",
        f"https://www.youtube.com/playlist?list={PLAYLIST}",
        f"https://m.youtube.com/playlist?list={PLAYLIST}&si=x",
        f"https://www.youtube.com/watch?list={PLAYLIST}",
        f"https://www.youtube.com/embed/videoseries?list={PLAYLIST}",
    ],
)
def test_links_that_fetch_the_same_playlist_are_duplicates(
    manager: DownloadManager, tmp_path: Path, same_playlist: str
) -> None:
    folder = str(tmp_path)
    whole = _put(manager, IN_PLAYLIST + "&index=1", folder, playlist=True)
    assert manager.find_active(same_playlist, folder, _preset(), playlist=True) == whole.id


def test_links_into_a_playlist_differ_when_they_fetch_something_else(
    manager: DownloadManager, tmp_path: Path
) -> None:
    folder = str(tmp_path)
    page = f"https://www.youtube.com/playlist?list={PLAYLIST}"
    whole = _put(manager, page, folder, playlist=True)
    # A playlist page fetches the whole playlist in either mode.
    assert manager.find_active(page, folder) == whole.id
    # Playlist off: only video B, though the playlist has it too (they take turns instead).
    assert manager.find_active(f"https://youtu.be/BBBBBBBBBBB?list={PLAYLIST}", folder) is None
    assert manager.find_active("https://youtu.be/BBBBBBBBBBB?list=PLother", folder, playlist=True) is None
    # A channel's list= picks no playlist: it stays part of that link.
    assert manager.find_active(f"https://www.youtube.com/@someone?list={PLAYLIST}", folder,
                               playlist=True) is None
    # Other sites: the playlist mode may change what a link fetches, so only the same mode matches.
    show = _put(manager, "https://example.com/show/1", folder)
    assert manager.find_active("https://example.com/show/1/", folder) == show.id
    assert manager.find_active("https://example.com/show/1", folder, playlist=True) is None


def test_downloads_of_one_playlist_take_turns(manager: DownloadManager, tmp_path: Path) -> None:
    folder = str(tmp_path)
    running = _put(manager, IN_PLAYLIST + "&index=1", folder, JobStatus.DOWNLOADING, playlist=True)
    entry = _put(manager, f"https://www.youtube.com/watch?v=BBBBBBBBBBB&list={PLAYLIST}&index=2",
                 folder, playlist=True)
    page = _put(manager, f"https://www.youtube.com/playlist?list={PLAYLIST}", folder, audio=True)
    one_of_its_videos = _put(manager, f"https://youtu.be/CCCCCCCCCCC?list={PLAYLIST}", folder)
    other_playlist = _put(manager, "https://www.youtube.com/watch?v=DDDDDDDDDDD&list=PLother",
                          folder, playlist=True)
    elsewhere = _put(manager, f"https://www.youtube.com/playlist?list={PLAYLIST}",
                     str(tmp_path / "other"), playlist=True)
    running.process = QProcess()
    try:
        assert manager._target_busy(entry)
        assert manager._target_busy(page)
        assert manager._target_busy(one_of_its_videos)
        assert not manager._target_busy(other_playlist)
        assert not manager._target_busy(elsewhere)
        manager.set_max_parallel(3)  # schedules: yt-dlp is missing, so started jobs fail at once
        assert entry.status == JobStatus.WAITING, "both would write the playlist's files at once"
        assert page.status == JobStatus.WAITING
        assert one_of_its_videos.status == JobStatus.WAITING
        assert other_playlist.status == JobStatus.ERROR
        assert elsewhere.status == JobStatus.ERROR
    finally:
        running.process = None


def test_a_playlist_waits_for_one_of_its_videos(manager: DownloadManager, tmp_path: Path) -> None:
    folder = str(tmp_path)
    running = _put(manager, f"https://youtu.be/CCCCCCCCCCC?list={PLAYLIST}", folder,
                   JobStatus.DOWNLOADING)  # playlist off: video C only
    page = _put(manager, f"https://www.youtube.com/playlist?list={PLAYLIST}", folder)
    whole = _put(manager, IN_PLAYLIST, folder, playlist=True)
    next_video = _put(manager, IN_PLAYLIST, folder, audio=True)  # playlist off: video A only
    running.process = QProcess()
    try:
        assert manager._target_busy(page)
        assert manager._target_busy(whole)
        assert not manager._target_busy(next_video), "videos of one playlist download side by side"
        manager.set_max_parallel(3)
        assert page.status == JobStatus.WAITING
        assert whole.status == JobStatus.WAITING
        assert next_video.status == JobStatus.ERROR
    finally:
        running.process = None


@pytest.mark.parametrize("kind", ["shorts", "live", "embed"])
def test_other_video_links_with_a_list_fetch_the_whole_list(
    manager: DownloadManager, tmp_path: Path, kind: str
) -> None:
    # yt-dlp sends these to its playlist extractor, which ignores --no-playlist: even with
    # the playlist off they fetch the whole list, not the video alone.
    folder = str(tmp_path)
    link = f"https://www.youtube.com/{kind}/AAAAAAAAAAA?list={PLAYLIST}"
    alone = _put(manager, "https://www.youtube.com/watch?v=AAAAAAAAAAA", folder)
    assert manager.find_active(link, folder, _preset()) is None, "must be queued"
    _put(manager, f"https://www.youtube.com/{kind}/AAAAAAAAAAA?list=PLother", folder)
    assert manager.find_active(link, folder, _preset()) is None, "another playlist"
    whole = _put(manager, f"https://www.youtube.com/playlist?list={PLAYLIST}", folder)
    assert manager.find_active(link, folder, _preset()) == whole.id
    assert manager.find_active(link, folder, _preset(), playlist=True) == whole.id
    # An empty list= names no playlist: the video alone.
    no_list = f"https://www.youtube.com/{kind}/AAAAAAAAAAA?list="
    assert manager.find_active(no_list, folder) == alone.id


def test_embedded_players_of_one_playlist_take_turns(
    manager: DownloadManager, tmp_path: Path
) -> None:
    folder = str(tmp_path)
    # The playlist is off for all of them: /embed/, /shorts/ and /live/ still fetch the whole list.
    running = _put(manager, f"https://www.youtube.com/embed/AAAAAAAAAAA?list={PLAYLIST}", folder,
                   JobStatus.DOWNLOADING)
    other_video = _put(manager, f"https://www.youtube.com/embed/BBBBBBBBBBB?list={PLAYLIST}",
                       folder)
    shorts = _put(manager, f"https://www.youtube.com/shorts/CCCCCCCCCCC?list={PLAYLIST}", folder,
                  audio=True)
    one_of_its_videos = _put(manager, f"https://youtu.be/DDDDDDDDDDD?list={PLAYLIST}", folder)
    other_playlist = _put(manager, "https://www.youtube.com/live/EEEEEEEEEEE?list=PLother", folder)
    running.process = QProcess()
    try:
        assert manager._target_busy(other_video)
        assert manager._target_busy(shorts)
        assert manager._target_busy(one_of_its_videos)
        assert not manager._target_busy(other_playlist)
        manager.set_max_parallel(4)  # schedules: yt-dlp is missing, so started jobs fail at once
        assert other_video.status == JobStatus.WAITING, "both would write the playlist's files"
        assert shorts.status == JobStatus.WAITING
        assert one_of_its_videos.status == JobStatus.WAITING
        assert other_playlist.status == JobStatus.ERROR
    finally:
        running.process = None


def test_same_link_tells_a_repeat_from_another_link_to_its_playlist(
    manager: DownloadManager, tmp_path: Path
) -> None:
    job = _put(manager, IN_PLAYLIST + "&index=1", str(tmp_path), playlist=True)
    assert job.same_link(IN_PLAYLIST + "&si=share")
    assert job.same_link(f"https://youtu.be/AAAAAAAAAAA?list={PLAYLIST}")
    assert not job.same_link(f"https://www.youtube.com/watch?v=BBBBBBBBBBB&list={PLAYLIST}")
    assert not job.same_link(f"https://www.youtube.com/playlist?list={PLAYLIST}")
    page = _put(manager, f"https://www.youtube.com/playlist?list={PLAYLIST}", str(tmp_path))
    assert page.same_link(f"https://m.youtube.com/playlist?list={PLAYLIST}&si=x")


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
