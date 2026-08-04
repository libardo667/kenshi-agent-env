from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from operation_test_support import operation_family, operation_for

from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    Action,
    PauseAction,
    RespondToImmediateThreatAction,
    SkillAction,
    ThreatResponseStrategy,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    Disposition,
    GameState,
    NearbyEntity,
    TelemetrySnapshot,
    UIState,
    Vec3,
)
from kenshi_agent.core.transport import (
    ActionReceipt,
    Transition,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.env.base import AgentEnvironment
from kenshi_agent.options import (
    OptionStatus,
    StatefulMovementOption,
    StatefulThreatResponseOption,
)


def observation(sequence: int, *, paused: bool = True) -> Observation:
    return Observation(
        run_id="option-test",
        step_index=sequence,
        mode="mock",
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            frame_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            capabilities=["game.pause", "game.time"],
            game=GameState(paused=paused, elapsed_minutes=0.0),
        ),
        telemetry_age_seconds=0.0,
    )


class BlockingEnvironment(AgentEnvironment):
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return observation(1)

    async def observe(self) -> Observation:
        return observation(1)

    async def step(self, action: Action) -> Transition:
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
            ),
            observation=observation(2),
        )

    async def close(self) -> None:
        return None


class FailingCancellationEnvironment(BlockingEnvironment):
    async def step(self, action: Action) -> Transition:
        del action
        try:
            await self.release.wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("re-pause confirmation failed") from exc
        raise AssertionError("Failing cancellation environment unexpectedly resumed.")


def test_movement_option_has_explicit_success_lifecycle() -> None:
    async def scenario() -> None:
        environment = BlockingEnvironment()
        action = SkillAction(name="move")
        option = StatefulMovementOption(
            option_id="option-success",
            action=action,
            operation=operation_for(environment, action),
        )

        assert option.prepare(observation(1)).status is OptionStatus.PREPARED
        task = option.start()
        assert option.poll().status is OptionStatus.RUNNING
        environment.release.set()
        await task

        assert option.poll().status is OptionStatus.SUCCEEDED
        assert option.result().observation.world_revision.telemetry_sequence == 2

    asyncio.run(scenario())


def test_movement_option_cancellation_is_idempotent_and_leak_free() -> None:
    async def scenario() -> None:
        environment = BlockingEnvironment()
        action = SkillAction(name="move")
        option = StatefulMovementOption(
            option_id="option-cancel",
            action=action,
            operation=operation_for(environment, action),
        )
        option.prepare(observation(1))
        task = option.start()
        await asyncio.sleep(0)

        first = await option.cancel("operator interruption")
        second = await option.cancel("duplicate interruption")

        assert first.status is OptionStatus.CANCELLED
        assert second.status is OptionStatus.CANCELLED
        assert first.reason == second.reason == "operator interruption"
        assert task.done()
        assert environment.cancelled.is_set()

    asyncio.run(scenario())


def test_movement_option_surfaces_cancellation_cleanup_failure() -> None:
    async def scenario() -> None:
        environment = FailingCancellationEnvironment()
        action = SkillAction(name="move")
        option = StatefulMovementOption(
            option_id="option-cleanup-failure",
            action=action,
            operation=operation_for(environment, action),
        )
        option.prepare(observation(1))
        task = option.start()
        await asyncio.sleep(0)

        cancelled = await option.cancel("safety preemption")

        assert cancelled.status is OptionStatus.FAILED
        assert "re-pause confirmation failed" in cancelled.reason
        assert task.done()

    asyncio.run(scenario())


def test_approach_can_start_from_a_running_world() -> None:
    """A paused start is a stop-motion assumption, not a safety property.

    An agent meant to play continuously begins its walk from a world that is
    already running. Demanding a paused start there meant the approach could
    never begin at all, and the operator had to pause the game by hand.
    """

    from kenshi_agent.core.telemetry import (
        Disposition,
        NearbyEntity,
    )
    from kenshi_agent.options import OptionLifecycleError, StatefulApproachOption

    target = NearbyEntity(
        id="entity-target",
        name="Barman",
        is_animal=False,
        has_dialogue=True,
        disposition=Disposition.NEUTRAL,
        distance=20.0,
        conscious=True,
    )
    running = observation(1, paused=False)
    telemetry = running.telemetry
    assert telemetry is not None
    running = running.model_copy(
        update={"telemetry": telemetry.model_copy(update={"nearby_entities": [target]})},
        deep=True,
    )

    strict_environment = BlockingEnvironment()
    strict_action = SkillAction(name="mock_approach")
    strict = StatefulApproachOption(
        option_id="strict",
        action=strict_action,
        operation=operation_for(strict_environment, strict_action),
        target_id="entity-target",
    )
    try:
        strict.prepare(running)
        raise AssertionError("expected a paused start to be required")
    except OptionLifecycleError as exc:
        assert "paused" in str(exc)

    relaxed_environment = BlockingEnvironment()
    relaxed_action = SkillAction(name="mock_approach")
    relaxed = StatefulApproachOption(
        option_id="relaxed",
        action=relaxed_action,
        operation=operation_for(relaxed_environment, relaxed_action),
        target_id="entity-target",
        require_paused_start=False,
    )
    assert relaxed.prepare(running).status is OptionStatus.PREPARED


