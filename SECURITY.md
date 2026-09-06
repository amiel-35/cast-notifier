# Security policy

Cast Notifier speaks messages on Google Cast players you already control; it
makes no outbound network calls of its own beyond the `tts.speak` and
`media_player.*` service calls it issues on your own Home Assistant instance.

## About `deny_domains`

The one security-relevant behavior this integration owns is the
`deny_domains` list: by default, a call whose `data.source_entity` belongs to
`alarm_control_panel` or `lock` is refused and logged instead of spoken.

**It is an optional safety net, not a guarantee, and it is not a security
boundary.** It applies only when a call chooses to declare
`data.source_entity`; it does not inspect the message text, and it is not
reachable from the `NotifyEntity` surface, which has no `data` payload at
all. A message about your alarm sent without `data.source_entity` is spoken
like any other. Its purpose is to turn "never announce the alarm" into a
setting an automation opts into, so that a wiring mistake in one automation
is caught by configuration rather than by review (project doctrine
ADR-010).

In particular, `deny_domains` is no defence against anyone who can already
call services on your Home Assistant instance: at that point they can call
`tts.speak` directly and never touch Cast Notifier.

## Reporting

If you find a way to bypass the check *within* that stated scope, or any
other vulnerability (for example a way to make Cast Notifier call an
unintended `tts.speak`/`media_player` target), please open a private
security advisory on GitHub (Security → Report a vulnerability) rather than
a public issue. You will get an answer within 14 days.

Supported versions: the latest minor release only.
