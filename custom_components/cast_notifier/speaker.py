"""Core speaking logic for Cast Notifier.

Both notify surfaces defined in notify.py -- the legacy `notify.cast_<name>`
service and the `NotifyEntity` -- build a `SpeakRequest` and hand it to a
`CastSpeaker`. See docs/ARCHITECTURE.md for the full contract, and for why
Cast Notifier manages volume and waits for playback itself instead of
relying on Home Assistant's native "announce" capability: Google Cast does
not support it (`homeassistant/components/cast/media_player.py`,
`supported_features` never includes `MediaPlayerEntityFeature.MEDIA_ANNOUNCE`),
and `tts.speak` never waits for an unannounced playback to finish
(`homeassistant/components/tts/entity.py::TextToSpeechEntity.async_speak`
calls `media_player.play_media` and returns once that call returns, not once
the speaker is quiet again).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.media_player.const import (
    ATTR_MEDIA_VOLUME_LEVEL,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_VOLUME_SET as MP_SERVICE_VOLUME_SET,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    ATTR_LANGUAGE as DATA_LANGUAGE,
    ATTR_SOURCE_ENTITY,
    ATTR_TTS_ENTITY as DATA_TTS_ENTITY,
    ATTR_VOICE as DATA_VOICE,
    ATTR_VOLUME as DATA_VOLUME,
    PLAYBACK_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

TTS_DOMAIN = "tts"
SERVICE_SPEAK = "speak"

ATTR_MEDIA_PLAYER_ENTITY_ID = "media_player_entity_id"
ATTR_MESSAGE = "message"
ATTR_CACHE = "cache"
ATTR_LANGUAGE = "language"
ATTR_OPTIONS = "options"


class CastNotifierRefused(HomeAssistantError):
    """Raised when a message is refused by the `deny_domains` rule."""


@dataclass(slots=True)
class SpeakRequest:
    """A single request to speak a message on the configured player."""

    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CastSpeakerConfig:
    """The settings a `CastSpeaker` acts on, read from a config entry."""

    media_player: str
    tts_entity: str
    language: str | None = None
    voice: str | None = None
    volume: float | None = None
    restore_volume: bool = True
    announce_prefix: str | None = None
    deny_domains: list[str] = field(default_factory=list)


def _build_options(voice: str | None) -> dict[str, Any] | None:
    """Build the `options` dict passed to `tts.speak`.

    A plain string is a convenience for the common case (a voice name); a
    JSON object string lets a call fully control the options dict passed to
    `tts.speak`, e.g. `{"voice": "fr-FR-Standard-A", "gender": "female"}`.
    """
    if not voice:
        return None
    try:
        parsed = json.loads(voice)
    except ValueError:
        return {"voice": voice}
    return parsed if isinstance(parsed, dict) else {"voice": voice}


class CastSpeaker:
    """Speak messages on one Cast player via `tts.speak`.

    Shared by the legacy `notify.cast_<name>` service and the `NotifyEntity`
    defined in notify.py, so behavior never diverges between them.
    """

    def __init__(self, hass: HomeAssistant, config: CastSpeakerConfig) -> None:
        """Initialize the speaker."""
        self.hass = hass
        self.config = config

    async def async_speak(self, request: SpeakRequest) -> None:
        """Speak `request.message`, refusing it if `deny_domains` applies.

        Raises `CastNotifierRefused` if `request.data["source_entity"]`
        belongs to a domain in `deny_domains` -- the message is never sent
        to `tts.speak` in that case.
        """
        self._enforce_deny_domains(request)

        message = self._effective_message(request)
        tts_entity = request.data.get(DATA_TTS_ENTITY) or self.config.tts_entity
        language = request.data.get(DATA_LANGUAGE) or self.config.language
        voice = request.data.get(DATA_VOICE) or self.config.voice
        volume: float | None = request.data.get(DATA_VOLUME)
        if volume is None:
            volume = self.config.volume

        # Only bother tracking playback at all if a volume needs restoring:
        # that is the one thing Cast Notifier cannot learn from `tts.speak`
        # itself (see module docstring). When the target natively supports
        # MediaPlayerEntityFeature.MEDIA_ANNOUNCE, its own `play_media`
        # implementation is expected to duck and wait, so no manual
        # management is needed there either. Cast never advertises that
        # support (see module docstring), so `manage_volume` is always
        # `volume is not None` against a real Cast player today; the check
        # is kept so a media_player of another platform reusing this
        # integration benefits from native ducking/resume automatically.
        manage_volume = volume is not None and not self._supports_announce()

        previous_volume: float | None = None
        previous_state: str | None = None
        if manage_volume and volume is not None:
            previous_volume = self._current_volume()
            await self._async_set_volume(volume)
            previous_state = self._current_state()

        await self._async_call_speak(tts_entity, message, language, voice)

        if manage_volume:
            if previous_state is not None:
                await self._async_wait_for_playback_end(previous_state)
            if self.config.restore_volume and previous_volume is not None:
                await self._async_set_volume(previous_volume)

    def _enforce_deny_domains(self, request: SpeakRequest) -> None:
        source_entity = request.data.get(ATTR_SOURCE_ENTITY)
        if source_entity is None:
            return
        domain = source_entity.split(".", 1)[0]
        if domain in self.config.deny_domains:
            _LOGGER.warning(
                "Refusing to speak a message about %s on %s: domain %r is "
                "in deny_domains %s",
                source_entity,
                self.config.media_player,
                domain,
                self.config.deny_domains,
            )
            raise CastNotifierRefused(
                f"Messages about entities in domain {domain!r} are never spoken"
            )

    def _effective_message(self, request: SpeakRequest) -> str:
        prefix = self.config.announce_prefix
        return f"{prefix} {request.message}" if prefix else request.message

    async def _async_call_speak(
        self,
        tts_entity: str,
        message: str,
        language: str | None,
        voice: str | None,
    ) -> None:
        service_data: dict[str, Any] = {
            ATTR_ENTITY_ID: tts_entity,
            ATTR_MEDIA_PLAYER_ENTITY_ID: self.config.media_player,
            ATTR_MESSAGE: message,
            ATTR_CACHE: True,
        }
        if language:
            service_data[ATTR_LANGUAGE] = language
        options = _build_options(voice)
        if options is not None:
            service_data[ATTR_OPTIONS] = options

        await self.hass.services.async_call(
            TTS_DOMAIN, SERVICE_SPEAK, service_data, blocking=True
        )

    def _supports_announce(self) -> bool:
        state = self.hass.states.get(self.config.media_player)
        if state is None:
            return False
        supported = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        return bool(supported & MediaPlayerEntityFeature.MEDIA_ANNOUNCE)

    def _current_state(self) -> str | None:
        state = self.hass.states.get(self.config.media_player)
        return state.state if state is not None else None

    def _current_volume(self) -> float | None:
        state = self.hass.states.get(self.config.media_player)
        if state is None:
            return None
        volume = state.attributes.get(ATTR_MEDIA_VOLUME_LEVEL)
        return float(volume) if volume is not None else None

    async def _async_set_volume(self, volume: float) -> None:
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            MP_SERVICE_VOLUME_SET,
            {
                ATTR_ENTITY_ID: self.config.media_player,
                ATTR_MEDIA_VOLUME_LEVEL: volume,
            },
            blocking=True,
        )

    async def _async_wait_for_playback_end(self, previous_state: str) -> None:
        """Wait for the player to report it is done, or time out.

        `tts.speak` returns as soon as `media_player.play_media` returns,
        which for Cast is as soon as playback *starts* (see module
        docstring): it never waits for the announcement to finish. Cast
        Notifier tracks state changes itself instead, waiting for the
        player to leave `previous_state` and then come back to it, bounded
        by `PLAYBACK_TIMEOUT` seconds in total so a player that never
        settles back (e.g. it was already playing something else) cannot
        block a call forever.
        """
        deadline = time.monotonic() + PLAYBACK_TIMEOUT
        await self._async_wait_until(lambda state: state != previous_state, deadline)
        await self._async_wait_until(lambda state: state == previous_state, deadline)

    async def _async_wait_until(self, predicate: Any, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        current = self._current_state()
        if current is not None and predicate(current):
            return

        done = asyncio.Event()

        @callback
        def _on_state_change(event: Event[EventStateChangedData]) -> None:
            new_state = event.data["new_state"]
            if new_state is not None and predicate(new_state.state):
                done.set()

        unsub = async_track_state_change_event(
            self.hass, [self.config.media_player], _on_state_change
        )
        try:
            await asyncio.wait_for(done.wait(), timeout=remaining)
        except TimeoutError:
            pass
        finally:
            unsub()
