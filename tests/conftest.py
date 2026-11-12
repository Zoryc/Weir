"""Shared test setup: project root on sys.path, a headless QApplication, isolated settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole test run, with its own settings folder name."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setApplicationName(f"Weir-tests-{os.getpid()}")
    app = QApplication.instance() or QApplication([])
    yield app
