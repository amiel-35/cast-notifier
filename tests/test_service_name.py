"""Tests for how the legacy `notify.cast_<name>` service is named.

Until 0.1.1 the name came from the *entity id* of the Cast player, so a
player whose entity id had been auto-suffixed produced
`notify.cast_kitchen_2` for an entry titled "Kitchen" -- a name nobody
could have predicted from the UI. Since 0.2.0 it comes from the entry
title, which is what the user sees and can rename, and the name each
entry ends up with is frozen in its `entry.data` so that nothing another
entry does can move it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
# The `entry.data` keys the service name is frozen into. Spelled out
# rather than imported: they are an on-disk contract, and a test that
# imported the constant could not notice one of them being renamed.
SERVICE_NAME_KEY = "service_name"
SERVICE_NAME_BASE_KEY = "service_name_base"
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


async def test_a_rename_onto_a_name_another_entry_owns_takes_the_next_one(
    hass: HomeAssistant,
) -> None:
    """Renaming an entry onto a taken name must not evict the owner.

    "Kitchen" was created first, so it is first in creation order; rename
    it to "Dining" and a name derived from creation order alone hands it
    `cast_dining`, which the second entry already owns and has already
    registered. `notify/legacy.py::BaseNotificationService`
    `async_register_services` returns early when
    `hass.services.has_service(DOMAIN, self._service_name)` is already
    true (2026.9.1, line 312), so the renamed entry would register
    nothing at all and both entries would believe they own
    `notify.cast_dining` -- one of them speaking on the other's player.
    """
    _set_player(hass, "media_player.upstairs")
    _set_player(hass, "media_player.downstairs")

    first = _entry("media_player.upstairs", title="Kitchen")
    first.add_to_hass(hass)
    second = _entry("media_player.downstairs", title="Dining")
    second.add_to_hass(hass)

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    assert first.runtime_data.service_name == "cast_kitchen"
    assert second.runtime_data.service_name == "cast_dining"

    hass.config_entries.async_update_entry(first, title="Dining")
    await hass.async_block_till_done()

    assert second.runtime_data.service_name == "cast_dining"
    assert first.runtime_data.service_name == "cast_dining_2"
    assert hass.services.has_service("notify", "cast_dining")
    assert hass.services.has_service("notify", "cast_dining_2")
    assert not hass.services.has_service("notify", "cast_kitchen")


async def test_unloading_after_a_rename_leaves_the_other_service_alone(
    hass: HomeAssistant,
) -> None:
    """An entry only ever retracts the service it owns.

    Second half of the collision above: with both entries believing they
    own `notify.cast_dining`, unloading the renamed one would remove a
    service the other is still serving, and the surviving entry -- still
    LOADED -- would go silent with nothing in the log to say so.
    """
    _set_player(hass, "media_player.upstairs")
    _set_player(hass, "media_player.downstairs")

    first = _entry("media_player.upstairs", title="Kitchen")
    first.add_to_hass(hass)
    second = _entry("media_player.downstairs", title="Dining")
    second.add_to_hass(hass)

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(first, title="Dining")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()

    assert second.state is ConfigEntryState.LOADED
    assert hass.services.has_service("notify", "cast_dining")
    assert not hass.services.has_service("notify", "cast_dining_2")


async def test_a_new_entry_never_takes_a_name_a_surviving_entry_owns(
    hass: HomeAssistant,
) -> None:
    """Removing an entry must not hand its neighbour's name to a new one.

    Two entries titled "Speaker" own `cast_speaker` and `cast_speaker_2`.
    Delete the first and add a third one called "Speaker": numbering by
    creation order gives the newcomer `cast_speaker_2`, which the
    survivor owns and has registered -- the same silent no-op as above,
    plus a service that dies with the wrong entry.
    """
    for player in ("media_player.upstairs", "media_player.downstairs"):
        _set_player(hass, player)

    first = _entry("media_player.upstairs", title="Speaker")
    first.add_to_hass(hass)
    second = _entry("media_player.downstairs", title="Speaker")
    second.add_to_hass(hass)

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    assert second.runtime_data.service_name == "cast_speaker_2"

    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()

    _set_player(hass, "media_player.garden")
    third = _entry("media_player.garden", title="Speaker")
    third.add_to_hass(hass)
    assert await hass.config_entries.async_setup(third.entry_id)
    await hass.async_block_till_done()

    assert third.runtime_data.service_name == "cast_speaker"
    assert second.runtime_data.service_name == "cast_speaker_2"
    assert hass.services.has_service("notify", "cast_speaker")
    assert hass.services.has_service("notify", "cast_speaker_2")


async def test_removing_an_entry_does_not_rename_the_other(
    hass: HomeAssistant,
) -> None:
    """The survivor of two entries sharing a title keeps its own name.

    Until 0.2.0 the number came from the entry's rank in creation order,
    so deleting `cast_speaker` promoted `cast_speaker_2` to `cast_speaker`
    on its next reload -- silently, since nothing about that entry had
    changed. A frozen name cannot move on its own.
    """
    _set_player(hass, "media_player.upstairs")
    _set_player(hass, "media_player.downstairs")

    first = _entry("media_player.upstairs", title="Speaker")
    first.add_to_hass(hass)
    second = _entry("media_player.downstairs", title="Speaker")
    second.add_to_hass(hass)

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(second.entry_id)
    await hass.async_block_till_done()

    assert second.runtime_data.service_name == "cast_speaker_2"
    assert hass.services.has_service("notify", "cast_speaker_2")


async def test_the_name_is_frozen_in_the_entry_data(hass: HomeAssistant) -> None:
    """The chosen name is written to `entry.data`, so a restart keeps it.

    An entry from 0.1.x has no stored name -- this is the upgrade path:
    the first setup after the upgrade picks one and freezes it, and every
    later setup reuses it instead of recomputing from whatever the entry
    list looks like then.
    """
    _set_player(hass, "media_player.upstairs")
    _set_player(hass, "media_player.downstairs")

    first = _entry("media_player.upstairs", title="Speaker")
    first.add_to_hass(hass)
    second = _entry("media_player.downstairs", title="Speaker")
    second.add_to_hass(hass)
    assert SERVICE_NAME_KEY not in first.data

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    assert first.data[SERVICE_NAME_KEY] == "cast_speaker"
    assert first.data[SERVICE_NAME_BASE_KEY] == "cast_speaker"
    assert second.data[SERVICE_NAME_KEY] == "cast_speaker_2"
    assert second.data[SERVICE_NAME_BASE_KEY] == "cast_speaker"


async def test_a_frozen_name_survives_a_reload(hass: HomeAssistant) -> None:
    """Reloading both entries in any order gives back the same two names."""
    _set_player(hass, "media_player.upstairs")
    _set_player(hass, "media_player.downstairs")

    first = _entry("media_player.upstairs", title="Speaker")
    first.add_to_hass(hass)
    second = _entry("media_player.downstairs", title="Speaker")
    second.add_to_hass(hass)

    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(second.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(first.entry_id)
    await hass.async_block_till_done()

    assert first.runtime_data.service_name == "cast_speaker"
    assert second.runtime_data.service_name == "cast_speaker_2"


async def test_a_notify_service_owned_by_something_else_is_left_alone(
    hass: HomeAssistant,
) -> None:
    """A `notify.*` name another integration already serves is never taken.

    Core registers nothing when the name is already in use (see the
    collision test above), so claiming it would leave this entry mute and
    unloading it would delete somebody else's service.
    """

    async def _foreign(call: Any) -> None:
        """Stand in for another integration's notify service."""

    hass.services.async_register("notify", "cast_kitchen", _foreign)

    _set_player(hass, MEDIA_PLAYER)
    entry = _entry(title="Kitchen")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.service_name == "cast_kitchen_2"
    assert hass.services.has_service("notify", "cast_kitchen_2")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_kitchen")
    assert not hass.services.has_service("notify", "cast_kitchen_2")


