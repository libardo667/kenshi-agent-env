from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.env import AgentEnvironment
from kenshi_agent.models import (
    Action,
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionReceipt,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionResult,
    Disposition,
    GameState,
    NearbyEntity,
    Observation,
    StopAction,
    TelemetrySnapshot,
    Transition,
    WorldStateRevision,
)
from kenshi_agent.planning import PlanningClock, evaluate_condition
from kenshi_agent.world_state import (
    CommandCausalityError,
    ObservationPump,
    RevisionConflictError,
    RevisionRegressionError,
    SequenceStatus,
    WorldStateClosedError,
    WorldStateStore,
)


class ManualClock(PlanningClock):
    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        deadline = self.now + seconds
        future = asyncio.get_running_loop().create_future()
        self._sleepers.append((deadline, future))
        await future

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in self._sleepers:
            if deadline <= self.now and not future.done():
                future.set_result(None)
        self._sleepers = [
            (deadline, future) for deadline, future in self._sleepers if not future.done()
        ]


def entity(
    source_id: str,
    name: str,
    *,
    x: float,
) -> NearbyEntity:
    return NearbyEntity.model_validate(
        {
            "id": source_id,
            "name": name,
            "kind": "character",
            "faction": "Nomads",
            "disposition": Disposition.NEUTRAL,
            "position": {"x": x, "y": 0.0, "z": 0.0},
            "visible": True,
        }
    )


def observation(
    sequence: int,
    *,
    paused: bool = True,
    events: list[str] | None = None,
    nearby: list[NearbyEntity] | None = None,
) -> Observation:
    return Observation(
        run_id="world-state",
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
            capabilities=["game.pause", "game.time", "nearby.characters"],
            game=GameState(
                loaded=True,
                paused=paused,
                elapsed_minutes=float(sequence),
            ),
            nearby_entities=nearby or [],
        ),
        telemetry_age_seconds=0.0,
        events=events or [],
    )


def test_store_detects_duplicate_stall_and_rejects_revision_regression() -> None:
    store = WorldStateStore()
    first = observation(4)

    assert store.publish(first).sequence_status is SequenceStatus.INITIAL
    assert store.publish(first).sequence_status is SequenceStatus.DUPLICATE
    assert store.metrics.sequence_stall_incidents == 1

    with pytest.raises(RevisionRegressionError, match="regressed"):
        store.publish(observation(3))

    assert store.metrics.revision_regressions == 1


def test_planner_context_can_decorate_only_the_latest_revision() -> None:
    store = WorldStateStore()
    first = store.publish(observation(1)).observation
    outcome = ActionOutcome(
        step_index=0,
        intent="Pause safely.",
        action=StopAction(reason="unused"),
        executed=True,
        assessment=ActionOutcomeAssessment.UNKNOWN,
        feedback="No later causal observation.",
    )
    decorated = first.model_copy(
        update={
            "objective": "Retain causal context.",
            "recent_action_outcomes": [outcome],
        }
    )

    latest = store.decorate_latest(decorated)

    assert latest.objective == "Retain causal context."
    assert latest.recent_action_outcomes == [outcome]
    assert store.history()[-1].recent_action_outcomes == [outcome]
    with pytest.raises(RevisionConflictError, match="current world-state revision"):
        store.decorate_latest(observation(2))


