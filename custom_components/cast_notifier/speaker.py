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
import datetime as dt
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

import voluptuous as vol
from homeassistant.components.media_player.const import (
    ATTR_APP_ID,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_VOLUME_SET as MP_SERVICE_VOLUME_SET,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    ANNOUNCEMENT_START_TIMEOUT,
    ATTR_LANGUAGE as DATA_LANGUAGE,
    ATTR_PRIORITY as DATA_PRIORITY,
    ATTR_SOURCE_ENTITY,
    ATTR_TTS_ENTITY as DATA_TTS_ENTITY,
    ATTR_VOICE as DATA_VOICE,
    ATTR_VOLUME as DATA_VOLUME,
    DOMAIN,
    PLAYBACK_TIMEOUT,
    PRIORITY_CRITICAL,
)

_LOGGER = logging.getLogger(__name__)

TTS_DOMAIN = "tts"
SERVICE_SPEAK = "speak"

ATTR_MEDIA_PLAYER_ENTITY_ID = "media_player_entity_id"
ATTR_MESSAGE = "message"
ATTR_CACHE = "cache"
ATTR_LANGUAGE = "language"
ATTR_OPTIONS = "options"


def _entity_id_in_domain(domain: str) -> Callable[[Any], str]:
    """Return a validator for an entity id belonging to `domain`."""

    def validate(value: Any) -> str:
        entity_id: str = cv.entity_id(value)
        if entity_id.split(".", 1)[0] != domain:
            raise vol.Invalid(f"Expected an entity of domain {domain!r}, got {value!r}")
        return entity_id

    return validate


# The `data` payload accepted by `notify.cast_<name>`. Validated before
# anything is read out of it, so a malformed automation gets a clear
# `CastNotifierInvalidData` (a `HomeAssistantError`) instead of a
# `TypeError`/`AttributeError` deep inside the speaking path. Unknown keys
# are allowed through: they are simply ignored, so a payload shared with
# another notifier does not break Cast Notifier.
SPEAK_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SOURCE_ENTITY): cv.entity_id,
        vol.Optional(DATA_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(DATA_TTS_ENTITY): _entity_id_in_domain(TTS_DOMAIN),
        vol.Optional(DATA_LANGUAGE): cv.string,
        vol.Optional(DATA_VOICE): vol.Any(cv.string, dict),
        vol.Optional(DATA_PRIORITY): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)


# Both of these are `ServiceValidationError`s, not bare
# `HomeAssistantError`s: the caller asked for something this integration
# refuses to do, which is a problem with the call, not a failure inside it.
# Home Assistant renders a `ServiceValidationError` as its translated
# message without a traceback, and per ADR-015 of the suite both reach the
# caller instead of being swallowed -- see docs/ARCHITECTURE.md.


class CastNotifierRefused(ServiceValidationError):
    """Raised when a message is refused by the `deny_domains` rule."""


class CastNotifierInvalidData(ServiceValidationError):
    """Raised when a call's `data` payload does not match the contract."""


class CastNotifierQuietHours(ServiceValidationError):
    """Raised when a message falls inside quiet hours with no quiet volume."""


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
    # Quiet hours. Both bounds are `HH:MM:SS` strings straight out of the
    # options flow's `TimeSelector`, or `None` when the feature is off.
    quiet_start: str | None = None
    quiet_end: str | None = None
    quiet_volume: float | None = None


def is_within_quiet_hours(start: str | None, end: str | None, now: dt.time) -> bool:
    """Return whether `now` falls inside the `[start, end)` quiet window.

    The window is half-open, so an end of `07:00:00` means "quiet until
    07:00, speaking from 07:00". It may cross midnight (`22:00` ->
    `07:00`), which is the normal case for a night window, and is then
    read as "at or after start, or before end".

    Quiet hours are off -- this returns `False` -- whenever the window
    cannot be read as a real interval: either bound missing (a
    half-configured entry, which the options flow refuses in the first
    place), either bound unparseable, or the two bounds equal. A
    zero-length window is deliberately *not* read as "always quiet": a
    user who sets the same time twice has said nothing, and silently
    muting a notifier for 24 hours is the worst possible reading of that.
    """
    if not start or not end:
        return False
    parsed_start = dt_util.parse_time(start)
    parsed_end = dt_util.parse_time(end)
    if parsed_start is None or parsed_end is None:
        return False
    if parsed_start == parsed_end:
        return False
    if parsed_start < parsed_end:
        return parsed_start <= now < parsed_end
    return now >= parsed_start or now < parsed_end


