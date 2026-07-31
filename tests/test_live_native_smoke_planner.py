from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from kenshi_agent.live_native_smoke_planner import (
    build_decision,
    build_output,
    build_plan,
)
from kenshi_agent.models import (
    ActivePlanContext,
    CharacterState,
    ContextActionKind,
    ControlMode,
    ExitCurrentBuildingAction,
    GameBinding,
    GameState,
    HarvestResourceAction,
    InterruptPolicy,
    MoveToCharacterAction,
    NearbyEntity,
    Observation,
    PauseAction,
    PlanEnvelope,
    PlanningMode,
    PlanPatch,
    StopAction,
    TelemetrySnapshot,
    UIState,
    UseGameBindingAction,
    Vec3,
    WorldStateRevision,
    WorldTarget,
)
from kenshi_agent.planners import SubprocessPlanner

TARGET_ID = "entity-copper"
CHARACTER_TARGET_ID = "entity-max"


def observation(*, indoors: bool, advertise_operate: bool = True) -> Observation:
    target = WorldTarget(
        id=TARGET_ID,
        name="Copper Resource",
        kind="natural_resource",
        position=Vec3(x=10.0, y=0.0, z=20.0),
        distance=40.0,
        context_actions=(
            [ContextActionKind.OPERATE]
            if advertise_operate
            else []
        ),
        default_task="operate_machinery",
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
                "control.move_to_character",
                "control.produce_resource_output",
                "control.open_context_inventory",
                "game.pause",
                "identity.stable_handles",
                "squad.basic",
                "squad.health",
                "squad.inventory",
                "squad.indoors",
                "ui.context_inventory_target",
                "ui.inventory",
                "ui.visible_controls",
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
                    alive=True,
                    conscious=True,
                    down=False,
                    in_combat=False,
                    indoors=indoors,
                    inventory_complete=True,
                )
            ],
            nearby_entities=[
                NearbyEntity(
                    id=CHARACTER_TARGET_ID,
                    name="Max",
                    kind="character",
                    is_animal=False,
                    has_dialogue=False,
                    distance=162.0,
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
    assert isinstance(plan.steps[1].action, PauseAction)
    assert plan.steps[1].preconditions[0].kind.value == "telemetry_fresh"
    assert plan.steps[0].on_success == plan.steps[1].step_id
    assert plan.steps[1].on_success == plan.steps[2].step_id
    assert isinstance(plan.steps[2].action, StopAction)
    with pytest.raises(ValueError, match="confirmed indoors"):
        build_plan(
            observation(indoors=False),
            action_kind="exit_current_building",
        )


def test_harvest_plan_requires_the_exact_advertised_action() -> None:
    with pytest.raises(ValueError, match="not currently actionable"):
        build_plan(
            observation(indoors=False, advertise_operate=False),
            action_kind="harvest_resource",
            target_id=TARGET_ID,
        )

    plan = build_plan(
        observation(indoors=False),
        action_kind="harvest_resource",
        target_id=TARGET_ID,
    )

    assert plan.steps[0].action == HarvestResourceAction(
        actor_id="entity-hep",
        target_id=TARGET_ID,
        quantity=1,
    )


def test_character_walk_smoke_requires_one_exact_current_character() -> None:
    plan = build_plan(
        observation(indoors=False),
        action_kind="move_to_character",
        target_id=CHARACTER_TARGET_ID,
    )

    assert plan.steps[0].action == MoveToCharacterAction(
        target_id=CHARACTER_TARGET_ID
    )
    with pytest.raises(ValueError, match="not currently nearby"):
        build_plan(
            observation(indoors=False),
            action_kind="move_to_character",
            target_id="entity-absent",
        )


def test_single_step_refuses_the_continuous_harvest_option() -> None:
    with pytest.raises(ValueError, match="continuous option ownership"):
        build_decision(
            observation(indoors=False),
            action_kind="harvest_resource",
            target_id=TARGET_ID,
        )


def test_active_native_smoke_preserves_only_its_future_pause_handoff() -> None:
    state = observation(indoors=False).model_copy(
        update={
            "planning_mode": PlanningMode.CONTINUOUS,
            "active_plan": ActivePlanContext(
                plan_id="live-harvest-resource-smoke",
                plan_version=1,
                objective="Operate the exact advertised target.",
                active_step_id="native-smoke",
                remaining_actions=2,
            ),
        }
    )

    output = build_output(
        state,
        action_kind="harvest_resource",
        target_id=TARGET_ID,
    )

    assert isinstance(output, PlanPatch)
    assert output.plan_id == state.active_plan.plan_id
    assert output.based_on_plan_version == state.active_plan.plan_version
    assert output.interrupt_active_step_id is None
    assert [type(step.action) for step in output.replace_future_steps] == [
        PauseAction,
        StopAction,
    ]


def test_harvest_smoke_runs_through_the_subprocess_entrypoint() -> None:
    state = observation(indoors=False).model_copy(
        update={"planning_mode": PlanningMode.CONTINUOUS}
    )
    script = (
        Path(__file__).parents[1]
        / "src"
        / "kenshi_agent"
        / "live_native_smoke_planner.py"
    )

    output = asyncio.run(
        SubprocessPlanner(
            [
                sys.executable,
                str(script),
                "--action",
                "harvest_resource",
                "--target-id",
                TARGET_ID,
            ]
        ).decide(state)
    )

    assert isinstance(output, PlanEnvelope)
    assert output.steps[0].action == HarvestResourceAction(
        actor_id="entity-hep",
        target_id=TARGET_ID,
        quantity=1,
    )


def test_quicksave_smoke_runs_through_the_subprocess_entrypoint() -> None:
    state = observation(indoors=False).model_copy(
        update={
            "planning_mode": PlanningMode.CONTINUOUS,
            "telemetry": observation(indoors=False).telemetry.model_copy(
                update={
                    "capabilities": [
                        *observation(indoors=False).telemetry.capabilities,
                        "host.quicksave_completion",
                    ]
                }
            ),
        }
    )
    script = Path(__file__).parents[1] / "scripts" / "live_quicksave_smoke_planner.py"

    output = asyncio.run(
        SubprocessPlanner([sys.executable, str(script)]).decide(state)
    )

    assert isinstance(output, PlanEnvelope)
    assert output.max_actions == 1
    assert output.steps[0].action == UseGameBindingAction(
        binding=GameBinding.QUICKSAVE,
        expected_effect="write the current game to the exact quicksave slot",
    )


def test_direction_subprocess_returns_a_patch_instead_of_a_second_move() -> None:
    state = observation(indoors=False).model_copy(
        update={
            "planning_mode": PlanningMode.CONTINUOUS,
            "active_plan": ActivePlanContext(
                plan_id="live-direction-smoke",
                plan_version=1,
                objective="Continue the exact bounded directional move.",
                active_step_id="direction-smoke",
                remaining_actions=2,
            ),
        }
    )
    script = Path(__file__).parents[1] / "scripts" / "live_direction_smoke_planner.py"

    output = asyncio.run(
        SubprocessPlanner(
            [
                sys.executable,
                str(script),
                "--bearing",
                "223",
                "--distance",
                "800",
            ]
        ).decide(state)
    )

    assert isinstance(output, PlanPatch)
    assert output.plan_id == "live-direction-smoke"
    assert not [
        step
        for step in output.replace_future_steps
        if step.action.kind == "move_in_direction"
    ]


def test_direction_subprocess_can_explicitly_interrupt_the_active_move() -> None:
    state = observation(indoors=False).model_copy(
        update={
            "planning_mode": PlanningMode.CONTINUOUS,
            "active_plan": ActivePlanContext(
                plan_id="live-direction-smoke",
                plan_version=1,
                objective="Interrupt the exact bounded directional move.",
                active_step_id="direction-smoke",
                active_step_interrupt_policy=(
                    InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH
                ),
                remaining_actions=2,
            ),
        }
    )
    script = Path(__file__).parents[1] / "scripts" / "live_direction_smoke_planner.py"

    output = asyncio.run(
        SubprocessPlanner(
            [
                sys.executable,
                str(script),
                "--bearing",
                "43",
                "--distance",
                "800",
                "--interrupt-on-advisory",
            ]
        ).decide(state)
    )

    assert isinstance(output, PlanPatch)
    assert output.interrupt_active_step_id == "direction-smoke"
    assert [type(step.action) for step in output.replace_future_steps] == [
        PauseAction,
        StopAction,
    ]
