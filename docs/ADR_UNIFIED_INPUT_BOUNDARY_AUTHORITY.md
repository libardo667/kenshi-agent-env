# ADR: Unify planner authority at the live input boundary

Status: accepted 2026-07-27; portable conflict proof and supervised successful
continuous-boundary evidence. Supersedes the continuous-only scope and single-step exception in
[ADR_INPUT_BOUNDARY_AUTHORITY](ADR_INPUT_BOUNDARY_AUTHORITY.md).

## Context

A polite input lease can wait without limit in either scheduler. Single-step has
no plan assumptions or typed step preconditions, but its action can still lose
authority while waiting: telemetry can stall, the control mode can change, a
human can intervene, or an exact target or UI reference can disappear. A paused
world reduces game evolution; it does not make the authority gap safe.

The action guard reserves rate and purchase authority during initial validation.
Running that same validation at the boundary must not charge the reservation
again or reject a purchase merely because its own reservation reached the
configured limit.

## Decision

- Every ordinary planner-authored live dispatch in both `single_step` and
  `continuous` carries an `ExecutionToken`. Mock and replay environments may
  ignore it because they have no real input lease.
- The live boundary rejects an absent canonical observation, stale telemetry,
  revision regression, control-mode drift, human input, emergency stop,
  calibration drift, failed plan conditions, or failed current action/reference
  validation before any primitive is emitted.
- Single-step reads telemetry and input ownership synchronously inside the
  acquired lease. Continuous execution also carries the plan conditions and
  reads its observation pump's current canonical state.
- `ActionGuard.revalidate()` repeats current safety, capability, reference, and
  spending-limit checks without consuming rate or purchase authority a second
  time. Initial `validate()` remains the only reservation point.
- The pause toggle derives whether it would pause or unpause from current fresh
  state and shares the direct action's unpause gate. The long-form profile opts
  into unpause explicitly because its world is meant to run continuously.
- Boundary rejection returns an attributable
  `InputBoundaryRejected` receipt with zero primitives in either scheduler.
- Supervisor safe-pause cleanup and control-handback restoration are not
  planner-authored gameplay actions. They keep their dedicated authority paths:
  emergency cleanup must not be vetoed by the emergency that requires it.
- Direct bare `environment.step()` remains a test/integration seam without a
  token; its existing calibration mismatch raises fail closed.

## Consequences

Scheduler choice no longer changes whether delayed input is checked against
current authority. A future unpaused single-step profile cannot silently reopen
the gap.

The fence remains additive to stronger action-specific in-lease rebinding and
native issue-time validation. It grants no new action, capability, target, or
spending authority.
