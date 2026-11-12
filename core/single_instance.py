"""Keep Weir to one window per user.

Two Weir windows would delete each other's partial downloads and overwrite each other's
settings.json and presets.json. The first launch takes a per-user lock file and keeps it
until it exits; while it runs it also listens on a per-user local socket (a named pipe on
Windows). A later launch cannot take the lock, so it hands its links over the socket and
quits without a window.

The lock, not the socket, decides which launch runs: creating a lock file is atomic, while
on Windows two programs can listen under one pipe name, and on Linux a new socket simply
replaces the old one. Qt frees a lock left by a crashed Weir once it sees that the process
is gone.

Wire protocol (same user, same computer): the new launch sends its links as UTF-8 text,
one per line (possibly none), followed by a NUL byte; the running Weir answers with a
single ACK byte once it has taken them. When the desktop gave the new launch an activation
token (Wayland), it comes first, on a line of its own that starts with _TOKEN_MARK; links
never start with that, and a Weir from before the token sees no link in that line.
"""

from __future__ import annotations

import enum
import hashlib
import os
import stat
import time
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QEventLoop, QLockFile, QObject, QSysInfo, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import APP_NAME
from .paths import IS_WINDOWS, config_dir

# How long a single hand-over attempt waits for the running Weir to answer.
HAND_OVER_TIMEOUT_MS = 1500
# How long a new launch keeps trying while another Weir holds the lock but does not take
# the links: it may still be starting, or quitting (then it stops answering at once but
# keeps the lock for up to 8 s while it stops downloads and cleans up, and the new launch
# starts fresh once it has exited). A Weir that is stuck must not block launches forever.
START_WAIT_MS = 20_000
# Pause between attempts while nothing answers.
_RETRY_PAUSE_MS = 100
# A Weir that started without the lock (the one holding it was stuck) tries to take it
# this often, so that once the stuck Weir is gone later launches come here.
_RECLAIM_MS = 1500
# A lock file without readable details (a power cut can leave one empty) counts as left
# over once it is this old: a starting Weir writes its details right after creating it.
_UNREADABLE_LOCK_AGE_S = 10

_END = b"\0"
_ACK = b"\x06"
_MAX_MESSAGE = 1 << 20  # a lot of links; anything bigger did not come from Weir
_ASFW_ANY = 0xFFFFFFFF
# Starts the optional token line. A control character, so no link can start with it.
_TOKEN_MARK = "\x01activation-token "
_MAX_TOKEN = 1024  # tokens are short; anything longer is not one
_TOKEN_VARIABLE = "XDG_ACTIVATION_TOKEN"

# Linux: systemd and elogind give each signed-in user /run/user/<uid> (what XDG_RUNTIME_DIR
# names), a folder only that user can use.
_RUNTIME_ROOT = "/run/user"
_FALLBACK_SOCKET_DIR = "/tmp"

# Lock files held by this process (the tests run several instances in one process).
_held_here: set[str] = set()


def default_server_name() -> str:
    """Per-user name, so two people signed in on one computer each get their own Weir."""
    home = os.path.normcase(str(Path.home()))
    digest = hashlib.sha256(os.fsencode(home)).hexdigest()[:12]
    return f"{APP_NAME}-{digest}"


def socket_address(name: str) -> str:
    """What the socket calls are given for ``name``: the same in every launch.

    On Windows the bare name (of a named pipe). Elsewhere Qt would put a bare name in
    QDir::tempPath(), which follows each launch's own TMPDIR: a launch from a terminal
    that sets another TMPDIR would then never find the running Weir, wait, and start a
    second one. So both ends use one absolute path instead (an absolute name is kept).
    """
    if IS_WINDOWS or name.startswith("/"):
        return name
    return f"{_socket_dir()}/{name}"


