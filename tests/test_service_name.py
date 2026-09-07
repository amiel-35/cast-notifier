"""Tests for how the legacy `notify.cast_<name>` service is named.

Until 0.1.1 the name came from the *entity id* of the Cast player, so a
player whose entity id had been auto-suffixed produced
`notify.cast_kitchen_2` for an entry titled "Kitchen" -- a name nobody
could have predicted from the UI. Since 0.2.0 it comes from the entry
title, which is what the user sees and can rename.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_notifier import _service_name
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

MEDIA_PLAYER = "media_player.kitchen_2"
CAST_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET
)


def _set_player(hass: HomeAssistant, entity_id: str) -> None:
    hass.states.async_set(
        entity_id,
        "idle",
        {"supported_features": CAST_FEATURES, "volume_level": 0.3},
    )


def _entry(
    media_player: str = MEDIA_PLAYER,
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


async def test_service_is_named_after_the_entry_title(hass: HomeAssistant) -> None:
    """The service name is `cast_<slugified entry title>`.

    The real instance configured `media_player.kitchen_2` under the title
    "Cuisine" and got `notify.cast_cuisine_2`-style surprises: the name
    followed the entity id, suffix and all.
    """
    _set_player(hass, MEDIA_PLAYER)
    entry = _entry(title="Kitchen")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_kitchen")
    assert not hass.services.has_service("notify", "cast_kitchen_2")


async def test_a_multi_word_title_is_slugified(hass: HomeAssistant) -> None:
    """Spaces and accents in the title become a usable service name."""
    _set_player(hass, "media_player.salon")
    entry = _entry("media_player.salon", title="Salle à manger")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_salle_a_manger")


async def test_two_entries_sharing_a_title_are_numbered(hass: HomeAssistant) -> None:
    """A duplicate title falls back to `<slug>_<n>`, in creation order.

    Two players can legitimately be called the same thing, and a service
    name has to be unique: the first entry created keeps the plain slug,
    the next ones are suffixed deterministically.
    """
    _set_player(hass, "media_player.upstairs")
    _set_player(hass, "media_player.downstairs")
    _set_player(hass, "media_player.garden")

    first = _entry("media_player.upstairs", title="Speaker")
    first.add_to_hass(hass)
    second = _entry("media_player.downstairs", title="Speaker")
    second.add_to_hass(hass)
    third = _entry("media_player.garden", title="Speaker")
    third.add_to_hass(hass)

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_speaker")
    assert hass.services.has_service("notify", "cast_speaker_2")
    assert hass.services.has_service("notify", "cast_speaker_3")
    assert first.runtime_data.service_name == "cast_speaker"
    assert second.runtime_data.service_name == "cast_speaker_2"
    assert third.runtime_data.service_name == "cast_speaker_3"


async def test_renaming_the_entry_renames_the_service(hass: HomeAssistant) -> None:
    """Renaming the entry in the UI moves the service to the new name.

    A title change fires the entry's update listeners
    (`homeassistant/config_entries.py`, `_async_update_entry` ->
    `_async_save_and_notify`), and the listener reloads the entry, which
    retracts the old service and registers the new one.
    """
    _set_player(hass, MEDIA_PLAYER)
    entry = _entry(title="Kitchen")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service("notify", "cast_kitchen")

    hass.config_entries.async_update_entry(entry, title="Dining room")
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_dining_room")
    assert not hass.services.has_service("notify", "cast_kitchen")


async def test_renaming_the_entry_renames_the_device(hass: HomeAssistant) -> None:
    """The service device follows the entry title too, not just the service."""
    _set_player(hass, MEDIA_PLAYER)
    entry = _entry(title="Kitchen")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(entry, title="Dining room")
    await hass.async_block_till_done()

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [device.name for device in devices] == ["Dining room"]


async def test_an_empty_title_falls_back_to_the_player(hass: HomeAssistant) -> None:
    """An entry with no title still yields a callable service name.

    `homeassistant/util/__init__.py::slugify` returns the empty string --
    not its usual `"unknown"` fallback -- for an empty input, and
    `notify.cast_` is not a service anyone can call, so the player's
    object id is used instead.
    """
    _set_player(hass, "media_player.kitchen")
    entry = _entry("media_player.kitchen", title="")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_kitchen")


async def test_an_unsluggable_title_keeps_the_core_fallback(
    hass: HomeAssistant,
) -> None:
    """A punctuation-only title is `unknown`, per core's own slugify.

    Not a fallback of this integration's: `slugify("!!!")` returns
    `"unknown"`, so the service is `notify.cast_unknown` -- unhelpful, but
    predictable, callable, and the same name core would produce anywhere
    else.
    """
    _set_player(hass, "media_player.kitchen")
    entry = _entry("media_player.kitchen", title="!!!")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_unknown")


async def test_naming_an_entry_hass_does_not_know_about(hass: HomeAssistant) -> None:
    """The dedup pass degrades to the plain slug for an unregistered entry.

    `_service_name` walks `hass.config_entries.async_entries(DOMAIN)` to
    find out how many entries want the same slug. An entry that is not in
    that list -- a bare `MockConfigEntry`, or one being torn down -- has
    nothing to collide with, and still needs a name.
    """
    entry = _entry("media_player.kitchen", title="Kitchen")

    assert _service_name(hass, entry) == "cast_kitchen"
    assert not hass.config_entries.async_entries(DOMAIN)
