"""Tests for the Cast Notifier notify platform (legacy service + entity)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
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
