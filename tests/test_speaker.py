"""Unit tests for CastSpeaker: the speaking, volume and deny_domains logic.

These exercise `CastSpeaker` directly against a bare `hass`, without going
through a config entry, so each behavior (deny_domains, volume management,
per-call overrides, waiting for playback) can be tested in isolation from
config flow / notify platform wiring (covered in test_config_flow.py and
test_notify.py).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.cast_notifier.const import DOMAIN
from custom_components.cast_notifier.speaker import (
    CastNotifierInvalidData,
    CastNotifierRefused,
    CastSpeaker,
    CastSpeakerConfig,
    SpeakRequest,
)

MEDIA_PLAYER = "media_player.kitchen"
TTS_ENTITY = "tts.demo"

# What a real Cast player advertises: it can change its volume, and it
# never advertises MEDIA_ANNOUNCE (see docs/ARCHITECTURE.md).
CAST_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET
)


def _set_player(
    hass: HomeAssistant,
    state: str,
    *,
    supported_features: int = CAST_FEATURES,
    **attributes: Any,
) -> None:
    """Put the target media_player in a given state."""
    hass.states.async_set(
        MEDIA_PLAYER,
        state,
        {"supported_features": supported_features, **attributes},
    )


def _mock_live_volume_set(
    hass: HomeAssistant, state: str = "idle"
) -> list[ServiceCall]:
    """Mock `media_player.volume_set` so it really moves `volume_level`.

    `async_mock_service` records calls without touching the state machine,
    which hides any bug where the speaker re-reads the volume it just set
    and mistakes it for the user's own setting.
    """
    calls: list[ServiceCall] = []

    async def _handle(call: ServiceCall) -> None:
        calls.append(call)
        _set_player(hass, state, volume_level=call.data["volume_level"])

    hass.services.async_register("media_player", "volume_set", _handle)
    return calls


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
    _set_player(hass, "idle")
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
    _set_player(hass, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(announce_prefix="Attention."))
    await speaker.async_speak(SpeakRequest(message="Water leak"))

    assert calls[0].data["message"] == "Attention. Water leak"


async def test_voice_string_becomes_options_dict(hass: HomeAssistant) -> None:
    """A plain string `voice` is wrapped as {"voice": ...} in tts options."""
    _set_player(hass, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(voice="fr-FR-Standard-A"))
    await speaker.async_speak(SpeakRequest(message="Bonjour"))

    assert calls[0].data["options"] == {"voice": "fr-FR-Standard-A"}


async def test_voice_json_object_is_passed_through(hass: HomeAssistant) -> None:
    """A JSON object `voice` fully controls the tts options dict."""
    _set_player(hass, "idle")
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
    _set_player(hass, "idle")
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
    _set_player(hass, "idle", volume_level=0.8)
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.2, restore_volume=False))
    await speaker.async_speak(SpeakRequest(message="Hi", data={"volume": 0.9}))

    assert volume_calls[0].data["volume_level"] == 0.9


async def test_deny_domains_refuses_without_calling_tts(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A denied message is refused, never spoken, logged *and* raised.

    ADR-0003: a refusal is an error for the caller, not a silent success.
    The WARNING stays because the log line is the operator's only trace
    when the caller did not pass `blocking: true`.
    """
    _set_player(hass, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(deny_domains=["alarm_control_panel", "lock"]))

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(CastNotifierRefused) as raised,
    ):
        await speaker.async_speak(
            SpeakRequest(
                message="Alarm is armed",
                data={"source_entity": "alarm_control_panel.home"},
            )
        )

    assert len(calls) == 0
    assert "alarm_control_panel.home" in caplog.text
    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "message_refused"
    assert raised.value.translation_placeholders == {
        "domain": "alarm_control_panel",
        "source_entity": "alarm_control_panel.home",
        "player": MEDIA_PLAYER,
    }


async def test_refusals_are_service_validation_errors(hass: HomeAssistant) -> None:
    """Both refusal types are `ServiceValidationError`s (ADR-0003).

    Home Assistant renders those as their translated message, without a
    traceback: the caller asked for something refused, nothing crashed.
    """
    assert issubclass(CastNotifierRefused, ServiceValidationError)
    assert issubclass(CastNotifierInvalidData, ServiceValidationError)


