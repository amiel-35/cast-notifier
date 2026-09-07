"""Constants for the Cast Notifier integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cast_notifier"

# Config entry data keys. Not user-editable: the player the entry speaks
# on, and the service name it owns. Everything else lives in
# `entry.options` and is editable through the options flow.
CONF_MEDIA_PLAYER: Final = "media_player"
# The `notify.<name>` service this entry owns, and the `cast_<slug>` base
# it was derived from. Frozen the first time the entry is set up and only
# recomputed when the base no longer matches the entry title, so that a
# name never moves because of something another entry did. Also recomputed
# if a foreign integration has since started serving the frozen name.
# Absent from entries created before 0.2.0.
CONF_SERVICE_NAME: Final = "service_name"
CONF_SERVICE_NAME_BASE: Final = "service_name_base"

# Config entry option keys.
CONF_TTS_ENTITY: Final = "tts_entity"
CONF_LANGUAGE: Final = "language"
CONF_VOICE: Final = "voice"
CONF_VOLUME: Final = "volume"
CONF_RESTORE_VOLUME: Final = "restore_volume"
CONF_ANNOUNCE_PREFIX: Final = "announce_prefix"
CONF_DENY_DOMAINS: Final = "deny_domains"
# Quiet hours: a `HH:MM:SS` window (it may cross midnight) during which a
# message is either spoken at `quiet_volume` or refused outright. Both
# bounds empty means the feature is off.
CONF_QUIET_START: Final = "quiet_start"
CONF_QUIET_END: Final = "quiet_end"
CONF_QUIET_VOLUME: Final = "quiet_volume"

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
# How urgent the caller considers this message. This is the key the wider
# notification layer forwards untouched to every notifier, so Cast
# Notifier accepts it -- but only as one of `PRIORITIES`, matched exactly
# and in lower case. Only `critical` acts (it bypasses quiet hours); the
# other three are accepted and ignored. Anything else -- `"CRITICAL"`,
# `"urgent"`, `3`, `""` -- is refused as invalid data rather than quietly
# treated as "not critical": a vocabulary that swallows what it does not
# understand teaches a caller nothing. See
# docs/ADR/0002-priority-vocabulary.md.
ATTR_PRIORITY: Final = "priority"
PRIORITIES: Final = ("info", "normal", "high", "critical")
PRIORITY_CRITICAL: Final = "critical"

# How long Cast Notifier waits, in total, for a player to report it is done
# speaking (it looks the way it did before the announcement again) before
# giving up and restoring the volume anyway.
PLAYBACK_TIMEOUT: Final = 30

# How long Cast Notifier waits for the announcement to *start* -- i.e. for
# the player to stop looking the way it did before the call -- before
# concluding it will never see that transition and skipping straight to
# restoring the volume. Bounded separately from, and much shorter than,
# `PLAYBACK_TIMEOUT`: a player that was already playing music may never
# produce an observable change, and a caller must not hang 30s for that.
ANNOUNCEMENT_START_TIMEOUT: Final = 5
