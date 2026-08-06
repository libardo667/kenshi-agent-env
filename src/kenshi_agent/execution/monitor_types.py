"""Typed seam between operation handlers and reusable monitoring lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ..core.lifecycle import LifecycleOutcome
from ..core.observation import Observation
from ..core.planner_context import AuthoredPlannerContext
from ..core.planning import (
    PlanEnvelope,
    PlanPatch,
    PlanStep,
)
from ..core.transport import (
    CommandDispatchContext,
    Transition,
)
from ..input_boundary import ExecutionToken
from ..options import OptionPoll
from ..planning import PlanBudgetLedger
from ..world_state import StoreUpdate


@dataclass(frozen=True, slots=True)
class StagedPatch:
    patch: PlanPatch
    planner_observation: Observation
    authored_context: AuthoredPlannerContext

    @property
    def interrupts_active_step(self) -> bool:
        return self.patch.interrupt_active_step_id is not None


@dataclass(frozen=True, slots=True)
class MonitoredOperationResult:
    transition: Transition
    terminal: OptionPoll
    staged_patch: StagedPatch | None
    interrupted: bool = False
    # What became of the monitor, and separately what became of the order.
    # A timeout ends the first and touches nothing about the second, but both
    # used to arrive as one `CANCELLED`, which reads as "the order was
    # cancelled" and is almost never what happened.
    lifecycle: LifecycleOutcome | None = None


@dataclass(frozen=True, slots=True)
class MonitorScope:
    plan: PlanEnvelope
    step: PlanStep
    observation: Observation
    budget: PlanBudgetLedger
    remaining_run_actions: int
    protected_step_ids: frozenset[str]
    deadline: float


class MonitoredOperation(Protocol):
    option_id: str
    reason: str
    transition: Transition | None

    def prepare(self, observation: Observation) -> OptionPoll: ...

    def start(
        self,
        command: CommandDispatchContext | None = None,
        *,
        token: ExecutionToken | None = None,
    ) -> asyncio.Task[Transition]: ...

    def poll(self, update: StoreUpdate | None = None) -> OptionPoll: ...

    async def cancel(self, reason: str) -> OptionPoll: ...

    def result(self) -> Transition: ...


MonitorFinalizer = Callable[[], Awaitable[OptionPoll]]


class OperationMonitorPort(Protocol):
    async def run(
        self,
        option: MonitoredOperation,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
        allow_concurrent_planning: bool,
        finalize: MonitorFinalizer | None = None,
        observation: Observation | None = None,
    ) -> MonitoredOperationResult: ...
