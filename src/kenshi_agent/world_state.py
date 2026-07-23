from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from math import dist
from typing import TypeAlias

from .env import AgentEnvironment
from .models import NearbyEntity, Observation, WorldStateRevision
from .planning import PlanningClock, SystemPlanningClock


class WorldStateError(RuntimeError):
    pass


class RevisionRegressionError(WorldStateError):
    pass


class RevisionConflictError(WorldStateError):
    pass


class CommandCausalityError(WorldStateError):
    pass


class WorldStateClosedError(WorldStateError):
    pass


class SequenceStatus(StrEnum):
    INITIAL = "initial"
    ADVANCED = "advanced"
    DUPLICATE = "duplicate"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class StateDelta:
    before_revision: WorldStateRevision | None
    after_revision: WorldStateRevision
    changed_paths: tuple[str, ...]
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class WorldEvent:
    event_id: int
    event_type: str
    revision: WorldStateRevision | None
    observed_at_monotonic: float
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EntityLifetime:
    stable_id: str
    name: str
    kind: str
    faction: str | None
    source_ids: list[str]
    first_revision: WorldStateRevision
    last_revision: WorldStateRevision
    ended_revision: WorldStateRevision | None
    active: bool
    ambiguous_matches: int


@dataclass(slots=True)
class _EntityRecord:
    stable_id: str
    name: str
    kind: str
    faction: str | None
    is_animal: bool | None
    source_ids: set[str]
    first_revision: WorldStateRevision
    last_revision: WorldStateRevision
    position: tuple[float, float, float] | None
    ended_revision: WorldStateRevision | None = None
    active: bool = True
    ambiguous_matches: int = 0

    @property
    def fingerprint(self) -> tuple[str, str, str | None, bool | None]:
        return self.name, self.kind, self.faction, self.is_animal

    def public(self) -> EntityLifetime:
        return EntityLifetime(
            stable_id=self.stable_id,
            name=self.name,
            kind=self.kind,
            faction=self.faction,
            source_ids=sorted(self.source_ids),
            first_revision=self.first_revision.model_copy(deep=True),
            last_revision=self.last_revision.model_copy(deep=True),
            ended_revision=(
                self.ended_revision.model_copy(deep=True)
                if self.ended_revision is not None
                else None
            ),
            active=self.active,
            ambiguous_matches=self.ambiguous_matches,
        )


@dataclass(frozen=True, slots=True)
class ActivePlanState:
    plan_id: str
    plan_version: int
    accepted_revision: WorldStateRevision
    step_id: str | None = None
    status: str = "accepted"


@dataclass(frozen=True, slots=True)
class CommandState:
    command_id: str
    plan_id: str
    plan_version: int
    step_id: str
    action_kind: str
    started_revision: WorldStateRevision
    started_at_monotonic: float
    completed_revision: WorldStateRevision | None = None
    completed_at_monotonic: float | None = None
    causally_advanced: bool | None = None


@dataclass(slots=True)
class WorldStateMetrics:
    observations_published: int = 0
    sequence_stall_incidents: int = 0
    revision_regressions: int = 0
    revision_conflicts: int = 0
    transient_events_retained: int = 0
    transient_events_lost: int = 0
    journal_events_evicted: int = 0
    subscriber_drops: int = 0
    pump_errors: int = 0
    entity_lifetimes_started: int = 0
    entity_lifetimes_ended: int = 0
    command_mismatches: int = 0


@dataclass(frozen=True, slots=True)
class StoreUpdate:
    observation: Observation
    sequence_status: SequenceStatus
    delta: StateDelta
    events: tuple[WorldEvent, ...] = ()


class WorldStateSubscription:
    def __init__(
        self,
        store: WorldStateStore,
        subscription_id: int,
        queue: asyncio.Queue[StoreUpdate | None],
    ) -> None:
        self._store = store
        self.subscription_id = subscription_id
        self._queue = queue
        self._closed = False

    async def get(self) -> StoreUpdate:
        if self._closed:
            raise RuntimeError("World-state subscription is closed.")
        update = await self._queue.get()
        if update is None:
            self._closed = True
            raise WorldStateClosedError("World-state store is closed.")
        return update

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._store._unsubscribe(self.subscription_id)

    async def __aenter__(self) -> WorldStateSubscription:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self.close()


