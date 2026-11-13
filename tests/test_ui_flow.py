"""First download without the tools: one prompt, one install, the links start by themselves.
Small laptop screens: the queue and the error banner are scrolled into view.

The main window runs headless with stub tools (no files, no network): a fake installer
stands in for the real downloads, queued jobs never start, and the message boxes answer
themselves.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEvent, QObject, QProcess, QRect, Qt, Signal
from PySide6.QtGui import QScreen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

import core.trash
from core.deps import TOOL_KEYS, ToolInfo
from core.downloader import DownloadManager, JobStatus
from core.paths import IS_WINDOWS
from core.settings import Settings

LINKS = ["https://example.com/watch?v=one", "https://example.com/watch?v=two"]
SHORT = (1280, 600)  # a laptop-wide window, a bit shorter than a 1366x768 screen allows
ESC = "<Esc>"  # prompts["answer"] that closes a message box with Esc / ✕


class StubDeps(QObject):
    """Stands in for DepsManager: tools are "installed" when their key is in `installed`."""

    checked = Signal()
    updated = Signal()

    def __init__(self, installed: Iterable[str] = ()) -> None:
        super().__init__()
        self.installed: set[str] = set(installed)
        self.checking = False
        self.has_checked = True

    def tool(self, key: str) -> ToolInfo:
        if key not in self.installed:
            return ToolInfo(key=key)
        path = Path("stub-bin") / key
        companion = path.with_name("ffprobe") if key == "ffmpeg" else None
        return ToolInfo(key=key, path=path, version="1.0", companion=companion, bundled=True)

    def missing(self) -> list[str]:
        return [key for key in TOOL_KEYS if key not in self.installed]

    def refresh(self) -> None:
        pass

    def relocate(self, key: str) -> ToolInfo:
        self.updated.emit()
        return self.tool(key)


class FakeInstaller(QObject):
    """Replaces DependencyInstaller: records what was asked and finishes on demand."""

    progress = Signal(str, float, float)
    message = Signal(str)
    stage = Signal(str, str)
    tool_finished = Signal(str, bool, str)
    all_finished = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.requested: list[list[str]] = []
        self.cancelled = False
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def install(self, keys: list[str]) -> None:
        self.requested.append(list(keys))
        self._busy = True

    def cancel(self) -> None:
        self.cancelled = True

    def finish(self, deps: StubDeps, ok: Iterable[str] = (), failed: Iterable[str] = (),
               cancelled: Iterable[str] = ()) -> None:
        failed, cancelled = list(failed), list(cancelled)
        for key in ok:
            deps.installed.add(key)
            self.tool_finished.emit(key, True, f"Installed {key}")
        for key in failed:
            self.tool_finished.emit(key, False, "Download failed: no connection")
        for key in cancelled:
            self.tool_finished.emit(key, False, "Cancelled.")
        self._busy = False
        self.all_finished.emit(not failed and not cancelled)


@pytest.fixture(scope="module")
def theme_manager(qapp: QApplication) -> Any:
    from ui.design import DESIGN
    from ui.theme import ThemeManager

    return ThemeManager(qapp, DESIGN)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s._normalize()
    s.save_dir = str(tmp_path / "downloads")
    s.save = lambda: None  # type: ignore[method-assign]  # never touch a settings file
    return s


@pytest.fixture(autouse=True)
def fake_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    import ui.dialogs

    monkeypatch.setattr(ui.dialogs, "DependencyInstaller", FakeInstaller)


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Message boxes answer themselves: set state["answer"] to the start of a button's text."""
    state: dict[str, Any] = {"answer": None, "shown": []}

    def fake_exec(box: QMessageBox) -> int:
        state["shown"].append({
            "title": box.windowTitle(),
            "text": box.text(),
            "info": box.informativeText(),
            "buttons": [b.text() for b in box.buttons()],
        })
        if state["answer"] == ESC:
            box.escapeButton().click()
            return 0
        for button in box.buttons():
            if state["answer"] and button.text().startswith(state["answer"]):
                button.click()
                break
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    return state


