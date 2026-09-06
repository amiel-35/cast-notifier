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
| `volume` | none | If set, volume is set to this level before speaking and restored after. |
| `restore_volume` | on | Restore the previous volume once the message is done. |
| `announce_prefix` | none | Text spoken before every message, e.g. "Attention." |
| `deny_domains` | `alarm_control_panel, lock` | Entity domains that are never spoken about. |

This creates `notify.cast_<player>`, e.g. `notify.cast_kitchen` for
`media_player.kitchen`.

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

`data.source_entity` is optional and does no harm here (`binary_sensor` is
not in `deny_domains`), but it is what enforces the security rule: sending
`data.source_entity: alarm_control_panel.home` refuses the message instead
of speaking it, so alarm or lock state can never leak out through a
speaker.

## Removal

Settings -> Devices & services -> Cast Notifier -> delete, for each
configured player. This removes its `notify.cast_<name>` service and its
entity; it does not touch the Cast player or the `tts.*` entity it used.

## License

[MIT](LICENSE) (c) 2026 Amiel Lavon
