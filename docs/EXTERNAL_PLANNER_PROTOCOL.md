# External planner protocol

The subprocess adapter starts a new child process for each decision. This is
slower than a persistent RPC service but gives a simple, isolated contract for
early experiments.

## Request

The child's stdin receives exactly one UTF-8 JSON line containing the complete
`Observation` schema. `screenshot_path` refers to a local file available to the
child process.

## Response

The child writes one `PlannerDecision` JSON object to stdout and exits with code
zero. Diagnostic logs belong on stderr. If several stdout lines are written, the
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

## Errors

A timeout, non-zero exit code, empty stdout, or schema violation becomes a
planner error. The runtime records it and selects a stop decision rather than
attempting to repair arbitrary output silently.

## Persistent service upgrade

When process startup becomes material, add a separate planner implementation
using localhost HTTP, a named pipe, or stdio JSON-RPC. Preserve the same
`Observation` and `PlannerDecision` schemas so evaluation remains comparable.
