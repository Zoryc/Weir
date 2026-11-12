"""Download presets: built-in ones from presets.json plus user-created ones."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import app_dir, config_dir, resource_dir

PRESETS_FILENAME = "presets.json"

# Used only if presets.json is missing or unreadable.
FALLBACK_PRESETS: list[tuple[str, str, str]] = [
    ("Best video – MP4 (plays everywhere)", "-t mp4",
     "Best quality that plays on any phone, TV or computer."),
    ("1080p MP4",
     '-S "res:1080,vcodec:h264,acodec:aac" --merge-output-format mp4 --remux-video mp4',
     "Full HD video, H.264 + AAC. Very compatible."),
    ("720p MP4 (smaller file)",
     '-S "res:720,vcodec:h264,acodec:aac" --merge-output-format mp4 --remux-video mp4',
     "HD video at roughly half the size of 1080p."),
    ("Max quality – MKV (4K/VP9/AV1)", '-f "bv*+ba/b" -t mkv',
     "Highest resolution available (4K and above). Best played on a computer, e.g. with VLC."),
    ("Audio – MP3 (best quality)", "-x --audio-format mp3 --audio-quality 0",
     "Sound only, as an MP3 that plays everywhere."),
    ("Audio – original (no re-encode)", '-f "ba/b" -x',
     "Sound only, in the site's original format (often Opus or M4A). No quality loss."),
]

# Options a user preset may carry in addition to its yt-dlp arguments.
PRESET_OPTION_KEYS: tuple[str, ...] = (
    "playlist",
    "embed_subs",
    "embed_meta",
    "filename_template",
    "rate_limit",
)


def split_args(text: str) -> list[str]:
    """Split a command-line string into arguments.

    Works the same on Windows and Linux: single or double quotes group words,
    and backslashes are kept literally (so Windows paths survive) except for
    \\" inside double quotes, which produces a literal quote.
    """
    args: list[str] = []
    current: list[str] = []
    quote: str | None = None
    has_token = False
    i = 0
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < len(text) and text[i + 1] == '"':
                current.append('"')
                i += 1
            else:
                current.append(ch)
        elif ch in "\"'":
            quote = ch
            has_token = True
        elif ch.isspace():
            if has_token:
                args.append("".join(current))
                current = []
                has_token = False
        else:
            current.append(ch)
            has_token = True
        i += 1
    if quote is not None:
        raise ValueError(f"Missing closing {quote} quote")
    if has_token:
        args.append("".join(current))
    return args


def join_args(args: list[str]) -> str:
    """Inverse of split_args, for display and for saving presets."""
    parts: list[str] = []
    for arg in args:
        if arg == "" or any(c.isspace() for c in arg) or any(c in arg for c in "\"'"):
            parts.append('"' + arg.replace('"', '\\"') + '"')
        else:
            parts.append(arg)
    return " ".join(parts)


def is_audio_only(args: list[str]) -> bool:
    """True if the arguments extract audio (subtitles make no sense then)."""
    for i, arg in enumerate(args):
        if arg in ("-x", "--extract-audio"):
            return True
        if arg in ("-t", "--preset-alias") and i + 1 < len(args) and args[i + 1] in ("mp3", "aac"):
            return True
    return False


@dataclass
class Preset:
    name: str
    args: list[str]
    builtin: bool = True
    options: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    @property
    def args_text(self) -> str:
        return join_args(self.args)

    @property
    def is_audio(self) -> bool:
        return is_audio_only(self.args)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "args": self.args_text}
        if self.description:
            data["description"] = self.description
        if self.options:
            data["options"] = self.options
        return data


def _parse_preset(entry: Any, builtin: bool) -> Preset | None:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    raw_args = entry.get("args", "")
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        if isinstance(raw_args, str):
            args = split_args(raw_args)
        elif isinstance(raw_args, list) and all(isinstance(a, str) for a in raw_args):
            args = list(raw_args)
        else:
            return None
    except ValueError:
        return None
    options_raw = entry.get("options", {})
    options: dict[str, Any] = {}
    if isinstance(options_raw, dict):
        options = {k: v for k, v in options_raw.items() if k in PRESET_OPTION_KEYS}
    description = entry.get("description", "")
    return Preset(
        name=name.strip(),
        args=args,
        builtin=builtin,
        options=options,
        description=description.strip() if isinstance(description, str) else "",
    )


def _read_presets_file(path: Path, builtin: bool) -> list[Preset] | None:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = data.get("presets") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return None
    presets: list[Preset] = []
    for entry in entries:
        preset = _parse_preset(entry, builtin)
        if preset is not None and all(p.name != preset.name for p in presets):
            presets.append(preset)
    return presets


class PresetStore:
    """Built-in presets (read-only presets.json next to the app) + user presets."""

    def __init__(self, builtin: list[Preset], user: list[Preset]) -> None:
        self.builtin = builtin
        self.user = [p for p in user if all(b.name != p.name for b in builtin)]

    @staticmethod
    def builtin_path() -> Path:
        # Prefer an editable copy next to the executable, then the bundled one.
        for directory in (app_dir(), resource_dir()):
            candidate = directory / PRESETS_FILENAME
            if candidate.is_file():
                return candidate
        return app_dir() / PRESETS_FILENAME

    @staticmethod
    def user_path() -> Path:
        return config_dir() / PRESETS_FILENAME

    @classmethod
    def load(cls) -> PresetStore:
        builtin = _read_presets_file(cls.builtin_path(), builtin=True)
        if not builtin:
            builtin = [Preset(name, split_args(args), description=desc)
                       for name, args, desc in FALLBACK_PRESETS]
        user = _read_presets_file(cls.user_path(), builtin=False) or []
        return cls(builtin, user)

    def all(self) -> list[Preset]:
        return [*self.builtin, *self.user]

    def get(self, name: str) -> Preset | None:
        return next((p for p in self.all() if p.name == name), None)

    def is_builtin_name(self, name: str) -> bool:
        return any(p.name == name for p in self.builtin)

    def add_user(self, preset: Preset) -> None:
        preset.builtin = False
        self.user = [p for p in self.user if p.name != preset.name]
        self.user.append(preset)
        self.save_user()

    def remove_user(self, name: str) -> None:
        self.user = [p for p in self.user if p.name != name]
        self.save_user()

    def save_user(self) -> None:
        path = self.user_path()
        tmp = path.with_suffix(".json.tmp")
        payload = {
            "_comment": "Your own presets. 'args' are yt-dlp options, written like on a command line.",
            "presets": [p.to_json() for p in self.user],
        }
        try:
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
