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
            vol.Optional(CONF_VOLUME, default=defaults.get(CONF_VOLUME)): vol.Any(
                None,
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step=0.01,
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            ),
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
        }
    )


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
        if user_input is not None:
            return self.async_create_entry(data=_parse_options(user_input))

        schema = _options_schema(self.hass, dict(self.config_entry.options))
        return self.async_show_form(step_id="init", data_schema=schema)
