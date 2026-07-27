"""Shared deterministic handoff for one-action supervised live proofs."""

from __future__ import annotations

from .models import (
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    IdempotencyPolicy,
    InterruptPolicy,
    Observation,
    PauseAction,
    PlanPatch,
    PlanStep,
)


def pause_handoff_step(*, step_id: str = "pause-after-smoke") -> PlanStep:
    """Pause after the owned option ends and prove its native order is terminal."""

    return PlanStep(
        step_id=step_id,
        action=PauseAction(paused=True),
        preconditions=[
            Condition(
                kind=ConditionKind.FIELD,
                path=ConditionPath.TELEMETRY_GAME_PAUSED,
                operator=ConditionOperator.EQUALS,
                expected=False,
                max_age_seconds=3.0,
                required_capabilities=["game.pause"],
            )
        ],
        success_conditions=[
            Condition(
                kind=ConditionKind.FIELD,
                path=ConditionPath.TELEMETRY_GAME_PAUSED,
                operator=ConditionOperator.EQUALS,
                expected=True,
                max_age_seconds=3.0,
                required_capabilities=["game.pause"],
            ),
            Condition(
                kind=ConditionKind.FIELD,
                path=ConditionPath.TELEMETRY_NATIVE_CONTROL_COMMAND_ACTIVE,
                operator=ConditionOperator.EQUALS,
                expected=False,
                max_age_seconds=3.0,
            ),
        ],
        timeout_seconds=3.0,
        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    )


def preserve_pause_handoff_patch(
    observation: Observation,
    *,
    step_id: str = "pause-after-smoke",
) -> PlanPatch:
    """Keep only the deterministic future pause while the option is active."""

    active = observation.active_plan
    if active is None:
        raise ValueError("active smoke patch requires an exact active plan")
    if active.remaining_actions < 1:
        raise ValueError("active smoke plan reserved no action for its pause handoff")
    return PlanPatch(
        schema_version="1.0",
        plan_id=active.plan_id,
        based_on_plan_version=active.plan_version,
        based_on_revision=observation.world_revision,
        replace_future_steps=[pause_handoff_step(step_id=step_id)],
        rationale=(
            "Preserve the deterministic pause handoff while the exact active "
            "option continues under executor ownership."
        ),
    )


def interrupt_with_pause_handoff_patch(
    observation: Observation,
    *,
    step_id: str = "pause-after-smoke",
) -> PlanPatch:
    """Request cancellation of the exact opt-in option through a safe pause."""

    active = observation.active_plan
    if active is None:
        raise ValueError("active smoke interrupt requires an exact active plan")
    if (
        active.active_step_interrupt_policy
        is not InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH
    ):
        raise ValueError("active smoke step does not permit planner interruption")
    if active.remaining_actions < 1:
        raise ValueError("active smoke plan reserved no action for its pause handoff")
    return PlanPatch(
        schema_version="1.0",
        plan_id=active.plan_id,
        based_on_plan_version=active.plan_version,
        based_on_revision=observation.world_revision,
        interrupt_active_step_id=active.active_step_id,
        replace_future_steps=[pause_handoff_step(step_id=step_id)],
        rationale=(
            "Interrupt the exact active option and begin replacement through "
            "the deterministic causal pause handoff."
        ),
    )
