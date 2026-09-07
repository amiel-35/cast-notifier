"""Tests for quiet hours: refusing or lowering announcements at night.

The window itself is a pure function of two `HH:MM:SS` strings and a
`datetime.time`, so it is unit tested directly (every boundary, midnight
crossing included) rather than by freezing the clock fifteen times. The
behaviour that depends on the wall clock -- refusal, quiet volume,
`data.priority: critical` bypass -- is tested through `CastSpeaker` with
the clock frozen.
"""

from __future__ import annotations

import logging
from datetime import time
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.cast_notifier.const import (
    CONF_DENY_DOMAINS,
    CONF_MEDIA_PLAYER,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_RESTORE_VOLUME,
    CONF_TTS_ENTITY,
    CONF_VOLUME,
    DOMAIN,
)
from custom_components.cast_notifier.speaker import (
    CastNotifierQuietHours,
    CastSpeaker,
    CastSpeakerConfig,
    SpeakRequest,
    is_within_quiet_hours,
)

MEDIA_PLAYER = "media_player.kitchen"
TTS_ENTITY = "tts.demo"
CAST_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.VOLUME_SET
)

# A night window that crosses midnight, i.e. the interesting one.
NIGHT = {"quiet_start": "22:00:00", "quiet_end": "07:00:00"}
# A window that does not cross midnight, e.g. a nap in the afternoon.
NAP = {"quiet_start": "14:00:00", "quiet_end": "16:00:00"}


def _set_player(hass: HomeAssistant, state: str = "idle", **attributes: Any) -> None:
    hass.states.async_set(
        MEDIA_PLAYER,
        state,
        {"supported_features": CAST_FEATURES, **attributes},
    )


async def _freeze(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: str
) -> None:
    """Pin the instance to UTC, then stop the clock at `when`.

    The test harness sets the instance to US/Pacific
    (`pytest_homeassistant_custom_component/common.py`), and quiet hours
    are evaluated against the instance's local time, so a frozen UTC
    instant only means what it looks like once the zone is pinned.
    """
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(when)


def _config(**overrides: object) -> CastSpeakerConfig:
    defaults: dict[str, object] = {
        "media_player": MEDIA_PLAYER,
        "tts_entity": TTS_ENTITY,
    }
    defaults.update(overrides)
    return CastSpeakerConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start", "end", "now", "expected"),
    [
        # Crossing midnight: 22:00 -> 07:00.
        ("22:00:00", "07:00:00", time(21, 59), False),
        ("22:00:00", "07:00:00", time(22, 0), True),
        ("22:00:00", "07:00:00", time(23, 30), True),
        ("22:00:00", "07:00:00", time(2, 0), True),
        ("22:00:00", "07:00:00", time(6, 59), True),
        # The end of the window is exclusive: 07:00 is already daytime.
        ("22:00:00", "07:00:00", time(7, 0), False),
        ("22:00:00", "07:00:00", time(12, 0), False),
        # Same day: 14:00 -> 16:00.
        ("14:00:00", "16:00:00", time(13, 59), False),
        ("14:00:00", "16:00:00", time(14, 0), True),
        ("14:00:00", "16:00:00", time(15, 59, 59), True),
        ("14:00:00", "16:00:00", time(16, 0), False),
        ("14:00:00", "16:00:00", time(23, 0), False),
        # Not configured, half configured, or a zero-length window: off.
        (None, None, time(3, 0), False),
        ("22:00:00", None, time(23, 0), False),
        (None, "07:00:00", time(3, 0), False),
        ("22:00:00", "22:00:00", time(22, 0), False),
        # Unparseable values never silently turn into a window.
        ("nonsense", "07:00:00", time(3, 0), False),
        ("22:00:00", "later", time(23, 0), False),
    ],
)
def test_quiet_window_boundaries(
    start: str | None, end: str | None, now: time, expected: bool
) -> None:
    """The window is half-open `[start, end)` and may cross midnight."""
    assert is_within_quiet_hours(start, end, now) is expected


