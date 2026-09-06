"""Unit tests for CastSpeaker: the speaking, volume and deny_domains logic.

These exercise `CastSpeaker` directly against a bare `hass`, without going
through a config entry, so each behavior (deny_domains, volume management,
per-call overrides, waiting for playback) can be tested in isolation from
config flow / notify platform wiring (covered in test_config_flow.py and
test_notify.py).
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.cast_notifier.speaker import (
    CastNotifierRefused,
    CastSpeaker,
    CastSpeakerConfig,
    SpeakRequest,
)

MEDIA_PLAYER = "media_player.kitchen"
TTS_ENTITY = "tts.demo"


def _config(**overrides: object) -> CastSpeakerConfig:
    defaults: dict[str, object] = {
        "media_player": MEDIA_PLAYER,
        "tts_entity": TTS_ENTITY,
    }
    defaults.update(overrides)
    return CastSpeakerConfig(**defaults)  # type: ignore[arg-type]


async def test_speak_calls_tts_with_media_player_and_message(
    hass: HomeAssistant,
) -> None:
    """A plain call speaks the message on the configured player."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(language="en"))
    await speaker.async_speak(SpeakRequest(message="Hello there"))

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == TTS_ENTITY
    assert calls[0].data["media_player_entity_id"] == MEDIA_PLAYER
    assert calls[0].data["message"] == "Hello there"
    assert calls[0].data["language"] == "en"
    assert calls[0].data["cache"] is True
    assert "options" not in calls[0].data


async def test_announce_prefix_is_prepended(hass: HomeAssistant) -> None:
    """`announce_prefix` is spoken before the message, space-separated."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(announce_prefix="Attention."))
    await speaker.async_speak(SpeakRequest(message="Water leak"))

    assert calls[0].data["message"] == "Attention. Water leak"


async def test_voice_string_becomes_options_dict(hass: HomeAssistant) -> None:
    """A plain string `voice` is wrapped as {"voice": ...} in tts options."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(voice="fr-FR-Standard-A"))
    await speaker.async_speak(SpeakRequest(message="Bonjour"))

    assert calls[0].data["options"] == {"voice": "fr-FR-Standard-A"}


async def test_voice_json_object_is_passed_through(hass: HomeAssistant) -> None:
    """A JSON object `voice` fully controls the tts options dict."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(
        hass, _config(voice='{"voice": "fr-FR-Standard-A", "gender": "female"}')
    )
    await speaker.async_speak(SpeakRequest(message="Bonjour"))

    assert calls[0].data["options"] == {
        "voice": "fr-FR-Standard-A",
        "gender": "female",
    }


async def test_per_call_overrides_tts_entity_language_and_voice(
    hass: HomeAssistant,
) -> None:
    """`data.tts_entity`/`language`/`voice` override the entry's settings."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(
        hass, _config(tts_entity="tts.default", language="en", voice="en-voice")
    )
    await speaker.async_speak(
        SpeakRequest(
            message="Bonjour",
            data={
                "tts_entity": "tts.other",
                "language": "fr",
                "voice": "fr-voice",
            },
        )
    )

    assert calls[0].data["entity_id"] == "tts.other"
    assert calls[0].data["language"] == "fr"
    assert calls[0].data["options"] == {"voice": "fr-voice"}


async def test_per_call_volume_overrides_entry_volume(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`data.volume` overrides the entry's configured volume for one call."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.8})
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.2, restore_volume=False))
    await speaker.async_speak(SpeakRequest(message="Hi", data={"volume": 0.9}))

    assert volume_calls[0].data["volume_level"] == 0.9


async def test_deny_domains_refuses_without_calling_tts(hass: HomeAssistant) -> None:
    """A message about a denied domain is refused and never spoken."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(deny_domains=["alarm_control_panel", "lock"]))

    with pytest.raises(CastNotifierRefused):
        await speaker.async_speak(
            SpeakRequest(
                message="Alarm is armed",
                data={"source_entity": "alarm_control_panel.home"},
            )
        )

    assert len(calls) == 0


async def test_deny_domains_allows_other_domains(hass: HomeAssistant) -> None:
    """A source_entity outside deny_domains is spoken normally."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(deny_domains=["alarm_control_panel"]))
    await speaker.async_speak(
        SpeakRequest(
            message="Washing machine is done",
            data={"source_entity": "binary_sensor.washing_machine"},
        )
    )

    assert len(calls) == 1


async def test_volume_set_before_and_restored_after_speaking(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """volume is set before speaking and restored afterwards by default."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.8})
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))
    await speaker.async_speak(SpeakRequest(message="Hi"))

    assert [call.data["volume_level"] for call in volume_calls] == [0.3, 0.8]


async def test_restore_volume_false_keeps_the_new_volume(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """restore_volume=False leaves the player at the volume used to speak."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.8})
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=False))
    await speaker.async_speak(SpeakRequest(message="Hi"))

    assert len(volume_calls) == 1
    assert volume_calls[0].data["volume_level"] == 0.3


async def test_no_volume_configured_never_touches_volume(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a configured/override volume, volume_set is never called."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.8})
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config())
    await speaker.async_speak(SpeakRequest(message="Hi"))

    assert len(volume_calls) == 0


async def test_waits_for_the_player_to_actually_return_to_idle(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait resolves as soon as the player is back, not just on timeout.

    `PLAYBACK_TIMEOUT` is set high on purpose: if `CastSpeaker` only ever
    waited for the timeout instead of tracking the actual state change,
    this test's outer `asyncio.wait_for` would raise `TimeoutError`.
    """
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 5)
    hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.8})
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    async def _simulate_playback() -> None:
        await asyncio.sleep(0.01)
        hass.states.async_set(MEDIA_PLAYER, "playing", {"volume_level": 0.3})
        await asyncio.sleep(0.01)
        hass.states.async_set(MEDIA_PLAYER, "idle", {"volume_level": 0.3})

    hass.async_create_task(_simulate_playback())

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))
    await asyncio.wait_for(speaker.async_speak(SpeakRequest(message="Hi")), timeout=2)

    assert [call.data["volume_level"] for call in volume_calls] == [0.3, 0.8]
