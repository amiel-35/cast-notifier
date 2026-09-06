"""Tests for the Cast Notifier config and options flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cast_notifier.const import (
    CONF_DENY_DOMAINS,
    CONF_LANGUAGE,
    CONF_MEDIA_PLAYER,
    CONF_RESTORE_VOLUME,
    CONF_TTS_ENTITY,
    CONF_VOLUME,
    DOMAIN,
)

USER_INPUT = {
    CONF_MEDIA_PLAYER: "media_player.kitchen",
    CONF_TTS_ENTITY: "tts.home_assistant_cloud",
    CONF_LANGUAGE: "",
    "voice": "",
    # CONF_VOLUME is intentionally absent: it is optional and left empty,
    # matching a user who never touches the volume slider in the form.
    CONF_RESTORE_VOLUME: True,
    "announce_prefix": "",
    CONF_DENY_DOMAINS: "alarm_control_panel, lock",
}


async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    """The user step creates one entry per Cast player, with sane defaults."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "media_player.kitchen"
    assert result["data"] == {CONF_MEDIA_PLAYER: "media_player.kitchen"}
    assert result["options"][CONF_TTS_ENTITY] == "tts.home_assistant_cloud"
    assert result["options"][CONF_RESTORE_VOLUME] is True
    assert result["options"][CONF_VOLUME] is None
    assert result["options"][CONF_DENY_DOMAINS] == ["alarm_control_panel", "lock"]


async def test_duplicate_player_is_aborted(hass: HomeAssistant) -> None:
    """The same media_player cannot be configured twice."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    first_result = await hass.config_entries.flow.async_configure(
        first["flow_id"], USER_INPUT
    )
    assert first_result["type"] is FlowResultType.CREATE_ENTRY

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    second_result = await hass.config_entries.flow.async_configure(
        second["flow_id"], USER_INPUT
    )

    assert second_result["type"] is FlowResultType.ABORT
    assert second_result["reason"] == "already_configured"


async def test_different_player_is_allowed(hass: HomeAssistant) -> None:
    """A second entry for a different media_player is allowed."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(first["flow_id"], USER_INPUT)

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    second_input = {**USER_INPUT, CONF_MEDIA_PLAYER: "media_player.living_room"}
    second_result = await hass.config_entries.flow.async_configure(
        second["flow_id"], second_input
    )

    assert second_result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_updates_settings(hass: HomeAssistant) -> None:
    """The options flow re-shows the same schema (minus media_player) and saves it."""
    init_result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    created = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], USER_INPUT
    )
    entry = hass.config_entries.async_get_entry(created["result"].entry_id)
    assert entry is not None

    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert options_result["type"] is FlowResultType.FORM
    assert options_result["step_id"] == "init"

    updated_input = {
        **USER_INPUT,
        CONF_VOLUME: 0.4,
        CONF_DENY_DOMAINS: "alarm_control_panel, lock, person",
    }
    del updated_input[CONF_MEDIA_PLAYER]

    updated = await hass.config_entries.options.async_configure(
        options_result["flow_id"], updated_input
    )
    await hass.async_block_till_done()

    assert updated["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_VOLUME] == 0.4
    assert entry.options[CONF_DENY_DOMAINS] == [
        "alarm_control_panel",
        "lock",
        "person",
    ]
