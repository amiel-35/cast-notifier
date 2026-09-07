# ADR-0003: a refusal is raised to the caller, and logged

Status: accepted (0.1.1)

## Context

Cast Notifier refuses to speak in three cases: the `deny_domains` rule
matched the call's `data.source_entity`, the `data` payload does not
match the contract, or the call landed inside a quiet window with no
`quiet_volume` to speak it at.

Until 0.1.1 a `deny_domains` refusal was logged and swallowed. The
service call returned success. An automation with `blocking: true`, a
script, and the UI all saw "sent" for a message nobody heard -- and the
only trace was a log line the author of the automation had no reason to
be watching.

A notifier answering "sent" for a message it deliberately did not send
is the worst available outcome. It is worse than an error, because the
caller cannot distinguish it from working; and worse than silence,
because it actively asserts something false.

## Decision

Nothing is swallowed. Every refusal reaches the caller **and** the log.

- The three refusals are `ServiceValidationError` subclasses --
  `CastNotifierRefused`, `CastNotifierInvalidData`,
  `CastNotifierQuietHours` -- not bare `HomeAssistantError`s: the caller
  asked for something this integration refuses to do, which is a problem
  with the call, not a failure inside it. Home Assistant renders a
  `ServiceValidationError` as its translated message, without a
  traceback.
- Each carries a `translation_domain`, a `translation_key` declared in
  the `exceptions` section of `strings.json`, and placeholders, so the
  message names the player and the reason in the user's language.
- Each is raised *after* `CastSpeaker` has restored any volume it
  changed. A refusal never leaves a speaker turned down.
- A failing `tts.speak`, or any unexpected error, surfaces the same way
  as a `HomeAssistantError`.
- `notify.py::_async_speak` re-raises rather than reporting HTTP 200 for
  a message nobody heard.
- The refusal is *also* logged, because a caller that did not pass
  `blocking: true` never sees the exception -- core's own `alert`
  integration is exactly such a caller. Deny-list and invalid-data
  refusals are logged at WARNING. A quiet-hours refusal is logged at
  INFO: the configuration is doing precisely what it was told to do, and
  a nightly alert would otherwise fill the log with warnings about
  working as intended.

## Consequences

- Under core `alert`, which calls its notifiers without waiting for
  them, a refusal produces **two** log lines: the one `CastSpeaker`
  writes, and Home Assistant's own report of the unhandled error. This
  is a known and accepted cost, documented in
  [`../known-issues.md`](../known-issues.md); the alternative is
  silence.
- An operator who wants to see quiet-hours refusals has to be logging at
  INFO, or to call with `blocking: true` and read the error.
- Callers that treat a notify failure as fatal will now fail where they
  used to succeed. That is the correct behaviour, and the 0.1.1 changelog
  says so.

The wider Notify Switchboard project follows the same rule, where it is
written down under its own number. This ADR is the authority for Cast
Notifier: the integration is usable, and correct, on its own.
