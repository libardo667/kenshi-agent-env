# ADR: non-progress action boundaries

Status: accepted (2026-07-28)

## Decision

An observed thing and an authorable action on that thing are separate facts.
The observation retains stable identity and current distance; the action
contract refuses an intention whose terminal is already satisfied. For remote
map travel, settlement markers within the local-interaction radius remain
visible with `travel_available: false`, and only farther markers bind.

A keyed controller terminal outranks an intermediate transport state. In
particular, native map arrival deliberately pauses Kenshi. If arrival races the
controller's attempt to establish running playback, the exact matching terminal
is success; the pause is not reclassified as a failed unpause.

Planner-authored failure conditions describe future abort states. Every one must
be definitively false immediately before dispatch and again inside the input
lease. A true or unobservable failure condition emits no input. Exact duplicate
conditions are normalized at the schema boundary.

## Consequences

Completed destinations cannot become activity merely by being selected again.
The planner can still reason about the current settlement because its marker is
not hidden. A malformed failure predicate cannot execute an action and then
claim that its always-true condition was caused by that action. The existing
completion-authority and input-boundary fences remain the owners of mechanical
effects and final input authorization.
