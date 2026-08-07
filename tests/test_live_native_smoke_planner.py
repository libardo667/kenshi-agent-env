from __future__ import annotations

import pytest

from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    ControlMode,
    ExitCurrentBuildingAction,
    MoveToCharacterAction,
    PauseAction,
    StopAction,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    ContextActionKind,
    GameState,
    NearbyEntity,
    TelemetrySnapshot,
    UIState,
    Vec3,
    WorldTarget,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.live_native_smoke_planner import (
    build_plan,
)

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



