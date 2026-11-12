"""Download queue: runs the yt-dlp binary through QProcess and parses its output."""

from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, Signal

from .deps import DepsManager
from .errors import ATTEMPT_PREFIX, FriendlyError, explain_error
from .presets import Preset, is_audio_only
from .process import configure_process, kill_process_tree, qprocess_environment

SUB_LANGS = "en.*,fr.*"
PROGRESS_TEMPLATE = (
    "download:[PROG]%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s"
)
POSTPROCESS_TEMPLATE = "postprocess:[PP]%(progress.postprocessor)s|%(progress.status)s"
# Printed for each video before anything is written: playlist position and
# the target file name (used to clean up partial files after Cancel).
DEST_PRINT = "video:[DEST]%(playlist_autonumber|)s|%(n_entries|)s|%(filename)s"
TITLE_PRINT = "before_dl:[TITLE]%(title)s"
FILE_PRINT = "after_move:[FILE]%(filepath)s"

CONVERT_PREFIXES: tuple[str, ...] = (
    "[Merger]",
    "[ExtractAudio]",
    "[EmbedThumbnail]",
    "[EmbedSubtitle]",
    "[Fixup",
    "[VideoRemuxer]",
    "[VideoConvertor]",
    "[Metadata]",
)
# Post-processors that only move files around; they don't mean "Converting".
TRIVIAL_POSTPROCESSORS = {"MoveFiles", "MoveFilesAfterDownload"}
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LOG_LINES_PER_JOB = 400
# DownloadOptions.cookies_browser may hold "file:<path to cookies.txt>" instead of a browser name.
COOKIES_FILE_PREFIX = "file:"
# Audio/video file extensions. A finished download leaves such a file behind only as one of its
# outputs (reported by [FILE]), unless it was told to keep its intermediate files.
MEDIA_EXTENSIONS = frozenset({
    "3gp", "aac", "aiff", "alac", "avi", "flac", "flv", "m4a", "m4v", "mka", "mkv", "mov",
    "mp3", "mp4", "oga", "ogg", "opus", "ts", "wav", "weba", "webm",
})
KEEP_INTERMEDIATE_ARGS = frozenset({"-k", "--keep-video"})
# Link parameters that only record where a link was shared from (YouTube's Share button adds a
# new ?si= value every time); they never change what yt-dlp downloads. utm_* is matched by prefix.
TRACKING_PARAMS = frozenset({"si", "feature", "pp", "fbclid", "gclid", "igshid"})
# Hosts (after dropping "www."/"m.") whose video links all come down to an 11-character video id.
YOUTUBE_HOSTS = frozenset({"youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com"})
YOUTUBE_ID_PATHS = frozenset({"shorts", "live", "embed"})  # youtube.com/<this>/<video id>
YOUTUBE_ID_RE = re.compile(r"[0-9A-Za-z_-]{11}")
# 11-character words that look like a video id but aren't one (yt-dlp excludes the same two):
# /embed/videoseries?list=... is a playlist player and /embed/live_stream?channel=... is a
# channel's current live stream, so these links fall back to the plain key with list=/channel=.
YOUTUBE_NOT_IDS = frozenset({"videoseries", "live_stream"})
# A #fragment that only gives a start time (vimeo.com/ID#t=10s). Other fragments are kept: on
# hash-routed sites they pick the song or station (music.163.com/#/song?id=..., radiko.jp/#!/live/QRR).
TIME_FRAGMENT_RE = re.compile(r"t=(?:npt:)?[0-9hms:.,]*", re.IGNORECASE)
SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
# A link typed without "https://" ("youtu.be/ID"), which yt-dlp still accepts. Requiring a dot
# keeps bare video ids and "ytsearch:" queries out: they are compared as typed.
BARE_HOST_RE = re.compile(r"[^\s/?#:@]+\.[^\s/?#:@]+(?::\d+)?(?:[/?#]|$)")


class JobStatus(str, Enum):
    WAITING = "Waiting"
    DOWNLOADING = "Downloading"
    CONVERTING = "Converting"
    DONE = "Done"
    ERROR = "Error"
    CANCELLED = "Cancelled"


ACTIVE_STATES = {JobStatus.DOWNLOADING, JobStatus.CONVERTING}
FINISHED_STATES = {JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED}


