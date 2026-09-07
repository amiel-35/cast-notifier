"""Tests for the Cast Notifier config and options flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.cast_notifier.const import (
    CONF_DENY_DOMAINS,
    CONF_LANGUAGE,
    CONF_MEDIA_PLAYER,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_QUIET_VOLUME,
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


async def test_player_without_play_media_is_refused(hass: HomeAssistant) -> None:
    """A media_player that cannot play media cannot speak, so it is refused.

    The dev instance configured a demo player advertising everything but
    `PLAY_MEDIA`: the entry was created happily and every announcement
    failed with `ServiceNotSupported`.
    """
    hass.states.async_set(
        "media_player.kitchen",
        "idle",
        {
            "friendly_name": "Kitchen",
            "supported_features": int(
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.TURN_ON
                | MediaPlayerEntityFeature.TURN_OFF
            ),
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_MEDIA_PLAYER: "player_cannot_play_media"}
    assert not hass.config_entries.async_entries(DOMAIN)

    # The form is usable again: picking a player that *can* play media
    # from the same flow creates the entry.
    hass.states.async_set(
        "media_player.living_room",
        "idle",
        {
            "friendly_name": "Living room",
            "supported_features": int(MediaPlayerEntityFeature.PLAY_MEDIA),
        },
    )
    retried = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**USER_INPUT, CONF_MEDIA_PLAYER: "media_player.living_room"},
    )

    assert retried["type"] is FlowResultType.CREATE_ENTRY
    assert retried["title"] == "Living room"


async def test_player_with_play_media_is_accepted(hass: HomeAssistant) -> None:
    """A player advertising PLAY_MEDIA passes the check, titled after it."""
    hass.states.async_set(
        "media_player.kitchen",
        "idle",
        {
            "friendly_name": "Kitchen speaker",
            "supported_features": int(
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.VOLUME_SET
            ),
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen speaker"


async def test_player_without_feature_information_is_accepted(
    hass: HomeAssistant,
) -> None:
    """A player that advertises no features at all is not refused.

    The check is permissive where it cannot know: refusing on an absent
    attribute would block template players and entities seen for the
    first time.
    """
    hass.states.async_set("media_player.kitchen", "idle")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_selector_is_filtered_to_cast_when_a_cast_player_exists(
    hass: HomeAssistant,
) -> None:
    """The media_player picker narrows to the `cast` integration when it can."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "media_player", "cast", "kitchen-uuid", suggested_object_id="kitchen"
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    schema = result["data_schema"].schema
    media_player_selector = schema[CONF_MEDIA_PLAYER]
    assert media_player_selector.config["integration"] == "cast"


async def test_quiet_hours_are_stored_by_the_user_step(hass: HomeAssistant) -> None:
    """A complete quiet window is saved with the rest of the options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **USER_INPUT,
            CONF_QUIET_START: "22:00:00",
            CONF_QUIET_END: "07:00:00",
            CONF_QUIET_VOLUME: 0.1,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_QUIET_START] == "22:00:00"
    assert result["options"][CONF_QUIET_END] == "07:00:00"
    assert result["options"][CONF_QUIET_VOLUME] == 0.1


async def test_quiet_hours_default_to_off(hass: HomeAssistant) -> None:
    """Leaving both times empty stores nothing, which means "off"."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["options"][CONF_QUIET_START] is None
    assert result["options"][CONF_QUIET_END] is None
    assert result["options"][CONF_QUIET_VOLUME] is None


async def test_half_a_quiet_window_is_refused_by_the_user_step(
    hass: HomeAssistant,
) -> None:
    """One bound without the other would be stored and silently ignored.

    `is_within_quiet_hours` reads a half-configured window as "off", so
    the form is the last place the user can still see what they meant.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_QUIET_START: "22:00:00"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_QUIET_END: "quiet_hours_incomplete"}
    assert not hass.config_entries.async_entries(DOMAIN)

    # And the same the other way round.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_QUIET_END: "07:00:00"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_QUIET_START: "quiet_hours_incomplete"}


async def test_half_a_quiet_window_is_refused_by_the_options_flow(
    hass: HomeAssistant,
) -> None:
    """The options flow enforces the same rule, and re-shows the form."""
    init_result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    created = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], USER_INPUT
    )
    entry = hass.config_entries.async_get_entry(created["result"].entry_id)
    assert entry is not None

    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    updated_input = {**USER_INPUT, CONF_QUIET_START: "22:00:00"}
    del updated_input[CONF_MEDIA_PLAYER]

    refused = await hass.config_entries.options.async_configure(
        options_result["flow_id"], updated_input
    )

    assert refused["type"] is FlowResultType.FORM
    assert refused["errors"] == {CONF_QUIET_END: "quiet_hours_incomplete"}
    assert entry.options[CONF_QUIET_START] is None

    # The window is accepted once it is complete.
    accepted = await hass.config_entries.options.async_configure(
        refused["flow_id"], {**updated_input, CONF_QUIET_END: "07:00:00"}
    )
    await hass.async_block_till_done()

    assert accepted["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_QUIET_END] == "07:00:00"


async def test_the_options_form_prefills_the_configured_quiet_hours(
    hass: HomeAssistant,
) -> None:
    """A `TimeSelector` cannot carry a schema default, so it is suggested.

    `selector.TimeSelector` validates with `cv.time`, which refuses the
    empty string, so an unset quiet window cannot be expressed as a
    `default=""`. The current value is offered as a suggested value
    instead (`data_entry_flow.py::add_suggested_values_to_schema`), and
    this checks the form really does come back filled in.
    """
    init_result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    created = await hass.config_entries.flow.async_configure(
        init_result["flow_id"],
        {**USER_INPUT, CONF_QUIET_START: "22:00:00", CONF_QUIET_END: "07:00:00"},
    )
    entry = hass.config_entries.async_get_entry(created["result"].entry_id)
    assert entry is not None

    options_result = await hass.config_entries.options.async_init(entry.entry_id)

    suggested = {
        marker.schema: marker.description["suggested_value"]
        for marker in options_result["data_schema"].schema
        if marker.description and "suggested_value" in marker.description
    }
    assert suggested[CONF_QUIET_START] == "22:00:00"
    assert suggested[CONF_QUIET_END] == "07:00:00"
