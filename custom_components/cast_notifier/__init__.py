"""The Cast Notifier integration.

Home Assistant has no `notify` that speaks: voice goes through `tts.speak`,
an action that `alert`, blueprints and automations cannot list as a
notifier. Cast Notifier fixes that with one `notify.*` service per
configured Cast player. See docs/ARCHITECTURE.md for the full contract.

This module wires a config entry to two independent notify surfaces defined
in notify.py:

- the legacy `notify.cast_<name>` service, loaded through the discovery
  helper so that `alert.notifiers:` can reference it by name;
- a modern `NotifyEntity`, set up as a regular entity platform.

Both surfaces share the same `CastSpeaker` instance stored on
`entry.runtime_data`.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery

from .const import (
    CONF_ANNOUNCE_PREFIX,
    CONF_DENY_DOMAINS,
    CONF_LANGUAGE,
    CONF_MEDIA_PLAYER,
    CONF_RESTORE_VOLUME,
    CONF_TTS_ENTITY,
    CONF_VOICE,
    CONF_VOLUME,
    DEFAULT_DENY_DOMAINS,
    DEFAULT_RESTORE_VOLUME,
    DOMAIN,
)
from .speaker import CastSpeaker, CastSpeakerConfig

PLATFORMS: list[Platform] = [Platform.NOTIFY]


@dataclass(slots=True)
class CastNotifierRuntimeData:
    """Runtime data stored on the config entry."""

    speaker: CastSpeaker
    service_name: str


type CastNotifierConfigEntry = ConfigEntry[CastNotifierRuntimeData]


def _service_name(media_player: str) -> str:
    """Derive the `notify.cast_<name>` service name from an entity ID.

    `notify/legacy.py::async_setup_legacy.async_setup_platform` slugifies
    whatever is passed as `CONF_NAME` in the discovery payload into the
    final service name, so this only needs to produce a readable object id
    (e.g. `media_player.kitchen` -> `cast_kitchen`).
    """
    object_id = media_player.split(".", 1)[-1]
    return f"cast_{object_id}"


def _build_speaker_config(entry: CastNotifierConfigEntry) -> CastSpeakerConfig:
    """Build the speaker config from a config entry's data and options."""
    options = entry.options
    return CastSpeakerConfig(
        media_player=entry.data[CONF_MEDIA_PLAYER],
        tts_entity=options[CONF_TTS_ENTITY],
        language=options.get(CONF_LANGUAGE),
        voice=options.get(CONF_VOICE),
        volume=options.get(CONF_VOLUME),
        restore_volume=options.get(CONF_RESTORE_VOLUME, DEFAULT_RESTORE_VOLUME),
        announce_prefix=options.get(CONF_ANNOUNCE_PREFIX),
        deny_domains=list(options.get(CONF_DENY_DOMAINS, DEFAULT_DENY_DOMAINS)),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> bool:
    """Set up Cast Notifier from a config entry."""
    speaker = CastSpeaker(hass, _build_speaker_config(entry))
    service_name = _service_name(entry.data[CONF_MEDIA_PLAYER])
    entry.runtime_data = CastNotifierRuntimeData(
        speaker=speaker, service_name=service_name
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    # Legacy `notify.cast_<name>` service, discovered like `mobile_app` does
    # for its own per-device services. `CONF_NAME` is what the legacy notify
    # platform slugifies into the service name (see notify/legacy.py:
    # async_setup_legacy.async_setup_platform).
    hass.async_create_task(
        discovery.async_load_platform(
            hass,
            Platform.NOTIFY,
            DOMAIN,
            {CONF_NAME: service_name, "entry_id": entry.entry_id},
            {},
        ),
        eager_start=True,
    )

    # Modern `NotifyEntity`.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_options(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
