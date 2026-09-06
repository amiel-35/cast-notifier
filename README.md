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

This creates `notify.cast_<player>`, e.g. `notify.cast_kitchen` for
`media_player.kitchen`, plus a device and a `notify.<player>` entity per
entry.

Changing any option reloads the entry, which re-registers
`notify.cast_<player>` against the new settings: the change takes effect on
the very next call, with no restart.

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