async def test_deny_domains_allows_other_domains(hass: HomeAssistant) -> None:
    """A source_entity outside deny_domains is spoken normally."""
    _set_player(hass, "idle")
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
    _set_player(hass, "idle", volume_level=0.8)
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
    _set_player(hass, "idle", volume_level=0.8)
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
    _set_player(hass, "idle", volume_level=0.8)
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
    _set_player(hass, "idle", volume_level=0.8)
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    async def _simulate_playback() -> None:
        await asyncio.sleep(0.01)
        _set_player(hass, "playing", volume_level=0.3)
        await asyncio.sleep(0.01)
        _set_player(hass, "idle", volume_level=0.3)

    hass.async_create_task(_simulate_playback())

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))
    await asyncio.wait_for(speaker.async_speak(SpeakRequest(message="Hi")), timeout=2)

    assert [call.data["volume_level"] for call in volume_calls] == [0.3, 0.8]


async def test_volume_is_restored_when_tts_speak_fails(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing `tts.speak` still puts the volume back (regression: B3).

    Before the fix, the set-volume/speak/restore sequence had no
    `try/finally`: an exception from `tts.speak` skipped the restore and
    left the speaker stuck at the announcement volume forever.
    """
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    _set_player(hass, "idle", volume_level=0.8)
    volume_calls = _mock_live_volume_set(hass)

    async def _failing_speak(call: ServiceCall) -> None:
        raise HomeAssistantError("TTS engine is unavailable")

    hass.services.async_register("tts", "speak", _failing_speak)

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))

    with pytest.raises(HomeAssistantError):
        await speaker.async_speak(SpeakRequest(message="Hi"))

    assert [call.data["volume_level"] for call in volume_calls] == [0.3, 0.8]


async def test_overlapping_calls_do_not_strand_the_volume(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two overlapping announcements still end at the original volume (I1).

    Without the per-speaker lock, the second call reads the *announcement*
    volume as "previous" (the first call already lowered it) and restores
    that instead of the user's setting.
    """
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 1)
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.ANNOUNCEMENT_START_TIMEOUT", 0.05
    )
    _set_player(hass, "idle", volume_level=0.8)
    _mock_live_volume_set(hass)

    async def _slow_speak(call: ServiceCall) -> None:
        await asyncio.sleep(0.1)

    hass.services.async_register("tts", "speak", _slow_speak)

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))

    async def _second_call() -> None:
        # Start while the first announcement is still speaking.
        await asyncio.sleep(0.01)
        await speaker.async_speak(SpeakRequest(message="Second"))

    await asyncio.gather(
        speaker.async_speak(SpeakRequest(message="First")), _second_call()
    )

    state = hass.states.get(MEDIA_PLAYER)
    assert state is not None
    assert state.attributes["volume_level"] == 0.8


async def test_already_playing_player_does_not_stall_the_call(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A player already `playing` returns fast instead of stalling (I2).

    The old wait only tracked `state`: a player playing music was
    `playing` before and after the announcement, so phase 1 ("leave the
    previous state") never resolved and every call burned the whole
    `PLAYBACK_TIMEOUT`. The "leave" phase is now bounded separately.
    """
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 5)
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.ANNOUNCEMENT_START_TIMEOUT", 0.2
    )
    _set_player(
        hass,
        "playing",
        volume_level=0.8,
        media_content_id="spotify:track:1",
        media_title="Some song",
        app_id="CC1AD845",
    )
    async_mock_service(hass, "tts", "speak")
    async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))

    started = time.monotonic()
    await speaker.async_speak(SpeakRequest(message="Hi"))
    elapsed = time.monotonic() - started

    assert elapsed < 1, f"speaking stalled for {elapsed:.1f}s"


async def test_media_content_id_change_is_seen_as_the_announcement(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait tracks media identity, not only `state` (I2).

    A Cast player stays `playing` through a TTS announcement; only
    `media_content_id`/`media_title` change. The wait must resolve on the
    round trip, not time out.
    """
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 5)
    music = {
        "media_content_id": "spotify:track:1",
        "media_title": "Some song",
        "app_id": "CC1AD845",
    }
    _set_player(hass, "playing", volume_level=0.8, **music)
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    async def _simulate_announcement() -> None:
        await asyncio.sleep(0.01)
        _set_player(
            hass,
            "playing",
            volume_level=0.3,
            media_content_id="media-source://tts/demo?message=Hi",
            media_title="Hi",
            app_id="CC1AD845",
        )
        await asyncio.sleep(0.01)
        _set_player(hass, "playing", volume_level=0.3, **music)

    hass.async_create_task(_simulate_announcement())

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))
    await asyncio.wait_for(speaker.async_speak(SpeakRequest(message="Hi")), timeout=2)

    assert [call.data["volume_level"] for call in volume_calls] == [0.3, 0.8]


