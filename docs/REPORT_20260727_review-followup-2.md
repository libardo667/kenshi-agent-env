# External review follow-up 2 — 2026-07-27

Write-once. Extends `REPORT_20260727_review-followup.md`; both earlier reports
remain unchanged. Supersede this file with a later dated report.

Scope: review of the implementation through `663ba35`. Portable evidence only:
718 tests passed and 4 skipped; `ruff` and `mypy src` passed. No Windows, native,
or supervised-live evidence is claimed here.

## Findings now closed

### Finding 2 — `single_step` lacked an execution token

Closed by `82a69f0`.

Both schedulers now construct the same `ExecutionToken` for ordinary dispatch.
The single-step integration test blocks inside a live-shaped lease, makes the
authorizing observation over-age, and proves the receipt rejects with zero
primitives. This closes the class the prior rationale missed: freshness,
calibration, control mode, action/reference authority, human input, and emergency
stop are boundary concerns even when no plan conditions exist.

### Finding 3 — stall or age could pass the boundary

Closed by `82a69f0` and `663ba35`;
`ADR_INPUT_BOUNDARY_AUTHORITY_V2.md`.

The first commit made the canonical stale bit independently withdraw authority.
The second carries the live reader's configured maximum age into the token and
rejects unknown ceiling, unknown age, and age above the ceiling. Revision
identity remains permitted only inside that time bound, which preserves normal
duplicate samples without turning an unbounded lease into unbounded authority.

The adversarial test was observed red before implementation: unknown and
3.001-second ages emitted three pointer primitives. Reversing the implemented
comparison also made it red, then the source was restored. The invariant is
therefore sensitive to the behavior it names.

## Still open

### Finding 4 — rate and purchase budgets move at validation

Unchanged and still low severity. A boundary-rejected action emits zero
primitives but has already consumed proposal-rate or purchase allowance. The
system fails safe, but configured counters still describe validated proposals
rather than execution evidence.

Failing invariant: a proven zero-input boundary rejection does not spend an
execution budget, while ambiguous or partially delivered input remains spent.

## Residual from finding 1

Entity-scoped recall remains closed. Memory `active` state still has no
deactivation path, and salience still ratchets without decay. The partitioned
entity budget limits the immediate harm; neither behavior should be described as
implemented forgetting.

## Not investigated

Windows, native build/load, supervised-live behavior, and mutation coverage
outside the changed comparison. The live resource run occurring in the same
work period is separate evidence and does not strengthen these portable claims.
