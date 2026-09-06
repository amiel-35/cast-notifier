# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository scaffold: `custom_components/cast_notifier`, CI (hassfest,
  HACS validation, lint, tests, release), HACS metadata.
- Config flow (one entry per `media_player`, duplicates aborted) and
  options flow (`tts_entity`, `language`, `voice`, `volume`,
  `restore_volume`, `announce_prefix`, `deny_domains`).
- Legacy `notify.cast_<name>` service and a modern `NotifyEntity`, both
  speaking through `tts.speak` on the configured Cast player, sharing one
  `CastSpeaker`.
- Volume management: set a configured/overridden volume before speaking,
  wait for the player to return to its previous state (or a 30s timeout),
  restore the volume afterwards if `restore_volume` is on.
- `deny_domains` security rule (`alarm_control_panel`, `lock` by default):
  a call whose `data.source_entity` belongs to a denied domain is refused
  and logged instead of spoken.
- Diagnostics, translations (`en`, `fr`, `es`), and tests for the config
  flow, the speaker logic, the notify platform, diagnostics, and
  translation key parity.
