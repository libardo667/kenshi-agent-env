from __future__ import annotations

import pytest

from kenshi_agent.live_native_smoke_planner import build_decision, build_plan
from kenshi_agent.models import (
    CharacterState,
    ContextActionKind,
    ControlMode,
    ExitCurrentBuildingAction,
    GameState,
    Observation,
    PerformContextAction,
    TelemetrySnapshot,
    UIState,
    Vec3,
    WorldStateRevision,
    WorldTarget,
)

TARGET_ID = "entity-copper"


def observation(*, indoors: bool, task_available: bool = False) -> Observation:
    target = WorldTarget(
        id=TARGET_ID,
        name="Copper Resource",
        kind="natural_resource",
        position=Vec3(x=10.0, y=0.0, z=20.0),
        distance=40.0,
        context_actions=[ContextActionKind.OPERATE],
        default_task="operate_machinery",
        task_available=task_available,
        task_probability=1.0 if task_available else 0.0,
        mining_resource_level=0.8,
    )
    return Observation(
        run_id="live-native-smoke-test",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(
            telemetry_sequence=10,
            frame_sequence=10,
            capability_epoch=1,
        ),
        telemetry=TelemetrySnapshot(
            sequence=10,
            identity_session_id="session-live-native-smoke",
            capabilities=[
                "control.exit_current_building",
                "control.perform_context_action",
                "game.pause",
                "identity.stable_handles",
                "squad.indoors",
                "world.context_targets",
            ],
            game=GameState(loaded=True, paused=True),
            ui=UIState(
                selected_character_id="entity-hep",
                selected_character_ids=["entity-hep"],
            ),
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    indoors=indoors,
                )
            ],
            world_targets=[target],
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.1,
    )


def test_exit_plan_requires_and_preserves_the_observed_indoor_state() -> None:
    state = observation(indoors=True)

    plan = build_plan(state, action_kind="exit_current_building")

    assert plan.based_on_revision == state.world_revision
    assert isinstance(plan.steps[0].action, ExitCurrentBuildingAction)
    with pytest.raises(ValueError, match="confirmed indoors"):
        build_plan(
            observation(indoors=False),
            action_kind="exit_current_building",
        )


def test_context_plan_never_emits_an_unavailable_target() -> None:
    with pytest.raises(ValueError, match="not currently actionable"):
        build_plan(
            observation(indoors=False),
            action_kind="perform_context_action",
            target_id=TARGET_ID,
        )

    plan = build_plan(
        observation(indoors=False, task_available=True),
        action_kind="perform_context_action",
        target_id=TARGET_ID,
    )

    assert plan.steps[0].action == PerformContextAction(
        target_id=TARGET_ID,
        context_action=ContextActionKind.OPERATE,
    )


def test_single_step_decision_uses_the_same_context_eligibility_fence() -> None:
    with pytest.raises(ValueError, match="not currently actionable"):
        build_decision(
            observation(indoors=False),
            action_kind="perform_context_action",
            target_id=TARGET_ID,
        )

    decision = build_decision(
        observation(indoors=False, task_available=True),
        action_kind="perform_context_action",
        target_id=TARGET_ID,
    )

    assert decision.action == PerformContextAction(
        target_id=TARGET_ID,
        context_action=ContextActionKind.OPERATE,
    )
