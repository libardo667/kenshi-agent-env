# ADR: Independent safety supervision

Status: accepted for portable continuous mode

## Context

The P2 observation pump can keep publishing while a strategic planner is
blocked, but the P2 scheduler still waits for that planner before checking its
ordinary reflex layer. A fresh threat, stale or stalled telemetry, withdrawn
pause capability, or unauthorized unpause therefore needs a deterministic
consumer that is independent of model availability.

The safety path must also avoid two false claims: cancellation does not prove
that a dispatched input was never delivered, and an accepted pause input does
not prove that the game reached a paused state.

## Decision

Portable continuous mode owns one `SafetySupervisor` task subscribed to the
authoritative `WorldStateStore`.

- It applies only deterministic rules: the existing reflex engine, telemetry
  staleness, a configured consecutive sequence-stall threshold,
  pause-capability withdrawal, an exact `human_input_detected` observation
  event, and unpause without an active authorized plan or command.
- `StoreUpdate` includes immutable copies of active plan and command state at
  publication time. Subscriber delay cannot turn a formerly authorized update
  into an unauthorized one.
- The first preemption is latched. Duplicate requests and repeated stop calls
  are idempotent.
- Strategic planner and plan-executor awaits are raced against the latch.
  Completed work wins a simultaneous race and is revalidated on the next
  scheduler pass; otherwise blocked work is canceled once.
- Cancellation after attempted dispatch commits the reserved budget and marks
  the command inconclusive. The runtime does not retry an at-most-once action
  merely because cancellation obscured delivery.
- The sole cleanup override is `PauseAction(paused=true)`. It preserves
  control-mode and allowlist validation and bypasses only the per-minute action
  counter. It cannot unpause or execute another action kind.
- Cleanup success requires a causally later, capable observation with
  `paused=true`. Otherwise the run records cleanup failure or an unverified
  stop.
- Supervisor decisions, planner/executor cancellations, cleanup lifecycle, and
  terminal state use distinct events and evaluator metrics.

## Consequences

A blocked portable planner or fake action can no longer delay the supported
deterministic safety rules. Logs conservatively preserve ambiguity across the
dispatch boundary, and downstream evaluation can distinguish requested,
confirmed, failed, and unverified cleanup.

This decision does not enable live continuous mode, add a stateful option
protocol, overlap useful strategic planning with active execution, or establish
real Windows F12/human-input/controller latency. Those require later
platform-specific and P4 work.
