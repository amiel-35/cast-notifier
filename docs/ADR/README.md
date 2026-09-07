# Architecture decision records

Decisions that shaped Cast Notifier and that a reader would otherwise
have to reverse-engineer from the code. One file per decision, numbered
in the order they were written down, never renumbered.

An ADR is here when the decision is *not* obvious from the code it
governs -- when the alternative is defensible, when the reason lives in
Home Assistant core rather than in this repository, or when someone
"simplifying" the code later would undo it. Everything else belongs in a
comment or in [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

| ADR | Decision |
| --- | --- |
| [0001](0001-frozen-service-name.md) | A config entry's `notify.*` service name is decided once and frozen in `entry.data`. |
| [0002](0002-priority-vocabulary.md) | `data.priority` is a closed vocabulary of four values, matched exactly. |
| [0003](0003-refusals-raise.md) | A refusal is raised to the caller, and logged. Never swallowed. |
| [0004](0004-deny-list-is-a-declared-guard.md) | `deny_domains` guards what a caller declares, and says so rather than pretending to cover more. |

These supersede references to an external "ADR-010" and "ADR-015 of the
suite" that appeared in earlier comments and docs: the numbering was the
wider Notify Switchboard project's, and pointed at documents this
repository does not ship. Where the same rule holds in both places it is
noted, but this directory is the authority for Cast Notifier.