@dataclass
class DownloadOptions:
    save_dir: str
    playlist: bool
    embed_subs: bool
    embed_meta: bool
    extra_args: list[str]
    filename_template: str
    cookies_browser: str
    rate_limit: str


@dataclass
class _Destination:
    directory: str
    stem: str
    existing: frozenset[str]  # files with this stem that existed before we started


@dataclass(frozen=True)
class _Protected:
    """Files of another download that a cancel cleanup must leave alone."""

    job_id: int
    directory: str  # normalised with _norm_path
    stem: str  # os.path.normcase'd file name stem, e.g. "title [id]"
    partials: bool  # also keep .part/.ytdl/temp files (that download may still need them)
    # Also keep audio/video files. False for a finished download: its media files are its [FILE]
    # outputs (kept by exact path), so other media files with its name are the cancelled one's.
    media: bool = True

    def covers(self, directory: str, name: str, partial: bool) -> bool:
        if partial and not self.partials:
            return False
        if not partial and not self.media and _is_media(name):
            return False
        return directory == self.directory and os.path.normcase(name).startswith(self.stem + ".")


@dataclass(eq=False)
class _CleanupTask:
    """One cancel cleanup: prepared on the UI thread, run on a worker thread."""

    job_id: int
    destinations: list[_Destination]
    finals: list[str]  # normalised paths of the files the cancelled download finished
    protected: list[_Protected]  # other downloads' files when the cleanup started
    protected_paths: dict[str, int]  # normalised path -> id of the download that finished it
    _late: list[_Protected] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def claim(self, item: _Protected) -> None:
        """A download announced a file name while this cleanup runs (called on the UI thread)."""
        with self._lock:
            self._late.append(item)

    def late_owner(self, directory: str, name: str, partial: bool) -> int | None:
        with self._lock:
            return next((p.job_id for p in self._late if p.covers(directory, name, partial)), None)


@dataclass
class DownloadJob:
    id: int
    url: str
    preset: Preset
    options: DownloadOptions
    status: JobStatus = JobStatus.WAITING
    title: str = ""
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    item_index: str = ""
    item_count: str = ""
    files: list[str] = field(default_factory=list)
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINES_PER_JOB))
    error: FriendlyError | None = None
    exit_code: int | None = None
    process: QProcess | None = field(default=None, repr=False)
    cancel_requested: bool = False
    remove_when_stopped: bool = False
    used_js_runtime: bool = False
    _buffer: bytes = b""
    _destinations: list[_Destination] = field(default_factory=list)
    _link_keys: dict[bool, str] = field(default_factory=dict, init=False, repr=False)

    def link_key(self, keep_list: bool = True) -> str:
        """_link_key(self.url, keep_list), worked out once per job.

        find_active compares a pasted link with every queued job, once per pasted link;
        parsing all their links every time froze the window when hundreds were pasted.
        The link of a job never changes, so the key can be kept.
        """
        key = self._link_keys.get(keep_list)
        if key is None:
            key = self._link_keys[keep_list] = _link_key(self.url, keep_list)
        return key

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATES

    @property
    def display_title(self) -> str:
        title = self.title or self.url
        if self.item_index and self.item_count:
            return f"[{self.item_index.lstrip('0') or '0'}/{self.item_count}] {title}"
        return title

    def reset(self) -> None:
        self.status = JobStatus.WAITING
        self.percent = 0.0
        self.speed = ""
        self.eta = ""
        self.item_index = ""
        self.item_count = ""
        self.files = []
        self.error = None
        self.exit_code = None
        self.cancel_requested = False
        self._buffer = b""
        self._destinations = []


