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
live-example and legacy burn-in profiles use `disabled`. The short dialogue and
long-form profiles name `dialogue_interaction_v1`, and live input still requires
the ordinary execution gate, native-assisted acknowledgement where applicable,
and the separate `--acknowledge-continuous-live` flag.

## Generic live policy

`dialogue_interaction_v1` is the historical name of the current generic live
policy. It prescribes no Barman, vendor, dialogue, food, or step sequence. It
accepts planner control actions plus actions from the authoritative contract
catalog when the current observation and control mode advertise them:

- approach an exact current dialogue target;
- move to an exact nearby character;
- walk a declared bounded bearing/distance without inventing a target; its
  keyed monitored option waits for the exact native vector to complete;
- activate one unique visible control or dismiss the current screen;
- purchase, sell, equip, or scroll one exact current window/cell;
- use one allowlisted reversible Kenshi binding.

Raw key, hotkey, cursor, click, and scroll primitives are rejected because a
bare input carries no evidence about what it would activate. Each semantic
contract owns its required capabilities, control modes, pointer class,
native-assisted flag, risk, idempotency, reference fields, execution route, and
verification paths.

Only the step about to execute must bind immediately. Later steps may refer to
state created by earlier steps—for example, activating a closing reply in a
dialogue that an approach has not opened yet—but each binds when reached and is
rebound inside the input lease. At-most-once actions cannot be retried. Every
semantic step must include a success condition on causally later world state;
purchases and sales specifically require a money condition so an ineffective
gesture cannot report success.

This is a temporal and schema-validity fence, not a general effect engine. A
later revision proves that pre-action state was not reused, but the planner
still chooses most condition paths, operators, and expected values. Contracts
do not yet derive effect predicates from the bound action and its pre-action
baseline. A correlated later change can therefore be mistaken for the intended
effect; purchase/sale money checks are narrower, but even they do not prove the
full inventory-side transition.

The concrete camera case illustrates the gap: `camera.position` is a capability
name in the condition vocabulary, not camera X/Y/Z. A field condition using
that path is normalized to capability presence and can pass on a later
telemetry tick even when the camera did not move.

The former `food_procurement_v1` policy is retired. Its useful guarantees now
live in the generic contracts: exact cell/owner binding, current item identity
and value, optional task markers, operator spending preferences, at-most-once
delivery, and causal money verification.

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
configured base, per-step increment, and ceiling. The long-form profile
defaults to a non-reasoning OpenRouter model selected by a five-run
schema-validity/latency benchmark; other profiles retain configurable OpenAI
reasoning effort.

`Condition.path` is a closed schema enum containing the field and capability
vocabulary the evaluator implements. This makes unsupported shorthand visible
to structured generation. Cross-field rules remain deterministic code:
freshness uses `operator=equals` and `expected=true`, and target paths carry the
exact stable target ID. Every supported operator compares against an explicit
expected value; the unused and structurally ambiguous `exists` operator is not
part of the contract.

Both hosted adapters send a schema every provider can consume. When an
OpenRouter provider refuses the compiled dialect, the adapter asks for the same
JSON shape in the prompt and validates it locally. A malformed answer, wrong
response type, or unmatched patch counts toward `max_consecutive_replans`
instead of ending an otherwise safe continuous session immediately.

## World revisions and causal confirmation

`WorldStateRevision` carries telemetry sequence, frame sequence, capability
epoch, and local monotonic observation time. A plan basis must match the
observation used for acceptance.

Portable generic output that becomes stale during a strategic call is rejected.
The live `dialogue_interaction_v1` policy may rebase when:

- the returned basis matches its immutable planner snapshot;
- the latest revision is causally later;
- control mode and advertised capabilities are unchanged;
- plan assumptions still evaluate to `true`;
- the first action still binds to the same current semantic reference;
- no human-input or emergency-stop evidence appeared.

Later steps need not bind before their predecessors create their state, but they
must bind when reached. A successful `plan_rebased` event moves only the basis;
policy, topology, budgets, conditions, ordinary guard validation, and final
in-lease rebinding still run. No policy code rewrites a model's step sequence or
injects a scenario recipe.

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
7. builds a bounded `ExecutionToken` carrying that exact authorization;
8. dispatches through the ordinary environment path;
9. commits the reservation when accepted or delivery is uncertain, and releases
   it only after a definitive no-execution rejection;
10. evaluates failure and success predicates only on later relevant revisions;
11. follows a declared branch, completes, aborts, or requests a bounded replan.

Environment errors after dispatch conservatively consume the reservation so an
at-most-once action is not duplicated.

The generic policy recognizes that scrolling is intrinsically safe to retry
and that non-toggle camera/speed bindings can be repeated. The shared plan
validator is currently narrower and accepts retry budgets only on run-control
actions, however, so hosted plans should express another semantic scroll or
binding as an explicit later step rather than setting `retry_budget`.

## Final input-boundary revalidation

Step 7 above is validated before the environment waits for a polite input turn.
`LiveEnvironment` then acquires an input lease whose wait is unbounded by
design, so that evidence can be obsolete when the wait ends.

