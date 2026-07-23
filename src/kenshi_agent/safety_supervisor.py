from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from .models import Observation, PauseAction, PlannerDecision, StopAction
from .reflexes import ReflexEngine
from .world_state import (
    SequenceStatus,
    StoreUpdate,
    WorldStateClosedError,
    WorldStateStore,
    WorldStateSubscription,
)


class SafetyCause(StrEnum):
    REFLEX = "reflex"
    HUMAN_INPUT = "human_input"
    TELEMETRY_STALE = "telemetry_stale"
    SEQUENCE_STALLED = "sequence_stalled"
    PAUSE_CAPABILITY_WITHDRAWN = "pause_capability_withdrawn"
    UNEXPECTED_UNPAUSE = "unexpected_unpause"


@dataclass(frozen=True, slots=True)
class SafetyPreemption:
    cause: SafetyCause
    reason: str
    observation: Observation
    decision: PlannerDecision


@dataclass(slots=True)
class SafetySupervisorMetrics:
    preemptions_requested: int = 0
    duplicate_requests: int = 0
    sequence_stall_preemptions: int = 0
    telemetry_stale_preemptions: int = 0
    capability_withdrawal_preemptions: int = 0
    reflex_preemptions: int = 0
    unexpected_unpause_preemptions: int = 0
    human_input_preemptions: int = 0