async def test_a_message_inside_quiet_hours_is_refused(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With no `quiet_volume`, a night message is refused, not spoken.

    ADR-015 of the suite: a refusal raises, so an automation is told the
    message was not spoken instead of being answered with a silent
    success.
    """
    await _freeze(hass, freezer, "2026-09-07 23:30:00+00:00")
    _set_player(hass)
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(**NIGHT))

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(CastNotifierQuietHours) as raised,
    ):
        await speaker.async_speak(SpeakRequest(message="Washing machine done"))

    assert len(calls) == 0
    assert "quiet_hours" in caplog.text
    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "quiet_hours"
    assert raised.value.translation_placeholders == {
        "player": MEDIA_PLAYER,
        "start": "22:00:00",
        "end": "07:00:00",
    }


async def test_the_quiet_hours_refusal_is_a_service_validation_error() -> None:
    """A refusal is a problem with the call, not a crash inside it."""
    assert issubclass(CastNotifierQuietHours, ServiceValidationError)


async def test_a_message_outside_quiet_hours_is_spoken(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Outside the window, nothing changes."""
    await _freeze(hass, freezer, "2026-09-07 12:00:00+00:00")
    _set_player(hass)
    calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(**NIGHT))
    await speaker.async_speak(SpeakRequest(message="Hello"))

    assert len(calls) == 1


async def test_quiet_volume_speaks_instead_of_refusing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, freezer: FrozenDateTimeFactory
) -> None:
    """A configured `quiet_volume` replaces the refusal by a quiet message."""
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0)
    await _freeze(hass, freezer, "2026-09-07 23:30:00+00:00")
    _set_player(hass, volume_level=0.8)
    speak_calls = async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(
        hass, _config(volume=0.5, quiet_volume=0.1, restore_volume=True, **NIGHT)
    )
    await speaker.async_speak(SpeakRequest(message="Hello"))

    assert len(speak_calls) == 1
    assert [call.data["volume_level"] for call in volume_calls] == [0.1, 0.8]


async def test_quiet_volume_applies_without_a_configured_volume(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, freezer: FrozenDateTimeFactory
) -> None:
    """`quiet_volume` alone is enough to manage the volume at night.

    An entry with no `volume` option never touches the volume by day; at
    night it still has to, otherwise `quiet_volume` would mean nothing.
    """
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0)
    await _freeze(hass, freezer, "2026-09-07 23:30:00+00:00")
    _set_player(hass, volume_level=0.8)
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(quiet_volume=0.1, **NIGHT))
    await speaker.async_speak(SpeakRequest(message="Hello"))

    assert [call.data["volume_level"] for call in volume_calls] == [0.1, 0.8]


async def test_a_per_call_volume_wins_over_quiet_volume(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, freezer: FrozenDateTimeFactory
) -> None:
    """`data.volume` is the most specific instruction there is, so it wins."""
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0)
    await _freeze(hass, freezer, "2026-09-07 23:30:00+00:00")
    _set_player(hass, volume_level=0.8)
    async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.5, quiet_volume=0.1, **NIGHT))
    await speaker.async_speak(SpeakRequest(message="Hello", data={"volume": 0.4}))

    assert volume_calls[0].data["volume_level"] == 0.4


async def test_a_critical_message_ignores_quiet_hours_entirely(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, freezer: FrozenDateTimeFactory
) -> None:
    """`data.priority: critical` speaks at the configured volume, at night.

    A water leak at 3am is exactly what a notifier is for. `priority` is
    the key Notify Switchboard forwards untouched, so a critical alert
    stays critical all the way down.
    """
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0)
    await _freeze(hass, freezer, "2026-09-07 03:00:00+00:00")
    _set_player(hass, volume_level=0.8)
    speak_calls = async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(volume=0.5, quiet_volume=0.1, **NIGHT))
    await speaker.async_speak(
        SpeakRequest(message="Water leak", data={"priority": "critical"})
    )

    assert len(speak_calls) == 1
    assert volume_calls[0].data["volume_level"] == 0.5


