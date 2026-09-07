"""Tests for the volume timeline recorded around every announcement.

The first real-world announcement (07/09/2026, a Nest speaker, Home
Assistant Cloud TTS) played at an observed volume of 0.55 while the entry
was configured for 0.40, and nothing in the logs said why. These tests
pin down the trace that will answer that question next time: five volume
readings, timestamped, in the log at DEBUG and in the entry's diagnostics.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
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
from custom_components.cast_notifier.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.cast_notifier.speaker import (
    STEP_VOLUME_AFTER_SET,
    STEP_VOLUME_BEFORE,
    STEP_VOLUME_REQUESTED,
    STEP_VOLUME_RESTORED,
    STEP_VOLUME_WHILE_PLAYING,
    CastSpeaker,
    CastSpeakerConfig,
    SpeakRequest,
)

MEDIA_PLAYER = "media_player.kitchen"
TTS_ENTITY = "tts.demo"
CAST_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET
)


def _set_player(hass: HomeAssistant, state: str = "idle", **attributes: Any) -> None:
    hass.states.async_set(
        MEDIA_PLAYER,
        state,
        {"supported_features": CAST_FEATURES, **attributes},
    )


def _config(**overrides: object) -> CastSpeakerConfig:
    defaults: dict[str, object] = {
        "media_player": MEDIA_PLAYER,
        "tts_entity": TTS_ENTITY,
    }
    defaults.update(overrides)
    return CastSpeakerConfig(**defaults)  # type: ignore[arg-type]


def _volume_set_moves_the_player(hass: HomeAssistant) -> None:
    """Make `media_player.volume_set` really move `volume_level`."""

    async def _handle(call: ServiceCall) -> None:
        _set_player(hass, "idle", volume_level=call.data["volume_level"])

    hass.services.async_register("media_player", "volume_set", _handle)


def _speak_plays_at(hass: HomeAssistant, volume: float) -> None:
    """Mock `tts.speak` so the player reports `playing` at `volume`.

    This is the 0.55-while-configured-for-0.40 case from the real
    instance: the announcement is audibly louder than the volume the
    integration set, and only an observation taken *during* playback can
    show it.
    """

    async def _handle(call: ServiceCall) -> None:
        _set_player(
            hass,
            "playing",
            volume_level=volume,
            media_content_id="media-source://tts/demo?message=Hi",
        )

    hass.services.async_register("tts", "speak", _handle)


async def test_the_timeline_records_the_five_volume_readings(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One announcement leaves a five-step, timestamped volume timeline."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    _set_player(hass, "idle", volume_level=0.30)
    _volume_set_moves_the_player(hass)
    _speak_plays_at(hass, 0.55)

    speaker = CastSpeaker(hass, _config(volume=0.40, restore_volume=True))
    await speaker.async_speak(SpeakRequest(message="Hi"))

    timeline = speaker.last_announcement
    assert timeline is not None
    volumes = {step["step"]: step["volume"] for step in timeline.steps}
    assert volumes == {
        STEP_VOLUME_BEFORE: 0.30,
        STEP_VOLUME_REQUESTED: 0.40,
        STEP_VOLUME_AFTER_SET: 0.40,
        STEP_VOLUME_WHILE_PLAYING: 0.55,
        STEP_VOLUME_RESTORED: 0.30,
    }
    # Timestamps, in order, so a slow step is visible as well as a wrong
    # value.
    stamps = [step["at"] for step in timeline.steps]
    assert stamps == sorted(stamps)
    assert timeline.player == MEDIA_PLAYER


async def test_every_step_is_logged_at_debug(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same five readings reach the log, for an instance with no UI."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    _set_player(hass, "idle", volume_level=0.30)
    _volume_set_moves_the_player(hass)
    _speak_plays_at(hass, 0.55)

    speaker = CastSpeaker(hass, _config(volume=0.40))

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.cast_notifier.speaker"
    ):
        await speaker.async_speak(SpeakRequest(message="Hi"))

    for step in (
        STEP_VOLUME_BEFORE,
        STEP_VOLUME_REQUESTED,
        STEP_VOLUME_AFTER_SET,
        STEP_VOLUME_WHILE_PLAYING,
        STEP_VOLUME_RESTORED,
    ):
        assert step in caplog.text
    assert "0.55" in caplog.text


async def test_only_the_last_announcement_is_kept(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The timeline is bounded to one announcement, not a growing history."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    _set_player(hass, "idle", volume_level=0.30)
    _volume_set_moves_the_player(hass)
    async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(volume=0.40))
    await speaker.async_speak(SpeakRequest(message="First"))
    await speaker.async_speak(SpeakRequest(message="Second", data={"volume": 0.20}))

    timeline = speaker.last_announcement
    assert timeline is not None
    requested = [
        step["volume"]
        for step in timeline.steps
        if step["step"] == STEP_VOLUME_REQUESTED
    ]
    assert requested == [0.20]


async def test_an_announcement_without_volume_management_still_leaves_a_trace(
    hass: HomeAssistant,
) -> None:
    """With no volume configured, the timeline records what it can.

    There is nothing to request, set or restore, but "what the player was
    at" is still the first thing anyone will ask.
    """
    _set_player(hass, "idle", volume_level=0.30)
    async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config())
    await speaker.async_speak(SpeakRequest(message="Hi"))

    timeline = speaker.last_announcement
    assert timeline is not None
    steps = {step["step"] for step in timeline.steps}
    assert STEP_VOLUME_BEFORE in steps
    assert STEP_VOLUME_REQUESTED not in steps


async def test_a_failed_announcement_still_leaves_a_timeline(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `tts.speak` that raises is exactly when the trace matters most."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    _set_player(hass, "idle", volume_level=0.30)
    _volume_set_moves_the_player(hass)

    async def _failing_speak(call: ServiceCall) -> None:
        raise HomeAssistantError("TTS engine is unavailable")

    hass.services.async_register("tts", "speak", _failing_speak)

    speaker = CastSpeaker(hass, _config(volume=0.40))

    with pytest.raises(HomeAssistantError):
        await speaker.async_speak(SpeakRequest(message="Hi"))

    timeline = speaker.last_announcement
    assert timeline is not None
    volumes = {step["step"]: step["volume"] for step in timeline.steps}
    assert volumes[STEP_VOLUME_REQUESTED] == 0.40
    assert volumes[STEP_VOLUME_RESTORED] == 0.30


async def test_diagnostics_expose_the_last_announcement(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The timeline is downloadable from the entry, not only greppable."""
    monkeypatch.setattr(
        "custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0.05
    )
    _set_player(hass, "idle", volume_level=0.30)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER},
        options={
            CONF_TTS_ENTITY: TTS_ENTITY,
            CONF_RESTORE_VOLUME: True,
            CONF_VOLUME: 0.40,
            CONF_DENY_DOMAINS: [],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    before = await async_get_config_entry_diagnostics(hass, entry)
    assert before["last_announcement"] is None

    _volume_set_moves_the_player(hass)
    _speak_plays_at(hass, 0.55)
    await hass.services.async_call(
        "notify", "cast_kitchen", {"message": "Hi"}, blocking=True
    )
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    last = diagnostics["last_announcement"]
    assert last is not None
    volumes = {step["step"]: step["volume"] for step in last["steps"]}
    assert volumes[STEP_VOLUME_WHILE_PLAYING] == 0.55
    assert volumes[STEP_VOLUME_REQUESTED] == 0.40
