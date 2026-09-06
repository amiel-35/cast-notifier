"""Tests for the Cast Notifier notify platform (legacy service + entity)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.cast_notifier.const import (
    CONF_DENY_DOMAINS,
    CONF_MEDIA_PLAYER,
    CONF_RESTORE_VOLUME,
    CONF_TTS_ENTITY,
    CONF_VOLUME,
    DOMAIN,
)
from custom_components.cast_notifier.notify import async_get_service

MEDIA_PLAYER = "media_player.kitchen"


def _entry(**option_overrides: object) -> MockConfigEntry:
    options: dict[str, object] = {
        CONF_TTS_ENTITY: "tts.demo",
        CONF_RESTORE_VOLUME: True,
        CONF_VOLUME: None,
        CONF_DENY_DOMAINS: ["alarm_control_panel", "lock"],
    }
    options.update(option_overrides)
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER},
        options=options,
    )


async def test_setup_registers_legacy_service_named_after_the_player(
    hass: HomeAssistant,
) -> None:
    """Setting up the entry registers notify.cast_<object_id>."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service("notify", "cast_kitchen")


async def test_setup_registers_notify_entity(hass: HomeAssistant) -> None:
    """Setting up the entry also registers a NotifyEntity."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.async_entity_ids("notify")


async def test_legacy_service_speaks_the_message(hass: HomeAssistant) -> None:
    """notify.cast_kitchen triggers tts.speak with the configured player."""
    hass.states.async_set(MEDIA_PLAYER, "idle")

    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Registered only now: setting up the entry sets up the real `tts`
    # integration as a dependency, which registers its own `speak` service
    # (an entity service requiring the target to actually exist). Mocking
    # it before that point would just be overwritten once `tts` sets up.
    speak_calls = async_mock_service(hass, "tts", "speak")

    await hass.services.async_call(
        "notify",
        "cast_kitchen",
        {"message": "Washing machine finished"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 1
    assert speak_calls[0].data["media_player_entity_id"] == MEDIA_PLAYER
    assert speak_calls[0].data["message"] == "Washing machine finished"
    assert speak_calls[0].data["entity_id"] == "tts.demo"


async def test_legacy_service_refuses_denied_source_entity(
    hass: HomeAssistant,
) -> None:
    """A call about a denied domain is refused: tts.speak is never called."""
    hass.states.async_set(MEDIA_PLAYER, "idle")

    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    await hass.services.async_call(
        "notify",
        "cast_kitchen",
        {
            "message": "The alarm is now armed",
            "data": {"source_entity": "alarm_control_panel.home"},
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 0


async def test_legacy_service_per_call_data_overrides(hass: HomeAssistant) -> None:
    """`data.language`/`voice`/`tts_entity` override the entry's settings."""
    hass.states.async_set(MEDIA_PLAYER, "idle")

    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    await hass.services.async_call(
        "notify",
        "cast_kitchen",
        {
            "message": "Bonjour",
            "data": {
                "language": "fr",
                "voice": "fr-voice",
                "tts_entity": "tts.other",
            },
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert speak_calls[0].data["language"] == "fr"
    assert speak_calls[0].data["options"] == {"voice": "fr-voice"}
    assert speak_calls[0].data["entity_id"] == "tts.other"


async def test_notify_entity_speaks_the_message(hass: HomeAssistant) -> None:
    """`notify.send_message` on the entity also triggers `tts.speak`."""
    hass.states.async_set(MEDIA_PLAYER, "idle")

    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    entity_id = hass.states.async_entity_ids("notify")[0]
    await hass.services.async_call(
        "notify",
        "send_message",
        {"entity_id": entity_id, "message": "Bonsoir"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 1
    assert speak_calls[0].data["message"] == "Bonsoir"
    assert speak_calls[0].data["media_player_entity_id"] == MEDIA_PLAYER


async def test_legacy_service_surfaces_a_tts_failure(hass: HomeAssistant) -> None:
    """A failing `tts.speak` reaches the caller as a HomeAssistantError (B3).

    Only a `deny_domains` refusal is swallowed; a broken engine is a bug
    the automation author must see.
    """
    hass.states.async_set(MEDIA_PLAYER, "idle")

    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async def _failing_speak(call: ServiceCall) -> None:
        raise HomeAssistantError("TTS engine is unavailable")

    hass.services.async_register("tts", "speak", _failing_speak)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "notify", "cast_kitchen", {"message": "Hello"}, blocking=True
        )


async def test_legacy_service_surfaces_invalid_data(hass: HomeAssistant) -> None:
    """A malformed `data` payload reaches the caller, not a TypeError (I5c)."""
    hass.states.async_set(MEDIA_PLAYER, "idle")

    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "notify",
            "cast_kitchen",
            {"message": "Hello", "data": {"volume": 11}},
            blocking=True,
        )

    assert len(speak_calls) == 0


async def test_unexpected_error_is_wrapped_as_home_assistant_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-HomeAssistantError from the speaker still reaches the caller (B3)."""
    hass.states.async_set(MEDIA_PLAYER, "idle")

    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async def _boom(self: object, request: object) -> None:
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.CastSpeaker.async_speak", _boom
    )

    with pytest.raises(HomeAssistantError, match="could not speak"):
        await hass.services.async_call(
            "notify", "cast_kitchen", {"message": "Hello"}, blocking=True
        )


async def test_legacy_platform_refuses_yaml_setup(hass: HomeAssistant) -> None:
    """Without discovery info (i.e. from YAML) no service is created."""
    assert await async_get_service(hass, {}, None) is None
    assert await async_get_service(hass, {}, {}) is None


async def test_legacy_platform_refuses_an_unloaded_entry(hass: HomeAssistant) -> None:
    """A discovery pointing at an entry with no runtime data is ignored."""
    entry = _entry()
    entry.add_to_hass(hass)

    assert await async_get_service(hass, {}, {"entry_id": entry.entry_id}) is None
    assert await async_get_service(hass, {}, {"entry_id": "does-not-exist"}) is None
