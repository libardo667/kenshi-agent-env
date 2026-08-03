"""Shared lifecycle types at the operation execution boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from ..input_boundary import ExecutionToken
from ..models import CommandDispatchContext, Observation, Transition
from ..operation_definitions import BoundOperation
from ..planning import PlanningClock
from ..session_log import SessionLogger
from ..world_state import WorldStateStore
from .monitor_types import OperationMonitorPort


class CancellationSignal(Protocol):
    """Read-only cancellation authority supplied by the run coordinator."""

    def cancelled(self) -> bool: ...


class NeverCancelled:
    """Default signal for callers without an independent cancellation source."""

    def cancelled(self) -> bool:
        return False


class OperationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class OperationProgress:
    """Handler-owned progress without a second terminal authority."""

    reason: str
    event_type: str = "plan_step_progress"
    evidence: dict[str, Any] = field(default_factory=dict)


ProgressReporter = Callable[[OperationProgress, Observation], None]


@dataclass(frozen=True, slots=True)
class ExecutionScope:
    """Stable correlation identity for one operation attempt."""

    operation_id: str
    plan_id: str
    plan_version: int
    step_id: str


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Cross-cutting services available to handlers.

    Family-specific external ports are injected into their handlers. Keeping
    them out of this object prevents the context from becoming a replacement
    giant environment.
    """

    world: WorldStateStore
    logger: SessionLogger
    clock: PlanningClock
    scope: ExecutionScope
    command: CommandDispatchContext | None = None
    token: ExecutionToken | None = None
    monitor: OperationMonitorPort | None = None
    cancellation: CancellationSignal = field(default_factory=NeverCancelled)
    report_progress: ProgressReporter | None = None

    def progress(
        self,
        reason: str,
        observation: Observation,
        *,
        event_type: str = "plan_step_progress",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        if self.report_progress is not None:
            self.report_progress(
                OperationProgress(
                    reason=reason,
                    event_type=event_type,
                    evidence=evidence or {},
                ),
                observation,
            )


@dataclass(frozen=True, slots=True)
class ActiveOperation:
    """Identity needed to cancel exactly one in-flight operation."""

    bound: BoundOperation
    operation_id: str
    started_observation: Observation


@dataclass(frozen=True, slots=True)
class OperationResult:
    """The one terminal verdict returned by an operation handler."""

    status: OperationStatus
    observation: Observation
    reason: str
    transition: Transition | None = None
    terminated: bool = False
    success: bool | None = None
    monitoring_started: bool = False
    staged_patch: object | None = None
    pause_before_replan: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status is OperationStatus.SUCCEEDED


class OperationHandler(Protocol):
    """Mechanics and operation-specific monitoring for one handler key."""

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult: ...

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult: ...