def _socket_dir() -> str:
    """This user's runtime folder when it is there and private, else /tmp.

    Goes by the user ID, not by XDG_RUNTIME_DIR (or Qt's runtime location, which falls
    back to TMPDIR): a launch can inherit those differently, say through su. Both paths
    stay far below the 107 bytes a socket path may have, and the socket itself is for
    this user only either way (UserAccessOption).
    """
    uid = os.getuid()
    runtime = f"{_RUNTIME_ROOT}/{uid}"
    try:
        info = os.lstat(runtime)  # lstat: a link put there does not count
    except OSError:
        return _FALLBACK_SOCKET_DIR
    private = (stat.S_ISDIR(info.st_mode) and info.st_uid == uid
               and not info.st_mode & 0o077)
    if private and os.access(runtime, os.W_OK | os.X_OK):
        return runtime
    return _FALLBACK_SOCKET_DIR


def default_lock_path() -> Path:
    """Per-user lock file in Weir's settings folder.

    Named after this computer as well: with a home folder shared over the network, a Weir
    on one computer must not hold up launches on another (the socket is per computer too),
    and Qt can only tell whether the owner of a lock is still running on its own computer.
    The computer's unique ID names it, as it is what Qt itself checks; not the host name,
    which on Linux can change while Weir runs (NetworkManager may take one from DHCP) and
    would then let a second Weir start.
    """
    machine = bytes(QSysInfo.machineUniqueId())
    if not machine:  # Qt then goes by the host name too
        machine = QSysInfo.machineHostName().encode("utf-8", errors="replace")
    digest = hashlib.sha256(machine).hexdigest()[:12]
    return config_dir() / f"running-{digest}.lock"


def _usable_token(token: str) -> str:
    """``token`` if it can be one (plain characters, one line), else ""."""
    if (0 < len(token) <= _MAX_TOKEN and token.isascii() and token.isprintable()
            and " " not in token):
        return token
    return ""


def launch_activation_token() -> str:
    """The token the desktop gave this launch for bringing a window forward, if any.

    On Wayland only a program holding such a token may take the focus; the launcher
    gives it to the new launch, so the running Weir needs it handed over.
    """
    return _usable_token(os.environ.get(_TOKEN_VARIABLE, ""))


def encode_message(links: Sequence[str], token: str = "") -> bytes:
    # Without the mark's control character no link can pass for the token line.
    cleaned = (link.replace(_TOKEN_MARK[0], "").strip() for link in links)
    text = "\n".join(link for link in cleaned if link)
    token = _usable_token(token)
    if token:
        text = f"{_TOKEN_MARK}{token}\n{text}"
    return text.encode("utf-8", errors="replace").replace(_END, b"") + _END


def decode_message(data: bytes) -> tuple[list[str], str]:
    """The links and the activation token ("" when none came) of a message."""
    text = data.decode("utf-8", errors="replace")
    token = ""
    if text.startswith(_TOKEN_MARK):
        first, _, text = text.partition("\n")
        token = _usable_token(first[len(_TOKEN_MARK):])
    return [line.strip() for line in text.splitlines() if line.strip()], token


class HandOver(enum.Enum):
    """What happened when a new launch tried to reach the running Weir."""

    DELIVERED = enum.auto()  # the running Weir took the links
    NO_SERVER = enum.auto()  # nothing listens under our name (or only a crash leftover)
    NO_ANSWER = enum.auto()  # something listens but did not answer in time, or hung up


class _Claim(enum.Enum):
    TAKEN = enum.auto()  # this launch holds the lock now
    BUSY = enum.auto()  # another Weir holds it
    UNAVAILABLE = enum.auto()  # no lock file can be made here at all


