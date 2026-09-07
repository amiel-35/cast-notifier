"""Config flow for Cast Notifier.

One config entry = one Cast player. The user step picks the `media_player`
(this also sets the entry's unique ID, so the same player cannot be
configured twice, and refuses a player that cannot play media at all --
see `_supports_play_media`) and the `tts_entity` to speak with; everything else
(language, voice, volume, `deny_domains`, ...) has a sensible default and
can be changed later through the options flow, which reuses the same
schema minus `media_player`.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_ANNOUNCE_PREFIX,
    CONF_DENY_DOMAINS,
    CONF_LANGUAGE,
    CONF_MEDIA_PLAYER,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_QUIET_VOLUME,
    CONF_RESTORE_VOLUME,
    CONF_TTS_ENTITY,
    CONF_VOICE,
    CONF_VOLUME,
    DEFAULT_DENY_DOMAINS,
    DEFAULT_RESTORE_VOLUME,
    DOMAIN,
)

CAST_PLATFORM = "cast"
MEDIA_PLAYER_DOMAIN = "media_player"
TTS_DOMAIN = "tts"

# Options the form leaves out entirely when they are empty, rather than
# submitting an empty string: a `TimeSelector` validates its value with
# `cv.time` (`homeassistant/helpers/selector.py`), which refuses `""`, so
# these cannot carry a `default` in the schema and are prefilled with
# suggested values instead.
SUGGESTED_ONLY_OPTIONS = (CONF_QUIET_START, CONF_QUIET_END)


def _deny_domains_to_string(domains: list[str]) -> str:
    """Render a list of domains as a comma-separated string."""
    return ", ".join(domains)


def _string_to_deny_domains(value: str) -> list[str]:
    """Parse a comma-separated string into a list of domains.

    Domains are case-folded here so `Lock` and `lock` are the same rule;
    `CastSpeaker` folds again when comparing, so an entry stored before
    this normalization still behaves.
    """
    return [part.strip().casefold() for part in value.split(",") if part.strip()]


def _has_cast_media_players(hass: HomeAssistant) -> bool:
    """Return whether any `media_player` entity belongs to the `cast` platform."""
    registry = er.async_get(hass)
    return any(
        entry.domain == MEDIA_PLAYER_DOMAIN and entry.platform == CAST_PLATFORM
        for entry in registry.entities.values()
    )


def _supports_play_media(state: State) -> bool:
    """Return whether a media_player state advertises `PLAY_MEDIA`.

    Everything Cast Notifier does ends in `media_player.play_media`:
    `tts.speak` calls it (`homeassistant/components/tts/entity.py`,
    `TextToSpeechEntity.async_speak`), and Home Assistant refuses the call
    with `ServiceNotSupported` when the target does not advertise
    `MediaPlayerEntityFeature.PLAY_MEDIA`
    (`homeassistant/components/media_player/const.py`). Such a player can
    never speak, so picking one is worth catching in the form rather than
    in the logs of the first announcement that matters.

    Permissive when the information is missing: a player with no
    `supported_features` attribute at all (never seen, a stub, a template
    entity) is accepted rather than refused on the strength of an absent
    attribute.
    """
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES)
    if not isinstance(features, int):
        return True
    return bool(features & MediaPlayerEntityFeature.PLAY_MEDIA)


def _media_player_selector(hass: HomeAssistant) -> selector.EntitySelector:
    """Build the media_player selector, filtered to `cast` when possible."""
    config: selector.EntitySelectorConfig = {"domain": MEDIA_PLAYER_DOMAIN}
    if _has_cast_media_players(hass):
        config["integration"] = CAST_PLATFORM
    return selector.EntitySelector(config)


def _tts_entity_key(defaults: dict[str, Any]) -> vol.Marker:
    """Build the `tts_entity` schema key.

    A `vol.Required` with `default=None` is not a required field at all --
    voluptuous fills the `None` in and the form validates empty -- so the
    default is only attached when there actually is one to prefill (the
    options flow re-showing the current engine).
    """
    if (current := defaults.get(CONF_TTS_ENTITY)) is not None:
        return vol.Required(CONF_TTS_ENTITY, default=current)
    return vol.Required(CONF_TTS_ENTITY)


def _options_schema(hass: HomeAssistant, defaults: dict[str, Any]) -> vol.Schema:
    """Build the schema shared by the user step and the options flow."""
    return vol.Schema(
        {
            _tts_entity_key(defaults): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=TTS_DOMAIN)
            ),
            vol.Optional(
                CONF_LANGUAGE, default=defaults.get(CONF_LANGUAGE, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_VOICE, default=defaults.get(CONF_VOICE, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_VOLUME, default=defaults.get(CONF_VOLUME)
            ): _volume_slider(),
            vol.Required(
                CONF_RESTORE_VOLUME,
                default=defaults.get(CONF_RESTORE_VOLUME, DEFAULT_RESTORE_VOLUME),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_ANNOUNCE_PREFIX, default=defaults.get(CONF_ANNOUNCE_PREFIX, "")
            ): selector.TextSelector(),
            vol.Required(
                CONF_DENY_DOMAINS,
                default=_deny_domains_to_string(
                    defaults.get(CONF_DENY_DOMAINS, DEFAULT_DENY_DOMAINS)
                ),
            ): selector.TextSelector(),
            vol.Optional(CONF_QUIET_START): selector.TimeSelector(),
            vol.Optional(CONF_QUIET_END): selector.TimeSelector(),
            vol.Optional(
                CONF_QUIET_VOLUME, default=defaults.get(CONF_QUIET_VOLUME)
            ): _volume_slider(),
        }
    )


def _volume_slider() -> vol.Any:
    """Build the 0-1 slider shared by `volume` and `quiet_volume`.

    Wrapped in `vol.Any(None, ...)` so that "no volume at all" -- the
    default, meaning "never touch the player's volume" -- is a value the
    schema accepts, and not just a missing key.
    """
    return vol.Any(
        None,
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=1,
                step=0.01,
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
    )


def _suggested_values(defaults: dict[str, Any]) -> dict[str, Any]:
    """Return the current values of the options prefilled by suggestion."""
    return {
        key: value
        for key in SUGGESTED_ONLY_OPTIONS
        if (value := defaults.get(key)) is not None
    }


def _validate_options(user_input: dict[str, Any]) -> dict[str, str]:
    """Return the form errors in a submitted set of options.

    Quiet hours are the only thing that can be wrong here: an interval
    needs both of its bounds, and half of one would otherwise be stored
    and silently ignored for good (`is_within_quiet_hours` in
    `speaker.py` treats it as "off"). Refusing it in the form is the only
    place the user can still see what they meant to configure.
    """
    start = user_input.get(CONF_QUIET_START)
    end = user_input.get(CONF_QUIET_END)
    if bool(start) is bool(end):
        return {}
    return {CONF_QUIET_START if not start else CONF_QUIET_END: "quiet_hours_incomplete"}


def _parse_options(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn raw form input into the options dict stored on the entry."""
    return {
        CONF_TTS_ENTITY: user_input[CONF_TTS_ENTITY],
        CONF_LANGUAGE: user_input.get(CONF_LANGUAGE) or None,
        CONF_VOICE: user_input.get(CONF_VOICE) or None,
        CONF_VOLUME: user_input.get(CONF_VOLUME),
        CONF_RESTORE_VOLUME: user_input[CONF_RESTORE_VOLUME],
        CONF_ANNOUNCE_PREFIX: user_input.get(CONF_ANNOUNCE_PREFIX) or None,
        CONF_DENY_DOMAINS: _string_to_deny_domains(
            user_input.get(CONF_DENY_DOMAINS, "")
        ),
        CONF_QUIET_START: user_input.get(CONF_QUIET_START) or None,
        CONF_QUIET_END: user_input.get(CONF_QUIET_END) or None,
        CONF_QUIET_VOLUME: user_input.get(CONF_QUIET_VOLUME),
    }


class CastNotifierConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cast Notifier."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the single setup step: pick the player and its settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            media_player = user_input[CONF_MEDIA_PLAYER]
            # Known limitation: the unique ID is the media_player *entity
            # id*, not its entity registry id. Renaming the player's entity
            # id therefore orphans the entry (it keeps pointing at the old
            # id) instead of following the rename, and re-adding the player
            # under its new id is possible. The registry id would fix that,
            # but the picked entity is not guaranteed to be in the registry
            # at all (a template or YAML media_player is not), so the entity
            # id stays the identity; rename the player before configuring
            # it, or delete and re-add the entry afterwards.
            await self.async_set_unique_id(media_player)
            self._abort_if_unique_id_configured()

            state = self.hass.states.get(media_player)
            if state is not None and not _supports_play_media(state):
                # A player that cannot play media cannot speak: `tts.speak`
                # would raise `ServiceNotSupported` on every call. Refuse it
                # here, where the user can pick another one.
                errors[CONF_MEDIA_PLAYER] = "player_cannot_play_media"
            elif option_errors := _validate_options(user_input):
                errors.update(option_errors)
            else:
                title = state.name if state is not None else media_player

                return self.async_create_entry(
                    title=title,
                    data={CONF_MEDIA_PLAYER: media_player},
                    options=_parse_options(user_input),
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_MEDIA_PLAYER): _media_player_selector(self.hass),
            }
        ).extend(_options_schema(self.hass, {}).schema)
        if user_input is not None:
            # Re-showing the form after an error: keep what was typed.
            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> CastNotifierOptionsFlow:
        """Return the options flow for this handler."""
        return CastNotifierOptionsFlow()


class CastNotifierOptionsFlow(OptionsFlow):
    """Handle options for Cast Notifier: everything but the player itself."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the speaking settings for this entry's player."""
        errors: dict[str, str] = {}
        if user_input is not None and not (errors := _validate_options(user_input)):
            return self.async_create_entry(data=_parse_options(user_input))

        defaults = dict(self.config_entry.options)
        schema = _options_schema(self.hass, defaults)
        # Re-showing the form after an error: keep everything that was
        # typed, exactly as the user step does. The schema's own defaults
        # come from the *stored* options, so suggesting only the two
        # quiet-hours fields meant every other edit in the same
        # submission was silently rolled back to what it had been.
        schema = self.add_suggested_values_to_schema(
            schema, user_input if user_input else _suggested_values(defaults)
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
