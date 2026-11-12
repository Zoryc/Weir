"""Settings: the default file name pattern and the migration of old defaults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.settings import DEFAULT_TEMPLATE, OLD_DEFAULT_TEMPLATES, Settings, migrate_template


@pytest.fixture
def settings_file(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Settings at a throwaway file instead of the user's real settings."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(Settings, "path", staticmethod(lambda: path))
    return path


def _write(path: Path, **values: object) -> None:
    data = {"save_dir": str(path.parent), **values}
    path.write_text(json.dumps(data), encoding="utf-8")


def test_default_template_limits_the_title_length() -> None:
    assert DEFAULT_TEMPLATE == "%(title).150B [%(id)s].%(ext)s"
    assert DEFAULT_TEMPLATE not in OLD_DEFAULT_TEMPLATES
    assert Settings().filename_template == DEFAULT_TEMPLATE


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("%(title)s [%(id)s].%(ext)s", DEFAULT_TEMPLATE),
        ("", DEFAULT_TEMPLATE),
        ("   ", DEFAULT_TEMPLATE),
        (DEFAULT_TEMPLATE, DEFAULT_TEMPLATE),
        ("%(uploader)s/%(title)s [%(id)s].%(ext)s", "%(uploader)s/%(title)s [%(id)s].%(ext)s"),
        ("%(title)s.%(ext)s", "%(title)s.%(ext)s"),
        ("%(title)s [%(id)s].%(ext)s.backup", "%(title)s [%(id)s].%(ext)s.backup"),
    ],
)
def test_migrate_template(template: str, expected: str) -> None:
    assert migrate_template(template) == expected


def test_load_moves_the_old_default_to_the_new_one(settings_file: Path) -> None:
    _write(settings_file, filename_template="%(title)s [%(id)s].%(ext)s", max_parallel=3)
    settings = Settings.load()
    assert settings.filename_template == DEFAULT_TEMPLATE
    assert settings.max_parallel == 3


def test_load_keeps_a_custom_template(settings_file: Path) -> None:
    custom = "%(uploader)s/%(title)s.%(ext)s"
    _write(settings_file, filename_template=custom)
    assert Settings.load().filename_template == custom


def test_load_fills_in_a_blank_template(settings_file: Path) -> None:
    _write(settings_file, filename_template="  ")
    assert Settings.load().filename_template == DEFAULT_TEMPLATE


def test_migrated_template_is_saved(settings_file: Path) -> None:
    _write(settings_file, filename_template="%(title)s [%(id)s].%(ext)s")
    Settings.load().save()
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["filename_template"] == DEFAULT_TEMPLATE


def test_user_preset_with_an_old_default_template_gets_the_new_one() -> None:
    from core.presets import _parse_preset

    old = OLD_DEFAULT_TEMPLATES[0]
    preset = _parse_preset({"name": "Mine", "args": "-t mp4",
                            "options": {"filename_template": old}}, builtin=False)
    assert preset is not None and preset.options["filename_template"] == DEFAULT_TEMPLATE
    own = "%(uploader)s - %(title)s.%(ext)s"
    preset = _parse_preset({"name": "Mine", "args": "-t mp4",
                            "options": {"filename_template": own}}, builtin=False)
    assert preset is not None and preset.options["filename_template"] == own
