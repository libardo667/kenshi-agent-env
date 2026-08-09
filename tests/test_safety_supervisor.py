from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import PauseAction
from kenshi_agent.core.planning import PlannerDecision
from kenshi_agent.core.telemetry import (
    CharacterState,
    Disposition,
    GameState,
    NearbyEntity,
    TelemetrySnapshot,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.safety_supervisor import SafetyCause, SafetySupervisor
from kenshi_agent.world_state import WorldStateStore


def observation(
    sequence: int,
    *,
    paused: bool = True,
    capabilities: list[str] | None = None,
    threatened: bool = False,
    getting_eaten: bool = False,
    mode: str = "mock",
    age_seconds: float = 0.0,
    events: list[str] | None = None,
) -> Observation:
    return Observation(
        run_id="safety-supervisor",
        step_index=sequence,
        mode=mode,
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            frame_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            capabilities=capabilities if capabilities is not None else ["game.pause", "game.time"],
            game=GameState(
                loaded=True,
                paused=paused,
                elapsed_minutes=float(sequence),
            ),
            nearby_entities=(
                [
                    NearbyEntity(
                        id="threat",
                        name="Hungry Bandit",
                        disposition=Disposition.HOSTILE,
                        distance=10.0,
                        visible=True,
                    )
                ]
                if threatened
                else []
            ),
            roster=(
                [
                    CharacterState(
                        id="entity-bark",
                        name="Bark",
                        alive=True,
                        conscious=True,
                        getting_eaten=True,
                    )
                ]
                if getting_eaten
                else []
            ),
        ),
        telemetry_age_seconds=age_seconds,
        events=events or [],
    )


def pause_decision(reason: str) -> PlannerDecision:
    return PlannerDecision(
        intent="Restore deterministic safe pause.",
        rationale=reason,
        action=PauseAction(paused=True),
        confidence=1.0,
    )


def test_preemption_request_and_stop_are_idempotent() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        current = store.publish(observation(1)).observation
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
        )
        await supervisor.start()

        assert supervisor.request_preemption(
            cause=SafetyCause.UNEXPECTED_UNPAUSE,
            reason="test preemption",
            observation=current,
            decision=pause_decision("test preemption"),
        )
        assert not supervisor.request_preemption(
            cause=SafetyCause.UNEXPECTED_UNPAUSE,
            reason="duplicate",
            observation=current,
            decision=pause_decision("duplicate"),
        )

        preemption = await supervisor.wait_for_preemption()
        assert preemption.cause is SafetyCause.UNEXPECTED_UNPAUSE
        assert supervisor.metrics.preemptions_requested == 1
        assert supervisor.metrics.duplicate_requests == 1
        assert len(store.events(event_type="safety_preemption_requested")) == 1

        await supervisor.stop()
        await supervisor.stop()
        assert supervisor.task is None
        assert store.subscription_count == 0

    asyncio.run(scenario())


def test_capability_withdrawal_preempts_without_treating_missing_pause_as_false() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        store.publish(observation(1))
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
        )
        await supervisor.start()
        withdrawn = observation(
            2,
            paused=False,
            capabilities=["game.time"],
        )

        store.publish(withdrawn)

        preemption = await asyncio.wait_for(
            supervisor.wait_for_preemption(),
            timeout=1.0,
        )
        assert preemption.cause is SafetyCause.PAUSE_CAPABILITY_WITHDRAWN
        assert preemption.decision.action.kind == "stop"
        assert "capability" in preemption.reason
        await supervisor.stop()

    asyncio.run(scenario())


def test_sequence_stall_threshold_is_consecutive_and_deterministic() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        current = store.publish(observation(1)).observation
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
        )
        await supervisor.start()

        store.publish(current)
        await asyncio.sleep(0)
        assert not supervisor.preempted
        store.publish(current)

        preemption = await asyncio.wait_for(
            supervisor.wait_for_preemption(),
            timeout=1.0,
        )
        assert preemption.cause is SafetyCause.SEQUENCE_STALLED
        assert supervisor.metrics.sequence_stall_preemptions == 1
        await supervisor.stop()

    asyncio.run(scenario())


def test_terminal_window_preempts_before_sequence_stall_and_never_requests_pause() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        current = store.publish(observation(1)).observation
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
        )
        await supervisor.start()

        store.publish(
            current.model_copy(
                update={"events": ["terminal_window_detected: Kenshi has crashed"]}
            )
        )

        preemption = await asyncio.wait_for(
            supervisor.wait_for_preemption(),
            timeout=1.0,
        )
        assert preemption.cause.value == "host_terminal"
        assert preemption.decision.action.kind == "stop"
        assert "Kenshi has crashed" in preemption.reason
        assert supervisor.metrics.host_terminal_preemptions == 1
        assert supervisor.metrics.sequence_stall_preemptions == 0
        await supervisor.stop()

    asyncio.run(scenario())


def test_live_sequence_stall_waits_for_wall_age_before_counting_duplicates() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        current = store.publish(
            observation(1, mode="live", age_seconds=0.1)
        ).observation
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
            minimum_live_stall_age_seconds=1.0,
        )
        await supervisor.start()

        for _ in range(4):
            store.publish(current)
            await asyncio.sleep(0)
        assert not supervisor.preempted

        aged = current.model_copy(update={"telemetry_age_seconds": 1.1})
        store.publish(aged)
        await asyncio.sleep(0)
        assert not supervisor.preempted
        store.publish(aged)

        preemption = await asyncio.wait_for(
            supervisor.wait_for_preemption(),
            timeout=1.0,
        )
        assert preemption.cause is SafetyCause.SEQUENCE_STALLED
        await supervisor.stop()

    asyncio.run(scenario())


