"""The app icon and small UI glyphs, drawn in code so no image files are needed.

Run `python -m ui.icon OUTPUT.png` to export a 256 px app icon (used by the build scripts).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def paint_icon(painter: QPainter, size: float) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    margin = size * 0.04
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#5b8cff"))
    gradient.setColorAt(1.0, QColor("#2a4fd1"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(rect, size * 0.24, size * 0.24)

    white = QColor("#ffffff")
    pen = QPen(white, max(1.5, size * 0.095))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(size * 0.5, size * 0.22), QPointF(size * 0.5, size * 0.6))
    arrow = QPainterPath()
    arrow.moveTo(QPointF(size * 0.31, size * 0.43))
    arrow.lineTo(QPointF(size * 0.5, size * 0.62))
    arrow.lineTo(QPointF(size * 0.69, size * 0.43))
    painter.drawPath(arrow)
    painter.drawLine(QPointF(size * 0.28, size * 0.78), QPointF(size * 0.72, size * 0.78))


def app_icon() -> QIcon:
    icon = QIcon()
    for size in _ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        paint_icon(painter, float(size))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def render_png(path: str, size: int = 256) -> bool:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_icon(painter, float(size))
    painter.end()
    return image.save(path, "PNG")


# ----------------------------------------------------------------- glyphs
# Each glyph is drawn in a 16 x 16 unit box with a round 1.6-unit stroke.


def _paint_glyph(painter: QPainter, name: str, color: QColor, size: float) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 16.0, size / 16.0)
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if name == "video":
        painter.drawRoundedRect(QRectF(1.8, 3.2, 12.4, 9.6), 2.2, 2.2)
        triangle = QPainterPath()
        triangle.moveTo(6.6, 5.7)
        triangle.lineTo(10.6, 8.0)
        triangle.lineTo(6.6, 10.3)
        triangle.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(triangle)
    elif name == "audio":
        painter.drawLine(QPointF(10.5, 2.6), QPointF(10.5, 11.0))
        flag = QPainterPath()
        flag.moveTo(10.5, 2.6)
        flag.cubicTo(11.5, 4.4, 13.6, 4.6, 13.8, 6.6)
        painter.drawPath(flag)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8.2, 11.4), 2.7, 2.2)
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(1.8, 12.6)
        path.lineTo(1.8, 3.8)
        path.lineTo(6.0, 3.8)
        path.lineTo(7.6, 5.4)
        path.lineTo(14.2, 5.4)
        path.lineTo(14.2, 12.6)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "download":
        painter.drawLine(QPointF(8.0, 2.2), QPointF(8.0, 10.2))
        head = QPainterPath()
        head.moveTo(4.6, 7.0)
        head.lineTo(8.0, 10.4)
        head.lineTo(11.4, 7.0)
        painter.drawPath(head)
        painter.drawLine(QPointF(3.0, 13.6), QPointF(13.0, 13.6))
    elif name == "chevron":
        path = QPainterPath()
        path.moveTo(3.5, 6.0)
        path.lineTo(8.0, 10.5)
        path.lineTo(12.5, 6.0)
        painter.drawPath(path)
    elif name == "check":
        pen.setWidthF(2.1)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(3.6, 8.4)
        path.lineTo(6.7, 11.4)
        path.lineTo(12.6, 4.9)
        painter.drawPath(path)
    elif name == "chevron-right":
        path = QPainterPath()
        path.moveTo(6.0, 3.5)
        path.lineTo(10.5, 8.0)
        path.lineTo(6.0, 12.5)
        painter.drawPath(path)
    elif name == "alert":
        painter.drawEllipse(QPointF(8.0, 8.0), 6.4, 6.4)
        painter.drawLine(QPointF(8.0, 4.6), QPointF(8.0, 8.8))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8.0, 11.3), 1.0, 1.0)
    elif name == "paste":
        painter.drawRoundedRect(QRectF(3.0, 3.0, 10.0, 11.2), 1.8, 1.8)
        painter.drawRoundedRect(QRectF(5.6, 1.6, 4.8, 2.8), 1.0, 1.0)
        painter.drawLine(QPointF(5.6, 8.0), QPointF(10.4, 8.0))
        painter.drawLine(QPointF(5.6, 10.8), QPointF(9.0, 10.8))
    elif name == "settings":
        for y in (4.0, 8.0, 12.0):
            painter.drawLine(QPointF(2.0, y), QPointF(14.0, y))
        painter.setBrush(color)
        for x, y in ((5.0, 4.0), (10.5, 8.0), (7.0, 12.0)):
            painter.drawEllipse(QPointF(x, y), 1.6, 1.6)
    elif name == "inbox":
        path = QPainterPath()
        path.moveTo(1.8, 9.0)
        path.lineTo(4.2, 3.0)
        path.lineTo(11.8, 3.0)
        path.lineTo(14.2, 9.0)
        path.lineTo(14.2, 13.0)
        path.lineTo(1.8, 13.0)
        path.closeSubpath()
        painter.drawPath(path)
        tray = QPainterPath()
        tray.moveTo(1.8, 9.0)
        tray.lineTo(5.4, 9.0)
        tray.lineTo(6.4, 10.8)
        tray.lineTo(9.6, 10.8)
        tray.lineTo(10.6, 9.0)
        tray.lineTo(14.2, 9.0)
        painter.drawPath(tray)
    else:
        raise KeyError(name)


def glyph_pixmap(name: str, color: QColor, size: int, ratio: float = 2.0) -> QPixmap:
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    _paint_glyph(painter, name, color, size * ratio)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def glyph_icon(name: str, color: QColor, disabled: QColor | None = None) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32):
        icon.addPixmap(glyph_pixmap(name, color, size, 1.0), QIcon.Mode.Normal)
        icon.addPixmap(glyph_pixmap(name, color, size, 1.0), QIcon.Mode.Active)
        if disabled is not None:
            icon.addPixmap(glyph_pixmap(name, disabled, size, 1.0), QIcon.Mode.Disabled)
    return icon


def write_glyph_png(name: str, color: QColor, path: Path, size: int = 32) -> None:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    _paint_glyph(painter, name, color, float(size))
    painter.end()
    if not image.save(str(path), "PNG"):
        raise OSError(f"Couldn't write {path}")


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv[:1])
    target = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    folder = os.path.dirname(os.path.abspath(target))
    os.makedirs(folder, exist_ok=True)
    sys.exit(0 if render_png(target) else 1)
