"""Composition and submission boundary for one private operation."""

from __future__ import annotations

from dataclasses import dataclass

from .action_budget import ActionBudgetLedger
from .affordances import OPERATION_BINDING_AUTHORITY, bind_runtime_operation
from .config import PlanningConfig
from .core.observation import Observation
from .core.operation import IdempotencyPolicy
from .core.planning import (
    PlanEnvelope,
    PlanStep,
)
from .env.base import AgentEnvironment
from .execution.handlers.camera import camera_handlers
from .execution.handlers.cognition import (
    AdvisorConsultant,
    CognitiveServices,
    FieldbookReader,
    MemoryReader,
    cognition_handlers,
)
from .execution.handlers.dialogue import dialogue_handlers
from .execution.handlers.inventory import inventory_handlers
from .execution.handlers.movement import movement_handlers
from .execution.handlers.resources import resource_handlers
from .execution.handlers.runtime import runtime_handlers
from .execution.handlers.screens import screen_handlers
from .execution.handlers.trade import trade_handlers
from .execution.kernel import (
    ActionStartedReporter,
    ExecutionKernel,
    KernelHooks,
    KernelRequest,
    TransitionObserver,
)
from .execution.monitor_types import MonitorScope, StagedPatch
from .execution.monitoring import OperationMonitor
from .execution.ports import OperationMechanicsPort
from .execution.registry import HandlerRegistry
from .future_planning import (
    ConcurrentPlanner,
    FuturePlanningPolicy,
    FuturePlanResolution,
    PatchContinuityApplier,
)
from .operation_authority import OperationAuthority
from .plan_events import PlanEventReporter
from .planning import PlanBudgetLedger, PlanningClock
from .session_log import SessionLogger
from .skills import MacroRegistry
from .world_state import WorldStateStore


@dataclass(frozen=True, slots=True)
class OperationSubmissionResult:
    observation: Observation
    succeeded: bool
    actions_completed: int
    reason: str
    terminated: bool = False
    success: bool | None = None
    staged_patch: StagedPatch | None = None
    interrupted: bool = False
    pause_before_replan: bool = False
    retry_authorized: bool = False