def test_causal_wait_cannot_succeed_from_starting_revision_and_uses_fake_clock() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        store = WorldStateStore(clock=clock)
        first = observation(1, paused=True)
        store.publish(first)

        wait = asyncio.create_task(
            store.wait_for(
                lambda item: item.telemetry is not None and item.telemetry.game.paused is False,
                after_revision=first.world_revision,
                timeout_seconds=5.0,
            )
        )
        await asyncio.sleep(0)
        store.publish(first)
        await asyncio.sleep(0)
        assert not wait.done()

        later = observation(2, paused=False)
        store.publish(later)
        assert await wait == later

        timeout = asyncio.create_task(
            store.wait_for(
                lambda _: False,
                after_revision=later.world_revision,
                timeout_seconds=2.0,
            )
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        clock.advance(2.0)
        with pytest.raises(TimeoutError):
            await timeout
        assert store.subscription_count == 0

        cancelled = asyncio.create_task(
            store.wait_for(
                lambda _: False,
                after_revision=later.world_revision,
                timeout_seconds=10.0,
            )
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert store.subscription_count == 1
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert store.subscription_count == 0

    asyncio.run(scenario())


def test_transient_events_survive_latest_snapshot_replacement() -> None:
    emitted = []
    store = WorldStateStore(event_limit=8, event_sink=emitted.append)

    store.publish(observation(1, events=["attack_started"]))
    store.publish(observation(2))

    assert store.latest is not None
    assert store.latest.events == []
    retained = store.events(event_type="observation_event")
    assert [event.payload["message"] for event in retained] == ["attack_started"]
    assert any(event.event_type == "observation_event" for event in emitted)
    assert store.metrics.transient_events_retained == 1


def test_telemetry_only_update_carries_last_validated_visual_revision() -> None:
    store = WorldStateStore()
    visual = observation(1).model_copy(
        update={
            "screenshot_path": Path("frame-1.png"),
            "screenshot_sha256": "frame-one",
        }
    )
    store.publish(visual)
    telemetry_only = observation(2).model_copy(
        update={
            "world_revision": observation(2).world_revision.model_copy(
                update={"frame_sequence": None}
            ),
            "screenshot_path": None,
            "screenshot_sha256": None,
        }
    )

    store.publish(telemetry_only)

    latest = store.latest
    assert latest is not None
    assert latest.world_revision.frame_sequence == 1
    assert latest.screenshot_path == Path("frame-1.png")
    assert latest.screenshot_sha256 == "frame-one"


def test_state_history_deltas_and_transient_journal_are_bounded() -> None:
    store = WorldStateStore(
        history_limit=2,
        delta_limit=2,
        event_limit=2,
        max_delta_paths=16,
    )

    for sequence in range(1, 4):
        store.publish(
            observation(
                sequence,
                paused=sequence % 2 == 0,
                events=[f"transient-{sequence}"],
            )
        )

    assert len(store.history()) == 2
    assert len(store.deltas()) == 2
    assert all(len(delta.changed_paths) <= 16 for delta in store.deltas())
    assert len(store.events()) == 2
    assert store.metrics.transient_events_retained == 3
    assert store.metrics.transient_events_lost == 1


def test_entity_registry_survives_source_id_reordering_and_closes_lifetime() -> None:
    store = WorldStateStore()
    store.publish(
        observation(
            1,
            nearby=[
                entity("nearby:0", "Alice", x=1.0),
                entity("nearby:1", "Bob", x=5.0),
            ],
        )
    )
    alice_first = next(item for item in store.entity_lifetimes() if item.name == "Alice")

    store.publish(
        observation(
            2,
            nearby=[
                entity("nearby:0", "Bob", x=5.1),
                entity("nearby:1", "Alice", x=1.1),
            ],
        )
    )
    alice_reordered = next(item for item in store.entity_lifetimes() if item.name == "Alice")
    latest = store.latest
    assert latest is not None and latest.telemetry is not None
    stable_by_name = {item.name: item.id for item in latest.telemetry.nearby_entities}
    assert alice_reordered.stable_id == alice_first.stable_id
    assert stable_by_name["Alice"] == alice_first.stable_id
    assert alice_reordered.source_ids == ["nearby:0", "nearby:1"]
    assert alice_reordered.active

    store.publish(observation(3, nearby=[entity("nearby:0", "Bob", x=5.2)]))
    alice_gone = next(item for item in store.entity_lifetimes() if item.name == "Alice")
    assert not alice_gone.active
    assert alice_gone.ended_revision is not None
    assert alice_gone.ended_revision.telemetry_sequence == 3
    alice_visible = Condition(
        kind=ConditionKind.FIELD,
        path="target.visible",
        operator=ConditionOperator.EQUALS,
        expected=True,
        target_id=alice_first.stable_id,
        max_age_seconds=3.0,
    )
    assert evaluate_condition(alice_visible, latest).result is ConditionResult.TRUE
    current = store.latest
    assert current is not None
    assert evaluate_condition(alice_visible, current).result is ConditionResult.UNKNOWN


def test_entity_registry_does_not_conflate_duplicate_names_when_ordinals_swap() -> None:
    store = WorldStateStore()
    store.publish(
        observation(
            1,
            nearby=[
                entity("nearby:0", "Nomad", x=1.0),
                entity("nearby:1", "Nomad", x=9.0),
            ],
        )
    )
    first = store.latest
    assert first is not None and first.telemetry is not None
    stable_by_position = {
        item.position.x: item.id
        for item in first.telemetry.nearby_entities
        if item.position is not None
    }

    store.publish(
        observation(
            2,
            nearby=[
                entity("nearby:0", "Nomad", x=9.1),
                entity("nearby:1", "Nomad", x=1.1),
            ],
        )
    )

    second = store.latest
    assert second is not None and second.telemetry is not None
    left = min(
        second.telemetry.nearby_entities,
        key=lambda item: item.position.x if item.position is not None else float("inf"),
    )
    right = max(
        second.telemetry.nearby_entities,
        key=lambda item: item.position.x if item.position is not None else float("-inf"),
    )
    assert left.id == stable_by_position[1.0]
    assert right.id == stable_by_position[9.0]
    assert sum(lifetime.ambiguous_matches for lifetime in store.entity_lifetimes()) == 1


def test_capability_withdrawal_advances_epoch_and_blocks_field_use() -> None:
    store = WorldStateStore()
    first = observation(1)
    store.publish(first)
    without_pause = observation(2)
    assert without_pause.telemetry is not None
    without_pause = without_pause.model_copy(
        update={
            "telemetry": without_pause.telemetry.model_copy(
                update={"capabilities": ["game.time", "nearby.characters"]}
            )
        }
    )

    update = store.publish(without_pause)

    assert update.observation.world_revision.capability_epoch == 2
    pause_known = Condition(
        kind=ConditionKind.FIELD,
        path="telemetry.game.paused",
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )
    assert evaluate_condition(pause_known, update.observation).result is ConditionResult.UNAVAILABLE
    assert any(
        event.event_type == "capabilities_changed" and event.payload["removed"] == ["game.pause"]
        for event in update.events
    )


def test_missing_nearby_capability_does_not_end_entity_lifetimes() -> None:
    store = WorldStateStore()
    store.publish(
        observation(
            1,
            nearby=[entity("nearby:0", "Alice", x=1.0)],
        )
    )
    unavailable = observation(2)
    assert unavailable.telemetry is not None
    unavailable = unavailable.model_copy(
        update={
            "telemetry": unavailable.telemetry.model_copy(
                update={"capabilities": ["game.pause", "game.time"]}
            )
        }
    )

    update = store.publish(unavailable)

    alice = next(item for item in store.entity_lifetimes() if item.name == "Alice")
    assert alice.active
    assert not any(event.event_type == "entity_disappeared" for event in update.events)

    restored_update = store.publish(observation(3))
    alice = next(item for item in store.entity_lifetimes() if item.name == "Alice")
    assert not alice.active
    assert any(event.event_type == "entity_disappeared" for event in restored_update.events)


def test_subscribers_share_one_published_update_without_transport_polling() -> None:
    async def scenario() -> None:
        store = WorldStateStore(subscriber_queue_limit=2)
        first = store.subscribe()
        second = store.subscribe()

        expected = store.publish(observation(1))

        first_update = await first.get()
        second_update = await second.get()
        assert first_update == expected
        assert second_update == expected
        first_update.observation.step_index = 99
        assert second_update.observation.step_index == 1
        assert store.latest is not None
        assert store.latest.step_index == 1
        first.close()
        second.close()
        assert store.subscription_count == 0

    asyncio.run(scenario())


def test_subscriber_event_payloads_are_isolated() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        store.publish(observation(1))
        first = store.subscribe()
        second = store.subscribe()
        changed = observation(2)
        assert changed.telemetry is not None
        changed = changed.model_copy(
            update={
                "telemetry": changed.telemetry.model_copy(
                    update={"capabilities": ["game.time", "nearby.characters"]}
                )
            }
        )

        store.publish(changed)

        first_update = await first.get()
        second_update = await second.get()
        first_event = next(
            event for event in first_update.events if event.event_type == "capabilities_changed"
        )
        second_event = next(
            event for event in second_update.events if event.event_type == "capabilities_changed"
        )
        removed = first_event.payload["removed"]
        assert isinstance(removed, list)
        removed.append("mutated")
        assert second_event.payload["removed"] == ["game.pause"]
        assert store.events(event_type="capabilities_changed")[0].payload["removed"] == [
            "game.pause"
        ]
        first.close()
        second.close()

    asyncio.run(scenario())


def test_slow_subscriber_drops_oldest_update_with_a_metric() -> None:
    async def scenario() -> None:
        store = WorldStateStore(subscriber_queue_limit=2)
        subscription = store.subscribe()

        store.publish(observation(1))
        store.publish(observation(2))
        store.publish(observation(3))

        assert store.metrics.subscriber_drops == 1
        assert (await subscription.get()).observation.step_index == 2
        assert (await subscription.get()).observation.step_index == 3
        subscription.close()

    asyncio.run(scenario())


def test_store_shutdown_wakes_waiting_subscribers_without_a_leak() -> None:
    async def scenario() -> None:
        store = WorldStateStore()
        subscription = store.subscribe()
        waiting = asyncio.create_task(subscription.get())
        await asyncio.sleep(0)

        store.shutdown()

        with pytest.raises(WorldStateClosedError):
            await waiting
        assert store.subscription_count == 0

    asyncio.run(scenario())


def test_active_plan_and_command_causality_are_executor_owned() -> None:
    store = WorldStateStore()
    first = observation(1)
    second = observation(2)
    store.publish(first)
    store.activate_plan("plan", 1, first.world_revision)
    store.activate_step("step-a")

    command = store.begin_command(
        plan_id="plan",
        plan_version=1,
        step_id="step-a",
        action_kind="pause",
        start_revision=first.world_revision,
    )
    with pytest.raises(CommandCausalityError, match="does not match"):
        store.complete_command("wrong-command", second.world_revision)

    completed = store.complete_command(command.command_id, second.world_revision)
    assert completed.completed_revision == second.world_revision
    assert store.active_command is None
    assert store.active_plan is not None
    assert store.active_plan.step_id == "step-a"

    store.clear_active_plan("completed")
    assert store.active_plan is None


def test_store_update_preserves_authorization_state_at_publish_time() -> None:
    store = WorldStateStore()
    first = observation(1)
    second = observation(2)
    store.publish(first)
    store.activate_plan("plan", 1, first.world_revision)
    store.activate_step("step-a")
    command = store.begin_command(
        plan_id="plan",
        plan_version=1,
        step_id="step-a",
        action_kind="pause",
        start_revision=first.world_revision,
    )

    update = store.publish(second)
    store.complete_command(command.command_id, second.world_revision)
    store.clear_active_plan("completed")

    assert update.active_plan is not None
    assert update.active_plan.plan_id == "plan"
    assert update.active_plan.step_id == "step-a"
    assert update.active_command is not None
    assert update.active_command.command_id == command.command_id
    assert store.active_plan is None
    assert store.active_command is None


class ScriptedObservationEnvironment(AgentEnvironment):
    def __init__(self, observations: list[Observation]) -> None:
        self.observations = observations
        self.index = 0
        self.observation_calls: list[str] = []
        self.closed = False

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return self.observations[0]

    async def observe(self) -> Observation:
        self.observation_calls.append("capture")
        self.index += 1
        return self.observations[self.index]

    async def observe_without_capture(self) -> Observation:
        self.observation_calls.append("telemetry")
        self.index += 1
        return self.observations[self.index]

    async def step(self, action: Action) -> Transition:
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
            ),
            observation=self.observations[self.index],
        )

    async def close(self) -> None:
        self.closed = True


def test_observation_pump_capture_trigger_and_shutdown_are_deterministic() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        environment = ScriptedObservationEnvironment(
            [observation(1), observation(2), observation(3)]
        )
        store = WorldStateStore(clock=clock)
        store.publish(await environment.reset())
        pump = ObservationPump(
            environment,
            store,
            interval_seconds=1.0,
            clock=clock,
        )
        await pump.start()

        clock.advance(1.0)
        await asyncio.sleep(0)
        assert store.latest is not None
        assert store.latest.world_revision.telemetry_sequence == 2
        assert environment.observation_calls == ["telemetry"]

        pump.request_capture()
        clock.advance(1.0)
        await asyncio.sleep(0)
        assert store.latest is not None
        assert store.latest.world_revision.telemetry_sequence == 3
        assert environment.observation_calls == ["telemetry", "capture"]

        await pump.stop()
        assert not pump.running
        assert pump.task is None

    asyncio.run(scenario())
