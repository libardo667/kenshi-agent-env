from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from kenshi_agent.models import (
    Disposition,
    GameState,
    NearbyEntity,
    Observation,
    PauseAction,
    PlannerDecision,
    TelemetrySnapshot,
    WorldStateRevision,
)
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.safety_supervisor import SafetyCause, SafetySupervisor
from kenshi_agent.world_state import WorldStateStore


def observation(
    sequence: int,
    *,
    paused: bool = True,
    capabilities: list[str] | None = None,
    threatened: bool = False,
) -> Observation:
    return Observation(
        run_id="safety-supervisor",
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
        ),
        telemetry_age_seconds=0.0,
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


def test_reflex_without_pause_capability_stops_instead_of_claiming_cleanup() -> None:
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
                threatened=True,
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