def _build_options(voice: str | dict[str, Any] | None) -> dict[str, Any] | None:
    """Build the `options` dict passed to `tts.speak`.

    A plain string is a convenience for the common case (a voice name); a
    JSON object string, or a real mapping in a `data.voice` payload, lets a
    call fully control the options dict passed to `tts.speak`, e.g.
    `{"voice": "fr-FR-Standard-A", "gender": "female"}`.
    """
    if not voice:
        return None
    if isinstance(voice, dict):
        return dict(voice)
    try:
        parsed = json.loads(voice)
    except ValueError:
        return {"voice": voice}
    return parsed if isinstance(parsed, dict) else {"voice": voice}


# The five volume readings an announcement records, in the order they are
# taken. The first real announcement on a Nest speaker played at an
# observed 0.55 while the entry was configured for 0.40, and the log said
# nothing at all about it: these steps exist so that question has an
# answer next time, from the log at DEBUG or from the entry's diagnostics.
STEP_VOLUME_BEFORE: Final = "volume_before"
STEP_VOLUME_REQUESTED: Final = "volume_requested"
STEP_VOLUME_AFTER_SET: Final = "volume_after_set"
STEP_VOLUME_WHILE_PLAYING: Final = "volume_while_playing"
STEP_VOLUME_RESTORED: Final = "volume_restored"
# Recorded instead of `volume_restored` when `media_player.volume_set`
# refused to put the volume back: the value is what the player is left
# at, which is the number an operator needs.
STEP_VOLUME_RESTORE_FAILED: Final = "volume_restore_failed"