def build_arguments(job: DownloadJob, deps: DepsManager) -> list[str]:
    """Full yt-dlp argument list for a job (without the program itself)."""
    opts = job.options
    args: list[str] = [
        "--ignore-config",
        "--newline",
        "--color", "never",
        "--encoding", "utf-8",
        "--no-simulate",
        "--progress",
        "--progress-delta", "0.5",
        "--progress-template", PROGRESS_TEMPLATE,
        "--progress-template", POSTPROCESS_TEMPLATE,
        "--print", DEST_PRINT,
        "--print", TITLE_PRINT,
        "--print", FILE_PRINT,
        "-P", opts.save_dir,
        "-o", opts.filename_template,
    ]
    ffmpeg = deps.tool("ffmpeg")
    if ffmpeg.path is not None:
        args += ["--ffmpeg-location", str(ffmpeg.path.parent)]
    deno = deps.tool("deno")
    if deno.path is not None:
        args += ["--js-runtimes", f"deno:{deno.path}"]
    args.append("--yes-playlist" if opts.playlist else "--no-playlist")
    if opts.embed_subs and not is_audio_only(job.preset.args):
        args += ["--embed-subs", "--sub-langs", SUB_LANGS]
    if opts.embed_meta:
        args += ["--embed-thumbnail", "--embed-metadata"]
    if opts.cookies_browser.startswith(COOKIES_FILE_PREFIX):
        cookies_file = opts.cookies_browser[len(COOKIES_FILE_PREFIX):].strip()
        if cookies_file:
            args += ["--cookies", cookies_file]
    elif opts.cookies_browser:
        args += ["--cookies-from-browser", opts.cookies_browser]
    if opts.rate_limit:
        args += ["--limit-rate", opts.rate_limit]
    args += job.preset.args
    args += opts.extra_args
    args += ["--", job.url]
    return args


def _clean_field(value: str) -> str:
    value = value.strip()
    if not value or value.lower() in {"unknown", "n/a", "na", "none", "unknown b/s", "unknown eta"}:
        return ""
    return value


def _parse_percent(text: str) -> float | None:
    match = re.search(r"([\d.]+)\s*%", text)
    if not match:
        return None
    try:
        return max(0.0, min(100.0, float(match.group(1))))
    except ValueError:
        return None


def _link_key(url: str, keep_list: bool = True) -> str:
    """A key that is the same for links that download the same thing.

    Links to one video often differ: a new ?si= on every Share, youtu.be/ID vs watch?v=ID vs
    /shorts/ID, "www." or "m." hosts, a #t= start time or a trailing "/". Two downloads of
    such links into one folder write the same files, so they must be seen as the same link.
    YouTube's list= is kept (with “Download whole playlist” on, watch?v=X&list=Y fetches the
    whole playlist) unless keep_list is False. Never raises; odd text is compared as typed.
    When unsure, links stay different: a wrong match throws a link away as a duplicate.
    """
    text = url.strip().rstrip("/")
    link = text
    if not SCHEME_RE.match(text):
        if not BARE_HOST_RE.match(text):
            return text
        link = "//" + text  # so urlsplit reads "youtu.be" as the host, not as a path
    try:
        parts = urlsplit(link)
    except ValueError:  # e.g. an unclosed "[" or a host with look-alike Unicode slashes
        return text
    # http, https and no scheme at all fetch the same page and write the same files.
    scheme = parts.scheme.lower()
    if scheme in ("http", "https"):
        scheme = ""
    userinfo, at, host = parts.netloc.rpartition("@")
    host = host.lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix) and "." in host[len(prefix):]:
            host = host[len(prefix):]
            break
    path = parts.path.rstrip("/")
    params = [
        p for p in parts.query.split("&")
        if p and not _is_tracking_param(p.partition("=")[0])
    ]
    fragment = parts.fragment
    if host in YOUTUBE_HOSTS:
        fragment = ""  # YouTube never puts what to play after "#" (#comments, #t=...)
        video = _youtube_video_id(host, path, params)
        if video:
            # The other parameters (t=, start=, index=, ...) don't change what yt-dlp downloads.
            lists = [p.partition("=")[2] for p in params if p.partition("=")[0] == "list"]
            if keep_list and lists and lists[0]:
                return f"youtube:{video}?list={lists[0]}"
            return f"youtube:{video}"
    elif TIME_FRAGMENT_RE.fullmatch(fragment):
        fragment = ""
    # Parameter order never changes the page; a stable sort keeps repeated names in order.
    params.sort(key=lambda p: p.partition("=")[0])
    query = f"?{'&'.join(params)}" if params else ""
    fragment = f"#{fragment}" if fragment else ""
    return f"{scheme}://{userinfo}{at}{host}{path}{query}{fragment}"


