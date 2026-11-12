"""Keep Weir to one window per user.

Two Weir windows would delete each other's partial downloads and overwrite each other's
settings.json and presets.json. The first launch listens on a per-user local socket (a
named pipe on Windows); a later launch hands its links to it and quits without a window.

Wire protocol (same user, same computer): the new launch sends its links as UTF-8 text,
one per line (possibly none), followed by a NUL byte; the running Weir answers with a
single ACK byte once it has taken them.
"""

from __future__ import annotations

import enum
import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import APP_NAME
from .paths import IS_WINDOWS

# How long a new launch waits for the running Weir to answer before starting on its own.
HAND_OVER_TIMEOUT_MS = 1500

_END = b"\0"
_ACK = b"\x06"
_MAX_MESSAGE = 1 << 20  # a lot of links; anything bigger did not come from Weir
_ASFW_ANY = 0xFFFFFFFF


def default_server_name() -> str:
    """Per-user name, so two people signed in on one computer each get their own Weir."""
    home = os.path.normcase(str(Path.home()))
    digest = hashlib.sha256(os.fsencode(home)).hexdigest()[:12]
    return f"{APP_NAME}-{digest}"


def encode_links(links: Sequence[str]) -> bytes:
    text = "\n".join(link.strip() for link in links if link.strip())
    return text.encode("utf-8", errors="replace").replace(_END, b"") + _END


def decode_links(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


class HandOver(enum.Enum):
    """What happened when a new launch tried to reach the running Weir."""

    DELIVERED = enum.auto()  # the running Weir took the links
    NO_SERVER = enum.auto()  # nothing listens under our name (or only a crash leftover)
    NO_ANSWER = enum.auto()  # something listens but did not answer in time


def _let_running_window_come_forward() -> None:
    """Windows only lets the app the user just started take the foreground; pass that on."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint32(_ASFW_ANY))
    except (AttributeError, OSError):
        pass


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

    def _on_error(self, error: QLocalSocket.LocalSocketError) -> None:
        nobody_there = error in (QLocalSocket.LocalSocketError.ServerNotFoundError,
                                 QLocalSocket.LocalSocketError.ConnectionRefusedError)
        self._finish(HandOver.NO_SERVER if nobody_there and not self._connected
                     else HandOver.NO_ANSWER)


class _Incoming(QObject):
    """One connection from a new launch, until its message is complete.

    Owns its socket and uses only bound slots, so Qt drops every connection when it
    goes away and nothing calls back into Python objects that are being destroyed.
    """

    received = Signal(list)

    def __init__(self, socket: QLocalSocket, parent: QObject) -> None:
        super().__init__(parent)
        self._socket = socket
        self._buffer = bytearray()
        self._done = False
        socket.setParent(self)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(self.deleteLater)

    def begin(self) -> None:
        self._on_ready_read()  # data may already be waiting
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.deleteLater()

    def _on_ready_read(self) -> None:
        if self._done:
            return
        self._buffer += self._socket.readAll().data()
        end = self._buffer.find(_END)
        if end < 0:
            if len(self._buffer) > _MAX_MESSAGE:
                self._done = True
                self._socket.abort()
            return
        self._done = True
        links = decode_links(bytes(self._buffer[:end]))
        self._buffer.clear()
        # Answer first so the new launch can quit at once; it closes the connection.
        self._socket.write(_ACK)
        self._socket.flush()
        self.received.emit(links)


class SingleInstance(QObject):
    """Makes the first Weir the only one; later launches pass their links to it.

    Call :meth:`start` before creating settings or windows. When it returns True another
    Weir has the links and this launch should exit. Otherwise this Weir is now the one that
    answers, and :attr:`activation_requested` fires whenever Weir is launched again.
    """

    # Links from the new launch (may be empty): add them and bring the window forward.
    activation_requested = Signal(list)

    def __init__(self, server_name: str | None = None, timeout_ms: int = HAND_OVER_TIMEOUT_MS,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = server_name or default_server_name()
        self._timeout_ms = timeout_ms
        self._server: QLocalServer | None = None

    @property
    def server_name(self) -> str:
        return self._name

    @property
    def is_listening(self) -> bool:
        return self._server is not None and self._server.isListening()

    def start(self, links: Sequence[str] = ()) -> bool:
        """Become the running Weir, or hand ``links`` to the one already running.

        Returns True when another Weir took the links (this launch should then quit).
        Waits at most about twice the timeout. Never refuses to start: if the name cannot
        be claimed, Weir simply runs without the one-window guard.
        """
        outcome = self.hand_over(links)
        if outcome is HandOver.DELIVERED:
            return True
        if outcome is HandOver.NO_SERVER:
            if self._listen():
                return False
            # The name is taken although nothing answered a moment ago: another launch
            # may have claimed it just now, so try handing over once more.
            if self.hand_over(links) is HandOver.DELIVERED:
                return True
        # Nobody answers: a crashed Weir left its name behind, or the running one is stuck.
        # Start normally and take the name over, so the next launch reaches this one.
        QLocalServer.removeServer(self._name)
        self._listen()
        return False

    def hand_over(self, links: Sequence[str]) -> HandOver:
        """Send ``links`` to the Weir that is already running; waits at most the timeout."""
        return _Delivery(self._name, encode_links(links), self._timeout_ms).run()

    def close(self) -> None:
        """Stop answering new launches (call when Weir is quitting)."""
        if self._server is not None:
            self._server.close()

    # ------------------------------------------------------------- server side

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
