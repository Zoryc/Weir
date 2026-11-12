"""Paper variant — placeholder until it is designed (reuses the Classic layout)."""

from __future__ import annotations

from dataclasses import replace

from .classic import VARIANT as _CLASSIC

VARIANT = replace(_CLASSIC, key="paper", name="Paper", description="Placeholder (Classic look).")
