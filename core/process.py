"""Helpers for launching and stopping external programs (yt-dlp, ffmpeg, deno)."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer

from .paths import IS_WINDOWS, bin_dirs

CREATE_NO_WINDOW = 0x08000000


def child_environment(extra_dirs: Iterable[Path] | None = None) -> dict[str, str]:
    """Environment for launched tools: our bin/ folders first on PATH, UTF-8 I/O."""
    env = dict(os.environ)
    dirs = [str(d) for d in (extra_dirs if extra_dirs is not None else bin_dirs()) if d.is_dir()]
    current_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*dirs, current_path]) if dirs else current_path
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # yt-dlp's official binaries are PyInstaller programs too. When this app is
    # itself a PyInstaller build, make sure they start as independent programs.
    for key in list(env):
        if key.startswith("_PYI_") or key == "_MEIPASS2":
            del env[key]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def qprocess_environment(extra_dirs: Iterable[Path] | None = None) -> QProcessEnvironment:
    qenv = QProcessEnvironment()
    for key, value in child_environment(extra_dirs).items():
        qenv.insert(key, value)
    return qenv


def subprocess_kwargs() -> dict[str, Any]:
    """Extra arguments for subprocess.run so no console window appears on Windows."""
    if IS_WINDOWS:
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def configure_process(proc: QProcess, new_group: bool = False) -> None:
    """Hide the console window on Windows; optionally start a new session on Unix.

    Qt already adds CREATE_NO_WINDOW when this app has no console of its own (the
    packaged, windowed build). The modifier makes it explicit when the PySide6
    build exposes it.
    """
    if IS_WINDOWS:
        setter = getattr(proc, "setCreateProcessArgumentsModifier", None)
        if setter is not None:

            def _no_window(args: Any) -> None:
                args.flags |= CREATE_NO_WINDOW

            setter(_no_window)
            setattr(proc, "_no_window_modifier", _no_window)  # keep the callable alive
        return
    if new_group:
        setter = getattr(proc, "setUnixProcessParameters", None)
        flag_enum = getattr(QProcess, "UnixProcessFlag", None)
        if setter is not None and flag_enum is not None and hasattr(flag_enum, "CreateNewSession"):
            try:
                setter(flag_enum.CreateNewSession)
            except (TypeError, ValueError):
                pass


def _read_stat(pid: int) -> list[str] | None:
    """Fields of /proc/<pid>/stat after the command name (index 0 = state)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            stat = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    # Format: pid (comm) state ppid ...; comm may contain spaces/parentheses.
    fields = stat.rsplit(")", 1)[-1].split()
    return fields if len(fields) > 20 else None


def _linux_descendants(root_pid: int) -> dict[int, str]:
    """Descendant PIDs of root_pid mapped to their start time (to detect PID reuse)."""
    children: dict[int, list[tuple[int, str]]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return {}
    for entry in entries:
        if not entry.isdigit():
            continue
        fields = _read_stat(int(entry))
        if fields is None:
            continue
        try:
            ppid = int(fields[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append((int(entry), fields[19]))
    result: dict[int, str] = {}
    stack = [root_pid]
    while stack:
        current = stack.pop()
        for child, start_time in children.get(current, []):
            if child not in result:
                result[child] = start_time
                stack.append(child)
    return result


def _signal_if_same(pid: int, start_time: str, sig: int) -> None:
    fields = _read_stat(pid)
    if fields is None or fields[19] != start_time:
        return  # already gone, or the PID now belongs to another process
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _signal_group(pid: int, sig: int) -> None:
    """Signal the process group led by pid, if the process leads one."""
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        pass


def kill_process_tree(proc: QProcess, owner: QObject) -> None:
    """Stop a process and everything it started (yt-dlp + ffmpeg/deno children).

    Non-blocking: on Windows taskkill runs as its own QProcess; on Linux the
    process group (or the /proc tree) gets SIGTERM, then SIGKILL if needed.
    """
    if proc.state() == QProcess.ProcessState.NotRunning:
        return
    pid = int(proc.processId())
    if pid <= 0:
        proc.kill()
        return
    if IS_WINDOWS:
        killer = QProcess(owner)
        configure_process(killer)
        killer.finished.connect(killer.deleteLater)
        killer.errorOccurred.connect(lambda _err: proc.kill())
        killer.start("taskkill", ["/PID", str(pid), "/T", "/F"])

        def _fallback() -> None:
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()

        QTimer.singleShot(4000, proc, _fallback)
        return

    # Collect the tree first: once the parent dies, its children get re-parented.
    # The process runs in its own session when the Qt binding allows it, so the
    # group signal covers everything; the explicit tree walk covers the rest.
    descendants = _linux_descendants(pid)
    _signal_group(pid, signal.SIGTERM)
    for child, start_time in descendants.items():
        _signal_if_same(child, start_time, signal.SIGTERM)
    proc.terminate()

    def _hard_kill() -> None:
        for child, start_time in descendants.items():
            _signal_if_same(child, start_time, signal.SIGKILL)
        if proc.state() != QProcess.ProcessState.NotRunning:
            _signal_group(pid, signal.SIGKILL)
            proc.kill()

    QTimer.singleShot(2000, owner, _hard_kill)


def run_quiet(args: list[str], timeout: float = 60.0) -> tuple[int, str]:
    """Run a short command (for version checks) from a worker thread."""
    completed = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=child_environment(),
        **subprocess_kwargs(),
    )
    return completed.returncode, completed.stdout.decode("utf-8", errors="replace")
