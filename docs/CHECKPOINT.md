# Checkpoint: EvoGen session-event disposition inventory

Goal 9 freezes the current Kenshi Agent Environment session-event vocabulary
before any exporter or logger migration is attempted. The inventory is derived
from the source that can reach `SessionLogger.write`; a separate reviewed
authority assigns every discovered event exactly one EvoGen disposition.

This is a source and test checkpoint. It makes no new live-game claim, changes
no environment or evaluator behavior, and does not convert the logger to an
EvoGen trajectory.

## Repository and authority

```text
parent commit          a18af271634a767f90a9e9356f1f8bbc50411e8f
integration branch     main
starting tree          clean
EvoGen counterpart     f53298ff15ca1bfc2c9559c8c77568b153c5649e
producer protocol      2.0.0
disposition schema     1
```

The preceding KAE stage closed the 120-turn native survival soak and
human-facing charged-turn overlay at the parent commit. Its run and recovery
fixture claims remain in that committed checkpoint history; G09 neither
extends nor weakens them.

## Source-derived denominator

The current source-local denominator is **89 outer session event types** across
**127 producer records**. The extractor follows direct logger writes and the
current wrapper/callback routes through `PlanEventRecorder`, operation
progress, monitored options, budget reservation, continuity reads, and control
ownership. Repeated producer records remain in the canonical AST fingerprint,
so adding another emission of an already known event still stales the generated
artifact.

The generated inventory also retains every reviewed string boundary. Seven are
open event-type pass-through sinks whose current in-repository callers resolve
to the 89-event denominator. Nine are reviewed non-event writes such as native
request files, telemetry snapshots, speech input, and console output. A new
writer receiver or logger alias therefore fails closed instead of escaping a
name-based scan.

`SessionLogger.write(event_type: str)` itself remains an open string API. G09
does not pretend this makes the global event domain closed; it proves the
current in-repository producers and names the open boundary explicitly.

## Reviewed dispositions

Every event has exactly one of four dispositions in
`docs/reconstruction/session_event_dispositions.json`. The generated artifact
at `docs/generated/SESSION_EVENT_DISPOSITIONS.json` combines that reviewed
authority with the source records and fingerprint.

The 89 rows divide into:

- **22 exact EvoGen events.** Run start/finalization, observations and world
  deltas, typed decisions, action receipts, later outcomes, bounded recovery,
  and concrete failures map to explicit EvoGen event kinds.
- **54 subject-only raw evidence events.** These retain KAE ontology and
  lifecycle evidence without claiming a one-to-one normalized meaning.
- **9 derived summaries.** Planner, plan-outcome, safety, and world-state
  aggregates remain diagnostic summaries rather than trajectory transitions.
- **4 intentionally ignored normalized events.** Campaign scope belongs in run
  metadata, while action-budget reserve/commit/release records are internal
  accounting rather than capability events.

The map is conservative at the causal boundaries:

- `planner_context_prepared` is not an exact affordance set because missing
  telemetry or preparation failure can make an apparent empty set incomplete;
- input-boundary events are not exact bindings without joining the full
  operation and target;
- `continuity_receipt` and `fieldbook_receipt` multiplex accepted writes,
  rejected writes, and no-ops, so they are not unconditional memory updates;
- ownership events require payload-state interpretation before they can prove
  human intervention;
- `world_state_event` is a heterogeneous nested journal, not a dispatch event;
- plan, option, and affordance success does not prove a world or goal effect;
  and
- KAE currently has no exact outer event for native dispatch, `goal_blocked`,
  or `goal_achieved`.

## Freshness and adversarial proof

The source inventory uses deterministic AST records containing stable source
paths, owners, sink kinds, event types, and canonical open-boundary
expressions. It contains no line numbers, timestamps, or Git hashes. Comment
and blank-line changes preserve the fingerprint; producer, owner, sink,
open-boundary, or event-domain changes do not.

Focused tests prove that:

- the reviewed and generated event sets are exactly the 89 source events;
- a new direct event or plain/annotated control-ownership enum value fails
  until reviewed;
- unresolved and partially resolved dynamic producers fail closed;
- missing, payload-only, splatted, direct-alias, bound-method, destructured,
  assigned-`getattr`, and renamed operation-progress routes cannot evade the
  inventory;
- duplicate and extra semantic rows are rejected;
- function defaults and decorators remain inside the scanned source boundary;
- duplicate resolved emissions change the source fingerprint even when the
  event-name set does not, while duplicate open boundaries fail freshness; and
- comment-only source changes do not create false freshness churn.

Raman independently reviewed all 89 semantic dispositions against the KAE
payload producers and EvoGen event meanings and found no G09 blocker. The
review preserves downstream constraints: digest observations remain
incomplete, `world_state_update` is delta metadata rather than a complete
effect, overloaded receipts require payload-aware filtering, and no current
event proves native dispatch, `goal_blocked`, or `goal_achieved`.

Lovelace and Gauss independently attacked the source denominator and freshness
boundary. Their initial reviews found mixed-condition, malformed/splatted,
duplicate-open-sink, function-default, annotated-enum, bound-method,
destructured, and dynamic-`getattr` escapes. The corrected extractor names the
existing `OperationExecutionService.submit` bound callback explicitly, and
both final re-reviews pass the repaired 89-event / 127-record / 16-open-boundary
candidate. Candidate-author tests were not used as certification.

The portable gate now regenerates this artifact before schemas, documentation,
and the full suite. Stale bytes or an incomplete reviewed authority fail the
same local and hosted gate used by the rest of KAE.

## Completion boundary and next goal

G09 changes no `SessionLogger` signature, event payload, fixture, protocol,
runtime behavior, environment behavior, or evaluator behavior. In particular,
there is still no logger-owned serialized event sequence. That migration and
its concurrency stress proof belong to G10; source log sequence must remain
distinct from environment step index and telemetry revision.

G12 still owns a dedicated authoritative affordance-set event, and G14 owns the
production KAE exporter. This inventory is their reviewed input, not an early
implementation of either goal.

## Verification

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache uv run --frozen --extra dev pytest -q \
  tests/test_session_event_dispositions.py tests/test_checkpoint_freshness.py
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
git diff --check
```

The focused disposition/freshness suite and the complete portable gate pass on
the final G09 candidate. The portable gate includes locked dependency sync,
Ruff, strict mypy, research validation, event-map/schema/document generation,
the full pytest suite, and whitespace checks.
