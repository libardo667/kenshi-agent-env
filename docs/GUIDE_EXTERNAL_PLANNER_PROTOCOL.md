# External planner protocol

The subprocess adapter starts a new child process for each decision. This is
slower than a persistent RPC service but gives a simple, isolated contract for
early experiments.

## Request

The child's stdin receives exactly one UTF-8 JSON line containing the complete
`Observation` schema. `screenshot_path` refers to a local file available to the
child process.

## Response

The child writes one JSON object to stdout and exits with code zero:

- `planning_mode: single_step` requires `PlannerDecision`.
- `planning_mode: continuous` without `active_plan` requires a bounded
  `PlanEnvelope` tied to the observation's exact `world_revision`.
- `planning_mode: continuous` with `active_plan` requires a `PlanPatch` tied to
  that exact plan, version, active step, and world revision.

Diagnostic logs belong on stderr. If several stdout lines are written, the
runtime parses the final non-empty line.

Example:

```json
{
  "intent": "Pause before resolving the threat",
  "rationale": "A visible hostile is within 25 units and the game is unpaused.",
  "action": {"kind": "pause", "paused": true},
  "confidence": 0.96,
  "expected_observation": "The next telemetry snapshot should report paused=true.",
  "memory_writes": []
}
```

The rationale must be a concise decision basis, not private chain-of-thought.

For continuous output, use `schemas/plan.schema.json` or
`schemas/plan_patch.schema.json` according to `active_plan`. Every plan is
bounded and acyclic, binds its control mode and causal revision, declares typed
assumptions, preconditions and postconditions, and carries action, wall-clock,
game-time, and risk budgets. During a monitored option, a patch may change only
future steps unless the exact active step opts into interruption and the
replacement begins with a causally confirmed pause handoff. The executor—not
the child process—owns active state, retries, branches, budget accounting,
condition evaluation, cancellation, and postcondition polling. A snapshot at or
before the action-start revision cannot confirm success. A later snapshot
satisfies only the temporal fence; most postconditions are still
planner-authored and are not controller-owned proof that the intended action
caused the observed change.

## Errors

A timeout, non-zero exit code, empty stdout, or schema violation becomes a
planner error. The runtime records it. Single-step mode produces a stop
decision; continuous mode requests a fresh plan until
`max_consecutive_replans` is exhausted. It never repairs arbitrary output
silently.

## Persistent service upgrade

When process startup becomes material, add a separate planner implementation
using localhost HTTP, a named pipe, or stdio JSON-RPC. Preserve the same
`Observation`, `PlannerDecision`, `PlanEnvelope`, and `PlanPatch` schemas so
evaluation remains comparable, and select the continuous response type from
`active_plan` just as the hosted adapters do.
