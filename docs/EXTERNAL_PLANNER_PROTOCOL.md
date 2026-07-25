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
- `planning_mode: continuous` requires a bounded `PlanEnvelope` tied to the
  observation's exact `world_revision`.

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

For continuous output, use `schemas/plan.schema.json`. Every plan is bounded and
acyclic, binds its control mode and causal revision, declares typed assumptions,
preconditions and postconditions, and carries action, wall-clock, game-time, and
risk budgets. The executor—not the child process—owns active state, retries,
branches, budget accounting, condition evaluation, cancellation, and
postcondition polling. A snapshot at or before the action-start revision cannot
confirm success. A later snapshot satisfies only the temporal fence; most
postconditions are still planner-authored and are not controller-owned proof
that the intended action caused the observed change.

The subprocess adapter currently always parses continuous output as a
`PlanEnvelope`. It does not select `PlanPatch` when `active_plan` is present, so
do not use it for concurrent option advice. The OpenAI/OpenRouter adapters
implement that mode-aware output selection.

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
