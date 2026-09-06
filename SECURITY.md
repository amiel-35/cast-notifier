# Security policy

Cast Notifier speaks messages on Google Cast players you already control; it
makes no outbound network calls of its own beyond the `tts.speak` and
`media_player.*` service calls it issues on your own Home Assistant instance.

The one security-relevant behavior this integration owns is the
`deny_domains` list: by default, a call whose `data.source_entity` belongs to
`alarm_control_panel` or `lock` is refused and logged, so alarm/lock state is
never spoken out loud. If you find a way to bypass that check, or any other
vulnerability (for example a way to make Cast Notifier call an unintended
`tts.speak`/`media_player` target), please open a private security advisory
on GitHub (Security → Report a vulnerability) rather than a public issue.
You will get an answer within 14 days.

Supported versions: the latest minor release only.
