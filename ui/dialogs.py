"""Dialogs: dependency wizard, yt-dlp updater, error details, About."""

from __future__ import annotations

import codecs
import html
import platform
import sys

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QProcess, Qt, QTimer, Signal, qVersion
from PySide6.QtGui import QCloseEvent, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import APP_DISPLAY_NAME, APP_VERSION
from core.deps import (
    TOOL_DESCRIPTIONS,
    TOOL_KEYS,
    TOOL_LABELS,
    DependencyInstaller,
    DepsManager,
    ToolInfo,
    asset_for,
    describe,
)
from core.errors import FriendlyError
from core.paths import IS_LINUX, writable_bin_dir
from core.process import configure_process, qprocess_environment
from core.settings import Settings

from . import theme
from .widgets import Card


def _mono_font_edit(parent: QWidget) -> QPlainTextEdit:
    edit = QPlainTextEdit(parent)
    edit.setReadOnly(True)
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return edit


class DepsDialog(QDialog):
    """First-run wizard / tools manager with a one-click download."""

    tools_changed = Signal()

    def __init__(self, deps: DepsManager, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tools used by the downloader")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(620)
        self._deps = deps
        self._settings = settings
        self._installer = DependencyInstaller(self)
        self._failures: list[str] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        try:
            target = str(writable_bin_dir())
        except OSError:
            target = "the app's bin folder"
        intro = QLabel(
            "This app relies on three free tools. Anything missing can be downloaded for you "
            "from its official GitHub release page into:<br>"
            f"<code>{html.escape(target)}</code>",
            self,
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(intro)

        card = Card(self)
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 16, 18, 16)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)
        self._status: dict[str, QLabel] = {}
        self._buttons: dict[str, QPushButton] = {}
        for row, key in enumerate(TOOL_KEYS):
            name = QLabel(
                f"<b>{TOOL_LABELS[key]}</b><br><small>{html.escape(TOOL_DESCRIPTIONS[key])}"
                f" Download size {asset_for(key).approx_size}.</small>",
                self,
            )
            name.setWordWrap(True)
            status = QLabel(self)
            status.setWordWrap(True)
            status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            button = QPushButton("Download", self)
            button.clicked.connect(lambda _checked=False, k=key: self.start_install([k]))
            grid.addWidget(name, row, 0)
            grid.addWidget(status, row, 1)
            grid.addWidget(button, row, 2, Qt.AlignmentFlag.AlignVCenter)
            self._status[key] = status
            self._buttons[key] = button
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        layout.addWidget(card)

        self._linux_hint = QLabel(
            "Tip: on Linux a system ffmpeg works too, e.g. <code>sudo apt install ffmpeg</code> "
            "or <code>sudo dnf install ffmpeg</code>.",
            self,
        )
        self._linux_hint.setWordWrap(True)
        self._linux_hint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._linux_hint)

        self._progress = QProgressBar(self)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        self._message = QLabel(self)
        self._message.setWordWrap(True)
        self._message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._message)

        self._dont_ask = QCheckBox("Don't show this at startup when only optional tools are missing", self)
        self._dont_ask.setChecked(settings.skip_startup_deps_prompt)
        layout.addWidget(self._dont_ask)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._install_all = QPushButton("Download it for me", self)
        self._install_all.setObjectName("primary")
        self._install_all.setDefault(True)
        self._install_all.clicked.connect(lambda: self.start_install(self._deps.missing()))
        self._close_button = QPushButton("Close", self)
        self._close_button.clicked.connect(self.reject)
        buttons.addWidget(self._install_all)
        buttons.addWidget(self._close_button)
        layout.addLayout(buttons)

        self._installer.progress.connect(self._on_progress)
        self._installer.message.connect(self._message.setText)
        self._installer.tool_finished.connect(self._on_tool_finished)
        self._installer.all_finished.connect(self._on_all_finished)
        self._deps.checked.connect(self._refresh)
        self._refresh()

    @property
    def busy(self) -> bool:
        return self._installer.busy

    def stop(self) -> None:
        """Abort a running download without asking (used when the app quits)."""
        self._installer.cancel()

    def set_startup_mode(self, startup: bool) -> None:
        self._dont_ask.setVisible(startup)

    def start_install(self, keys: list[str]) -> None:
        if self._installer.busy or not keys:
            return
        self._failures = []
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._set_buttons_enabled(False)
        self._close_button.setText("Stop")
        self._installer.install(keys)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in self._buttons.values():
            button.setEnabled(enabled)
        self._install_all.setEnabled(enabled)

    def _refresh(self) -> None:
        missing = self._deps.missing()
        for key in TOOL_KEYS:
            info = self._deps.tool(key)
            label = self._status[key]
            if self._deps.checking and not self._deps.has_checked:
                label.setText("Checking…")
            elif info.found:
                label.setText(f"<span style='color:{theme.current().success}'>✔</span> "
                              f"{html.escape(describe(info))}")
            else:
                label.setText(f"<span style='color:{theme.current().danger}'>✖</span> "
                              f"{html.escape(describe(info))}")
            self._buttons[key].setText("Re-download" if info.found else "Download")
            self._buttons[key].setToolTip(
                "Download the latest version into the app's tools folder" if info.found
                else f"Download {TOOL_LABELS[key]} for me"
            )
        self._linux_hint.setVisible(IS_LINUX and "ffmpeg" in missing)
        self._install_all.setVisible(bool(missing))
        if missing:
            names = ", ".join(TOOL_LABELS[k] for k in missing)
            self._install_all.setText(f"Download it for me ({names})")
        if not self._installer.busy:
            self._set_buttons_enabled(True)
            if not missing and self._deps.has_checked and not self._failures:
                self._message.setText("All set! Everything the app needs is installed.")

    def _on_progress(self, _key: str, received: float, total: float) -> None:
        if total > 0:
            self._progress.setRange(0, 1000)
            self._progress.setValue(int(received / total * 1000))
        else:
            self._progress.setRange(0, 0)

    def _on_tool_finished(self, key: str, ok: bool, text: str) -> None:
        if not ok:
            self._failures.append(f"{TOOL_LABELS[key]}: {text}")

    def _on_all_finished(self, _ok: bool) -> None:
        self._progress.setVisible(False)
        self._close_button.setText("Close")
        if self._failures:
            self._message.setText("Something went wrong:\n" + "\n".join(self._failures))
        else:
            self._message.setText("Download complete. Checking the tools…")
        self._deps.refresh()
        self.tools_changed.emit()
        self._refresh()

    def reject(self) -> None:
        if self._installer.busy:
            answer = QMessageBox.question(
                self, "Stop downloading?", "A tool is still downloading. Stop it?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._installer.cancel()
            return
        self._settings.skip_startup_deps_prompt = self._dont_ask.isChecked()
        self._settings.save()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._installer.busy:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)