@dataclass(slots=True)
class AnnouncementTimeline:
    """What happened to one player's volume during one announcement.

    Bounded to a single announcement on purpose: this is a diagnostic
    aid, not a history. Each step carries a UTC timestamp as well as its
    value, because "the volume was right but the restore came 8s late" and
    "the volume was wrong" look identical without one.
    """

    player: str
    started_at: str
    steps: list[dict[str, Any]] = field(default_factory=list)

    def record(self, step: str, volume: float | None) -> None:
        """Record one reading, and log it at DEBUG."""
        self.steps.append(
            {
                "step": step,
                "at": dt_util.utcnow().isoformat(),
                "volume": volume,
            }
        )
        _LOGGER.debug("Announcement on %s: %s = %s", self.player, step, volume)

    def has(self, step: str) -> bool:
        """Return whether `step` has already been recorded."""
        return any(recorded["step"] == step for recorded in self.steps)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable view, for diagnostics."""
        return {
            "player": self.player,
            "started_at": self.started_at,
            "steps": list(self.steps),
        }


# What Cast Notifier compares to decide whether an announcement is under
# way. See `_async_wait_for_playback_end` for why `state` alone is not
# enough.
_Fingerprint = tuple[str | None, ...]


class CastSpeaker:
    """Speak messages on one Cast player via `tts.speak`.

    Shared by the legacy `notify.cast_<name>` service and the `NotifyEntity`
    defined in notify.py, so behavior never diverges between them.
    """

    def __init__(self, hass: HomeAssistant, config: CastSpeakerConfig) -> None:
        """Initialize the speaker."""
        self.hass = hass
        self.config = config
        # Announcements on one player are serialized: two overlapping calls
        # would otherwise both read "the previous volume" *after* the first
        # one already lowered it, and the second one would restore the
        # announcement volume as if it were the user's setting.
        self._lock = asyncio.Lock()
        # The volume trace of the most recent announcement, exposed through
        # diagnostics. One announcement only: see `AnnouncementTimeline`.
        self.last_announcement: AnnouncementTimeline | None = None

    async def async_speak(self, request: SpeakRequest) -> None:
        """Speak `request.message`, refusing it if a rule says not to.

        Raises `CastNotifierInvalidData` if `request.data` does not match
        `SPEAK_DATA_SCHEMA`, `CastNotifierRefused` if
        `request.data["source_entity"]` belongs to a domain in
        `deny_domains`, and `CastNotifierQuietHours` if the call lands
        inside a quiet window with no `quiet_volume` to speak it at -- the
        message is never sent to `tts.speak` in any of the three cases.
        All are `ServiceValidationError`s that reach the caller (ADR-015)
        as well as the log. All three checks run before the per-player
        lock, so a refused call never queues behind an announcement in
        progress.
        """
        data = self._validated_data(request)
        self._enforce_deny_domains(data)
        quiet_volume = self._enforce_quiet_hours(data)

        async with self._lock:
            await self._async_speak_locked(request, data, quiet_volume)

    async def _async_speak_locked(
        self,
        request: SpeakRequest,
        data: dict[str, Any],
        quiet_volume: float | None = None,
    ) -> None:
        """Speak one message; called with `self._lock` held."""
        message = self._effective_message(request)
        tts_entity = data.get(DATA_TTS_ENTITY) or self.config.tts_entity
        language = data.get(DATA_LANGUAGE) or self.config.language
        voice = data.get(DATA_VOICE) or self.config.voice
        # Volume precedence, most specific first: this call's own
        # `data.volume`, then the quiet-hours volume when the call landed
        # in the window, then the entry's configured volume. A caller that
        # names a volume has said something about *this* message, which
        # beats a rule about this time of day.
        volume: float | None = data.get(DATA_VOLUME)
        speaking_quietly = volume is None and quiet_volume is not None
        if volume is None:
            volume = quiet_volume
        if volume is None:
            volume = self.config.volume

        timeline = AnnouncementTimeline(
            player=self.config.media_player,
            started_at=dt_util.utcnow().isoformat(),
        )
        self.last_announcement = timeline

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
        # A player that cannot set its volume at all (a Cast *group*, a
        # fixed-output device) is left alone entirely: speaking matters
        # more than the volume it is spoken at.
        manage_volume = (
            volume is not None
            and not self._has_feature(MediaPlayerEntityFeature.MEDIA_ANNOUNCE)
            and self._has_feature(MediaPlayerEntityFeature.VOLUME_SET)
        )

        if speaking_quietly and not manage_volume:
            # Not a refusal: a notifier that goes mute at night because
            # the speaker has a fixed output is worse than one that is
            # too loud. But the entry asked for something that is not
            # happening, and only a warning can say so -- the volume is
            # not managed, so nothing else in this call will mention it.
            _LOGGER.warning(
                "quiet_volume could not be applied on %s: this player's volume "
                "is not managed by Cast Notifier (no volume_set support, or it "
                "handles announcements itself). Speaking at %s instead of %s",
                self.config.media_player,
                self._current_volume(),
                volume,
            )

        timeline.record(STEP_VOLUME_BEFORE, self._current_volume())

        previous_volume: float | None = None
        previous_fingerprint: _Fingerprint | None = None
        if manage_volume and volume is not None:
            timeline.record(STEP_VOLUME_REQUESTED, volume)
            previous_volume = self._current_volume()
            if await self._async_set_volume(volume):
                # Read back rather than assumed: `media_player.volume_set`
                # returning does not prove the player took the value, and
                # a discrepancy here is exactly the kind of thing this
                # timeline exists to show.
                timeline.record(STEP_VOLUME_AFTER_SET, self._current_volume())
                previous_fingerprint = self._fingerprint()
            else:
                # The volume could not be changed; there is nothing to
                # restore and nothing to wait for, so just speak.
                manage_volume = False

        # Whatever happens to `tts.speak` -- an unavailable TTS entity, a
        # dead player, a cancelled call -- the volume this integration
        # changed is put back. Leaving a speaker permanently quiet (or
        # permanently loud) because a message failed is the worst possible
        # failure mode for a notifier.
        spoke = False
        unsub_observer = self._async_observe_playing_volume(timeline)
        try:
            await self._async_call_speak(tts_entity, message, language, voice)
            spoke = True
        finally:
            try:
                if manage_volume:
                    if spoke and previous_fingerprint is not None:
                        await self._async_wait_for_playback_end(previous_fingerprint)
                    if self.config.restore_volume and previous_volume is not None:
                        # Two distinct steps, never both: a restore that
                        # did not happen used to read the volume back and
                        # record it as `volume_restored`, which is exactly
                        # the reading someone would trust when asking why
                        # a speaker is stuck at announcement volume.
                        step = (
                            STEP_VOLUME_RESTORED
                            if await self._async_set_volume(previous_volume)
                            else STEP_VOLUME_RESTORE_FAILED
                        )
                        timeline.record(step, self._current_volume())
            finally:
                unsub_observer()

    def _validated_data(self, request: SpeakRequest) -> dict[str, Any]:
        try:
            validated: dict[str, Any] = SPEAK_DATA_SCHEMA(request.data)
        except vol.Invalid as err:
            # Logged *and* raised: the caller must know nothing was spoken
            # (ADR-015), and a call made without `blocking: true` -- core
            # `alert` does exactly that -- would otherwise leave no trace
            # an operator can find.
            _LOGGER.warning(
                "Refusing a message on %s: invalid `data` payload: %s",
                self.config.media_player,
                err,
            )
            raise CastNotifierInvalidData(
                translation_domain=DOMAIN,
                translation_key="invalid_data",
                translation_placeholders={
                    "player": self.config.media_player,
                    "error": str(err),
                },
            ) from err
        return validated

    def _denied_domains(self) -> set[str]:
        """Return `deny_domains` folded for case-insensitive comparison."""
        return {domain.casefold() for domain in self.config.deny_domains}

    def _enforce_deny_domains(self, data: dict[str, Any]) -> None:
        source_entity = data.get(ATTR_SOURCE_ENTITY)
        if source_entity is None:
            return
        domain = source_entity.split(".", 1)[0].casefold()
        if domain in self._denied_domains():
            # Logged *and* raised (ADR-015): the log line carries the full
            # context for an operator, the exception tells the caller that
            # nothing was spoken. A refusal that only logged would be an
            # HTTP 200 for a message nobody ever heard.
            _LOGGER.warning(
                "Refusing to speak a message about %s on %s: domain %r is "
                "in deny_domains %s",
                source_entity,
                self.config.media_player,
                domain,
                self.config.deny_domains,
            )
            raise CastNotifierRefused(
                translation_domain=DOMAIN,
                translation_key="message_refused",
                translation_placeholders={
                    "domain": domain,
                    "source_entity": source_entity,
                    "player": self.config.media_player,
                },
            )

    def _enforce_quiet_hours(self, data: dict[str, Any]) -> float | None:
        """Apply the quiet window, returning the volume to speak at.

        Returns `None` when quiet hours do not apply (not configured, out
        of the window, or bypassed), and the configured `quiet_volume`
        when the call lands inside the window and there is one. Raises
        `CastNotifierQuietHours` when the call lands inside the window
        with no `quiet_volume`: the entry has said "no announcements at
        this hour", and per ADR-015 that is an error for the caller, not
        a silent success.

        `data.priority: "critical"` bypasses the window entirely, volume
        included. A water leak at 3am is what a notifier is for, and
        `priority` is the key the wider notification layer forwards
        untouched, so a critical alert stays critical all the way down.
        """
        priority = data.get(DATA_PRIORITY)
        if isinstance(priority, str) and priority.casefold() == PRIORITY_CRITICAL:
            return None

        now = dt_util.now().time()
        if not is_within_quiet_hours(
            self.config.quiet_start, self.config.quiet_end, now
        ):
            return None

        if self.config.quiet_volume is not None:
            return self.config.quiet_volume

        # INFO, not WARNING: this is the configuration doing its job, not
        # something going wrong. It is still logged, because a caller
        # without `blocking: true` would otherwise have no trace at all of
        # a message that was never spoken (ADR-015).
        _LOGGER.info(
            "Not speaking on %s: reason quiet_hours, window %s-%s, local time %s",
            self.config.media_player,
            self.config.quiet_start,
            self.config.quiet_end,
            now.isoformat(),
        )
        raise CastNotifierQuietHours(
            translation_domain=DOMAIN,
            translation_key="quiet_hours",
            translation_placeholders={
                "player": self.config.media_player,
                "start": str(self.config.quiet_start),
                "end": str(self.config.quiet_end),
            },
        )

    @callback
    def _async_observe_playing_volume(
        self, timeline: AnnouncementTimeline
    ) -> Callable[[], None]:
        """Record the volume the player reports while it is `playing`.

        This is the only reading that says what the announcement was
        actually heard at. `volume_set` returning, and the state attribute
        just after it, both describe what Home Assistant asked for; a Cast
        device can report something else once it starts playing (0.55 for
        a requested 0.40, on the first real announcement). Only the first
        `playing` observation is kept, so a long clip cannot grow the
        timeline.
        """

        @callback
        def _on_state_change(event: Event[EventStateChangedData]) -> None:
            new_state = event.data["new_state"]
            if new_state is None or new_state.state != MediaPlayerState.PLAYING:
                return
            if timeline.has(STEP_VOLUME_WHILE_PLAYING):
                return
            volume = new_state.attributes.get(ATTR_MEDIA_VOLUME_LEVEL)
            timeline.record(
                STEP_VOLUME_WHILE_PLAYING,
                float(volume) if volume is not None else None,
            )

        return async_track_state_change_event(
            self.hass, [self.config.media_player], _on_state_change
        )

    def _effective_message(self, request: SpeakRequest) -> str:
        prefix = self.config.announce_prefix
        return f"{prefix} {request.message}" if prefix else request.message

    async def _async_call_speak(
        self,
        tts_entity: str,
        message: str,
        language: str | None,
        voice: str | dict[str, Any] | None,
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

    def _has_feature(self, feature: MediaPlayerEntityFeature) -> bool:
        """Return whether the target player advertises `feature`.

        A player with no state, or one that advertises no features at all,
        counts as not supporting anything: Cast Notifier then does the
        least intrusive thing (speak, touch nothing else).
        """
        state = self.hass.states.get(self.config.media_player)
        if state is None:
            return False
        supported = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        return bool(supported & feature)

    def _fingerprint(self) -> _Fingerprint:
        """Snapshot what the player is doing right now.

        `state` alone cannot tell a TTS clip apart from the music that was
        already playing (both are `playing`), so the media identity is
        folded in: `media_content_id` changes for every TTS clip, and
        `app_id`/`media_title` change on most Cast transitions too.
        """
        state = self.hass.states.get(self.config.media_player)
        if state is None:
            return (None, None, None, None)
        attributes = state.attributes
        return (
            state.state,
            attributes.get(ATTR_MEDIA_CONTENT_ID),
            attributes.get(ATTR_APP_ID),
            attributes.get(ATTR_MEDIA_TITLE),
        )

    def _current_volume(self) -> float | None:
        state = self.hass.states.get(self.config.media_player)
        if state is None:
            return None
        volume = state.attributes.get(ATTR_MEDIA_VOLUME_LEVEL)
        return float(volume) if volume is not None else None

    async def _async_set_volume(self, volume: float) -> bool:
        """Set the player's volume, returning whether it worked.

        Volume management is a convenience, never a precondition: a player
        that refuses `volume_set` (a Cast group, a device with fixed
        output, a transient failure) must still speak, so the failure is
        logged and reported rather than raised.
        """
        try:
            await self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                MP_SERVICE_VOLUME_SET,
                {
                    ATTR_ENTITY_ID: self.config.media_player,
                    ATTR_MEDIA_VOLUME_LEVEL: volume,
                },
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Could not set the volume of %s to %s: %s",
                self.config.media_player,
                volume,
                err,
            )
            return False
        return True

    async def _async_wait_for_playback_end(
        self, previous_fingerprint: _Fingerprint
    ) -> None:
        """Wait for the player to report it is done, or time out.

        `tts.speak` returns as soon as `media_player.play_media` returns,
        which for Cast is as soon as playback *starts* (see module
        docstring): it never waits for the announcement to finish. Cast
        Notifier tracks state changes itself instead, in two bounded
        phases:

        1. wait for the player to stop looking like `previous_fingerprint`
           (the announcement started), capped at
           `ANNOUNCEMENT_START_TIMEOUT`. This phase is deliberately short:
           when the player was already playing music, the change may be
           invisible from Home Assistant, and there is no point holding the
           caller for the full timeout to find that out.
        2. wait for it to look like `previous_fingerprint` again (the
           announcement ended), capped by the remaining part of
           `PLAYBACK_TIMEOUT`.

        Both phases share one overall `PLAYBACK_TIMEOUT` deadline so a
        player that never settles back cannot block a caller forever. If
        phase 1 times out, phase 2 returns immediately -- the player never
        visibly left its previous state, so it is already "back".
        """
        deadline = time.monotonic() + PLAYBACK_TIMEOUT
        start_deadline = min(deadline, time.monotonic() + ANNOUNCEMENT_START_TIMEOUT)
        await self._async_wait_until(
            lambda fingerprint: fingerprint != previous_fingerprint, start_deadline
        )
        await self._async_wait_until(
            lambda fingerprint: fingerprint == previous_fingerprint, deadline
        )

    async def _async_wait_until(
        self, predicate: Callable[[_Fingerprint], bool], deadline: float
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if predicate(self._fingerprint()):
            return

        done = asyncio.Event()

        @callback
        def _on_state_change(event: Event[EventStateChangedData]) -> None:
            new_state = event.data["new_state"]
            if new_state is None:
                return
            attributes = new_state.attributes
            fingerprint: _Fingerprint = (
                new_state.state,
                attributes.get(ATTR_MEDIA_CONTENT_ID),
                attributes.get(ATTR_APP_ID),
                attributes.get(ATTR_MEDIA_TITLE),
            )
            if predicate(fingerprint):
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
