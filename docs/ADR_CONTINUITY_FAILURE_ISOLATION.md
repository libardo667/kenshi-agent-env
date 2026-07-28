# ADR: Continuity failures are isolated and planner-visible

## Status

Accepted. Extends
[ADR_CONTINUITY_AUTHORITY](ADR_CONTINUITY_AUTHORITY.md) at the storage and
planner-feedback boundaries.

## Context

Continuity operations execute beside valid gameplay. A semantic lifecycle
conflict is expected model feedback, while an unexpected SQLite failure means
the writer may no longer be trustworthy. Treating both as exceptions could
cancel the surrounding plan; treating both as ordinary rejection could invite
more writes into a damaged store.

Receipts were logged but had no identity, carried their full evidence payload
into planner context, and were not represented in the exact delivery manifest.
Payload budgeting kept the whole bounded list accidentally rather than
deliberately protecting the latest corrective feedback.

## Decision

Every attempted continuity operation receives a runtime-owned receipt ID and
exactly one status:

- `accepted` means the transition committed and may expose its resulting memory
  ID and status;
- `rejected` means evidence, lifecycle, identity, or an expected uniqueness
  constraint refused the operation without changing state;
- `no_op` means the configured continuity authority did not write;
- `failed` means an unexpected database failure occurred.

Expected active-key conflicts are preflighted where possible and uniqueness
races are translated at the store boundary. Event append and projection update
remain one SQLite transaction.

An unexpected SQLite write failure rolls that transaction back, produces a
`failed` receipt when an operation exists, and quarantines further continuity
writes for the run. A failure in diagnostic delivery bookkeeping has no
invented planner operation, so it emits a distinct store-failure event and the
same persistent degraded state instead. Later operations receive `failed`
receipts without touching the store.

A failure while resolving, recalling, or searching memory makes reads
indefensible too. The runtime quarantines both reads and writes, does not retry
the damaged boundary every observation, and exposes the exact persistent read
and write degradation reasons to subsequent planners. Gameplay continues from
current world evidence; the database is neither deleted nor recreated.

The event log retains full receipts. The next planner sees a bounded digest
with receipt ID, operation, origin, status, reason, result, authored and commit
revisions, plan/step origin, compact evidence, timestamp, and degraded state.
The planner-context manifest names only receipt IDs present in the final
payload. Tight budgeting always protects the newest rejected or failed digest.
Receipt visibility never reinforces durable memory.

Generic live configuration does not select a campaign. A run must provide an
explicit save-lineage campaign or an attested scenario must derive it.

## Consequences

A bad continuity operation cannot cancel a valid game action, and a damaged
writer cannot silently continue. The planner can correct the exact rejected
operation on its next turn. Reports count all four statuses separately and use
continuity terminology while retaining deliberate input compatibility for
older `memory_written` log events.
