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
    ExitCurrentBuildingAction,
    GameState,
    MoveInDirectionAction,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NativeControlState,
    Observation,
    TelemetrySnapshot,
    Transition,
    UIState,
    WorldStateRevision,
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
