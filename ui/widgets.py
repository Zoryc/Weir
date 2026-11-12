"""Small reusable widgets and item delegates for the main window."""

from __future__ import annotations

import html
import re

from PySide6.QtCore import (
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    Signal,
    SignalInstance,
)
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QIcon,
    QKeyEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QWidget,
)

from core.settings import COOKIE_BROWSERS, DEFAULT_TEMPLATE

from . import theme
from .icon import glyph_icon, glyph_pixmap

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

PROGRESS_ROLE = Qt.ItemDataRole.UserRole  # int percent, or None for "no bar"
ACCENT_ROLE = Qt.ItemDataRole.UserRole + 2  # QColor for the bar / status pill
SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 3  # second line under the title
SUBTITLE_COLOR_ROLE = Qt.ItemDataRole.UserRole + 4  # QColor for that line


def extract_links_from_text(text: str) -> list[str]:
    return [link.rstrip(".,);]") for link in URL_RE.findall(text)]


def extract_links(mime: QMimeData) -> list[str]:
    """Web links from a drag & drop or clipboard payload."""
    links: list[str] = []
    if mime.hasUrls():
        for url in mime.urls():
            if url.scheme().lower() in ("http", "https"):
                links.append(url.toString())
    if not links and mime.hasText():
        links = extract_links_from_text(mime.text())
    return list(dict.fromkeys(links))


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class Card(QFrame):
    """Rounded white (or dark) panel that groups related controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)


class SectionToggle(QToolButton):
    """“› Advanced options” style toggle: shows/hides a panel."""

    def __init__(self, text: str, panel: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panel = panel
        self.setObjectName("sectionToggle")
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(14, 14))
        self.toggled.connect(self._on_toggled)
        panel.setVisible(False)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        name = "chevron" if self.isChecked() else "chevron-right"
        self.setIcon(glyph_icon(name, theme.current().color("muted")))

    def _on_toggled(self, checked: bool) -> None:
        self._panel.setVisible(checked)
        self.refresh_icon()


class UrlEdit(QPlainTextEdit):
    """Multi-line link box. Pasted or dropped links each go on their own line."""

    submit = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("links")
        self.setPlaceholderText(
            "Paste a link to a video, playlist or song — one per line.\n"
            "Tip: you can also drag links from your browser onto this window."
        )
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabChangesFocus(True)
        self.setProperty("dragActive", False)

    def links(self) -> list[str]:
        result: list[str] = []
        for line in self.toPlainText().splitlines():
            text = line.strip()
            if text and not text.startswith("#") and text not in result:
                result.append(text)
        return result

    def add_links(self, links: list[str]) -> int:
        existing = set(self.links())
        new = [link for link in dict.fromkeys(links) if link not in existing]
        if not new:
            return 0
        text = self.toPlainText()
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        prefix = "" if not text or text.endswith("\n") else "\n"
        cursor.insertText(prefix + "\n".join(new))
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        return len(new)

    def set_drag_active(self, active: bool) -> None:
        if bool(self.property("dragActive")) != active:
            self.setProperty("dragActive", active)
            repolish(self)

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        return bool(extract_links(source)) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        links = extract_links(source)
        if links:
            self.add_links(links)
            return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        super().dragEnterEvent(event)
        self.set_drag_active(bool(extract_links(event.mimeData())))

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        super().dragLeaveEvent(event)
        self.set_drag_active(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self.set_drag_active(False)
        links = extract_links(event.mimeData())
        if links:
            self.add_links(links)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.submit.emit()
            return
        super().keyPressEvent(event)


# ------------------------------------------------------------------ delegates


def _draw_cell_background(painter: QPainter, option: QStyleOptionViewItem,
                          index: QModelIndex | QPersistentModelIndex,
                          delegate: QStyledItemDelegate) -> QStyleOptionViewItem:
    opt = QStyleOptionViewItem(option)
    delegate.initStyleOption(opt, index)
    opt.text = ""  # type: ignore[attr-defined]
    opt.icon = QIcon()  # type: ignore[attr-defined]
    widget = opt.widget  # type: ignore[attr-defined]
    style = widget.style() if widget is not None else QApplication.style()
    style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
    return opt


def _row_height(option: QStyleOptionViewItem) -> int:
    fm = QFontMetrics(option.font)  # type: ignore[attr-defined]
    return int(fm.height() * 2 + theme.metrics().row_extra)


class TitleDelegate(QStyledItemDelegate):
    """Title on the first line, a muted detail line (link, file name or error) below."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex | QPersistentModelIndex) -> None:
        opt = _draw_cell_background(painter, option, index, self)
        t = theme.current()
        rect = QRectF(opt.rect).adjusted(10, 0, -8, 0)  # type: ignore[attr-defined]
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        sub_color = index.data(SUBTITLE_COLOR_ROLE)

        title_font = QFont(opt.font)  # type: ignore[attr-defined]
        title_font.setWeight(QFont.Weight.DemiBold)
        small_font = QFont(opt.font)  # type: ignore[attr-defined]
        small_font.setPointSizeF(max(7.0, small_font.pointSizeF() * 0.9))
        tfm, sfm = QFontMetrics(title_font), QFontMetrics(small_font)
        gap = 3
        top = rect.top() + (rect.height() - (tfm.height() + gap + sfm.height())) / 2

        painter.save()
        painter.setFont(title_font)
        painter.setPen(QColor(t.text))
        painter.drawText(
            QRectF(rect.left(), top, rect.width(), tfm.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            tfm.elidedText(title, Qt.TextElideMode.ElideRight, int(rect.width())),
        )
        painter.setFont(small_font)
        painter.setPen(sub_color if isinstance(sub_color, QColor) else QColor(t.muted))
        painter.drawText(
            QRectF(rect.left(), top + tfm.height() + gap, rect.width(), sfm.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            sfm.elidedText(subtitle, Qt.TextElideMode.ElideMiddle, int(rect.width())),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem,
                 index: QModelIndex | QPersistentModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), _row_height(option))


class ProgressDelegate(QStyledItemDelegate):
    """Slim rounded progress bar with the percentage next to it."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex | QPersistentModelIndex) -> None:
        opt = _draw_cell_background(painter, option, index, self)
        value = index.data(PROGRESS_ROLE)
        if value is None:
            return
        t = theme.current()
        accent = index.data(ACCENT_ROLE)
        color = accent if isinstance(accent, QColor) else QColor(t.accent)
        fm = QFontMetrics(opt.font)  # type: ignore[attr-defined]
        cell = QRectF(opt.rect).adjusted(10, 0, -8, 0)  # type: ignore[attr-defined]
        text_width = fm.horizontalAdvance("100%") + 8
        bar = QRectF(cell.left(), cell.center().y() - 3, max(12.0, cell.width() - text_width), 6)
        fraction = max(0.0, min(1.0, float(value) / 100.0))

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.track))
        painter.drawRoundedRect(bar, 3, 3)
        if fraction > 0:
            fill = QRectF(bar)
            fill.setWidth(max(6.0, bar.width() * fraction))
            painter.setBrush(color)
            painter.drawRoundedRect(fill, 3, 3)
        painter.setPen(QColor(t.muted))
        painter.drawText(
            QRectF(bar.right() + 8, cell.top(), text_width, cell.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{int(value)}%",
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem,
                 index: QModelIndex | QPersistentModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), _row_height(option))


class StatusDelegate(QStyledItemDelegate):
    """Status as a coloured pill with a dot: ● Downloading."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex | QPersistentModelIndex) -> None:
        opt = _draw_cell_background(painter, option, index, self)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            return
        t = theme.current()
        accent = index.data(ACCENT_ROLE)
        color = accent if isinstance(accent, QColor) else QColor(t.muted)
        font = QFont(opt.font)  # type: ignore[attr-defined]
        font.setWeight(QFont.Weight.DemiBold)
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.92))
        fm = QFontMetrics(font)
        cell = QRectF(opt.rect).adjusted(8, 0, -6, 0)  # type: ignore[attr-defined]
        height = fm.height() + 8
        width = min(cell.width(), fm.horizontalAdvance(text) + 30)
        pill = QRectF(cell.left(), cell.center().y() - height / 2, width, height)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.tint(color, 70 if t.dark else 34))
        painter.drawRoundedRect(pill, height / 2, height / 2)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(pill.left() + 12, pill.center().y()), 3.5, 3.5)
        painter.setFont(font)
        painter.setPen(color)
        text_rect = pill.adjusted(21, 0, -9, 0)
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(text, Qt.TextElideMode.ElideRight, int(text_rect.width())),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem,
                 index: QModelIndex | QPersistentModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), _row_height(option))