def test_catastrophic_reflex_without_pause_capability_stops() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        store.publish(observation(1, capabilities=["game.time"]))
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
        )
        await supervisor.start()

        store.publish(
            observation(
                2,
                paused=False,
                capabilities=["game.time"],
                getting_eaten=True,
            )
        )

        preemption = await asyncio.wait_for(
            supervisor.wait_for_preemption(),
            timeout=1.0,
        )
        assert preemption.cause is SafetyCause.REFLEX
        assert preemption.decision.action.kind == "stop"
        assert "unavailable" in preemption.decision.rationale
        await supervisor.stop()

    asyncio.run(scenario())


def test_human_input_event_preempts_an_authorized_active_plan() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        current = store.publish(observation(1)).observation
        store.activate_plan("plan", 1, current.world_revision)
        store.activate_step("move")
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
        )
        await supervisor.start()

        store.publish(
            observation(2, paused=False).model_copy(
                update={"events": ["human_input_detected"]}
            )
        )

        preemption = await asyncio.wait_for(
            supervisor.wait_for_preemption(),
            timeout=1.0,
        )
        assert preemption.cause is SafetyCause.HUMAN_INPUT
        assert isinstance(preemption.decision.action, PauseAction)
        assert supervisor.metrics.human_input_preemptions == 1
        await supervisor.stop()

    asyncio.run(scenario())


def test_human_input_in_a_confirmed_pause_preserves_the_handoff_boundary() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        current = store.publish(observation(1)).observation
        store.activate_plan("plan", 1, current.world_revision)
        store.activate_step("move")
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
        )
        await supervisor.start()

        store.publish(
            observation(2, paused=True).model_copy(
                update={"events": ["human_input_detected"]}
            )
        )

        preemption = await asyncio.wait_for(
            supervisor.wait_for_preemption(),
            timeout=1.0,
        )
        assert preemption.cause is SafetyCause.HUMAN_INPUT
        assert isinstance(preemption.decision.action, PauseAction)
        assert preemption.decision.action.paused is True
        assert "already confirmed paused" in preemption.decision.rationale
        await supervisor.stop()

    asyncio.run(scenario())


def test_emergency_stop_event_preempts_an_authorized_active_plan() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        current = store.publish(observation(1)).observation
        store.activate_plan("plan", 1, current.world_revision)
        supervisor = SafetySupervisor(
            store=store,
            reflexes=ReflexEngine(),
            max_sequence_stalls=2,
        )
        await supervisor.start()

        store.publish(
            observation(
                2,
                events=["human_input_detected", "emergency_stop_detected"],
            )
        )

        preemption = await asyncio.wait_for(
            supervisor.wait_for_preemption(),
            timeout=1.0,
        )
        assert preemption.cause is SafetyCause.EMERGENCY_STOP
        assert preemption.decision.action.kind == "stop"
        await supervisor.stop()

    asyncio.run(scenario())


def test_an_unpaused_game_can_be_normal_for_a_continuously_playing_agent() -> None:
    """The unpause reflex is a stop-motion assumption, not a universal one.

    Pausing between every action suits a careful supervised experiment. An agent
    meant to play Kenshi continuously runs an unpaused game by definition, and
    preempting on that ended three otherwise-healthy live runs.
    """

    from kenshi_agent.safety_supervisor import SafetyCause

    store = WorldStateStore()
    strict = SafetySupervisor(
        store=store, reflexes=ReflexEngine(), max_sequence_stalls=3
    )
    relaxed = SafetySupervisor(
        store=store,
        reflexes=ReflexEngine(),
        max_sequence_stalls=3,
        require_paused_between_actions=False,
    )
    assert strict.require_paused_between_actions is True
    assert relaxed.require_paused_between_actions is False

    store.publish(observation(1))
    update = store.publish(observation(2, paused=False))

    strict_result = strict._evaluate(update)
    assert strict_result is not None
    assert strict_result.cause is SafetyCause.UNEXPECTED_UNPAUSE

    # The same state is unremarkable for an agent that is meant to be playing.
    assert relaxed._evaluate(update) is None


def test_hostility_is_ordinary_but_getting_eaten_still_preempts() -> None:
    store = WorldStateStore()
    store.publish(observation(1))
    supervisor = SafetySupervisor(
        store=store,
        reflexes=ReflexEngine(),
        max_sequence_stalls=3,
        require_paused_between_actions=False,
    )

    ordinary = store.publish(
        observation(2, paused=False, threatened=True)
    )
    assert supervisor._evaluate(ordinary) is None

    catastrophic_observation = observation(
        3,
        paused=False,
        threatened=True,
    )
    assert catastrophic_observation.telemetry is not None
    catastrophic_observation = catastrophic_observation.model_copy(
        update={
            "telemetry": catastrophic_observation.telemetry.model_copy(
                update={
                    "roster": [
                        CharacterState(
                            id="entity-bark",
                            name="Bark",
                            alive=True,
                            conscious=True,
                            getting_eaten=True,
                        )
                    ]
                }
            )
        },
        deep=True,
    )
    catastrophic = store.publish(catastrophic_observation)

    preemption = supervisor._evaluate(catastrophic)
    assert preemption is not None
    assert preemption.cause is SafetyCause.REFLEX
