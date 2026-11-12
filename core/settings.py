"""User settings, stored as JSON in the platform config folder."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .browsers import valid_cookie_setting
from .paths import config_dir, default_download_dir

# The title is cut to 150 bytes: Linux allows 255 bytes per file name, and
# Japanese or Korean titles take 3 bytes per character.
DEFAULT_TEMPLATE = "%(title).150B [%(id)s].%(ext)s"
# Earlier defaults. Saved settings that still hold one of them exactly get the
# current default (the first one had no length limit, so long titles failed).
OLD_DEFAULT_TEMPLATES: tuple[str, ...] = ("%(title)s [%(id)s].%(ext)s",)


def migrate_template(template: str) -> str:
    """The current default for a blank template or an old default, otherwise the template itself."""
    if not template.strip() or template in OLD_DEFAULT_TEMPLATES:
        return DEFAULT_TEMPLATE
    return template


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
        self.filename_template = migrate_template(self.filename_template)
        self.max_parallel = max(1, min(3, self.max_parallel))
        # "" (no cookies), a browser yt-dlp knows, or "file:<path to cookies.txt>".
        if not valid_cookie_setting(self.cookies_browser):
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
