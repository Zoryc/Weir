"""User settings, stored as JSON in the platform config folder."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .paths import config_dir, default_download_dir

DEFAULT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"
COOKIE_BROWSERS: list[tuple[str, str]] = [
    ("", "None"),
    ("firefox", "Firefox"),
    ("chrome", "Chrome"),
    ("edge", "Edge"),
    ("brave", "Brave"),
]


@dataclass
class Settings:
    save_dir: str = ""
    last_preset: str = ""
    playlist: bool = False
    embed_subs: bool = False
    embed_meta: bool = True
    extra_args: str = ""
    filename_template: str = DEFAULT_TEMPLATE
    cookies_browser: str = ""
    rate_limit: str = ""
    max_parallel: int = 2
    show_log: bool = False
    show_advanced: bool = False
    skip_startup_deps_prompt: bool = False
    window_geometry: str = ""

    @staticmethod
    def path() -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> Settings:
        settings = cls()
        try:
            raw: Any = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            for f in fields(cls):
                if f.name not in raw:
                    continue
                value = raw[f.name]
                default = getattr(settings, f.name)
                # bool is a subclass of int, so compare exact types.
                if type(value) is type(default):
                    setattr(settings, f.name, value)
        settings._normalize()
        return settings

    def _normalize(self) -> None:
        if not self.save_dir:
            self.save_dir = str(default_download_dir())
        if not self.filename_template.strip():
            self.filename_template = DEFAULT_TEMPLATE
        self.max_parallel = max(1, min(3, self.max_parallel))
        if self.cookies_browser not in {key for key, _ in COOKIE_BROWSERS}:
            self.cookies_browser = ""

    def save(self) -> None:
        path = self.path()
        tmp = path.with_suffix(".json.tmp")
        data = json.dumps(asdict(self), indent=2, ensure_ascii=False)
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
