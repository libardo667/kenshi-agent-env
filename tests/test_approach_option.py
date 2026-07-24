"""The monitored approach option (P6 Slice 3b)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from kenshi_agent.env import AgentEnvironment
from kenshi_agent.models import (
    Action,
    ActionReceipt,
    Disposition,
    GameState,
    NearbyEntity,
    Observation,
    SkillAction,
    TelemetrySnapshot,
    Transition,
    UIState,
    WorldStateRevision,
)
from kenshi_agent.options import OptionStatus, StatefulApproachOption
from kenshi_agent.world_state import SequenceStatus, StateDelta, StoreUpdate

TARGET_ID = "entity-barman"


def observation(
    sequence: int,
    *,
    paused: bool = True,
    target_distance: float | None = 40.0,
    target_present: bool = True,
    dialogue_target: str | None = None,
    hostile_distance: float | None = None,
) -> Observation:
    entities: list[NearbyEntity] = []
    if target_present:
        entities.append(
            NearbyEntity(
                id=TARGET_ID,
                name="Barman",
                is_animal=False,
                has_vendor_list=True,
                is_squad_leader=True,
                has_dialogue=True,
                disposition=Disposition.NEUTRAL,
                distance=target_distance,
            )
        )
    if hostile_distance is not None:
        entities.append(
            NearbyEntity(id="entity-bandit", name="Bandit",
                         disposition=Disposition.HOSTILE, distance=hostile_distance)
        )
    return Observation(
        run_id="approach-option-test",
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
            capabilities=["game.pause", "control.approach_vendor", "nearby.roles"],
            game=GameState(paused=paused, elapsed_minutes=0.0),
            nearby_entities=entities,
            ui=UIState(
                dialogue_open=dialogue_target is not None,
                dialogue_target_id=dialogue_target,
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
            changed_paths=("telemetry.nearby_entities",),
        ),
    )


class InstantApproachEnvironment(AgentEnvironment):
    """Dispatch acks immediately (the order is issued); walking is separate."""

    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return observation(1)

    async def observe(self) -> Observation:
        return observation(1)

    async def step(self, action: Action) -> Transition:
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=self.accepted,
                executed=self.accepted,
                dry_run=False,
                message="approach order issued" if self.accepted else "rejected",
            ),
            observation=observation(2),
        )

    async def close(self) -> None:
        return None


def approach_option(environment: AgentEnvironment) -> StatefulApproachOption:
    return StatefulApproachOption(
        option_id="approach-1",
        action=SkillAction(name="approach_confirmed_vendor"),
        environment=environment,
        target_id=TARGET_ID,
        arrival_distance=5.0,
        threat_distance=15.0,
    )


def test_approach_succeeds_only_after_arrival_not_after_the_ack() -> None:
    async def scenario() -> None:
        option = approach_option(InstantApproachEnvironment())
        assert option.prepare(observation(1, target_distance=40.0)).status is OptionStatus.PREPARED
        await option.start()
        await asyncio.sleep(0)

        # Dispatch acked, but the character is still far -- still RUNNING.
        walking = option.poll(update(observation(2, target_distance=40.0)))
        assert walking.status is OptionStatus.RUNNING
        closer = option.poll(update(observation(3, target_distance=18.0)))
        assert closer.status is OptionStatus.RUNNING
        assert "closer" in closer.reason

        # Dialogue opens with the exact target -> success.
        arrived = option.poll(
            update(observation(4, target_distance=12.0, dialogue_target=TARGET_ID))
        )
        assert arrived.status is OptionStatus.SUCCEEDED
        assert "Dialogue opened" in arrived.reason
        assert option.result().receipt.accepted is True

    asyncio.run(scenario())


def test_approach_succeeds_by_arrival_radius() -> None:
    async def scenario() -> None:
        option = approach_option(InstantApproachEnvironment())
        option.prepare(observation(1, target_distance=40.0))
        await option.start()
        await asyncio.sleep(0)
        option.poll(update(observation(2, target_distance=40.0)))
        arrived = option.poll(update(observation(3, target_distance=4.0)))
        assert arrived.status is OptionStatus.SUCCEEDED

    asyncio.run(scenario())


def test_target_loss_during_approach_fails() -> None:
    async def scenario() -> None:
        option = approach_option(InstantApproachEnvironment())
        option.prepare(observation(1, target_distance=40.0))
        await option.start()
        await asyncio.sleep(0)
        option.poll(update(observation(2, target_distance=40.0)))
        lost = option.poll(update(observation(3, target_present=False)))
        assert lost.status is OptionStatus.FAILED
        assert "no longer" in lost.reason

    asyncio.run(scenario())


def test_hostile_in_range_during_approach_fails() -> None:
    async def scenario() -> None:
        option = approach_option(InstantApproachEnvironment())
        option.prepare(observation(1, target_distance=40.0))
        await option.start()
        await asyncio.sleep(0)
        option.poll(update(observation(2, target_distance=30.0)))
        threatened = option.poll(
            update(observation(3, target_distance=25.0, hostile_distance=10.0))
        )
        assert threatened.status is OptionStatus.FAILED
        assert "hostile" in threatened.reason

    asyncio.run(scenario())


def test_rejected_dispatch_fails_without_walking() -> None:
    async def scenario() -> None:
        option = approach_option(InstantApproachEnvironment(accepted=False))
        option.prepare(observation(1, target_distance=40.0))
        await option.start()
        await asyncio.sleep(0)
        result = option.poll(update(observation(2, target_distance=40.0)))
        assert result.status is OptionStatus.FAILED
        assert "rejected" in result.reason

    asyncio.run(scenario())


def test_prepare_requires_present_target_and_paused_state() -> None:
    import pytest

    from kenshi_agent.options import OptionLifecycleError

    with pytest.raises(OptionLifecycleError, match="target"):
        approach_option(InstantApproachEnvironment()).prepare(
            observation(1, target_present=False)
        )
    with pytest.raises(OptionLifecycleError, match="paused"):
        approach_option(InstantApproachEnvironment()).prepare(
            observation(1, paused=False)
        )


def test_cancellation_during_approach_is_idempotent() -> None:
    async def scenario() -> None:
        option = approach_option(InstantApproachEnvironment())
        option.prepare(observation(1, target_distance=40.0))
        await option.start()
        await asyncio.sleep(0)
        first = await option.cancel("human input")
        second = await option.cancel("human input again")
        assert first.status is OptionStatus.CANCELLED
        assert second.status is OptionStatus.CANCELLED

    asyncio.run(scenario())