ObservationPredicate: TypeAlias = Callable[[Observation], bool]
EventCallback: TypeAlias = Callable[[WorldEvent], None]
_ENTITY_LIST_CAPABILITIES = frozenset(
    {
        "nearby.characters",
        "nearby.visible_entities",
    }
)


class WorldStateStore:
    """Single-writer-style bounded state stream for all continuous consumers."""

    def __init__(
        self,
        *,
        history_limit: int = 128,
        delta_limit: int = 128,
        event_limit: int = 256,
        subscriber_queue_limit: int = 32,
        max_delta_paths: int = 128,
        clock: PlanningClock | None = None,
        event_sink: EventCallback | None = None,
    ) -> None:
        if (
            min(
                history_limit,
                delta_limit,
                event_limit,
                subscriber_queue_limit,
                max_delta_paths,
            )
            <= 0
        ):
            raise ValueError("World-state bounds must all be positive.")
        self.history_limit = history_limit
        self.delta_limit = delta_limit
        self.event_limit = event_limit
        self.subscriber_queue_limit = subscriber_queue_limit
        self.max_delta_paths = max_delta_paths
        self.clock = clock or SystemPlanningClock()
        self.event_sink = event_sink
        self.metrics = WorldStateMetrics()
        self._latest: Observation | None = None
        self._history: deque[Observation] = deque(maxlen=history_limit)
        self._deltas: deque[StateDelta] = deque(maxlen=delta_limit)
        self._journal: deque[WorldEvent] = deque(maxlen=event_limit)
        self._subscribers: dict[int, asyncio.Queue[StoreUpdate | None]] = {}
        self._next_subscription_id = 1
        self._next_event_id = 1
        self._entities: dict[str, _EntityRecord] = {}
        self._next_entity_id = 1
        self._active_plan: ActivePlanState | None = None
        self._active_command: CommandState | None = None
        self._command_history: deque[CommandState] = deque(maxlen=event_limit)
        self._next_command_id = 1
        self._closed = False

    @property
    def latest(self) -> Observation | None:
        return self._latest.model_copy(deep=True) if self._latest is not None else None

    @property
    def active_plan(self) -> ActivePlanState | None:
        return (
            replace(
                self._active_plan,
                accepted_revision=self._active_plan.accepted_revision.model_copy(deep=True),
            )
            if self._active_plan is not None
            else None
        )

    @property
    def active_command(self) -> CommandState | None:
        return self._copy_command(self._active_command)

    @property
    def subscription_count(self) -> int:
        return len(self._subscribers)

    def history(self) -> list[Observation]:
        return [item.model_copy(deep=True) for item in self._history]

    def deltas(self) -> list[StateDelta]:
        return [self._copy_delta(delta) for delta in self._deltas]

    def events(self, *, event_type: str | None = None) -> list[WorldEvent]:
        return [
            self._copy_event(event)
            for event in self._journal
            if event_type is None or event.event_type == event_type
        ]

    def entity_lifetimes(self) -> list[EntityLifetime]:
        return [
            record.public()
            for record in sorted(
                self._entities.values(),
                key=lambda item: item.stable_id,
            )
        ]

    def command_history(self) -> list[CommandState]:
        return [
            command
            for item in self._command_history
            if (command := self._copy_command(item)) is not None
        ]

    def decorate_latest(self, observation: Observation) -> Observation:
        """Refresh planner context without claiming a new world revision."""

        if self._closed:
            raise WorldStateClosedError("Cannot decorate state after store shutdown.")
        if self._latest is None:
            raise WorldStateError("Cannot decorate an empty world-state store.")
        if not observation.world_revision.same_snapshot_as(self._latest.world_revision):
            raise RevisionConflictError(
                "Planner context must decorate the current world-state revision."
            )
        contextual_fields = (
            "planning_mode",
            "objective",
            "recent_action_outcomes",
            "available_skills",
            "skill_specs",
            "memories",
        )
        decorated = self._latest.model_copy(
            update={
                field_name: getattr(observation, field_name) for field_name in contextual_fields
            },
            deep=True,
        )
        self._latest = decorated
        if self._history:
            self._history[-1] = decorated.model_copy(deep=True)
        return decorated.model_copy(deep=True)

    def publish(self, observation: Observation) -> StoreUpdate:
        if self._closed:
            raise WorldStateClosedError("Cannot publish after store shutdown.")
        canonical = self._carry_forward_visual(observation.model_copy(deep=True))
        canonical, capability_change = self._canonicalize_capability_epoch(canonical)
        self._validate_revision(canonical)
        status = self._sequence_status(canonical)
        previous = self._latest

        emitted: list[WorldEvent] = []
        if capability_change is not None:
            added, removed = capability_change
            emitted.append(
                self.record_event(
                    "capabilities_changed",
                    revision=canonical.world_revision,
                    payload={
                        "added": list(added),
                        "removed": list(removed),
                    },
                )
            )
        if status == SequenceStatus.DUPLICATE:
            self.metrics.sequence_stall_incidents += 1
            emitted.append(
                self.record_event(
                    "sequence_stalled",
                    revision=canonical.world_revision,
                    payload={"telemetry_sequence": (canonical.world_revision.telemetry_sequence)},
                )
            )

        for message in canonical.events:
            self.metrics.transient_events_retained += 1
            emitted.append(
                self.record_event(
                    "observation_event",
                    revision=canonical.world_revision,
                    payload={"message": message},
                )
            )
        canonical, entity_events = self._update_entity_registry(canonical)
        emitted.extend(entity_events)
        delta = self._build_delta(previous, canonical)

        self._latest = canonical
        self._history.append(canonical.model_copy(deep=True))
        self._deltas.append(delta)
        self.metrics.observations_published += 1

        update = StoreUpdate(
            observation=canonical.model_copy(deep=True),
            sequence_status=status,
            delta=delta,
            events=tuple(emitted),
        )
        for queue in self._subscribers.values():
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                self.metrics.subscriber_drops += 1
            queue.put_nowait(self._copy_update(update))
        return update

    def record_event(
        self,
        event_type: str,
        *,
        revision: WorldStateRevision | None = None,
        payload: dict[str, object] | None = None,
    ) -> WorldEvent:
        if len(self._journal) == self.event_limit:
            evicted = self._journal[0]
            self.metrics.journal_events_evicted += 1
            if evicted.event_type == "observation_event":
                self.metrics.transient_events_lost += 1
        event = WorldEvent(
            event_id=self._next_event_id,
            event_type=event_type,
            revision=revision.model_copy(deep=True) if revision is not None else None,
            observed_at_monotonic=self.clock.monotonic(),
            payload=payload or {},
        )
        self._next_event_id += 1
        self._journal.append(event)
        if self.event_sink is not None:
            self.event_sink(self._copy_event(event))
        return event

    def subscribe(self) -> WorldStateSubscription:
        if self._closed:
            raise WorldStateClosedError("Cannot subscribe after store shutdown.")
        subscription_id = self._next_subscription_id
        self._next_subscription_id += 1
        queue: asyncio.Queue[StoreUpdate | None] = asyncio.Queue(
            maxsize=self.subscriber_queue_limit
        )
        self._subscribers[subscription_id] = queue
        return WorldStateSubscription(self, subscription_id, queue)

    async def wait_for(
        self,
        predicate: ObservationPredicate,
        *,
        after_revision: WorldStateRevision,
        timeout_seconds: float,
    ) -> Observation:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        subscription = self.subscribe()
        update_task: asyncio.Task[StoreUpdate] | None = None
        timeout_task: asyncio.Task[None] | None = None
        try:
            latest = self.latest
            if (
                latest is not None
                and latest.world_revision.is_later_than(after_revision)
                and predicate(latest)
            ):
                return latest

            timeout_task = asyncio.create_task(self.clock.sleep(timeout_seconds))
            while True:
                update_task = asyncio.create_task(subscription.get())
                done, _ = await asyncio.wait(
                    {update_task, timeout_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if update_task in done:
                    update = update_task.result()
                    candidate = update.observation
                    if candidate.world_revision.is_later_than(after_revision) and predicate(
                        candidate
                    ):
                        return candidate
                    update_task = None
                if timeout_task in done:
                    raise TimeoutError(
                        "World-state predicate did not become true on a later "
                        "revision before the deadline."
                    )
        finally:
            subscription.close()
            for task in (update_task, timeout_task):
                if task is not None and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    def activate_plan(
        self,
        plan_id: str,
        plan_version: int,
        accepted_revision: WorldStateRevision,
    ) -> ActivePlanState:
        if self._active_plan is not None:
            raise WorldStateError(f"Plan {self._active_plan.plan_id!r} is already active.")
        self._active_plan = ActivePlanState(
            plan_id=plan_id,
            plan_version=plan_version,
            accepted_revision=accepted_revision.model_copy(deep=True),
        )
        self.record_event(
            "active_plan_set",
            revision=accepted_revision,
            payload={"plan_id": plan_id, "plan_version": plan_version},
        )
        return self.active_plan  # type: ignore[return-value]

    def activate_step(self, step_id: str) -> ActivePlanState:
        if self._active_plan is None:
            raise WorldStateError("Cannot activate a step without an active plan.")
        self._active_plan = replace(
            self._active_plan,
            step_id=step_id,
            status="running",
        )
        self.record_event(
            "active_step_set",
            revision=self._latest.world_revision if self._latest is not None else None,
            payload={
                "plan_id": self._active_plan.plan_id,
                "plan_version": self._active_plan.plan_version,
                "step_id": step_id,
            },
        )
        return self.active_plan  # type: ignore[return-value]

    def clear_active_plan(self, reason: str) -> None:
        if self._active_plan is None:
            return
        self.record_event(
            "active_plan_cleared",
            revision=self._latest.world_revision if self._latest is not None else None,
            payload={
                "plan_id": self._active_plan.plan_id,
                "plan_version": self._active_plan.plan_version,
                "step_id": self._active_plan.step_id,
                "reason": reason,
            },
        )
        self._active_plan = None

    def begin_command(
        self,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
        action_kind: str,
        start_revision: WorldStateRevision,
    ) -> CommandState:
        if self._active_command is not None:
            raise CommandCausalityError(
                f"Command {self._active_command.command_id!r} is still active."
            )
        command = CommandState(
            command_id=f"cmd-{self._next_command_id:06d}",
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id,
            action_kind=action_kind,
            started_revision=start_revision.model_copy(deep=True),
            started_at_monotonic=self.clock.monotonic(),
        )
        self._next_command_id += 1
        self._active_command = command
        self.record_event(
            "command_started",
            revision=start_revision,
            payload={
                "command_id": command.command_id,
                "plan_id": plan_id,
                "plan_version": plan_version,
                "step_id": step_id,
                "action_kind": action_kind,
            },
        )
        return self._copy_command(command)  # type: ignore[return-value]

    def complete_command(
        self,
        command_id: str,
        completed_revision: WorldStateRevision,
    ) -> CommandState:
        if self._active_command is None:
            self.metrics.command_mismatches += 1
            raise CommandCausalityError(f"Command {command_id!r} does not match an active command.")
        if self._active_command.command_id != command_id:
            self.metrics.command_mismatches += 1
            self.record_event(
                "command_mismatch",
                revision=completed_revision,
                payload={
                    "expected_command_id": self._active_command.command_id,
                    "received_command_id": command_id,
                },
            )
            raise CommandCausalityError(
                f"Command {command_id!r} does not match active command "
                f"{self._active_command.command_id!r}."
            )
        completed = replace(
            self._active_command,
            completed_revision=completed_revision.model_copy(deep=True),
            completed_at_monotonic=self.clock.monotonic(),
            causally_advanced=completed_revision.is_later_than(
                self._active_command.started_revision
            ),
        )
        self._command_history.append(completed)
        self._active_command = None
        self.record_event(
            "command_completed",
            revision=completed_revision,
            payload={
                "command_id": completed.command_id,
                "causally_advanced": completed.causally_advanced,
            },
        )
        return self._copy_command(completed)  # type: ignore[return-value]

    def fail_active_command(self, reason: str) -> CommandState | None:
        if self._active_command is None:
            return None
        failed = self._active_command
        self._command_history.append(failed)
        self._active_command = None
        self.record_event(
            "command_inconclusive",
            revision=failed.started_revision,
            payload={"command_id": failed.command_id, "reason": reason},
        )
        return self._copy_command(failed)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        for subscription_id in list(self._subscribers):
            self._unsubscribe(subscription_id)
        if self._active_command is not None:
            self.fail_active_command("World-state store shut down.")

    def _unsubscribe(self, subscription_id: int) -> None:
        queue = self._subscribers.pop(subscription_id, None)
        if queue is not None:
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(None)

    def _carry_forward_visual(self, current: Observation) -> Observation:
        previous = self._latest
        if (
            previous is None
            or current.world_revision.frame_sequence is not None
            or previous.world_revision.frame_sequence is None
        ):
            return current
        return current.model_copy(
            update={
                "world_revision": current.world_revision.model_copy(
                    update={"frame_sequence": previous.world_revision.frame_sequence}
                ),
                "screenshot_path": previous.screenshot_path,
                "screenshot_sha256": previous.screenshot_sha256,
            }
        )

    def _canonicalize_capability_epoch(
        self,
        current: Observation,
    ) -> tuple[Observation, tuple[tuple[str, ...], tuple[str, ...]] | None]:
        current_capabilities = (
            tuple(sorted(current.telemetry.capabilities)) if current.telemetry is not None else ()
        )
        previous_capabilities = (
            tuple(sorted(self._latest.telemetry.capabilities))
            if self._latest is not None and self._latest.telemetry is not None
            else ()
        )
        if self._latest is None:
            if current_capabilities and current.world_revision.capability_epoch == 0:
                current = current.model_copy(
                    update={
                        "world_revision": current.world_revision.model_copy(
                            update={"capability_epoch": 1}
                        )
                    }
                )
            return current, None
        if current_capabilities == previous_capabilities:
            return current, None
        minimum_epoch = self._latest.world_revision.capability_epoch + 1
        if current.world_revision.capability_epoch < minimum_epoch:
            current = current.model_copy(
                update={
                    "world_revision": current.world_revision.model_copy(
                        update={"capability_epoch": minimum_epoch}
                    )
                }
            )
        added = tuple(sorted(set(current_capabilities) - set(previous_capabilities)))
        removed = tuple(sorted(set(previous_capabilities) - set(current_capabilities)))
        return current, (added, removed)

    def _validate_revision(self, current: Observation) -> None:
        revision = current.world_revision
        if (
            current.telemetry is not None
            and revision.telemetry_sequence != current.telemetry.sequence
        ):
            self.metrics.revision_conflicts += 1
            raise RevisionConflictError(
                "World revision telemetry sequence does not match the snapshot."
            )
        previous = self._latest
        if previous is None:
            if (
                revision.telemetry_sequence is None
                and revision.frame_sequence is None
                and revision.capability_epoch == 0
            ):
                self.metrics.revision_conflicts += 1
                raise RevisionConflictError(
                    "Initial world state has no validated revision channel."
                )
            return
        prior = previous.world_revision
        regressed = (
            revision.capability_epoch < prior.capability_epoch
            or revision.observed_at_monotonic < prior.observed_at_monotonic
            or (
                revision.telemetry_sequence is not None
                and prior.telemetry_sequence is not None
                and revision.telemetry_sequence < prior.telemetry_sequence
            )
            or (
                revision.frame_sequence is not None
                and prior.frame_sequence is not None
                and revision.frame_sequence < prior.frame_sequence
            )
        )
        if regressed:
            self.metrics.revision_regressions += 1
            self.record_event(
                "revision_regressed",
                revision=revision,
                payload={"previous_revision": prior.model_dump(mode="json")},
            )
            raise RevisionRegressionError(
                "World-state revision regressed relative to the latest state."
            )
        if revision.same_snapshot_as(prior):
            before = self._revision_bound_payload(previous)
            after = self._revision_bound_payload(current)
            if before != after:
                self.metrics.revision_conflicts += 1
                self.record_event(
                    "revision_conflict",
                    revision=revision,
                    payload={"reason": "State changed without a revision advance."},
                )
                raise RevisionConflictError(
                    "World state changed without advancing a revision channel."
                )

    @staticmethod
    def _revision_bound_payload(observation: Observation) -> dict[str, object]:
        telemetry = (
            observation.telemetry.model_dump(
                mode="json",
                exclude={"captured_at"},
            )
            if observation.telemetry is not None
            else None
        )
        if telemetry is not None:
            nearby = telemetry.get("nearby_entities")
            if isinstance(nearby, list):
                normalized_nearby: list[dict[str, object]] = []
                for item in nearby:
                    if isinstance(item, dict):
                        normalized = dict(item)
                        normalized.pop("id", None)
                        normalized_nearby.append(normalized)
                telemetry["nearby_entities"] = sorted(
                    normalized_nearby,
                    key=repr,
                )
        return {
            "telemetry": telemetry,
            "screenshot_sha256": observation.screenshot_sha256,
        }

    def _sequence_status(self, observation: Observation) -> SequenceStatus:
        sequence = observation.world_revision.telemetry_sequence
        if sequence is None:
            return SequenceStatus.UNAVAILABLE
        if self._latest is None:
            return SequenceStatus.INITIAL
        prior = self._latest.world_revision.telemetry_sequence
        if prior is None or sequence > prior:
            return SequenceStatus.ADVANCED
        return SequenceStatus.DUPLICATE

    def _build_delta(
        self,
        previous: Observation | None,
        current: Observation,
    ) -> StateDelta:
        before = self._semantic_payload(previous) if previous is not None else {}
        after = self._semantic_payload(current)
        changed = sorted(
            path for path in set(before) | set(after) if before.get(path) != after.get(path)
        )
        truncated = len(changed) > self.max_delta_paths
        return StateDelta(
            before_revision=(
                previous.world_revision.model_copy(deep=True) if previous is not None else None
            ),
            after_revision=current.world_revision.model_copy(deep=True),
            changed_paths=tuple(changed[: self.max_delta_paths]),
            truncated=truncated,
        )

    @classmethod
    def _semantic_payload(
        cls,
        observation: Observation | None,
    ) -> dict[str, object]:
        if observation is None:
            return {}
        payload = observation.model_dump(
            mode="json",
            exclude={
                "observed_at",
                "world_revision",
                "events",
                "memories",
                "recent_action_outcomes",
                "screenshot_path",
            },
        )
        telemetry = payload.get("telemetry")
        if isinstance(telemetry, dict):
            telemetry.pop("sequence", None)
            telemetry.pop("captured_at", None)
        flattened: dict[str, object] = {}
        cls._flatten("", payload, flattened)
        return flattened

    @classmethod
    def _flatten(
        cls,
        prefix: str,
        value: object,
        output: dict[str, object],
    ) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                path = f"{prefix}.{key}" if prefix else str(key)
                cls._flatten(path, value[key], output)
        elif isinstance(value, list):
            rendered = [str(item) for item in value]
            if prefix.endswith("nearby_entities") or prefix.endswith("capabilities"):
                rendered.sort()
            output[prefix] = tuple(rendered)
        else:
            output[prefix] = value

    def _update_entity_registry(
        self,
        observation: Observation,
    ) -> tuple[Observation, list[WorldEvent]]:
        telemetry = observation.telemetry
        if telemetry is None or not _ENTITY_LIST_CAPABILITIES.intersection(telemetry.capabilities):
            return observation, []
        entities = telemetry.nearby_entities
        revision = observation.world_revision
        active_records = [record for record in self._entities.values() if record.active]
        assigned: set[str] = set()
        emitted: list[WorldEvent] = []
        normalized_entities: list[NearbyEntity] = []

        for observed in entities:
            fingerprint = self._entity_fingerprint(observed)
            candidates = [
                record
                for record in active_records
                if record.stable_id not in assigned and record.fingerprint == fingerprint
            ]
            record: _EntityRecord
            if candidates:
                record = min(
                    candidates,
                    key=lambda candidate: (
                        self._entity_distance(candidate, observed),
                        0 if observed.id in candidate.source_ids else 1,
                        candidate.stable_id,
                    ),
                )
                if len(candidates) > 1:
                    record.ambiguous_matches += 1
                    emitted.append(
                        self.record_event(
                            "entity_identity_ambiguous",
                            revision=revision,
                            payload={
                                "stable_id": record.stable_id,
                                "source_id": observed.id,
                                "candidate_count": len(candidates),
                            },
                        )
                    )
                record.source_ids.add(observed.id)
                record.last_revision = revision.model_copy(deep=True)
                record.position = self._entity_position(observed)
            else:
                stable_id = f"entity-{self._next_entity_id:06d}"
                self._next_entity_id += 1
                record = _EntityRecord(
                    stable_id=stable_id,
                    name=observed.name,
                    kind=observed.kind,
                    faction=observed.faction,
                    is_animal=observed.is_animal,
                    source_ids={observed.id},
                    first_revision=revision.model_copy(deep=True),
                    last_revision=revision.model_copy(deep=True),
                    position=self._entity_position(observed),
                )
                self._entities[stable_id] = record
                active_records.append(record)
                self.metrics.entity_lifetimes_started += 1
                emitted.append(
                    self.record_event(
                        "entity_appeared",
                        revision=revision,
                        payload={
                            "stable_id": stable_id,
                            "source_id": observed.id,
                            "name": observed.name,
                        },
                    )
                )
            assigned.add(record.stable_id)
            normalized_entities.append(observed.model_copy(update={"id": record.stable_id}))

        for record in active_records:
            if record.stable_id in assigned:
                continue
            record.active = False
            record.ended_revision = revision.model_copy(deep=True)
            record.last_revision = revision.model_copy(deep=True)
            self.metrics.entity_lifetimes_ended += 1
            emitted.append(
                self.record_event(
                    "entity_disappeared",
                    revision=revision,
                    payload={"stable_id": record.stable_id, "name": record.name},
                )
            )
        normalized_observation = observation
        if telemetry is not None:
            normalized_observation = observation.model_copy(
                update={
                    "telemetry": telemetry.model_copy(
                        update={"nearby_entities": normalized_entities}
                    )
                }
            )
        return normalized_observation, emitted

    @staticmethod
    def _entity_fingerprint(
        entity: NearbyEntity,
    ) -> tuple[str, str, str | None, bool | None]:
        return entity.name, entity.kind, entity.faction, entity.is_animal

    @staticmethod
    def _entity_position(
        entity: NearbyEntity,
    ) -> tuple[float, float, float] | None:
        if entity.position is None:
            return None
        return entity.position.x, entity.position.y, entity.position.z

    @classmethod
    def _entity_distance(
        cls,
        record: _EntityRecord,
        entity: NearbyEntity,
    ) -> float:
        position = cls._entity_position(entity)
        if record.position is None or position is None:
            return float("inf")
        return dist(record.position, position)

    @staticmethod
    def _copy_command(command: CommandState | None) -> CommandState | None:
        if command is None:
            return None
        return replace(
            command,
            started_revision=command.started_revision.model_copy(deep=True),
            completed_revision=(
                command.completed_revision.model_copy(deep=True)
                if command.completed_revision is not None
                else None
            ),
        )

    @staticmethod
    def _copy_delta(delta: StateDelta) -> StateDelta:
        return replace(
            delta,
            before_revision=(
                delta.before_revision.model_copy(deep=True)
                if delta.before_revision is not None
                else None
            ),
            after_revision=delta.after_revision.model_copy(deep=True),
        )

    @staticmethod
    def _copy_event(event: WorldEvent) -> WorldEvent:
        return replace(
            event,
            revision=(event.revision.model_copy(deep=True) if event.revision is not None else None),
            payload=deepcopy(event.payload),
        )

    @classmethod
    def _copy_update(cls, update: StoreUpdate) -> StoreUpdate:
        return StoreUpdate(
            observation=update.observation.model_copy(deep=True),
            sequence_status=update.sequence_status,
            delta=cls._copy_delta(update.delta),
            events=tuple(cls._copy_event(event) for event in update.events),
        )


ObservationTransform: TypeAlias = Callable[[Observation], Observation]
UpdateCallback: TypeAlias = Callable[[StoreUpdate], None]


class ObservationPump:
    """One cancellable transport poller shared by every state consumer."""

    def __init__(
        self,
        environment: AgentEnvironment,
        store: WorldStateStore,
        *,
        interval_seconds: float,
        clock: PlanningClock | None = None,
        transform: ObservationTransform | None = None,
        on_update: UpdateCallback | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive.")
        self.environment = environment
        self.store = store
        self.interval_seconds = interval_seconds
        self.clock = clock or SystemPlanningClock()
        self.transform = transform
        self.on_update = on_update
        self.task: asyncio.Task[None] | None = None
        self._capture_requested = False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self) -> None:
        if self.running:
            raise RuntimeError("Observation pump is already running.")
        self.task = asyncio.create_task(
            self._run(),
            name="kenshi-agent-observation-pump",
        )
        await asyncio.sleep(0)

    def request_capture(self) -> None:
        self._capture_requested = True

    async def stop(self) -> None:
        task = self.task
        self.task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            await self.clock.sleep(self.interval_seconds)
            capture = self._capture_requested
            self._capture_requested = False
            try:
                observation = (
                    await self.environment.observe()
                    if capture
                    else await self.environment.observe_without_capture()
                )
                if self.transform is not None:
                    observation = self.transform(observation)
                update = self.store.publish(observation)
                if self.on_update is not None:
                    self.on_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.metrics.pump_errors += 1
                self.store.record_event(
                    "observation_pump_error",
                    revision=(
                        self.store.latest.world_revision if self.store.latest is not None else None
                    ),
                    payload={"type": type(exc).__name__, "message": str(exc)},
                )