The `ExecutionToken` closes that window. It carries the plan/step/command
identity, control mode, validated revision, the plan's assumptions, the step's
preconditions, and a deferred accessor to the world-state store. Inside the
acquired lease — after the calibration recheck and immediately before the first
primitive — the environment re-reads the latest canonical observation and
rejects the dispatch when:

- no canonical observation is available;
- the canonical revision regressed;
- the control mode changed;
- the observation carries `human_input_detected` or `emergency_stop_detected`;
- any assumption or precondition is no longer `true`.

The check calls the same `evaluate_conditions` machinery used before dispatch,
so `unknown`, `unavailable`, and `stale` block input exactly as `false` does. A
rejection emits zero primitives and returns `accepted=false`, `executed=false`,
`primitive_actions=0`, and `error_type="InputBoundaryRejected"`, which releases
the reservation through the ordinary definitive-rejection path.

Every token-bearing dispatch attaches an `InputBoundaryReport` to its receipt
with the decision, reason, lease wait, plan/step identity, validated and
boundary revisions, and bounded evaluations. The executor emits
`input_boundary_revalidated` or `input_boundary_rejected` accordingly.

The proven client width/height recheck still runs first and still fails closed
by raising, so it is not demoted into a boundary rejection. Native-assisted
commands keep their stronger issue-time DLL fences unchanged; the boundary is
additive and does not alter command-ID or acknowledgement semantics. Single-step
dispatch builds no token, because it has no plan assumptions or typed step
preconditions to re-check.

Portable deterministic tests block inside a fake lease, publish conflicting
state, and prove zero primitives are emitted. Supervised generic live runs carry
successful `InputBoundaryReport` evidence from real leases; deliberately
changing an authorization fact during a real lease remains an unrun destructive
test case.

## Calibration identity for pointer actions

A profile-calibrated click depends on more than client size. Each action is
classified as coordinate-independent, semantic-current, profile-calibrated, or
unsupported; only profile-calibrated actions require a match. `CalibrationIdentity`
models client size, window mode, UI scale, DPI transform, keymap, and the
profile/macro hashes, each nullable. `evaluate_calibration_identity` compares
only the fields the profile declares against what the controller observes and
returns `not_required`, `matched`, `mismatched`, or `unknown`. A declared field
the host cannot read is `unknown` and blocks input — a null is never treated as
agreement — and `unknown` outranks `mismatched` so an incomplete identity is
never reported as a clean block.

The report is attached to every pointer receipt and carried into the boundary
by the `ExecutionToken`, so a calibration change during the lease wait is
rejected by the same fence as a changed precondition. With a token present that
rejection is graceful (zero input, reservation released); on the tokenless path
a mismatch raises, preserving the exact client-size brake. Only client width and
height are observable today; the remaining fields are modelled and enforced but
declaring one the controller cannot read safely refuses input.

## Stateful movement option and future-only patching

When enabled, a configured movement-pulse skill is adapted into a
`StatefulMovementOption`. Contracted `approach_dialogue_target` always uses a
`StatefulApproachOption`; it issues or adopts one exact native order and
monitors progress until exact dialogue, arrival, target/threat failure,
cancellation, or timeout. Stop-motion movement requires a capable
confirmed-paused start and owns bounded unpause/re-pause pulses. The long-form
profile instead monitors ordinary movement in an intentionally unpaused world.
Every option owns its task/subscription and releases both on every terminal
path.

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

The long-form and short dialogue profiles enable this advisory so strategic
work can overlap monitored movement. The legacy single-step burn-in profile
keeps it disabled. No advisory may mutate the running option.

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
input_boundary_revalidated
input_boundary_rejected
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
`input_boundary_revalidations` and `input_boundary_rejections` count the final
post-lease fence separately from pre-lease policy rejections, so a run can
distinguish input that was never authorized from input that lost its authority
while the agent waited for a quiet turn.
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

## Proven cases

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
- the generic live policy rebases planner latency only while current contracts,
  assumptions, control mode, capabilities, and input ownership remain valid,
  and rejects the same response after an authorizing fact changes;
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
- generic action contracts reject missing/ambiguous references, unsafe retries,
  raw controller primitives, and action claims without causal verification;
- exact dialogue approach and current-control activation compose in one live
  plan; supervised runs also cover purchases, screen dismissal, game bindings,
  inventory/trade navigation, and exact-character local movement. Sale/equip
  binding and money checks have portable coverage grounded in the observed
  live UI semantics, but are not claimed as completed live sale/equip proofs.
  Targetless directional movement now has a portable end-to-end cross-language
  proof, pinned native build, and one exact live Kenshi acceptance/completion
  proof. Other routes are not inferred from that single smoke.

Option conversion remains bounded to configured movement skills, contracted
exact-target approach, and targetless native direction. The latter uses a
separate keyed acknowledgement monitor because a bare destination has no
nearby-entity distance to observe. Live continuous work remains disabled in
default profiles and requires an implemented policy plus explicit
acknowledgement. Stable native identity and causal bridge acknowledgements use
the same caller-owned command/revision semantics; supervised results remain
version- and host-specific.