class OperationExecutionService:
    """Bind and submit operations while plan traversal remains elsewhere."""

    def __init__(
        self,
        *,
        kernel: ExecutionKernel,
        future_planning: FuturePlanningPolicy,
        clock: PlanningClock,
        state_store: WorldStateStore,
        event: PlanEventReporter,
    ) -> None:
        self.kernel = kernel
        self.future_planning = future_planning
        self.clock = clock
        self.state_store = state_store
        self.event = event

    async def submit(
        self,
        plan: PlanEnvelope,
        step: PlanStep,
        observation: Observation,
        budget: PlanBudgetLedger,
        *,
        plan_started_at: float,
        plan_started_observation: Observation,
        remaining_run_actions: int,
        protected_step_ids: set[str],
    ) -> OperationSubmissionResult:
        try:
            bound = bind_runtime_operation(
                step.action,
                observation,
                affordance=step.affordance,
            )
        except ValueError as exc:
            return OperationSubmissionResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=f"Operation binding failed before execution: {exc}",
            )
        monitor = OperationMonitor(
            scope=MonitorScope(
                plan=plan,
                step=step,
                observation=observation,
                budget=budget,
                remaining_run_actions=remaining_run_actions,
                protected_step_ids=frozenset(protected_step_ids),
                deadline=self.clock.monotonic() + step.timeout_seconds,
            ),
            future_planning=self.future_planning,
            clock=self.clock,
            state_store=self.state_store,
            event=self.event,
        )
        result = await self.kernel.execute(
            bound,
            KernelRequest(
                plan=plan,
                step=step,
                observation=observation,
                budget=budget,
                plan_started_at=plan_started_at,
                plan_started_observation=plan_started_observation,
                remaining_run_actions=remaining_run_actions,
                monitor=monitor,
            ),
        )
        staged = result.staged_patch
        if staged is not None and not isinstance(staged, StagedPatch):
            raise TypeError("Execution kernel returned an invalid staged plan patch.")
        return OperationSubmissionResult(
            observation=result.observation,
            succeeded=result.succeeded,
            actions_completed=result.actions_completed,
            reason=result.reason,
            terminated=result.terminated,
            success=result.success,
            staged_patch=staged,
            interrupted=result.interrupted,
            pause_before_replan=result.pause_before_replan,
            retry_authorized=(bound.definition.idempotency is IdempotencyPolicy.SAFE_TO_RETRY),
        )

    def activate_future_patch(
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
        return self.future_planning.activate(
            staged,
            active_plan=active_plan,
            current_observation=current_observation,
            budget=budget,
            remaining_run_actions=remaining_run_actions,
            protected_step_ids=protected_step_ids,
            step_id=step_id,
            budget_reason=budget_reason,
            interrupted=interrupted,
        )


class OperationExecutionFactory:
    """Composition root for handlers, kernel, monitor, and planning policy."""

    def __init__(
        self,
        *,
        environment: AgentEnvironment,
        operation_port: OperationMechanicsPort,
        macros: MacroRegistry,
        action_budget: ActionBudgetLedger,
        authority: OperationAuthority,
        logger: SessionLogger,
        clock: PlanningClock,
        observe_transition: TransitionObserver,
        concurrent_planner: ConcurrentPlanner | None = None,
        consult_advisor: AdvisorConsultant | None = None,
        apply_patch_continuity: PatchContinuityApplier | None = None,
        read_memory: MemoryReader | None = None,
        read_fieldbook: FieldbookReader | None = None,
        report_action_started: ActionStartedReporter | None = None,
    ) -> None:
        self.environment = environment
        self.operation_port = operation_port
        self.macros = macros
        self.action_budget = action_budget
        self.authority = authority
        self.logger = logger
        self.clock = clock
        self.observe_transition = observe_transition
        self.concurrent_planner = concurrent_planner
        self.consult_advisor = consult_advisor
        self.apply_patch_continuity = apply_patch_continuity
        self.read_memory = read_memory
        self.read_fieldbook = read_fieldbook
        self.report_action_started = report_action_started

    def create(
        self,
        *,
        state_store: WorldStateStore,
        planning_config: PlanningConfig,
        event: PlanEventReporter,
        concurrent_planning: bool,
    ) -> OperationExecutionService:
        handlers = {
            **runtime_handlers(self.operation_port),
            **screen_handlers(self.operation_port),
            **trade_handlers(self.operation_port),
            **inventory_handlers(self.operation_port),
            **camera_handlers(self.operation_port),
            **dialogue_handlers(self.operation_port, planning_config),
            **movement_handlers(
                self.operation_port,
                planning_config,
                self.macros,
            ),
            **resource_handlers(
                self.operation_port,
                self.authority,
                OPERATION_BINDING_AUTHORITY,
                planning_config,
            ),
            **cognition_handlers(
                CognitiveServices(
                    consult_advisor=self.consult_advisor,
                    read_memory=self.read_memory,
                    read_fieldbook=self.read_fieldbook,
                )
            ),
        }
        kernel = ExecutionKernel(
            handlers=HandlerRegistry(handlers),
            action_budget=self.action_budget,
            macros=self.macros,
            logger=self.logger,
            clock=self.clock,
            state_store=state_store,
            hooks=KernelHooks(
                event=event,
                observe_transition=self.observe_transition,
                authorized=self.authority.evaluate,
                pointer_class=self.authority.pointer_class_for,
                report_action_started=self.report_action_started,
            ),
            input_boundary_observation=self.environment.input_boundary_observation,
            input_boundary_max_telemetry_age_seconds=(
                self.environment.input_boundary_max_telemetry_age_seconds
            ),
        )
        future_planning = FuturePlanningPolicy(
            config=planning_config,
            planner=self.concurrent_planner if concurrent_planning else None,
            macros=self.macros,
            logger=self.logger,
            clock=self.clock,
            state_store=state_store,
            event=event,
            apply_continuity=self.apply_patch_continuity,
        )
        return OperationExecutionService(
            kernel=kernel,
            future_planning=future_planning,
            clock=self.clock,
            state_store=state_store,
            event=event,
        )
