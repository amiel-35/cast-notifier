# Cast Notifier

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A [Home Assistant](https://www.home-assistant.io/) custom integration that
gives you a `notify.*` service that **speaks** on a Google Cast player
(Google Home, Nest, Chromecast -- any `media_player` of the `cast`
integration).

## Why

Home Assistant has no `notify` that speaks. Voice goes through
`tts.speak`, an **action**, not a notify service -- so `alert`, blueprints
and automations written against a notify target cannot use it. Cast
Notifier fixes that: one config entry = one Cast player = one
`notify.cast_<name>` legacy service (so `alert.notifiers:` can list it) +
one `NotifyEntity`. Both speak the message through `tts.speak`, managing
volume and waiting for playback to finish themselves, because Cast does
not support Home Assistant's native "announce" capability -- see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full contract and
the core evidence behind that claim.

## Install

Via [HACS](https://hacs.xyz/), as a custom repository:

1. HACS -> Integrations -> menu -> Custom repositories.
2. Add `https://github.com/amiel-35/cast-notifier`, category "Integration".
3. Install "Cast Notifier", then restart Home Assistant.

## Configuration

Settings -> Devices & services -> Add integration -> "Cast Notifier".
Pick the Cast player and the `tts.*` entity to speak with; everything else
has a default and can be changed later from the integration's options:

| Option | Default | Meaning |
|---|---|---|
| `language` | engine default | Language code passed to `tts.speak`. |
| `voice` | none | Voice name, or a JSON object of `tts.speak` options. |
| `volume` | none | If set, volume is set to this level before speaking and restored after. Ignored on a player that does not support `volume_set` (Cast groups, fixed-output devices): the message is still spoken. |
| `restore_volume` | on | Restore the previous volume once the message is done. |
| `announce_prefix` | none | Text spoken before every message, e.g. "Attention." |
| `deny_domains` | `alarm_control_panel, lock` | Entity domains a call may not declare as its `data.source_entity`. Case-insensitive. See [the deny list](#the-deny-list-a-safety-net-not-a-guarantee). |
| `quiet_start` / `quiet_end` | none | A window during which this player stays quiet. Both empty means off; the window may cross midnight. See [quiet hours](#quiet-hours). |
| `quiet_volume` | none | Volume used inside that window. Empty means messages are refused instead. |

A player that does not support `play_media` is refused in the form: it
could never speak, since every announcement ends in
`media_player.play_media`.

This creates `notify.cast_<entry title>`, e.g. `notify.cast_kitchen` for
an entry named "Kitchen", plus a device and a notify entity per entry.
**Renaming the entry renames the service**; if two entries share a title,
the second gets `_2`, the third `_3`, and so on, in creation order.

Changing any option reloads the entry, which re-registers the service
against the new settings: the change takes effect on the very next call,
with no restart.

## Examples

An `alert:` that speaks in the kitchen when triggered:

```yaml
alert:
  washing_machine_done:
    name: The washing machine is done
    entity_id: binary_sensor.washing_machine_done
    state: "on"
    notifiers:
      - cast_kitchen
```

An automation, with a per-call language override and the security
deny-list at work:

```yaml
automation:
  - alias: Announce washing machine finished
    trigger:
      - trigger: state
        entity_id: binary_sensor.washing_machine_done
        to: "on"
    action:
      - action: notify.cast_kitchen
        data:
          message: The washing machine is done.
          data:
            source_entity: binary_sensor.washing_machine_done
```

Every other `data` key is validated before anything is spoken: a bad
`volume`, a `tts_entity` that is not a `tts.*` entity, a non-string
`source_entity` and so on are refused with a clear error instead of failing
somewhere inside the speaking path.

## When a message is not spoken, the call fails

A refusal is an error, not a silent success. A call blocked by the deny
list, one blocked by [quiet hours](#quiet-hours), and one with a malformed
`data` payload all raise: an automation or script using `blocking: true`
gets the failure in its own trace, the error appears in the UI, and a line
is written to the log either way (a warning, or INFO for quiet hours --
that one is the configuration working as intended). Cast Notifier never
answers "sent" for a message nobody heard.

One consequence worth knowing: core's `alert` integration calls its
notifiers without waiting for them, so a refused `alert` produces two log
lines instead of one -- the warning, then Home Assistant's own report of
the unhandled error. See
[`docs/known-issues.md`](docs/known-issues.md).

## Quiet hours

Set `quiet_start` and `quiet_end` to keep a player quiet at night (or
during a nap: the window does not have to be nocturnal). It is a
half-open window -- `22:00` to `07:00` means quiet from 22:00 up to, but
not including, 07:00 -- evaluated against your instance's local time, and
it may cross midnight. Leave both empty to disable it; one without the
other is refused in the form rather than stored and silently ignored.

Inside the window, what happens depends on `quiet_volume`:

- **`quiet_volume` set**: the message is spoken at that volume.
- **`quiet_volume` empty**: the message is **refused**. The call fails,
  the same way a deny-list refusal does, and an INFO line is written to
  the log with the reason `quiet_hours`.

Two things still get through:

```yaml
action: notify.cast_kitchen
data:
  message: Water leak in the kitchen.
  data:
    priority: critical
```

`data.priority: critical` bypasses quiet hours entirely -- window, quiet
volume and all -- because a leak at 3am is exactly what a notifier is
for. And a per-call `data.volume` still wins over `quiet_volume`: a
caller that names a volume has said something about *this* message, which
beats a rule about this time of day.

Volume precedence, most specific first: `data.volume`, then
`quiet_volume` (inside the window), then the entry's `volume`.

## Announcement while music is playing

When a `volume` is configured, Cast Notifier lowers the volume, speaks, and
waits for the player to be doing what it was doing before restoring it.
That wait is a heuristic against the player's reported state, and it has a
cost worth knowing about: **if the player was already playing something,
the call can take up to 5 seconds** even for a two-word message.

Home Assistant may never observe the switch to the TTS clip -- a short clip
can start and finish between two state updates from the Cast device -- so
Cast Notifier gives the announcement 5 seconds to become visible before
concluding it already ended and restoring the volume
(`ANNOUNCEMENT_START_TIMEOUT`, capped by an overall 30s
`PLAYBACK_TIMEOUT`). The message itself is spoken immediately; it is the
service call that returns late. A script that chains several announcements,
or one that calls with `blocking: true` in a tight sequence, will feel it.

Set no `volume` on the entry to opt out entirely: with nothing to restore,
Cast Notifier speaks and returns straight away.

## The deny list: a safety net, not a guarantee

`deny_domains` refuses a call **whose `data.source_entity` names an entity
in one of those domains**. That is the whole of its contract. It is an
opt-in safety net for automations that declare what they are talking about
-- a way to make "never announce the alarm" a setting rather than a code
review -- and it follows the project doctrine ADR-010: a guard belongs
where the caller can state its intent, and must not pretend to cover what
it cannot see.

What it therefore does **not** do:

- it never inspects the message text, so
  `notify.cast_kitchen: {message: "The alarm is armed"}` with no
  `data.source_entity` is spoken normally;
- it is unreachable from the `NotifyEntity` surface, which has no `data`
  payload at all (see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md));
- it is not a security boundary against someone who can already call
  services on your Home Assistant instance.

Treat it as a seatbelt for your own automations, not as a lock. Domain
matching is case-insensitive.

## Troubleshooting

**"Why was that announcement so loud?"** Every announcement records five
volume readings, timestamped: what the player was at before, what Cast
Notifier asked for, what the player reported once `volume_set` returned,
what it reported while it was actually `playing`, and what it was left at.
Two ways to read them:

- Settings -> Devices & services -> Cast Notifier -> the three-dot menu on
  the entry -> **Download diagnostics**. The `last_announcement` section
  holds the timeline of the most recent announcement.
- Or turn on debug logging and read the same five lines:

```yaml
logger:
  logs:
    custom_components.cast_notifier: debug
```

A `volume_while_playing` that does not match `volume_requested` means the
device -- not this integration -- decided what to play at.

**"The call takes seconds to return."** Expected when a `volume` is set
and the player was already playing something; see
[above](#announcement-while-music-is-playing). Clear the `volume` option
to opt out.

**"One refusal, two errors in the log."** Core's `alert` calls its
notifiers without waiting for them, so the warning and Home Assistant's
own report of the unhandled error both appear. See
[`docs/known-issues.md`](docs/known-issues.md).

**"Nothing is spoken at night."** Quiet hours with no `quiet_volume`
refuse messages; look for `reason quiet_hours` at INFO in the log. Set a
`quiet_volume`, or send the message with `data.priority: critical`.

## Upgrade notes

### 0.1.x to 0.2.0: services are renamed

**This will break automations, scripts and `alert.notifiers:` entries
that name a Cast Notifier service.**

Until 0.1.1 the service name came from the `media_player` **entity id**:
`media_player.kitchen` gave `notify.cast_kitchen`, and
`media_player.kitchen_2` gave `notify.cast_kitchen_2` however the entry
was named. From 0.2.0 it comes from the **entry title** -- the name shown
in Settings -> Devices & services, and the one you can change there.

There is deliberately **no alias**: an integration that silently answers
to two names is an integration nobody can reason about, and the old name
was the accident.

What to do, once, after upgrading:

1. Note each entry's title, or rename it to what you want the service to
   be called (renaming now renames the service, with no restart).
2. Find the new names in Developer tools -> Actions, under `notify`.
3. Replace the old names wherever they appear -- automations, scripts,
   `alert.notifiers:`, blueprints, dashboards.

If a title happens to slugify to what the entity id gave before, nothing
changes for that entry.

## Removal

Settings -> Devices & services -> Cast Notifier -> delete, for each
configured player. This removes its `notify.cast_<name>` service, its
device and its entity; it does not touch the Cast player or the `tts.*`
entity it used. Unloading or disabling the entry retracts the service the
same way.

## Translations

English and French are maintained by hand. **Spanish (`es`) is machine
translated** and has not been reviewed by a native speaker --
[corrections are very welcome](https://github.com/amiel-35/cast-notifier/issues),
as are new languages.

## License

[MIT](LICENSE) (c) 2026 Amiel Lavon
