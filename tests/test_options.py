from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from kenshi_agent.env import AgentEnvironment
from kenshi_agent.models import (
    Action,
    ActionReceipt,
    GameState,
    Observation,
    SkillAction,
    TelemetrySnapshot,
    Transition,
    WorldStateRevision,
)
from kenshi_agent.options import OptionStatus, StatefulMovementOption


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
        option = StatefulMovementOption(
            option_id="option-success",
            action=SkillAction(name="move"),
            environment=environment,
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
        option = StatefulMovementOption(
            option_id="option-cancel",
            action=SkillAction(name="move"),
            environment=environment,
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
        option = StatefulMovementOption(
            option_id="option-cleanup-failure",
            action=SkillAction(name="move"),
            environment=environment,
        )
        option.prepare(observation(1))
        task = option.start()
        await asyncio.sleep(0)

        cancelled = await option.cancel("safety preemption")

        assert cancelled.status is OptionStatus.FAILED
        assert "re-pause confirmation failed" in cancelled.reason
        assert task.done()

    asyncio.run(scenario())
