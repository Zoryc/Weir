"""Cookies from the browser: which browsers are found, what the "Cookies from" list shows,
and what the "Use browser cookies" fix picks (never a browser that isn't there).

Browser detection runs against fake profile folders in a temporary directory. The window
runs headless with stub tools; file dialogs answer themselves and nothing is downloaded.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

import core.browsers as browsers
from core.browsers import (
    COOKIES_FILE_PREFIX,
    cookies_file_path,
    cookies_locked,
    installed_browsers,
    is_network_path,
    suggest_browser,
    valid_cookie_setting,
)
from core.deps import ToolInfo
from core.downloader import JobStatus
from core.settings import Settings

URL = "https://example.com/watch?v=one"


def _firefox_profile(root: Path) -> Path:
    profile = root / "abcd1234.default-release"
    profile.mkdir(parents=True)
    (profile / "cookies.sqlite").write_bytes(b"")
    return profile


# --------------------------------------------------------------------------- detection


def test_windows_profile_folders(tmp_path: Path) -> None:
    roaming, local = tmp_path / "Roaming", tmp_path / "Local"
    env = {"APPDATA": str(roaming), "LOCALAPPDATA": str(local)}
    assert installed_browsers(platform="win32", env=env, home=tmp_path) == []

    _firefox_profile(roaming / "Mozilla" / "Firefox" / "Profiles")
    (local / "Microsoft" / "Edge" / "User Data").mkdir(parents=True)
    (local / "BraveSoftware" / "Brave-Browser" / "User Data").mkdir(parents=True)
    (roaming / "Opera Software" / "Opera Stable").mkdir(parents=True)
    (tmp_path / ".config" / "google-chrome").mkdir(parents=True)  # a Linux folder: ignored

    assert installed_browsers(platform="win32", env=env, home=tmp_path) == [
        ("firefox", "Firefox"), ("edge", "Edge"), ("brave", "Brave"), ("opera", "Opera"),
    ]


def test_windows_firefox_from_the_microsoft_store(tmp_path: Path) -> None:
    local = tmp_path / "Local"
    env = {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(local)}
    store = local.joinpath("Packages", "Mozilla.Firefox_n80bbvh6b1yt2", "LocalCache", "Roaming",
                           "Mozilla", "Firefox", "Profiles")
    _firefox_profile(store)
    assert installed_browsers(platform="win32", env=env, home=tmp_path) == [("firefox", "Firefox")]


@pytest.mark.parametrize("where", ["", "abcd1234.default-release", "Profiles/abcd1234.default"])
def test_firefox_cookies_in_every_place_yt_dlp_looks(tmp_path: Path, where: str) -> None:
    # yt-dlp looks for root/cookies.sqlite, root/*/cookies.sqlite and root/Profiles/*/cookies.sqlite.
    folder = tmp_path.joinpath(".mozilla", "firefox", *filter(None, where.split("/")))
    folder.mkdir(parents=True)
    (folder / "cookies.sqlite").write_bytes(b"")
    assert installed_browsers(platform="linux", env={}, home=tmp_path) == [("firefox", "Firefox")]


def test_windows_without_appdata_variables_uses_the_home_folder(tmp_path: Path) -> None:
    (tmp_path / "AppData" / "Local" / "Google" / "Chrome" / "User Data").mkdir(parents=True)
    assert installed_browsers(platform="win32", env={}, home=tmp_path) == [("chrome", "Chrome")]


def test_firefox_needs_a_profile_with_cookies(tmp_path: Path) -> None:
    env = {"APPDATA": str(tmp_path)}
    profiles = tmp_path / "Mozilla" / "Firefox" / "Profiles"
    (profiles / "empty.default").mkdir(parents=True)  # never started or no cookies yet
    assert installed_browsers(platform="win32", env=env, home=tmp_path) == []
    _firefox_profile(profiles)
    assert installed_browsers(platform="win32", env=env, home=tmp_path) == [("firefox", "Firefox")]


def test_linux_profile_folders(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".config" / "google-chrome").mkdir(parents=True)
    (home / ".config" / "chromium").mkdir(parents=True)
    (home / ".config" / "BraveSoftware" / "Brave-Browser").mkdir(parents=True)
    (home / ".config" / "microsoft-edge").mkdir(parents=True)
    _firefox_profile(home / ".mozilla" / "firefox")
    (home / "AppData" / "Local" / "Vivaldi" / "User Data").mkdir(parents=True)  # Windows: ignored

    assert installed_browsers(platform="linux", env={}, home=home) == [
        ("firefox", "Firefox"), ("chrome", "Chrome"), ("edge", "Edge"), ("brave", "Brave"),
        ("chromium", "Chromium"),
    ]


@pytest.mark.parametrize("where", [
    "snap/firefox/common/.mozilla/firefox",
    ".var/app/org.mozilla.firefox/.mozilla/firefox",
    ".var/app/org.mozilla.firefox/config/mozilla/firefox",
    ".config/mozilla/firefox",
])
def test_linux_snap_flatpak_and_xdg_firefox(tmp_path: Path, where: str) -> None:
    _firefox_profile(tmp_path.joinpath(*where.split("/")))
    assert installed_browsers(platform="linux", env={}, home=tmp_path) == [("firefox", "Firefox")]


def test_linux_follows_xdg_config_home(tmp_path: Path) -> None:
    config = tmp_path / "elsewhere"
    (config / "vivaldi").mkdir(parents=True)
    (tmp_path / ".config" / "opera").mkdir(parents=True)  # not where XDG_CONFIG_HOME points
    found = installed_browsers(platform="linux", env={"XDG_CONFIG_HOME": str(config)}, home=tmp_path)
    assert found == [("vivaldi", "Vivaldi")]


def test_default_locations_come_from_the_environment(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    if sys.platform == "win32":
        (tmp_path / "Local" / "Microsoft" / "Edge" / "User Data").mkdir(parents=True)
    else:
        (tmp_path / ".config" / "microsoft-edge").mkdir(parents=True)
    assert installed_browsers() == [("edge", "Edge")]


# --------------------------------------------------------------------------- choices


def test_suggest_prefers_firefox_and_skips_the_one_that_failed() -> None:
    assert suggest_browser(["chrome", "firefox"], platform="linux") == "firefox"
    assert suggest_browser(["chrome", "firefox"], skip="firefox", platform="linux") == "chrome"
    assert suggest_browser(["firefox"], skip="firefox", platform="linux") is None
    assert suggest_browser([], platform="linux") is None


def test_suggest_never_picks_chrome_edge_or_brave_on_windows() -> None:
    assert suggest_browser(["chrome", "edge", "brave"], platform="win32") is None
    assert suggest_browser(["chrome", "edge", "vivaldi"], platform="win32") == "vivaldi"
    assert suggest_browser(["edge", "firefox"], platform="win32") == "firefox"
    assert cookies_locked("edge", "win32") and not cookies_locked("edge", "linux")
    assert not cookies_locked("firefox", "win32")


def test_cookie_settings() -> None:
    from core.downloader import COOKIES_FILE_PREFIX as DOWNLOADER_PREFIX

    assert COOKIES_FILE_PREFIX == DOWNLOADER_PREFIX
    for value in ("", "firefox", "vivaldi", "file:C:/Users/me/cookies.txt"):
        assert valid_cookie_setting(value), value
    for value in ("safari-ish", "file:", "file:   "):
        assert not valid_cookie_setting(value), value
    assert cookies_file_path("file: /home/me/cookies.txt ") == "/home/me/cookies.txt"
    assert cookies_file_path("firefox") == ""


def test_network_paths() -> None:
    assert is_network_path(r"\\nas\share\cookies.txt", platform="linux")
    assert is_network_path("//nas/share/cookies.txt", platform="win32")
    assert not is_network_path("/home/me/cookies.txt", platform="linux")
    assert not is_network_path("C:/Users/me/cookies.txt", platform="linux")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive letters")
def test_local_drive_is_not_a_network_path() -> None:
    import os

    drive = os.environ.get("SystemDrive", "C:")
    assert not is_network_path(f"{drive}\\Users\\me\\cookies.txt", platform="win32")


@pytest.mark.parametrize("value", ["vivaldi", "opera", "file:C:/Users/me/My cookies.txt",
                                   "file:/home/me/my cookies.txt"])
def test_cookie_setting_survives_a_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           value: str) -> None:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(Settings, "path", staticmethod(lambda: path))
    settings = Settings()
    settings.save_dir = str(tmp_path)
    settings.cookies_browser = value
    settings.save()
    assert Settings.load().cookies_browser == value


# --------------------------------------------------------------------------- the list


@pytest.fixture
def found(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The browsers "installed" on this computer: edit the list in a test."""
    import ui.widgets

    detected: list[tuple[str, str]] = [("firefox", "Firefox"), ("edge", "Edge")]
    monkeypatch.setattr(ui.widgets, "installed_browsers", lambda: list(detected))
    return detected


