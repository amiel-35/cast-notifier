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
