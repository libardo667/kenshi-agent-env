# External review follow-up — 2026-07-27

Write-once. Extends `REPORT_20260727_external-review.md`, which stays exactly as
written; this file records what closed, what did not, and what changed in the
closing. Supersede by adding a later `REPORT_<date>_*.md`.

Scope: external read of the public checkout at `b9582dd`, nine commits after the
reviewed `9c012bd`. Portable evidence only — `pytest` (670 passed, 4 skipped),
`ruff`, `mypy src` (67 files), and byte-comparison of `export_docs`,
`export_native_capabilities`, and the schema exports. No Windows, native, or live
evidence was produced or is claimed.

## Closed

### Finding 1 — entity-scoped memory recall

Closed by `92c872b`; `docs/ADR_ENTITY_SCOPED_MEMORY.md`.

The implementation partitions rather than unions, which is stronger than the
finding asked for. `memory.py:137` restricts general recall to `target_id=''`, so
a bound fact reappears only through an exact current target ID and cannot leak
onto a same-named or later-session entity. A union would have shipped that leak.
`target_id` entered the UNIQUE constraint with a full table rebuild, because
`ALTER TABLE ADD COLUMN` leaves SQLite's old three-column constraint in force and
would silently merge one fact learned about two entities.

Two things closed that the finding did not name. `current_memory_target_ids()`
returns empty on a stale snapshot, so an obsolete observation cannot reactivate
bound memories. And `add()` no longer trusts `cursor.lastrowid`, which reports
the connection's last INSERT rather than the row an upsert actually touched.

The reported portable ablation moves repeated approaches from two to zero under
scoped recall, and the ADR claims no live or hosted-model improvement from it.
Approach-count-by-target is now an evaluator metric, so the live measurement is
instrumented before it is run.

**Residual.** `active` is still written `1` at `memory.py:39` and `:87` and never
set to `0`; salience still ratchets upward with no decay. The partition makes
this materially less urgent — entity memories hold a separate budget and can no
longer be crowded out — but the ADR is silent on that reasoning, so a later
reader sees an unexplained absence rather than a decision.

### Finding 5 — uncapped root prose

Closed by `71f889e`; `tests/test_docs_hygiene.py:53`.

`ROOT_DOC_CAPS` gives `LOOP_PROMPT.md` its own ceiling at its measured length,
and `ROOT_DOC_EXEMPTIONS` requires a stated reason. A further test rejects any
root document carrying neither, closing the omission *pattern* rather than the
instance: the next root document cannot be missed the way this one was.

### Finding 6 — cross-layer evidence axis

Closed by `71f889e`; `tests/test_capability_consistency.py`,
`docs/ADR_EVIDENCE_VOCABULARY.md`.

The axis is now a gate for the two rungs a portable test can reach, and the ADR
records that the fourth is enforced by review and not collapsible into
"supported". `GameplayCapabilities.json` generating
`GameplayCapabilities.generated.h` goes past the finding: capability names now
have one staleness-gated source of truth across Python and C++, closing the
declared/advertised gap at the language boundary where it opened.

## Still open

### Finding 2 — `single_step` dispatch carries no `ExecutionToken`

Unchanged in code. `continuous_executor.py:995` passes `token=token`; the three
`runtime.py` dispatch sites (390, 1387, 1556) do not.

It now carries a documented decision, which is a legitimate resolution. The
stated reason is not:

> Single-step dispatch does not build a token, because it has no plan
> assumptions or typed step preconditions to re-check.

The token also carries control-mode change, revision regression,
`human_input_detected`, and `emergency_stop_detected`. None are plan-shaped and
all apply to single-step, so the stated reason does not reach them.

The real defence is elsewhere and unstated: `config/live.burnin.yaml` sets
`allow_live_unpause_actions: false` and its objective requires staying paused
between actions. Single-step live is stop-motion against a paused world, which is
why an unbounded lease wait cannot convert stale authority into damage there.

That is the sentence a later reader needs, because it names where the gap
reopens — the moment anyone runs single-step against an unpaused world.

Failing invariant, if this is ever fixed rather than documented: a `single_step`
live dispatch whose authorizing observation became invalid during the lease wait
emits zero primitives.

### Finding 3 — the boundary rejects revision regression but not stall

Unchanged. `input_boundary.py:136` remains the only revision check, and
`observation.telemetry_stale` is still not consulted in `_decide()`.

### Finding 4 — rate and purchase budgets spent at validation

Unchanged. `safety.py:54` and `:139`; `:56` and `:145`. Low severity.

## Process note

The prior report landed byte-identical and stays that way, which is correct.
Three of its six findings are now closed, so a reader opening it cold will chase
resolved items. This file is the intended answer to that, and one like it should
be written whenever the balance shifts again. The failure the shape guards
against is someone concluding that editing the original would be simpler.

## Not investigated

Windows, native build, and live behaviour. The scenario-fixture and
affordance-aggregation subsystems landed in this window and were read for
structure only. Whether any of the 670 tests would fail if the behaviour it names
broke — the two strongest tests in this batch exist because that question was
asked by hand on the cases most likely to answer it wrongly, which is the
argument for asking it mechanically. Mutation testing is queue item 4 and remains
the only proposed gate here that is not self-referential.
