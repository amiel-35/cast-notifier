"""Diagnostics support for Cast Notifier.

Nothing Cast Notifier stores is a secret: `deny_domains` is a policy list,
and the entity IDs it speaks through are visible everywhere else in Home
Assistant. `TO_REDACT` is therefore empty, but the redaction pass runs
anyway so that adding a sensitive option later is a one-line change to that
tuple rather than a review of this whole module.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CastNotifierConfigEntry

TO_REDACT: tuple[str, ...] = ()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Diagnostics can be downloaded for an entry that failed to set up or is
    disabled, in which case `runtime_data` does not exist; the stored
    data/options are still worth reporting, so that case degrades instead
    of raising.
    """
    diagnostics: dict[str, Any] = {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "loaded": hasattr(entry, "runtime_data"),
    }

    if hasattr(entry, "runtime_data"):
        runtime_data = entry.runtime_data
        diagnostics["speaker_config"] = asdict(runtime_data.speaker.config)
        diagnostics["service_name"] = runtime_data.service_name
        # The volume trace of the most recent announcement, or `None` if
        # this entry has not spoken since it was last loaded. Bounded to
        # one announcement: enough to answer "why was it that loud?" for
        # the message someone just heard, without turning diagnostics into
        # a log of its own.
        last = runtime_data.speaker.last_announcement
        diagnostics["last_announcement"] = last.as_dict() if last else None
    else:
        diagnostics["speaker_config"] = None
        diagnostics["service_name"] = None
        diagnostics["last_announcement"] = None

    return async_redact_data(diagnostics, TO_REDACT)
