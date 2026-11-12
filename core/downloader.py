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

from PySide6.QtCore import QObject, QProcess, Signal

from .deps import DepsManager
from .errors import FriendlyError, explain_error
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
    if opts.cookies_browser:
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


class DownloadManager(QObject):
    """Owns the queue. All QProcess work happens on the UI thread, asynchronously."""

    job_added = Signal(int)
    job_changed = Signal(int)
    job_removed = Signal(int)
    job_log = Signal(int, str)
    job_failed = Signal(int)
    queue_finished = Signal(int, int, int)  # done, failed, cancelled since the queue was last idle
    stopped = Signal()  # nothing running and no cleanup pending
    _cleanup_done = Signal(int, int)

    def __init__(self, deps: DepsManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._deps = deps
        self._jobs: dict[int, DownloadJob] = {}
        self._next_id = 1
        self._max_parallel = 2
        self._batch_counts = [0, 0, 0]
        self._batch_active = False
        self._pending_cleanups = 0
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
            if job.status == JobStatus.WAITING and job.process is None:
                self._start(job)

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

        self._log(job, "▶ " + _format_command(ytdlp.path.name, args))
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
            if path and path not in job.files:
                job.files.append(path)
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
                cookies_browser=job.options.cookies_browser,
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
        destinations = list(job._destinations)
        finals = list(job.files)
        if not destinations:
            return
        self._pending_cleanups += 1
        thread = threading.Thread(
            target=self._cleanup_worker,
            args=(job.id, destinations, finals),
            name=f"cleanup-{job.id}",
            daemon=True,
        )
        thread.start()

    def _cleanup_worker(self, job_id: int, destinations: list[_Destination], finals: list[str]) -> None:
        """Delete .part/.ytdl/temp files (and files created by the cancelled item)."""
        final_paths = {os.path.normcase(os.path.abspath(f)) for f in finals}
        finished_stems = {Path(f).stem for f in finals}
        removed = 0
        for dest in destinations:
            try:
                names = os.listdir(dest.directory)
            except OSError:
                continue
            for name in names:
                if not name.startswith(dest.stem + "."):
                    continue
                full = os.path.join(dest.directory, name)
                if os.path.normcase(os.path.abspath(full)) in final_paths:
                    continue
                suffix = name[len(dest.stem):]
                is_partial = (
                    suffix.endswith((".part", ".ytdl"))
                    or ".part-Frag" in suffix
                    or suffix.startswith(".temp.")
                )
                if not is_partial and (name in dest.existing or dest.stem in finished_stems):
                    continue
                for _attempt in range(10):
                    try:
                        os.remove(full)
                        removed += 1
                        break
                    except FileNotFoundError:
                        break
                    except OSError:
                        time.sleep(0.5)  # Windows: the killed process may still hold the file
        self._cleanup_done.emit(job_id, removed)

    def _on_cleanup_done(self, job_id: int, removed: int) -> None:
        self._pending_cleanups = max(0, self._pending_cleanups - 1)
        job = self._jobs.get(job_id)
        if job is not None and removed:
            self._log(job, f"Removed {removed} unfinished file(s).")
        if self.is_idle():
            self.stopped.emit()


def _format_command(program: str, args: list[str]) -> str:
    def quote(arg: str) -> str:
        if arg and not any(c.isspace() or c in "\"'|&<>%" for c in arg):
            return arg
        return '"' + arg.replace('"', '\\"') + '"'

    return " ".join([program, *(quote(a) for a in args)])