async def test_a_disabled_entry_still_reserves_its_number(
    hass: HomeAssistant,
) -> None:
    """Disabling an entry does not renumber the ones created after it.

    A disabled entry never sets up, so it never registers a service and
    never freezes a name -- but it is still in the entry list, and
    enabling it again would hand it the plain slug. Counting it keeps the
    numbering the same whether or not it is switched on.
    """
    _set_player(hass, "media_player.upstairs")
    _set_player(hass, "media_player.downstairs")

    first = _entry("media_player.upstairs", title="Speaker")
    first.add_to_hass(hass)
    await hass.config_entries.async_set_disabled_by(
        first.entry_id, ConfigEntryDisabler.USER
    )
    second = _entry("media_player.downstairs", title="Speaker")
    second.add_to_hass(hass)

    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    assert second.runtime_data.service_name == "cast_speaker_2"
    assert SERVICE_NAME_KEY not in first.data


async def test_a_frozen_name_is_dropped_when_something_else_serves_it(
    hass: HomeAssistant,
) -> None:
    """A stored name another integration now serves is not reused.

    The freeze exists so that nothing *another Cast Notifier entry* does
    can move a name. It was never meant to override core: while this
    entry was unloaded, a foreign integration registered
    `notify.cast_kitchen`, and
    `notify/legacy.py::BaseNotificationService.async_register_services`
    returns early when the name is already taken (2026.9.1, line 312).
    Reusing the stored name verbatim would leave this entry LOADED and
    mute, and its unload would delete the other integration's service.

    So the stored name is checked against the same foreign-service test a
    fresh name goes through: it fails, the entry recomputes to
    `cast_kitchen_2`, and persists that.
    """

    async def _foreign(call: Any) -> None:
        """Stand in for another integration's notify service."""

    _set_player(hass, MEDIA_PLAYER)
    entry = _entry(title="Kitchen")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data[SERVICE_NAME_KEY] == "cast_kitchen"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    hass.services.async_register("notify", "cast_kitchen", _foreign)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.service_name == "cast_kitchen_2"
    assert entry.data[SERVICE_NAME_KEY] == "cast_kitchen_2"
    assert hass.services.has_service("notify", "cast_kitchen_2")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_kitchen")
    assert not hass.services.has_service("notify", "cast_kitchen_2")
