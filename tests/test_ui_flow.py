"""First download without the tools: one prompt, one install, the links start by themselves.
Small laptop screens: the queue and the error banner are scrolled into view.

The main window runs headless with stub tools (no files, no network): a fake installer
stands in for the real downloads, queued jobs never start, and the message boxes answer
themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from core.deps import TOOL_KEYS, ToolInfo
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
    """A short window with Advanced open: the queue's rows start below the visible area."""
    win.advanced_toggle.setChecked(True)
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
