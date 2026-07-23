# Continuous planning contract

## Status and boundary

`planning.mode` selects one of two explicit scheduler contracts:

- `single_step` is the default regression baseline. One planner call returns one
  `PlannerDecision`.
- `continuous` accepts one bounded `PlanEnvelope` and may execute multiple
  guarded actions before another strategic planner call.

The continuous path is currently enabled only for mock and fake event-driven
environments. A live-labeled observation terminates the run before a strategic
call or action. This milestone proves the planning substrate; it does not claim
live Kenshi continuity.

## Plan authority

A plan is advisory until deterministic code accepts it. Acceptance requires:

- strict schema validation and a supported schema version;
- exact control-mode and `WorldStateRevision` binding;
- a bounded, acyclic, fully reachable step graph;
- retry policy consistent with action idempotency;
- plan horizons and risk declarations within configured maxima;
- action risk within the plan's own declared budget;
- observable game time for enforcement of the game-time budget;
- every plan assumption evaluating to `true`.

`PlanPatch` carries plan ID, base plan version, and base world revision for
optimistic concurrency. It is schema-exported and parsed by scripted planners,
but application to an active plan is intentionally deferred. A patch returned
without an active matching plan is rejected normally, not treated as executable
work.

## World revisions and causal confirmation

`WorldStateRevision` carries telemetry sequence, frame sequence, capability
epoch, and local monotonic observation time. A plan basis must match the
observation used for acceptance.

Continuous mode publishes observations through one bounded
`WorldStateStore`. The store rejects revision regression and state changes
without a revision advance, detects duplicate telemetry sequences, carries
forward the last validated visual frame on telemetry-only updates, and feeds
isolated subscriber queues. `wait_for(predicate, after_revision=R)` subscribes
to that stream and cannot succeed from `R` or an earlier revision.

Postconditions use the relevant causal channel:

- telemetry, selected-character, target, freshness, and capability conditions
  require a telemetry sequence strictly later than the action-start sequence;
- non-telemetry conditions require a later world revision.

A value already present in the action-start snapshot cannot confirm that action,
even when the snapshot remains below its wall-clock staleness threshold.
Each plan command also has a deterministic command ID. Its receipt records the
action-start revision, canonical store completion revision, and whether that
revision causally advanced. Raw environment state rejected by the store cannot
become a successful action outcome.

## Bounded history, events, and identity

The store bounds snapshot history, semantic deltas, transient-event journal,
command history, and subscriber queues. Slow subscribers drop their oldest
queued update with an explicit metric; the transport is never polled once per
consumer. Shutdown wakes subscribers and cancels the pump without leaving
owned tasks.

Nearby source IDs such as `nearby:0` are treated as weak evidence. The store
issues process-local lifetime IDs and matches subsequent observations using
typed fingerprints and spatial continuity, including same-name ordinal swaps.
Ambiguous matches are journaled. Disappearance closes a lifetime only while an
authoritative entity-list capability is present; capability withdrawal keeps
the prior lifetime unresolved. These IDs are not native Kenshi stable handles.

## Typed condition outcomes

The condition language is an allowlisted set of scalar field, capability, and
telemetry-freshness predicates. Field paths have deterministic capability gates
even if planner output omits `required_capabilities`.

The evaluator preserves five outcomes:

| Result | Assumption or precondition | Success predicate | Failure predicate |
| --- | --- | --- | --- |
| `true` | permits progress | contributes to success | triggers failure |
| `false` | cancels before action | keeps waiting | does not trigger |
| `unknown` | cancels before action | keeps waiting | does not trigger |
| `unavailable` | cancels before action | keeps waiting | does not trigger |
| `stale` | cancels before action | keeps waiting | does not trigger |

Success requires every success predicate to become `true`. Unknown,
unavailable, and stale evidence never becomes implicit permission. If acceptable
evidence does not arrive within the step or plan budget, the step fails and the
executor follows its bounded failure branch or requests a replan.

## Executor-owned state

The executor, not planner prose, owns:

- active plan ID and version;
- active step and success/failure branch;
- remaining run and plan action budgets;
- pointer, purchase, and native-assisted risk budgets;
- wall-clock and game-time horizons;
- retries and idempotency;
- action-start revision and pending postconditions;
- cancellation and terminal reason.

Before every action it:

1. checks remaining run, plan, wall-clock, game-time, and risk budgets;
2. lets the deterministic reflex layer preempt the future plan;
3. re-evaluates all plan assumptions;
4. re-evaluates that step's capabilities and preconditions;
5. validates the action with the ordinary `ActionGuard`;
6. reserves plan action/risk budget;
7. dispatches through the ordinary environment path;
8. commits the reservation when accepted or delivery is uncertain, and releases
   it only after a definitive no-execution rejection;
9. evaluates failure and success predicates only on later relevant revisions;
10. follows a declared branch, completes, aborts, or requests a bounded replan.

Environment errors after dispatch conservatively consume the reservation so an
at-most-once action is not duplicated.

## Lifecycle and replay

Append-only logs carry plan ID, plan version, step ID where applicable, world
revision, control mode, reason, and evidence for plan lifecycle events:

```text
plan_proposed
plan_accepted
plan_rejected
plan_started
plan_step_ready
plan_step_started
plan_step_progress
plan_step_succeeded
plan_step_failed
plan_step_cancelled
plan_patch_requested
plan_completed
plan_aborted
safety_preempted
```

Budget reservation, commit, and release are logged separately. The evaluator
reports strategic calls, plan and step outcomes, budget transactions, and
actions per strategic planner call. It also reports causal command-receipt
coverage, sequence stalls, transient-event retention/loss, subscriber drops,
pump errors, revision failures, entity lifetime counts, and command mismatches.
`replay_plan_lifecycle` reconstructs each plan's terminal status and succeeded,
failed, and cancelled step IDs.

## Proven P1-P2 cases

Portable tests and the built-in heuristic prove:

- one strategic call executes `pause=false` and `speed=3` through the normal
  guard/environment path;
- a changed future precondition prevents the second action from reaching the
  environment;
- a deterministic safety reflex cancels the future action and executes through
  the normal path;
- an unchanged but fresh revision cannot certify a postcondition;
- stale plan output executes nothing;
- invalid graph topology, unsafe retries, excessive horizons, and policy budgets
  are rejected;
- lifecycle replay reaches the logged terminal state.
- multiple subscribers receive isolated copies of the same validated update;
- a transient event remains queryable after it leaves the latest snapshot;
- telemetry-only ingest preserves the last visual revision;
- duplicate, regressing, conflicting, and capability-withdrawal cases remain
  explicit;
- stable lifetime IDs survive ordinal reorder, including duplicate names at
  distinct positions;
- a planner response made stale while the observation pump advances is rejected
  before execution;
- command receipts distinguish later causal evidence from an unchanged,
  inconclusive revision.

The next architectural step is an independent safety supervisor. Until that
exists, there is no claim of planner/executor overlap or blocked-planner safety
preemption.
