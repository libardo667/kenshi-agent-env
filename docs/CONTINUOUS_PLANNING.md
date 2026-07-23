# Continuous planning contract

## Status and boundary

`planning.mode` selects one of two explicit scheduler contracts:

- `single_step` is the default regression baseline. One planner call returns one
  `PlannerDecision`.
- `continuous` accepts one bounded `PlanEnvelope` and may execute multiple
  guarded actions before another strategic planner call.

Mock and fake event-driven environments may use the general bounded contract.
Live observations terminate before a strategic call unless
`planning.live_execution_policy` names an implemented policy. The default and
live-example profiles use `disabled`; the calibrated profile names
`food_procurement_v1`, and live input still requires the ordinary execution
gate, native-assisted acknowledgement, and the separate
`--acknowledge-continuous-live` flag.

## Conditional live policy

`food_procurement_v1` is a deterministic action grammar, not general live
planning permission. It accepts only:

- world: approach the exact confirmed vendor, choose exact option zero, inspect
  one item;
- exact dialogue: choose option zero, then inspect;
- trade without authoritative tooltip: inspect once;
- trade with visible tooltip and source bounds: buy that one item once.

Every action binds the same stable target ID, is at-most-once with no retry,
and rechecks pause, one exact selection, phase-specific screen/dialogue/owner
evidence, and the full capability set. A purchase additionally copies exact
item name and price from the current tooltip. Its click must fall inside the
tooltip's current source-widget bounds; this is stronger and less
history-dependent than trusting a claimed earlier cursor coordinate. Success
requires the exact later money debit, one additional selected-character food
item, and `paused=true`.

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
optimistic concurrency. A patch returned without an active matching movement
option is rejected normally. During a stateful movement option, only a
future-only patch matching the immutable planner snapshot may be staged, and it
is revalidated after the option before application.

Hosted structured output follows the same state machine rather than always
requesting a plan envelope: `single_step` requests `PlannerDecision`, idle
continuous mode requests `PlanEnvelope`, and an observation with
`ActivePlanContext` requests `PlanPatch`. The OpenAI request's
`max_output_tokens` is computed from that expected response complexity using a
configured base, per-step increment, and ceiling. The current live profile uses
medium reasoning effort and budgets 10,240/8,192/6,144 tokens for the
world/dialogue/trade food phases respectively.

`Condition.path` is a closed schema enum containing the field and capability
vocabulary the evaluator implements. This makes unsupported shorthand visible
to structured generation. Cross-field rules remain deterministic code:
freshness uses `operator=equals` and `expected=true`, and target paths carry the
exact stable target ID. Every supported operator compares against an explicit
expected value; the unused and structurally ambiguous `exists` operator is not
part of the contract.

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

## Stateful movement option and future-only patching

When enabled, a configured movement-pulse skill is adapted into one
`StatefulMovementOption`. The macro and environment still own the proven
movement mechanics; the adapter adds explicit prepare, start, state-stream
progress, success, failure, and idempotent cancel states. Preparation requires a
capable confirmed-paused start. The option owns one named action task and one
bounded store subscription, both released on every terminal path.

While the option runs, one concurrent strategic call receives an immutable
observation with `ActivePlanContext`: plan ID/version, active and completed step
IDs, objective, and remaining action count. It may return only a `PlanPatch`.
The executor stages the patch only when:

- plan ID and version match the active plan;
- patch revision exactly matches both the planner snapshot and still-current
  store revision;
- no replacement step reuses an active or completed step ID;
- the replacement graph is finite, acyclic, reachable, and policy-valid;
- its declared actions fit remaining action and risk budgets.

Staging never changes the running option. After the movement succeeds and its
transition is recorded, the executor validates again against latest state,
unchanged assumptions, remaining run/plan/risk/time budgets, and the protected
completed-step set. Only then does the plan version advance and its replacement
future entry become eligible. Each replacement action still passes ordinary
precondition and guard checks. Wrong-type, failed, late, stale, mismatched, or
invalid advisory output is logged and discarded; the original branch remains.

