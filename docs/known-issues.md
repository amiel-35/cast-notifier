# Known issues

Behaviours that are understood, deliberate, and still worth knowing about
before they surprise someone in a log.

## A refusal under core `alert` produces two log lines

Since 0.1.1, a `deny_domains` refusal and an invalid `data` payload are
raised to the caller as a translated `ServiceValidationError`, on top of
being logged at WARNING by `CastSpeaker` ("Refusals raise -- ADR-015 of the
suite" in [`ARCHITECTURE.md`](ARCHITECTURE.md)).

Core's `alert` integration calls its notifiers **without** `blocking`
(`homeassistant/components/alert/entity.py`,
`AlertEntity._send_notification_message` ->
`hass.services.async_call(NOTIFY_DOMAIN, target, msg_payload, context=...)`,
no `blocking=True`; only `ServiceNotFound` is caught there). Nothing awaits
the call, so nobody is there to receive the exception:
`HomeAssistant._run_service_call_catch_exceptions` (`homeassistant/core.py`)
logs it instead. The result, for one refused `alert`, is two entries:

```
WARNING (MainThread) [custom_components.cast_notifier.speaker] Refusing to
  speak a message about alarm_control_panel.home on media_player.kitchen:
  domain 'alarm_control_panel' is in deny_domains ['alarm_control_panel', 'lock']
ERROR (MainThread) [homeassistant.core] Error executing service:
  <ServiceCall notify.cast_kitchen ...>
```

This is noise, not a malfunction, and it is the lesser of the two evils:
the alternative -- swallowing the refusal so the log stays tidy -- answers
the caller with a success for a message that was never spoken. An
automation or script that calls `notify.cast_<name>` with `blocking: true`
sees the error where it belongs, in its own trace, and logs only the
WARNING.

Nothing to do about it here: the double reporting comes from how `alert`
calls its notifiers, not from this integration.

## A `NotifyEntity` call cannot be refused by `deny_domains`

`NotifyEntityFeature` defines no `data` payload, so the modern entity
surface has no `source_entity` to check. Automations that want the deny
list must call the legacy `notify.cast_<name>` service. See
[`ARCHITECTURE.md`](ARCHITECTURE.md), "The deny list: an opt-in safety net".

## Renaming a Cast player's entity id orphans its entry

The config entry's unique ID is the `media_player` entity id, not its
entity registry id (which a template or YAML `media_player` does not have).
Rename the player before configuring it, or delete and re-add the entry
afterwards.

Note that this is about the *player's* entity id, not the entry's title.
Renaming the **entry** is supported and, since 0.2.0, renames its
`notify.cast_<name>` service with it.

## Deleting one of two entries that share a title renames the other

Since 0.2.0 the service name comes from the entry title, and duplicates
are numbered in creation order: two entries titled "Speaker" own
`notify.cast_speaker` and `notify.cast_speaker_2`. Delete the first, and
the second becomes `notify.cast_speaker` on the next reload -- silently,
because from its point of view nothing about it changed.

That is the cost of a name derived from something the user controls, and
it is preferred to the alternative (a name derived from the player's
entity id, which nobody could predict from the UI -- the bug this
replaced). Give two players two different titles and the situation never
arises.

## A quiet-hours refusal is invisible unless the caller waits

Like every other refusal, a message blocked by quiet hours raises
(ADR-015). It is logged at INFO with the reason `quiet_hours`, not at
WARNING: the configuration is doing what it was told to do, and a nightly
alert would otherwise fill the log with warnings about working as
intended. An operator who wants to see them has to be logging at INFO, or
call with `blocking: true` and read the error.

## `volume_while_playing` can be missing from the timeline

The volume timeline's fourth reading comes from a state listener watching
for the player to report `playing`. Home Assistant may never see that
transition -- a short clip can start and finish between two state updates
from a Cast device, which is the same reason the "announcement started"
phase of the playback wait is bounded at 5s. When that happens the step
is simply absent from `last_announcement`, which is itself informative:
Home Assistant never observed the announcement at all.
