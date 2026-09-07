# ADR-0001: a service name is decided once, and frozen

Status: accepted (0.2.0)

## Context

Each config entry owns one legacy `notify.<name>` service. The name is
derived from the entry title -- `slugify(title)`, prefixed `cast_` -- so
that what the user renames in the UI is what the service is called.

Titles are not unique. Two Nest speakers can both be called "Speaker",
and two entries would then want `notify.cast_speaker`. Duplicates are
therefore numbered: `cast_speaker`, `cast_speaker_2`, `cast_speaker_3`.

The obvious implementation -- recompute the numbering from the entry
list at every setup -- is wrong in two ways, and both were observed:

- **The name moves under an entry that did not change.** Delete the
  first of two entries titled "Speaker" and the second is promoted from
  `cast_speaker_2` to `cast_speaker`. Every automation, script and
  `alert.notifiers:` line naming `cast_speaker_2` breaks, because of an
  edit to a *different* entry.
- **A recomputed name can land on one already registered.**
  `BaseNotificationService.async_register_services` returns early when
  `hass.services.has_service(DOMAIN, self._service_name)` is already
  true (`homeassistant/components/notify/legacy.py:312`, 2026.9.1). The
  second claimant registers nothing at all while believing it owns the
  service -- so it is LOADED and mute -- and retracting "its" service on
  unload silences the first.

Setup order cannot be leaned on either: Home Assistant sets a domain's
entries up concurrently (`homeassistant/setup.py`, `asyncio.gather` over
`entry.async_setup_locked`), so "whoever is set up first gets the plain
slug" is not a rule, it is a race.

## Decision

The name is decided **once**, the first time an entry is set up, and
written to `entry.data` as `service_name`, alongside the `cast_<slug>`
it was derived from as `service_name_base`.

- Later setups reuse the stored name verbatim.
- It is recomputed only when `service_name_base` no longer matches the
  slug of the current title -- i.e. when the user renamed the entry.
  Nothing another entry does can move it.
- A recompute skips names already claimed: those stored in another
  entry's `data` (which covers entries that are disabled, failed, or not
  yet set up), those in the in-memory `hass.data[SERVICE_OWNERS]` map
  (which covers an entry that claimed a name in this same event loop
  iteration, before its `entry.data` write could be seen), and those
  served by a *foreign* integration (see below).
- The starting number for an entry that has no stored name is its rank,
  in creation order, among the entries wanting the same base and not yet
  frozen. `hass.config_entries.async_entries` is insertion-ordered
  (`ConfigEntryItems` in `homeassistant/config_entries.py`). A name is
  deterministic once frozen; the one-time upgrade numbering -- several
  entries at once, none of them frozen, as after an upgrade from 0.1.x --
  is creation order among enabled entries, and can depend on setup order
  when a disabled entry sharing the same title sits in the middle of that
  creation order.
- On unload, an entry retracts **only** the name `SERVICE_OWNERS`
  attributes to it. Removing `notify.<name>` by name alone would let one
  entry retract another's service, and core -- which never registered a
  second service under that name -- would leave the other entry LOADED
  and mute.
- The claim and the `entry.data` write happen in a `@callback`, with no
  `await` between them, so two entries setting up in the same event loop
  iteration cannot both claim one name.

The freeze binds this integration to **itself**. It is not a claim
against core: a stored name that a different integration has started
serving in the meantime is dropped and recomputed, because core would
refuse to register over it and this entry's unload would delete somebody
else's service.

No config entry version bump. The two keys are additive, absent from
0.1.x entries, and their absence is the upgrade path: the first setup
after the upgrade picks a name and freezes it. A migration would have to
invent the same numbering with less information than setup has.

## Consequences

- Renaming an entry is the only thing that moves its service, and it
  does so immediately: `async_update_entry` fires the update listeners
  for a changed `title` exactly as for changed `options`, and the reload
  retracts the old service and registers the new one, with no restart.
  There is deliberately no alias for the old name.
- Two entries titled "Speaker" keep `cast_speaker` and `cast_speaker_2`
  for as long as they exist, in that order, whatever happens to their
  neighbours. Deleting `cast_speaker` leaves a hole; the next entry
  created with that title fills it. That is the intended trade: a stable
  name is worth more than tidy numbering.
- The name an entry owns while it is *stopped* lives in `entry.data`;
  the name it owns while it is *running* lives in `SERVICE_OWNERS`. Both
  are needed, and neither is redundant -- the first survives a restart,
  the second is visible before the first is written.
