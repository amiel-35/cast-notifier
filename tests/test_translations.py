"""Ensure translation files stay in sync with strings.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

INTEGRATION_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "cast_notifier"
)


def _flatten_keys(data: dict[str, Any], prefix: str = "") -> set[str]:
    """Return every dotted key path in a nested translation mapping."""
    keys: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys |= _flatten_keys(value, path)
        else:
            keys.add(path)
    return keys


def test_translations_match_strings_reference() -> None:
    """en, fr and es must expose exactly the same keys as strings.json."""
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    reference_keys = _flatten_keys(strings)
    assert reference_keys, "strings.json should not be empty"

    for language in ("en", "fr", "es"):
        translation = json.loads(
            (INTEGRATION_DIR / "translations" / f"{language}.json").read_text()
        )
        translation_keys = _flatten_keys(translation)
        assert translation_keys == reference_keys, (
            f"translations/{language}.json is missing "
            f"{reference_keys - translation_keys} and has extra "
            f"{translation_keys - reference_keys}"
        )
