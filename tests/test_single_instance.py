"""One Weir per user: the first launch takes the lock and listens, later launches hand
their links over.

Some tests start real launches in separate processes: this file doubles as the script
they run (see _child at the bottom).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import types
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtTest import QTest

from core import single_instance
from core.single_instance import (HandOver, SingleInstance, decode_message, encode_message,
                                  socket_address)

ROOT = Path(__file__).resolve().parent.parent
LINKS = ["https://www.youtube.com/watch?v=abc", "https://vimeo.com/123", "https://example.com/é"]
TOKEN = "2c5a9f1e-7d3b-4a8e-9c1f-0b6d4e2a8f37_TIME1234"  # like a desktop's activation token


def _unique_name() -> str:
    return f"Weir-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _spin(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QTest.qWait(10)


def _spin_until(condition: Callable[[], bool], ms: int = 5000) -> bool:
    end = time.monotonic() + ms / 1000
    while not condition() and time.monotonic() < end:
        QTest.qWait(10)
    return condition()


def _record(instance: SingleInstance, received: list[list[str]]) -> None:
    """Collect the links ``instance`` is handed (without the activation tokens)."""
    instance.activation_requested.connect(lambda links, _token: received.append(links))


def _deliveries(received: Sequence[Sequence[str]]) -> list[list[str]]:
    """What the running Weir was handed, each delivery once. start() gives up on an
    attempt after 1.5 s and tries again, and on a busy computer the attempt it gave up on
    can still get through; the window then just gets the same links twice."""
    return [list(item) for item in dict.fromkeys(tuple(item) for item in received)]


def _connect_client(instance: SingleInstance) -> QLocalSocket:
    """A raw connection to the running Weir, so a test can send its message bit by bit.
    Returns once the running Weir has taken the connection."""
    taken = len(instance.findChildren(single_instance._Incoming))
    client = QLocalSocket()
    client.connectToServer(instance.server_name)
    assert _spin_until(lambda: client.state() == QLocalSocket.LocalSocketState.ConnectedState
                       and len(instance.findChildren(single_instance._Incoming)) > taken)
    return client


def _forget_python_side_of_connections(instance: SingleInstance) -> None:
    """Do on purpose what PySide rarely does on its own: mark the Python objects for the
    running Weir's open connections as deleted while the connections stay open."""
    sockets = [sock for incoming in instance.findChildren(single_instance._Incoming)
               for sock in incoming.findChildren(QLocalSocket)]
    assert sockets
    for sock in sockets:
        shiboken6.invalidate(sock)
        assert not shiboken6.isValid(sock)


def _set_age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


@pytest.fixture
def make_instance(qapp, tmp_path) -> Iterator[Callable[..., SingleInstance]]:
    """SingleInstance objects with a private name and lock; both ends live in this thread."""
    created: list[SingleInstance] = []

    def make(name: str, wait_ms: int = 5000, lock_path: Path | None = None) -> SingleInstance:
        # Off Windows a server_name is an absolute socket path, and tmp_path / "/run/..."
        # would drop tmp_path: name the lock after the last part, so make(name) and
        # make(first.server_name) share one lock in tmp_path on every system.
        lock = lock_path or tmp_path / f"{Path(name).name}.lock"
        instance = SingleInstance(name, lock_path=lock, wait_ms=wait_ms)
        created.append(instance)
        return instance

    yield make
    for instance in created:
        instance.release()


@pytest.fixture
def running(make_instance) -> tuple[SingleInstance, list[list[str]]]:
    """A first launch that is listening, plus everything it was handed."""
    first = make_instance(_unique_name())
    assert first.start() is False
    received: list[list[str]] = []
    _record(first, received)
    return first, received


def test_default_server_name_is_per_user(monkeypatch) -> None:
    name = single_instance.default_server_name()
    assert name.startswith("Weir-")
    assert name == single_instance.default_server_name()
    monkeypatch.setattr(single_instance.Path, "home", classmethod(lambda cls: Path("/home/someone-else")))
    assert single_instance.default_server_name() != name


