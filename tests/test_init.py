"""Tests for the config entry lifecycle: setup, unload, reload, migration.

These cover what `test_notify.py` cannot: what happens to the legacy
`notify.cast_<name>` service and to the entity/device registries when an
entry is removed, reconfigured, or migrated.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.cast_notifier import async_migrate_entry
from custom_components.cast_notifier.config_flow import CastNotifierConfigFlow
from custom_components.cast_notifier.const import (
    CONF_ANNOUNCE_PREFIX,
    CONF_DENY_DOMAINS,
    CONF_LANGUAGE,
    CONF_MEDIA_PLAYER,
    CONF_RESTORE_VOLUME,
    CONF_TTS_ENTITY,
    CONF_VOICE,
    CONF_VOLUME,
    DOMAIN,
)

MEDIA_PLAYER = "media_player.kitchen"
CAST_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET
)


def _set_player(hass: HomeAssistant, entity_id: str = MEDIA_PLAYER) -> None:
    hass.states.async_set(
        entity_id, "idle", {"supported_features": CAST_FEATURES, "volume_level": 0.8}
    )


def _entry(
    media_player: str = MEDIA_PLAYER,
    # The service name comes from the title since 0.2.0, so the default
    # here is what makes `notify.cast_kitchen` the name under test.
    title: str = "Kitchen",
    **option_overrides: Any,
) -> MockConfigEntry:
    options: dict[str, Any] = {
        CONF_TTS_ENTITY: "tts.demo",
        CONF_LANGUAGE: None,
        CONF_VOICE: None,
        CONF_VOLUME: None,
        CONF_RESTORE_VOLUME: True,
        CONF_ANNOUNCE_PREFIX: None,
        CONF_DENY_DOMAINS: ["alarm_control_panel", "lock"],
    }
    options.update(option_overrides)
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=media_player,
        data={CONF_MEDIA_PLAYER: media_player},
        options=options,
    )


async def test_removing_the_entry_removes_the_legacy_service(
    hass: HomeAssistant,
) -> None:
    """Deleting an entry retracts `notify.cast_<name>` (regression: B1).

    Home Assistant never unregisters a legacy notify service on config
    entry unload -- `notify/legacy.py::async_reset_platform` is only
    reachable from `helpers/reload.py` -- so before the fix the service
    survived removal of the entry and kept calling into a dead speaker.
    """
    _set_player(hass)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service("notify", "cast_kitchen")

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service("notify", "cast_kitchen")


async def test_unloading_the_entry_removes_the_legacy_service(
    hass: HomeAssistant,
) -> None:
    """Unloading (without removing) also retracts the service (B1)."""
    _set_player(hass)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service("notify", "cast_kitchen")


async def test_unload_is_quiet_when_the_service_is_already_gone(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Unloading does not complain about a service that is not registered.

    `hass.services.async_remove` logs "Unable to remove unknown service"
    for a name it does not know, so the retraction is guarded by
    `has_service`.
    """
    _set_player(hass)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Something else got there first (a YAML reload, a manual removal).
    hass.services.async_remove("notify", "cast_kitchen")
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert "Unable to remove unknown service" not in caplog.text


async def test_the_discovery_task_belongs_to_the_entry(hass: HomeAssistant) -> None:
    """Setting up then unloading leaves no service behind.

    The legacy platform is discovered through `entry.async_create_task`,
    so `ConfigEntry.async_unload` awaits it instead of leaving it to
    register a service against an entry that is already gone.
    """
    _set_player(hass)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.services.has_service("notify", "cast_kitchen")


async def test_options_change_takes_effect_on_the_legacy_service(
    hass: HomeAssistant,
) -> None:
    """Changing `tts_entity` in the options flow changes what is spoken (B2).

    The reload on an options change re-runs `async_get_service`, but
    `BaseNotificationService.async_register_services` returns early when
    the service name is already registered, so before B1 the *old*
    speaker's service object stayed bound and kept using the old TTS
    engine forever.
    """
    _set_player(hass)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        {
            CONF_TTS_ENTITY: "tts.other_engine",
            CONF_LANGUAGE: "",
            CONF_VOICE: "",
            CONF_RESTORE_VOLUME: True,
            CONF_ANNOUNCE_PREFIX: "",
            CONF_DENY_DOMAINS: "alarm_control_panel, lock",
        },
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_TTS_ENTITY] == "tts.other_engine"

    speak_calls = async_mock_service(hass, "tts", "speak")
    await hass.services.async_call(
        "notify", "cast_kitchen", {"message": "Hello"}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 1
    assert speak_calls[0].data["entity_id"] == "tts.other_engine"


async def test_two_entries_get_distinct_entities_and_devices(
    hass: HomeAssistant,
) -> None:
    """Each entry owns its own device and its own notify entity (I4).

    Before the fix every entry hard-coded the same `Cast Notifier` name
    and registered no device, so the second entry collided on entity id
    and both were indistinguishable in the UI.
    """
    _set_player(hass, MEDIA_PLAYER)
    _set_player(hass, "media_player.living_room")

    kitchen = _entry(MEDIA_PLAYER, title="Kitchen speaker")
    kitchen.add_to_hass(hass)
    living_room = _entry("media_player.living_room", title="Living room speaker")
    living_room.add_to_hass(hass)

    # Setting up the integration sets up every entry of the domain at once.
    assert await hass.config_entries.async_setup(kitchen.entry_id)
    await hass.async_block_till_done()
    assert living_room.state is ConfigEntryState.LOADED

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entities = {
        entry_id: er.async_entries_for_config_entry(entity_registry, entry_id)
        for entry_id in (kitchen.entry_id, living_room.entry_id)
    }
    assert all(len(found) == 1 for found in entities.values())
    entity_ids = {found[0].entity_id for found in entities.values()}
    assert len(entity_ids) == 2

    devices = {
        entry_id: dr.async_entries_for_config_entry(device_registry, entry_id)
        for entry_id in (kitchen.entry_id, living_room.entry_id)
    }
    assert all(len(found) == 1 for found in devices.values())
    assert len({found[0].id for found in devices.values()}) == 2
    assert {found[0].name for found in devices.values()} == {
        "Kitchen speaker",
        "Living room speaker",
    }
    # Each entity is attached to its own entry's device, and takes its name.
    for entry_id, found in entities.items():
        assert found[0].device_id == devices[entry_id][0].id


async def test_config_entry_declares_a_minor_version(hass: HomeAssistant) -> None:
    """The handler declares MINOR_VERSION so a schema change can migrate (I6)."""
    assert CastNotifierConfigFlow.VERSION == 1
    assert CastNotifierConfigFlow.MINOR_VERSION == 1


async def test_migration_is_a_no_op_today(hass: HomeAssistant) -> None:
    """`async_migrate_entry` exists and accepts a current entry (I6)."""
    _set_player(hass)
    entry = _entry()
    entry.add_to_hass(hass)

    assert entry.version == 1
    assert entry.minor_version == 1
    assert await async_migrate_entry(hass, entry) is True
