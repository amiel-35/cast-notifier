"""Constants for the Cast Notifier integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cast_notifier"

# Config entry data keys (immutable identity of the entry: the player it
# speaks on). Everything else lives in `entry.options` and is editable
# through the options flow.
CONF_MEDIA_PLAYER: Final = "media_player"

# Config entry option keys.
CONF_TTS_ENTITY: Final = "tts_entity"
CONF_LANGUAGE: Final = "language"
CONF_VOICE: Final = "voice"
CONF_VOLUME: Final = "volume"
CONF_RESTORE_VOLUME: Final = "restore_volume"
CONF_ANNOUNCE_PREFIX: Final = "announce_prefix"
CONF_DENY_DOMAINS: Final = "deny_domains"

DEFAULT_RESTORE_VOLUME: Final = True
# The security rule: state from these domains is never spoken, so a
# security event can never be inferred from what is heard on a speaker.
DEFAULT_DENY_DOMAINS: Final[list[str]] = ["alarm_control_panel", "lock"]

# `data` payload attributes accepted by the legacy `notify.cast_<name>`
# service and by `NotifyEntity.send_message`. All are per-call overrides of
# the config entry options above, except `source_entity`, which only
# exists at call time.
ATTR_VOLUME: Final = "volume"
ATTR_LANGUAGE: Final = "language"
ATTR_VOICE: Final = "voice"
ATTR_TTS_ENTITY: Final = "tts_entity"
# The entity a message is *about*, e.g. `binary_sensor.washing_machine_done`
# or `alarm_control_panel.home`. Used only to enforce `deny_domains`; it is
# never spoken and never forwarded to `tts.speak`.
ATTR_SOURCE_ENTITY: Final = "source_entity"

# How long Cast Notifier waits for a player to report it is done speaking
# (state returns to what it was before the announcement) before giving up
# and restoring the volume anyway.
PLAYBACK_TIMEOUT: Final = 30
