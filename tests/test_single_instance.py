"""One Weir per user: the first launch listens, later launches hand their links over."""

from __future__ import annotations

import os
import socket
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QDir
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtTest import QTest

from core import single_instance
from core.single_instance import HandOver, SingleInstance, decode_links, encode_links

LINKS = ["https://www.youtube.com/watch?v=abc", "https://vimeo.com/123", "https://example.com/é"]


def _unique_name() -> str:
    return f"Weir-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _spin(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QTest.qWait(10)


@pytest.fixture
def make_instance(qapp) -> Iterator[Callable[..., SingleInstance]]:
    """SingleInstance objects with a private test name; both ends live in this thread."""
    created: list[SingleInstance] = []

    def make(name: str, timeout_ms: int = 1500) -> SingleInstance:
        instance = SingleInstance(name, timeout_ms=timeout_ms)
        created.append(instance)
        return instance

    yield make
    for instance in created:
        instance.close()


@pytest.fixture
def running(make_instance) -> tuple[SingleInstance, list[list[str]]]:
    """A first launch that is listening, plus everything it was handed."""
    first = make_instance(_unique_name())
    assert first.start() is False
    received: list[list[str]] = []
    first.activation_requested.connect(received.append)
    return first, received


def test_default_server_name_is_per_user(monkeypatch) -> None:
    name = single_instance.default_server_name()
    assert name.startswith("Weir-")
    assert name == single_instance.default_server_name()
    monkeypatch.setattr(single_instance.Path, "home", classmethod(lambda cls: Path("/home/someone-else")))
    assert single_instance.default_server_name() != name


def test_links_round_trip() -> None:
    assert decode_links(encode_links(LINKS)[:-1]) == LINKS
    assert encode_links([]) == b"\0"
    assert decode_links(encode_links([" ", ""])[:-1]) == []


def test_first_launch_listens(running) -> None:
    first, received = running
    assert first.is_listening
    assert received == []


def test_second_launch_hands_links_to_the_first(running, make_instance) -> None:
    first, received = running
    second = make_instance(first.server_name)

    assert second.start(LINKS) is True
    assert not second.is_listening
    assert received == [LINKS]


def test_second_launch_without_links_still_brings_the_window_forward(running, make_instance) -> None:
    first, received = running

    assert make_instance(first.server_name).start([]) is True
    assert make_instance(first.server_name).start(LINKS[:1]) is True
    assert received == [[], LINKS[:1]]


def test_closed_instance_frees_its_name(running, make_instance) -> None:
    first, _received = running
    first.close()

    second = make_instance(first.server_name)
    assert second.start(LINKS) is False
    assert second.is_listening


def test_unanswered_name_starts_normally(make_instance) -> None:
    """A running Weir that does not answer in time must not keep this launch from starting."""
    name = _unique_name()
    stuck = QLocalServer()  # accepts connections but never answers
    assert stuck.listen(name)
    instance = make_instance(name, timeout_ms=300)
    try:
        began = time.monotonic()
        assert instance.start(LINKS) is False
        assert time.monotonic() - began < 5
        assert instance.is_listening
    finally:
        instance.close()
        stuck.close()


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes vanish with their process")
def test_name_left_behind_by_a_crash_is_taken_over(make_instance) -> None:
    name = _unique_name()
    path = os.path.join(QDir.tempPath(), name)
    leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    leftover.bind(path)
    leftover.close()  # like a crash: the socket file stays, nobody listens
    assert os.path.exists(path)

    first = make_instance(name)
    assert first.start() is False
    assert first.is_listening
    received: list[list[str]] = []
    first.activation_requested.connect(received.append)

    assert make_instance(name).start(LINKS) is True
    assert received == [LINKS]


def test_start_retries_when_another_launch_claims_the_name_first(monkeypatch, make_instance) -> None:
    instance = make_instance(_unique_name())
    outcomes = iter([HandOver.NO_SERVER, HandOver.DELIVERED])
    monkeypatch.setattr(instance, "hand_over", lambda links: next(outcomes))
    monkeypatch.setattr(instance, "_listen", lambda: False)
    assert instance.start(LINKS) is True


def test_start_takes_the_name_over_when_nobody_answers(monkeypatch, make_instance) -> None:
    instance = make_instance(_unique_name())
    removed: list[str] = []
    monkeypatch.setattr(instance, "hand_over", lambda links: HandOver.NO_ANSWER)
    monkeypatch.setattr(single_instance.QLocalServer, "removeServer", staticmethod(removed.append))
    assert instance.start(LINKS) is False
    assert removed == [instance.server_name]
    assert instance.is_listening


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
    assert received == [LINKS]


def test_handed_over_links_restore_the_window(qapp) -> None:
    import main
    from PySide6.QtWidgets import QWidget

    from ui.widgets import UrlEdit

    class FakeWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.url_edit = UrlEdit(self)

    window = FakeWindow()
    window.showMinimized()
    main._show_handed_over_links(window, ["not a link", LINKS[0], LINKS[0]])  # type: ignore[arg-type]
    assert window.url_edit.links() == [LINKS[0]]
    assert window.isVisible()
    assert not window.isMinimized()
    window.close()