@pytest.fixture
def file_dialog(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """QFileDialog.getOpenFileName answers with state["path"] ("" = cancelled)."""
    state: dict[str, Any] = {"path": "", "asked": 0}

    def fake(*_args: Any, **_kwargs: Any) -> tuple[str, str]:
        state["asked"] += 1
        return state["path"], ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake)
    return state


@pytest.fixture
def platform(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    return lambda name: monkeypatch.setattr(browsers, "PLATFORM", name)


def _items(combo: Any) -> list[tuple[str, Any]]:
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


def test_list_shows_installed_browsers_and_cookies_file(qapp: QApplication, found: Any) -> None:
    from ui.widgets import CHOOSE_COOKIES_FILE, AdvancedPanel

    panel = AdvancedPanel()
    items = _items(panel.cookies_combo)
    assert items[:3] == [("None", ""), ("Firefox", "firefox"), ("Edge", "edge")]
    assert items[-1] == ("cookies.txt file…", CHOOSE_COOKIES_FILE)
    assert not any(data in ("chrome", "brave") for _text, data in items), "only what is installed"
    assert panel.cookie_source() == ""


def test_saved_browser_that_is_missing_stays_visible(qapp: QApplication, found: Any) -> None:
    from ui.widgets import AdvancedPanel

    panel = AdvancedPanel()
    panel.set_cookie_source("chrome")
    combo = panel.cookies_combo
    assert combo.currentText() == "Chrome (not found)"
    assert combo.currentData() == "chrome"
    assert panel.cookie_source() == "chrome"

    found.append(("chrome", "Chrome"))
    panel.refresh_browsers()
    assert combo.currentText() == "Chrome"
    assert "Chrome (not found)" not in [text for text, _data in _items(combo)]


def test_saved_cookies_file_is_listed(qapp: QApplication, found: Any, tmp_path: Path) -> None:
    from ui.widgets import AdvancedPanel

    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    panel = AdvancedPanel()
    panel.set_cookie_source(f"file:{cookies}")
    assert panel.cookies_combo.currentText() == "cookies.txt"
    assert panel.cookie_source() == f"file:{cookies}"

    panel.set_cookie_source(f"file:{tmp_path / 'gone.txt'}")
    assert panel.cookies_combo.currentText() == "gone.txt (not found)"


def test_cookies_file_on_the_network_is_not_checked(qapp: QApplication, found: Any,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import ui.widgets
    from ui.widgets import AdvancedPanel

    checked: list[str] = []

    def isfile(path: Any) -> bool:
        checked.append(str(path))
        return False

    monkeypatch.setattr(ui.widgets.os.path, "isfile", isfile)
    panel = AdvancedPanel()
    unc = "//nas/share/cookies.txt"
    panel.set_cookie_source(COOKIES_FILE_PREFIX + unc)
    assert panel.cookies_combo.currentText() == "cookies.txt", "not marked (not found)"
    assert unc not in checked, "a slow or offline network can't hang the window"


def test_choosing_a_cookies_file(qapp: QApplication, found: Any, file_dialog: dict[str, Any],
                                 tmp_path: Path) -> None:
    from ui.widgets import CHOOSE_COOKIES_FILE, AdvancedPanel

    panel = AdvancedPanel()
    panel.set_cookie_source("firefox")
    changes: list[str] = []
    panel.cookies_changed.connect(changes.append)
    combo = panel.cookies_combo
    choose = combo.findData(CHOOSE_COOKIES_FILE)

    # Cancelled: the previous choice stays.
    combo.setCurrentIndex(choose)
    combo.activated.emit(choose)
    assert file_dialog["asked"] == 1
    assert panel.cookie_source() == "firefox"
    assert combo.currentData() == "firefox"
    assert changes == []

    path = tmp_path / "my cookies.txt"
    path.write_text("", encoding="utf-8")
    file_dialog["path"] = path.as_posix()
    combo.setCurrentIndex(choose)
    combo.activated.emit(choose)
    expected = COOKIES_FILE_PREFIX + str(path)
    assert panel.cookie_source() == expected
    assert combo.currentData() == expected
    assert combo.currentText() == "my cookies.txt"
    assert changes == [expected]

    # Back to a browser: the file stays in the list for later.
    combo.setCurrentIndex(combo.findData("edge"))
    assert panel.cookie_source() == "edge"
    panel.refresh_browsers()
    assert combo.findData(expected) >= 0


def _wheel(combo: Any, down: bool) -> QWheelEvent:
    pos = QPointF(combo.rect().center())
    return QWheelEvent(pos, combo.mapToGlobal(pos), QPoint(), QPoint(0, -120 if down else 120),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


def test_scrolling_the_page_over_the_list_leaves_it_alone(qapp: QApplication, found: Any,
                                                          file_dialog: dict[str, Any]) -> None:
    from ui.widgets import AdvancedPanel

    panel = AdvancedPanel()
    panel.set_cookie_source("firefox")
    combo = panel.cookies_combo
    assert combo.focusPolicy() == Qt.FocusPolicy.StrongFocus, "the wheel doesn't focus it"
    event = _wheel(combo, down=True)
    combo.wheelEvent(event)
    assert not event.isAccepted(), "passed on, so the page scrolls"
    assert panel.cookie_source() == "firefox"
    assert file_dialog["asked"] == 0


def test_wheel_and_arrow_keys_never_open_the_file_dialog(qapp: QApplication, found: Any,
                                                         file_dialog: dict[str, Any]) -> None:
    from ui.widgets import CHOOSE_COOKIES_FILE, AdvancedPanel

    panel = AdvancedPanel()
    panel.set_cookie_source("edge")  # the last browser: "cookies.txt file…" comes next
    combo = panel.cookies_combo
    combo.hasFocus = lambda: True  # clicked or tabbed into
    changes: list[str] = []
    panel.cookies_changed.connect(changes.append)

    combo.wheelEvent(_wheel(combo, down=True))
    assert file_dialog["asked"] == 0
    assert combo.currentData() == "edge"
    QTest.keyClick(combo, Qt.Key.Key_Down)
    assert file_dialog["asked"] == 0
    assert combo.currentData() == "edge"
    assert panel.cookie_source() == "edge"
    assert changes == []

    # Stepping between browsers still works.
    combo.wheelEvent(_wheel(combo, down=False))
    assert panel.cookie_source() == "firefox"
    QTest.keyClick(combo, Qt.Key.Key_Down)
    assert panel.cookie_source() == "edge"
    assert changes == ["firefox", "edge"]

    # Picked from the open list, it asks for a file.
    choose = combo.findData(CHOOSE_COOKIES_FILE)
    combo.setCurrentIndex(choose)
    combo.activated.emit(choose)
    assert file_dialog["asked"] == 1
    assert combo.currentData() == "edge"


# --------------------------------------------------------------------------- the window


class StubDeps(QObject):
    """Every tool "installed", nothing on disk."""

    checked = Signal()
    updated = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.checking = False
        self.has_checked = True

    def tool(self, key: str) -> ToolInfo:
        return ToolInfo(key=key, path=Path("stub-bin") / key, version="1.0", bundled=True)

    def missing(self) -> list[str]:
        return []

    def refresh(self) -> None:
        pass


@pytest.fixture(scope="module")
def theme_manager(qapp: QApplication) -> Any:
    from ui.design import DESIGN
    from ui.theme import ThemeManager

    return ThemeManager(qapp, DESIGN)


@pytest.fixture
def window(qapp: QApplication, theme_manager: Any, tmp_path: Path, found: Any
           ) -> Iterator[Any]:
    import shiboken6

    from core.presets import PresetStore
    from ui.main_window import MainWindow

    settings = Settings()
    settings._normalize()
    settings.save_dir = str(tmp_path / "downloads")
    settings.save = lambda: None  # type: ignore[method-assign]  # never touch a settings file
    win = MainWindow(settings, PresetStore.load(), StubDeps(), theme_manager)
    win.manager._schedule = lambda: None  # jobs stay "Waiting": no yt-dlp runs
    yield win
    qapp.processEvents()  # pending single-shot work (e.g. scrolling new rows into view)
    win._save_timer.stop()
    win.hide()
    shiboken6.delete(win)
    qapp.processEvents()


def _failed_job(win: Any, cookies: str) -> int:
    """A download that failed while reading cookies from `cookies`."""
    options = win._current_options()
    options.cookies_browser = cookies
    return win.manager.add(URL, win._current_preset(), options)


def _use_cookies(win: Any, job_id: int) -> str:
    from core.errors import ACTION_COOKIES

    win._on_error_action(job_id, ACTION_COOKIES)
    return win.statusBar().currentMessage()


def test_fix_picks_firefox_when_it_is_installed(window: Any, platform: Any) -> None:
    platform("win32")
    message = _use_cookies(window, _failed_job(window, ""))
    assert window.advanced_toggle.isChecked()
    assert window.advanced_panel.cookie_source() == "firefox"
    assert "Firefox" in message


def test_fix_never_picks_a_browser_that_is_not_installed(window: Any, found: Any,
                                                         platform: Any) -> None:
    platform("linux")
    found[:] = [("chrome", "Chrome")]
    window.advanced_panel.refresh_browsers()
    window.advanced_panel.set_cookie_source("firefox")  # saved earlier, Firefox since removed
    message = _use_cookies(window, _failed_job(window, "firefox"))
    assert window.advanced_panel.cookie_source() == "chrome"
    assert "Chrome" in message


def test_fix_moves_on_from_the_browser_that_failed(window: Any, found: Any, platform: Any) -> None:
    platform("linux")
    found[:] = [("firefox", "Firefox"), ("chromium", "Chromium")]
    window.advanced_panel.set_cookie_source("firefox")
    _use_cookies(window, _failed_job(window, "firefox"))
    assert window.advanced_panel.cookie_source() == "chromium"


def test_fix_never_goes_back_to_the_browser_that_failed(window: Any, found: Any,
                                                        platform: Any) -> None:
    platform("linux")
    found[:] = [("firefox", "Firefox"), ("chromium", "Chromium")]
    job_id = _failed_job(window, "firefox")
    window.advanced_panel.set_cookie_source("")  # cookies turned off since it failed
    _use_cookies(window, job_id)
    assert window.advanced_panel.cookie_source() == "chromium"


def test_fix_keeps_the_only_browser_and_says_to_sign_in(window: Any, found: Any,
                                                        platform: Any, file_dialog: Any) -> None:
    platform("linux")
    found[:] = [("firefox", "Firefox")]
    window.advanced_panel.set_cookie_source("firefox")
    message = _use_cookies(window, _failed_job(window, "firefox"))
    assert window.advanced_panel.cookie_source() == "firefox"
    assert "signed in to the site in Firefox" in message
    assert file_dialog["asked"] == 0


@pytest.mark.parametrize("system, browsers", [
    ("linux", [("firefox", "Firefox")]),
    ("win32", [("firefox", "Firefox"), ("chrome", "Chrome")]),  # Chrome's cookies are locked
])
def test_fix_goes_back_to_the_browser_that_failed_when_nothing_else_can_be_read(
        window: Any, found: Any, platform: Any, file_dialog: Any, system: str,
        browsers: list[tuple[str, str]]) -> None:
    platform(system)
    found[:] = browsers
    job_id = _failed_job(window, "firefox")
    window.advanced_panel.set_cookie_source("")  # cookies turned off since it failed
    message = _use_cookies(window, job_id)
    assert window.advanced_panel.cookie_source() == "firefox"
    assert message.startswith("Cookies from Firefox didn't work. Make sure you're signed in to "
                              "the site in Firefox, then click Retry.")
    assert "wasn't found" not in message and "No browser" not in message
    assert file_dialog["asked"] == 0


@pytest.mark.parametrize("system, installed, expected", [
    ("linux", ["firefox"],
     "Make sure you're signed in to the site in Firefox, or choose a cookies.txt file"),
    ("win32", ["firefox", "chrome"],
     "Chrome keeps its cookies locked away from other apps on Windows. Make sure you're "
     "signed in to the site in Firefox, or choose a cookies.txt file"),
    ("win32", ["chrome"],
     "Firefox wasn't found, and Chrome keeps its cookies locked away from other apps on "
     "Windows. Install Firefox and sign in to the site there, or choose a cookies.txt file"),
])
def test_cookies_file_request_only_says_what_is_true_here(window: Any, platform: Any,
                                                          file_dialog: Any, system: str,
                                                          installed: list[str],
                                                          expected: str) -> None:
    platform(system)
    window._ask_for_cookies_file(installed, "")
    message = window.statusBar().currentMessage()
    assert message.startswith(expected)
    if "firefox" in installed:
        assert "Firefox wasn't found" not in message and "Install Firefox" not in message
    assert "No browser" not in message, "a browser was found"
    assert file_dialog["asked"] == 1


def test_fix_keeps_a_choice_made_after_the_failure(window: Any, platform: Any) -> None:
    platform("win32")
    job_id = _failed_job(window, "")
    window.advanced_panel.set_cookie_source("edge")
    message = _use_cookies(window, job_id)
    assert window.advanced_panel.cookie_source() == "edge"
    assert "Edge" in message


def test_fix_on_windows_with_only_chrome_and_edge_asks_for_a_cookies_file(
        window: Any, found: Any, platform: Any, file_dialog: Any, tmp_path: Path) -> None:
    platform("win32")
    found[:] = [("chrome", "Chrome"), ("edge", "Edge")]
    job_id = _failed_job(window, "")

    message = _use_cookies(window, job_id)  # cancelled
    assert file_dialog["asked"] == 1
    assert window.advanced_panel.cookie_source() == ""
    assert "Firefox wasn't found" in message
    assert "Chrome and Edge keep their cookies locked" in message
    assert "cookies.txt" in message

    cookies = tmp_path / "cookies.txt"
    file_dialog["path"] = str(cookies)
    message = _use_cookies(window, job_id)
    assert file_dialog["asked"] == 2
    assert window.advanced_panel.cookie_source() == COOKIES_FILE_PREFIX + str(cookies)
    assert "cookies.txt" in message


def test_fix_after_edge_failed_on_windows_does_not_loop(window: Any, found: Any, platform: Any,
                                                        file_dialog: Any) -> None:
    platform("win32")
    found[:] = [("edge", "Edge")]
    window.advanced_panel.set_cookie_source("edge")
    message = _use_cookies(window, _failed_job(window, "edge"))  # cancelled
    assert file_dialog["asked"] == 1, "offers a cookies.txt file instead of Edge again"
    assert "Edge keeps its cookies locked" in message
    assert "cookies are off" in message
    assert window.advanced_panel.cookie_source() == "", "later downloads don't fail the same way"


def test_fix_turns_off_a_saved_browser_that_is_not_installed(window: Any, found: Any,
                                                             platform: Any,
                                                             file_dialog: Any) -> None:
    # Weir 1.0.0 picked Firefox for everyone; this computer only has Chrome and Edge.
    platform("win32")
    found[:] = [("chrome", "Chrome"), ("edge", "Edge")]
    panel = window.advanced_panel
    panel.refresh_browsers()
    panel.set_cookie_source("firefox")
    assert panel.cookies_combo.currentText() == "Firefox (not found)"

    message = _use_cookies(window, _failed_job(window, "firefox"))  # cancelled
    assert file_dialog["asked"] == 1
    assert panel.cookie_source() == ""
    assert window._current_options().cookies_browser == ""
    assert message.startswith("Firefox wasn't found, so cookies are off for now.")
    assert "Chrome and Edge keep their cookies locked" in message
    assert "Firefox (not found)" not in [text for text, _data in _items(panel.cookies_combo)]


def test_fix_turns_off_a_missing_browser_when_nothing_else_is_there(window: Any, found: Any,
                                                                   platform: Any,
                                                                   file_dialog: Any) -> None:
    platform("linux")
    found[:] = []
    window.advanced_panel.set_cookie_source("chrome")
    message = _use_cookies(window, _failed_job(window, "chrome"))  # cancelled
    assert window.advanced_panel.cookie_source() == ""
    assert message.startswith("Chrome wasn't found, so cookies are off for now. "
                              "No other browser with cookies was found.")


def test_fix_with_no_browser_at_all(window: Any, found: Any, platform: Any,
                                    file_dialog: Any) -> None:
    platform("linux")
    found[:] = []
    message = _use_cookies(window, _failed_job(window, ""))
    assert file_dialog["asked"] == 1
    assert window.advanced_panel.cookie_source() == ""
    assert "No browser with cookies was found" in message


def test_fix_after_a_cookies_file_failed_asks_for_a_fresh_one(window: Any, file_dialog: Any,
                                                              tmp_path: Path) -> None:
    old = COOKIES_FILE_PREFIX + str(tmp_path / "old.txt")
    window.advanced_panel.set_cookie_source(old)
    message = _use_cookies(window, _failed_job(window, old))
    assert file_dialog["asked"] == 1
    assert window.advanced_panel.cookie_source() == old
    assert "fresh cookies.txt" in message


def test_cookie_choice_is_saved_and_used(window: Any, tmp_path: Path) -> None:
    value = COOKIES_FILE_PREFIX + str(tmp_path / "cookies.txt")
    window.advanced_panel.set_cookie_source(value)
    assert window._current_options().cookies_browser == value
    window._save_settings()
    assert window.settings.cookies_browser == value


# --------------------------------------------------------------------------- duplicate links


def test_links_already_in_the_list_are_not_added_again(window: Any) -> None:
    other = "https://example.com/watch?v=two"
    window.url_edit.add_links([URL])
    window._start_downloads()
    assert [job.url for job in window.manager.jobs()] == [URL]
    assert window.statusBar().currentMessage() == "Added 1 download to the list."

    window.url_edit.add_links([URL, other])
    window._start_downloads()
    assert [job.url for job in window.manager.jobs()] == [URL, other]
    assert window.statusBar().currentMessage() == (
        "Added 1 download to the list. 1 link was already in the list."
    )
    assert window.url_edit.links() == []

    window.url_edit.add_links([URL, other])
    window._start_downloads()
    assert len(window.manager.jobs()) == 2
    assert window.statusBar().currentMessage() == "2 links were already in the list."


def test_a_link_listed_twice_is_added_once(window: Any) -> None:
    window.url_edit.add_links([URL, URL + "/"])  # the same link, once with a slash at the end
    window._start_downloads()
    assert [job.url for job in window.manager.jobs()] == [URL]
    assert window.statusBar().currentMessage() == (
        "Added 1 download to the list. 1 link was listed twice."
    )


def test_retry_skips_a_link_that_was_added_again(window: Any) -> None:
    failed = _failed_job(window, "")
    window.manager.get(failed).status = JobStatus.ERROR
    window.url_edit.add_links([URL])
    window._start_downloads()  # the same link again: now waiting
    again = [job.id for job in window.manager.jobs() if job.id != failed]
    assert len(again) == 1

    window._retry_jobs([failed])
    assert window.manager.get(failed).status == JobStatus.ERROR, "no second copy"
    assert window.statusBar().currentMessage() == (
        "Not retried: that link is already waiting or downloading in the list."
    )

    window.manager.cancel(again[0])
    window._retry_jobs([failed])
    assert window.manager.get(failed).status == JobStatus.WAITING


def test_whole_playlist_of_a_video_in_the_list_is_added(window: Any) -> None:
    alone = "https://www.youtube.com/watch?v=AAAAAAAAAAA&list=PLxyz"
    window.playlist_check.setChecked(False)
    window.url_edit.add_links([alone])
    window._start_downloads()

    whole = "https://youtu.be/AAAAAAAAAAA?list=PLxyz&si=share"
    window.playlist_check.setChecked(True)
    window.url_edit.add_links([whole])
    window._start_downloads()
    assert [(job.url, job.options.playlist) for job in window.manager.jobs()] == [
        (alone, False), (whole, True),
    ]
    assert window.statusBar().currentMessage() == "Added 1 download to the list."
    assert window.url_edit.links() == []

    # Another video of that playlist, with the playlist on: the same playlist again.
    window.url_edit.add_links(["https://www.youtube.com/watch?v=BBBBBBBBBBB&list=PLxyz&index=2"])
    window._start_downloads()
    assert len(window.manager.jobs()) == 2
    assert window.statusBar().currentMessage() == "1 link was for a playlist already in the list."


def test_links_into_one_playlist_pasted_together_are_added_once(window: Any) -> None:
    first = "https://www.youtube.com/watch?v=AAAAAAAAAAA&list=PLxyz&index=1"
    window.playlist_check.setChecked(True)
    window.url_edit.add_links([
        first,
        "https://www.youtube.com/watch?v=BBBBBBBBBBB&list=PLxyz&index=2",  # another video of it
        "https://youtu.be/AAAAAAAAAAA?list=PLxyz&si=share",  # the first link again
    ])
    window._start_downloads()
    assert [job.url for job in window.manager.jobs()] == [first]
    # Only the real repeat counts as "listed twice".
    assert window.statusBar().currentMessage() == (
        "Added 1 download to the list. 1 link was listed twice. "
        "1 link was for a playlist already in the list."
    )


def test_a_shorts_link_with_a_list_is_not_its_video_alone(window: Any) -> None:
    # yt-dlp fetches the whole list for it even with the playlist off.
    window.playlist_check.setChecked(False)
    window.url_edit.add_links(["https://www.youtube.com/watch?v=AAAAAAAAAAA"])
    window._start_downloads()
    window.url_edit.add_links(["https://www.youtube.com/shorts/AAAAAAAAAAA?list=PLxyz"])
    window._start_downloads()
    assert len(window.manager.jobs()) == 2
    assert window.statusBar().currentMessage() == "Added 1 download to the list."


def test_retry_says_when_its_playlist_is_already_in_the_list(window: Any) -> None:
    window.playlist_check.setChecked(True)
    entries = []
    for link in ("https://www.youtube.com/watch?v=AAAAAAAAAAA&list=PLxyz",
                 "https://www.youtube.com/watch?v=BBBBBBBBBBB&list=PLxyz"):
        window.url_edit.add_links([link])
        window._start_downloads()
        entries.append(window.manager.jobs()[-1])
        entries[-1].status = JobStatus.ERROR
    window.url_edit.add_links(["https://www.youtube.com/playlist?list=PLxyz"])
    window._start_downloads()  # waiting now: the whole playlist
    assert len(window.manager.jobs()) == 3

    window._retry_jobs([entries[0].id])
    assert entries[0].status == JobStatus.ERROR, "no second copy"
    assert window.statusBar().currentMessage() == (
        "Not retried: its playlist is already waiting or downloading in the list."
    )
    window._retry_jobs([job.id for job in entries])
    assert window.statusBar().currentMessage() == (
        "2 downloads weren't retried: their links or playlists are already waiting or "
        "downloading in the list."
    )


def test_retry_of_a_whole_playlist_is_not_blocked_by_its_video_alone(window: Any) -> None:
    link = "https://www.youtube.com/watch?v=AAAAAAAAAAA&list=PLxyz"
    window.playlist_check.setChecked(True)
    window.url_edit.add_links([link])
    window._start_downloads()
    (whole,) = window.manager.jobs()
    whole.status = JobStatus.ERROR
    window.playlist_check.setChecked(False)
    window.url_edit.add_links([link])
    window._start_downloads()  # waiting now: only video AAAAAAAAAAA
    assert len(window.manager.jobs()) == 2

    window._retry_jobs([whole.id])
    assert whole.status == JobStatus.WAITING
    assert whole.options.playlist, "still the whole playlist"


def test_the_same_link_with_another_preset_is_added(window: Any) -> None:
    window.url_edit.add_links([URL])
    window._start_downloads()
    first = window._current_preset()
    other = next(i for i in range(window.preset_combo.count())
                 if window.preset_combo.itemText(i) != window.preset_combo.currentText())
    window.preset_combo.setCurrentIndex(other)  # e.g. the MP3 of the same video
    assert window._current_preset().args != first.args
    window.url_edit.add_links([URL])
    window._start_downloads()
    assert [job.url for job in window.manager.jobs()] == [URL, URL]
    assert window.statusBar().currentMessage() == "Added 1 download to the list."
