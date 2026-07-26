# ADR: bounded continuous planning

Companion decisions live in their own records: [world-state
stream](ADR_WORLD_STATE_STREAM.md), [input-boundary
authority](ADR_INPUT_BOUNDARY_AUTHORITY.md), [calibration
identity](ADR_CALIBRATION_IDENTITY.md), [stateful movement
options](ADR_STATEFUL_MOVEMENT_OPTIONS.md), [safety
supervisor](ADR_SAFETY_SUPERVISOR.md), [control modes](ADR_CONTROL_MODES.md).

## Context

`single_step` — one planner call, one action — is safe but cannot express
anything that spans several actions. Letting a model author a multi-step plan
means letting it author authority it should not have, unless acceptance is
deterministic and the executor keeps ownership of everything stateful.

## Decision: a plan is advisory until deterministic code accepts it

Acceptance requires strict schema validation at a supported version; exact
control-mode and `WorldStateRevision` binding; a bounded, acyclic, fully
reachable step graph; retry policy consistent with action idempotency; horizons
and risk declarations within configured maxima; action risk within the plan's own
declared budget; observable game time when a game-time budget is enforced; and
every plan assumption evaluating `true`.

`PlanPatch` carries plan ID, base plan version, and base world revision for
optimistic concurrency. During a stateful movement option only a future-only
patch matching the immutable planner snapshot may be staged, revalidated after
the option before it applies.

Hosted structured output follows the same state machine rather than always
asking for a plan: `single_step` requests `PlannerDecision`, idle continuous
requests `PlanEnvelope`, and an observation carrying `ActivePlanContext` requests
`PlanPatch`. `Condition.path` is a closed enum of the vocabulary the evaluator
actually implements, so unsupported shorthand is visible to structured
generation instead of failing at runtime.

## Decision: only causally later evidence can confirm an action

Telemetry, selected-character, target, freshness, and capability conditions
require a telemetry sequence strictly later than the action-start sequence;
other conditions require a later world revision. A value already present in the
action-start snapshot cannot confirm the action that followed it, even while
that snapshot is within its staleness threshold.

The evaluator preserves five outcomes, and only one of them permits progress:

| Result | As assumption/precondition | As success predicate | As failure predicate |
| --- | --- | --- | --- |
| `true` | permits progress | contributes to success | triggers failure |
| `false` | cancels before action | keeps waiting | does not trigger |
| `unknown` | cancels before action | keeps waiting | does not trigger |
| `unavailable` | cancels before action | keeps waiting | does not trigger |
| `stale` | cancels before action | keeps waiting | does not trigger |

Unknown, unavailable, and stale evidence never becomes implicit permission.

The live generic policy may rebase a plan across planner latency only when the
returned basis matches its immutable snapshot, the latest revision is causally
later, control mode and capabilities are unchanged, assumptions still hold, the
first action still binds to the same semantic reference, and no human-input or
emergency-stop evidence appeared. A rebase moves the basis and nothing else — no
policy code rewrites a model's steps or injects a recipe.

## Decision: the executor owns all mutable state

Active plan ID and version, active step and branch, remaining run/plan/risk
budgets, wall-clock and game-time horizons, retries and idempotency,
action-start revision and pending postconditions, cancellation and terminal
reason — none of it is planner prose. Before every action the executor checks
budgets, lets the reflex layer preempt, re-evaluates assumptions then step
preconditions, validates through the ordinary `ActionGuard`, reserves budget,
builds an `ExecutionToken` carrying that exact authorization, dispatches through
the normal environment path, commits the reservation when acceptance or delivery
is uncertain, and evaluates predicates only on later relevant revisions.

Environment errors after dispatch conservatively consume the reservation, so an
at-most-once action is never duplicated by an ambiguous failure.

## Consequences

Live continuous execution stays disabled in default profiles and requires an
implemented policy plus `--acknowledge-continuous-live`. Model-authored
postconditions are still not controller-owned proof that the action caused the
change — a later revision satisfies the temporal fence only. Contracts marked
`controller_verified` are the exception, and expanding that set is the ongoing
work. See [`GUIDE_LIVE_RUNS.md`](GUIDE_LIVE_RUNS.md) for what has actually been
demonstrated live.
