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

from dataclasses import dataclass, field

from homeassistant.components.notify.const import DOMAIN as NOTIFY_DOMAIN
from homeassistant.components.notify.legacy import (
    NOTIFY_SERVICES,
    BaseNotificationService,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, callback
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
    # Set by notify.py's `async_get_service` once the legacy service has
    # actually been created, so unload knows which instance to retract.
    legacy_service: BaseNotificationService | None = field(default=None)
    # Set by the unload callback. The legacy platform is registered from a
    # dispatcher callback that Home Assistant runs in a task of its own
    # (`helpers/discovery.py` -> `async_dispatcher_send_internal`), which
    # nothing here owns: it can still be in flight when the entry unloads,
    # and would then re-register a service that was just retracted. See
    # `async_get_service`.
    unloaded: bool = field(default=False)


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
        deny_domains=[
            domain.casefold()
            for domain in options.get(CONF_DENY_DOMAINS, DEFAULT_DENY_DOMAINS)
        ],
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> bool:
    """Set up Cast Notifier from a config entry."""
    speaker = CastSpeaker(hass, _build_speaker_config(entry))
    service_name = _service_name(entry.data[CONF_MEDIA_PLAYER])
    runtime_data = CastNotifierRuntimeData(speaker=speaker, service_name=service_name)
    entry.runtime_data = runtime_data

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    @callback
    def _async_retract_legacy_service() -> None:
        """Remove the legacy `notify.cast_<name>` service on unload.

        Home Assistant never does this for us: `notify/legacy.py` only
        unregisters legacy services from `async_reset_platform`, which is
        called from `homeassistant/helpers/reload.py` for YAML reloads --
        never from a config entry unload. Without this, deleting an entry
        leaves a dangling `notify.cast_<name>` that calls into a dead
        speaker, and re-adding or reconfiguring it is a no-op because
        `BaseNotificationService.async_register_services` returns early
        when `hass.services.has_service(...)` is already true.

        `hass.services.async_remove` rather than the service object's own
        `async_unregister_services()`: that method is a coroutine, and an
        `entry.async_on_unload` callback is synchronous, so calling it
        would mean firing a task whose completion nothing waits for -- the
        entry could be set up again before the old service is gone. It
        also reads `self._service_name`, a private attribute that only
        exists once `async_register_services` has run, which is not
        guaranteed here (discovery may never have completed). Removing the
        service by name is synchronous, and correct either way.
        """
        runtime_data.unloaded = True
        if hass.services.has_service(NOTIFY_DOMAIN, service_name):
            hass.services.async_remove(NOTIFY_DOMAIN, service_name)
        services = hass.data.get(NOTIFY_SERVICES, {}).get(DOMAIN)
        instance = runtime_data.legacy_service
        if services is not None and instance is not None and instance in services:
            services.remove(instance)
            if not services:
                del hass.data[NOTIFY_SERVICES][DOMAIN]
        runtime_data.legacy_service = None

    entry.async_on_unload(_async_retract_legacy_service)

    # Legacy `notify.cast_<name>` service, discovered like `mobile_app` does
    # for its own per-device services. `CONF_NAME` is what the legacy notify
    # platform slugifies into the service name (see notify/legacy.py:
    # async_setup_legacy.async_setup_platform).
    # Tied to the entry rather than to `hass`: `ConfigEntry.async_unload`
    # awaits the entry's own tasks (`_async_process_on_unload` in
    # `config_entries.py`), so the discovery cannot still be running
    # against an entry that no longer exists. A `hass.async_create_task`
    # is owned by nobody and outlives the entry entirely.
    entry.async_create_task(
        hass,
        discovery.async_load_platform(
            hass,
            Platform.NOTIFY,
            DOMAIN,
            {CONF_NAME: service_name, "entry_id": entry.entry_id},
            {},
        ),
        name="cast_notifier legacy notify discovery",
        eager_start=True,
    )

    # Modern `NotifyEntity`.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> bool:
    """Unload a config entry.

    The legacy service is retracted by the `entry.async_on_unload` callback
    registered in `async_setup_entry`, which Home Assistant runs once this
    returns `True`.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> bool:
    """Migrate an old config entry.

    Nothing to do yet: the entry format has not changed since the first
    release (`VERSION` 1, `MINOR_VERSION` 1). This exists so that the first
    schema change ships as a migration instead of as a broken entry, and so
    the shape of that future migration is already decided.
    """
    return True


async def _async_update_options(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> None:
    """Reload the entry when options change.

    The reload is what makes an options change take effect on the legacy
    service too: unloading retracts `notify.cast_<name>`, so setting up
    again registers it afresh against a `CastSpeaker` built from the new
    options (see `_async_retract_legacy_service`).
    """
    await hass.config_entries.async_reload(entry.entry_id)
