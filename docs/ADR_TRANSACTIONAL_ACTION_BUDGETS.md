# ADR: Finalize action budgets from delivery evidence

Status: accepted; extends `ADR_UNIFIED_INPUT_BOUNDARY_AUTHORITY.md`

## Context

The action guard historically incremented its per-minute primitive counter and
per-run purchase counter during validation. A later in-lease rejection could
prove that zero input escaped, and the continuous plan ledger would release its
reservation, but the global guard counters stayed spent. The two accounting
layers therefore disagreed about the same dispatch.

Charging only after successful execution would create the opposite safety bug:
an exception, cancellation, partial delivery, or mismatched receipt cannot prove
that input was absent. At-most-once work must remain spent under ambiguity.

## Decision

- Initial policy validation creates one `ActionBudgetReservation` covering the
  exact primitive count and purchase count. Pending reservations count against
  both limits so concurrent validation cannot oversubscribe authority.
- Both schedulers carry that reservation through preparation and dispatch.
  Revalidation checks current policy without creating another reservation.
- Release global and plan reservations only when a command-matched receipt says
  both `accepted=false` and `executed=false`, or when a failure occurs before
  dispatch is attempted.
- Commit both layers when a receipt is accepted or executed, or when delivery is
  ambiguous because of cancellation, environment error, partial input, or
  command-identity mismatch.
- A mismatched rejection receipt is not evidence about the active command. It
  commits conservatively, fails the active command, and emits an attributable
  plan-budget event.
- Direct `ActionGuard.validate()` callers retain immediate-commit behavior.
  Runtime paths use explicit reserve/commit/release ownership.
- The dedicated safe-pause path remains outside the rate counter so prior
  activity cannot lock out deterministic cleanup.

## Consequences

Rate and purchase limits now describe input authority rather than model
proposals. A proven zero-input rejection cannot exhaust the run, while an
uncertain purchase cannot become executable twice.

Portable tests hold the entire configured rate budget across a blocked lease,
prove that rejection returns it in both schedulers, and prove that replacing
release with commit makes those tests fail. Purchase tests prove pending
reservations prevent oversubscription, release restores capacity, and commit
exhausts the configured per-run allowance.

No live rejection was induced. Existing successful live receipts already take
the conservative commit branch; this change grants no new action authority.