@pytest.fixture
def quits(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """QApplication.quit only records the call, so the test run goes on."""
    calls: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", lambda: calls.append(True))
    return calls


@pytest.fixture
def make_window(qapp: QApplication, theme_manager: Any, settings: Settings
                ) -> Iterator[Callable[..., Any]]:
    import shiboken6

    from core.presets import PresetStore
    from ui.main_window import MainWindow

    windows: list[Any] = []

    def make(installed: Iterable[str] = (), real_queue: bool = False) -> Any:
        """real_queue: links become rows in the queue (that never start) instead of only
        being recorded in win.added."""
        deps = StubDeps(installed)
        win = MainWindow(settings, PresetStore.load(), deps, theme_manager)
        win.added = []  # links handed to the download queue
        if real_queue:
            win.manager._schedule = lambda: None  # jobs stay "Waiting": no yt-dlp runs
            win.manager.job_added.connect(lambda job_id: win.added.append(win.manager.get(job_id).url))
        else:
            win.manager.add = lambda link, _preset, _options: win.added.append(link)
        win.url_edit.add_links(LINKS)
        win.show()
        windows.append(win)
        return win

    yield make
    qapp.processEvents()
    for win in windows:
        win.hide()
        shiboken6.delete(win)
    qapp.processEvents()


def _settle(qapp: QApplication) -> None:
    for _ in range(3):
        qapp.processEvents()


def _page_rows(win: Any, widget: QWidget, rect: QRect | None = None) -> tuple[int, int]:
    """Top and bottom of a widget (or of a rect inside it) in the page's visible area."""
    rect = widget.rect() if rect is None else rect
    viewport = win._page.viewport()
    return widget.mapTo(viewport, rect.topLeft()).y(), widget.mapTo(viewport, rect.bottomLeft()).y()


def _in_view(win: Any, widget: QWidget, rect: QRect | None = None) -> bool:
    top, bottom = _page_rows(win, widget, rect)
    return top >= 0 and bottom < win._page.viewport().height()


def _row_in_view(win: Any, index: int) -> bool:
    item = win.queue.topLevelItem(index)
    rect = win.queue.visualItemRect(item)
    inside_list = rect.top() >= 0 and rect.bottom() < win.queue.viewport().height()
    return inside_list and _in_view(win, win.queue.viewport(), rect)


def _tall_page(qapp: QApplication, win: Any) -> None:
    """A short window with a page taller than it: the queue's rows start below the visible
    area. The layout fits a 1280x600 window by itself now, so a tall link box (as if the
    user had enlarged it, or a large font) stands in for whatever makes a page overflow."""
    win.advanced_toggle.setChecked(True)
    win.url_edit.setMinimumHeight(360)
    win.resize(*SHORT)
    _settle(qapp)
    win._page.verticalScrollBar().setValue(0)
    _settle(qapp)
    assert _page_rows(win, win.queue.viewport())[0] >= win._page.viewport().height(), "precondition"


def _fail_job(win: Any, url: str) -> int:
    """Queue a job and make it fail the way the download manager reports it."""
    from core.downloader import DownloadManager, JobStatus
    from core.errors import ACTION_COOKIES, FriendlyError

    job_id = DownloadManager.add(win.manager, url, win._current_preset(), win._current_options())
    job = win.manager.get(job_id)
    job.status = JobStatus.ERROR
    job.error = FriendlyError("The site wants you to sign in.", "Use your browser's cookies, then retry.",
                              ACTION_COOKIES, "ERROR: Sign in to confirm you're not a bot.")
    win.manager.job_changed.emit(job_id)
    win.manager.job_failed.emit(job_id)
    return job_id


# --------------------------------------------------------------------------- main window


def test_first_download_installs_everything_then_starts_links_once(qapp, make_window, prompts):
    from ui.dialogs import download_size_text

    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()

    assert len(prompts["shown"]) == 1, "one prompt instead of a message box + a second prompt"
    shown = prompts["shown"][0]
    assert download_size_text(TOOL_KEYS) in shown["text"]
    assert "Your 2 links will start automatically." in shown["info"]
    assert "Download it for me" not in shown["text"] + shown["info"]

    dialog = win._deps_dialog
    installer = dialog._installer
    assert dialog.isVisible()
    assert installer.requested == [list(TOOL_KEYS)], "every missing tool in one go"
    assert win.added == []

    installer.finish(win.deps, ok=TOOL_KEYS)
    _settle(qapp)
    assert win.added == LINKS, "the links start by themselves"
    assert not dialog.isVisible(), "the tools window closes on success"
    assert win.url_edit.links() == []
    assert len(prompts["shown"]) == 1

    # A later install (e.g. Re-download from the tools window) doesn't start anything again.
    win.url_edit.add_links(["https://example.com/watch?v=three"])
    win._show_deps_dialog()
    dialog.start_install(["deno"])
    installer.finish(win.deps, ok=["deno"])
    _settle(qapp)
    assert win.added == LINKS


def test_links_wait_when_ytdlp_did_not_install(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    dialog = win._deps_dialog

    dialog._installer.finish(win.deps, ok=["ffmpeg", "deno"], failed=["yt-dlp"])
    _settle(qapp)
    assert win.added == []
    assert win.url_edit.links() == LINKS
    assert dialog.isVisible(), "stays open to show what went wrong"
    assert "✖" in dialog._status["yt-dlp"].text()
    assert "still in the box" in win.statusBar().currentMessage()


def test_links_start_once_ytdlp_installs_on_a_second_try(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    dialog = win._deps_dialog
    dialog._installer.finish(win.deps, ok=["ffmpeg", "deno"], failed=["yt-dlp"])
    _settle(qapp)
    assert "start by themselves" in win.statusBar().currentMessage()

    dialog.start_install(["yt-dlp"])  # Download again, from the tools window
    dialog._installer.finish(win.deps, ok=["yt-dlp"])
    _settle(qapp)
    assert win.added == LINKS


def test_closing_the_tools_window_after_a_failed_install_forgets_the_links(qapp, make_window,
                                                                           prompts):
    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    dialog = win._deps_dialog
    dialog._installer.finish(win.deps, ok=["ffmpeg", "deno"], failed=["yt-dlp"])
    _settle(qapp)
    dialog.reject()  # Close
    _settle(qapp)

    win._show_deps_dialog()  # later, from Settings > Tools
    dialog.start_install(["yt-dlp"])
    dialog._installer.finish(win.deps, ok=["yt-dlp"])
    _settle(qapp)
    assert win.added == []
    assert win.url_edit.links() == LINKS


def test_stopping_the_install_does_not_start_links(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    dialog = win._deps_dialog
    installer = dialog._installer

    dialog.reject()  # "Stop" -> "Stop it?" -> Yes
    assert installer.cancelled
    installer.finish(win.deps, ok=["yt-dlp"], cancelled=["ffmpeg"])
    _settle(qapp)
    assert win.added == []
    assert win.url_edit.links() == LINKS
    assert "✖" not in dialog._status["ffmpeg"].text(), "stopping isn't a failure"
    assert "Not installed yet" in dialog._status["ffmpeg"].text()


def test_not_now_keeps_the_links_and_asks_again(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = "Not now"
    win._start_downloads()
    assert win._deps_dialog is None
    assert win.added == []
    assert win.url_edit.links() == LINKS

    win._start_downloads()  # yt-dlp is required: ask every time
    assert len(prompts["shown"]) == 2


def test_recommended_tools_prompt_installs_them_then_starts(qapp, make_window, prompts):
    win = make_window(installed=["yt-dlp"])
    prompts["answer"] = "Download"
    win._start_downloads()

    shown = prompts["shown"][0]
    assert "ffmpeg and Deno are not installed yet" in shown["text"]
    assert any(b.startswith("Continue without") for b in shown["buttons"])
    installer = win._deps_dialog._installer
    assert installer.requested == [["ffmpeg", "deno"]]
    assert win.added == []

    installer.finish(win.deps, ok=["ffmpeg", "deno"])
    _settle(qapp)
    assert win.added == LINKS
    assert not win._deps_dialog.isVisible()


def test_continue_without_recommended_tools_starts_now_and_asks_once(qapp, make_window, prompts):
    win = make_window(installed=["yt-dlp"])
    prompts["answer"] = "Continue without"
    win._start_downloads()
    assert win.added == LINKS
    assert win._deps_dialog is None

    win.url_edit.add_links(["https://example.com/watch?v=three"])
    win._start_downloads()
    assert len(prompts["shown"]) == 1, "only asked once per session"
    assert win.added[-1] == "https://example.com/watch?v=three"


def test_link_handed_over_while_the_prompt_is_open_is_kept(qapp, make_window, prompts,
                                                           monkeypatch):
    win = make_window(installed=["yt-dlp"])
    later = "https://example.com/watch?v=three"
    answer = QMessageBox.exec  # the prompts fixture's stand-in

    def hand_over_then_answer(box: QMessageBox) -> int:
        # A second launch hands its link over while the prompt runs its own event loop
        # (main.py adds it to the box).
        win.url_edit.add_links([later])
        return answer(box)

    monkeypatch.setattr(QMessageBox, "exec", hand_over_then_answer)
    prompts["answer"] = "Continue without"
    win._start_downloads()
    assert win.added == LINKS
    assert win.url_edit.links() == [later], "waits in the box for the next Download"


def test_startup_window_does_not_take_over_a_running_install(qapp, make_window, prompts):
    from ui.dialogs import TITLE_TOOLS, TITLE_WELCOME

    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    win.deps.checked.emit()  # the startup version check reports back meanwhile
    _settle(qapp)
    assert win._deps_dialog.windowTitle() == TITLE_TOOLS
    assert not win._deps_dialog._dont_ask.isVisible()

    win._deps_dialog._installer.finish(win.deps, ok=TOOL_KEYS)
    _settle(qapp)
    win._show_deps_dialog(startup=True)
    assert win._deps_dialog.windowTitle() == TITLE_WELCOME


def test_links_wait_when_a_recommended_tool_did_not_install(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    dialog = win._deps_dialog

    dialog._installer.finish(win.deps, ok=["yt-dlp", "deno"], failed=["ffmpeg"])
    _settle(qapp)
    assert win.added == [], "no batch of downloads that need ffmpeg behind the error"
    assert win.url_edit.links() == LINKS
    assert dialog.isVisible()
    message = win.statusBar().currentMessage()
    assert "ffmpeg didn't install" in message and "Click Download" in message

    win._start_downloads()  # the user goes on anyway: no second prompt
    _settle(qapp)
    assert len(prompts["shown"]) == 1
    assert win.added == LINKS


def test_links_wait_for_every_tool_asked_for_not_only_the_last_try(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    dialog = win._deps_dialog
    installer = dialog._installer

    installer.finish(win.deps, ok=["deno"], failed=["yt-dlp", "ffmpeg"])
    _settle(qapp)
    assert win.added == []
    # Names what must install: "once they're installed" would read as the links.
    assert win.statusBar().currentMessage() == (
        "yt-dlp and ffmpeg didn't install, so your links are still in the box. They start by "
        "themselves once yt-dlp and ffmpeg are installed."
    )

    dialog.start_install(["yt-dlp"])  # the yt-dlp row's Download, in the tools window
    installer.finish(win.deps, ok=["yt-dlp"])
    _settle(qapp)
    assert win.added == [], "ffmpeg, asked for and still missing: nothing starts behind the window"
    assert win.url_edit.links() == LINKS
    assert dialog.isVisible()
    message = win.statusBar().currentMessage()
    assert "ffmpeg didn't install" in message and "Click Download" in message
    # ... and the tools window, the one in front, doesn't claim the opposite.
    text = dialog._message.text()
    assert "All set" not in text and "ffmpeg" in text

    dialog.start_install(["ffmpeg"])  # ... and then ffmpeg's: everything asked for is there
    installer.finish(win.deps, ok=["ffmpeg"])
    _settle(qapp)
    assert win.added == LINKS


def test_links_started_by_hand_no_longer_wait_for_the_tools(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    dialog = win._deps_dialog
    dialog._installer.finish(win.deps, ok=["yt-dlp", "deno"], failed=["ffmpeg"])
    _settle(qapp)
    win._start_downloads()  # the user goes on without ffmpeg
    assert win.added == LINKS

    later = "https://example.com/watch?v=three"
    win.url_edit.add_links([later])  # e.g. handed over by a second launch
    dialog.start_install(["ffmpeg"])
    dialog._installer.finish(win.deps, ok=["ffmpeg"])
    _settle(qapp)
    assert win.added == LINKS, "a link added afterwards waits for Download"
    assert win.url_edit.links() == [later]


def test_stop_that_came_too_late_says_stopped_not_all_set(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = "Download"
    win._start_downloads()
    dialog = win._deps_dialog

    dialog.reject()  # "Stop" -> "Stop it?" -> Yes, while the last tool is being unpacked
    dialog._installer.finish(win.deps, ok=TOOL_KEYS)  # ... so everything installed anyway
    _settle(qapp)
    text = dialog._message.text()
    assert text.startswith("Stopped") and "everything is installed" in text
    assert win.added == [], "stopped: the links wait for the user"
    assert "still in the box" in win.statusBar().currentMessage()

    win.deps.checked.emit()  # the version check reports back later
    _settle(qapp)
    assert dialog._message.text() == text, "no 'All set!' contradicting 'Stopped'"
    assert dialog.isVisible()


def test_closing_the_recommended_prompt_changes_nothing(qapp, make_window, prompts):
    win = make_window(installed=["yt-dlp"])
    prompts["answer"] = ESC
    win._start_downloads()
    assert "Cancel" in prompts["shown"][0]["buttons"]
    assert win.added == [] and win._deps_dialog is None
    assert win.url_edit.links() == LINKS

    prompts["answer"] = "Continue without"
    win._start_downloads()  # not answered before, so asked again
    assert len(prompts["shown"]) == 2
    assert win.added == LINKS


def test_closing_the_required_prompt_keeps_the_links(qapp, make_window, prompts):
    win = make_window()
    prompts["answer"] = ESC
    win._start_downloads()
    assert win.added == [] and win._deps_dialog is None
    assert win.url_edit.links() == LINKS


# --------------------------------------------------------------------------- small screens


def test_failed_download_scrolls_the_banner_into_view(qapp, make_window):
    win = make_window(installed=TOOL_KEYS, real_queue=True)
    _tall_page(qapp, win)

    _fail_job(win, LINKS[0])
    _settle(qapp)
    assert win.banner.isVisible()
    assert _in_view(win, win.banner), "the message and its one-click fix are on screen"
    assert _row_in_view(win, 0), "with the top of the list below it"
    assert _page_rows(win, win.banner)[1] < _page_rows(win, win.queue)[0], "banner sits above the list"


def test_download_scrolls_the_new_rows_into_view(qapp, make_window, prompts):
    win = make_window(installed=TOOL_KEYS, real_queue=True)
    _tall_page(qapp, win)

    win.download_btn.click()
    _settle(qapp)
    assert prompts["shown"] == []
    assert win.added == LINKS
    assert win._page.verticalScrollBar().value() > 0, "the page itself scrolled"
    assert _in_view(win, win.queue.header()), "the queue's column headers"
    assert _row_in_view(win, 0) and _row_in_view(win, 1), "the rows just added"


def test_nothing_jumps_when_the_banner_is_already_in_view(qapp, make_window):
    win = make_window(installed=TOOL_KEYS, real_queue=True)
    win.resize(1280, 1000)
    _settle(qapp)
    win._page.verticalScrollBar().setValue(0)

    _fail_job(win, LINKS[0])
    _settle(qapp)
    assert _in_view(win, win.banner) and _row_in_view(win, 0)
    assert win._page.verticalScrollBar().value() == 0


@pytest.mark.parametrize("area, expected", [
    (QRect(0, 0, 1366, 728), None),  # 1366x768 laptop minus the taskbar
    (QRect(0, 0, 1280, 672), None),  # 1080p at 150 %
    (QRect(0, 0, 1920, 1040), (1040, 820)),  # big enough: the usual size
])
def test_first_run_window_fits_the_screen(qapp, make_window, monkeypatch, area, expected):
    from ui.main_window import FRAME_ALLOWANCE

    monkeypatch.setattr(QScreen, "availableGeometry", lambda _screen: QRect(area))
    win = make_window()
    size = win.size()
    if expected is not None:
        assert (size.width(), size.height()) == expected
    assert size.width() + FRAME_ALLOWANCE.width() <= area.width()
    assert size.height() + FRAME_ALLOWANCE.height() <= area.height()
    frame = QRect(win.pos(), size + FRAME_ALLOWANCE)
    assert area.contains(frame), f"{frame} starts off-screen"


# --------------------------------------------------------------------------- quitting


def _shutdowns(win: Any) -> list[bool]:
    fired: list[bool] = []
    win.shutdown_started.connect(lambda: fired.append(True))
    return fired


@pytest.mark.parametrize("installing", [False, True])
def test_quitting_right_away_announces_the_shutdown_once(qapp, make_window, prompts, quits,
                                                         installing):
    win = make_window(installed=() if installing else TOOL_KEYS)
    if installing:  # "A tool is still downloading. Stop it and quit?" -> Yes
        prompts["answer"] = "Download"
        win._start_downloads()
        assert win._deps_dialog.busy
    fired = _shutdowns(win)
    win.close()
    _settle(qapp)
    assert fired == [True]
    assert quits == [True]
    assert not win.isVisible()


def test_stop_them_and_quit_announces_the_shutdown_once(qapp, make_window, prompts, quits):
    win = make_window(installed=TOOL_KEYS, real_queue=True)
    win._start_downloads()
    assert win.manager.has_work(), "precondition: downloads waiting"
    fired = _shutdowns(win)

    win.close()  # "Downloads are still running. Stop them and quit?" -> Yes
    assert fired == [True], "as soon as it starts waiting for the downloads to stop"
    _settle(qapp)  # they stopped: the window closes for real
    assert fired == [True]
    assert quits == [True]


@pytest.mark.parametrize("installing", [False, True])
def test_declined_quit_is_not_a_shutdown(qapp, make_window, prompts, quits, monkeypatch,
                                         installing):
    if installing:  # "A tool is still downloading. Stop it and quit?" -> No
        win = make_window()
        prompts["answer"] = "Download"
        win._start_downloads()
    else:  # "Downloads are still running. Stop them and quit?" -> No
        win = make_window(installed=TOOL_KEYS, real_queue=True)
        win._start_downloads()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *_args, **_kwargs: QMessageBox.StandardButton.No)
    fired = _shutdowns(win)
    win.close()
    _settle(qapp)
    assert fired == []
    assert quits == []
    assert win.isVisible()


# --------------------------------------------------------------------------- tools window


def test_tools_window_is_neutral_for_tools_never_installed(qapp, theme_manager, settings):
    from ui import theme
    from ui.dialogs import DepsDialog, download_size_text

    dialog = DepsDialog(StubDeps(), settings)
    try:
        danger = theme.current().danger
        for key in TOOL_KEYS:
            text = dialog._status[key].text()
            assert "Not installed yet" in text
            assert "✖" not in text and danger not in text
        assert dialog._install_all.text() == f"Download them for me ({download_size_text(TOOL_KEYS)})"
        assert "recommended" in dialog._dont_ask.text()
        assert "optional" not in dialog._dont_ask.text()
        assert dialog._details.toolTip(), "the folder and GitHub wording live in Details"

        dialog.show()
        dialog.start_install(["deno"])  # a single row: the window stays open afterwards
        dialog._installer.finish(dialog._deps, failed=["deno"])
        assert "✖" in dialog._status["deno"].text(), "red stays for real failures"
        assert "Not installed yet" in dialog._status["ffmpeg"].text()
        assert dialog.isVisible()
    finally:
        dialog.deleteLater()


def test_tools_window_closes_after_installing_everything(qapp, theme_manager, settings):
    from ui.dialogs import DepsDialog

    deps = StubDeps(installed=["yt-dlp", "ffmpeg"])
    dialog = DepsDialog(deps, settings)
    try:
        dialog.show()
        assert dialog._install_all.text().startswith("Download it for me (about ")
        dialog._install_all.click()
        installer = dialog._installer
        assert installer.requested == [["deno"]]
        installer.finish(deps, ok=["deno"])
        assert not dialog.isVisible()
    finally:
        dialog.deleteLater()


def test_tools_window_says_all_set_only_when_nothing_is_missing(qapp, theme_manager, settings):
    from ui.dialogs import DepsDialog

    deps = StubDeps()
    dialog = DepsDialog(deps, settings)
    try:
        dialog.show()
        dialog.start_install(["yt-dlp"])  # one row's Download while ffmpeg and Deno are missing
        dialog._installer.finish(deps, ok=["yt-dlp"])
        assert dialog._message.text() == "Done. Still not installed: ffmpeg + ffprobe and Deno."
        deps.checked.emit()  # the version check reports back later
        assert dialog._message.text() == "Done. Still not installed: ffmpeg + ffprobe and Deno."
        assert dialog.isVisible()

        dialog.start_install(["ffmpeg", "deno"])
        dialog._installer.finish(deps, ok=["ffmpeg", "deno"])
        assert dialog._message.text() == "All set! Everything the app needs is installed."
    finally:
        dialog.deleteLater()


# --------------------------------------------------------------------------- error details


def _error_details() -> Any:
    from core.errors import ACTION_COOKIES, FriendlyError
    from ui.dialogs import ErrorDetailsDialog

    error = FriendlyError("The site wants you to sign in.", "Use your browser's cookies, then retry.",
                          ACTION_COOKIES, "ERROR: Sign in to confirm you're not a bot.")
    dialog = ErrorDetailsDialog("A video", LINKS[0], error, "Use browser cookies")
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # as the main window opens it
    dialog.show()
    return dialog


@pytest.mark.parametrize("button", ["Close", "Retry", "Use browser cookies"])
def test_closing_error_details_right_after_copy(qapp, monkeypatch, button):
    import sys

    import ui.dialogs
    from PySide6.QtWidgets import QPushButton

    errors: list[BaseException] = []
    monkeypatch.setattr(sys, "excepthook", lambda _type, exc, _tb: errors.append(exc))
    monkeypatch.setattr(ui.dialogs, "COPIED_FEEDBACK_MS", 20)
    dialog = _error_details()
    dialog._copy.click()
    assert dialog._copy.text() == "Copied!"
    next(b for b in dialog.findChildren(QPushButton) if b.text() == button).click()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # the dialog is gone
    QTest.qWait(100)  # longer than "Copied!" shows
    assert errors == [], "no 'Internal C++ object already deleted' once the dialog is gone"


def test_error_details_copy_button_reads_copy_again(qapp, monkeypatch):
    import ui.dialogs

    monkeypatch.setattr(ui.dialogs, "COPIED_FEEDBACK_MS", 20)
    dialog = _error_details()
    try:
        dialog._copy.click()
        assert dialog._copy.text() == "Copied!"
        QTest.qWait(100)
        assert dialog._copy.text() == "Copy"
    finally:
        dialog.close()


# --------------------------------------------------------------------------- helpers


def test_download_size_text() -> None:
    from ui.dialogs import download_size_text

    assert download_size_text(TOOL_KEYS) == ("about 280 MB" if IS_WINDOWS else "about 230 MB")
    assert download_size_text(["deno"]) == "about 45 MB"


def test_join_names() -> None:
    from ui.dialogs import join_names

    assert join_names(["yt-dlp"]) == "yt-dlp"
    assert join_names(["ffmpeg", "Deno"]) == "ffmpeg and Deno"
    assert join_names(["yt-dlp", "ffmpeg", "Deno"]) == "yt-dlp, ffmpeg and Deno"

# =========================================================================== delete files
# "Remove and delete files": which files, asking first, the Recycle Bin, and what's left.
# Nothing here touches the real Recycle Bin or trash: core.trash is replaced by a fake that
# moves files into a folder of the test's own.


class FakeTrash:
    """Stands in for the Recycle Bin: "trashed" files go to a folder; some paths refuse."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir()
        self.trashed: list[str] = []
        self.deleted: list[str] = []
        self.refuse: set[str] = set()  # the Recycle Bin won't take them (a USB stick, say)
        self.locked: set[str] = set()  # open in a video player: nothing can delete them
        self.network: set[str] = set()  # on a network drive: deleted for good, never trashed

    def can_trash(self, path: str) -> bool:
        return path not in self.network

    def in_use(self, path: str) -> bool:
        return path in self.locked

    def move_to_trash(self, path: str) -> bool:
        if path in self.refuse or path in self.locked:
            return False
        shutil.move(path, self.folder / Path(path).name)
        self.trashed.append(path)
        return True

    def delete_for_good(self, path: str) -> bool:
        if path in self.locked:
            return False
        Path(path).unlink()
        self.deleted.append(path)
        return True


@pytest.fixture
def bin_(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeTrash:
    fake = FakeTrash(tmp_path / "recycle-bin")
    for name in ("can_trash", "in_use", "move_to_trash", "delete_for_good"):
        monkeypatch.setattr(core.trash, name, getattr(fake, name))
    return fake


def _finished(win: Any, folder: Path, *names: str, status: JobStatus = JobStatus.DONE) -> int:
    """A download in the list that finished (or failed) after writing these files."""
    folder.mkdir(exist_ok=True)
    options = win._current_options()
    options.save_dir = str(folder)
    job_id = DownloadManager.add(win.manager, f"https://example.com/watch?v={len(names)}{names}",
                                 win._current_preset(), options)
    job = win.manager.get(job_id)
    for name in names:
        (folder / name).write_bytes(b"video")
        job.files.append(str(folder / name))
    job.status = status
    win.manager.job_changed.emit(job_id)
    return job_id


def _ids(win: Any) -> list[int]:
    return [job.id for job in win.manager.jobs()]


# --------------------------------------------------------------------------- deleting


def test_files_go_to_the_recycle_bin_and_the_downloads_leave_the_list(
        make_window, prompts, bin_, tmp_path):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    one = _finished(win, folder, "Song.mp3")
    two = _finished(win, folder, "Clip 1.mp4", "Clip 2.mp4")  # a playlist: two files
    prompts["answer"] = "Move to"

    win._delete_jobs([one, two])

    shown = prompts["shown"][-1]
    assert shown["text"] == (f"Move these 3 files to the {core.trash.TRASH_NAME} and remove their "
                             "downloads from the list?")
    assert "Clip 2.mp4" in shown["info"]
    assert sorted(Path(p).name for p in bin_.trashed) == ["Clip 1.mp4", "Clip 2.mp4", "Song.mp3"]
    assert list(folder.iterdir()) == []
    assert _ids(win) == []
    assert win.statusBar().currentMessage() == f"Moved 3 files to the {core.trash.TRASH_NAME}."


def test_one_file_is_named_in_the_question(make_window, prompts, bin_, tmp_path):
    win = make_window(real_queue=True)
    job = _finished(win, tmp_path / "Downloads", "Me at the zoo [jNQXAC9IVRw].mp4")
    prompts["answer"] = ESC
    win._delete_jobs([job])
    assert prompts["shown"][-1]["text"] == (
        f"Move “Me at the zoo [jNQXAC9IVRw].mp4” to the {core.trash.TRASH_NAME} and remove its "
        "download from the list?"
    )


@pytest.mark.parametrize("answer", ["Cancel", ESC])
def test_saying_no_changes_nothing(make_window, prompts, bin_, tmp_path, answer):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    job = _finished(win, folder, "Song.mp3")
    prompts["answer"] = answer
    win._delete_jobs([job])
    assert (folder / "Song.mp3").exists()
    assert bin_.trashed == []
    assert _ids(win) == [job]


def test_a_file_in_use_keeps_its_download_in_the_list(make_window, prompts, bin_, tmp_path):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    playing = _finished(win, folder, "Playing.mp4")
    other = _finished(win, folder, "Other.mp4")
    bin_.locked.add(str(folder / "Playing.mp4"))
    prompts["answer"] = "Move to"

    win._delete_jobs([playing, other])

    assert (folder / "Playing.mp4").exists()
    assert not (folder / "Other.mp4").exists()
    assert _ids(win) == [playing]
    assert len(prompts["shown"]) == 1, "no pointless 'Delete for good?' for a file in use"
    assert win.statusBar().currentMessage() == (
        f"Moved 1 file to the {core.trash.TRASH_NAME}. “Playing.mp4” is open in another program: "
        "close it, then try again. Its download stays in the list."
    )


def test_a_file_the_recycle_bin_refuses_is_deleted_for_good_only_after_asking(
        make_window, prompts, bin_, tmp_path, monkeypatch):
    import ui.main_window

    win = make_window(real_queue=True)
    folder = tmp_path / "USB stick"
    job = _finished(win, folder, "Song.mp3")
    bin_.refuse.add(str(folder / "Song.mp3"))
    prompts["answer"] = "Move to"
    original = ui.main_window.MainWindow._confirm_delete_for_good
    asked: list[list[str]] = []

    def confirm(self: Any, paths: list[str]) -> bool:
        asked.append([Path(p).name for p in paths])
        prompts["answer"] = "Delete for good"
        return original(self, paths)

    monkeypatch.setattr(ui.main_window.MainWindow, "_confirm_delete_for_good", confirm)
    win._delete_jobs([job])
    assert asked == [["Song.mp3"]]
    assert prompts["shown"][-1]["text"] == (
        f"“Song.mp3” can't go to the {core.trash.TRASH_NAME}. Delete it for good?")
    assert bin_.deleted == [str(folder / "Song.mp3")]
    assert _ids(win) == []
    assert win.statusBar().currentMessage() == "Deleted 1 file."


def test_saying_no_to_delete_for_good_keeps_the_file(make_window, prompts, bin_, tmp_path,
                                                     monkeypatch):
    import ui.main_window

    win = make_window(real_queue=True)
    folder = tmp_path / "USB stick"
    job = _finished(win, folder, "Song.mp3")
    bin_.refuse.add(str(folder / "Song.mp3"))
    prompts["answer"] = "Move to"
    monkeypatch.setattr(ui.main_window.MainWindow, "_confirm_delete_for_good",
                        lambda self, paths: False)
    win._delete_jobs([job])
    assert (folder / "Song.mp3").exists()
    assert _ids(win) == [job]
    assert win.statusBar().currentMessage() == "Kept “Song.mp3”. Its download stays in the list."


def test_two_rows_of_the_same_file_both_leave_the_list(make_window, prompts, bin_, tmp_path):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    first = _finished(win, folder, "Song.mp3")
    again = _finished(win, folder, "Song.mp3")  # saved again: "already downloaded"
    prompts["answer"] = "Move to"
    win._delete_jobs([first, again])
    assert bin_.trashed == [str(folder / "Song.mp3")], "the shared file goes once"
    assert _ids(win) == []
    assert prompts["shown"][-1]["text"].startswith("Move “Song.mp3”")


def test_files_on_a_network_drive_are_deleted_for_good_after_saying_so(
        make_window, prompts, bin_, tmp_path):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    job = _finished(win, folder, "Song.mp3")
    bin_.network.add(str(folder / "Song.mp3"))
    prompts["answer"] = "Delete for good"

    win._delete_jobs([job])

    shown = prompts["shown"][-1]
    assert shown["text"] == "Delete “Song.mp3” for good and remove its download from the list?"
    assert "network drive" in shown["info"]
    assert bin_.deleted == [str(folder / "Song.mp3")] and bin_.trashed == []
    assert _ids(win) == []
    assert win.statusBar().currentMessage() == "Deleted 1 file."


def test_enter_does_not_delete_for_good(make_window, prompts, bin_, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    job = _finished(win, folder, "Song.mp3")
    bin_.network.add(str(folder / "Song.mp3"))
    defaults: list[str] = []

    def press_enter(box: QMessageBox) -> int:
        defaults.append(box.defaultButton().text())
        box.defaultButton().click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", press_enter)
    win._delete_jobs([job])
    assert defaults == ["Cancel"]
    assert (folder / "Song.mp3").exists()


def test_running_downloads_are_left_alone(make_window, prompts, bin_, tmp_path):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    done = _finished(win, folder, "Done.mp4")
    running = _finished(win, folder, "Part 1.mp4", status=JobStatus.DOWNLOADING)
    win.manager.get(running).process = QProcess()  # a playlist still downloading
    prompts["answer"] = "Move to"
    try:
        win._delete_jobs([done, running])
        assert (folder / "Part 1.mp4").exists()
        assert not (folder / "Done.mp4").exists()
        assert _ids(win) == [running]
    finally:
        win.manager.get(running).process = None


def test_files_already_gone_just_say_so(make_window, prompts, bin_, tmp_path):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    job = _finished(win, folder, "Song.mp3")
    (folder / "Song.mp3").unlink()
    win._delete_jobs([job])
    assert prompts["shown"] == []
    assert _ids(win) == [job]
    assert win.statusBar().currentMessage() == "Those files aren't there any more."


def test_a_file_another_download_also_wrote_is_kept(make_window, prompts, bin_, tmp_path):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    first = _finished(win, folder, "Song.mp3", "Cover.mp3")
    again = _finished(win, folder, "Song.mp3")  # the same video saved again: same file
    assert win.manager.files_on_disk([first]) == {first: [str(folder / "Cover.mp3")]}
    # Chosen together, each lists the shared file (the window deletes it once).
    assert win.manager.files_on_disk([first, again]) == {
        first: [str(folder / "Song.mp3"), str(folder / "Cover.mp3")],
        again: [str(folder / "Song.mp3")],
    }


def test_a_failed_download_loses_its_unfinished_files_too(make_window, prompts, bin_, tmp_path,
                                                          monkeypatch):
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    failed = _finished(win, folder, "Part 1.mp4", status=JobStatus.ERROR)
    done = _finished(win, folder, "Done.mp4")
    cleaned: list[tuple[int, bool]] = []
    monkeypatch.setattr(win.manager, "_start_cleanup",
                        lambda job, partials_only=False: cleaned.append((job.id, partials_only)))
    prompts["answer"] = "Move to"
    win._delete_jobs([failed, done])
    assert ".part files" in prompts["shown"][-1]["info"]
    assert cleaned == [(failed, True)], "only the failed one, and only its partial files"


def test_a_failed_download_s_cleanup_keeps_everything_but_partial_files(
        qapp, make_window, prompts, bin_, tmp_path):
    """The real cleanup, run long after the failure: only yt-dlp's partial files go. A
    complete video whose conversion failed, and a file the user saved since with the same
    name, stay."""
    win = make_window(real_queue=True)
    folder = tmp_path / "Downloads"
    job_id = _finished(win, folder, "A [id1].mp4", status=JobStatus.ERROR)
    job = win.manager.get(job_id)
    # Item 2 of the playlist: yt-dlp announced it, then its conversion failed.
    win.manager._handle_line(job, b"[DEST]2|2|" + str(folder / "B [id2].mp4").encode())
    for name in ("B [id2].mp4", "B [id2].f251.webm.part", "B [id2].mp4.ytdl", "B [id2].pdf"):
        (folder / name).write_bytes(b"x")
    job.status = JobStatus.ERROR
    prompts["answer"] = "Move to"

    win._delete_jobs([job_id])
    for _ in range(100):
        if not win.manager._cleanups:
            break
        QTest.qWait(20)

    assert sorted(p.name for p in folder.iterdir()) == ["B [id2].mp4", "B [id2].pdf"]
    assert bin_.trashed == [str(folder / "A [id1].mp4")]


# --------------------------------------------------------------------------- the menu


def _menu_entries(win: Any, qapp: Any, monkeypatch: pytest.MonkeyPatch, job_id: int) -> dict[str, bool]:
    """The queue's right-click menu for this row: entry text -> enabled. Never opens it."""
    import ui.main_window

    entries: dict[str, bool] = {}

    class RecordingMenu(QMenu):
        # Patching QMenu.exec itself doesn't reach PySide's method: replace the class.
        def exec(self, *_args: Any) -> None:  # type: ignore[override]
            entries.update({a.text(): a.isEnabled() for a in self.actions() if a.text()})

    monkeypatch.setattr(ui.main_window, "QMenu", RecordingMenu)
    item = win._items[job_id]
    win.queue.setCurrentItem(item)
    qapp.processEvents()
    win._show_queue_menu(win.queue.visualItemRect(item).center())
    return entries


def test_menu_offers_to_delete_the_file(qapp, make_window, bin_, tmp_path, monkeypatch):
    win = make_window(real_queue=True)
    job = _finished(win, tmp_path / "Downloads", "Song.mp3")
    entries = _menu_entries(win, qapp, monkeypatch, job)
    assert entries["Remove"] is True
    assert entries["Remove and delete file…"] is True


def test_menu_counts_a_playlist_s_files(qapp, make_window, bin_, tmp_path, monkeypatch):
    win = make_window(real_queue=True)
    job = _finished(win, tmp_path / "Downloads", "Clip 1.mp4", "Clip 2.mp4")
    assert _menu_entries(win, qapp, monkeypatch, job)["Remove and delete files…"] is True


def test_menu_greys_it_out_without_files(qapp, make_window, bin_, tmp_path, monkeypatch):
    win = make_window(real_queue=True)
    job = _finished(win, tmp_path / "Downloads", status=JobStatus.ERROR)  # failed before any file
    assert _menu_entries(win, qapp, monkeypatch, job)["Remove and delete files…"] is False