async def test_player_without_volume_set_still_speaks(hass: HomeAssistant) -> None:
    """A player that cannot set its volume is spoken on anyway (I3).

    Cast *groups* and fixed-output devices do not advertise VOLUME_SET;
    calling `media_player.volume_set` on them fails, and the old code let
    that failure abort the announcement.
    """
    _set_player(
        hass,
        "idle",
        supported_features=MediaPlayerEntityFeature.PLAY_MEDIA,
        volume_level=0.8,
    )
    speak_calls = async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))
    await speaker.async_speak(SpeakRequest(message="Hi"))

    assert len(speak_calls) == 1
    assert len(volume_calls) == 0


async def test_failing_volume_set_does_not_prevent_speech(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `volume_set` that raises is logged, and the message is still spoken."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    _set_player(hass, "idle", volume_level=0.8)
    speak_calls = async_mock_service(hass, "tts", "speak")

    async def _refuse(call: ServiceCall) -> None:
        raise HomeAssistantError("Volume is fixed on this device")

    hass.services.async_register("media_player", "volume_set", _refuse)

    speaker = CastSpeaker(hass, _config(volume=0.3, restore_volume=True))
    await speaker.async_speak(SpeakRequest(message="Hi"))

    assert len(speak_calls) == 1


async def test_deny_domains_matching_is_case_insensitive(
    hass: HomeAssistant,
) -> None:
    """`Lock` in the deny list still blocks `lock.*` (I5b)."""
    _set_player(hass, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(deny_domains=["Alarm_Control_Panel", "LOCK"]))

    with pytest.raises(CastNotifierRefused):
        await speaker.async_speak(
            SpeakRequest(
                message="Front door unlocked",
                data={"source_entity": "lock.front_door"},
            )
        )

    assert len(calls) == 0


@pytest.mark.parametrize(
    "data",
    [
        # A number where an entity id is expected: used to blow up with
        # AttributeError inside `_enforce_deny_domains`.
        {"source_entity": 123},
        {"source_entity": "not an entity id"},
        {"volume": 5},
        {"volume": -1},
        {"volume": "loud"},
        {"tts_entity": "media_player.kitchen"},
        {"tts_entity": 42},
        {"language": ["fr"]},
        {"voice": ["fr-FR-Standard-A"]},
    ],
)
async def test_invalid_data_is_refused_with_a_clear_error(
    hass: HomeAssistant, data: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed `data` payload raises `CastNotifierInvalidData` (I5c).

    Translated for the caller, logged for the operator (ADR-0003).
    """
    _set_player(hass, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config())

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(CastNotifierInvalidData) as raised,
    ):
        await speaker.async_speak(SpeakRequest(message="Hi", data=data))

    assert len(calls) == 0
    assert "invalid `data` payload" in caplog.text
    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "invalid_data"
    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["player"] == MEDIA_PLAYER
    assert placeholders["error"]


async def test_valid_data_payload_is_accepted(hass: HomeAssistant) -> None:
    """A well-formed `data` payload -- including a dict `voice` -- passes."""
    _set_player(hass, "idle")
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config())
    await speaker.async_speak(
        SpeakRequest(
            message="Bonjour",
            data={
                "source_entity": "binary_sensor.washing_machine",
                "tts_entity": "tts.other",
                "language": "fr",
                "voice": {"voice": "fr-FR-Standard-A", "gender": "female"},
                "unknown_key": "ignored",
            },
        )
    )

    assert calls[0].data["entity_id"] == "tts.other"
    assert calls[0].data["options"] == {
        "voice": "fr-FR-Standard-A",
        "gender": "female",
    }


async def test_missing_player_still_speaks_without_touching_volume(
    hass: HomeAssistant,
) -> None:
    """A player with no state yet is spoken to, and its volume left alone."""
    speak_calls = async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.3))
    await speaker.async_speak(SpeakRequest(message="Hi"))

    assert len(speak_calls) == 1
    assert len(volume_calls) == 0
