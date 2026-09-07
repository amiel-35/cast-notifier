# Sprint 2 brief — Cast Notifier v0.2.0 (quiet hours, sane service names, honest quality scale)

> One coding agent, branch `feat/0.2.0`, in its own worktree. English. Tests
> first: write the failing tests for items 1–3 before touching the
> integration. Paths from the orchestrator: core clone 2026.9.1 at
> `$HA_CORE_SRC`, venv at `$VENV`. Never touch `.github/workflows` or any
> Home Assistant instance.

## Context

0.1.1 was installed for the first time on a real instance on 07/09/2026
(HA 2026.9.1, Google Nest speaker, Home Assistant Cloud TTS). Findings:
the service was created as `notify.cast_cuisine_2` because the name derives
from the media_player *entity id*, not from the entry title "Cuisine"; the
announcement played (8 s of `playing`) and the volume was restored to 0.30,
but the volume recorded *during* playback was 0.55 while the configured
announcement volume was 0.40, and nothing in the logs explains it.

## Scope (must)

1. **Service name from the entry title.** `notify.cast_<slugify(title)>`;
   if that slug is already taken by another entry of this integration, fall
   back to `<slug>_<n>` (n = 2, 3, …) deterministically. Renaming the entry
   in the UI renames the service (the entry-update listener already reloads).
   Breaking change for 0.1.x users: documented in `CHANGELOG.md` and
   `README.md` ("upgrade notes"); no automatic alias.
2. **Quiet hours.** New options `quiet_start` / `quiet_end` (`TimeSelector`,
   both empty = disabled; a window may cross midnight) and `quiet_volume`
   (0–1 slider, optional). Inside the window: if `quiet_volume` is set, the
   message is spoken at that volume instead of the configured/`data.volume`
   one; if it is not set, the message is **refused** with a translated
   `ServiceValidationError` (ADR-0003: refusals raise), logged at
   INFO with the reason `quiet_hours`. `data.priority: "critical"` (the key
   Notify Switchboard forwards untouched) bypasses quiet hours entirely.
   Per-call `data.volume` still wins over `quiet_volume`.
3. **Volume observability.** DEBUG logs at every step of an announcement:
   volume read before, volume requested, volume observed after `volume_set`
   settles (state attribute), volume observed when the player reports
   `playing`, volume restored. Diagnostics of the entry expose the last
   announcement's timeline (timestamps + those five values), bounded to the
   last one. This is what will explain the 0.55 above on the next real test.
4. **Honest quality scale.** `quality_scale.yaml`: every bronze, silver and
   gold rule assessed (`done` / `todo` with reason / `exempt` with reason);
   `brands: done` ("bundled in-repo icon, HA 2026.3+"); add `icons.json` for
   the notify entity if `icon-translations` is otherwise the only gap.
5. **Docs.** `docs/known-issues.md` refreshed (the entity-id-as-unique-id
   limitation stays; the `data` keys on the `NotifyEntity` path stay);
   `README.md` gains "Quiet hours" and "Upgrade notes"; `CHANGELOG.md`.

## Out of scope (must not)

Chimes / pre-announce media (deliberately refused: timing-based, fragile),
Cast groups, multi-player entries, any change to the deny-list semantics,
the `NotifyEntity` `data` limitation.

## Definition of done

Tests for items 1–3 written first and green; overall coverage stays ≥ 95 %;
ruff / mypy / hassfest green; `strings.json` + en/fr/es complete (options,
exception `quiet_hours`); conventional commits; PR opened, not merged, not
tagged; PR description cites every core API with its path in `$HA_CORE_SRC`.
