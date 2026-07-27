# ADR: context actions authorize attempts, not predicted success

## Context

World-target telemetry originally used Kenshi's undocumented
`getPlayerTaskProbability` result both to hide objects and to decide whether the
planner could act on them. A player could visibly select ordinary mining while
that result was false. The field therefore conflated perception, predicted game
mechanics, and action authority, and made an existing mine indistinguishable
from no mine.

Kenshi also exposes terrain resource values through `ZoneManager`, but those are
hidden mechanical truth. A player learns terrain suitability through
prospecting, at a position and with character skill.

## Decision

Protocol 1 removes task-availability and task-probability predictions from
nearby characters and world targets.

`world_targets` contains exact structurally recognized mining objects from the
bounded current query. `context_actions` means the current producer and
controller support one reviewed, bounded attempt on that exact object. It does
not predict that Kenshi will accept or sustain the task.

The binder requires the exact current target ID and advertised action. Native
dispatch re-resolves the same identity and mining role, issues Kenshi's own
task, and reports success only when the selected character's AI holds the exact
task and subject. Dispatch never substitutes a different target.

`squad[].current_goal` is the UI-facing goal string and is meaningful only with
`squad.current_goal`. It is observation of what Kenshi currently tells the
player, not access to the internal task stack or a grant of authority.

Terrain resource values are never exported directly from `ZoneManager`. A
future prospecting action may produce position-bound learned knowledge. That
knowledge belongs in persistent memory, not current-world telemetry.

## Consequences

The wire change is protocol major 1 because fields were removed and
`context_actions` now carries the complete reviewed-attempt meaning.

Perception expands without weakening safety: Python still binds exact current
identity and action, the native bridge revalidates at dispatch and during the
command lease, and completion remains keyed causal evidence.

The planner may see an operation that Kenshi later rejects. That is an honest
action outcome to learn from, not a reason to hide the object or expose a hidden
mechanical score.
