"""Stopping downloads: kill_process_tree must never touch a QProcess that is already gone."""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from types import TracebackType

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QProcess, QTimer

from core import process
from core.process import configure_process, kill_process_tree

SIGKILL = 9  # signal.SIGKILL does not exist on Windows
SLEEPER = "import time; time.sleep(30)"
TERM_IGNORER = (
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "print('ready', flush=True); time.sleep(30)"
)
CHILD_PID = 424242
CHILD_START = "777"


def _spin(ms: int) -> None:
    """Run the Qt event loop for ms milliseconds (timers fire, deleteLater runs)."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _wait_until(condition: Callable[[], bool], timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if condition():
            return True
        _spin(20)
    return condition()


def _stat_fields(start_time: str) -> list[str]:
    """Fake /proc/<pid>/stat fields after the command name (19 = start time)."""
    return ["S", "1", *(["0"] * 17), start_time, *(["0"] * 5)]


def _start_python(code: str) -> QProcess:
    proc = QProcess()
    proc.start(sys.executable, ["-c", code])
    assert proc.waitForStarted(10000), proc.errorString()
    return proc


def _stop(proc: QProcess) -> None:
    if shiboken6.isValid(proc) and proc.state() != QProcess.ProcessState.NotRunning:
        proc.kill()
        proc.waitForFinished(5000)


@pytest.fixture
def unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> list[BaseException]:
    """Exceptions that would have reached the app's crash dialog (sys.excepthook)."""
    seen: list[BaseException] = []

    def hook(_type: type[BaseException], exc: BaseException, _tb: TracebackType | None) -> None:
        seen.append(exc)

    monkeypatch.setattr(sys, "excepthook", hook)
    return seen


