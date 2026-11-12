"""Small layout helpers used by ui/design.py."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLayout, QVBoxLayout, QWidget

# Layout items accepted by hbox()/vbox():
#   QWidget / QLayout         added as-is
#   (QWidget | QLayout, int)  added with that stretch factor
#   int                       fixed spacing in px
#   None                      a flexible stretch
Item = QWidget | QLayout | int | None | tuple[QWidget | QLayout, int]


def _fill(box: QBoxLayout, items: Iterable[Item]) -> None:
    for item in items:
        stretch = 0
        if isinstance(item, tuple):
            item, stretch = item
        if item is None:
            box.addStretch(1)
        elif isinstance(item, int):
            box.addSpacing(item)
        elif isinstance(item, QLayout):
            box.addLayout(item, stretch)
        else:
            box.addWidget(item, stretch)


def hbox(*items: Item, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QHBoxLayout:
    box = QHBoxLayout()
    box.setSpacing(spacing)
    box.setContentsMargins(*margins)
    _fill(box, items)
    return box


def vbox(*items: Item, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QVBoxLayout:
    box = QVBoxLayout()
    box.setSpacing(spacing)
    box.setContentsMargins(*margins)
    _fill(box, items)
    return box


def panel(parent: QWidget, name: str) -> QFrame:
    """Plain frame with an objectName, styled by the design's stylesheet."""
    frame = QFrame(parent)
    frame.setObjectName(name)
    frame.setFrameShape(QFrame.Shape.NoFrame)
    return frame


def set_stretch(widget: QWidget, stretch: int) -> bool:
    """Change the stretch factor of a widget inside whatever box layout holds it."""
    parent = widget.parentWidget()
    root = parent.layout() if parent is not None else None
    box = _find_box(root, widget) if root is not None else None
    if box is None:
        return False
    return box.setStretchFactor(widget, stretch)


def _find_box(layout: QLayout, widget: QWidget) -> QBoxLayout | None:
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item is None:
            continue
        if item.widget() is widget and isinstance(layout, QBoxLayout):
            return layout
        child = item.layout()
        if child is not None:
            found = _find_box(child, widget)
            if found is not None:
                return found
    return None
