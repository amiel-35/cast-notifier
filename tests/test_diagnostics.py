"""Tests for Cast Notifier diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cast_notifier.const import (
    CONF_DENY_DOMAINS,
    CONF_MEDIA_PLAYER,
    CONF_RESTORE_VOLUME,
    CONF_SERVICE_NAME,
    CONF_SERVICE_NAME_BASE,
    CONF_TTS_ENTITY,
    CONF_VOLUME,
    DOMAIN,
)
from custom_components.cast_notifier.diagnostics import (
    async_get_config_entry_diagnostics,
)

MEDIA_PLAYER = "media_player.kitchen"


async def test_diagnostics_report_entry_and_speaker_config(
    hass: HomeAssistant,
) -> None:
    """Diagnostics expose the entry's data/options and the speaker config."""
    hass.states.async_set(MEDIA_PLAYER, "idle")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER},
        options={
            CONF_TTS_ENTITY: "tts.demo",
            CONF_RESTORE_VOLUME: True,
            CONF_VOLUME: 0.5,
            CONF_DENY_DOMAINS: ["alarm_control_panel", "lock"],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["data"] == {
        CONF_MEDIA_PLAYER: MEDIA_PLAYER,
        # Frozen by the first setup, and reported: "which service is this
        # entry supposed to own" is the first question a bug report about
        # a missing notifier has to answer.
        CONF_SERVICE_NAME: "cast_kitchen",
        CONF_SERVICE_NAME_BASE: "cast_kitchen",
    }
    assert diagnostics["options"][CONF_TTS_ENTITY] == "tts.demo"
    assert diagnostics["speaker_config"]["media_player"] == MEDIA_PLAYER
    assert diagnostics["speaker_config"]["volume"] == 0.5
    assert diagnostics["service_name"] == "cast_kitchen"
    # Nothing has been spoken yet: the timeline is there and empty, not
    # missing (see tests/test_observability.py).
    assert diagnostics["last_announcement"] is None


async def test_diagnostics_survive_an_unloaded_entry(hass: HomeAssistant) -> None:
    """Diagnostics still work when `runtime_data` is not there.

    Diagnostics can be downloaded for an entry that failed to set up; the
    old implementation raised `AttributeError` in that case.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        unique_id=MEDIA_PLAYER,
        data={CONF_MEDIA_PLAYER: MEDIA_PLAYER},
        options={
            CONF_TTS_ENTITY: "tts.demo",
            CONF_RESTORE_VOLUME: True,
            CONF_VOLUME: 0.5,
            CONF_DENY_DOMAINS: ["alarm_control_panel", "lock"],
        },
    )
    entry.add_to_hass(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["loaded"] is False
    assert diagnostics["speaker_config"] is None
    assert diagnostics["service_name"] is None
    assert diagnostics["last_announcement"] is None
    assert diagnostics["data"] == {CONF_MEDIA_PLAYER: MEDIA_PLAYER}