async def test_a_critical_message_is_not_refused_at_night(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The bypass applies to the refusal too, not only to the volume."""
    await _freeze(hass, freezer, "2026-09-07 03:00:00+00:00")
    _set_player(hass)
    speak_calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(**NIGHT))
    await speaker.async_speak(
        SpeakRequest(message="Water leak", data={"priority": "CRITICAL"})
    )

    assert len(speak_calls) == 1


async def test_a_non_critical_priority_does_not_bypass(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Only `critical` bypasses; every other priority is a normal message."""
    await _freeze(hass, freezer, "2026-09-07 23:30:00+00:00")
    _set_player(hass)
    speak_calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(**NIGHT))

    with pytest.raises(CastNotifierQuietHours):
        await speaker.async_speak(
            SpeakRequest(message="Hello", data={"priority": "high"})
        )

    assert len(speak_calls) == 0


async def test_a_same_day_window_is_honoured(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A window that does not cross midnight works the same way."""
    await _freeze(hass, freezer, "2026-09-07 15:00:00+00:00")
    _set_player(hass)
    speak_calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(**NAP))

    with pytest.raises(CastNotifierQuietHours):
        await speaker.async_speak(SpeakRequest(message="Hello"))

    assert len(speak_calls) == 0


async def test_quiet_hours_are_off_by_default(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """An entry with no quiet hours configured speaks at 3am like at noon."""
    await _freeze(hass, freezer, "2026-09-07 03:00:00+00:00")
    _set_player(hass)
    speak_calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config())
    await speaker.async_speak(SpeakRequest(message="Hello"))

    assert len(speak_calls) == 1


async def test_a_priority_that_is_not_a_string_is_invalid_data(
    hass: HomeAssistant,
) -> None:
    """`priority` goes through the same `data` validation as everything else."""
    _set_player(hass)
    speak_calls = async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config())

    with pytest.raises(ServiceValidationError):
        await speaker.async_speak(
            SpeakRequest(message="Hello", data={"priority": ["critical"]})
        )

    assert len(speak_calls) == 0


async def test_quiet_hours_through_the_legacy_service(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """End to end: the refusal and the `critical` bypass reach the caller.

    The unit tests above drive `CastSpeaker` directly. This one goes
    through `notify.cast_<name>`, which is the surface a wider
    notification layer actually calls, and checks that `data.priority`
    survives the trip -- it is carried in the `data` payload of a legacy
    notify call, which nothing between the caller and the speaker
    inspects.
    """
    await _freeze(hass, freezer, "2026-09-07 23:30:00+00:00")
    _set_player(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER},
        options={
            CONF_TTS_ENTITY: TTS_ENTITY,
            CONF_RESTORE_VOLUME: True,
            CONF_VOLUME: None,
            CONF_DENY_DOMAINS: [],
            CONF_QUIET_START: "22:00:00",
            CONF_QUIET_END: "07:00:00",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    speak_calls = async_mock_service(hass, "tts", "speak")

    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            "notify", "cast_kitchen", {"message": "Hello"}, blocking=True
        )

    assert len(speak_calls) == 0
    assert raised.value.translation_key == "quiet_hours"

    await hass.services.async_call(
        "notify",
        "cast_kitchen",
        {"message": "Water leak", "data": {"priority": "critical"}},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(speak_calls) == 1
    assert speak_calls[0].data["message"] == "Water leak"


async def test_a_player_that_cannot_set_its_volume_warns_about_quiet_volume(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`quiet_volume` on a fixed-output player is a warning, not a refusal.

    A Cast group, or any player that does not advertise `VOLUME_SET`, is
    left alone entirely (`speaker.py`, `manage_volume`): the message is
    spoken at whatever volume the player is on, and the whole point of
    `quiet_volume` -- "at night, but quietly" -- silently does not
    happen. Speaking anyway is the right call, since the alternative is a
    notifier that goes mute at night; saying nothing about it is not.
    """
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0)
    await _freeze(hass, freezer, "2026-09-07 23:30:00+00:00")
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {
            "supported_features": MediaPlayerEntityFeature.PLAY_MEDIA,
            "volume_level": 0.8,
        },
    )
    speak_calls = async_mock_service(hass, "tts", "speak")
    volume_calls = async_mock_service(hass, "media_player", "volume_set")

    speaker = CastSpeaker(hass, _config(quiet_volume=0.1, **NIGHT))

    with caplog.at_level(
        logging.WARNING, logger="custom_components.cast_notifier.speaker"
    ):
        await speaker.async_speak(SpeakRequest(message="Hello"))

    assert len(speak_calls) == 1
    assert volume_calls == []
    assert "quiet_volume could not be applied" in caplog.text
    assert MEDIA_PLAYER in caplog.text


