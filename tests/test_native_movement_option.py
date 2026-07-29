"""Targetless native movement is owned by its keyed acknowledgement."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from kenshi_agent.action_contracts import NATIVE_WALK_DESTINATION_REACHED_RESULT
from kenshi_agent.env import AgentEnvironment
from kenshi_agent.models import (
    Action,
    ActionReceipt,
    CharacterState,
    CommandDispatchContext,
    ContextActionKind,
    ExitCurrentBuildingAction,
    GameState,
    KnownMapDestination,
    MoveInDirectionAction,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NativeControlState,
    Observation,
    PerformContextAction,
    ProduceResourceOutputAction,
    TelemetrySnapshot,
    Transition,
    TravelToMapDestinationAction,
    UIState,
    Vec3,
    WorldStateRevision,
    WorldTarget,
)
from kenshi_agent.options import OptionStatus, StatefulNativeMovementOption
from kenshi_agent.world_state import SequenceStatus, StateDelta, StoreUpdate

COMMAND_ID = "cmd-0123456789abcdef0123456789abcdef"
SELECTED_ID = "entity-selected"


def acknowledgement(
    sequence: int,
    status: NativeCommandStatus,
    *,
    command_id: str = COMMAND_ID,
    bearing_degrees: float = 90.0,
    distance_units: float = 250.0,
) -> NativeCommandAcknowledgement:
    terminal = status in {
        NativeCommandStatus.COMPLETED,
        NativeCommandStatus.REJECTED,
        NativeCommandStatus.CANCELLED,
    }
    accepted = status is not NativeCommandStatus.REJECTED
    return NativeCommandAcknowledgement(
        command_id=command_id,
        command="move_in_direction",
        status=status,
        reason={
            NativeCommandStatus.ACCEPTED: "issued",
            NativeCommandStatus.COMPLETED: NATIVE_WALK_DESTINATION_REACHED_RESULT,
            NativeCommandStatus.REJECTED: "selection_mismatch",
            NativeCommandStatus.CANCELLED: "movement_interrupted",
        }[status],
        target_id="",
        bearing_degrees=bearing_degrees,
        distance_units=distance_units,
        selected_character_ids=[SELECTED_ID],
        based_on_telemetry_sequence=1,
        acknowledged_at_telemetry_sequence=2,
        accepted_at_telemetry_sequence=2 if accepted else None,
        terminal_at_telemetry_sequence=sequence if terminal else None,
    )


def observation(
    sequence: int,
    *,
    ack: NativeCommandAcknowledgement | None = None,
    paused: bool = True,
) -> Observation:
    return Observation(
        run_id="native-movement-option-test",
        step_index=sequence,
        mode="mock",
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            capabilities=[
                "game.pause",
                "control.move_in_direction",
                "squad.health",
            ],
            game=GameState(paused=paused, elapsed_minutes=0.0),
            ui=UIState(
                selected_character_id=SELECTED_ID,
                selected_character_ids=[SELECTED_ID],
            ),
            native_control=NativeControlState(
                active_command_id=(
                    ack.command_id
                    if ack is not None and ack.status is NativeCommandStatus.ACCEPTED
                    else None
                ),
                acknowledgements=[ack] if ack is not None else [],
            ),
        ),
        telemetry_age_seconds=0.0,
    )


def map_travel_acknowledgement(
    sequence: int,
    status: NativeCommandStatus,
) -> NativeCommandAcknowledgement:
    terminal = status is not NativeCommandStatus.ACCEPTED
    return NativeCommandAcknowledgement(
        command_id=COMMAND_ID,
        command="travel_to_map_destination",
        status=status,
        reason=(
            "issued"
            if status is NativeCommandStatus.ACCEPTED
            else (
                "map_destination_reached"
                if status is NativeCommandStatus.COMPLETED
                else "movement_interrupted"
            )
        ),
        target_id="entity-known-town",
        selected_character_ids=[SELECTED_ID],
        based_on_telemetry_sequence=1,
        acknowledged_at_telemetry_sequence=2,
        accepted_at_telemetry_sequence=2,
        terminal_at_telemetry_sequence=sequence if terminal else None,
    )


def map_travel_observation(
    sequence: int,
    *,
    ack: NativeCommandAcknowledgement | None = None,
) -> Observation:
    return Observation(
        run_id="native-map-travel-option-test",
        step_index=sequence,
        mode="mock",
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            capabilities=[
                "game.pause",
                "control.travel_to_map_destination",
                "world.known_map_destinations",
                "squad.health",
            ],
            game=GameState(paused=True, elapsed_minutes=0.0),
            ui=UIState(
                selected_character_id=SELECTED_ID,
                selected_character_ids=[SELECTED_ID],
            ),
            known_map_destinations=[
                KnownMapDestination(
                    id="entity-known-town",
                    name="The Hub",
                    distance=1250.0,
                )
            ],
            native_control=NativeControlState(
                active_command_id=(
                    ack.command_id
                    if ack is not None
                    and ack.status is NativeCommandStatus.ACCEPTED
                    else None
                ),
                acknowledgements=[ack] if ack is not None else [],
            ),
        ),
        telemetry_age_seconds=0.0,
    )


def update(obs: Observation) -> StoreUpdate:
    return StoreUpdate(
        observation=obs,
        sequence_status=SequenceStatus.ADVANCED,
        delta=StateDelta(
            before_revision=None,
            after_revision=obs.world_revision,
            changed_paths=("telemetry.native_control",),
        ),
    )


def exit_acknowledgement(
    sequence: int,
    status: NativeCommandStatus,
) -> NativeCommandAcknowledgement:
    terminal = status is not NativeCommandStatus.ACCEPTED
    return NativeCommandAcknowledgement(
        command_id=COMMAND_ID,
        command="exit_current_building",
        status=status,
        reason=(
            "issued"
            if status is NativeCommandStatus.ACCEPTED
            else (
                "left_current_building"
                if status is NativeCommandStatus.COMPLETED
                else "movement_stalled"
            )
        ),
        selected_character_ids=[SELECTED_ID],
        based_on_telemetry_sequence=1,
        acknowledged_at_telemetry_sequence=2,
        accepted_at_telemetry_sequence=2,
        terminal_at_telemetry_sequence=sequence if terminal else None,
    )


def exit_observation(
    sequence: int,
    *,
    ack: NativeCommandAcknowledgement | None = None,
    indoors: bool = True,
) -> Observation:
    return Observation(
        run_id="native-exit-option-test",
        step_index=sequence,
        mode="mock",
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            capabilities=[
                "game.pause",
                "control.exit_current_building",
                "squad.indoors",
            ],
            game=GameState(paused=True, elapsed_minutes=0.0),
            ui=UIState(
                selected_character_id=SELECTED_ID,
                selected_character_ids=[SELECTED_ID],
            ),
            squad=[
                CharacterState(
                    id=SELECTED_ID,
                    name="Hep",
                    selected=True,
                    indoors=indoors,
                )
            ],
            native_control=NativeControlState(
                active_command_id=(
                    ack.command_id
                    if ack is not None
                    and ack.status is NativeCommandStatus.ACCEPTED
                    else None
                ),
                acknowledgements=[ack] if ack is not None else [],
            ),
        ),
        telemetry_age_seconds=0.0,
    )


def context_acknowledgement(
    sequence: int,
    status: NativeCommandStatus,
) -> NativeCommandAcknowledgement:
    terminal = status is not NativeCommandStatus.ACCEPTED
    return NativeCommandAcknowledgement(
        command_id=COMMAND_ID,
        command="operate_natural_resource",
        status=status,
        reason=(
            "issued"
            if status is NativeCommandStatus.ACCEPTED
            else (
                "context_task_started"
                if status is NativeCommandStatus.COMPLETED
                else "movement_stalled"
            )
        ),
        target_id="entity-copper",
        selected_character_ids=[SELECTED_ID],
        based_on_telemetry_sequence=1,
        acknowledged_at_telemetry_sequence=2,
        accepted_at_telemetry_sequence=2,
        terminal_at_telemetry_sequence=sequence if terminal else None,
    )


def context_observation(
    sequence: int,
    *,
    ack: NativeCommandAcknowledgement | None = None,
) -> Observation:
    return Observation(
        run_id="native-context-option-test",
        step_index=sequence,
        mode="mock",
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            capabilities=[
                "game.pause",
                "control.perform_context_action",
                "world.context_targets",
            ],
            game=GameState(paused=True, elapsed_minutes=0.0),
            ui=UIState(
                selected_character_id=SELECTED_ID,
                selected_character_ids=[SELECTED_ID],
            ),
            world_targets=[
                WorldTarget(
                    id="entity-copper",
                    name="Copper Resource",
                    kind="natural_resource",
                    position=Vec3(x=1.0, y=0.0, z=2.0),
                    distance=20.0,
                    context_actions=[ContextActionKind.OPERATE],
                    default_task="operate_machinery",
                )
            ],
            native_control=NativeControlState(
                active_command_id=(
                    ack.command_id
                    if ack is not None
                    and ack.status is NativeCommandStatus.ACCEPTED
                    else None
                ),
                acknowledgements=[ack] if ack is not None else [],
            ),
        ),
        telemetry_age_seconds=0.0,
    )


def production_acknowledgement(
    sequence: int,
    status: NativeCommandStatus,
    *,
    reason: str,
    minimum_output_quantity: int = 1,
) -> NativeCommandAcknowledgement:
    terminal = status is not NativeCommandStatus.ACCEPTED
    return NativeCommandAcknowledgement(
        command_id=COMMAND_ID,
        command="produce_resource_output",
        status=status,
        reason=reason,
        target_id="entity-copper",
        minimum_output_quantity=minimum_output_quantity,
        selected_character_ids=[SELECTED_ID],
        based_on_telemetry_sequence=1,
        acknowledged_at_telemetry_sequence=2,
        accepted_at_telemetry_sequence=2,
        terminal_at_telemetry_sequence=sequence if terminal else None,
    )


def production_observation(
    sequence: int,
    *,
    ack: NativeCommandAcknowledgement | None = None,
    current_goal: str | None = None,
) -> Observation:
    state = context_observation(sequence)
    assert state.telemetry is not None
    selected = CharacterState(
        id=SELECTED_ID,
        name="Hep",
        selected=True,
        current_goal=current_goal,
    )
    return state.model_copy(
        update={
            "telemetry": state.telemetry.model_copy(
                update={
                    "capabilities": [
                        "game.pause",
                        "control.produce_resource_output",
                        "world.context_targets",
                    ],
                    "squad": [selected],
                    "native_control": NativeControlState(
                        active_command_id=(
                            ack.command_id
                            if ack is not None
                            and ack.status is NativeCommandStatus.ACCEPTED
                            else None
                        ),
                        acknowledgements=[ack] if ack is not None else [],
                    ),
                }
            )
        },
        deep=True,
    )


class InstantNativeMovementEnvironment(AgentEnvironment):
    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return observation(1)

    async def observe(self) -> Observation:
        return observation(1)

    async def step(self, action: Action) -> Transition:
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                message="direction order issued",
                native_acknowledgement=acknowledgement(
                    2, NativeCommandStatus.ACCEPTED
                ),
            ),
            observation=observation(
                2, ack=acknowledgement(2, NativeCommandStatus.ACCEPTED)
            ),
        )

    async def close(self) -> None:
        return None


class AdoptedNativeMovementEnvironment(InstantNativeMovementEnvironment):
    def __init__(self, native_command_id: str) -> None:
        self.native_command_id = native_command_id

    async def step(self, action: Action) -> Transition:
        accepted = acknowledgement(
            2,
            NativeCommandStatus.ACCEPTED,
            command_id=self.native_command_id,
        )
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                message="existing exact direction order adopted",
                native_acknowledgement=accepted,
            ),
            observation=observation(2, ack=accepted),
        )


class InstantExitBuildingEnvironment(AgentEnvironment):
    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return exit_observation(1)

    async def observe(self) -> Observation:
        return exit_observation(1)

    async def step(self, action: Action) -> Transition:
        accepted = exit_acknowledgement(2, NativeCommandStatus.ACCEPTED)
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                message="building exit issued",
                native_acknowledgement=accepted,
            ),
            observation=exit_observation(2, ack=accepted),
        )

    async def close(self) -> None:
        return None


class InstantContextActionEnvironment(AgentEnvironment):
    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return context_observation(1)

    async def observe(self) -> Observation:
        return context_observation(1)

    async def step(self, action: Action) -> Transition:
        accepted = context_acknowledgement(2, NativeCommandStatus.ACCEPTED)
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                message="context action issued",
                native_acknowledgement=accepted,
            ),
            observation=context_observation(2, ack=accepted),
        )

    async def close(self) -> None:
        return None


class InstantResourceProductionEnvironment(AgentEnvironment):
    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return production_observation(1)

    async def observe(self) -> Observation:
        return production_observation(1)

    async def step(self, action: Action) -> Transition:
        accepted = production_acknowledgement(
            2,
            NativeCommandStatus.ACCEPTED,
            reason="issued",
        )
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                message="resource production issued",
                native_acknowledgement=accepted,
            ),
            observation=production_observation(2, ack=accepted),
        )

    async def close(self) -> None:
        return None


class InstantMapTravelEnvironment(AgentEnvironment):
    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return map_travel_observation(1)

    async def observe(self) -> Observation:
        return map_travel_observation(1)

    async def step(self, action: Action) -> Transition:
        accepted = map_travel_acknowledgement(
            2,
            NativeCommandStatus.ACCEPTED,
        )
        return Transition(
            receipt=ActionReceipt(
                action=action,
                command_id=COMMAND_ID,
                started_after_revision=map_travel_observation(1).world_revision,
                accepted=True,
                executed=True,
                dry_run=False,
                message="map travel issued",
                native_acknowledgement=accepted,
            ),
            observation=map_travel_observation(2, ack=accepted),
        )

    async def close(self) -> None:
        return None


def option() -> StatefulNativeMovementOption:
    return StatefulNativeMovementOption(
        option_id="native-direction-1",
        action=MoveInDirectionAction(
            bearing_degrees=90.0,
            distance_units=250.0,
            expected_effect="leave the current building",
        ),
        environment=InstantNativeMovementEnvironment(),
    )


def command() -> CommandDispatchContext:
    return CommandDispatchContext(
        command_id=COMMAND_ID,
        based_on_revision=observation(1).world_revision,
    )


def test_direction_option_waits_for_terminal_acknowledgement() -> None:
    async def scenario() -> None:
        movement = option()
        assert movement.prepare(observation(1)).status is OptionStatus.PREPARED
        await movement.start(command())
        await asyncio.sleep(0)

        accepted = movement.poll(
            update(observation(2, ack=acknowledgement(2, NativeCommandStatus.ACCEPTED)))
        )
        assert accepted.status is OptionStatus.RUNNING

        completed = movement.poll(
            update(observation(3, ack=acknowledgement(3, NativeCommandStatus.COMPLETED)))
        )
        assert completed.status is OptionStatus.SUCCEEDED
        assert movement.result().receipt.accepted is True

    asyncio.run(scenario())


def test_map_travel_option_owns_one_exact_destination_until_native_arrival() -> None:
    async def scenario() -> None:
        travel = StatefulNativeMovementOption(
            option_id="native-map-travel-1",
            action=TravelToMapDestinationAction(
                destination_id="entity-known-town",
            ),
            environment=InstantMapTravelEnvironment(),
        )
        assert (
            travel.prepare(map_travel_observation(1)).status
            is OptionStatus.PREPARED
        )
        await travel.start(
            CommandDispatchContext(
                command_id=COMMAND_ID,
                based_on_revision=map_travel_observation(1).world_revision,
            )
        )
        await asyncio.sleep(0)

        accepted = travel.poll(
            update(
                map_travel_observation(
                    2,
                    ack=map_travel_acknowledgement(
                        2,
                        NativeCommandStatus.ACCEPTED,
                    ),
                )
            )
        )
        assert accepted.status is OptionStatus.RUNNING

        completed = travel.poll(
            update(
                map_travel_observation(
                    3,
                    ack=map_travel_acknowledgement(
                        3,
                        NativeCommandStatus.COMPLETED,
                    ),
                )
            )
        )
        assert completed.status is OptionStatus.SUCCEEDED

    asyncio.run(scenario())


def test_map_travel_is_routed_through_the_native_movement_option() -> None:
    assert StatefulNativeMovementOption.supports(
        TravelToMapDestinationAction(
            destination_id="entity-known-town",
        )
    )


def test_building_exit_option_accepts_native_terminal_when_indoor_handle_lingers() -> None:
    async def scenario() -> None:
        movement = StatefulNativeMovementOption(
            option_id="native-exit-1",
            action=ExitCurrentBuildingAction(),
            environment=InstantExitBuildingEnvironment(),
        )
        movement.prepare(exit_observation(1))
        await movement.start(
            CommandDispatchContext(
                command_id=COMMAND_ID,
                based_on_revision=exit_observation(1).world_revision,
            )
        )
        await asyncio.sleep(0)

        accepted = exit_acknowledgement(2, NativeCommandStatus.ACCEPTED)
        assert (
            movement.poll(update(exit_observation(2, ack=accepted))).status
            is OptionStatus.RUNNING
        )
        completed = exit_acknowledgement(3, NativeCommandStatus.COMPLETED)
        outcome = movement.poll(
            update(exit_observation(3, ack=completed, indoors=True))
        )

        assert outcome.status is OptionStatus.SUCCEEDED
        assert movement.result().receipt.native_acknowledgement == accepted

    asyncio.run(scenario())


def test_context_action_option_waits_for_exact_native_task_proof() -> None:
    async def scenario() -> None:
        context = StatefulNativeMovementOption(
            option_id="native-context-1",
            action=PerformContextAction(
                target_id="entity-copper",
                context_action=ContextActionKind.OPERATE,
            ),
            environment=InstantContextActionEnvironment(),
        )
        start = context_observation(1)
        context.prepare(start)
        await context.start(
            CommandDispatchContext(
                command_id=COMMAND_ID,
                based_on_revision=start.world_revision,
            )
        )
        await asyncio.sleep(0)

        accepted = context_acknowledgement(2, NativeCommandStatus.ACCEPTED)
        assert (
            context.poll(update(context_observation(2, ack=accepted))).status
            is OptionStatus.RUNNING
        )
        completed = context_acknowledgement(3, NativeCommandStatus.COMPLETED)
        outcome = context.poll(
            update(context_observation(3, ack=completed))
        )

        assert outcome.status is OptionStatus.SUCCEEDED
        assert "context_task_started" in outcome.reason

    asyncio.run(scenario())


def test_resource_production_retains_work_until_output_is_ready() -> None:
    async def scenario() -> None:
        production = StatefulNativeMovementOption(
            option_id="native-production-1",
            action=ProduceResourceOutputAction(
                target_id="entity-copper",
                minimum_output_quantity=5,
            ),
            environment=InstantResourceProductionEnvironment(),
        )
        smaller_active_order = production_acknowledgement(
            2,
            NativeCommandStatus.ACCEPTED,
            reason="context_task_active",
            minimum_output_quantity=1,
        )
        start = production_observation(1, ack=smaller_active_order)
        production.prepare(start)
        assert production.native_command_id is None
        await production.start(
            CommandDispatchContext(
                command_id=COMMAND_ID,
                based_on_revision=start.world_revision,
            )
        )
        await asyncio.sleep(0)

        operating = production_acknowledgement(
            3,
            NativeCommandStatus.ACCEPTED,
            reason="context_task_active",
            minimum_output_quantity=5,
        )
        progress = production.poll(
            update(
                production_observation(
                    3,
                    ack=operating,
                    current_goal="Operating machine",
                )
            )
        )
        assert progress.status is OptionStatus.RUNNING

        ready = production_acknowledgement(
            4,
            NativeCommandStatus.COMPLETED,
            reason="resource_output_ready",
            minimum_output_quantity=5,
        )
        outcome = production.poll(
            update(production_observation(4, ack=ready))
        )
        assert outcome.status is OptionStatus.SUCCEEDED
        assert "resource_output_ready" in outcome.reason

    asyncio.run(scenario())


def test_resource_production_never_accepts_task_start_as_terminal_output() -> None:
    async def scenario() -> None:
        production = StatefulNativeMovementOption(
            option_id="native-production-bad-terminal",
            action=ProduceResourceOutputAction(target_id="entity-copper"),
            environment=InstantResourceProductionEnvironment(),
        )
        start = production_observation(1)
        production.prepare(start)
        await production.start(
            CommandDispatchContext(
                command_id=COMMAND_ID,
                based_on_revision=start.world_revision,
            )
        )
        await asyncio.sleep(0)

        false_terminal = production_acknowledgement(
            3,
            NativeCommandStatus.COMPLETED,
            reason="context_task_started",
        )
        outcome = production.poll(
            update(production_observation(3, ack=false_terminal))
        )

        assert outcome.status is OptionStatus.FAILED
        assert "output" in outcome.reason

    asyncio.run(scenario())
def test_building_exit_option_rejects_an_outdoor_start() -> None:
    movement = StatefulNativeMovementOption(
        option_id="native-exit-outdoors",
        action=ExitCurrentBuildingAction(),
        environment=InstantExitBuildingEnvironment(),
    )

    try:
        movement.prepare(exit_observation(1, indoors=False))
    except Exception as exc:
        assert "confirmed indoors" in str(exc)
    else:
        raise AssertionError("outdoor building-exit start was accepted")


def test_direction_option_rejects_acknowledgement_for_other_vector() -> None:
    async def scenario() -> None:
        movement = option()
        movement.prepare(observation(1))
        await movement.start(command())
        await asyncio.sleep(0)

        outcome = movement.poll(
            update(
                observation(
                    2,
                    ack=acknowledgement(
                        2,
                        NativeCommandStatus.ACCEPTED,
                        bearing_degrees=180.0,
                    ),
                )
            )
        )
        assert outcome.status is OptionStatus.FAILED
        assert "identity" in outcome.reason

    asyncio.run(scenario())


def test_direction_option_treats_native_rejection_as_terminal_failure() -> None:
    async def scenario() -> None:
        movement = option()
        movement.prepare(observation(1))
        await movement.start(command())
        await asyncio.sleep(0)

        outcome = movement.poll(
            update(observation(2, ack=acknowledgement(2, NativeCommandStatus.REJECTED)))
        )
        assert outcome.status is OptionStatus.FAILED
        assert "selection_mismatch" in outcome.reason

    asyncio.run(scenario())


def test_direction_option_monitors_the_original_id_when_order_is_adopted() -> None:
    async def scenario() -> None:
        adopted_id = "cmd-" + "a" * 32
        accepted = acknowledgement(
            2,
            NativeCommandStatus.ACCEPTED,
            command_id=adopted_id,
        )
        movement = StatefulNativeMovementOption(
            option_id="native-direction-adopted",
            action=MoveInDirectionAction(
                bearing_degrees=90.0,
                distance_units=250.0,
                expected_effect="continue the existing eastbound walk",
            ),
            environment=AdoptedNativeMovementEnvironment(adopted_id),
        )
        movement.prepare(observation(2, ack=accepted))
        logical_command = CommandDispatchContext(
            command_id="cmd-" + "b" * 32,
            based_on_revision=observation(2, ack=accepted).world_revision,
        )
        await movement.start(logical_command)
        await asyncio.sleep(0)

        completed = acknowledgement(
            3,
            NativeCommandStatus.COMPLETED,
            command_id=adopted_id,
        )
        outcome = movement.poll(update(observation(3, ack=completed)))

        assert movement.native_command_id == adopted_id
        assert outcome.status is OptionStatus.SUCCEEDED
        assert movement.result().receipt.command_id == logical_command.command_id

    asyncio.run(scenario())