class SafetySupervisor:
    """Deterministic subscriber that can interrupt slow strategic work."""

    def __init__(
        self,
        *,
        store: WorldStateStore,
        reflexes: ReflexEngine,
        max_sequence_stalls: int,
    ) -> None:
        if max_sequence_stalls <= 0:
            raise ValueError("max_sequence_stalls must be positive.")
        self.store = store
        self.reflexes = reflexes
        self.max_sequence_stalls = max_sequence_stalls
        self.metrics = SafetySupervisorMetrics()
        self.task: asyncio.Task[None] | None = None
        self._subscription: WorldStateSubscription | None = None
        self._preemption: SafetyPreemption | None = None
        self._preemption_ready = asyncio.Event()
        self._consecutive_stalls = 0

    @property
    def preempted(self) -> bool:
        return self._preemption is not None

    async def start(self) -> None:
        if self.task is not None:
            raise RuntimeError("Safety supervisor is already started.")
        self._subscription = self.store.subscribe()
        self.task = asyncio.create_task(
            self._run(),
            name="kenshi-agent-safety-supervisor",
        )
        await asyncio.sleep(0)

    async def stop(self) -> None:
        task = self.task
        self.task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def wait_for_preemption(self) -> SafetyPreemption:
        await self._preemption_ready.wait()
        if self._preemption is None:
            raise RuntimeError("Safety preemption event was set without a cause.")
        return self._copy_preemption(self._preemption)

    def request_preemption(
        self,
        *,
        cause: SafetyCause,
        reason: str,
        observation: Observation,
        decision: PlannerDecision,
    ) -> bool:
        if self._preemption is not None:
            self.metrics.duplicate_requests += 1
            return False
        preemption = SafetyPreemption(
            cause=cause,
            reason=reason,
            observation=observation.model_copy(deep=True),
            decision=decision.model_copy(deep=True),
        )
        self._preemption = preemption
        self.metrics.preemptions_requested += 1
        if cause is SafetyCause.SEQUENCE_STALLED:
            self.metrics.sequence_stall_preemptions += 1
        elif cause is SafetyCause.TELEMETRY_STALE:
            self.metrics.telemetry_stale_preemptions += 1
        elif cause is SafetyCause.PAUSE_CAPABILITY_WITHDRAWN:
            self.metrics.capability_withdrawal_preemptions += 1
        elif cause is SafetyCause.REFLEX:
            self.metrics.reflex_preemptions += 1
        elif cause is SafetyCause.UNEXPECTED_UNPAUSE:
            self.metrics.unexpected_unpause_preemptions += 1
        elif cause is SafetyCause.HUMAN_INPUT:
            self.metrics.human_input_preemptions += 1
        self.store.record_event(
            "safety_preemption_requested",
            revision=observation.world_revision,
            payload={
                "cause": cause.value,
                "reason": reason,
                "action_kind": decision.action.kind,
            },
        )
        self._preemption_ready.set()
        return True

    async def _run(self) -> None:
        subscription = self._subscription
        if subscription is None:
            raise RuntimeError("Safety supervisor started without a subscription.")
        try:
            while not self.preempted:
                update = await subscription.get()
                preemption = self._evaluate(update)
                if preemption is not None:
                    self.request_preemption(
                        cause=preemption.cause,
                        reason=preemption.reason,
                        observation=preemption.observation,
                        decision=preemption.decision,
                    )
        except WorldStateClosedError:
            return
        finally:
            subscription.close()
            self._subscription = None

    def _evaluate(self, update: StoreUpdate) -> SafetyPreemption | None:
        observation = update.observation
        telemetry = observation.telemetry
        capabilities = set(telemetry.capabilities) if telemetry is not None else set()
        pause_withdrawn = False
        for event in update.events:
            removed = event.payload.get("removed")
            if (
                event.event_type == "capabilities_changed"
                and isinstance(removed, list)
                and "game.pause" in removed
            ):
                pause_withdrawn = True
                break
        if pause_withdrawn:
            return SafetyPreemption(
                cause=SafetyCause.PAUSE_CAPABILITY_WITHDRAWN,
                reason="The authoritative game.pause capability was withdrawn.",
                observation=observation,
                decision=self._stop_decision(
                    "Pause capability was withdrawn; safe cleanup cannot be verified."
                ),
            )

        human_input_detected = any(
            event.event_type == "observation_event"
            and event.payload.get("message") == "human_input_detected"
            for event in update.events
        )
        if human_input_detected:
            return SafetyPreemption(
                cause=SafetyCause.HUMAN_INPUT,
                reason="The authoritative state stream reported resumed human input.",
                observation=observation,
                decision=self._pause_or_stop(
                    observation,
                    capabilities,
                    stop_reason="Human input resumed and safe pause cannot be verified.",
                ),
            )

        if observation.telemetry_stale:
            return SafetyPreemption(
                cause=SafetyCause.TELEMETRY_STALE,
                reason="The continuous state stream reported stale telemetry.",
                observation=observation,
                decision=self._pause_or_stop(
                    observation,
                    capabilities,
                    stop_reason="Telemetry is stale and safe pause cannot be verified.",
                ),
            )

        reflex = self.reflexes.decide(observation)
        if reflex is not None:
            decision = reflex
            if isinstance(reflex.action, PauseAction) and "game.pause" not in capabilities:
                decision = self._stop_decision(
                    "A safety reflex fired, but pause capability is unavailable."
                )
            return SafetyPreemption(
                cause=SafetyCause.REFLEX,
                reason=reflex.rationale,
                observation=observation,
                decision=decision,
            )

        if update.sequence_status is SequenceStatus.DUPLICATE:
            self._consecutive_stalls += 1
        else:
            self._consecutive_stalls = 0
        if self._consecutive_stalls >= self.max_sequence_stalls:
            return SafetyPreemption(
                cause=SafetyCause.SEQUENCE_STALLED,
                reason=(
                    "Telemetry sequence stalled for "
                    f"{self._consecutive_stalls} consecutive updates."
                ),
                observation=observation,
                decision=self._pause_or_stop(
                    observation,
                    capabilities,
                    stop_reason="Telemetry sequence stalled; safe progress is unverified.",
                ),
            )

        if (
            telemetry is not None
            and telemetry.game.paused is False
            and update.active_plan is None
            and update.active_command is None
        ):
            return SafetyPreemption(
                cause=SafetyCause.UNEXPECTED_UNPAUSE,
                reason="The game became unpaused without an active authorized command.",
                observation=observation,
                decision=self._pause_or_stop(
                    observation,
                    capabilities,
                    stop_reason="Unexpected unpause cannot be corrected safely.",
                ),
            )
        return None

    @classmethod
    def _pause_or_stop(
        cls,
        observation: Observation,
        capabilities: set[str],
        *,
        stop_reason: str,
    ) -> PlannerDecision:
        telemetry = observation.telemetry
        if (
            telemetry is not None
            and telemetry.game.paused is False
            and "game.pause" in capabilities
        ):
            return PlannerDecision(
                intent="Restore deterministic safe pause.",
                rationale="The supervisor can verify and request the pause capability.",
                action=PauseAction(paused=True),
                confidence=1.0,
            )
        return cls._stop_decision(stop_reason)

    @staticmethod
    def _stop_decision(reason: str) -> PlannerDecision:
        return PlannerDecision(
            intent="Stop unsafe continuous work.",
            rationale=reason,
            action=StopAction(reason=reason),
            confidence=1.0,
        )

    @staticmethod
    def _copy_preemption(preemption: SafetyPreemption) -> SafetyPreemption:
        return SafetyPreemption(
            cause=preemption.cause,
            reason=preemption.reason,
            observation=preemption.observation.model_copy(deep=True),
            decision=preemption.decision.model_copy(deep=True),
        )