class QueueView(QTreeWidget):
    """Download list that shows a friendly hint while it is empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("queue")

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.topLevelItemCount() > 0:
            return
        t = theme.current()
        viewport = self.viewport()
        painter = QPainter(viewport)
        icon = glyph_pixmap("inbox", QColor(t.border_strong), 40)
        title_font = QFont(self.font())
        title_font.setWeight(QFont.Weight.DemiBold)
        title_font.setPointSizeF(title_font.pointSizeF() * 1.08)
        tfm = QFontMetrics(title_font)
        fm = QFontMetrics(self.font())
        total = 40 + 12 + tfm.height() + 4 + fm.height()
        top = max(8, (viewport.height() - total) // 2)
        painter.drawPixmap((viewport.width() - 40) // 2, top, icon)
        y = top + 40 + 12
        painter.setFont(title_font)
        painter.setPen(QColor(t.text))
        painter.drawText(QRectF(0, y, viewport.width(), tfm.height()),
                         Qt.AlignmentFlag.AlignCenter, "No downloads yet")
        painter.setFont(self.font())
        painter.setPen(QColor(t.muted))
        painter.drawText(QRectF(0, y + tfm.height() + 4, viewport.width(), fm.height()),
                         Qt.AlignmentFlag.AlignCenter, "Paste a link above and click Download.")
        painter.end()


class ErrorBanner(QFrame):
    """Inline message under the queue when a download fails, with a one-click fix."""

    action_clicked = Signal(int, str)
    retry_clicked = Signal(int)
    details_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("errorBanner")
        self._job_id: int | None = None
        self._action: str | None = None

        self._icon = QLabel(self)
        self._text = QLabel(self)
        self._text.setWordWrap(True)
        self._text.setTextFormat(Qt.TextFormat.RichText)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._action_button = QPushButton(self)
        self._action_button.setObjectName("accentSmall")
        self._retry_button = QPushButton("Retry", self)
        self._details_button = QPushButton("Details", self)
        self._details_button.setObjectName("flat")
        self._close_button = QToolButton(self)
        self._close_button.setObjectName("closeButton")
        self._close_button.setText("✕")
        self._close_button.setToolTip("Hide this message")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        layout.setSpacing(10)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._text, 1)
        for button in (self._details_button, self._retry_button, self._action_button):
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._close_button, 0, Qt.AlignmentFlag.AlignTop)

        self._action_button.clicked.connect(self._emit_action)
        self._retry_button.clicked.connect(lambda: self._emit_job(self.retry_clicked))
        self._details_button.clicked.connect(lambda: self._emit_job(self.details_clicked))
        self._close_button.clicked.connect(self.hide)
        self.refresh_theme()
        self.hide()

    @property
    def job_id(self) -> int | None:
        return self._job_id if self.isVisible() else None

    def refresh_theme(self) -> None:
        self._icon.setPixmap(glyph_pixmap("alert", theme.current().color("danger"), 20))

    def show_error(self, job_id: int, subject: str, title: str, hint: str,
                   action: str | None, action_label: str | None) -> None:
        self._job_id = job_id
        self._action = action
        danger = theme.current().danger
        self._text.setText(
            f"<b style='color:{danger}'>{html.escape(title)}</b> {html.escape(hint)}"
            f"<br><small style='color:{theme.current().muted}'>{html.escape(subject)}</small>"
        )
        self._action_button.setVisible(bool(action and action_label))
        self._action_button.setText(action_label or "")
        self.show()

    def _emit_action(self) -> None:
        if self._job_id is not None and self._action:
            self.action_clicked.emit(self._job_id, self._action)

    def _emit_job(self, signal: SignalInstance) -> None:
        if self._job_id is not None:
            signal.emit(self._job_id)


class AdvancedPanel(QWidget):
    """The “Advanced options” controls: labels on the left, fields on the right."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("advancedPanel")
        self.extra_args_edit = QLineEdit(self)
        self.extra_args_edit.setPlaceholderText("Extra yt-dlp options, e.g. --sponsorblock-remove all")
        self.template_edit = QLineEdit(self)
        self.template_edit.setPlaceholderText(DEFAULT_TEMPLATE)
        self.template_edit.setToolTip(
            "yt-dlp output template, e.g. %(uploader)s/%(title)s.%(ext)s puts each channel "
            "in its own folder."
        )
        self.template_reset_btn = QPushButton("Reset", self)
        self.template_reset_btn.setObjectName("flat")
        self.cookies_combo = QComboBox(self)
        for key, label in COOKIE_BROWSERS:
            self.cookies_combo.addItem(label, key)
        self.cookies_combo.setMinimumWidth(130)
        self.cookies_hint = QLabel("For sites that ask you to sign in. Firefox works best.", self)
        self.cookies_hint.setObjectName("muted")
        self.cookies_hint.setWordWrap(True)
        self.rate_edit = QLineEdit(self)
        self.rate_edit.setPlaceholderText("No limit (e.g. 5M)")
        self.rate_edit.setValidator(QRegularExpressionValidator(r"^\d*(\.\d*)?[KMGkmg]?$", self.rate_edit))
        self.rate_edit.setToolTip("Maximum download speed per download, e.g. 500K or 5M (bytes/second).")
        self.parallel_combo = QComboBox(self)
        for count in (1, 2, 3):
            self.parallel_combo.addItem(str(count), count)
        self.parallel_combo.setToolTip("How many links download at the same time.")
        self.save_preset_btn = QPushButton("Save current settings as a new preset…", self)
        self.delete_preset_btn = QPushButton("Delete this preset", self)
        self.delete_preset_btn.setObjectName("flat")

        self.rate_edit.setMaximumWidth(150)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 6, 0, 2)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        rows: list[tuple[str, list[QWidget | int | None | tuple[QWidget, int]]]] = [
            ("Extra options", [self.extra_args_edit]),
            ("File name", [self.template_edit, self.template_reset_btn]),
            ("Cookies from", [self.cookies_combo, (self.cookies_hint, 1)]),
            ("Speed limit", [self.rate_edit, 10, self._field_label("Parallel downloads"),
                             self.parallel_combo, None]),
            ("", [self.save_preset_btn, self.delete_preset_btn, None]),
        ]
        for index, (label, items) in enumerate(rows):
            if label:
                grid.addWidget(self._field_label(label), index, 0)
            grid.addLayout(self._row(items), index, 1)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("fieldLabel")
        return label

    @staticmethod
    def _row(items: list[QWidget | int | None | tuple[QWidget, int]]) -> QHBoxLayout:
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        for item in items:
            if item is None:
                box.addStretch(1)
            elif isinstance(item, int):
                box.addSpacing(item)
            elif isinstance(item, tuple):
                box.addWidget(item[0], item[1])
            else:
                box.addWidget(item)
        return box
