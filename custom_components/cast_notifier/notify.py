"""Notify platform for Cast Notifier.

Exposes the same `CastSpeaker` on the two surfaces `alert.notifiers:` and
modern automations expect:

- `async_get_service` registers the legacy `notify.cast_<name>` service
  (see __init__.py, which discovers this platform with the service name
  already resolved);
- `async_setup_entry` registers a `NotifyEntity` for the same config entry.

Both simply build a `SpeakRequest` from the call's `message`/`data` and hand
it to the shared `CastSpeaker`. A refusal (`deny_domains`) is logged and
swallowed rather than raised to the caller, matching how a notify service is
expected to behave when a message cannot be delivered: the caller (an
`alert`, an automation) should not crash because Cast Notifier decided a
message about an alarm should stay silent. Every *other* failure -- an
unavailable TTS engine, a malformed `data` payload -- does surface to the
caller as a `HomeAssistantError`, because that is a bug to fix, not a
policy decision.
"""

from __future__ import annotations

import logging
from typing import Any

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
from .speaker import CastNotifierRefused, CastSpeaker, SpeakRequest

_LOGGER = logging.getLogger(__name__)


async def _async_speak(speaker: CastSpeaker, request: SpeakRequest) -> None:
    """Speak a request, logging (not raising) a `deny_domains` refusal.

    Anything else is raised to the caller as a `HomeAssistantError`, after
    `CastSpeaker` has restored any volume it changed: silently swallowing a
    failed announcement would leave an automation believing it spoke.
    """
    try:
        await speaker.async_speak(request)
    except CastNotifierRefused as err:
        _LOGGER.warning("Message refused: %s", err)
    except HomeAssistantError:
        # Already a clear, caller-facing error (invalid `data`, a failing
        # `tts.speak`, ...). Let it through untouched.
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
    service = CastNotificationService(hass, runtime_data.speaker)
    # Remembered so unloading the entry can drop this instance from
    # `notify.legacy`'s registry along with the service itself; core never
    # does that for a config entry (see __init__.py).
    runtime_data.legacy_service = service
    return service


class CastNotificationService(BaseNotificationService):
    """Legacy notify service that speaks via the `CastSpeaker`."""

    def __init__(self, hass: HomeAssistant, speaker: CastSpeaker) -> None:
        """Initialize the service."""
        self.hass = hass
        self._speaker = speaker

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
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "cast_notifier"
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