def _let_running_window_come_forward() -> None:
    """Windows only lets the app the user just started take the foreground; pass that on."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint32(_ASFW_ANY))
    except (AttributeError, OSError):
        pass


def _pause(ms: int) -> None:
    """Wait while still handling Qt events (in the tests both ends live in this thread)."""
    loop = QEventLoop()
    QTimer.singleShot(max(ms, 0), loop.quit)
    loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)


class _Delivery(QObject):
    """One attempt to pass links to the running Weir.

    Runs a short local event loop rather than blocking in waitFor...(), so it never
    freezes the process for longer than the timeout and also works when both ends live
    in one thread (as in the tests).
    """

    def __init__(self, server_name: str, payload: bytes, timeout_ms: int) -> None:
        super().__init__()
        self.outcome = HandOver.NO_ANSWER
        self._server_name = server_name
        self._payload = payload
        self._timeout_ms = timeout_ms
        self._reply = bytearray()
        self._connected = False
        self._finished = False
        self._loop = QEventLoop(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.readyRead.connect(self._on_ready_read)
        self._socket.errorOccurred.connect(self._on_error)
        # On Windows a hang-up (Weir quitting or crashing) brings no error, only this.
        self._socket.disconnected.connect(self._on_disconnected)

    def run(self) -> HandOver:
        self._timer.start(self._timeout_ms)
        self._socket.connectToServer(self._server_name)  # may finish (or fail) right away
        if not self._finished:
            self._loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        self._timer.stop()
        self._socket.abort()
        return self.outcome

    def _finish(self, outcome: HandOver) -> None:
        if not self._finished:
            self._finished = True
            self.outcome = outcome
            self._loop.quit()

    def _on_timeout(self) -> None:
        self._finish(HandOver.NO_ANSWER)

    def _on_connected(self) -> None:
        self._connected = True
        _let_running_window_come_forward()
        self._socket.write(self._payload)

    def _on_ready_read(self) -> None:
        self._reply += self._socket.readAll().data()
        if _ACK in self._reply:
            self._finish(HandOver.DELIVERED)

    def _on_disconnected(self) -> None:
        self._on_ready_read()  # the answer may have arrived together with the hang-up
        self._finish(HandOver.NO_ANSWER)

    def _on_error(self, error: QLocalSocket.LocalSocketError) -> None:
        self._on_ready_read()  # likewise
        nobody_there = error in (QLocalSocket.LocalSocketError.ServerNotFoundError,
                                 QLocalSocket.LocalSocketError.ConnectionRefusedError)
        self._finish(HandOver.NO_SERVER if nobody_there and not self._connected
                     else HandOver.NO_ANSWER)


class _Incoming(QObject):
    """One connection from a new launch, until its message is complete.

    Owns its socket and uses only bound slots, so Qt drops every connection when it
    goes away and nothing calls back into Python objects that are being destroyed.
    """

    received = Signal(list, str)  # links, activation token

    def __init__(self, socket: QLocalSocket, parent: QObject) -> None:
        super().__init__(parent)
        self._buffer = bytearray()
        self._done = False
        socket.setParent(self)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(self.deleteLater)

    def _socket(self) -> QLocalSocket | None:
        """The connection's socket, looked up each time rather than kept.

        PySide can, rarely, mark a kept Python reference to a socket that Qt created as
        deleted while the socket is still open; every use of it then raises RuntimeError.
        A fresh lookup still finds the socket.
        """
        return self.findChild(QLocalSocket, options=Qt.FindChildOption.FindDirectChildrenOnly)

    def begin(self) -> None:
        self._on_ready_read()  # data may already be waiting
        socket = self._socket()
        if socket is None or socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.deleteLater()

    def drop(self) -> None:
        """Hang up; a launch whose links were not taken yet then tries again.

        Closing every connection frees the name at once: on Windows the pipe stays busy
        while one is open, and a new launch would wait for it.
        """
        socket = self._socket()
        if socket is None:
            self.deleteLater()
        elif self._done:
            socket.disconnectFromServer()  # lets the answer go out first
        else:
            self._done = True
            socket.abort()
            self.deleteLater()

    def _on_ready_read(self) -> None:
        socket = self._socket()
        if self._done or socket is None:
            return
        self._buffer += socket.readAll().data()
        end = self._buffer.find(_END)
        if end < 0:
            if len(self._buffer) > _MAX_MESSAGE:
                self._done = True
                socket.abort()
            return
        self._done = True
        links, token = decode_message(bytes(self._buffer[:end]))
        self._buffer.clear()
        # Answer first so the new launch can quit at once; it closes the connection.
        socket.write(_ACK)
        socket.flush()
        self.received.emit(links, token)


class SingleInstance(QObject):
    """Makes the first Weir the only one; later launches pass their links to it.

    Call :meth:`start` before creating settings or windows. When it returns True another
    Weir has the links and this launch should exit. Otherwise this Weir runs, and
    :attr:`activation_requested` fires whenever Weir is launched again. Call
    :meth:`stop_listening` when Weir starts quitting and :meth:`release` as it exits.
    """

    # Links from the new launch (may be empty): add them and bring the window forward,
    # using the launch's activation token ("" when it had none).
    activation_requested = Signal(list, str)

    def __init__(self, server_name: str | None = None, *,
                 lock_path: str | os.PathLike[str] | None = None,
                 wait_ms: int = START_WAIT_MS, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = socket_address(server_name or default_server_name())
        path = Path(lock_path) if lock_path is not None else default_lock_path()
        self._lock_key = os.path.normcase(os.path.abspath(path))
        self._lock = QLockFile(str(path))
        # Weir keeps the lock for as long as it runs, so the lock's age says nothing (by
        # default Qt calls a lock older than 30 s stale). Qt still frees the lock of a
        # process that is gone, and _left_behind() covers what Qt cannot tell.
        self._lock.setStaleLockTime(0)
        self._wait_ms = wait_ms
        self._server: QLocalServer | None = None
        self._reclaim_timer: QTimer | None = None

    @property
    def server_name(self) -> str:
        return self._name

    @property
    def is_listening(self) -> bool:
        return self._server is not None and self._server.isListening()

    @property
    def holds_lock(self) -> bool:
        return self._lock.isLocked()

    def start(self, links: Sequence[str] = ()) -> bool:
        """Become the running Weir, or hand ``links`` to the one already running.

        Returns True when another Weir took the links (this launch should then quit).
        While another Weir holds the lock without taking the links (it is still starting,
        or quitting), keeps trying for up to ``wait_ms`` and runs as soon as the lock is
        free. Never refuses to start: past that time, or when no lock can be made at all,
        this Weir runs anyway. Past that time it also takes the lock over (and answers
        later launches) once the Weir holding it is gone.
        """
        deadline = time.monotonic() + self._wait_ms / 1000
        while True:
            claim = self._claim()
            if claim is _Claim.TAKEN:
                self._become_primary()
                return False
            if claim is _Claim.UNAVAILABLE:
                # Without a lock (say the settings folder is read-only) the socket alone
                # has to do: hand over if a Weir answers, otherwise answer from now on.
                if self.hand_over(links) is HandOver.DELIVERED:
                    return True
                self._listen()
                return False
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                # The running Weir neither takes the links nor exits: start anyway rather
                # than keep the user waiting. Without the lock this one does not answer
                # yet; it takes over once the stuck Weir is gone, or the next launch would
                # take the freed lock and run as a second Weir beside this one.
                self._keep_trying_to_claim()
                return False
            # Short attempts: on Windows a Weir that accepts several launches at the moment
            # it starts listening can leave one of them unanswered for good, while a new
            # connection gets through. (Should an abandoned attempt get through after all,
            # the window is just handed the same links twice.)
            attempt_ms = min(remaining_ms, HAND_OVER_TIMEOUT_MS)
            if self.hand_over(links, attempt_ms) is HandOver.DELIVERED:
                return True
            _pause(_RETRY_PAUSE_MS)  # not answering yet, or any more: try again shortly

    def hand_over(self, links: Sequence[str],
                  timeout_ms: int = HAND_OVER_TIMEOUT_MS) -> HandOver:
        """Send ``links`` to the Weir that is already running; waits at most ``timeout_ms``."""
        payload = encode_message(links, launch_activation_token())
        return _Delivery(self._name, payload, timeout_ms).run()

    def stop_listening(self) -> None:
        """Stop answering new launches but keep the lock (call when Weir starts quitting).

        A launch from now on waits until this Weir has exited and then starts fresh,
        instead of handing its links to a window that is closing.
        """
        self._stop_claiming()  # a quitting Weir must not take over either
        if self._server is not None:
            self._server.close()  # also hangs up on connections not taken yet
        for incoming in self.findChildren(_Incoming):
            try:
                incoming.drop()
            except RuntimeError:
                # PySide lost track of this one's objects after all: still hang up on the
                # rest, and do not turn quitting into an error message.
                pass

    def release(self) -> None:
        """Stop answering and give up the lock (call as Weir exits)."""
        try:
            self.stop_listening()
        finally:
            # Whatever happened above, a launch waiting for this Weir to exit must start.
            if self._lock.isLocked():
                self._lock.unlock()
                _held_here.discard(self._lock_key)

    # ------------------------------------------------------------------- lock

    def _claim(self) -> _Claim:
        if self._lock.tryLock(0):
            _held_here.add(self._lock_key)
            return _Claim.TAKEN
        if self._lock.error() != QLockFile.LockError.LockFailedError:
            return _Claim.UNAVAILABLE
        # removeStaleLockFile() cannot remove the lock of a running Weir: Windows keeps
        # the file open and undeletable, Linux keeps an flock on it.
        if self._left_behind() and self._lock.removeStaleLockFile() and self._lock.tryLock(0):
            _held_here.add(self._lock_key)
            return _Claim.TAKEN
        return _Claim.BUSY

    def _keep_trying_to_claim(self) -> None:
        if self._reclaim_timer is None:
            self._reclaim_timer = QTimer(self)
            self._reclaim_timer.setInterval(_RECLAIM_MS)
            self._reclaim_timer.timeout.connect(self._try_to_claim)
        self._reclaim_timer.start()

    def _stop_claiming(self) -> None:
        if self._reclaim_timer is not None:
            self._reclaim_timer.stop()

    def _try_to_claim(self) -> None:
        if self._claim() is _Claim.TAKEN:
            self._stop_claiming()
            self._become_primary()

    def _left_behind(self) -> bool:
        """True for leftover locks Qt does not recognise as stale on its own."""
        pid, _host, _app = self._lock.getLockInfo()  # (0, "", "") when unreadable
        if pid == os.getpid():
            # Windows may give the process ID of a crashed Weir to this very launch;
            # Qt would then take the lock for one held by a running Weir.
            return self._lock_key not in _held_here
        if pid <= 0:
            try:
                age = time.time() - os.path.getmtime(self._lock.fileName())
            except OSError:
                return False  # gone already: the next attempt takes it
            return abs(age) > _UNREADABLE_LOCK_AGE_S
        return False

    # ------------------------------------------------------------- server side

    def _become_primary(self) -> None:
        # Holding the lock proves no other Weir runs, so a socket left by a crash can go.
        QLocalServer.removeServer(self._name)
        if not self._listen():
            # Later launches would wait in vain for an answer: let them start instead.
            self.release()

    def _listen(self) -> bool:
        if self._server is None:
            self._server = QLocalServer(self)
            # Only this user's programs may talk to us.
            self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            self._server.newConnection.connect(self._on_new_connection)
        if self._server.isListening():
            return True
        return self._server.listen(self._name)

    def _on_new_connection(self) -> None:
        server = self._server
        while server is not None and server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                break
            incoming = _Incoming(socket, self)
            incoming.received.connect(self.activation_requested)
            incoming.begin()
