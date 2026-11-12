"""Weir — a simple desktop window for yt-dlp.

Run from source:  python main.py [links...]
"""

from __future__ import annotations

import sys
import traceback

import shiboken6
from types import TracebackType

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from core import APP_DESKTOP_ID, APP_DISPLAY_NAME, APP_NAME, APP_VERSION
from core.deps import DepsManager
from core.paths import IS_WINDOWS, config_dir, sanitize_inherited_environment
from core.presets import PresetStore
from core.settings import Settings
from core.single_instance import SingleInstance
from ui.icon import app_icon
from ui.main_window import MainWindow
from ui.theme import ThemeManager
from ui.design import DESIGN
from ui.widgets import extract_links_from_text


def _set_windows_app_id() -> None:
    """Group the taskbar button and notifications under our own name and icon."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.App")
    except (AttributeError, OSError):
        pass


def _install_excepthook() -> None:
    """Never fail silently: log unexpected errors and tell the user."""
    shown = False

    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        nonlocal shown
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if sys.__stderr__ is not None:
            sys.__stderr__.write(text)
        log_path = config_dir() / "error.log"
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass
        if not shown and QApplication.instance() is not None:
            shown = True
            QMessageBox.critical(
                None, APP_DISPLAY_NAME,
                f"Something unexpected went wrong. Details were saved to:\n{log_path}\n\n{exc}",
            )

    sys.excepthook = hook


def _show_handed_over_links(window: MainWindow, links: list[str]) -> None:
    """Weir was started again: take the new launch's links and bring the window forward."""
    found = extract_links_from_text("\n".join(links))
    if found:
        window.url_edit.add_links(found)
    if window.isMaximized():  # also true while minimized from maximized
        window.showMaximized()
    else:
        window.showNormal()
    window.raise_()
    window.activateWindow()


def _answer_new_launches(instance: SingleInstance, window: MainWindow) -> None:
    """Pass later launches on to the window, until it starts quitting."""
    instance.activation_requested.connect(
        lambda received: _show_handed_over_links(window, received)
    )
    # Links handed to a window that is closing would be lost: from then on a new launch
    # waits for this Weir to exit and then starts fresh.
    window.shutdown_started.connect(instance.stop_listening)


def main() -> int:
    sanitize_inherited_environment()
    _set_windows_app_id()
    QGuiApplication.setApplicationName(APP_NAME)
    QGuiApplication.setApplicationDisplayName(APP_DISPLAY_NAME)
    QGuiApplication.setApplicationVersion(APP_VERSION)
    QGuiApplication.setDesktopFileName(APP_DESKTOP_ID)

    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    _install_excepthook()

    links = extract_links_from_text(" ".join(sys.argv[1:]))
    # One Weir per user: two windows would delete each other's partial downloads and
    # overwrite each other's settings. A later launch hands its links over and quits
    # before it creates settings, tool checks or windows.
    instance = SingleInstance()
    if instance.start(links):
        return 0

    settings = Settings.load()
    # Fusion + Weir's palette and stylesheet; follows the system light/dark setting.
    theme = ThemeManager(app, DESIGN)
    presets = PresetStore.load()
    deps = DepsManager()
    window = MainWindow(settings, presets, deps, theme)
    if links:
        window.url_edit.add_links(links)
    _answer_new_launches(instance, window)
    window.show()
    deps.refresh()  # version checks run in a background thread
    code = app.exec()
    instance.stop_listening()  # in case Weir quit without the window announcing it
    # Destroy the window while Python is still fully alive, so Qt never calls back into
    # half-torn-down Python objects (event filters, overridden methods) at exit.
    shiboken6.delete(window)
    # Last: a launch that is waiting for this Weir to exit starts as soon as this is free.
    instance.release()
    return code


if __name__ == "__main__":
    sys.exit(main())
