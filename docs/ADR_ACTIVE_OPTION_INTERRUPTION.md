# ADR: active-option interruption

## Context

Concurrent planning originally accepted only future-only patches while a
monitored semantic movement option ran. That protected executor ownership, but
it also made the strategic planner unable to change course until arrival. A live
directional probe then exposed the opposite failure on timeout: local option
ownership ended while Kenshi kept walking, and ordinary replanning resumed
before a confirmed pause.

This decision supersedes only the future-only restriction in
[bounded continuous planning](ADR_CONTINUOUS_PLANNING.md). Its remaining
acceptance, budget, causal-evidence, and executor-ownership rules still apply.

## Decision

`PlanPatch.interrupt_active_step_id` is null for an ordinary future-only patch.
An interrupt request must copy the exact active step ID from
`ActivePlanContext`; the plan ID, plan version, and immutable planner revision
remain mandatory.

Interruption is opt-in per step and executor-supported option through
`cancel_on_reflex_or_plan_patch`. A foreign step ID, a non-interruptible or
unsupported option, or a replacement that can act before safe handoff is
rejected.

The first replacement step must request `pause: true` and causally prove both
that Kenshi is paused and that native control has no active command. Only after
that step succeeds can any other replacement action execute. The executor
cancels local option monitoring, conservatively spends an already delivered
at-most-once action, and revalidates the replacement against latest state and
remaining budgets.

The world need not freeze while the planner thinks. An interrupt patch may
arrive on a causally later snapshot if the exact plan and step still own the
option, because its only immediate authority is the guarded pause handoff.
Future actions remain current-state-bound after the pause.

Timeout, target loss, or another failed monitored terminal cannot simply release
ownership. The scheduler must execute a deterministic, causally confirmed pause
before the strategic planner receives another observation. If pause capability
is unavailable, the run stops instead.

## Consequences

Background planning can now either preserve a walk and revise what follows, or
explicitly change course without overwriting an in-flight command. Stale prose
never cancels movement by itself; exact typed identity plus current executor
state does.

An interruption consumes the original delivered action and at least one
additional action for the pause. Plans that want this responsiveness must
reserve that action budget. The interruption is logged distinctly from success
or failure so evaluation does not claim an interrupted destination was reached.