class FakeLinux:
    """Forces kill_process_tree's Linux branch on any OS and records the signals sent."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.start_times: dict[int, str] = {}
        self.kills: list[tuple[int, int]] = []
        self.group_kills: list[tuple[int, int]] = []
        monkeypatch.setattr(process, "IS_WINDOWS", False)
        monkeypatch.setattr(process, "HARD_KILL_DELAY_MS", 100)
        monkeypatch.setattr(signal, "SIGKILL", SIGKILL, raising=False)
        monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=False)
        monkeypatch.setattr(os, "killpg", lambda pid, sig: self.group_kills.append((pid, sig)), raising=False)
        monkeypatch.setattr(os, "kill", lambda pid, sig: self.kills.append((pid, sig)))
        monkeypatch.setattr(process, "_linux_descendants", lambda pid: {CHILD_PID: CHILD_START})
        monkeypatch.setattr(process, "_read_stat", self._read_stat)

    def _read_stat(self, pid: int) -> list[str] | None:
        start = self.start_times.get(pid)
        return _stat_fields(start) if start is not None else None


@pytest.mark.parametrize(
    ("leader_later", "leader_killed"),
    [(None, False), ("999", False), ("100", True)],
    ids=["program-gone", "pid-reused", "program-still-there"],
)
def test_hard_kill_after_qprocess_deleted(
    qapp: object,
    monkeypatch: pytest.MonkeyPatch,
    unexpected_errors: list[BaseException],
    leader_later: str | None,
    leader_killed: bool,
) -> None:
    fake = FakeLinux(monkeypatch)
    owner = QObject()
    proc = _start_python(SLEEPER)
    proc.finished.connect(proc.deleteLater)  # same wiring as core/downloader.py
    pid = int(proc.processId())
    fake.start_times = {pid: "100", CHILD_PID: CHILD_START}
    try:
        kill_process_tree(proc, owner)
        assert (pid, signal.SIGTERM) in fake.group_kills
        assert (CHILD_PID, signal.SIGTERM) in fake.kills

        # The program exits and the downloader deletes its QProcess...
        proc.kill()
        assert proc.waitForFinished(5000)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(proc)
        if leader_later is None:
            del fake.start_times[pid]
        else:
            fake.start_times[pid] = leader_later

        # ...before the delayed hard kill runs.
        assert _wait_until(lambda: (CHILD_PID, SIGKILL) in fake.kills, 3000)
    finally:
        _stop(proc)

    assert unexpected_errors == []
    assert ((pid, SIGKILL) in fake.group_kills) is leader_killed
    assert ((pid, SIGKILL) in fake.kills) is leader_killed
    del owner


def test_hard_kill_stops_program_that_ignores_sigterm(
    qapp: object, monkeypatch: pytest.MonkeyPatch, unexpected_errors: list[BaseException]
) -> None:
    fake = FakeLinux(monkeypatch)
    owner = QObject()
    proc = _start_python(TERM_IGNORER)
    try:
        assert proc.waitForReadyRead(10000)
        pid = int(proc.processId())
        fake.start_times = {pid: "100", CHILD_PID: CHILD_START}

        kill_process_tree(proc, owner)
        assert _wait_until(lambda: proc.state() == QProcess.ProcessState.NotRunning, 5000)
    finally:
        _stop(proc)

    assert unexpected_errors == []
    assert (CHILD_PID, SIGKILL) in fake.kills
    assert (pid, SIGKILL) in fake.group_kills
    del owner


def test_windows_fallback_after_qprocess_deleted(
    qapp: object, monkeypatch: pytest.MonkeyPatch, unexpected_errors: list[BaseException]
) -> None:
    # Off Windows "taskkill" fails to start, which takes the errorOccurred path.
    monkeypatch.setattr(process, "IS_WINDOWS", True)
    monkeypatch.setattr(process, "WINDOWS_FALLBACK_DELAY_MS", 100)
    owner = QObject()
    proc = _start_python(SLEEPER)
    proc.finished.connect(proc.deleteLater)
    try:
        kill_process_tree(proc, owner)
        assert _wait_until(lambda: not shiboken6.isValid(proc), 10000)
        _spin(300)
    finally:
        _stop(proc)

    assert unexpected_errors == []
    del owner


def test_is_running_on_deleted_qprocess(qapp: object) -> None:
    proc = QProcess()
    assert not process._is_running(proc)
    shiboken6.delete(proc)
    assert not process._is_running(proc)
    kill_process_tree(proc, QObject())  # no crash on an already-deleted QProcess


def _comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _gone(pid: int) -> bool:
    fields = process._read_stat(pid)
    return fields is None or fields[0] in ("Z", "X")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs Linux /proc and signals")
def test_linux_cancel_with_children_real_processes(
    qapp: object, monkeypatch: pytest.MonkeyPatch, unexpected_errors: list[BaseException]
) -> None:
    monkeypatch.setattr(process, "HARD_KILL_DELAY_MS", 1500)
    owner = QObject()
    proc = QProcess()
    # The shell dies on SIGTERM; its child ignores SIGTERM, so only the delayed
    # hard kill can stop it -- after the shell's QProcess has been deleted.
    proc.setProgram("/bin/sh")
    proc.setArguments(["-c", "(trap '' TERM; sleep 30) & wait"])
    configure_process(proc, new_group=True)
    proc.finished.connect(proc.deleteLater)  # same wiring as core/downloader.py
    proc.start()
    assert proc.waitForStarted(10000), proc.errorString()
    pid = int(proc.processId())
    children: dict[int, str] = {}
    try:
        assert _wait_until(
            lambda: any(_comm(c) == "sleep" for c in process._linux_descendants(pid)), 5000
        )
        children = process._linux_descendants(pid)

        kill_process_tree(proc, owner)
        assert _wait_until(lambda: not shiboken6.isValid(proc), 1200), "QProcess was not deleted"
        assert not all(_gone(c) for c in children)  # the SIGTERM-ignoring child is still there

        assert _wait_until(lambda: all(_gone(c) for c in children), 5000)
    finally:
        _stop(proc)
        for child, start_time in children.items():
            process._signal_if_same(child, start_time, signal.SIGKILL)

    assert unexpected_errors == []
    del owner
