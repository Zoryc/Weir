"""Dialogs: dependency wizard, yt-dlp updater, error details, About."""

from __future__ import annotations

import codecs
import html
import platform
import re
import sys
from collections.abc import Iterable

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QPoint, QProcess, Qt, QTimer, Signal, qVersion
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
    QToolTip,
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


def _approx_mb(key: str) -> float:
    """Middle of a tool's approximate download size ("~25–40 MB" -> 32.5)."""
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", asset_for(key).approx_size)]
    return sum(numbers) / len(numbers) if numbers else 0.0


def download_size_text(keys: Iterable[str]) -> str:
    """Rough total download size of some tools, e.g. "about 280 MB"."""
    total = sum(_approx_mb(key) for key in keys)
    step = 5 if total < 100 else 10
    return f"about {max(step, int(total / step + 0.5) * step)} MB"


def join_names(names: list[str]) -> str:
    """["a", "b", "c"] -> "a, b and c"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


TITLE_TOOLS = "Tools used by the downloader"
TITLE_WELCOME = f"Welcome to {APP_DISPLAY_NAME}: one last step"
COPIED_FEEDBACK_MS = 1500  # how long the error details' Copy button reads "Copied!"


def _mono_font_edit(parent: QWidget) -> QPlainTextEdit:
    edit = QPlainTextEdit(parent)
    edit.setReadOnly(True)
    edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return edit


class DepsDialog(QDialog):
    """First-run wizard / tools manager with a one-click download."""

    tools_changed = Signal()  # an install run ended (whatever its outcome)

    def __init__(self, deps: DepsManager, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE_TOOLS)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(620)
        self._deps = deps
        self._settings = settings
        self._installer = DependencyInstaller(self)
        self._failures: dict[str, str] = {}  # tool -> what went wrong in the last install
        self._stopping = False  # the user stopped the current/last install
        self._close_when_done = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        try:
            target = str(writable_bin_dir())
        except OSError:
            target = "the app's bin folder"
        intro_row = QHBoxLayout()
        intro_row.setSpacing(8)
        intro = QLabel(
            f"{APP_DISPLAY_NAME} uses three free helper tools to download and convert videos. "
            "Anything not installed yet can be downloaded and set up for you in one click.",
            self,
        )
        intro.setWordWrap(True)
        intro_row.addWidget(intro, 1)
        self._details = QPushButton("Details", self)
        self._details.setObjectName("flat")
        self._details.setCursor(Qt.CursorShape.PointingHandCursor)
        self._details.setToolTip(
            "Each tool comes from its official release page on GitHub and is checked against "
            f"the checksum published with it.\n\nDownloaded tools are saved in:\n{target}"
        )
        self._details.clicked.connect(self._show_details)
        intro_row.addWidget(self._details, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(intro_row)

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

        # Matches the rows: yt-dlp is "Required", ffmpeg and Deno are "(Strongly) recommended".
        self._dont_ask = QCheckBox(
            "Don't show this at startup when only recommended tools are missing", self,
        )
        self._dont_ask.setChecked(settings.skip_startup_deps_prompt)
        layout.addWidget(self._dont_ask)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._install_all = QPushButton("Download it for me", self)
        self._install_all.setObjectName("primary")
        self._install_all.setDefault(True)
        self._install_all.clicked.connect(
            lambda: self.start_install(self._deps.missing(), close_when_done=True)
        )
        self._close_button = QPushButton("Close", self)
        self._close_button.clicked.connect(self.reject)
        buttons.addWidget(self._install_all)
        buttons.addWidget(self._close_button)
        layout.addLayout(buttons)

        # What each tool is doing while an install runs: "waiting", "downloading", "installing".
        self._stages: dict[str, str] = {}
        self._percent: dict[str, int] = {}
        self._installer.progress.connect(self._on_progress)
        self._installer.message.connect(self._message.setText)
        self._installer.stage.connect(self._on_stage)
        self._installer.tool_finished.connect(self._on_tool_finished)
        self._installer.all_finished.connect(self._on_all_finished)
        self._deps.checked.connect(self._refresh)
        self._deps.updated.connect(self._refresh)
        self._refresh()

    @property
    def busy(self) -> bool:
        return self._installer.busy

    @property
    def stopped_by_user(self) -> bool:
        """True when the user stopped the current (or last) install with "Stop"."""
        return self._stopping

    def stop(self) -> None:
        """Abort a running download without asking (used when the app quits)."""
        if self._installer.busy:
            self._stopping = True
        self._installer.cancel()

    def set_startup_mode(self, startup: bool) -> None:
        self._dont_ask.setVisible(startup)
        self.setWindowTitle(TITLE_WELCOME if startup else TITLE_TOOLS)

    def start_install(self, keys: list[str], close_when_done: bool = False) -> None:
        """Download and install these tools. With close_when_done, the window closes by
        itself once they all installed and nothing is missing any more."""
        if self._installer.busy or not keys:
            return
        self._failures = {}
        self._stopping = False
        self._close_when_done = close_when_done
        self._stages = {key: "waiting" for key in keys}
        self._percent = {}
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._set_buttons_enabled(False)
        self._close_button.setText("Stop")
        self._refresh()
        self._installer.install(keys)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in self._buttons.values():
            button.setEnabled(enabled)
        self._install_all.setEnabled(enabled)

    def _show_details(self) -> None:
        QToolTip.showText(
            self._details.mapToGlobal(QPoint(0, self._details.height())),
            self._details.toolTip(), self._details,
        )

    def _status_html(self, key: str) -> str:
        """One row's status. A red ✖ only for a real failure (the last install of this tool
        failed, or it is only half there); a tool that was simply never installed reads a
        neutral "Not installed yet". Nothing red while a tool is downloaded, installed or checked."""
        t = theme.current()
        info = self._deps.tool(key)
        stage = self._stages.get(key)
        if stage == "waiting":
            return f"<span style='color:{t.muted}'>Waiting…</span>"
        if stage == "downloading":
            percent = self._percent.get(key)
            done = f" {percent} %" if percent is not None else ""
            return f"<span style='color:{t.accent_text}'>Downloading…{done}</span>"
        if stage == "installing":
            return f"<span style='color:{t.accent_text}'>Installing…</span>"
        if self._deps.checking and not self._deps.has_checked:
            return f"<span style='color:{t.muted}'>Checking…</span>"
        if info.found:
            if info.version is None and self._deps.checking:
                detail = "Installed — checking version…"
            else:
                detail = describe(info)
            return f"<span style='color:{t.success}'>✔</span> {html.escape(detail)}"
        if key in self._failures:
            return f"<span style='color:{t.danger}'>✖</span> Didn't install (see below)"
        if info.path is not None:  # e.g. ffmpeg without ffprobe: there, but not usable
            return f"<span style='color:{t.danger}'>✖</span> {html.escape(describe(info))}"
        return f"<span style='color:{t.muted}'>Not installed yet</span>"

    def _refresh(self) -> None:
        missing = self._deps.missing()
        for key in TOOL_KEYS:
            info = self._deps.tool(key)
            self._status[key].setText(self._status_html(key))
            self._buttons[key].setText("Re-download" if info.found else "Download")
            self._buttons[key].setToolTip(
                "Download the latest version into the app's tools folder" if info.found
                else f"Download {TOOL_LABELS[key]} for me"
            )
        self._linux_hint.setVisible(IS_LINUX and "ffmpeg" in missing)
        self._install_all.setVisible(bool(missing) or self._installer.busy)
        if self._installer.busy:
            self._install_all.setText("Downloading…")
        elif missing:
            size = download_size_text(missing)
            self._install_all.setText(
                f"Download it for me ({size})" if len(missing) == 1
                else f"Download them for me ({size})"
            )
            self._install_all.setToolTip(
                "Download and set up " + join_names([TOOL_LABELS[k] for k in missing])
            )
        if not self._installer.busy:
            self._set_buttons_enabled(True)
            # After "Stop" the message stays "Stopped…" (see _on_all_finished), even when
            # the last tool was already downloaded and installed anyway.
            if not missing and self._deps.has_checked and not self._failures and not self._stopping:
                self._message.setText("All set! Everything the app needs is installed.")

    def _on_progress(self, key: str, received: float, total: float) -> None:
        if total > 0:
            self._progress.setRange(0, 1000)
            self._progress.setValue(int(received / total * 1000))
            percent = int(received / total * 100)
            if self._percent.get(key) != percent:
                self._percent[key] = percent
                self._status[key].setText(self._status_html(key))
        else:
            self._progress.setRange(0, 0)

    def _on_stage(self, key: str, stage: str) -> None:
        self._stages[key] = stage
        if stage == "installing":
            self._progress.setRange(0, 0)  # unpacking: no byte count to show
        self._status[key].setText(self._status_html(key))

    def _on_tool_finished(self, key: str, ok: bool, text: str) -> None:
        self._stages.pop(key, None)
        if not ok:
            if not self._stopping:  # stopping on purpose is not a failure
                self._failures[key] = f"{TOOL_LABELS[key]}: {text}"
            self._status[key].setText(self._status_html(key))
            return
        # Start the version check first, then show the tool as present right away
        # ("Installed — checking version…"); the check fills in the version.
        self._deps.refresh()
        self._deps.relocate(key)

    def _on_all_finished(self, _ok: bool) -> None:
        self._stages.clear()
        self._progress.setVisible(False)
        self._close_button.setText("Close")
        if self._failures:
            self._message.setText("Something went wrong:\n" + "\n".join(self._failures.values()))
        elif self._stopping and self._deps.missing():
            self._message.setText("Stopped. You can download the tools from here any time.")
        elif self._stopping:  # stopped too late: the last tool was already on its way in
            self._message.setText("Stopped, but the tools had already finished downloading: "
                                  "everything is installed.")
        elif missing := self._deps.missing():
            # This run only covered some tools (one row's Download): others are still missing.
            self._message.setText("Done. Still not installed: "
                                  f"{join_names([TOOL_LABELS[k] for k in missing])}.")
        else:
            self._message.setText("All set! Everything the app needs is installed.")
        self.tools_changed.emit()
        self._refresh()
        if (self._close_when_done and not self._failures and not self._stopping
                and not self._deps.missing() and self.isVisible()):
            self._remember_choice()
            self.accept()

    def _remember_choice(self) -> None:
        self._settings.skip_startup_deps_prompt = self._dont_ask.isChecked()
        self._settings.save()

    def reject(self) -> None:
        if self._installer.busy:
            answer = QMessageBox.question(
                self, "Stop downloading?", "A tool is still downloading. Stop it?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._stopping = True
            self._installer.cancel()
            return
        self._remember_choice()
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
        # The button is the timer's context: the dialog deletes itself on close, and closing
        # it within the delay must drop the timer instead of touching a deleted button.
        QTimer.singleShot(COPIED_FEEDBACK_MS, self._copy, lambda: self._copy.setText("Copy"))


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
