"""Bounded read-only cognition operation handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast

from ...models import (
    ActionReceipt,
    AdvisorConsultStatus,
    ConsultAdvisorAction,
    Observation,
    ReadFieldbookAction,
    RecallMemoryAction,
)
from ...operation_definitions import BoundOperation
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)


class AdvisorResult(Protocol):
    @property
    def observation(self) -> Observation: ...

    @property
    def receipt(self) -> ActionReceipt: ...


AdvisorConsultant = Callable[
    [ConsultAdvisorAction, Observation, str, int, str],
    Awaitable[AdvisorResult],
]


class MemoryReader(Protocol):
    def __call__(
        self,
        action: RecallMemoryAction,
        observation: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> ActionReceipt: ...


class FieldbookReader(Protocol):
    def __call__(
        self,
        action: ReadFieldbookAction,
        observation: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> ActionReceipt: ...


@dataclass(frozen=True, slots=True)
class CognitiveServices:
    consult_advisor: AdvisorConsultant | None = None
    read_memory: MemoryReader | None = None
    read_fieldbook: FieldbookReader | None = None


@dataclass(frozen=True, slots=True)
class AdvisorHandler:
    consultant: AdvisorConsultant | None

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(ConsultAdvisorAction, bound.operation)
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for advisor consultation.")
        if self.consultant is None:
            return OperationResult(
                status=OperationStatus.REJECTED,
                observation=observation,
                reason="No strategic advisor is attached to this runtime.",
            )
        context.progress(
            action.question,
            observation,
            event_type="advisor_requested",
            evidence={"focus": action.focus.value},
        )
        result = await self.consultant(
            action,
            observation,
            context.scope.plan_id,
            context.scope.plan_version,
            context.scope.step_id,
        )
        evidence = result.receipt.advisor
        if evidence is None:
            return OperationResult(
                status=OperationStatus.FAILED,
                observation=result.observation,
                reason="Advisor execution returned no typed evidence.",
            )
        succeeded = evidence.status is AdvisorConsultStatus.ANSWERED or (
            evidence.status is AdvisorConsultStatus.PENDING and result.receipt.accepted
        )
        terminal_event = (
            "advisor_queued"
            if evidence.status is AdvisorConsultStatus.PENDING and succeeded
            else "advisor_completed"
            if succeeded
            else "advisor_failed"
            if evidence.status is AdvisorConsultStatus.FAILED
            else "advisor_suppressed"
        )
        context.progress(
            evidence.reason,
            result.observation,
            event_type=terminal_event,
            evidence={
                "status": evidence.status.value,
                "controller_primitives": 0,
                "world_command_created": False,
            },
        )
        return OperationResult(
            status=(OperationStatus.SUCCEEDED if succeeded else OperationStatus.FAILED),
            observation=result.observation,
            reason=evidence.reason,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context, "Advisor consultation was cancelled.")


@dataclass(frozen=True, slots=True)
class MemoryHandler:
    reader: MemoryReader | None

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(RecallMemoryAction, bound.operation)
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for memory recall.")
        if self.reader is None:
            return OperationResult(
                status=OperationStatus.REJECTED,
                observation=observation,
                reason="No memory-read sink is attached to this runtime.",
            )
        receipt = self.reader(
            action,
            observation,
            plan_id=context.scope.plan_id,
            plan_version=context.scope.plan_version,
            step_id=context.scope.step_id,
        )
        latest = context.world.latest or observation
        context.progress(
            receipt.message,
            latest,
            event_type="memory_read_completed",
            evidence={"controller_primitives": 0, "world_command_created": False},
        )
        return OperationResult(
            status=OperationStatus.SUCCEEDED,
            observation=latest,
            reason=receipt.message,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context, "Memory recall was cancelled.")


@dataclass(frozen=True, slots=True)
class FieldbookHandler:
    reader: FieldbookReader | None

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(ReadFieldbookAction, bound.operation)
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for fieldbook reading.")
        if self.reader is None:
            return OperationResult(
                status=OperationStatus.REJECTED,
                observation=observation,
                reason="No fieldbook-read sink is attached to this runtime.",
            )
        receipt = self.reader(
            action,
            observation,
            plan_id=context.scope.plan_id,
            plan_version=context.scope.plan_version,
            step_id=context.scope.step_id,
        )
        latest = context.world.latest or observation
        context.progress(
            receipt.message,
            latest,
            event_type="fieldbook_read_completed",
            evidence={"controller_primitives": 0, "world_command_created": False},
        )
        return OperationResult(
            status=OperationStatus.SUCCEEDED,
            observation=latest,
            reason=receipt.message,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context, "Fieldbook reading was cancelled.")


def _cancelled(
    active: ActiveOperation,
    context: OperationContext,
    reason: str,
) -> OperationResult:
    return OperationResult(
        status=OperationStatus.CANCELLED,
        observation=context.world.latest or active.started_observation,
        reason=reason,
    )


def cognition_handlers(services: CognitiveServices) -> dict[str, OperationHandler]:
    return {
        "cognition.advisor": AdvisorHandler(services.consult_advisor),
        "cognition.memory": MemoryHandler(services.read_memory),
        "cognition.fieldbook": FieldbookHandler(services.read_fieldbook),
    }