async def test_a_configured_volume_that_cannot_be_applied_says_nothing_extra(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warning is about quiet hours, not about volume management.

    A player that cannot set its volume is a permanent, deliberate
    situation; warning on every announcement would make the log useless.
    The quiet-hours case is different: the user configured a rule that is
    silently not happening.
    """
    monkeypatch.setattr("custom_components.cast_notifier.speaker.PLAYBACK_TIMEOUT", 0)
    await _freeze(hass, freezer, "2026-09-07 12:00:00+00:00")
    hass.states.async_set(
        MEDIA_PLAYER,
        "idle",
        {
            "supported_features": MediaPlayerEntityFeature.PLAY_MEDIA,
            "volume_level": 0.8,
        },
    )
    async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(volume=0.4, quiet_volume=0.1, **NIGHT))

    with caplog.at_level(
        logging.WARNING, logger="custom_components.cast_notifier.speaker"
    ):
        await speaker.async_speak(SpeakRequest(message="Hello"))

    assert "quiet_volume could not be applied" not in caplog.text


async def test_quiet_hours_follow_the_instance_time_zone(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The window is read in local time, not in UTC.

    23:30 UTC is 01:30 in Paris, i.e. the middle of a 22:00-07:00 night
    window -- and 23:30 UTC would be inside that window too, which proves
    nothing. The daytime half below is what does.
    """
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-09-07 23:30:00+00:00")
    _set_player(hass)
    async_mock_service(hass, "tts", "speak")

    speaker = CastSpeaker(hass, _config(**NIGHT))
    with pytest.raises(ServiceValidationError):
        await speaker.async_speak(SpeakRequest(message="Hello"))

    # 06:30 UTC is 08:30 in Paris: past the end of the window, although a
    # UTC reading would still call it night.
    freezer.move_to("2026-09-07 06:30:00+00:00")
    await speaker.async_speak(SpeakRequest(message="Hello"))


async def test_a_dst_transition_moves_the_window_with_the_clock(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The offset used is the one in force on the day, not a fixed one.

    Europe/Paris moves from +01:00 to +02:00 at 01:00 UTC on 2026-03-29.
    The same 05:30 UTC instant is therefore 06:30 local the day before --
    still inside a 22:00-07:00 night window -- and 07:30 local on the
    transition day, which is daytime. Anything that cached an offset, or
    compared UTC, would treat the two identically.
    """
    await hass.config.async_set_time_zone("Europe/Paris")
    _set_player(hass)
    async_mock_service(hass, "tts", "speak")
    speaker = CastSpeaker(hass, _config(**NIGHT))

    freezer.move_to("2026-03-28 05:30:00+00:00")
    with pytest.raises(ServiceValidationError):
        await speaker.async_speak(SpeakRequest(message="Hello"))

    freezer.move_to("2026-03-29 05:30:00+00:00")
    await speaker.async_speak(SpeakRequest(message="Hello"))