def _is_tracking_param(name: str) -> bool:
    name = name.lower()
    return name in TRACKING_PARAMS or name.startswith("utm_")


def _youtube_video_id(host: str, path: str, params: list[str]) -> str:
    """The video id of a YouTube video link, or "" for other YouTube pages (playlists, channels)."""
    segments = [s for s in path.split("/") if s]
    if host == "youtu.be":
        candidate = segments[0] if len(segments) == 1 else ""
    elif len(segments) == 1 and segments[0].lower() == "watch":
        candidate = next((p.partition("=")[2] for p in params if p.partition("=")[0] == "v"), "")
    elif len(segments) == 2 and segments[0].lower() in YOUTUBE_ID_PATHS:
        candidate = segments[1]
    else:
        return ""
    if candidate in YOUTUBE_NOT_IDS or not YOUTUBE_ID_RE.fullmatch(candidate):
        return ""
    return candidate


def _norm_path(path: str, base: str = "") -> str:
    """Absolute, case-normalised path (relative paths are taken relative to base)."""
    p = Path(path)
    if base and not p.is_absolute():
        p = Path(base) / p
    return os.path.normcase(os.path.abspath(p))


def _is_partial(suffix: str) -> bool:
    """True for yt-dlp's unfinished files, given the part of the name after the stem."""
    return suffix.endswith((".part", ".ytdl")) or ".part-Frag" in suffix or suffix.startswith(".temp.")


def _is_media(name: str) -> bool:
    return Path(name).suffix.lower().lstrip(".") in MEDIA_EXTENSIONS


def _keeps_intermediates(job: DownloadJob) -> bool:
    """True if yt-dlp was told to keep the files it converts or merges (-k)."""
    return any(arg in KEEP_INTERMEDIATE_ARGS for arg in [*job.preset.args, *job.options.extra_args])


def _cookie_source_for_errors(cookies_browser: str) -> str:
    """The cookie source as explain_error expects it: a browser name or a cookies.txt path."""
    if cookies_browser.startswith(COOKIES_FILE_PREFIX):
        return cookies_browser[len(COOKIES_FILE_PREFIX):].strip()
    return cookies_browser


def _remove_unfinished(task: _CleanupTask) -> tuple[int, list[int]]:
    """Delete what a cancelled download left behind. Runs off the UI thread.

    Only files named after one of this download's destinations are looked at. Partial files
    are deleted; other files only if they did not exist when the item started and the item
    did not finish. Files that belong to another download (protected, or announced by one
    while the cleanup runs) are always kept.
    Returns the number of files removed and the ids of the downloads whose files were kept.
    """
    final_paths = {_norm_path(f) for f in task.finals}
    finished_stems = {os.path.normcase(Path(f).stem) for f in task.finals}
    removed = 0
    kept_for: set[int] = set()
    for dest in task.destinations:
        try:
            names = os.listdir(dest.directory)
        except OSError:
            continue
        directory = _norm_path(dest.directory)
        for name in names:
            if not name.startswith(dest.stem + "."):
                continue
            full = os.path.join(dest.directory, name)
            key = _norm_path(full)
            if key in final_paths:
                continue
            partial = _is_partial(name[len(dest.stem):])
            if not partial and (name in dest.existing or os.path.normcase(dest.stem) in finished_stems):
                continue
            owner = task.protected_paths.get(key)
            if owner is None:
                owner = next((p.job_id for p in task.protected if p.covers(directory, name, partial)), None)
            if owner is not None:
                kept_for.add(owner)
                continue
            for _attempt in range(10):
                # Checked right before each try: a download may have started meanwhile.
                owner = task.late_owner(directory, name, partial)
                if owner is not None:
                    kept_for.add(owner)
                    break
                try:
                    os.remove(full)
                    removed += 1
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.5)  # Windows: the killed process may still hold the file
    return removed, sorted(kept_for)


