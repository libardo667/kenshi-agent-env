"""Reusable monitoring lifecycle invoked by operation-specific handlers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import PlanningConfig
from ..input_boundary import ExecutionToken
from ..models import (
    ActivePlanContext,
    AuthoredPlannerOutput,
    CommandDispatchContext,
    Observation,
    PlanEnvelope,
    PlanPatch,
    PlanStep,
)
from ..options import OptionStatus
from ..planning import (
    PlanBudgetLedger,
    PlanningClock,
    PlanValidationError,
    validate_future_plan_patch,
)
from ..safety import ActionGuard
from ..session_log import SessionLogger
from ..world_state import WorldStateStore
from .monitor_types import (
    MonitoredOperation,
    MonitoredOperationResult,
    MonitorFinalizer,
    StagedPatch,
)


class MonitorEventReporter(Protocol):
    def __call__(
        self,
        event_type: str,
        plan: PlanEnvelope,
        observation: Observation,
        *,
        step: PlanStep | None = None,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> None: ...


ConcurrentPlanner = Any


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
        result: AuthoredPlannerOutput = await self.planner(self.observation)
        return result


@dataclass(frozen=True, slots=True)
class MonitorScope:
    plan: PlanEnvelope
    step: PlanStep
    observation: Observation
    budget: PlanBudgetLedger
    remaining_run_actions: int
    protected_step_ids: frozenset[str]
    deadline: float


class OperationMonitor:
    """Monitor one handler-owned option without knowing its operation type."""

    def __init__(
        self,
        *,
        scope: MonitorScope,
        planning_config: PlanningConfig,
        concurrent_planner: ConcurrentPlanner | None,
        guard: ActionGuard,
        logger: SessionLogger,
        clock: PlanningClock,
        state_store: WorldStateStore,
        event: MonitorEventReporter,
    ) -> None:
        self.scope = scope
        self.planning_config = planning_config
        self.concurrent_planner = concurrent_planner
        self.guard = guard
        self.logger = logger
        self.clock = clock
        self.state_store = state_store
        self.event = event

    async def run(
        self,
        option: MonitoredOperation,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
        allow_concurrent_planning: bool,
        finalize: MonitorFinalizer | None = None,
        observation: Observation | None = None,
    ) -> MonitoredOperationResult:
        option_task = option.start(command, token=token)
        scope = self.scope
        start_observation = observation or scope.observation
        self.event(
            "option_started",
            scope.plan,
            start_observation,
            step=scope.step,
            reason=option.reason,
            evidence={
                "option_id": option.option_id,
                "option_status": option.poll().status.value,
            },
        )
        subscription = self.state_store.subscribe()
        update_task: asyncio.Task[Any] | None = asyncio.create_task(subscription.get())
        planner_task: asyncio.Task[AuthoredPlannerOutput] | None = None
        planner_observation: Observation | None = None
        planner_call: _DeferredPlanner | None = None
        staged_patch: StagedPatch | None = None
        timed_out = False
        interrupted = False

        if (
            allow_concurrent_planning
            and self.planning_config.concurrent_option_planning_enabled
            and self.concurrent_planner is not None
            and self._has_concurrent_future_authority()
        ):
            planner_observation = start_observation.model_copy(
                update={
                    "active_plan": ActivePlanContext(
                        plan_id=scope.plan.plan_id,
                        plan_version=scope.plan.plan_version,
                        objective=scope.plan.objective,
                        active_step_id=scope.step.step_id,
                        active_step_interrupt_policy=scope.step.interrupt_policy,
                        completed_step_ids=sorted(
                            scope.protected_step_ids - {scope.step.step_id}
                        ),
                        remaining_actions=scope.budget.remaining_actions,
                    )
                },
                deep=True,
            )
            planner_call = _DeferredPlanner(
                clock=self.clock,
                delay_seconds=(
                    self.planning_config.concurrent_option_planning_delay_seconds
                ),
                planner=self.concurrent_planner,
                observation=planner_observation,
            )
            planner_task = asyncio.create_task(
                planner_call.run(),
                name=f"kenshi-agent-advisory-{option.option_id}",
            )

        try:
            while option.poll().status is OptionStatus.RUNNING:
                remaining = scope.deadline - self.clock.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                waiting: set[asyncio.Task[Any]] = set()
                if update_task is not None:
                    waiting.add(update_task)
                if not option_task.done():
                    waiting.add(option_task)
                if planner_task is not None:
                    waiting.add(planner_task)
                if not waiting:
                    timed_out = True
                    break
                done, _ = await asyncio.wait(
                    waiting,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    timed_out = True
                    break

                if planner_task is not None and planner_task in done:
                    assert planner_observation is not None
                    assert planner_call is not None
                    assert planner_call.started_at is not None
                    staged_patch = self._consume_concurrent_result(
                        planner_task,
                        planner_observation,
                        planner_latency_seconds=(
                            self.clock.monotonic() - planner_call.started_at
                        ),
                    )
                    planner_task = None

                if update_task is not None and update_task in done:
                    update = update_task.result()
                    progress = option.poll(update)
                    self.event(
                        "option_progress",
                        scope.plan,
                        update.observation,
                        step=scope.step,
                        reason=progress.reason,
                        evidence={
                            "option_id": option.option_id,
                            "option_status": progress.status.value,
                            "sequence_status": update.sequence_status.value,
                            "changed_paths": list(update.delta.changed_paths),
                        },
                    )
                    update_task = (
                        asyncio.create_task(subscription.get())
                        if progress.status is OptionStatus.RUNNING
                        else None
                    )
                elif option_task in done:
                    option.poll()

                if (
                    staged_patch is not None
                    and staged_patch.interrupts_active_step
                    and option.transition is not None
                    and option.poll().status is OptionStatus.RUNNING
                ):
                    interrupted = True
                    terminal = await option.cancel(
                        "The exact active option accepted a revision-bound "
                        "strategic interruption."
                    )
                    self.event(
                        "option_interrupted",
                        scope.plan,
                        self.state_store.latest or scope.observation,
                        step=scope.step,
                        reason=terminal.reason,
                        evidence={
                            "option_id": option.option_id,
                            "option_status": terminal.status.value,
                            "interrupt_active_step_id": (
                                staged_patch.patch.interrupt_active_step_id
                            ),
                        },
                    )
                    break

            if timed_out:
                await option.cancel(
                    "The monitored option exceeded its step timeout before terminal success."
                )
            if finalize is not None:
                await finalize()

            terminal = option.poll()
            latest = self.state_store.latest or start_observation
            if terminal.status is OptionStatus.SUCCEEDED:
                event_type = "option_succeeded"
                reason = terminal.reason
            elif interrupted:
                event_type = "option_interrupted"
                reason = terminal.reason
            else:
                event_type = "option_failed"
                reason = (
                    "The monitored option timed out before terminal success."
                    if timed_out
                    else terminal.reason
                )
            if not interrupted or event_type != "option_interrupted":
                self.event(
                    event_type,
                    scope.plan,
                    latest,
                    step=scope.step,
                    reason=reason,
                    evidence={
                        "option_id": option.option_id,
                        "option_status": terminal.status.value,
                    },
                )
            transition = (
                option.transition.model_copy(deep=True)
                if option.transition is not None
                else option.result()
            )
            return MonitoredOperationResult(
                transition=transition,
                terminal=terminal,
                staged_patch=staged_patch,
                interrupted=interrupted,
            )
        except asyncio.CancelledError:
            cancelled = await option.cancel(
                "Independent safety supervision cancelled the monitored operation."
            )
            self.event(
                (
                    "option_cancelled"
                    if cancelled.status is OptionStatus.CANCELLED
                    else "option_failed"
                ),
                scope.plan,
                self.state_store.latest or start_observation,
                step=scope.step,
                reason=cancelled.reason,
                evidence={
                    "option_id": option.option_id,
                    "option_status": cancelled.status.value,
                },
            )
            raise
        finally:
            subscription.close()
            if update_task is not None and not update_task.done():
                update_task.cancel()
                with suppress(asyncio.CancelledError):
                    await update_task
            if planner_task is not None:
                if not planner_task.done():
                    planner_task.cancel()
                with suppress(asyncio.CancelledError):
                    await planner_task
                self._record_cancelled_planner(planner_call, option)

    def _has_concurrent_future_authority(self) -> bool:
        return bool(
            self.scope.budget.remaining_actions > 0
            and self.scope.remaining_run_actions > 1
        )

    def _consume_concurrent_result(
        self,
        planner_task: asyncio.Task[AuthoredPlannerOutput],
        planner_observation: Observation,
        *,
        planner_latency_seconds: float,
    ) -> StagedPatch | None:
        scope = self.scope
        try:
            authored_output = planner_task.result()
            output = authored_output.output
        except Exception as exc:
            self._planner_log(
                planner_observation,
                source="concurrent_option_error",
                latency=planner_latency_seconds,
                output_type="error",
            )
            self.event(
                "concurrent_planner_discarded",
                scope.plan,
                self.state_store.latest or planner_observation,
                step=scope.step,
                reason=f"Concurrent planner failed: {type(exc).__name__}: {exc}",
            )
            return None

        self._planner_log(
            planner_observation,
            source="concurrent_option",
            latency=planner_latency_seconds,
            output_type=type(output).__name__,
        )
        if not isinstance(output, PlanPatch):
            self.event(
                "concurrent_planner_discarded",
                scope.plan,
                self.state_store.latest or planner_observation,
                step=scope.step,
                reason="Concurrent option planning accepts only a typed PlanPatch advisory.",
                evidence={"output_type": type(output).__name__},
            )
            return None

        latest = self.state_store.latest or planner_observation
        try:
            validate_future_plan_patch(
                output,
                active_plan=scope.plan,
                planner_observation=planner_observation,
                current_observation=latest,
                config=self.planning_config,
                macros=self.guard.macros,
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

        self.event(
            (
                "plan_interrupt_staged"
                if output.interrupt_active_step_id is not None
                else "plan_patch_staged"
            ),
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
            planner_observation=planner_observation.model_copy(deep=True),
            authored_context=authored_output.context,
        )

    def _record_cancelled_planner(
        self,
        planner_call: _DeferredPlanner | None,
        option: MonitoredOperation,
    ) -> None:
        if planner_call is None or planner_call.started_at is None:
            return
        scope = self.scope
        latency = self.clock.monotonic() - planner_call.started_at
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
