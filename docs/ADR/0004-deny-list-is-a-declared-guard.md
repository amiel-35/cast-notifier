# ADR-0004: `deny_domains` guards what the caller declares, and says so

Status: accepted (0.1.0)

## Context

Some things should not be announced out loud. A house guest should not
learn from a kitchen speaker that the alarm was just disarmed, or that
the front door unlocked. `deny_domains` exists for that, and defaults to
`alarm_control_panel` and `lock`.

The tempting implementation is to inspect the message text. It cannot
work: "the alarm is armed" is a sentence, not an entity reference, and
any text-matching guard is a list of words in one language that fails
open on the first phrasing nobody thought of -- while *reading* as a
guarantee to anyone who reads the option name.

## Decision

`deny_domains` matches on one thing: the domain of the
`data.source_entity` a call chose to declare. That is the whole of its
contract, and the contract is documented as being that narrow -- in the
README, in `docs/ARCHITECTURE.md`, and in `SECURITY.md`, each of which
states plainly what it does not do:

- it never inspects the message text, so a message *about* the alarm
  sent without `data.source_entity` is spoken like any other;
- it is unreachable from the `NotifyEntity` surface, which has no `data`
  payload at all;
- it is not a boundary against anyone who can already call services on
  the instance -- at that point they can call `tts.speak` directly and
  never touch Cast Notifier.

Matching is case-insensitive on both sides: `casefold()` at parse time in
the config flow, and again at comparison time, so an entry stored before
that normalization still behaves.

The general rule: a guard belongs where the caller can state its intent,
and must not pretend to cover what it cannot see.

## Consequences

- What `deny_domains` is genuinely good for: turning "never announce the
  alarm" into a setting an automation opts into by naming its subject,
  so a wiring mistake in one automation is caught by configuration
  rather than by review.
- Automations that want the protection have to declare
  `data.source_entity`. That is a real cost, paid by the caller, in
  exchange for a guard that means exactly one thing.
- A future `NotifyEntity` with a `data` payload would extend the reach
  of this rule without changing it.
