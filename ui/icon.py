"""Weir's logo and small UI glyphs, drawn in code so no image files are needed.

The logo: a lime crest bar (the weir) with a W of water flowing below it, on a
graphite tile — the colours of the app's design.

    python -m ui.icon OUTPUT.png                 # 256 px PNG of the logo
    python -m ui.icon --installer-assets DIR     # icon.ico/png + installer wizard artwork
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

LIME = "#b6f04a"
GRAPHITE_TOP = "#232a34"
GRAPHITE_BOTTOM = "#0e1115"
TILE_EDGE = "#323a46"

_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _mark_pen(size: float, width: float) -> QPen:
    pen = QPen(QColor(LIME), max(1.4, size * width))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def paint_mark(painter: QPainter, x: float, y: float, size: float) -> None:
    """The crest bar and the W, without the tile, in a size x size box at (x, y)."""
    painter.save()
    painter.translate(x, y)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_mark_pen(size, 0.075))
    painter.drawLine(QPointF(size * 0.22, size * 0.29), QPointF(size * 0.78, size * 0.29))
    painter.setPen(_mark_pen(size, 0.10))
    water = QPainterPath(QPointF(size * 0.24, size * 0.42))
    for px, py in ((0.37, 0.73), (0.50, 0.50), (0.63, 0.73), (0.76, 0.42)):
        water.lineTo(QPointF(size * px, size * py))
    painter.drawPath(water)
    painter.restore()


def paint_icon(painter: QPainter, size: float) -> None:
    """The full app icon: graphite rounded tile with the lime mark."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    margin = size * 0.04
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, QColor(GRAPHITE_TOP))
    gradient.setColorAt(1.0, QColor(GRAPHITE_BOTTOM))
    painter.setPen(QPen(QColor(TILE_EDGE), max(1.0, size * 0.012)))
    painter.setBrush(gradient)
    painter.drawRoundedRect(rect, size * 0.23, size * 0.23)
    paint_mark(painter, 0.0, 0.0, size)


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


def icon_image(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_icon(painter, float(size))
    painter.end()
    return image


def render_png(path: str, size: int = 256) -> bool:
    return icon_image(size).save(path, "PNG")


def write_ico(path: Path, sizes: tuple[int, ...] = (16, 20, 24, 32, 40, 48, 64, 128, 256)) -> None:
    """Multi-size Windows .ico: 32-bit DIB images, plus PNG for 256 px (Vista+ format)."""
    blobs: list[bytes] = []
    for size in sizes:
        image = icon_image(size).convertToFormat(QImage.Format.Format_ARGB32)
        if size >= 256:
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            buffer.close()
            blobs.append(bytes(data.data()))
            continue
        # BITMAPINFOHEADER (height doubled for the AND mask), BGRA rows bottom-up, empty AND mask.
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
        stride = image.bytesPerLine()
        raw = bytes(image.constBits())[: stride * size]
        rows = [raw[y * stride: y * stride + size * 4] for y in range(size)]
        mask = bytes(((size + 31) // 32) * 4 * size)
        blobs.append(header + b"".join(reversed(rows)) + mask)
    out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for size, blob in zip(sizes, blobs):
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    for blob in blobs:
        out += blob
    path.write_bytes(bytes(out))


def _wordmark_font(pixel_size: float) -> QFont:
    font = QFont()
    font.setFamilies(["JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono"])
    font.setPixelSize(max(1, round(pixel_size)))
    font.setWeight(QFont.Weight.Bold)
    return font


def _wizard_panel(width: int, height: int) -> QImage:
    """Large wizard image (left side of the Welcome/Finished pages)."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    gradient = QLinearGradient(0, 0, 0, height)
    gradient.setColorAt(0.0, QColor(GRAPHITE_TOP))
    gradient.setColorAt(1.0, QColor(GRAPHITE_BOTTOM))
    painter.fillRect(0, 0, width, height, gradient)
    mark = width * 0.62
    top = height * 0.24
    paint_mark(painter, (width - mark) / 2, top, mark)
    font = _wordmark_font(width * 0.17)
    painter.setFont(font)
    painter.setPen(QColor("#e6e9ee"))
    text_top = top + mark * 0.92
    metrics = QFontMetricsF(font)
    painter.drawText(QRectF(0, text_top, width, metrics.height()), Qt.AlignmentFlag.AlignHCenter, "Weir")
    small = _wordmark_font(width * 0.058)
    small.setWeight(QFont.Weight.Normal)
    painter.setFont(small)
    painter.setPen(QColor("#8a93a2"))
    painter.drawText(QRectF(0, text_top + metrics.height() * 1.05, width, QFontMetricsF(small).height()),
                     Qt.AlignmentFlag.AlignHCenter, "video & music downloader")
    bar = QPen(QColor(LIME), max(1.0, width * 0.012))
    painter.setPen(bar)
    painter.drawLine(QPointF(width * 0.42, height * 0.90), QPointF(width * 0.58, height * 0.90))
    painter.end()
    return image


def _wizard_small(size: int) -> QImage:
    """Small wizard image (top-right of the inner pages, on a white header)."""
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    inset = size * 0.06
    painter.translate(inset, inset)
    paint_icon(painter, size - 2 * inset)
    painter.end()
    return image


def write_installer_assets(folder: Path) -> list[Path]:
    """icon.ico, icon.png and the Inno Setup wizard bitmaps (100/150/200 % DPI)."""
    folder.mkdir(parents=True, exist_ok=True)
    written = [folder / "icon.ico", folder / "icon.png"]
    write_ico(written[0])
    if not icon_image(256).save(str(written[1]), "PNG"):
        raise OSError(f"Couldn't write {written[1]}")
    for scale, suffix in ((1.0, ""), (1.5, "-150"), (2.0, "-200")):
        panel = folder / f"wizard{suffix}.bmp"
        small = folder / f"wizard-small{suffix}.bmp"
        if not _wizard_panel(round(164 * scale), round(314 * scale)).save(str(panel), "BMP"):
            raise OSError(f"Couldn't write {panel}")
        if not _wizard_small(round(55 * scale)).save(str(small), "BMP"):
            raise OSError(f"Couldn't write {small}")
        written += [panel, small]
    return written


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
    if sys.platform != "win32":
        # Headless build machines have no display; Windows renders fonts fine without one,
        # while its offscreen platform has no fonts at all.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv[:1])
    if len(sys.argv) > 2 and sys.argv[1] == "--installer-assets":
        for written in write_installer_assets(Path(sys.argv[2])):
            print(written)
        sys.exit(0)
    target = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    folder = os.path.dirname(os.path.abspath(target))
    os.makedirs(folder, exist_ok=True)
    sys.exit(0 if render_png(target) else 1)
