# External review follow-up 3 — 2026-07-27

Write-once. Extends `REPORT_20260727_review-followup-2.md`; all earlier reports
remain unchanged. Supersede this file with a later dated report.

Scope: portable review of the transactional action-budget implementation. No
Windows, native, or supervised-live evidence is claimed.

Portable gates: 720 tests passed and 4 skipped; `ruff`, `mypy src`, and fixed-seed
single-step and continuous mock runs passed.

## Finding 4 now closed

Closed by `ADR_TRANSACTIONAL_ACTION_BUDGETS.md`.

Rate and purchase authority now enter a pending reservation after policy
validation. Both schedulers finalize that reservation from the same delivery
verdict as the continuous plan ledger:

- a command-matched `accepted=false`, `executed=false` receipt releases;
- accepted or executed input commits;
- cancellation, environment error, and command mismatch commit conservatively;
- a proven pre-dispatch failure releases.

Pending primitive and purchase counts participate in limit checks, so delaying
commit does not permit concurrent oversubscription. Boundary revalidation remains
read-only with respect to both budgets. Direct guard callers preserve their prior
immediate-commit contract.

The implementation also closes a sibling ambiguity bug. The continuous executor
previously classified a receipt as a definitive rejection before checking its
command ID, which could release the plan ledger on evidence belonging to another
command. Receipt identity is now part of the release predicate, and a mismatch
fails the active command with an attributable committed-budget event.

## Adversarial evidence

Before implementation, a single-step boundary rejection exhausted all three
configured primitive slots, and no releasable purchase reservation existed.
Both tests were observed red.

After implementation, replacing `release()` with `commit()` made the
single-step, continuous, and purchase conservation tests all fail. Restoring the
source made the focused and subsystem suites green. The tests therefore measure
the accounting behavior rather than merely enumerating successful calls.

## Review balance

All six numbered findings in `REPORT_20260727_external-review.md` are now closed
by code, generated gates, or explicit decisions. The entity-memory residual
recorded in follow-up 2 remains: `active` has no deactivation path and salience
does not decay. That is not implemented forgetting.

Mutation testing remains a separate queue item. The manual mutation above proves
this slice's release predicate only, not suite-wide mutation adequacy.

## Not investigated

Live boundary rejection, real purchase delivery, rate-window behavior across a
wall-clock minute, or mutation coverage outside this transaction.
