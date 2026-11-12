"""Draw the README artwork from the app's own logo (developer tool).

    python tools/screenshots.py         # first: fresh screenshots in screenshots/
    python tools/readme_art.py          # writes docs/banner.png, docs/logo.png and the
                                        # framed docs/weir-light.png / docs/weir-dark.png

The banner uses the design's colours: graphite, lime, and faint "water" lines
flowing over a weir on the right.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # Windows' offscreen platform has no fonts

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

MONO = ["JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono"]
SANS = ["Inter", "Segoe UI Variable Display", "Segoe UI", "Noto Sans", "DejaVu Sans"]
LIME = QColor("#b6f04a")


def _font(families: list[str], pixels: float, weight: QFont.Weight) -> QFont:
    font = QFont()
    font.setFamilies(families)
    font.setPixelSize(round(pixels))
    font.setWeight(weight)
    return font


def _water(painter: QPainter, width: float, height: float) -> None:
    """Lines flowing across a crest and falling away — the weir, on the right side."""
    start_x = width * 0.64
    crest_x = width * 0.80
    for i in range(9):
        y0 = height * (0.18 + i * 0.075)
        color = QColor(LIME)
        color.setAlphaF(0.07 + 0.04 * (i % 3))
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        path = QPainterPath(QPointF(start_x, y0))
        steps = 40
        for s in range(1, steps + 1):
            x = start_x + (crest_x - start_x) * s / steps
            path.lineTo(QPointF(x, y0 + math.sin(s / steps * math.pi * 2 + i) * 4))
        fall = height * (0.10 + 0.02 * i)
        path.cubicTo(QPointF(crest_x + 40, y0), QPointF(crest_x + 60, y0 + fall * 0.4),
                     QPointF(crest_x + 70 + i * 6, y0 + fall))
        path.cubicTo(QPointF(crest_x + 90 + i * 8, y0 + fall * 1.4), QPointF(width * 0.95, y0 + fall * 1.3),
                     QPointF(width + 20, y0 + fall * 1.2))
        painter.drawPath(path)


def banner(width: int = 1280, height: int = 360) -> QImage:
    from ui.icon import paint_icon

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    card = QRectF(0, 0, width, height)
    clip = QPainterPath()
    clip.addRoundedRect(card, 24, 24)
    p.setClipPath(clip)
    background = QLinearGradient(0, 0, width, height)
    background.setColorAt(0.0, QColor("#0e1115"))
    background.setColorAt(1.0, QColor("#1a1f27"))
    p.fillRect(card, background)
    glow = QRadialGradient(QPointF(width * 0.22, height * 0.5), width * 0.35)
    glow.setColorAt(0.0, QColor(182, 240, 74, 34))
    glow.setColorAt(1.0, QColor(182, 240, 74, 0))
    p.fillRect(card, glow)

    grid = QColor("#ffffff")
    grid.setAlphaF(0.025)
    p.setPen(QPen(grid, 1))
    for x in range(0, width, 32):
        p.drawLine(x, 0, x, height)
    for y in range(0, height, 32):
        p.drawLine(0, y, width, y)

    _water(p, float(width), float(height))

    icon = 150.0
    left = 92.0
    top = (height - icon) / 2
    p.save()
    p.translate(left, top)
    paint_icon(p, icon)
    p.restore()

    text_x = left + icon + 44
    title = _font(MONO, 96, QFont.Weight.Bold)
    p.setFont(title)
    p.setPen(QColor("#e6e9ee"))

    title_y = height / 2 - 18
    p.drawText(QPointF(text_x, title_y), "Weir")

    tagline = _font(SANS, 27, QFont.Weight.Normal)
    p.setFont(tagline)
    p.setPen(QColor("#aab2bf"))
    p.drawText(QPointF(text_x + 4, title_y + 50), "Download videos and music from a link.")

    chips = _font(MONO, 17, QFont.Weight.DemiBold)
    p.setFont(chips)
    cm = QFontMetricsF(chips)
    x = text_x + 4
    y = title_y + 82
    for number, word in (("01", "PASTE"), ("02", "CHOOSE"), ("03", "SAVE"), ("04", "RUN")):
        label = f"{number} {word}"
        w = cm.horizontalAdvance(label) + 22
        rect = QRectF(x, y, w, 32)
        p.setPen(QPen(QColor("#343c47"), 1.2))
        p.setBrush(QColor(21, 25, 32, 220))
        p.drawRoundedRect(rect, 7, 7)
        p.setPen(LIME)
        p.drawText(rect.adjusted(11, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, number)
        p.setPen(QColor("#c9cfd8"))
        p.drawText(rect.adjusted(11 + cm.horizontalAdvance(number + " "), 0, 0, 0),
                   Qt.AlignmentFlag.AlignVCenter, word)
        x += w + 10
        if word != "RUN":
            p.setPen(QColor("#5a6473"))
            p.drawText(QRectF(x - 8, y, 8, 32), Qt.AlignmentFlag.AlignCenter, "›")
            x += 4

    p.setClipping(False)
    p.setPen(QPen(QColor("#2a313b"), 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(card.adjusted(1, 1, -1, -1), 24, 24)
    p.end()
    return image


def framed(source: QImage, dark: bool, radius: float = 14, pad: int = 28) -> QImage:
    """Rounded corners, a hairline border and a soft shadow, so screenshots stand out
    on GitHub's white and dark pages alike."""
    w, h = source.width(), source.height()
    image = QImage(w + 2 * pad, h + 2 * pad, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(pad, 0, -2):  # shadow: stacked translucent rounded rects
        alpha = 0.016 if dark else 0.010
        p.setBrush(QColor(0, 0, 0, round(255 * alpha)))
        p.drawRoundedRect(QRectF(pad - i, pad - i + 6, w + 2 * i, h + 2 * i), radius + i, radius + i)
    window = QPainterPath()
    window.addRoundedRect(QRectF(pad, pad, w, h), radius, radius)
    p.setClipPath(window)
    p.drawImage(pad, pad, source)
    p.setClipping(False)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#30363d" if dark else "#d0d7de"), 1.2))
    p.drawRoundedRect(QRectF(pad + 0.5, pad + 0.5, w - 1, h - 1), radius, radius)
    p.end()
    return image


def main() -> int:
    app = QGuiApplication(sys.argv[:1])  # needed for fonts and painting
    app.setApplicationName("Weir")
    from ui.icon import icon_image

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    targets = {docs / "banner.png": banner(), docs / "logo.png": icon_image(256)}
    shots = ROOT / "screenshots"
    for scheme in ("light", "dark"):
        source = QImage(str(shots / f"weir-{scheme}.png"))
        if source.isNull():
            print(f"Run tools/screenshots.py first ({shots / f'weir-{scheme}.png'} is missing)", file=sys.stderr)
            return 1
        targets[docs / f"weir-{scheme}.png"] = framed(source, dark=scheme == "dark")
    for path, image in targets.items():
        if not image.save(str(path), "PNG"):
            print(f"Couldn't write {path}", file=sys.stderr)
            return 1
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