def test_default_lock_is_in_the_settings_folder(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(single_instance, "config_dir", lambda: tmp_path)
    path = single_instance.default_lock_path()
    assert path.parent == tmp_path
    assert path == single_instance.default_lock_path()


def _fake_computer(monkeypatch, machine_id: bytes, host: str) -> types.SimpleNamespace:
    computer = types.SimpleNamespace(machineUniqueId=lambda: machine_id,
                                     machineHostName=lambda: computer.host, host=host)
    monkeypatch.setattr(single_instance, "QSysInfo", computer)
    return computer


def test_default_lock_is_named_after_the_computer_not_its_host_name(monkeypatch, tmp_path) -> None:
    """On Linux the host name can change while Weir runs (NetworkManager may take one
    from DHCP); the computer's unique ID does not."""
    monkeypatch.setattr(single_instance, "config_dir", lambda: tmp_path)
    computer = _fake_computer(monkeypatch, b"c08911de-0000-4c0d-bd8c-3e2e2d936df0", "fedora")
    path = single_instance.default_lock_path()
    computer.host = "dhcp-10-0-0-7.example.net"
    assert single_instance.default_lock_path() == path

    _fake_computer(monkeypatch, b"another-computer", "fedora")
    assert single_instance.default_lock_path() != path


def test_lock_left_under_an_old_host_name_is_a_leftover(make_instance, tmp_path) -> None:
    """A Weir that crashed before the host name changed leaves its lock in the same file
    (it is named after the computer's ID); Qt goes by the ID inside it too, so it frees it."""
    probe = QLockFile(str(tmp_path / "probe.lock"))
    assert probe.tryLock(0)
    lines = (tmp_path / "probe.lock").read_bytes().split(b"\n")  # pid, app, host, ID, ...
    probe.unlock()
    if len(lines) < 4 or not lines[3]:
        pytest.skip("this computer has no ID; the lock is then named after the host name")
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    lines[0], lines[2] = str(gone.pid).encode(), b"old-host-name"
    lock_path = tmp_path / "left.lock"
    lock_path.write_bytes(b"\n".join(lines))

    instance = make_instance(_unique_name(), wait_ms=10_000, lock_path=lock_path)
    began = time.monotonic()
    assert instance.start() is False
    assert time.monotonic() - began < 3
    assert instance.holds_lock


def test_default_lock_goes_by_host_name_when_there_is_no_computer_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(single_instance, "config_dir", lambda: tmp_path)
    computer = _fake_computer(monkeypatch, b"", "laptop")
    path = single_instance.default_lock_path()
    assert path.parent == tmp_path
    computer.host = "desktop"
    assert single_instance.default_lock_path() != path


def test_host_name_change_while_weir_runs_does_not_start_a_second_weir(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(single_instance, "config_dir", lambda: tmp_path)
    computer = _fake_computer(monkeypatch, b"c08911de-0000-4c0d-bd8c-3e2e2d936df0", "fedora")
    name = _unique_name()
    started: list[SingleInstance] = []
    try:
        first = SingleInstance(name, wait_ms=5000)
        started.append(first)
        assert first.start() is False
        assert first.holds_lock and first.is_listening
        received: list[list[str]] = []
        _record(first, received)

        computer.host = "dhcp-10-0-0-7.example.net"  # Wi-Fi connected
        second = SingleInstance(name, wait_ms=5000)
        started.append(second)
        assert second.start(LINKS) is True
        assert not second.holds_lock
        assert first.is_listening
        assert _deliveries(received) == [LINKS]
    finally:
        for instance in reversed(started):
            instance.release()


def test_message_round_trip() -> None:
    assert decode_message(encode_message(LINKS)[:-1]) == (LINKS, "")
    assert encode_message([]) == b"\0"
    assert decode_message(encode_message([" ", ""])[:-1]) == ([], "")
    assert decode_message(encode_message(LINKS, TOKEN)[:-1]) == (LINKS, TOKEN)
    assert decode_message(encode_message([], TOKEN)[:-1]) == ([], TOKEN)


def test_message_from_a_launch_before_the_token_still_reads() -> None:
    assert decode_message(b"https://a.example/1\nhttps://b.example/2") == (
        ["https://a.example/1", "https://b.example/2"], "")


def test_weir_from_before_the_token_finds_only_the_links() -> None:
    """An update can leave an older Weir running: it takes every line for text that may
    hold links, and must find none in the token line."""
    from ui.widgets import extract_links_from_text

    data = encode_message(LINKS, TOKEN)[:-1]
    lines = [line.strip() for line in data.decode("utf-8").splitlines() if line.strip()]
    assert extract_links_from_text("\n".join(lines)) == LINKS


@pytest.mark.parametrize("token", ["two\nlines", "with space", "bell\x07", "é", "x" * 5000])
def test_what_cannot_be_a_token_is_not_sent(token) -> None:
    assert encode_message(LINKS, token) == encode_message(LINKS)


def test_link_cannot_pass_for_a_token() -> None:
    sneaky = single_instance._TOKEN_MARK + "made-up"
    links, token = decode_message(encode_message([sneaky, LINKS[0]])[:-1])
    assert token == ""
    assert links[-1] == LINKS[0]


@pytest.mark.parametrize("token", [TOKEN, None])
def test_launch_hands_over_its_activation_token(running, make_instance, monkeypatch, token) -> None:
    """On Wayland the desktop gives the token to the new launch, but the running Weir is
    the one that has to bring its window forward."""
    first, received = running
    tokens: list[str] = []
    first.activation_requested.connect(lambda _links, given: tokens.append(given))
    if token is None:
        monkeypatch.delenv("XDG_ACTIVATION_TOKEN", raising=False)
    else:
        monkeypatch.setenv("XDG_ACTIVATION_TOKEN", token)

    assert make_instance(first.server_name).start(LINKS) is True
    assert _deliveries(received) == [LINKS]
    assert set(tokens) == {token or ""}


def test_socket_address_keeps_a_bare_pipe_name_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(single_instance, "IS_WINDOWS", True)
    assert socket_address("Weir-0123456789ab") == "Weir-0123456789ab"


def test_socket_address_is_one_absolute_path_elsewhere(monkeypatch) -> None:
    """A bare name goes in the socket folder, the same for the running Weir and every
    launch; an address passed back in stays as it is."""
    monkeypatch.setattr(single_instance, "IS_WINDOWS", False)
    monkeypatch.setattr(single_instance, "_socket_dir", lambda: f"/run/user/{2**32 - 2}")
    name = single_instance.default_server_name()
    address = socket_address(name)
    assert address == f"/run/user/{2**32 - 2}/{name}"
    assert socket_address(address) == address
    assert len(os.fsencode(address)) <= 107  # what a Unix socket path can hold


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes do not live in a folder")
def test_socket_folder_does_not_follow_tmpdir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    address = socket_address("Weir-0123456789ab")
    assert address.startswith("/") and address.endswith("/Weir-0123456789ab")
    assert not address.startswith(str(tmp_path))
    monkeypatch.delenv("TMPDIR")
    assert socket_address("Weir-0123456789ab") == address


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes do not live in a folder")
def test_socket_folder_is_the_users_private_runtime_folder(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(single_instance, "_RUNTIME_ROOT", str(tmp_path))
    mine = tmp_path / str(os.getuid())
    assert single_instance._socket_dir() == "/tmp"  # no runtime folder (no systemd/elogind)

    mine.mkdir()
    os.chmod(mine, 0o700)
    assert single_instance._socket_dir() == str(mine)

    os.chmod(mine, 0o755)  # others could look in
    assert single_instance._socket_dir() == "/tmp"

    os.chmod(mine, 0o700)
    mine.rename(tmp_path / "elsewhere")
    mine.symlink_to(tmp_path / "elsewhere")  # not the folder the system made
    assert single_instance._socket_dir() == "/tmp"


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes do not live in a folder")
def test_launch_with_another_tmpdir_still_finds_the_running_weir(make_instance, monkeypatch,
                                                                tmp_path) -> None:
    """Qt would put a bare socket name in TMPDIR, and a launch from a terminal can have
    another one (export TMPDIR=~/tmp in .bashrc): it then waited and started a second Weir."""
    name = _unique_name()
    for folder in ("tmp-desktop", "tmp-terminal"):
        (tmp_path / folder).mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp-desktop"))
    first = make_instance(name)
    assert first.start() is False
    received: list[list[str]] = []
    _record(first, received)

    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp-terminal"))
    began = time.monotonic()
    assert make_instance(name, wait_ms=4000).start(LINKS) is True
    assert time.monotonic() - began < 3
    assert _deliveries(received) == [LINKS]


def test_launches_made_from_a_name_or_its_address_share_one_lock(make_instance, monkeypatch,
                                                                 tmp_path) -> None:
    """Many tests make the next launch from first.server_name, which off Windows is an
    absolute socket path: it must still wait on the first launch's lock, in tmp_path,
    or it takes a lock of its own and starts a second Weir (seen only on Linux CI)."""
    monkeypatch.setattr(single_instance, "IS_WINDOWS", False)
    monkeypatch.setattr(single_instance, "_socket_dir", lambda: "/run/user/1000")
    name = _unique_name()
    first = make_instance(name)
    assert first.server_name == f"/run/user/1000/{name}"
    second = make_instance(first.server_name)
    # Nothing listens here; this only looks at which lock file each one would take.
    expected = os.path.normcase(str(tmp_path / f"{name}.lock"))
    assert first._lock_key == expected
    assert second._lock_key == expected


def test_first_launch_takes_the_lock_and_listens(running) -> None:
    first, received = running
    assert first.holds_lock
    assert first.is_listening
    assert received == []


def test_second_launch_hands_links_to_the_first(running, make_instance) -> None:
    first, received = running
    second = make_instance(first.server_name)

    assert second.start(LINKS) is True
    assert not second.is_listening
    assert not second.holds_lock
    assert _deliveries(received) == [LINKS]


def test_second_launch_without_links_still_brings_the_window_forward(running, make_instance) -> None:
    first, received = running

    assert make_instance(first.server_name).start([]) is True
    assert make_instance(first.server_name).start(LINKS[:1]) is True
    assert _deliveries(received) == [[], LINKS[:1]]


def test_released_instance_lets_the_next_launch_start(running, make_instance) -> None:
    first, _received = running
    first.release()
    assert not first.holds_lock

    second = make_instance(first.server_name)
    assert second.start(LINKS) is False
    assert second.holds_lock
    assert second.is_listening


def test_socket_without_a_lock_does_not_hold_up_the_launch(make_instance) -> None:
    """Only the lock holder counts as the running Weir, whatever listens under the name."""
    name = _unique_name()
    stray = QLocalServer()  # accepts connections but never answers
    assert stray.listen(socket_address(name))
    instance = make_instance(name, wait_ms=10_000)
    try:
        began = time.monotonic()
        assert instance.start(LINKS) is False
        assert time.monotonic() - began < 3
        assert instance.holds_lock
        assert instance.is_listening
    finally:
        instance.release()
        stray.close()


def test_hand_over_notices_when_the_running_weir_hangs_up(make_instance) -> None:
    """A Weir that closes the connection instead of answering (it is quitting, or has
    crashed) must not keep the new launch waiting for the rest of the attempt."""
    name = _unique_name()
    server = QLocalServer()
    assert server.listen(socket_address(name))

    def hang_up() -> None:
        while server.hasPendingConnections():
            server.nextPendingConnection().abort()

    server.newConnection.connect(hang_up)
    instance = make_instance(name)
    try:
        began = time.monotonic()
        assert instance.hand_over(LINKS, timeout_ms=10_000) is HandOver.NO_ANSWER
        assert time.monotonic() - began < 3
    finally:
        server.close()


def test_connection_left_unanswered_is_tried_again(running, make_instance) -> None:
    """On Windows the running Weir can, rarely, accept a connection and never hear from
    it (seen when several launches connect as it starts listening). The launch must try
    a new connection rather than wait out the whole time and then run alone."""
    first, received = running
    server = first._server
    assert server is not None
    lost: list[QLocalSocket] = []

    def lose_the_first_connection() -> None:
        if not lost:
            lost.append(server.nextPendingConnection())  # kept open, never read
        first._on_new_connection()

    server.newConnection.disconnect(first._on_new_connection)
    server.newConnection.connect(lose_the_first_connection)
    second = make_instance(first.server_name, wait_ms=10_000)
    began = time.monotonic()
    assert second.start(LINKS) is True
    assert time.monotonic() - began < 5
    assert _deliveries(received) == [LINKS]


@pytest.mark.parametrize("answers", ["silent", "not listening"])
def test_stuck_weir_does_not_block_a_new_launch_forever(make_instance, tmp_path, answers) -> None:
    """Another Weir holds the lock but never takes the links: start alone after a while."""
    name = _unique_name()
    lock_path = tmp_path / "held.lock"
    held = QLockFile(str(lock_path))
    assert held.tryLock(0)
    silent = QLocalServer()
    if answers == "silent":
        assert silent.listen(socket_address(name))
    instance = make_instance(name, wait_ms=1000, lock_path=lock_path)
    try:
        began = time.monotonic()
        assert instance.start(LINKS) is False
        assert 0.9 <= time.monotonic() - began < 6
        # It runs without the lock, so it does not answer later launches either.
        assert not instance.holds_lock
        assert not instance.is_listening
        assert held.isLocked() and lock_path.exists()
    finally:
        silent.close()
        held.unlock()


def test_weir_that_started_alone_and_quits_does_not_take_over(make_instance, tmp_path,
                                                             monkeypatch) -> None:
    """It would answer launches with a window that is closing."""
    monkeypatch.setattr(single_instance, "_RECLAIM_MS", 50)
    lock_path = tmp_path / "held.lock"
    held = QLockFile(str(lock_path))
    assert held.tryLock(0)
    instance = make_instance(_unique_name(), wait_ms=300, lock_path=lock_path)
    assert instance.start() is False
    instance.stop_listening()  # starts quitting

    held.unlock()  # the stuck Weir is gone
    _spin(400)
    assert not instance.holds_lock
    assert not instance.is_listening


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes vanish with their process")
def test_name_left_behind_by_a_crash_is_taken_over(make_instance) -> None:
    name = _unique_name()
    path = socket_address(name)
    leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    leftover.bind(path)
    leftover.close()  # like a crash: the socket file stays, nobody listens
    assert os.path.exists(path)

    first = make_instance(name)
    assert first.start() is False
    assert first.is_listening
    received: list[list[str]] = []
    _record(first, received)

    assert make_instance(name).start(LINKS) is True
    assert _deliveries(received) == [LINKS]


def test_lock_with_this_launchs_own_process_id_is_a_leftover(make_instance, tmp_path) -> None:
    """Windows may give a crashed Weir's process ID to the next launch; Qt alone would
    then take the leftover lock for a running Weir's and keep the launch waiting."""
    probe = QLockFile(str(tmp_path / "probe.lock"))
    assert probe.tryLock(0)
    contents = (tmp_path / "probe.lock").read_bytes()  # names this process
    probe.unlock()
    lock_path = tmp_path / "left.lock"
    lock_path.write_bytes(contents)  # nobody holds it open: as after a crash

    instance = make_instance(_unique_name(), wait_ms=10_000, lock_path=lock_path)
    began = time.monotonic()
    assert instance.start() is False
    assert time.monotonic() - began < 3
    assert instance.holds_lock
    assert instance.is_listening


@pytest.mark.parametrize("age_s, taken_over", [(3600, True), (0, False)])
def test_unreadable_lock_is_a_leftover_once_it_is_old(make_instance, tmp_path, age_s, taken_over) -> None:
    """A power cut can leave an empty lock file; a fresh one belongs to a starting Weir."""
    lock_path = tmp_path / "empty.lock"
    lock_path.write_bytes(b"")
    _set_age(lock_path, age_s)

    instance = make_instance(_unique_name(), wait_ms=500, lock_path=lock_path)
    assert instance.start() is False
    assert instance.holds_lock is taken_over
    assert instance.is_listening is taken_over


def test_stop_listening_keeps_the_lock_and_the_next_launch_waits_for_it(running, make_instance) -> None:
    """A launch while Weir is quitting must not be handed to the closing window."""
    first, received = running
    first.stop_listening()
    assert not first.is_listening
    assert first.holds_lock

    QTimer.singleShot(800, first.release)  # the quitting Weir exits a moment later
    second = make_instance(first.server_name, wait_ms=10_000)
    began = time.monotonic()
    assert second.start(LINKS) is False
    assert time.monotonic() - began >= 0.7
    assert second.holds_lock
    assert second.is_listening
    assert received == []


def test_stop_listening_hangs_up_on_a_launch_in_progress(running) -> None:
    first, received = running
    client = QLocalSocket()
    client.connectToServer(first.server_name)
    _spin(100)
    assert client.state() == QLocalSocket.LocalSocketState.ConnectedState
    client.write(b"https://example.com/half")  # the rest has not arrived yet
    _spin(150)
    assert client.state() == QLocalSocket.LocalSocketState.ConnectedState

    first.stop_listening()
    _spin(150)
    assert client.state() == QLocalSocket.LocalSocketState.UnconnectedState
    assert received == []
    assert first.holds_lock


def test_incomplete_message_is_ignored(running, make_instance) -> None:
    first, received = running
    client = QLocalSocket()
    client.connectToServer(first.server_name)
    _spin(100)
    assert client.state() == QLocalSocket.LocalSocketState.ConnectedState
    client.write(b"https://example.com/no-end")  # no terminator
    _spin(100)
    client.abort()
    _spin(100)
    assert received == []

    # The running Weir still answers the next launch normally.
    assert make_instance(first.server_name).start(LINKS) is True
    assert _deliveries(received) == [LINKS]


def test_connection_python_lost_track_of_is_still_answered(running) -> None:
    first, received = running
    client = _connect_client(first)
    message = encode_message(LINKS)
    client.write(message[:10])  # the running Weir sees the start of the message
    _spin(150)
    _forget_python_side_of_connections(first)

    client.write(message[10:])
    assert _spin_until(lambda: client.bytesAvailable() > 0)
    assert client.readAll().data() == b"\x06"
    assert _deliveries(received) == [LINKS]


def test_stop_listening_hangs_up_even_when_python_lost_track_of_a_connection(running) -> None:
    """Otherwise quitting shows an error, keeps the lock and, on Windows, keeps the name
    busy for the next launch."""
    first, received = running
    client = _connect_client(first)
    client.write(b"https://example.com/half")
    _spin(150)
    _forget_python_side_of_connections(first)

    first.stop_listening()
    assert _spin_until(lambda: client.state() == QLocalSocket.LocalSocketState.UnconnectedState)
    assert first.holds_lock
    first.release()
    assert not first.holds_lock
    assert received == []


def test_connection_that_cannot_be_hung_up_does_not_stop_the_rest(running, monkeypatch) -> None:
    first, _received = running
    for _ in range(3):  # connections the running Weir has taken
        single_instance._Incoming(QLocalSocket(), first)
    tried: list[object] = []

    def drop(incoming) -> None:
        tried.append(incoming)
        if len(tried) == 1:
            raise RuntimeError("Internal C++ object (PySide6.QtNetwork.QLocalSocket) already deleted.")

    monkeypatch.setattr(single_instance._Incoming, "drop", drop)
    first.release()
    assert len(tried) == 3
    assert not first.holds_lock


# ----------------------------------------------------------------- main.py wiring


def _fake_window():
    """What main.py uses of MainWindow: the link box and the quitting signal."""
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget

    from ui.widgets import UrlEdit

    class FakeWindow(QWidget):
        shutdown_started = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.url_edit = UrlEdit(self)

    return FakeWindow()


def test_handed_over_links_restore_the_window(qapp) -> None:
    import main

    window = _fake_window()
    window.showMinimized()
    main._show_handed_over_links(window, ["not a link", LINKS[0], LINKS[0]])  # type: ignore[arg-type]
    assert window.url_edit.links() == [LINKS[0]]
    assert window.isVisible()
    assert not window.isMinimized()
    window.close()


def _watch_activation(window) -> list[str | None]:
    """The XDG_ACTIVATION_TOKEN that Qt would find each time the window is activated."""
    seen: list[str | None] = []
    window.activateWindow = lambda: seen.append(os.environ.get("XDG_ACTIVATION_TOKEN"))
    return seen


def test_handed_over_token_is_there_when_the_window_is_activated(qapp, monkeypatch) -> None:
    """Qt on Wayland takes the token from the environment as it activates the window."""
    import main

    monkeypatch.delenv("XDG_ACTIVATION_TOKEN", raising=False)
    window = _fake_window()
    seen = _watch_activation(window)
    main._show_handed_over_links(window, [LINKS[0]], TOKEN)  # type: ignore[arg-type]
    assert seen == [TOKEN]
    assert "XDG_ACTIVATION_TOKEN" not in os.environ  # not passed on to programs Weir starts

    main._show_handed_over_links(window, [LINKS[1]], "")  # type: ignore[arg-type]
    assert seen == [TOKEN, None]
    assert window.url_edit.links() == LINKS[:2]
    window.close()


def test_running_weir_activates_its_window_with_the_new_launchs_token(
        running, make_instance, monkeypatch) -> None:
    import main

    first, _received = running
    window = _fake_window()
    seen = _watch_activation(window)
    main._answer_new_launches(first, window)  # type: ignore[arg-type]
    monkeypatch.delenv("XDG_ACTIVATION_TOKEN", raising=False)
    # Only the new launch has the token (both ends share this test's environment).
    monkeypatch.setattr(single_instance, "launch_activation_token", lambda: TOKEN)

    assert make_instance(first.server_name).start(LINKS[:1]) is True
    assert seen and set(seen) == {TOKEN}
    assert window.url_edit.links() == LINKS[:1]
    window.close()


def test_window_that_starts_quitting_gets_no_more_launches(running, make_instance) -> None:
    import main

    first, _received = running
    window = _fake_window()
    main._answer_new_launches(first, window)  # type: ignore[arg-type]
    assert make_instance(first.server_name).start(LINKS[:1]) is True
    assert window.url_edit.links() == LINKS[:1]

    window.shutdown_started.emit()
    assert not first.is_listening
    assert first.holds_lock
    # The next launch is not handed to the closing window; it waits for the lock instead
    # (and is not held up by the finished connection of the launch before).
    began = time.monotonic()
    assert make_instance(first.server_name, wait_ms=500).start(LINKS[1:2]) is False
    assert time.monotonic() - began < 3
    assert window.url_edit.links() == LINKS[:1]
    window.close()


def test_attempt_given_up_on_can_still_get_through(running, make_instance, monkeypatch) -> None:
    """Why the tests count each delivery once: start() gives up on a slow attempt and
    tries again, and what the abandoned attempt sent can still reach the running Weir.
    The window then gets the same links twice, which does no harm."""
    import main

    first, received = running
    window = _fake_window()
    main._answer_new_launches(first, window)  # type: ignore[arg-type]
    server = first._server
    assert server is not None
    held_back: list[bool] = []

    def take_the_first_connection_late() -> None:
        if not held_back:
            held_back.append(True)  # left waiting until the launch gives up on it
            return
        first._on_new_connection()  # now takes it along with the next attempt

    server.newConnection.disconnect(first._on_new_connection)
    server.newConnection.connect(take_the_first_connection_late)
    monkeypatch.setattr(single_instance, "HAND_OVER_TIMEOUT_MS", 300)
    links = LINKS[:2]
    assert make_instance(first.server_name, wait_ms=10_000).start(links) is True
    _spin(300)
    if sys.platform == "win32":  # still reads what a closed connection sent
        assert len(received) >= 2
    assert _deliveries(received) == [links]
    assert window.url_edit.links() == links
    window.close()


# ------------------------------------------------- launches in separate processes


class _Launch:
    """A Weir launch in its own process, reporting through marker files in ``directory``."""

    def __init__(self, directory: Path, tag: str, name: str, lock: Path,
                 links: Sequence[str] = (), wait_ms: int = 15_000) -> None:
        self.directory = directory
        self.tag = tag
        path = os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p)
        env = dict(os.environ, PYTHONPATH=path, QT_QPA_PLATFORM="offscreen")
        self._log_path = directory / f"{tag}.log"
        with self._log_path.open("wb") as log:
            self.process = subprocess.Popen(
                [sys.executable, __file__, str(directory), tag, name, str(lock), str(wait_ms),
                 *links],
                stdout=log, stderr=subprocess.STDOUT, env=env, cwd=ROOT,
            )

    def wait_for(self, what: str, timeout: float = 60) -> object:
        path = self.directory / f"{self.tag}.{what}"
        end = time.monotonic() + timeout
        while True:
            try:
                return json.loads(path.read_bytes())
            except FileNotFoundError:
                pass  # not reported yet
            except PermissionError:
                pass  # Windows: a file just moved into place can refuse to open for a
                # moment (antivirus scanning it, say)
            if self.process.poll() is not None and not path.exists():
                pytest.fail(f"launch {self.tag} ended before {what!r}:\n{self.output()}")
            if time.monotonic() > end:
                pytest.fail(f"launch {self.tag} never reported {what!r}:\n{self.output()}")
            time.sleep(0.02)

    def finish(self, timeout: float = 60) -> None:
        try:
            self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            pytest.fail(f"launch {self.tag} did not exit:\n{self.output()}")

    def crash(self) -> None:
        """Make the launch die on the spot. Not process.kill(): on Windows a venv's
        python.exe only starts the real interpreter, which would live on for a moment."""
        (self.directory / "crash").touch()
        self.process.wait(60)

    def output(self) -> str:
        return self._log_path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def launch(tmp_path) -> Iterator[Callable[..., _Launch]]:
    started: list[_Launch] = []

    def start(tag: str, name: str, lock: Path, links: Sequence[str] = ()) -> _Launch:
        child = _Launch(tmp_path, tag, name, lock, links)
        started.append(child)
        return child

    yield start
    (tmp_path / "exit").touch()
    for child in started:
        if child.process.poll() is None:
            child.finish()


def test_launches_at_the_same_moment_make_exactly_one_running_weir(launch, tmp_path) -> None:
    name, lock = _unique_name(), tmp_path / "weir.lock"
    links = {tag: [f"https://example.com/{tag}"] for tag in ("a", "b", "c")}
    children = [launch(tag, name, lock, links[tag]) for tag in links]
    for child in children:
        child.wait_for("ready")
    (tmp_path / "go").touch()  # all of them call start() together

    results = {child.tag: child.wait_for("result") for child in children}
    assert sorted(results.values()) == ["handed", "handed", "primary"], results
    primary = next(child for child in children if results[child.tag] == "primary")
    (tmp_path / "exit").touch()
    primary.finish()
    received = primary.wait_for("received")
    # As a set: a launch that tried again may, rarely, get its links through twice.
    assert {tuple(item) for item in received} == {
        tuple(links[tag]) for tag in links if tag != primary.tag
    }


def test_lock_left_by_a_crashed_weir_does_not_block_the_next_launch(launch, make_instance, tmp_path) -> None:
    name, lock = _unique_name(), tmp_path / "weir.lock"
    (tmp_path / "go").touch()
    child = launch("crashes", name, lock)
    assert child.wait_for("result") == "primary"
    child.crash()
    assert lock.exists()  # the crash left it behind

    instance = make_instance(name, wait_ms=15_000, lock_path=lock)
    began = time.monotonic()
    assert instance.start(LINKS) is False
    assert time.monotonic() - began < 5
    assert instance.holds_lock
    assert instance.is_listening


@pytest.mark.parametrize("then", ["quit", "crash"])
def test_launch_waiting_for_an_answer_notices_the_running_weir_go_away(
        launch, make_instance, tmp_path, then) -> None:
    """The running Weir is busy for a moment, then quits (or crashes) instead of answering:
    the new launch must notice the hang-up and start once the lock is free, not wait for
    the full time and then run without the lock."""
    name, lock = _unique_name(), tmp_path / "weir.lock"
    (tmp_path / "go").touch()
    child = launch("busy", name, lock)
    assert child.wait_for("result") == "primary"
    (tmp_path / f"stall-then-{then}").touch()
    child.wait_for("stalled")  # it stays unresponsive for 2 s from now

    instance = make_instance(name, wait_ms=20_000, lock_path=lock)
    began = time.monotonic()
    assert instance.start(LINKS) is False
    assert time.monotonic() - began < 12
    assert instance.holds_lock
    assert instance.is_listening
    child.finish()
    if then == "quit":
        assert child.wait_for("received") == []  # not handed to the quitting Weir


def test_weir_that_runs_for_a_long_time_keeps_its_lock(launch, make_instance, tmp_path) -> None:
    name, lock = _unique_name(), tmp_path / "weir.lock"
    (tmp_path / "go").touch()
    child = launch("long", name, lock)
    assert child.wait_for("result") == "primary"
    _set_age(lock, 3600)  # Qt calls a lock older than 30 s stale unless told otherwise

    instance = make_instance(name, wait_ms=15_000, lock_path=lock)
    assert instance.start(LINKS) is True
    assert not instance.holds_lock
    (tmp_path / "exit").touch()
    child.finish()
    assert _deliveries(child.wait_for("received")) == [LINKS]


@pytest.mark.parametrize("then", ["exits", "crashes"])
def test_weir_that_started_beside_a_stuck_one_takes_over_once_it_is_gone(
        launch, make_instance, tmp_path, then) -> None:
    """A launch gives up waiting for a stuck Weir and runs without the lock. Once the stuck
    one is gone it must take the lock and answer launches: before, the next launch took
    the freed lock and ran as a second Weir beside it, and so did every launch after."""
    name, lock = _unique_name(), tmp_path / "weir.lock"
    (tmp_path / "go").touch()
    child = launch("stuck", name, lock)
    assert child.wait_for("result") == "primary"
    (tmp_path / "hang").touch()
    child.wait_for("hung")  # holds the lock, answers nothing

    alone = make_instance(name, wait_ms=1000, lock_path=lock)
    assert alone.start(LINKS) is False
    assert not alone.holds_lock
    assert not alone.is_listening
    received: list[list[str]] = []
    _record(alone, received)

    if then == "crashes":
        child.crash()
    else:
        (tmp_path / "exit").touch()
        child.finish()
    assert _spin_until(lambda: alone.holds_lock and alone.is_listening, ms=15_000)

    assert make_instance(name, lock_path=lock).start(LINKS[:1]) is True
    assert _deliveries(received) == [LINKS[:1]]


def test_launch_that_is_never_told_to_start_still_exits(launch, tmp_path) -> None:
    """Harness: when a test fails before "go", its launches must not hold up teardown."""
    child = launch("idle", _unique_name(), tmp_path / "weir.lock")
    child.wait_for("ready")
    (tmp_path / "exit").touch()
    child.finish(timeout=20)


def test_report_that_cannot_be_opened_yet_is_read_again(launch, tmp_path, monkeypatch) -> None:
    """Harness: on Windows a file just moved into place can refuse to open for a moment."""
    child = launch("shy", _unique_name(), tmp_path / "weir.lock")
    real_read_bytes = Path.read_bytes
    refused: list[Path] = []

    def read_bytes(self: Path) -> bytes:
        if self.name == "shy.ready" and self.exists() and len(refused) < 3:
            refused.append(self)
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert child.wait_for("ready") is None
    assert len(refused) == 3


def _child(directory: Path, tag: str, name: str, lock: str, wait_ms: int, links: list[str]) -> None:
    """One Weir launch: waits for "go", starts, and as the running Weir collects what it
    is handed until "exit" appears."""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication([])

    def mark(what: str, value: object = None) -> None:
        part = directory / f"{tag}.{what}.part"
        part.write_bytes(json.dumps(value).encode("utf-8"))
        os.replace(part, directory / f"{tag}.{what}")  # appears complete or not at all

    mark("ready")
    go, done = directory / "go", directory / "exit"
    give_up = time.monotonic() + 120
    while not go.exists():
        if done.exists() or time.monotonic() > give_up:
            return  # the test ended (or failed) before telling the launches to start
        time.sleep(0.0005)
    instance = SingleInstance(name, lock_path=lock, wait_ms=wait_ms)
    began = time.monotonic()
    if instance.start(links):
        mark("result", "handed")
        return
    received: list[list[str]] = []
    _record(instance, received)
    if instance.holds_lock and instance.is_listening:
        mark("result", "primary")
    else:  # runs without the guard: gave up waiting, or could not listen
        mark("result", f"alone: lock={instance.holds_lock} listening={instance.is_listening}"
                       f" after {time.monotonic() - began:.1f} s")

    give_up = time.monotonic() + 120

    def poll() -> None:
        if (directory / "crash").exists():
            os._exit(1)  # like a crash: no clean-up, so the lock file stays behind
        hang = directory / "hang"
        if hang.exists():
            hang.unlink()
            mark("hung")
            # Stuck: keeps the lock and answers nothing until told to crash or exit.
            while not (directory / "exit").exists():
                if (directory / "crash").exists():
                    os._exit(1)
                time.sleep(0.02)
        for then in ("quit", "crash"):
            stall = directory / f"stall-then-{then}"
            if stall.exists():
                stall.unlink()
                mark("stalled")
                time.sleep(2)  # unresponsive, like a Weir still building its window
                if then == "crash":
                    os._exit(1)
                instance.stop_listening()  # starts quitting, and exits a moment later
                QTimer.singleShot(1000, app.quit)
        if (directory / "exit").exists() or time.monotonic() > give_up:
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(20)
    app.exec()
    mark("received", received)
    instance.release()


if __name__ == "__main__":
    _child(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6:])
