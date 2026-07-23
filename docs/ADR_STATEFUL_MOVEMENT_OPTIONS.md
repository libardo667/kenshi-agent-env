# ADR: Adapt movement macros into stateful options

Status: accepted for portable continuous mode

## Context

P3 can cancel an opaque `environment.step()` await, but it cannot describe
movement progress or safely overlap useful future planning. Replacing the
already validated live movement implementation outright would unnecessarily
risk its input leasing, interruption checks, and guaranteed re-pause behavior.

Concurrent model output also cannot be allowed to mutate the running step. It
may be based on an old snapshot by the time movement ends, and remaining
budgets may have changed.

## Decision

- Keep the macro registry and environment movement pulse intact.
- In portable continuous mode, adapt configured movement-pulse skills into
  `StatefulMovementOption`, with prepared, running, succeeded, failed, and
  cancelled states plus state-stream progress polling.
- Require a capable, confirmed-paused start. One option owns one action task and
  one store subscription; cancellation is idempotent and cleanup failure is
  explicit.
- Allow one strategic advisory while the option is active. Its immutable
  observation includes typed `ActivePlanContext`.
- Accept only a future-only `PlanPatch` whose plan ID, version, and revision
  match that context and the still-current store. Active/completed step IDs are
  protected.
- Stage rather than execute matching output. After movement succeeds, rebuild
  the proposed future plan at the latest revision and revalidate graph,
  assumptions, configured policy, and remaining run/action/risk/time budgets.
- Apply by incrementing the active plan version. Every replacement step still
  passes the ordinary precondition, guard, causal receipt, and postcondition
  paths.
- Log wrong-type, failed, late, stale, mismatched, or unsafe output as discarded
  or rejected rather than repairing it or treating it as a planner crash.
- Preserve P3 cancellation semantics: uncertain movement delivery consumes its
  reservation and the independent supervisor owns the narrow verified pause.

## Consequences

Portable movement now has replayable lifecycle and genuine strategic/execution
overlap without granting the model authority over the running action. A valid
future patch can save a post-movement strategic round trip, while two distinct
revision checks and a final budget/policy check prevent stale output from
becoming executable.

The adapter is not evidence that live continuous control is safe. It does not
replace the live movement pulse, generalize every macro into an option, add
native command acknowledgement, or measure Windows F12/human-input latency.
