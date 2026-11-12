"""UI variants. Each module in this package defines ``VARIANT`` (a ui.theme.Variant).

See README.md in this folder for how to write one. Variants are listed in
Settings → Appearance in the order of VARIANT_MODULES.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from functools import lru_cache

from ..theme import Variant

DEFAULT_KEY = "classic"

VARIANT_MODULES: tuple[str, ...] = (
    "classic",
    "compact",
    "sidebar",
    "spotlight",
    "steps",
    "midnight",
    "paper",
    "material",
    "fluent",
    "large",
    "mono",
)


def load_variant(module_name: str) -> Variant | None:
    """Import one variant module; a broken module is reported and skipped."""
    try:
        module = importlib.import_module(f"{__name__}.{module_name}")
        variant = getattr(module, "VARIANT")
    except Exception:  # noqa: BLE001 - one bad variant must not stop the app
        traceback.print_exc()
        return None
    if not isinstance(variant, Variant):
        print(f"ui.variants.{module_name}.VARIANT is not a Variant", file=sys.stderr)
        return None
    return variant


@lru_cache(maxsize=1)
def available_variants() -> tuple[Variant, ...]:
    """All loadable variants, the default one first."""
    variants = [v for v in (load_variant(name) for name in VARIANT_MODULES) if v is not None]
    variants.sort(key=lambda v: v.key != DEFAULT_KEY)
    if not variants:
        raise RuntimeError("No UI variant could be loaded")
    return tuple(variants)


def get_variant(key: str) -> Variant:
    variants = available_variants()
    return next((v for v in variants if v.key == key), variants[0])
