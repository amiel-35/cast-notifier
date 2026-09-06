"""Diagnostics support for Cast Notifier.

`deny_domains` is not a secret, and neither are the entity IDs Cast
Notifier speaks through, so nothing here is redacted.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import CastNotifierConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "speaker_config": asdict(entry.runtime_data.speaker.config),
        "service_name": entry.runtime_data.service_name,
    }
