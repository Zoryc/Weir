"""The single main window: new-download card on top, downloads card below."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QByteArray, QDir, QPoint, QProcess, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QBrush,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QFontDatabase,
    QGuiApplication,
    QKeySequence,
    QRegularExpressionValidator,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QToolButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import APP_DISPLAY_NAME
from core.deps import TOOL_KEYS, TOOL_LABELS, DepsManager, describe
from core.downloader import DownloadJob, DownloadManager, DownloadOptions, JobStatus
from core.errors import ACTION_COOKIES, ACTION_DENO, ACTION_FFMPEG, ACTION_PRESET, ACTION_UPDATE
from core.paths import IS_LINUX, IS_WINDOWS, config_dir, default_download_dir, writable_bin_dir
from core.presets import Preset, PresetStore, split_args
from core.settings import COOKIE_BROWSERS, DEFAULT_TEMPLATE, Settings

from . import theme
from .dialogs import DepsDialog, ErrorDetailsDialog, UpdateDialog, show_about
from .icon import app_icon, glyph_icon
from .widgets import (
    ACCENT_ROLE,
    PROGRESS_ROLE,
    SUBTITLE_COLOR_ROLE,
    SUBTITLE_ROLE,
    Card,
    ErrorBanner,
    ProgressDelegate,
    QueueView,
    SectionToggle,
    StatusDelegate,
    TitleDelegate,
    UrlEdit,
    extract_links,
)

COL_TITLE, COL_PRESET, COL_PROGRESS, COL_SPEED, COL_ETA, COL_STATUS = range(6)
JOB_ID_ROLE = Qt.ItemDataRole.UserRole + 1
RATE_RE = re.compile(r"^\d+(\.\d+)?[KMGkmg]?$")

ACTION_LABELS: dict[str, str] = {
    ACTION_COOKIES: "Use browser cookies",
    ACTION_PRESET: "Choose another preset",
    ACTION_DENO: "Download Deno",
    ACTION_FFMPEG: "Download ffmpeg",
    ACTION_UPDATE: "Update yt-dlp",
}


def _field_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("fieldLabel")
    return label


def _muted_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("muted")
    return label


def _short_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.netloc:
        return url
    host = parts.netloc.removeprefix("www.").removeprefix("m.")
    rest = parts.path + (f"?{parts.query}" if parts.query else "")
    return host + (rest if rest != "/" else "")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, presets: PresetStore, deps: DepsManager) -> None:
        super().__init__()
        self.settings = settings
        self.presets = presets
        self.deps = deps
        self.manager = DownloadManager(deps, self)
        self._items: dict[int, QTreeWidgetItem] = {}
        self._deps_dialog: DepsDialog | None = None
        self._startup_check_done = False
        self._ffmpeg_prompted = False
        self._shutting_down = False
        self._force_close = False
        self._loading = True
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_settings)

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(760, 560)
        self.setAcceptDrops(True)

        self._build_ui()
        self.tray = self._build_tray()
        self._load_settings_into_ui()
        self._connect_signals()
        self._restore_geometry()
        self.refresh_theme()
        self._update_deps_button()
        self._update_download_button()
        self._refresh_summary()
        self._loading = False
        self.url_edit.setFocus()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # The page scrolls only when the window is too small for everything
        # (e.g. Advanced open on a small laptop screen); otherwise it just fits.
        page = QScrollArea(self)
        page.setObjectName("page")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        central = QWidget(page)
        central.setObjectName("pageContent")
        page.setWidget(central)
        self.setCentralWidget(page)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 6)
        root.setSpacing(14)

        root.addLayout(self._build_header(central))
        root.addWidget(self._build_new_download_card(central))
        root.addWidget(self._build_downloads_card(central), 1)
        self._align_field_labels()

        self.deps_button = QPushButton(self)
        self.deps_button.setObjectName("flat")
        self.deps_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.statusBar().addPermanentWidget(self.deps_button)
        self.statusBar().setSizeGripEnabled(False)

    def _build_header(self, parent: QWidget) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)
        logo = QLabel(parent)
        logo.setPixmap(app_icon().pixmap(34, 34))
        title = QLabel(APP_DISPLAY_NAME, parent)
        title.setObjectName("appTitle")
        tagline = _muted_label("Paste a link · pick a preset · click Download", parent)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(title)
        text.addWidget(tagline)
        header.addWidget(logo)
        header.addLayout(text)
        header.addStretch(1)

        self.settings_button = QToolButton(parent)
        self.settings_button.setObjectName("menuButton")
        self.settings_button.setText("Settings")
        self.settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        menu = QMenu(self.settings_button)
        self._add_action(menu, "Update yt-dlp…", self._update_ytdlp)
        self._add_action(menu, "Tools (yt-dlp, ffmpeg, Deno)…", self._show_deps_dialog)
        menu.addSeparator()
        self._add_action(menu, "Open settings folder", lambda: self._open_path(config_dir()))
        self._add_action(menu, "Open tools folder", lambda: self._open_path(writable_bin_dir()))
        menu.addSeparator()
        self._add_action(menu, f"About {APP_DISPLAY_NAME}", lambda: show_about(self, self.deps))
        self._add_action(menu, "Quit", self.close)
        self.settings_button.setMenu(menu)
        header.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _build_new_download_card(self, parent: QWidget) -> Card:
        card = Card(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel("New download", card)
        title.setObjectName("cardTitle")
        top.addWidget(title)
        top.addStretch(1)
        self.clear_links_btn = QPushButton("Clear", card)
        self.clear_links_btn.setObjectName("flat")
        self.clear_links_btn.setToolTip("Empty the link box")
        self.paste_btn = QPushButton("Paste", card)
        self.paste_btn.setToolTip("Paste links from the clipboard")
        top.addWidget(self.clear_links_btn)
        top.addWidget(self.paste_btn)
        layout.addLayout(top)

        self.url_edit = UrlEdit(card)
        self.url_edit.setMinimumHeight(84)
        self.url_edit.setMaximumHeight(130)
        layout.addWidget(self.url_edit)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self._main_grid = grid

        self.preset_combo = QComboBox(card)
        self.preset_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.preset_combo.setMinimumContentsLength(30)
        self.preset_combo.setIconSize(QSize(16, 16))
        self.preset_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preset_hint = _muted_label("", card)
        self.preset_hint.setWordWrap(True)
        preset_box = QVBoxLayout()
        preset_box.setSpacing(4)
        preset_box.addWidget(self.preset_combo)
        preset_box.addWidget(self.preset_hint)
        grid.addWidget(_field_label("Preset", card), 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addLayout(preset_box, 0, 1)
        grid.itemAtPosition(0, 0).widget().setContentsMargins(0, 8, 0, 0)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.folder_edit = QLineEdit(card)
        self.folder_edit.setToolTip("Downloaded files are saved here")
        self.folder_action = self.folder_edit.addAction(
            glyph_icon("folder", theme.current().color("muted")),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.folder_action.setToolTip("Choose a folder")
        self.browse_btn = QPushButton("Browse…", card)
        self.open_folder_btn = QPushButton("Open", card)
        self.open_folder_btn.setToolTip("Show this folder")
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_btn)
        folder_row.addWidget(self.open_folder_btn)
        grid.addWidget(_field_label("Save to", card), 1, 0)
        grid.addLayout(folder_row, 1, 1)

        checks = QHBoxLayout()
        checks.setSpacing(22)
        self.playlist_check = QCheckBox("Download whole playlist", card)
        self.playlist_check.setToolTip(
            "When a link points into a playlist: on = every video, off = only that video."
        )
        self.subs_check = QCheckBox("Embed subtitles", card)
        self.subs_check.setToolTip("Adds English and French subtitles to the video file when available.")
        self.meta_check = QCheckBox("Embed thumbnail && metadata", card)
        self.meta_check.setToolTip("Adds cover art, title, artist and date to the file.")
        for check in (self.playlist_check, self.subs_check, self.meta_check):
            check.setCursor(Qt.CursorShape.PointingHandCursor)
            checks.addWidget(check)
        checks.addStretch(1)
        grid.addWidget(_field_label("Options", card), 2, 0)
        grid.addLayout(checks, 2, 1)
        layout.addLayout(grid)

        self.advanced_panel = self._build_advanced(card)
        layout.addWidget(self.advanced_panel)

        divider = QFrame(card)
        divider.setObjectName("divider")
        layout.addWidget(divider)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.advanced_toggle = SectionToggle("Advanced options", self.advanced_panel, card)
        self.advanced_toggle.setToolTip("Cookies, file names, speed limit, your own presets…")
        footer.addWidget(self.advanced_toggle)
        footer.addStretch(1)
        self.cancel_btn = QPushButton("Cancel all", card)
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.setToolTip("Stop every running and waiting download")
        self.download_btn = QPushButton("Download", card)
        self.download_btn.setObjectName("primary")
        self.download_btn.setIconSize(QSize(18, 18))
        self.download_btn.setMinimumWidth(190)
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.download_btn)
        layout.addLayout(footer)
        return card

    def _build_advanced(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 6, 0, 2)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self._advanced_grid = grid

        self.extra_args_edit = QLineEdit(panel)
        self.extra_args_edit.setPlaceholderText("Extra yt-dlp options, e.g. --sponsorblock-remove all")
        grid.addWidget(_field_label("Extra options", panel), 0, 0)
        grid.addWidget(self.extra_args_edit, 0, 1)

        template_row = QHBoxLayout()
        self.template_edit = QLineEdit(panel)
        self.template_edit.setPlaceholderText(DEFAULT_TEMPLATE)
        self.template_edit.setToolTip(
            "yt-dlp output template, e.g. %(uploader)s/%(title)s.%(ext)s puts each channel "
            "in its own folder."
        )
        self.template_reset_btn = QPushButton("Reset", panel)
        self.template_reset_btn.setObjectName("flat")
        template_row.addWidget(self.template_edit, 1)
        template_row.addWidget(self.template_reset_btn)
        grid.addWidget(_field_label("File name", panel), 1, 0)
        grid.addLayout(template_row, 1, 1)

        cookies_row = QHBoxLayout()
        cookies_row.setSpacing(12)
        self.cookies_combo = QComboBox(panel)
        for key, label in COOKIE_BROWSERS:
            self.cookies_combo.addItem(label, key)
        self.cookies_combo.setMinimumWidth(140)
        cookies_row.addWidget(self.cookies_combo)
        cookies_row.addWidget(
            _muted_label("For sites that ask you to sign in. Firefox works best.", panel), 1
        )
        grid.addWidget(_field_label("Cookies from", panel), 2, 0)
        grid.addLayout(cookies_row, 2, 1)

        limits_row = QHBoxLayout()
        limits_row.setSpacing(12)
        self.rate_edit = QLineEdit(panel)
        self.rate_edit.setPlaceholderText("No limit (e.g. 5M)")
        self.rate_edit.setValidator(QRegularExpressionValidator(r"^\d*(\.\d*)?[KMGkmg]?$", self.rate_edit))
        self.rate_edit.setFixedWidth(150)
        self.rate_edit.setToolTip("Maximum download speed per download, e.g. 500K or 5M (bytes/second).")
        self.parallel_combo = QComboBox(panel)
        for count in (1, 2, 3):
            self.parallel_combo.addItem(str(count), count)
        self.parallel_combo.setToolTip("How many links download at the same time.")
        limits_row.addWidget(self.rate_edit)
        limits_row.addSpacing(10)
        limits_row.addWidget(_field_label("Parallel downloads", panel))
        limits_row.addWidget(self.parallel_combo)
        limits_row.addStretch(1)
        grid.addWidget(_field_label("Speed limit", panel), 3, 0)
        grid.addLayout(limits_row, 3, 1)

        preset_row = QHBoxLayout()
        self.save_preset_btn = QPushButton("Save current settings as a new preset…", panel)
        self.delete_preset_btn = QPushButton("Delete this preset", panel)
        self.delete_preset_btn.setObjectName("flat")
        preset_row.addWidget(self.save_preset_btn)
        preset_row.addWidget(self.delete_preset_btn)
        preset_row.addStretch(1)
        grid.addLayout(preset_row, 4, 1)
        return panel

    def _build_downloads_card(self, parent: QWidget) -> Card:
        card = Card(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 14, 20, 12)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("Downloads", card)
        title.setObjectName("cardTitle")
        self.summary_label = _muted_label("", card)
        self.clear_finished_btn = QPushButton("Clear finished", card)
        self.clear_finished_btn.setObjectName("flat")
        self.clear_finished_btn.setToolTip("Remove finished, failed and cancelled downloads from the list")
        top.addWidget(title)
        top.addSpacing(10)
        top.addWidget(self.summary_label)
        top.addStretch(1)
        top.addWidget(self.clear_finished_btn)
        layout.addLayout(top)

        self.queue = QueueView(card)
        self.queue.setColumnCount(6)
        self.queue.setHeaderLabels(["Title", "Preset", "Progress", "Speed", "ETA", "Status"])
        self.queue.setRootIsDecorated(False)
        self.queue.setUniformRowHeights(True)
        self.queue.setAllColumnsShowFocus(True)
        self.queue.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.queue.setItemDelegateForColumn(COL_TITLE, TitleDelegate(self.queue))
        self.queue.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self.queue))
        self.queue.setItemDelegateForColumn(COL_STATUS, StatusDelegate(self.queue))
        self.queue.setMinimumHeight(130)
        self.queue.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        header = self.queue.header()
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        for column, width in ((COL_PRESET, 175), (COL_PROGRESS, 150), (COL_SPEED, 92),
                              (COL_ETA, 64), (COL_STATUS, 136)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.queue.setColumnWidth(column, width)
        layout.addWidget(self.queue, 3)

        self.banner = ErrorBanner(card)
        layout.addWidget(self.banner)

        self.log_view = QPlainTextEdit(card)
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log_view.setMinimumHeight(110)

        log_row = QHBoxLayout()
        self.log_toggle = SectionToggle("Log", self.log_view, card)
        self.log_toggle.setToolTip("Show yt-dlp's own output (useful when asking for help)")
        self.copy_log_btn = QPushButton("Copy all", card)
        self.copy_log_btn.setObjectName("flat")
        self.clear_log_btn = QPushButton("Clear", card)
        self.clear_log_btn.setObjectName("flat")
        log_row.addWidget(self.log_toggle)
        log_row.addStretch(1)
        log_row.addWidget(self.copy_log_btn)
        log_row.addWidget(self.clear_log_btn)
        layout.addLayout(log_row)
        layout.addWidget(self.log_view, 2)
        self._downloads_layout = layout
        return card

    def _align_field_labels(self) -> None:
        """Give the left-hand labels of both forms the same width so inputs line up."""
        labels: list[QLabel] = []
        for grid in (self._main_grid, self._advanced_grid):
            for row in range(grid.rowCount()):
                item = grid.itemAtPosition(row, 0)
                widget = item.widget() if item is not None else None
                if isinstance(widget, QLabel):
                    labels.append(widget)
        width = max((label.sizeHint().width() for label in labels), default=0)
        for label in labels:
            label.setMinimumWidth(width)

    def _add_action(self, menu: QMenu, text: str, slot: object) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda _checked=False: slot())  # type: ignore[operator]
        menu.addAction(action)
        return action

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu(self)
        self._add_action(menu, "Show window", self._show_window)
        self._add_action(menu, "Quit", self.close)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._show_window()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        tray.messageClicked.connect(self._show_window)
        tray.setToolTip(APP_DISPLAY_NAME)
        tray.show()
        return tray

    def refresh_theme(self) -> None:
        """Re-create theme-coloured icons and repaint the queue (light/dark switch)."""
        t = theme.current()
        self.download_btn.setIcon(glyph_icon("download", QColor("#ffffff"), QColor(255, 255, 255, 170)))
        self.paste_btn.setIcon(glyph_icon("paste", t.color("text")))
        self.settings_button.setIcon(glyph_icon("settings", t.color("text")))
        self.folder_action.setIcon(glyph_icon("folder", t.color("muted")))
        self.advanced_toggle.refresh_icon()
        self.log_toggle.refresh_icon()
        self.banner.refresh_theme()
        self._refresh_preset_icons()
        for job in self.manager.jobs():
            self._update_item(job)
        self.queue.viewport().update()

    # ------------------------------------------------------------- signals

    def _connect_signals(self) -> None:
        self.paste_btn.clicked.connect(self._paste_links)
        self.clear_links_btn.clicked.connect(self.url_edit.clear)
        self.url_edit.submit.connect(self._start_downloads)
        self.url_edit.textChanged.connect(self._update_download_button)
        self.download_btn.clicked.connect(self._start_downloads)
        self.cancel_btn.clicked.connect(self.manager.cancel_all)
        self.browse_btn.clicked.connect(self._browse_folder)
        self.folder_action.triggered.connect(self._browse_folder)
        self.open_folder_btn.clicked.connect(self._open_save_folder)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        for widget in (self.folder_edit, self.extra_args_edit, self.template_edit, self.rate_edit):
            widget.textChanged.connect(self._schedule_save)
        for check in (self.playlist_check, self.subs_check, self.meta_check):
            check.toggled.connect(self._schedule_save)
        self.cookies_combo.currentIndexChanged.connect(self._schedule_save)
        self.parallel_combo.currentIndexChanged.connect(self._on_parallel_changed)
        self.template_reset_btn.clicked.connect(lambda: self.template_edit.setText(DEFAULT_TEMPLATE))
        self.save_preset_btn.clicked.connect(self._save_as_preset)
        self.delete_preset_btn.clicked.connect(self._delete_preset)
        self.advanced_toggle.toggled.connect(self._schedule_save)
        self.log_toggle.toggled.connect(self._on_log_toggled)
        self.copy_log_btn.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.log_view.toPlainText())
        )
        self.clear_log_btn.clicked.connect(self.log_view.clear)
        self.clear_finished_btn.clicked.connect(self.manager.clear_finished)
        self.deps_button.clicked.connect(lambda: self._show_deps_dialog())

        self.queue.customContextMenuRequested.connect(self._show_queue_menu)
        self.queue.itemDoubleClicked.connect(self._on_item_double_clicked)
        delete_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.queue)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(lambda: self._remove_jobs(self._selected_job_ids()))
        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(self.close)

        self.banner.action_clicked.connect(self._on_error_action)
        self.banner.retry_clicked.connect(lambda job_id: self._retry_jobs([job_id]))
        self.banner.details_clicked.connect(self._show_error_details)

        self.manager.job_added.connect(self._on_job_added)
        self.manager.job_changed.connect(self._on_job_changed)
        self.manager.job_removed.connect(self._on_job_removed)
        self.manager.job_log.connect(self._on_job_log)
        self.manager.job_failed.connect(self._on_job_failed)
        self.manager.queue_finished.connect(self._on_queue_finished)
        self.manager.stopped.connect(self._on_manager_stopped)

        self.deps.checked.connect(self._on_deps_checked)

    # ------------------------------------------------------------ settings

    def _load_settings_into_ui(self) -> None:
        s = self.settings
        self._populate_presets(select=s.last_preset)
        self.folder_edit.setText(s.save_dir)
        self.playlist_check.setChecked(s.playlist)
        self.subs_check.setChecked(s.embed_subs)
        self.meta_check.setChecked(s.embed_meta)
        self.extra_args_edit.setText(s.extra_args)
        self.template_edit.setText(s.filename_template)
        self.cookies_combo.setCurrentIndex(max(0, self.cookies_combo.findData(s.cookies_browser)))
        self.rate_edit.setText(s.rate_limit)
        self.parallel_combo.setCurrentIndex(max(0, self.parallel_combo.findData(s.max_parallel)))
        self.manager.set_max_parallel(s.max_parallel)
        self.advanced_toggle.setChecked(s.show_advanced)
        self.log_toggle.setChecked(s.show_log)
        self._on_log_toggled(s.show_log)

    def _restore_geometry(self) -> None:
        if self.settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode("ascii")))
        else:
            self.resize(1040, 820)

    def _schedule_save(self) -> None:
        if not self._loading:
            self._save_timer.start()

    def _save_settings(self) -> None:
        s = self.settings
        s.save_dir = self.folder_edit.text().strip()
        s.last_preset = str(self.preset_combo.currentData() or "")
        s.playlist = self.playlist_check.isChecked()
        s.embed_subs = self.subs_check.isChecked()
        s.embed_meta = self.meta_check.isChecked()
        s.extra_args = self.extra_args_edit.text()
        s.filename_template = self.template_edit.text().strip() or DEFAULT_TEMPLATE
        s.cookies_browser = str(self.cookies_combo.currentData() or "")
        s.rate_limit = self.rate_edit.text().strip()
        s.max_parallel = int(self.parallel_combo.currentData() or 2)
        s.show_advanced = self.advanced_toggle.isChecked()
        s.show_log = self.log_toggle.isChecked()
        s.window_geometry = bytes(self.saveGeometry().toBase64().data()).decode("ascii")
        s.save()

    def _on_parallel_changed(self, _index: int) -> None:
        self.manager.set_max_parallel(int(self.parallel_combo.currentData() or 2))
        self._schedule_save()

    def _on_log_toggled(self, expanded: bool) -> None:
        self._downloads_layout.setStretchFactor(self.log_view, 2 if expanded else 0)
        self.copy_log_btn.setVisible(expanded)
        self.clear_log_btn.setVisible(expanded)
        self._schedule_save()

    # ------------------------------------------------------------- presets

    def _populate_presets(self, select: str | None = None) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self.presets.builtin:
            self.preset_combo.addItem(preset.name, preset.name)
        if self.presets.user:
            self.preset_combo.insertSeparator(self.preset_combo.count())
            for preset in self.presets.user:
                self.preset_combo.addItem(f"{preset.name}  ★", preset.name)
        for i in range(self.preset_combo.count()):
            name = self.preset_combo.itemData(i)
            preset = self.presets.get(name) if isinstance(name, str) else None
            if preset is not None:
                self.preset_combo.setItemData(i, f"yt-dlp options: {preset.args_text}",
                                              Qt.ItemDataRole.ToolTipRole)
        self._refresh_preset_icons()
        index = self.preset_combo.findData(select) if select else -1
        self.preset_combo.setCurrentIndex(index if index >= 0 else 0)
        self.preset_combo.blockSignals(False)
        self._update_preset_widgets()

    def _refresh_preset_icons(self) -> None:
        t = theme.current()
        video = glyph_icon("video", t.color("accent"))
        audio = glyph_icon("audio", t.color("success"))
        for i in range(self.preset_combo.count()):
            name = self.preset_combo.itemData(i)
            preset = self.presets.get(name) if isinstance(name, str) else None
            if preset is not None:
                self.preset_combo.setItemIcon(i, audio if preset.is_audio else video)

    def _current_preset(self) -> Preset | None:
        name = self.preset_combo.currentData()
        return self.presets.get(name) if isinstance(name, str) else None

    def _update_preset_widgets(self) -> None:
        preset = self._current_preset()
        self.delete_preset_btn.setEnabled(preset is not None and not preset.builtin)
        if preset is None:
            self.preset_hint.setText("")
            return
        if preset.description:
            hint = preset.description
        elif not preset.builtin:
            hint = f"Your preset · yt-dlp {preset.args_text}"
        else:
            hint = f"yt-dlp {preset.args_text}"
        self.preset_hint.setText(hint)
        self.preset_combo.setToolTip(f"yt-dlp options: {preset.args_text}")

    def _on_preset_changed(self, _index: int) -> None:
        preset = self._current_preset()
        if preset is not None and preset.options:
            opts = preset.options
            if isinstance(opts.get("playlist"), bool):
                self.playlist_check.setChecked(opts["playlist"])
            if isinstance(opts.get("embed_subs"), bool):
                self.subs_check.setChecked(opts["embed_subs"])
            if isinstance(opts.get("embed_meta"), bool):
                self.meta_check.setChecked(opts["embed_meta"])
            if isinstance(opts.get("filename_template"), str) and opts["filename_template"]:
                self.template_edit.setText(opts["filename_template"])
            if isinstance(opts.get("rate_limit"), str):
                self.rate_edit.setText(opts["rate_limit"])
        self._update_preset_widgets()
        self._schedule_save()

    def _save_as_preset(self) -> None:
        base = self._current_preset()
        if base is None:
            return
        try:
            extra = split_args(self.extra_args_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Extra options", f"Please fix the extra options first: {exc}.")
            return
        name, ok = QInputDialog.getText(
            self, "Save preset", "Name for the new preset:", QLineEdit.EchoMode.Normal,
            f"{base.name} (custom)",
        )
        name = name.strip()
        if not ok or not name:
            return
        if self.presets.is_builtin_name(name):
            QMessageBox.warning(self, "Save preset", "A built-in preset already uses that name.")
            return
        if self.presets.get(name) is not None:
            answer = QMessageBox.question(self, "Save preset", f"Replace your preset “{name}”?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        preset = Preset(
            name=name,
            args=[*base.args, *extra],
            builtin=False,
            options={
                "playlist": self.playlist_check.isChecked(),
                "embed_subs": self.subs_check.isChecked(),
                "embed_meta": self.meta_check.isChecked(),
                "filename_template": self.template_edit.text().strip() or DEFAULT_TEMPLATE,
                "rate_limit": self.rate_edit.text().strip(),
            },
            description=f"Your preset, based on “{base.name}”"
            + (f" + {' '.join(extra)}" if extra else "") + ".",
        )
        self.presets.add_user(preset)
        self.extra_args_edit.clear()  # now part of the preset itself
        self._populate_presets(select=name)
        self._schedule_save()
        self.statusBar().showMessage(f"Saved preset “{name}”.", 5000)

    def _delete_preset(self) -> None:
        preset = self._current_preset()
        if preset is None or preset.builtin:
            return
        answer = QMessageBox.question(self, "Delete preset", f"Delete your preset “{preset.name}”?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.presets.remove_user(preset.name)
        self._populate_presets()
        self._schedule_save()

    # ---------------------------------------------------------------- links

    def _update_download_button(self) -> None:
        count = len(self.url_edit.links())
        self.download_btn.setEnabled(count > 0)
        self.download_btn.setText("Download" if count <= 1 else f"Download {count} links")
        self.download_btn.setToolTip(
            "Start downloading (Ctrl+Enter)" if count else "Paste a link first"
        )

    def _paste_links(self) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        links = extract_links(mime) if mime is not None else []
        if not links:
            text = QGuiApplication.clipboard().text().strip()
            links = [line.strip() for line in text.splitlines() if line.strip()]
        if not links:
            self.statusBar().showMessage("There's no link on the clipboard. Copy one from your browser first.", 5000)
            return
        added = self.url_edit.add_links(links)
        if not added:
            self.statusBar().showMessage("Those links are already in the list.", 4000)
        else:
            self.download_btn.setFocus()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if extract_links(event.mimeData()):
            event.acceptProposedAction()
            self.url_edit.set_drag_active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.url_edit.set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.url_edit.set_drag_active(False)
        links = extract_links(event.mimeData())
        if links:
            self.url_edit.add_links(links)
            event.acceptProposedAction()
            self.activateWindow()

    # --------------------------------------------------------------- folder

    def _browse_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(default_download_dir())
        folder = QFileDialog.getExistingDirectory(self, "Save downloads to", start)
        if folder:
            self.folder_edit.setText(QDir.toNativeSeparators(folder))

    def _save_dir(self) -> Path:
        text = self.folder_edit.text().strip()
        return Path(os.path.expanduser(text)) if text else default_download_dir()

    def _open_save_folder(self) -> None:
        folder = self._save_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Open folder", f"Can't open {folder}:\n{exc}")
            return
        self._open_path(folder)

    def _open_path(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------ downloads

    def _current_options(self) -> DownloadOptions | None:
        try:
            extra = split_args(self.extra_args_edit.text())
        except ValueError as exc:
            self.advanced_toggle.setChecked(True)
            self.extra_args_edit.setFocus()
            QMessageBox.warning(self, "Extra options", f"There's a problem with the extra options: {exc}.")
            return None
        rate = self.rate_edit.text().strip()
        if rate and not RATE_RE.match(rate):
            self.advanced_toggle.setChecked(True)
            self.rate_edit.setFocus()
            QMessageBox.warning(self, "Speed limit", "Write the speed limit like 5M, 500K or 1.5M.")
            return None
        template = self.template_edit.text().strip() or DEFAULT_TEMPLATE
        if "%(ext)s" not in template:
            template += ".%(ext)s"
        save_dir = self._save_dir()
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Save to", f"Can't use the folder {save_dir}:\n{exc}")
            return None
        return DownloadOptions(
            save_dir=str(save_dir),
            playlist=self.playlist_check.isChecked(),
            embed_subs=self.subs_check.isChecked(),
            embed_meta=self.meta_check.isChecked(),
            extra_args=extra,
            filename_template=template,
            cookies_browser=str(self.cookies_combo.currentData() or ""),
            rate_limit=rate,
        )

    def _start_downloads(self) -> None:
        links = self.url_edit.links()
        if not links:
            self.statusBar().showMessage("Paste a link first.", 4000)
            self.url_edit.setFocus()
            return
        if not self.deps.tool("yt-dlp").found:
            QMessageBox.information(
                self, "One more step",
                "The downloader (yt-dlp) isn't installed yet.\n\n"
                "Click “Download it for me” in the next window — it only takes a moment.",
            )
            self._show_deps_dialog(install=["yt-dlp"])
            return
        if not self.deps.tool("ffmpeg").found and not self._ffmpeg_prompted:
            self._ffmpeg_prompted = True
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("ffmpeg is missing")
            box.setText("Most presets need ffmpeg to join video with audio or to make MP3/MP4 files.")
            box.setInformativeText("Download it now? Your links will stay in the box.")
            download = box.addButton("Download ffmpeg", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Continue without it", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is download:
                self._show_deps_dialog(install=["ffmpeg"])
                return
        preset = self._current_preset()
        options = self._current_options()
        if preset is None or options is None:
            return
        for link in links:
            self.manager.add(link, preset, options)
        self.url_edit.clear()
        self.statusBar().showMessage(
            f"Added {len(links)} download{'s' if len(links) != 1 else ''} to the list.", 4000
        )

    def _retry_jobs(self, job_ids: list[int]) -> None:
        base = self._current_options()
        if base is None:
            return
        for job_id in job_ids:
            job = self.manager.get(job_id)
            if job is None or job.process is not None or job.status == JobStatus.WAITING:
                continue
            # Keep what the job was (link, preset, folder, playlist mode); take the
            # current cookies, speed limit, extra options etc. so fixes apply.
            options = replace(base, save_dir=job.options.save_dir, playlist=job.options.playlist)
            self.manager.retry(job_id, options)
            if self.banner.job_id == job_id:
                self.banner.hide()

    def _remove_jobs(self, job_ids: list[int]) -> None:
        for job_id in job_ids:
            self.manager.remove(job_id)

    def _selected_job_ids(self) -> list[int]:
        ids: list[int] = []
        for item in self.queue.selectedItems():
            job_id = item.data(COL_TITLE, JOB_ID_ROLE)
            if isinstance(job_id, int):
                ids.append(job_id)
        return ids

    # --------------------------------------------------------- queue items

    def _on_job_added(self, job_id: int) -> None:
        job = self.manager.get(job_id)
        if job is None:
            return
        item = QTreeWidgetItem()
        item.setData(COL_TITLE, JOB_ID_ROLE, job_id)
        self.queue.addTopLevelItem(item)
        self._items[job_id] = item
        self._update_item(job)
        self.queue.scrollToItem(item)
        self._refresh_summary()

    def _on_job_changed(self, job_id: int) -> None:
        job = self.manager.get(job_id)
        if job is not None:
            self._update_item(job)
        self._refresh_summary()

    def _on_job_removed(self, job_id: int) -> None:
        item = self._items.pop(job_id, None)
        if item is not None:
            index = self.queue.indexOfTopLevelItem(item)
            if index >= 0:
                self.queue.takeTopLevelItem(index)
        if self.banner.job_id == job_id:
            self.banner.hide()
        self._refresh_summary()

    def _on_job_log(self, job_id: int, line: str) -> None:
        self.log_view.appendPlainText(f"[#{job_id}] {line}")

    @staticmethod
    def _status_text(job: DownloadJob) -> str:
        if job.process is not None and job.cancel_requested:
            return "Cancelling…"
        if job.status == JobStatus.DONE and len(job.files) > 1:
            return f"Done · {len(job.files)} files"
        return job.status.value

    @staticmethod
    def _status_color(job: DownloadJob) -> QColor:
        t = theme.current()
        name = {
            JobStatus.DOWNLOADING: "accent",
            JobStatus.CONVERTING: "warning",
            JobStatus.DONE: "success",
            JobStatus.ERROR: "danger",
        }.get(job.status, "muted")
        if job.cancel_requested and job.process is not None:
            name = "muted"
        return t.color(name)

    @staticmethod
    def _subtitle(job: DownloadJob) -> tuple[str, QColor | None]:
        t = theme.current()
        if job.status == JobStatus.ERROR and job.error is not None:
            saved = f" · {len(job.files)} saved" if job.files else ""
            return job.error.title + saved, t.color("danger")
        if job.status == JobStatus.DONE and job.files:
            if len(job.files) == 1:
                return Path(job.files[0]).name, None
            return f"{len(job.files)} files in {Path(job.files[-1]).parent}", None
        if job.status == JobStatus.CONVERTING:
            return "Joining and converting with ffmpeg…", None
        if not job.title:
            # The title is still unknown, so the first line already shows the link.
            if job.status == JobStatus.WAITING:
                return "Waiting for a free slot", None
            if job.status == JobStatus.DOWNLOADING:
                return "Getting video info…", None
            if job.status == JobStatus.CANCELLED:
                return "Cancelled", None
            return "", None
        if job.status == JobStatus.CANCELLED:
            return "Cancelled · " + _short_url(job.url), None
        return _short_url(job.url), None

    def _update_item(self, job: DownloadJob) -> None:
        item = self._items.get(job.id)
        if item is None:
            return
        t = theme.current()
        muted = QBrush(t.color("muted"))
        item.setText(COL_TITLE, job.display_title if job.title else _short_url(job.url))
        subtitle, sub_color = self._subtitle(job)
        item.setData(COL_TITLE, SUBTITLE_ROLE, subtitle)
        item.setData(COL_TITLE, SUBTITLE_COLOR_ROLE, sub_color)
        tooltip = job.url + ("\n\n" + "\n".join(job.files[-10:]) if job.files else "")
        item.setToolTip(COL_TITLE, tooltip)
        item.setText(COL_PRESET, job.preset.name)
        item.setToolTip(COL_PRESET, job.preset.args_text)
        item.setForeground(COL_PRESET, muted)
        color = self._status_color(job)
        show_bar = job.status in (JobStatus.DOWNLOADING, JobStatus.CONVERTING, JobStatus.DONE) or (
            job.percent > 0 and job.status != JobStatus.WAITING
        )
        item.setData(COL_PROGRESS, PROGRESS_ROLE, int(round(job.percent)) if show_bar else None)
        item.setData(COL_PROGRESS, ACCENT_ROLE, color)
        item.setText(COL_SPEED, job.speed)
        item.setText(COL_ETA, job.eta)
        item.setForeground(COL_SPEED, muted)
        item.setForeground(COL_ETA, muted)
        item.setText(COL_STATUS, self._status_text(job))
        item.setData(COL_STATUS, ACCENT_ROLE, color)
        item.setToolTip(COL_STATUS, f"{job.error.title}\n{job.error.hint}" if job.error else "")

    def _refresh_summary(self) -> None:
        jobs = self.manager.jobs()
        running = sum(1 for j in jobs if j.process is not None)
        waiting = sum(1 for j in jobs if j.status == JobStatus.WAITING)
        done = sum(1 for j in jobs if j.status == JobStatus.DONE)
        failed = sum(1 for j in jobs if j.status == JobStatus.ERROR)
        parts: list[str] = []
        if running:
            parts.append(f"{running} downloading")
        if waiting:
            parts.append(f"{waiting} waiting")
        if done:
            parts.append(f"{done} done")
        if failed:
            parts.append(f"{failed} failed")
        text = " · ".join(parts)
        self.summary_label.setText(text)
        self.cancel_btn.setEnabled(running > 0 or waiting > 0)
        self.clear_finished_btn.setEnabled(
            any(j.status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED) and j.process is None
                for j in jobs)
        )
        if self.tray is not None:
            self.tray.setToolTip(f"{APP_DISPLAY_NAME} — {text or 'idle'}")

    def _on_job_failed(self, job_id: int) -> None:
        job = self.manager.get(job_id)
        if job is None or job.error is None or self._shutting_down:
            return
        label = ACTION_LABELS.get(job.error.action or "")
        self.banner.show_error(job.id, job.title or job.url, job.error.title, job.error.hint,
                               job.error.action, label)

    def _on_queue_finished(self, done: int, failed: int, _cancelled: int) -> None:
        if self._shutting_down or (done == 0 and failed == 0):
            return
        if failed and done:
            message = f"{done} finished, {failed} failed."
        elif failed:
            message = f"{failed} download{'s' if failed != 1 else ''} failed."
        else:
            message = f"{done} download{'s' if done != 1 else ''} finished."
        self._notify("Downloads finished", message)

    def _notify(self, title: str, message: str) -> None:
        if self.tray is not None and self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 8000)
        elif IS_LINUX:
            notify_send = shutil.which("notify-send")
            if notify_send:
                QProcess.startDetached(notify_send, ["-a", APP_DISPLAY_NAME, title, message])
        QApplication.alert(self, 0)

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -------------------------------------------------------- context menu

    def _job_at(self, pos: QPoint) -> DownloadJob | None:
        item = self.queue.itemAt(pos)
        if item is None:
            return None
        job_id = item.data(COL_TITLE, JOB_ID_ROLE)
        return self.manager.get(job_id) if isinstance(job_id, int) else None

    def _show_queue_menu(self, pos: QPoint) -> None:
        job = self._job_at(pos)
        menu = QMenu(self)
        if job is not None:
            selected = self._selected_job_ids()
            if job.id not in selected:
                selected = [job.id]
            act = self._add_action(menu, "Open file", lambda: self._open_file(job))
            act.setEnabled(self._existing_file(job) is not None)
            self._add_action(menu, "Open folder", lambda: self._open_job_folder(job))
            menu.addSeparator()
            active = [i for i in selected if (j := self.manager.get(i)) is not None
                      and (j.process is not None or j.status == JobStatus.WAITING)]
            if active:
                self._add_action(menu, "Cancel", lambda: [self.manager.cancel(i) for i in active])
            retryable = [i for i in selected if (j := self.manager.get(i)) is not None
                         and j.process is None and j.status != JobStatus.WAITING]
            act = self._add_action(menu, "Retry", lambda: self._retry_jobs(retryable))
            act.setEnabled(bool(retryable))
            self._add_action(menu, "Remove", lambda: self._remove_jobs(selected))
            menu.addSeparator()
            act = self._add_action(menu, "Copy error", lambda: self._copy_error(job))
            act.setEnabled(job.error is not None)
            if job.error is not None:
                self._add_action(menu, "Show error details…", lambda: self._show_error_details(job.id))
            self._add_action(menu, "Copy link", lambda: QGuiApplication.clipboard().setText(job.url))
            menu.addSeparator()
        act = self._add_action(menu, "Clear finished", self.manager.clear_finished)
        act.setEnabled(self.clear_finished_btn.isEnabled())
        menu.exec(self.queue.viewport().mapToGlobal(pos))

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        job_id = item.data(COL_TITLE, JOB_ID_ROLE)
        job = self.manager.get(job_id) if isinstance(job_id, int) else None
        if job is None:
            return
        if job.status == JobStatus.ERROR and job.error is not None:
            self._show_error_details(job.id)
        elif self._existing_file(job) is not None:
            self._open_file(job)

    @staticmethod
    def _existing_file(job: DownloadJob) -> str | None:
        return next((f for f in reversed(job.files) if Path(f).exists()), None)

    def _open_file(self, job: DownloadJob) -> None:
        path = self._existing_file(job)
        if path is None:
            self.statusBar().showMessage("The file isn't there any more.", 4000)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_job_folder(self, job: DownloadJob) -> None:
        path = self._existing_file(job)
        if path is not None and IS_WINDOWS:
            # Open Explorer with the file selected.
            proc = QProcess(self)
            proc.setProgram("explorer.exe")
            proc.setNativeArguments(f'/select,"{QDir.toNativeSeparators(path)}"')
            proc.startDetached()
            return
        folder = Path(path).parent if path is not None else Path(job.options.save_dir)
        self._open_path(folder)

    def _copy_error(self, job: DownloadJob) -> None:
        if job.error is not None:
            QGuiApplication.clipboard().setText(job.error.as_text(job.url))
            self.statusBar().showMessage("Error copied to the clipboard.", 3000)

    def _show_error_details(self, job_id: int) -> None:
        job = self.manager.get(job_id)
        if job is None or job.error is None:
            return
        dialog = ErrorDetailsDialog(job.title or job.url, job.url, job.error,
                                    ACTION_LABELS.get(job.error.action or ""), self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.action_requested.connect(lambda action: self._on_error_action(job_id, action))
        dialog.retry_requested.connect(lambda: self._retry_jobs([job_id]))
        dialog.open()

    def _on_error_action(self, job_id: int, action: str) -> None:
        job = self.manager.get(job_id)
        if action == ACTION_COOKIES:
            self.advanced_toggle.setChecked(True)
            if not self.cookies_combo.currentData():
                self.cookies_combo.setCurrentIndex(max(0, self.cookies_combo.findData("firefox")))
            self.cookies_combo.setFocus()
            self.statusBar().showMessage(
                f"Cookies will be read from {self.cookies_combo.currentText()}. "
                "Make sure you're signed in there, then click Retry.", 12000,
            )
        elif action == ACTION_PRESET:
            if job is not None:
                self.url_edit.add_links([job.url])
            self.banner.hide()
            self.preset_combo.setFocus()
            self.preset_combo.showPopup()
            self.statusBar().showMessage("Pick another preset, then click Download.", 8000)
        elif action == ACTION_DENO:
            self._show_deps_dialog(install=["deno"])
        elif action == ACTION_FFMPEG:
            self._show_deps_dialog(install=["ffmpeg"])
        elif action == ACTION_UPDATE:
            self._update_ytdlp()

    # ---------------------------------------------------------- dependencies

    def _show_deps_dialog(self, install: list[str] | None = None, startup: bool = False) -> None:
        if self._deps_dialog is None:
            self._deps_dialog = DepsDialog(self.deps, self.settings, self)
        dialog = self._deps_dialog
        dialog.set_startup_mode(startup)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if install:
            dialog.start_install(install)

    def _on_deps_checked(self) -> None:
        self._update_deps_button()
        if self._startup_check_done:
            return
        self._startup_check_done = True
        missing = self.deps.missing()
        if "yt-dlp" in missing or (missing and not self.settings.skip_startup_deps_prompt):
            QTimer.singleShot(0, lambda: self._show_deps_dialog(startup=True))

    def _update_deps_button(self) -> None:
        missing = self.deps.missing()
        tips = [f"{TOOL_LABELS[key]}: {describe(self.deps.tool(key))}" for key in TOOL_KEYS]
        if missing:
            names = ", ".join(TOOL_LABELS[k] for k in missing)
            self.deps_button.setText(f"⚠  Missing: {names} — click to install")
        else:
            version = self.deps.tool("yt-dlp").version
            self.deps_button.setText(f"✓  All tools ready{f' · yt-dlp {version}' if version else ''}")
        self.deps_button.setToolTip("\n".join(tips) + "\n\nClick to manage the tools.")

    def _update_ytdlp(self) -> None:
        info = self.deps.tool("yt-dlp")
        if not info.found:
            self._show_deps_dialog(install=["yt-dlp"])
            return
        if self.manager.running_count():
            QMessageBox.information(
                self, "Update yt-dlp",
                "Please wait until the current downloads finish (or cancel them), then try again.",
            )
            return
        dialog = UpdateDialog(info, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.update_finished.connect(self.deps.refresh)
        dialog.open()
        dialog.start()

    # ---------------------------------------------------------------- close

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_close or (self.manager.is_idle() and not self.manager.has_work()):
            self._finish_shutdown(event)
            return
        if not self._shutting_down:
            if self.manager.has_work():
                answer = QMessageBox.question(
                    self, "Quit", "Downloads are still running. Stop them and quit?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self._shutting_down = True
            self.setEnabled(False)
            self.statusBar().showMessage("Stopping downloads…")
            self.manager.cancel_all()
            QTimer.singleShot(8000, self._force_quit)  # never hang on exit
            if self.manager.is_idle():
                QTimer.singleShot(0, self._force_quit)
        event.ignore()

    def _on_manager_stopped(self) -> None:
        if self._shutting_down:
            QTimer.singleShot(0, self._force_quit)

    def _force_quit(self) -> None:
        if self._force_close:
            return
        self._force_close = True
        self.close()

    def _finish_shutdown(self, event: QCloseEvent) -> None:
        if self._deps_dialog is not None and self._deps_dialog.busy:
            if not self._force_close:
                answer = QMessageBox.question(
                    self, "Quit", "A tool is still downloading. Stop it and quit?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self._deps_dialog.stop()
        self._save_timer.stop()
        self._save_settings()
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        QTimer.singleShot(0, QApplication.quit)
