# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-09-07

### Fixed

- A message refused by `deny_domains` now **fails the call** instead of
  being logged and swallowed. It was answered with a silent success, so an
  automation could believe it had spoken. `CastNotifierRefused` and
  `CastNotifierInvalidData` are now translated `ServiceValidationError`s
  raised to the caller (ADR-015 of the suite), and are still logged at
  WARNING. Under core `alert`, which calls its notifiers without waiting
  for them, a refusal now produces two log lines -- see
  [`docs/known-issues.md`](docs/known-issues.md).
- The config flow refuses a `media_player` whose `supported_features`
  lacks `PLAY_MEDIA`, with the form error `player_cannot_play_media`.
  Configuring such a player used to succeed and then fail with
  `ServiceNotSupported` on every announcement.
- An entry that unloaded while its legacy platform was still being
  discovered left a `notify.cast_<name>` behind, bound to a dead speaker
  and with nothing left to retract it: the discovery runs in a task core
  owns, so it could register the service *after* the unload callback had
  looked for it. Both `async_get_service` and `async_register_services`
  now check whether the entry has unloaded.
- Unloading an entry only removes its `notify.cast_<name>` service if it is
  actually registered (no more "Unable to remove unknown service" in the
  log), and the discovery is tied to the entry (`entry.async_create_task`)
  rather than left to outlive it.

### Changed

- Removed the unused `entity.notify.cast_notifier.name` translation key and
  the entity's `_attr_translation_key`: the notify entity takes its device's
  name (`_attr_name = None`), which is what keeps two entries
  distinguishable.
- Documentation: a "Refusals raise -- ADR-015 of the suite" section and the
  `PLAY_MEDIA` check in `docs/ARCHITECTURE.md`, a new
  `docs/known-issues.md`, and a README section on the up-to-5s wait when
  the player is already playing.

## [0.1.0] - 2026-09-07

### Added

- Repository scaffold: `custom_components/cast_notifier`, CI (hassfest,
  HACS validation, lint, tests, release), HACS metadata.
- Config flow (one entry per `media_player`, duplicates aborted) and
  options flow (`tts_entity`, `language`, `voice`, `volume`,
  `restore_volume`, `announce_prefix`, `deny_domains`). Changing an option
  reloads the entry and takes effect on the next call.
- Legacy `notify.cast_<name>` service and a modern `NotifyEntity`, both
  speaking through `tts.speak` on the configured Cast player, sharing one
  `CastSpeaker`. One service device per entry, so several players are
  distinguishable in the UI.
- Volume management: set a configured/overridden volume before speaking,
  wait for the announcement to run its course, restore the volume
  afterwards if `restore_volume` is on. The volume is restored even when
  `tts.speak` fails, skipped entirely on a player that does not advertise
  `VOLUME_SET`, and never allowed to prevent the message from being spoken.
- Announcements are serialized per player, so overlapping calls cannot
  strand a speaker at the announcement volume.
- The wait for an announcement tracks the player's media identity
  (`media_content_id`, `app_id`, `media_title`), not just its `state`, and
  bounds the "announcement started" phase at 5s separately from the 30s
  overall timeout -- a player that was already playing music no longer
  stalls the caller.
- `deny_domains` safety net (`alarm_control_panel`, `lock` by default,
  case-insensitive): a call whose `data.source_entity` belongs to a denied
  domain is refused and logged instead of spoken. Documented as an opt-in
  net that only applies when `data.source_entity` is present, not as a
  guarantee.
- The `data` payload is validated up front, so a malformed automation gets
  a clear error instead of a `TypeError` from inside the speaking path.
- Removing, unloading or reloading an entry retracts its
  `notify.cast_<name>` service; Home Assistant core never does this for a
  legacy notify service.
- `MINOR_VERSION` and a no-op `async_migrate_entry`, so the first schema
  change can ship as a migration.
- Diagnostics (resilient to an entry that is not loaded), translations
  (`en`, `fr`; `es` machine translated), and tests for the entry
  lifecycle, the config flow, the speaker logic, the notify platform,
  diagnostics, and translation key parity.

[Unreleased]: https://github.com/amiel-35/cast-notifier/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/amiel-35/cast-notifier/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/amiel-35/cast-notifier/releases/tag/v0.1.0