class UpdateDialog(QDialog):
    """Runs `yt-dlp -U` and shows its output."""

    update_finished = Signal()

    def __init__(self, ytdlp: ToolInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update yt-dlp")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(640, 360)
        self._tool = ytdlp
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        layout = QVBoxLayout(self)
        self._label = QLabel(f"Running <code>{html.escape(ytdlp.path.name if ytdlp.path else 'yt-dlp')} -U</code>…", self)
        layout.addWidget(self._label)
        self._output = _mono_font_edit(self)
        layout.addWidget(self._output, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self._close = box.button(QDialogButtonBox.StandardButton.Close)
        self._close.setEnabled(False)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.setProcessEnvironment(qprocess_environment())
        configure_process(self._proc)
        self._proc.readyReadStandardOutput.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

    def start(self) -> None:
        if self._tool.path is None:
            self._output.setPlainText("yt-dlp was not found.")
            self._close.setEnabled(True)
            return
        self._proc.start(str(self._tool.path), ["-U"])

    def _append(self, text: str) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        cursor = self._output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    def _on_output(self) -> None:
        self._append(self._decoder.decode(bytes(self._proc.readAllStandardOutput().data())))

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._append(self._decoder.decode(b"", final=True))
        if exit_code == 0:
            self._label.setText("Finished.")
        else:
            self._label.setText(f"yt-dlp exited with code {exit_code}.")
            if not self._tool.bundled:
                self._append(
                    "\nThis copy of yt-dlp was installed outside the app and may not be able to update "
                    "itself. Use Settings → Tools → Re-download to get the app's own copy.\n"
                )
        self._close.setEnabled(True)
        self.update_finished.emit()

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._append(f"Couldn't start yt-dlp: {self._proc.errorString()}\n")
            self._label.setText("Update failed.")
            self._close.setEnabled(True)

    def reject(self) -> None:
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            return  # don't interrupt a self-update half way
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            event.ignore()
            return
        super().closeEvent(event)


class ErrorDetailsDialog(QDialog):
    """Plain-language explanation plus the raw lines, with Copy."""

    action_requested = Signal(str)
    retry_requested = Signal()

    def __init__(self, job_title: str, url: str, error: FriendlyError, action_label: str | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Download failed")
        self.resize(680, 420)
        self._error = error
        self._url = url

        layout = QVBoxLayout(self)
        headline = QLabel(f"<h3 style='margin:0'>{html.escape(error.title)}</h3>", self)
        layout.addWidget(headline)
        hint = QLabel(html.escape(error.hint), self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        info = QLabel(
            f"<small><b>{html.escape(job_title)}</b><br>{html.escape(url)}</small>", self
        )
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(info)
        layout.addWidget(QLabel("Last lines from yt-dlp:", self))
        details = _mono_font_edit(self)
        details.setPlainText(error.details)
        layout.addWidget(details, 1)

        buttons = QHBoxLayout()
        self._copy = QPushButton("Copy", self)
        self._copy.clicked.connect(self._copy_text)
        buttons.addWidget(self._copy)
        buttons.addStretch(1)
        if error.action and action_label:
            action = QPushButton(action_label, self)
            action.setObjectName("accentSmall")
            action.clicked.connect(lambda: (self.action_requested.emit(error.action or ""), self.accept()))
            buttons.addWidget(action)
        retry = QPushButton("Retry", self)
        retry.clicked.connect(lambda: (self.retry_requested.emit(), self.accept()))
        buttons.addWidget(retry)
        close = QPushButton("Close", self)
        close.setDefault(True)
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _copy_text(self) -> None:
        QGuiApplication.clipboard().setText(self._error.as_text(self._url))
        self._copy.setText("Copied!")
        QTimer.singleShot(1500, lambda: self._copy.setText("Copy"))


def show_about(parent: QWidget, deps: DepsManager) -> None:
    rows: list[str] = []
    for key in TOOL_KEYS:
        info = deps.tool(key)
        if info.found:
            version = info.version or ("checking…" if deps.checking else "unknown version")
            where = html.escape(str(info.path)) if info.path else ""
            rows.append(f"<tr><td><b>{TOOL_LABELS[key]}</b></td><td>{html.escape(version)}"
                        f"<br><small>{where}</small></td></tr>")
        else:
            rows.append(f"<tr><td><b>{TOOL_LABELS[key]}</b></td><td>not installed</td></tr>")
    text = (
        f"<h3>{APP_DISPLAY_NAME} {APP_VERSION}</h3>"
        "<p>A simple window for "
        "<a href='https://github.com/yt-dlp/yt-dlp'>yt-dlp</a>.</p>"
        f"<table cellspacing='6'>{''.join(rows)}</table>"
        f"<p><small>Qt {qVersion()} · PySide6 {PYSIDE_VERSION} · Python {platform.python_version()}"
        f" · {html.escape(sys.platform)}</small></p>"
        "<p><small>yt-dlp is released under the Unlicense, the FFmpeg builds under the GPL, "
        "Deno under the MIT license. Only download content you have the right to.</small></p>"
    )
    QMessageBox.about(parent, f"About {APP_DISPLAY_NAME}", text)