def _threat_observation(
    sequence: int,
    *,
    paused: bool,
    threatened: bool,
    in_combat: bool,
    blood: float = 100.0,
) -> Observation:
    current = observation(sequence, paused=paused)
    assert current.telemetry is not None
    return current.model_copy(
        update={
            "telemetry": current.telemetry.model_copy(
                update={
                    "capabilities": [
                        "game.pause",
                        "game.speed",
                        "control.move_in_direction",
                        "nearby.visible_entities",
                        "squad.health",
                    ],
                    "game": current.telemetry.game.model_copy(
                        update={"loaded": True, "speed_multiplier": 1.0}
                    ),
                    "squad": [
                        CharacterState(
                            id="entity-bark",
                            name="Bark",
                            selected=True,
                            alive=True,
                            conscious=True,
                            down=False,
                            blood=blood,
                            in_combat=in_combat,
                            position=Vec3(x=10.0, y=0.0, z=0.0),
                        )
                    ],
                    "ui": UIState(
                        selected_character_id="entity-bark",
                        selected_character_ids=["entity-bark"],
                    ),
                    "nearby_entities": (
                        [
                            NearbyEntity(
                                id="entity-bandit",
                                name="Dust Bandit",
                                disposition=Disposition.HOSTILE,
                                distance=8.0,
                                visible=True,
                                position=Vec3(x=0.0, y=0.0, z=0.0),
                            )
                        ]
                        if threatened
                        else []
                    ),
                }
            )
        },
        deep=True,
    )


def _withdrawal_option(option_id: str) -> StatefulThreatResponseOption:
    environment = BlockingEnvironment()
    action = RespondToImmediateThreatAction(
        actor_id="entity-bark",
        strategy=ThreatResponseStrategy.WITHDRAW,
    )
    pause = PauseAction(paused=True)
    return StatefulThreatResponseOption(
        option_id=option_id,
        action=action,
        operation=operation_for(environment, action),
        withdrawal_operation=operation_family(environment),
        pause_operation=operation_for(environment, pause),
    )


def test_withdrawal_derives_the_escape_vector_instead_of_asking_the_model() -> None:
    option = _withdrawal_option("threat-withdrawal")

    option.prepare(
        _threat_observation(
            1,
            paused=True,
            threatened=True,
            in_combat=True,
        )
    )

    assert option.movement_option is not None
    assert option.movement_option.action.bearing_degrees == 90.0
    assert option.movement_option.action.distance_units == 160.0


def test_withdrawal_prefers_a_squadmate_when_reunion_also_increases_safety() -> None:
    current = _threat_observation(
        1,
        paused=True,
        threatened=True,
        in_combat=True,
    )
    assert current.telemetry is not None
    current = current.model_copy(
        update={
            "telemetry": current.telemetry.model_copy(
                update={
                    "squad": [
                        *current.telemetry.squad,
                        CharacterState(
                            id="entity-plant",
                            name="Plant",
                            alive=True,
                            conscious=True,
                            down=False,
                            blood=100.0,
                            position=Vec3(x=10.0, y=0.0, z=100.0),
                        ),
                    ]
                }
            )
        },
        deep=True,
    )
    option = _withdrawal_option("threat-withdrawal-reunion")

    option.prepare(current)

    assert option.movement_option is not None
    assert option.movement_option.action.bearing_degrees == 0.0
    assert option.movement_option.action.distance_units == 100.0
    assert "Plant" in option.movement_option.action.expected_effect


def test_withdrawal_does_not_cross_a_hostile_to_reach_a_distant_squadmate() -> None:
    current = _threat_observation(
        1,
        paused=True,
        threatened=True,
        in_combat=True,
    )
    assert current.telemetry is not None
    current = current.model_copy(
        update={
            "telemetry": current.telemetry.model_copy(
                update={
                    "squad": [
                        *current.telemetry.squad,
                        CharacterState(
                            id="entity-plant",
                            name="Plant",
                            alive=True,
                            conscious=True,
                            down=False,
                            blood=100.0,
                            position=Vec3(x=-100.0, y=0.0, z=0.0),
                        ),
                    ]
                }
            )
        },
        deep=True,
    )
    option = _withdrawal_option("threat-withdrawal-no-crossing")

    option.prepare(current)

    assert option.movement_option is not None
    assert option.movement_option.action.bearing_degrees == 90.0
    assert option.movement_option.action.distance_units == 160.0
    assert "nearest immediate hostile" in option.movement_option.action.expected_effect