Cancellation keeps the existing P3 contract: dispatched movement remains spent
and inconclusive, the option reaches cancelled/failed once, and the independent
supervisor owns the single causally verified safe-pause cleanup.

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
plan_patch_staged
plan_patch_rejected
plan_patched
plan_completed
plan_aborted
safety_preempted
option_prepared
option_started
option_progress
option_succeeded
option_failed
option_cancelled
```

Budget reservation, commit, and release are logged separately. The evaluator
reports strategic calls, plan and step outcomes, budget transactions, and
actions per strategic planner call. It also reports causal command-receipt
coverage, native acknowledgement/final-status counts and sequence lag, sequence
stalls, transient-event retention/loss, subscriber drops, pump errors, revision
failures, entity lifetime counts, and command mismatches.
Supervisor preemptions, strategic/executor cancellations, cleanup starts,
completions/failures, terminal states, and cleanup success percentage are
reported separately from planner/reflex counts.
Future patches, concurrent advisory discards, option lifecycle counts, and
option success percentage are also separate metrics.
`replay_plan_lifecycle` reconstructs each plan's terminal status and succeeded,
failed, and cancelled step IDs.

## Independent safety supervisor

Continuous mode starts one deterministic supervisor subscriber before starting
the observation pump. It does not call a model. It latches the first detected
reflex, stale telemetry, consecutive sequence stall, pause-capability
withdrawal, exact `human_input_detected` event, or unexpected unpause without an
active authorized plan/command. Active authorization is copied into each
`StoreUpdate`, preventing queued old updates from being judged against newer
mutable executor state.

The scheduler races strategic planning and plan execution against that latch.
When the supervisor wins, it cancels the obsolete task once. Cancellation
during dispatch commits the reserved budget and records the command
inconclusive because delivery cannot be disproved. The supervisor then
terminates or issues one `PauseAction(paused=true)` through a narrow guard path.
That path preserves the action allowlist and control-mode checks, permits no
unpause, and bypasses only the per-minute action counter.

A pause input receipt is not enough. Cleanup completes only when a causally
later revision exposes `game.pause` and confirms `paused=true`. Timeout,
execution error, policy rejection, command mismatch, or lost capability emits
`safety_cleanup_failed` and a terminal failure/unverified state. The supervisor
and observation pump are stopped before the store shuts down, and repeated
preemption/stop calls are idempotent.

## Proven P1-P4 cases

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
- an unsafe update cancels a deliberately blocked planner and produces one
  causally confirmed pause;
- an unsafe update cancels an in-flight fake movement, records its delivery as
  inconclusive, clears its plan, and performs one confirmed cleanup;
- an accepted pause input without later paused evidence produces cleanup
  failure rather than a false safe-state claim;
- pause-capability withdrawal stops without treating missing capability as a
  false value, and consecutive duplicate revisions preempt deterministically;
- repeated preemption and shutdown are idempotent and release the supervisor
  subscription.
- one concurrent strategic advisory returns while fake movement remains active;
  its exact future-only patch is staged but cannot execute before movement
  succeeds and latest-state/budget revalidation passes;
- patch replay advances the plan version and preserves the completed movement
  step without restarting it;
- a pump update that makes the advisory basis stale rejects the patch and
  executes the original future step;
- an exact human-input stream event cancels movement, records its command
  inconclusive, and reaches one confirmed supervisor pause;
- option success, failure, cleanup failure, cancellation, and repeated
  cancellation release their owned tasks/subscriptions.

The current option conversion remains deliberately narrow: only configured
movement-pulse skills use it. Live continuous work is disabled by default and
restricted to `food_procurement_v1`. Stable native identity and causal bridge
acknowledgements use the same caller-owned command/revision semantics; the
deterministic live-shaped proof does not replace supervised Windows/Kenshi
latency and end-to-end validation.
