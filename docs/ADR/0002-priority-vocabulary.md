# ADR-0002: `data.priority` is a closed vocabulary, matched exactly

Status: accepted (0.2.0)

## Context

`priority` is not Cast Notifier's key. It belongs to the wider
notification layer, which puts it in the `data` payload it fans out to
every notifier it knows about. Cast Notifier is one of those notifiers,
and it acts on exactly one value: `critical` bypasses
[quiet hours](../ARCHITECTURE.md#quiet-hours), because a water leak at
3am is what a notifier is for.

The first implementation validated `priority` as `cv.string` and
compared with `.casefold()`. That combination accepts a great deal:

- `"CRITICAL"` bypassed quiet hours. Nothing documented that it would,
  and nothing would have told the caller if the casefold were ever
  removed.
- `"urgent"`, `"P1"`, `"très urgent"` -- any string at all -- were
  accepted and silently treated as "not critical". A caller who meant
  "wake them up" got a message held back at 3am and no indication why.
- `3` was accepted too: `cv.string` coerces scalars, so an integer
  priority from another system became `"3"`.
- `""` was accepted, and meant nothing.

The through-line is that every one of these is a caller and a notifier
disagreeing about what a word means, resolved silently in the notifier's
favour. That is the failure mode this integration exists to avoid: it
[raises rather than swallowing](0003-refusals-raise.md) everywhere else.

## Decision

`data.priority` accepts exactly four values, lower case, matched
literally:

```
info | normal | high | critical
```

Enforced in the schema -- `vol.Optional(DATA_PRIORITY):
vol.In(PRIORITIES)` in `speaker.py`, with `PRIORITIES` in `const.py` --
so a value outside the vocabulary is refused as **invalid data** before
anything is read out of the payload: `CastNotifierInvalidData`, a
translated `ServiceValidationError` with `translation_key ==
"invalid_data"`, and nothing is spoken.

No coercion (`vol.In`, not `vol.All(cv.string, vol.In(...))`), and no
casefolding anywhere downstream. `"CRITICAL"`, `"urgent"`, `3` and `""`
all fail the call.

Only `critical` acts. `info`, `normal` and `high` are accepted and
ignored -- they are in the vocabulary because they are what the wider
layer sends, and refusing them would break a payload that is perfectly
well-formed.

Because the schema is the only place that decides what a priority may
be, `_enforce_quiet_hours` compares with a plain `==`. Every lenient
comparison downstream was compensating for a lenient schema.

## Consequences

- A caller who writes `priority: CRITICAL` gets an error naming the
  problem, at the moment of the call, instead of a message that was
  quietly held until morning. This is the point.
- Adding a fifth level is a one-line change to `PRIORITIES` plus a
  CHANGELOG entry. Removing one is a breaking change.
- Unknown *keys* in `data` are still allowed through and ignored
  (`extra=vol.ALLOW_EXTRA`): a payload shared with another notifier must
  not break this one. The strictness is about the values of the keys
  Cast Notifier claims to understand, not about the shape of somebody
  else's payload.
- If the wider notification layer ever standardises on a different
  vocabulary, this is the single place to change, and the tests
  (`tests/test_quiet_hours.py`) enumerate both halves of the contract.
