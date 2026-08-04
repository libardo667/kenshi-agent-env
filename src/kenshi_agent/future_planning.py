"""Optional concurrent future-planning policy for a running operation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from .config import PlanningConfig
from .core.continuity import (
    ContinuityOperation,
    FieldbookOperation,
)
from .core.observation import Observation
from .core.planner_context import (
    AuthoredPlannerContext,
    AuthoredPlannerOutput,
)
from .core.planning import (
    ActivePlanContext,
    PlanEnvelope,
    PlanPatch,
)
from .execution.monitor_types import MonitoredOperation, MonitorScope, StagedPatch
from .plan_events import PlanEventReporter
from .planning import (
    PlanBudgetLedger,
    PlanningClock,
    PlanValidationError,
    validate_future_plan_patch,
)
from .session_log import SessionLogger
from .skills import MacroRegistry
from .world_state import WorldStateStore

ConcurrentPlanner = Callable[
    [Observation],
    Coroutine[Any, Any, AuthoredPlannerOutput],
]


class PatchContinuityApplier(Protocol):
    def __call__(
        self,
        operations: Sequence[ContinuityOperation],
        fieldbook_operations: Sequence[FieldbookOperation],
        observation: Observation,
        *,
        authored_context: AuthoredPlannerContext,
        plan_id: str,
        plan_version: int,
        step_id: str | None,
    ) -> None: ...


@dataclass(slots=True)
class _DeferredPlanner:
    clock: PlanningClock
    delay_seconds: float
    planner: ConcurrentPlanner
    observation: Observation
    started_at: float | None = None

    async def run(self) -> AuthoredPlannerOutput:
        await self.clock.sleep(self.delay_seconds)
        self.started_at = self.clock.monotonic()
        return await self.planner(self.observation)


@dataclass(frozen=True, slots=True)
class FuturePlanningSession:
    task: asyncio.Task[AuthoredPlannerOutput]
    call: _DeferredPlanner
    planner_observation: Observation


@dataclass(frozen=True, slots=True)
class FuturePlanResolution:
    plan: PlanEnvelope | None
    rejection_reason: str | None = None


class FuturePlanningPolicy:
    """Stage and activate future-only revisions without owning plan traversal."""

    def __init__(
        self,
        *,
        config: PlanningConfig,
        planner: ConcurrentPlanner | None,
        macros: MacroRegistry,
        logger: SessionLogger,
        clock: PlanningClock,
        state_store: WorldStateStore,
        event: PlanEventReporter,
        apply_continuity: PatchContinuityApplier | None,
    ) -> None:
        self.config = config
        self.planner = planner
        self.macros = macros
        self.logger = logger
        self.clock = clock
        self.state_store = state_store
        self.event = event
        self.apply_continuity = apply_continuity

    def begin(
        self,
        scope: MonitorScope,
        observation: Observation,
        *,
        option_id: str,
        enabled: bool,
    ) -> FuturePlanningSession | None:
        if (
            not enabled
            or not self.config.concurrent_option_planning_enabled
            or self.planner is None
            or scope.budget.remaining_actions <= 0
            or scope.remaining_run_actions <= 1
        ):
            return None
        planner_observation = observation.model_copy(
            update={
                "active_plan": ActivePlanContext(
                    plan_id=scope.plan.plan_id,
                    plan_version=scope.plan.plan_version,
                    objective=scope.plan.objective,
                    active_step_id=scope.step.step_id,
                    active_step_interrupt_policy=scope.step.interrupt_policy,
                    completed_step_ids=sorted(scope.protected_step_ids - {scope.step.step_id}),
                    remaining_actions=scope.budget.remaining_actions,
                )
            },
            deep=True,
        )
        call = _DeferredPlanner(
            clock=self.clock,
            delay_seconds=self.config.concurrent_option_planning_delay_seconds,
            planner=self.planner,
            observation=planner_observation,
        )
        return FuturePlanningSession(
            task=asyncio.create_task(
                call.run(),
                name=f"kenshi-agent-advisory-{option_id}",
            ),
            call=call,
            planner_observation=planner_observation,
        )

    def consume(self, session: FuturePlanningSession, scope: MonitorScope) -> StagedPatch | None:
        assert session.call.started_at is not None
        latency = self.clock.monotonic() - session.call.started_at
        try:
            authored_output = session.task.result()
            output = authored_output.output
        except Exception as exc:
            self._planner_log(
                session.planner_observation,
                source="concurrent_option_error",
                latency=latency,
                output_type="error",
            )
            self.event(
                "concurrent_planner_discarded",
                scope.plan,
                self.state_store.latest or session.planner_observation,
                step=scope.step,
                reason=f"Concurrent planner failed: {type(exc).__name__}: {exc}",
            )
            return None
        self._planner_log(
            session.planner_observation,
            source="concurrent_option",
            latency=latency,
            output_type=type(output).__name__,
        )
        if not isinstance(output, PlanPatch):
            self.event(
                "concurrent_planner_discarded",
                scope.plan,
                self.state_store.latest or session.planner_observation,
                step=scope.step,
                reason="Concurrent option planning accepts only a typed PlanPatch advisory.",
                evidence={"output_type": type(output).__name__},
            )
            return None
        latest = self.state_store.latest or session.planner_observation
        try:
            validate_future_plan_patch(
                output,
                active_plan=scope.plan,
                planner_observation=session.planner_observation,
                current_observation=latest,
                config=self.config,
                macros=self.macros,
                budget=scope.budget,
                remaining_run_actions=scope.remaining_run_actions - 1,
                protected_step_ids=set(scope.protected_step_ids),
            )
        except PlanValidationError as exc:
            self.event(
                "plan_patch_rejected",
                scope.plan,
                latest,
                step=scope.step,
                reason=f"Concurrent future patch was rejected: {exc}",
                evidence={"patch": output.model_dump(mode="json")},
            )
            return None
        event_type = (
            "plan_interrupt_staged"
            if output.interrupt_active_step_id is not None
            else "plan_patch_staged"
        )
        self.event(
            event_type,
            scope.plan,
            latest,
            step=scope.step,
            reason=(
                "Concurrent revision names the exact interruptible active step."
                if output.interrupt_active_step_id is not None
                else "Concurrent future patch passed latest-state validation."
            ),
            evidence={"patch": output.model_dump(mode="json")},
        )
        return StagedPatch(
            patch=output.model_copy(deep=True),
            planner_observation=session.planner_observation.model_copy(deep=True),
            authored_context=authored_output.context,
        )

    async def discard(
        self,
        session: FuturePlanningSession,
        scope: MonitorScope,
        option: MonitoredOperation,
    ) -> None:
        if not session.task.done():
            session.task.cancel()
        with suppress(asyncio.CancelledError):
            await session.task
        if session.call.started_at is None:
            return
        latency = self.clock.monotonic() - session.call.started_at
        self._planner_log(
            scope.observation,
            source="concurrent_option_cancelled",
            latency=latency,
            output_type="cancelled",
        )
        self.event(
            "concurrent_planner_discarded",
            scope.plan,
            self.state_store.latest or scope.observation,
            step=scope.step,
            reason="Monitored operation ended before the concurrent advisory completed.",
            evidence={"option_id": option.option_id},
        )

    def activate(
        self,
        staged: StagedPatch,
        *,
        active_plan: PlanEnvelope,
        current_observation: Observation,
        budget: PlanBudgetLedger,
        remaining_run_actions: int,
        protected_step_ids: set[str],
        step_id: str,
        budget_reason: str | None,
        interrupted: bool,
    ) -> FuturePlanResolution:
        try:
            if budget_reason is not None:
                raise PlanValidationError(budget_reason)
            patched = validate_future_plan_patch(
                staged.patch,
                active_plan=active_plan,
                planner_observation=staged.planner_observation,
                current_observation=current_observation,
                config=self.config,
                macros=self.macros,
                budget=budget,
                remaining_run_actions=remaining_run_actions,
                protected_step_ids=protected_step_ids,
            )
        except PlanValidationError as exc:
            if interrupted:
                return FuturePlanResolution(
                    plan=None,
                    rejection_reason=(
                        "The active option was interrupted, but its pause "
                        f"handoff patch failed latest-state validation: {exc}"
                    ),
                )
            self.event(
                "plan_patch_rejected",
                active_plan,
                current_observation,
                step=next(
                    (item for item in active_plan.steps if item.step_id == step_id),
                    None,
                ),
                reason=f"Staged future patch failed post-option revalidation: {exc}",
                evidence={"patch": staged.patch.model_dump(mode="json")},
            )
            return FuturePlanResolution(plan=None)

        previous_version = active_plan.plan_version
        self.state_store.apply_plan_patch(
            patched.plan_version,
            current_observation.world_revision,
        )
        if self.apply_continuity is not None:
            self.apply_continuity(
                staged.patch.continuity_operations,
                staged.patch.fieldbook_operations,
                current_observation,
                authored_context=staged.authored_context,
                plan_id=patched.plan_id,
                plan_version=patched.plan_version,
                step_id=step_id,
            )
        self.event(
            "plan_patched",
            patched,
            current_observation,
            reason=(
                "The exact active option accepted an explicit interruption; "
                "its guarded pause handoff is now the only executable future."
                if interrupted
                else (
                    "A future-only concurrent patch passed latest-state and "
                    "remaining-budget validation."
                )
            ),
            evidence={
                "previous_plan_version": previous_version,
                "patch": staged.patch.model_dump(mode="json"),
            },
        )
        return FuturePlanResolution(plan=patched)

    def _planner_log(
        self,
        observation: Observation,
        *,
        source: str,
        latency: float,
        output_type: str,
    ) -> None:
        self.logger.write(
            "strategic_planner_call",
            step_index=observation.step_index,
            payload={
                "source": source,
                "planner_latency_seconds": latency,
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
                "output_type": output_type,
            },
        )