class DownloadManager(QObject):
    """Owns the queue. All QProcess work happens on the UI thread, asynchronously."""

    job_added = Signal(int)
    job_changed = Signal(int)
    job_removed = Signal(int)
    job_log = Signal(int, str)
    job_failed = Signal(int)
    queue_finished = Signal(int, int, int)  # done, failed, cancelled since the queue was last idle
    stopped = Signal()  # nothing running and no cleanup pending
    _cleanup_done = Signal(object, int, object)  # _CleanupTask, files removed, ids of downloads whose files were kept

    def __init__(self, deps: DepsManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._deps = deps
        self._jobs: dict[int, DownloadJob] = {}
        self._next_id = 1
        self._max_parallel = 2
        self._batch_counts = [0, 0, 0]
        self._batch_active = False
        self._pending_cleanups = 0
        self._cleanups: list[_CleanupTask] = []
        # Every file a download finished: normalised path -> (job id, kept intermediates).
        # Outlives the job (Clear finished, Remove, Retry) so a cancel can't delete it later.
        self._finished_files: dict[str, tuple[int, bool]] = {}
        self._cleanup_done.connect(self._on_cleanup_done)

    # -- queries ------------------------------------------------------------

    def jobs(self) -> list[DownloadJob]:
        return list(self._jobs.values())

    def get(self, job_id: int) -> DownloadJob | None:
        return self._jobs.get(job_id)

    def running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.process is not None)

    def waiting_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status == JobStatus.WAITING)

    def has_work(self) -> bool:
        return self.running_count() > 0 or self.waiting_count() > 0

    def is_idle(self) -> bool:
        return self.running_count() == 0 and self._pending_cleanups == 0

    def find_active(self, url: str, save_dir: str, preset: Preset | None = None) -> int | None:
        """Id of a waiting or running download of the same link into the same folder, if any.

        Links count as the same when they lead to the same download (see _link_key), so a
        second Share link of a video (another ?si=) is caught too.
        With a preset, only downloads with the same yt-dlp options count: the same video
        as MP4 and as MP3 is fine (they take turns, see _target_busy).
        """
        wanted_url = _link_key(url)
        wanted_dir = _norm_path(save_dir)
        for job in self._jobs.values():
            running = job.process is not None and not job.cancel_requested
            if job.status != JobStatus.WAITING and not running:
                continue
            if preset is not None and job.preset.args != preset.args:
                continue
            if job.link_key() == wanted_url and _norm_path(job.options.save_dir) == wanted_dir:
                return job.id
        return None

    # -- commands -----------------------------------------------------------

    def set_max_parallel(self, value: int) -> None:
        self._max_parallel = max(1, min(3, value))
        self._schedule()

    def add(self, url: str, preset: Preset, options: DownloadOptions) -> int:
        job = DownloadJob(id=self._next_id, url=url, preset=preset, options=options)
        self._next_id += 1
        self._jobs[job.id] = job
        self.job_added.emit(job.id)
        self._schedule()
        return job.id

    def retry(self, job_id: int, options: DownloadOptions) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.process is not None or job.status == JobStatus.WAITING:
            return
        job.reset()
        job.options = options
        self.job_changed.emit(job.id)
        self._schedule()

    def cancel(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        if job.status == JobStatus.WAITING and job.process is None:
            job.status = JobStatus.CANCELLED
            self.job_changed.emit(job.id)
            self._check_idle()
            return
        if job.process is not None and not job.cancel_requested:
            job.cancel_requested = True
            job.speed = ""
            job.eta = ""
            self._log(job, "Cancelling…")
            self.job_changed.emit(job.id)
            kill_process_tree(job.process, self)

    def cancel_all(self) -> None:
        for job_id in list(self._jobs):
            self.cancel(job_id)

    def remove(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        if job.process is not None:
            job.remove_when_stopped = True
            self.cancel(job_id)
            return
        del self._jobs[job_id]
        self.job_removed.emit(job_id)
        self._check_idle()

    def clear_finished(self) -> None:
        for job in list(self._jobs.values()):
            if job.status in FINISHED_STATES and job.process is None:
                self.remove(job.id)

    # -- scheduling ---------------------------------------------------------

    def _schedule(self) -> None:
        for job in list(self._jobs.values()):
            if self.running_count() >= self._max_parallel:
                break
            if job.status == JobStatus.WAITING and job.process is None and not self._target_busy(job):
                self._start(job)

    def _target_busy(self, job: DownloadJob) -> bool:
        """True while another download of the same link into the same folder runs.

        Two presets of one video (MP4 and MP3, say) download the same streams under the
        same temporary names, so the second one waits for the first to finish. YouTube's
        list= is ignored here: watch?v=X&list=Y fetches video X too (alone when “Download
        whole playlist” is off), so it would write X's files at the same time.
        """
        url = job.link_key(keep_list=False)
        folder = _norm_path(job.options.save_dir)
        return any(
            other is not job
            and other.process is not None
            and other.link_key(keep_list=False) == url
            and _norm_path(other.options.save_dir) == folder
            for other in self._jobs.values()
        )

    def _start(self, job: DownloadJob) -> None:
        ytdlp = self._deps.tool("yt-dlp")
        if ytdlp.path is None:
            self._fail_without_process(
                job,
                FriendlyError(
                    "yt-dlp is missing.",
                    "Open Settings → Tools and click “Download it for me”, then retry.",
                    None,
                    "yt-dlp was not found in the app's bin folder or on PATH.",
                ),
            )
            return
        try:
            Path(job.options.save_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._fail_without_process(
                job,
                FriendlyError(
                    "Can't use the “Save to” folder.",
                    "Choose another folder you can write to.",
                    None,
                    str(exc),
                ),
            )
            return

        args = build_arguments(job, self._deps)
        job.used_js_runtime = self._deps.tool("deno").path is not None
        job.status = JobStatus.DOWNLOADING
        job.cancel_requested = False
        self._batch_active = True

        proc = QProcess(self)
        proc.setProgram(str(ytdlp.path))
        proc.setArguments(args)
        proc.setWorkingDirectory(job.options.save_dir)
        proc.setProcessEnvironment(qprocess_environment())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        configure_process(proc, new_group=True)
        job_id = job.id
        proc.readyReadStandardOutput.connect(lambda: self._on_output(job_id))
        proc.finished.connect(lambda code, status: self._on_finished(job_id, code, status))
        proc.errorOccurred.connect(lambda err: self._on_process_error(job_id, err))
        job.process = proc

        self._log(job, f"{ATTEMPT_PREFIX} " + _format_command(ytdlp.path.name, args))
        self.job_changed.emit(job.id)
        proc.start()

    # -- process output -----------------------------------------------------

    def _on_output(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.process is None:
            return
        job._buffer += bytes(job.process.readAllStandardOutput().data())
        if b"\n" not in job._buffer:
            return
        *lines, job._buffer = job._buffer.split(b"\n")
        changed = False
        for raw in lines:
            changed |= self._handle_line(job, raw)
        if changed:
            self.job_changed.emit(job.id)

    def _handle_line(self, job: DownloadJob, raw: bytes) -> bool:
        line = ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).rstrip("\r\n")
        if not line.strip():
            return False

        if line.startswith("[PROG]"):
            if job.cancel_requested:
                return False
            parts = line[len("[PROG]"):].split("|")
            percent = _parse_percent(parts[0]) if parts else None
            if percent is not None:
                job.percent = percent
            job.speed = _clean_field(parts[1]) if len(parts) > 1 else ""
            job.eta = _clean_field(parts[2]) if len(parts) > 2 else ""
            job.status = JobStatus.DOWNLOADING
            return True  # progress lines are not added to the log (too many)

        self._log(job, line)

        if line.startswith("[TITLE]"):
            job.title = line[len("[TITLE]"):].strip()
            job.percent = 0.0
            if not job.cancel_requested:
                job.status = JobStatus.DOWNLOADING
            return True
        if line.startswith("[DEST]"):
            index, count, filename = (line[len("[DEST]"):].split("|", 2) + ["", ""])[:3]
            job.item_index = index.strip()
            job.item_count = count.strip()
            self._remember_destination(job, filename.strip())
            return True
        if line.startswith("[FILE]"):
            path = line[len("[FILE]"):].strip()
            if path:
                if path not in job.files:
                    job.files.append(path)
                full = _norm_path(path, job.options.save_dir)
                self._finished_files[full] = (job.id, _keeps_intermediates(job))
            job.percent = 100.0
            return True
        if line.startswith("[PP]"):
            name, _, state = line[len("[PP]"):].partition("|")
            if state.strip() == "started" and name.strip() not in TRIVIAL_POSTPROCESSORS:
                if not job.cancel_requested:
                    job.status = JobStatus.CONVERTING
                    job.speed = ""
                    job.eta = ""
                return True
            return False
        if line.startswith(CONVERT_PREFIXES) and not job.cancel_requested:
            job.status = JobStatus.CONVERTING
            job.speed = ""
            job.eta = ""
            return True
        return False

    def _remember_destination(self, job: DownloadJob, filename: str) -> None:
        if not filename:
            return
        path = Path(filename)
        if not path.is_absolute():
            path = Path(job.options.save_dir) / path
        directory = str(path.parent)
        stem = path.stem  # "Title [id]" from "Title [id].mp4"
        if not stem:
            return
        try:
            existing = frozenset(n for n in os.listdir(directory) if n.startswith(stem + "."))
        except OSError:
            existing = frozenset()
        job._destinations.append(_Destination(directory, stem, existing))
        if self._cleanups:
            # Cleanups already running must leave this download's files alone, too (this
            # includes a cleanup for an earlier run of this same job, now retried).
            claim = _Protected(job.id, _norm_path(directory), os.path.normcase(stem), partials=True)
            for task in self._cleanups:
                task.claim(claim)

    def _log(self, job: DownloadJob, line: str) -> None:
        job.log.append(line)
        self.job_log.emit(job.id, line)

    # -- completion ---------------------------------------------------------

    def _on_process_error(self, job_id: int, error: QProcess.ProcessError) -> None:
        # Only FailedToStart arrives without a finished() signal.
        if error != QProcess.ProcessError.FailedToStart:
            return
        job = self._jobs.get(job_id)
        if job is None or job.process is None:
            return
        message = job.process.errorString()
        job.process.deleteLater()
        job.process = None
        self._fail_without_process(
            job,
            FriendlyError(
                "Couldn't start yt-dlp.",
                "Re-download it under Settings → Tools, then retry.",
                None,
                message,
            ),
        )

    def _fail_without_process(self, job: DownloadJob, error: FriendlyError) -> None:
        job.status = JobStatus.ERROR
        job.error = error
        self._log(job, f"ERROR: {error.title} {error.details}")
        self._count(job)
        self.job_changed.emit(job.id)
        self.job_failed.emit(job.id)
        self._schedule()
        self._check_idle()

    def _on_finished(self, job_id: int, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.process is None:
            return
        # Flush any output that arrived without a trailing newline.
        job._buffer += bytes(job.process.readAllStandardOutput().data())
        if job._buffer:
            for raw in job._buffer.split(b"\n"):
                self._handle_line(job, raw)
            job._buffer = b""
        job.process.deleteLater()
        job.process = None
        job.exit_code = exit_code
        job.speed = ""
        job.eta = ""

        if job.cancel_requested:
            job.status = JobStatus.CANCELLED
            self._log(job, "Cancelled.")
            self._start_cleanup(job)
        elif exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0:
            job.status = JobStatus.DONE
            job.percent = 100.0
        else:
            job.status = JobStatus.ERROR
            job.error = explain_error(
                list(job.log),
                has_js_runtime=job.used_js_runtime,
                cookies_browser=_cookie_source_for_errors(job.options.cookies_browser),
            )
        self._count(job)
        self.job_changed.emit(job.id)
        if job.status == JobStatus.ERROR:
            self.job_failed.emit(job.id)
        if job.remove_when_stopped:
            del self._jobs[job.id]
            self.job_removed.emit(job.id)
        self._schedule()
        self._check_idle()

    def _count(self, job: DownloadJob) -> None:
        index = {JobStatus.DONE: 0, JobStatus.ERROR: 1, JobStatus.CANCELLED: 2}.get(job.status)
        if index is not None:
            self._batch_counts[index] += 1

    def _check_idle(self) -> None:
        if self.running_count() == 0 and self.waiting_count() == 0 and self._batch_active:
            done, failed, cancelled = self._batch_counts
            self._batch_counts = [0, 0, 0]
            self._batch_active = False
            self.queue_finished.emit(done, failed, cancelled)
        if self.is_idle():
            self.stopped.emit()

    # -- partial-file cleanup after cancel ---------------------------------

    def _start_cleanup(self, job: DownloadJob) -> None:
        task = self._prepare_cleanup(job)
        if task is None:
            return
        thread = threading.Thread(
            target=self._cleanup_worker,
            args=(task,),
            name=f"cleanup-{job.id}",
            daemon=True,
        )
        thread.start()

    def _prepare_cleanup(self, job: DownloadJob) -> _CleanupTask | None:
        """Snapshot what the cleanup for `job` may look at and what it must keep (UI thread)."""
        if not job._destinations:
            return None
        protected, protected_paths = self._protected_files(job)
        task = _CleanupTask(
            job.id,
            list(job._destinations),
            [_norm_path(f, job.options.save_dir) for f in job.files],
            protected,
            protected_paths,
        )
        self._cleanups.append(task)
        self._pending_cleanups += 1
        return task

    def _protected_files(self, job: DownloadJob) -> tuple[list[_Protected], dict[str, int]]:
        """What other downloads own, so cancelling `job` can't delete it (e.g. the MP3 and the
        MP4 of the same link share the "Title [id]" file name)."""
        directories = {_norm_path(dest.directory) for dest in job._destinations}
        protected: dict[_Protected, None] = {}  # ordered set
        paths: dict[str, int] = {}

        def keep_finished(full: str, owner: int, keeps_intermediates: bool) -> None:
            directory = os.path.dirname(full)
            if directory not in directories:
                return
            paths.setdefault(full, owner)
            # Keep its side files (thumbnail, subtitles…) too. Other media files with that name
            # are the cancelled download's leftovers.
            item = _Protected(owner, directory, Path(full).stem, partials=False, media=keeps_intermediates)
            protected[item] = None

        # Includes downloads no longer in the list (Clear finished, Remove) or retried since.
        for full, (owner, keeps_intermediates) in self._finished_files.items():
            if owner != job.id:
                keep_finished(full, owner, keeps_intermediates)
        for other in self._jobs.values():
            if other.id == job.id:
                continue
            keeps_intermediates = _keeps_intermediates(other)
            for f in other.files:
                keep_finished(_norm_path(f, other.options.save_dir), other.id, keeps_intermediates)
            if other.status == JobStatus.CANCELLED:
                continue  # it cleans up after itself; only its finished files are kept
            running = other.process is not None
            # A running download (even one being cancelled) may still write its partial files,
            # and a failed one can resume from them on Retry. A finished one has listed its
            # media files with [FILE].
            keep_partials = running or other.status == JobStatus.ERROR
            done = other.status == JobStatus.DONE and not running
            for dest in other._destinations:
                directory = _norm_path(dest.directory)
                if directory not in directories:
                    continue
                item = _Protected(
                    other.id,
                    directory,
                    os.path.normcase(dest.stem),
                    partials=keep_partials,
                    media=keeps_intermediates or not done,
                )
                protected[item] = None
        return list(protected), paths

    def _cleanup_worker(self, task: _CleanupTask) -> None:
        """Delete .part/.ytdl/temp files (and files created by the cancelled item)."""
        removed: int = 0
        kept_for: list[int] = []
        try:
            removed, kept_for = _remove_unfinished(task)
        finally:
            self._cleanup_done.emit(task, removed, kept_for)

    def _on_cleanup_done(self, task: _CleanupTask, removed: int, kept_for: object) -> None:
        self._pending_cleanups = max(0, self._pending_cleanups - 1)
        if task in self._cleanups:
            self._cleanups.remove(task)
        job = self._jobs.get(task.job_id)
        if job is not None:
            if removed:
                self._log(job, f"Removed {removed} unfinished file(s).")
            for other_id in kept_for if isinstance(kept_for, list) else []:
                if other_id == job.id:
                    continue  # this download was retried while its old files were cleaned up
                other = self._jobs.get(other_id)
                name = f" (“{other.display_title}”)" if other is not None else ""
                self._log(job, f"Kept files shared with download #{other_id}{name}.")
        if self.is_idle():
            self.stopped.emit()


def _format_command(program: str, args: list[str]) -> str:
    def quote(arg: str) -> str:
        if arg and not any(c.isspace() or c in "\"'|&<>%" for c in arg):
            return arg
        return '"' + arg.replace('"', '\\"') + '"'

    return " ".join([program, *(quote(a) for a in args)])
