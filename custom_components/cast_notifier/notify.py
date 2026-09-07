"""Notify platform for Cast Notifier.

Exposes the same `CastSpeaker` on the two surfaces `alert.notifiers:` and
modern automations expect:

- `async_get_service` registers the legacy `notify.cast_<name>` service
  (see __init__.py, which discovers this platform with the service name
  already resolved);
- `async_setup_entry` registers a `NotifyEntity` for the same config entry.

Both simply build a `SpeakRequest` from the call's `message`/`data` and hand
it to the shared `CastSpeaker`. Nothing is swallowed here: per ADR-015 of
the suite, a call that did not speak fails. A `deny_domains` refusal and a
malformed `data` payload both reach the caller as a translated
`ServiceValidationError` (and are logged as warnings by `CastSpeaker` on
the way out); an unavailable TTS engine or a dead player reaches it as a
`HomeAssistantError`. An automation that believes it spoke when it did not
is worse than a red error in its trace.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.components.notify.const import ATTR_DATA
from homeassistant.components.notify.legacy import BaseNotificationService
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import CastNotifierConfigEntry, CastNotifierRuntimeData
from .const import DOMAIN
from .speaker import CastSpeaker, SpeakRequest

_LOGGER = logging.getLogger(__name__)

# Nothing here polls, and speaking is already serialized per player by the
# `CastSpeaker`'s own lock, so Home Assistant does not need to serialize
# entity calls on top of that.
PARALLEL_UPDATES = 0

# The entity's translation key. It carries no name -- see
# `CastNotifyEntity` -- and exists so `icons.json` can give the entity its
# icon rather than hard-coding one with `_attr_icon`.
ENTITY_TRANSLATION_KEY: Final = "announcement"


async def _async_speak(speaker: CastSpeaker, request: SpeakRequest) -> None:
    """Speak a request, raising anything that stopped it from being spoken.

    Every failure -- including a `deny_domains` refusal, which `CastSpeaker`
    has already logged as a warning -- is raised to the caller as a
    `HomeAssistantError` after `CastSpeaker` has restored any volume it
    changed. Swallowing a refusal would answer an automation with a silent
    HTTP 200 for a message nobody ever heard (ADR-015).
    """
    try:
        await speaker.async_speak(request)
    except HomeAssistantError:
        # Already a clear, caller-facing error: a `CastNotifierRefused` or
        # `CastNotifierInvalidData` (both translated
        # `ServiceValidationError`s), or a failing `tts.speak`. Let it
        # through untouched.
        raise
    except Exception as err:
        raise HomeAssistantError(
            f"Cast Notifier could not speak on {speaker.config.media_player}: {err}"
        ) from err


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> BaseNotificationService | None:
    """Set up the legacy `notify.cast_<name>` service.

    `discovery_info` is populated by __init__.py's `async_setup_entry` with
    the `entry_id` of the config entry it was discovered for.
    """
    if discovery_info is None or "entry_id" not in discovery_info:
        _LOGGER.error("Cast Notifier can only be set up through the UI")
        return None

    entry = hass.config_entries.async_get_entry(discovery_info["entry_id"])
    if entry is None or not hasattr(entry, "runtime_data"):
        _LOGGER.error("Cast Notifier config entry is not loaded")
        return None

    runtime_data: CastNotifierRuntimeData = entry.runtime_data
    if runtime_data.unloaded:
        # The entry unloaded while this discovery was still in flight.
        # Registering now would put back the `notify.cast_<name>` the
        # unload callback just retracted, bound to a dead speaker.
        _LOGGER.debug("Cast Notifier entry unloaded before discovery completed")
        return None

    service = CastNotificationService(hass, runtime_data)
    # Remembered so unloading the entry can drop this instance from
    # `notify.legacy`'s registry along with the service itself; core never
    # does that for a config entry (see __init__.py).
    runtime_data.legacy_service = service
    return service


class CastNotificationService(BaseNotificationService):
    """Legacy notify service that speaks via the `CastSpeaker`."""

    def __init__(
        self, hass: HomeAssistant, runtime_data: CastNotifierRuntimeData
    ) -> None:
        """Initialize the service."""
        self.hass = hass
        self._runtime_data = runtime_data
        self._speaker = runtime_data.speaker

    async def async_register_services(self) -> None:
        """Register `notify.cast_<name>`, unless the entry has unloaded.

        This platform is set up from a task Home Assistant owns -- the
        discovery dispatcher runs its listener as a task of its own
        (`helpers/dispatcher.py::async_dispatcher_send_internal`) -- so an
        entry can unload between `async_get_service` returning this
        instance and this method being reached. The unload callback in
        __init__.py has then already looked for a service that did not
        exist yet, and registering now would leave a `notify.cast_<name>`
        bound to a dead speaker with nothing left to retract it.

        `CastNotifierRuntimeData.unloaded` is set synchronously by that
        callback, and core's `async_register_services` does not await
        before registering, so this check cannot itself be raced.
        """
        if self._runtime_data.unloaded:
            _LOGGER.debug("Cast Notifier entry unloaded before its service registered")
            return
        await super().async_register_services()

    async def async_send_message(self, message: str, **kwargs: Any) -> None:
        """Speak `message` on the configured Cast player.

        `target` and `title`, if passed, are ignored: Cast Notifier always
        speaks on its one configured player, and a spoken message has no
        separate title.
        """
        request = SpeakRequest(message=message, data=dict(kwargs.get(ATTR_DATA) or {}))
        await _async_speak(self._speaker, request)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CastNotifierConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Cast Notifier entity for a config entry."""
    async_add_entities([CastNotifyEntity(entry)])


class CastNotifyEntity(NotifyEntity):
    """Modern notify entity that speaks via the `CastSpeaker`.

    One service device per config entry, named after the entry (i.e. after
    the Cast player it speaks on), and `_attr_name = None` so the entity
    takes that device's name. Without this, every entry produced the same
    `notify.cast_notifier` friendly name and collided on entity id.

    The `_attr_translation_key` exists for `icons.json` alone, never for a
    name. `Entity._name_internal` (`homeassistant/helpers/entity.py`)
    starts with `if hasattr(self, "_attr_name"): return self._attr_name`,
    so a class that sets `_attr_name = None` never reaches the translation
    lookup at all: the device name still wins, and two entries stay
    distinguishable (the collision above). `strings.json` deliberately has
    no `entity` section, so there is no name to find either way.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = ENTITY_TRANSLATION_KEY
    _attr_supported_features = NotifyEntityFeature(0)

    def __init__(self, entry: CastNotifierConfigEntry) -> None:
        """Initialize the entity."""
        self._attr_unique_id = f"{entry.entry_id}_notify_entity"
        self._speaker: CastSpeaker = entry.runtime_data.speaker
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Cast Notifier",
            name=entry.title or self._speaker.config.media_player,
        )

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Speak `message` on the configured Cast player.

        Overrides the base class instead of `send_message` so no executor
        job is scheduled: speaking is pure async I/O against Home
        Assistant's own service bus. `title` is accepted for API parity
        with `NotifyEntity` but is not spoken.

        There is no `data` payload on this surface (`NotifyEntityFeature`
        does not expose one), so `deny_domains` can only be enforced here
        via the legacy service; automations that need it should call
        `notify.cast_<name>` with `data.source_entity` instead.
        """
        del title
        await _async_speak(self._speaker, SpeakRequest(message=message))
