from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from .action_contracts import ActionExecution, contract_for
from .config import PlanningConfig
from .env import AgentEnvironment
from .input_boundary import ExecutionToken
from .models import (
    Action,
    ActionReceipt,
    ActivePlanContext,
    CameraRecoveryStatus,
    CommandDispatchContext,
    ConditionEvaluation,
    ConditionResult,
    ConsultAdvisorAction,
    ExitCurrentBuildingAction,
    InputBoundaryDecision,
    MoveInDirectionAction,
    Observation,
    ObservationPolicy,
    PauseAction,
    PerformContextAction,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanPatch,
    PlanStep,
    RequestAffordanceAction,
    SkillAction,
    StopAction,
    Transition,
    WorldStateRevision,
)
from .options import (
    OptionLifecycleError,
    OptionPoll,
    OptionStatus,
    StatefulApproachOption,
    StatefulMovementOption,
    StatefulNativeMovementOption,
)
from .planning import (
    PlanBudgetError,
    PlanBudgetLedger,
    PlanningClock,
    PlanValidationError,
    evaluate_conditions,
    game_elapsed_seconds,
    validate_future_plan_patch,
)
from .reflexes import ReflexEngine
from .safety import ActionGuard, SafetyViolation
from .session_log import SessionLogger
from .skills import ApproachOptionParams
from .world_state import CommandCausalityError, WorldStateStore

TransitionObserver = Callable[
    [
        PlanEnvelope,
        PlanStep,
        Observation,
        Transition,
        str,
        WorldStateRevision,
    ],
    Observation,
]
ConcurrentPlanner = Callable[
    [Observation],
    Coroutine[Any, Any, PlannerOutput],
]
AdvisorConsultant = Callable[
    [ConsultAdvisorAction, Observation, str, int, str],
    Coroutine[Any, Any, "AdvisorActionResult"],
]
AffordanceRequester = Callable[
    [RequestAffordanceAction, Observation, str, int, str],
    Coroutine[Any, Any, "AffordanceRequestActionResult"],
]


@dataclass(frozen=True, slots=True)
class PlanExecutionResult:
    observation: Observation
    actions_completed: int
    completed: bool
    terminated: bool
    success: bool | None
    reason: str
    reflex_decision: PlannerDecision | None = None


@dataclass(frozen=True, slots=True)
class AdvisorActionResult:
    observation: Observation
    receipt: ActionReceipt


@dataclass(frozen=True, slots=True)
class AffordanceRequestActionResult:
    observation: Observation
    receipt: ActionReceipt


@dataclass(frozen=True, slots=True)
class _StepResult:
    observation: Observation
    succeeded: bool
    actions_completed: int
    reason: str
    terminated: bool = False
    success: bool | None = None
    staged_patch: _StagedPatch | None = None
    interrupted: bool = False
    pause_before_replan: bool = False


@dataclass(frozen=True, slots=True)
class _StagedPatch:
    patch: PlanPatch
    planner_observation: Observation

    @property
    def interrupts_active_step(self) -> bool:
        return self.patch.interrupt_active_step_id is not None


def _unmet_postcondition_reason(
    success_evaluations: Sequence[ConditionEvaluation],
    *,
    step_deadline_seconds: float,
) -> str:
    """Say why a step ran out of time, in terms of what was actually observed.

    "Timed out" is only an honest summary when the evidence never arrived. When
    the conditions were evaluable the whole time and simply read false, the step
    did not run out of time - the action did not do what the plan predicted, and
    calling that a timeout sends the next reader hunting for a slow clock.
    """

    stale = [e for e in success_evaluations if e.result == ConditionResult.STALE]
    if stale:
        return (
            f"No causally later world revision arrived within "
            f"{step_deadline_seconds:.1f}s, so the step could not be verified: "
            + "; ".join(e.reason for e in stale[:3])
        )
    unmet = [e for e in success_evaluations if e.result != ConditionResult.TRUE]
    if not unmet:
        return f"Step ran out of its {step_deadline_seconds:.1f}s budget."
    return (
        f"The action completed but did not have its intended effect within "
        f"{step_deadline_seconds:.1f}s: "
        + "; ".join(f"{e.condition.path or e.condition.kind.value}: {e.reason}" for e in unmet[:3])
    )


class ContinuousPlanExecutor:
    """Deterministic owner of one accepted, bounded plan's real-time state."""

    def __init__(
        self,
        *,
        environment: AgentEnvironment,
        guard: ActionGuard,
        reflexes: ReflexEngine,
        logger: SessionLogger,
        clock: PlanningClock,
        state_store: WorldStateStore,
        observe_transition: TransitionObserver,
        planning_config: PlanningConfig,
        concurrent_planner: ConcurrentPlanner | None = None,
        consult_advisor: AdvisorConsultant | None = None,
        request_affordance: AffordanceRequester | None = None,
    ) -> None:
        self.environment = environment
        self.guard = guard
        self.reflexes = reflexes
        self.logger = logger
        self.clock = clock
        self.state_store = state_store
        self.observe_transition = observe_transition
        self.planning_config = planning_config
        self.concurrent_planner = concurrent_planner
        self.consult_advisor = consult_advisor
        self.request_affordance = request_affordance

    async def execute(
        self,
        plan: PlanEnvelope,
        observation: Observation,
        *,
        remaining_run_actions: int,
    ) -> PlanExecutionResult:
        self.state_store.activate_plan(
            plan.plan_id,
            plan.plan_version,
            observation.world_revision,
        )
        result: PlanExecutionResult | None = None
        try:
            result = await self._execute_active(
                plan,
                observation,
                remaining_run_actions=remaining_run_actions,
            )
            return result
        finally:
            self.state_store.clear_active_plan(
                result.reason if result is not None else "Executor failed unexpectedly."
            )

    async def _execute_active(
        self,
        plan: PlanEnvelope,
        observation: Observation,
        *,
        remaining_run_actions: int,
    ) -> PlanExecutionResult:
        plan_started_at = self.clock.monotonic()
        plan_started_observation = observation
        budget = PlanBudgetLedger.from_plan(plan)
        by_id = {step.step_id: step for step in plan.steps}
        step_id: str | None = plan.entry_step_id
        actions_completed = 0
        completed_step_ids: set[str] = set()

        self._event(
            "plan_started",
            plan,
            observation,
            reason="Executor accepted ownership of the bounded plan.",
        )

        while step_id is not None:
            step = by_id[step_id]
            latest_store_observation = self.state_store.latest
            if latest_store_observation is not None:
                observation = latest_store_observation
            self.state_store.activate_step(step.step_id)
            budget_reason = self._budget_stop_reason(
                plan,
                plan_started_at,
                plan_started_observation,
                observation,
                remaining_run_actions - actions_completed,
            )
            if budget_reason is not None:
                return self._abort(
                    plan,
                    step,
                    observation,
                    actions_completed,
                    budget_reason,
                )

            reflex = self.reflexes.decide(observation)
            if reflex is not None:
                reason = (
                    f"Deterministic safety reflex preempted the active plan: {reflex.rationale}"
                )
                self._event(
                    "safety_preempted",
                    plan,
                    observation,
                    step=step,
                    reason=reason,
                    evidence={"reflex": reflex.model_dump(mode="json")},
                )
                self._event(
                    "plan_step_cancelled",
                    plan,
                    observation,
                    step=step,
                    reason=reason,
                )
                aborted = self._abort(
                    plan,
                    step,
                    observation,
                    actions_completed,
                    reason,
                    emit_step_cancelled=False,
                )
                return PlanExecutionResult(
                    observation=aborted.observation,
                    actions_completed=aborted.actions_completed,
                    completed=False,
                    terminated=False,
                    success=None,
                    reason=aborted.reason,
                    reflex_decision=reflex,
                )

            assumptions = evaluate_conditions(plan.assumptions, observation)
            blocked_assumption = self._first_non_true(assumptions)
            if blocked_assumption is not None:
                reason = (
                    "Plan assumption changed before execution: "
                    f"{blocked_assumption.result.value}: {blocked_assumption.reason}"
                )
                return self._abort(
                    plan,
                    step,
                    observation,
                    actions_completed,
                    reason,
                    evidence={"assumptions": self._evaluations_json(assumptions)},
                )

            preconditions = evaluate_conditions(step.preconditions, observation)
            blocked_precondition = self._first_non_true(preconditions)
            if blocked_precondition is not None:
                reason = (
                    "Step precondition is not true immediately before execution: "
                    f"{blocked_precondition.result.value}: "
                    f"{blocked_precondition.reason}"
                )
                return self._abort(
                    plan,
                    step,
                    observation,
                    actions_completed,
                    reason,
                    evidence={"preconditions": self._evaluations_json(preconditions)},
                )

            self._event(
                "plan_step_ready",
                plan,
                observation,
                step=step,
                reason="All assumptions, capabilities, and preconditions are true.",
                evidence={"preconditions": self._evaluations_json(preconditions)},
            )

            retries_remaining = step.retry_budget
            while True:
                step_result = await self._execute_step(
                    plan,
                    step,
                    observation,
                    budget,
                    plan_started_at=plan_started_at,
                    plan_started_observation=plan_started_observation,
                    remaining_run_actions=(remaining_run_actions - actions_completed),
                    protected_step_ids=completed_step_ids | {step.step_id},
                )
                observation = step_result.observation
                actions_completed += step_result.actions_completed

                if step_result.interrupted:
                    assert step_result.staged_patch is not None
                    self._event(
                        "plan_step_interrupted",
                        plan,
                        observation,
                        step=step,
                        reason=step_result.reason,
                        evidence={
                            "patch": step_result.staged_patch.patch.model_dump(
                                mode="json"
                            )
                        },
                    )
                    budget_reason = self._budget_stop_reason(
                        plan,
                        plan_started_at,
                        plan_started_observation,
                        observation,
                        remaining_run_actions - actions_completed,
                    )
                    try:
                        if budget_reason is not None:
                            raise PlanValidationError(budget_reason)
                        patched_plan = validate_future_plan_patch(
                            step_result.staged_patch.patch,
                            active_plan=plan,
                            planner_observation=(
                                step_result.staged_patch.planner_observation
                            ),
                            current_observation=observation,
                            config=self.planning_config,
                            macros=self.guard.macros,
                            budget=budget,
                            remaining_run_actions=(
                                remaining_run_actions - actions_completed
                            ),
                            protected_step_ids=completed_step_ids | {step.step_id},
                            require_current_basis=False,
                        )
                    except PlanValidationError as exc:
                        reason = (
                            "The active option was interrupted, but its pause "
                            f"handoff patch failed latest-state validation: {exc}"
                        )
                        aborted = self._abort(
                            plan,
                            step,
                            observation,
                            actions_completed,
                            reason,
                            emit_step_cancelled=False,
                        )
                        return self._require_pause_before_replan(
                            aborted,
                            observation,
                            reason,
                        )
                    previous_version = plan.plan_version
                    self.state_store.apply_plan_patch(
                        patched_plan.plan_version,
                        observation.world_revision,
                    )
                    plan = patched_plan
                    by_id = {item.step_id: item for item in plan.steps}
                    step_id = plan.entry_step_id
                    self._event(
                        "plan_patched",
                        plan,
                        observation,
                        reason=(
                            "The exact active option accepted an explicit "
                            "interruption; its guarded pause handoff is now the "
                            "only executable future."
                        ),
                        evidence={
                            "previous_plan_version": previous_version,
                            "patch": (
                                step_result.staged_patch.patch.model_dump(mode="json")
                            ),
                        },
                    )
                    break

                if step_result.succeeded:
                    self._event(
                        "plan_step_succeeded",
                        plan,
                        observation,
                        step=step,
                        reason=step_result.reason,
                    )
                    if step_result.terminated:
                        self._event(
                            "plan_completed",
                            plan,
                            observation,
                            reason="The environment terminated after a verified plan step.",
                        )
                        return PlanExecutionResult(
                            observation=observation,
                            actions_completed=actions_completed,
                            completed=True,
                            terminated=True,
                            success=step_result.success,
                            reason=step_result.reason,
                        )
                    completed_step_ids.add(step.step_id)
                    if step_result.staged_patch is not None:
                        budget_reason = self._budget_stop_reason(
                            plan,
                            plan_started_at,
                            plan_started_observation,
                            observation,
                            remaining_run_actions - actions_completed,
                        )
                        try:
                            if budget_reason is not None:
                                raise PlanValidationError(budget_reason)
                            patched_plan = validate_future_plan_patch(
                                step_result.staged_patch.patch,
                                active_plan=plan,
                                planner_observation=(step_result.staged_patch.planner_observation),
                                current_observation=observation,
                                config=self.planning_config,
                                macros=self.guard.macros,
                                budget=budget,
                                remaining_run_actions=(remaining_run_actions - actions_completed),
                                protected_step_ids=completed_step_ids,
                                require_current_basis=False,
                            )
                        except PlanValidationError as exc:
                            self._event(
                                "plan_patch_rejected",
                                plan,
                                observation,
                                step=step,
                                reason=(
                                    f"Staged future patch failed post-option revalidation: {exc}"
                                ),
                                evidence={
                                    "patch": (
                                        step_result.staged_patch.patch.model_dump(mode="json")
                                    )
                                },
                            )
                        else:
                            previous_version = plan.plan_version
                            self.state_store.apply_plan_patch(
                                patched_plan.plan_version,
                                observation.world_revision,
                            )
                            plan = patched_plan
                            by_id = {item.step_id: item for item in plan.steps}
                            step_id = plan.entry_step_id
                            self._event(
                                "plan_patched",
                                plan,
                                observation,
                                reason=(
                                    "A future-only concurrent patch passed latest-state "
                                    "and remaining-budget validation."
                                ),
                                evidence={
                                    "previous_plan_version": previous_version,
                                    "patch": (
                                        step_result.staged_patch.patch.model_dump(mode="json")
                                    ),
                                },
                            )
                            break
                    step_id = step.on_success
                    break

                if step_result.pause_before_replan:
                    self._event(
                        "plan_step_failed",
                        plan,
                        observation,
                        step=step,
                        reason=step_result.reason,
                    )
                    aborted = self._abort(
                        plan,
                        step,
                        observation,
                        actions_completed,
                        step_result.reason,
                        emit_step_cancelled=False,
                    )
                    return self._require_pause_before_replan(
                        aborted,
                        observation,
                        step_result.reason,
                    )

                if retries_remaining > 0 and not step_result.terminated:
                    retries_remaining -= 1
                    self._event(
                        "plan_step_progress",
                        plan,
                        observation,
                        step=step,
                        reason=(
                            f"Verified-safe retry requested; {retries_remaining} retries remain."
                        ),
                        evidence={"prior_failure": step_result.reason},
                    )
                    assumptions = evaluate_conditions(plan.assumptions, observation)
                    preconditions = evaluate_conditions(step.preconditions, observation)
                    if (
                        self._first_non_true(assumptions) is not None
                        or self._first_non_true(preconditions) is not None
                    ):
                        reason = (
                            "Retry cancelled because an assumption or precondition "
                            "is no longer true."
                        )
                        return self._abort(
                            plan,
                            step,
                            observation,
                            actions_completed,
                            reason,
                            evidence={
                                "assumptions": self._evaluations_json(assumptions),
                                "preconditions": self._evaluations_json(preconditions),
                            },
                        )
                    continue

                self._event(
                    "plan_step_failed",
                    plan,
                    observation,
                    step=step,
                    reason=step_result.reason,
                )
                if step_result.terminated:
                    return self._abort(
                        plan,
                        step,
                        observation,
                        actions_completed,
                        step_result.reason,
                        terminated=True,
                        success=step_result.success,
                        emit_step_cancelled=False,
                    )
                if step.on_failure is None:
                    return self._abort(
                        plan,
                        step,
                        observation,
                        actions_completed,
                        step_result.reason,
                        emit_step_cancelled=False,
                    )
                step_id = step.on_failure
                break

        self._event(
            "plan_completed",
            plan,
            observation,
            reason="The accepted plan reached a terminal success branch.",
        )
        return PlanExecutionResult(
            observation=observation,
            actions_completed=actions_completed,
            completed=True,
            terminated=False,
            success=None,
            reason="Plan completed.",
        )

    def _resolve_approach_params(self, action: Action) -> ApproachOptionParams | None:
        """Decide whether this action is owned by a monitored approach option.

        A contracted semantic approach always is — the option is how the action
        is defined, not an optional optimization, because the native order is
        acknowledged long before the character stops walking. The legacy macro
        path keeps its existing feature flag.
        """

        contract = contract_for(action)
        if contract is not None:
            if contract.execution is not ActionExecution.MONITORED_OPTION:
                return None
            if isinstance(action, PerformContextAction):
                return None
            target_id = getattr(action, "target_id", None)
            if not isinstance(target_id, str) or not target_id:
                return None
            return ApproachOptionParams(
                target_id=target_id,
                arrival_distance=self.planning_config.semantic_approach_arrival_distance,
                threat_distance=self.planning_config.semantic_approach_threat_distance,
            )
        if (
            self.planning_config.stateful_approach_options_enabled
            and isinstance(action, SkillAction)
            and self.guard.macros.is_approach_option(action)
        ):
            return self.guard.macros.approach_option_params(action)
        return None

    async def _execute_step(
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
    ) -> _StepResult:
        if remaining_run_actions <= 0:
            return _StepResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason="Run action budget is exhausted.",
            )

        try:
            action = self.guard.validate(step.action, observation)
        except SafetyViolation as exc:
            self.logger.write(
                "action_rejected",
                step_index=observation.step_index,
                payload={
                    "action": step.action.model_dump(mode="json"),
                    "control_mode": observation.control_mode.value,
                    "accepted": False,
                    "executed": False,
                    "dry_run": True,
                    "primitive_actions": 0,
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return _StepResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=f"Existing action guard rejected the step: {exc}",
            )

        try:
            reserved_risk = budget.reserve(action, self.guard.macros)
        except PlanBudgetError as exc:
            return _StepResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=str(exc),
            )
        self._event(
            "plan_budget_reserved",
            plan,
            observation,
            step=step,
            reason="Reserved one action and its typed risk before dispatch.",
            evidence={
                "pointer_actions": reserved_risk[0],
                "purchase_actions": reserved_risk[1],
                "native_assisted_actions": reserved_risk[2],
            },
        )

        if isinstance(action, ConsultAdvisorAction):
            return await self._execute_advisor_step(
                action,
                plan,
                step,
                observation,
                budget,
            )

        if isinstance(action, RequestAffordanceAction):
            return await self._execute_affordance_request_step(
                action,
                plan,
                step,
                observation,
                budget,
            )

        movement_option: StatefulMovementOption | None = None
        if (
            self.planning_config.stateful_movement_options_enabled
            and self.guard.macros.is_stateful_movement(action)
        ):
            assert isinstance(action, SkillAction)
            movement_option = StatefulMovementOption(
                option_id=(f"option-{plan.plan_id}-{plan.plan_version}-{step.step_id}"),
                action=action,
                environment=self.environment,
                require_paused_start=(self.planning_config.require_paused_between_actions),
            )
            try:
                prepared = movement_option.prepare(observation)
            except OptionLifecycleError as exc:
                budget.release(reserved_risk)
                reason = f"Stateful movement option preparation failed: {exc}"
                self._event(
                    "plan_budget_released",
                    plan,
                    observation,
                    step=step,
                    reason="No movement command was dispatched.",
                )
                self._event(
                    "option_failed",
                    plan,
                    observation,
                    step=step,
                    reason=reason,
                    evidence={
                        "option_id": movement_option.option_id,
                        "option_status": movement_option.status.value,
                    },
                )
                return _StepResult(
                    observation=observation,
                    succeeded=False,
                    actions_completed=0,
                    reason=reason,
                )
            self._event(
                "option_prepared",
                plan,
                observation,
                step=step,
                reason=prepared.reason,
                evidence={
                    "option_id": prepared.option_id,
                    "option_status": prepared.status.value,
                    "start_revision": prepared.revision.model_dump(mode="json"),
                },
            )

        native_movement_option: StatefulNativeMovementOption | None = None
        contract = contract_for(action)
        if (
            isinstance(
                action,
                (
                    MoveInDirectionAction,
                    ExitCurrentBuildingAction,
                    PerformContextAction,
                ),
            )
            and contract is not None
            and contract.execution is ActionExecution.MONITORED_OPTION
        ):
            native_movement_option = StatefulNativeMovementOption(
                option_id=(f"native-movement-{plan.plan_id}-{plan.plan_version}-{step.step_id}"),
                action=action,
                environment=self.environment,
                require_paused_start=(self.planning_config.require_paused_between_actions),
            )
            try:
                prepared = native_movement_option.prepare(observation)
            except OptionLifecycleError as exc:
                budget.release(reserved_risk)
                reason = f"Native movement option preparation failed: {exc}"
                self._event(
                    "plan_budget_released",
                    plan,
                    observation,
                    step=step,
                    reason="No native movement command was dispatched.",
                )
                self._event(
                    "option_failed",
                    plan,
                    observation,
                    step=step,
                    reason=reason,
                    evidence={
                        "option_id": native_movement_option.option_id,
                        "option_status": native_movement_option.status.value,
                    },
                )
                return _StepResult(
                    observation=observation,
                    succeeded=False,
                    actions_completed=0,
                    reason=reason,
                )
            self._event(
                "option_prepared",
                plan,
                observation,
                step=step,
                reason=prepared.reason,
                evidence={
                    "option_id": prepared.option_id,
                    "option_status": prepared.status.value,
                    "start_revision": prepared.revision.model_dump(mode="json"),
                },
            )

        approach_option: StatefulApproachOption | None = None
        approach_params = self._resolve_approach_params(action)
        if approach_params is not None:
            approach_option = StatefulApproachOption(
                option_id=f"approach-{plan.plan_id}-{plan.plan_version}-{step.step_id}",
                action=action,
                environment=self.environment,
                target_id=approach_params.target_id,
                arrival_distance=approach_params.arrival_distance,
                threat_distance=approach_params.threat_distance,
                require_paused_start=(self.planning_config.require_paused_between_actions),
            )
            try:
                prepared = approach_option.prepare(observation)
            except OptionLifecycleError as exc:
                budget.release(reserved_risk)
                reason = f"Approach option preparation failed: {exc}"
                self._event(
                    "plan_budget_released",
                    plan,
                    observation,
                    step=step,
                    reason="No approach command was dispatched.",
                )
                self._event(
                    "option_failed",
                    plan,
                    observation,
                    step=step,
                    reason=reason,
                    evidence={
                        "option_id": approach_option.option_id,
                        "option_status": approach_option.status.value,
                    },
                )
                return _StepResult(
                    observation=observation,
                    succeeded=False,
                    actions_completed=0,
                    reason=reason,
                )
            self._event(
                "option_prepared",
                plan,
                observation,
                step=step,
                reason=prepared.reason,
                evidence={
                    "option_id": prepared.option_id,
                    "option_status": prepared.status.value,
                    "start_revision": prepared.revision.model_dump(mode="json"),
                },
            )

        action_start_revision = observation.world_revision
        command = self.state_store.begin_command(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            step_id=step.step_id,
            action_kind=action.kind,
            start_revision=action_start_revision,
        )
        dispatch_context = CommandDispatchContext(
            command_id=command.command_id,
            based_on_revision=action_start_revision,
        )
        # The token re-checks this exact authorization after any polite input
        # lease is acquired, because the lease wait can outlive the evidence.
        token = ExecutionToken(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            step_id=step.step_id,
            command_id=command.command_id,
            control_mode=plan.control_mode,
            validated_revision=action_start_revision,
            # Deferred: the boundary must read the store when the lease is
            # acquired, not the snapshot that existed at validation time.
            latest_observation=lambda: self.state_store.latest,
            assumptions=tuple(plan.assumptions),
            preconditions=tuple(step.preconditions),
        )
        step_deadline = self.clock.monotonic() + step.timeout_seconds
        self._event(
            "plan_step_started",
            plan,
            observation,
            step=step,
            reason="Action passed the normal guard and reserved plan budget.",
            evidence={
                "action_start_revision": action_start_revision.model_dump(mode="json"),
                "command_id": command.command_id,
                "remaining_actions_before_commit": budget.remaining_actions,
            },
        )

        staged_patch: _StagedPatch | None = None
        monitored_outcome: OptionPoll | None = None
        try:
            if movement_option is not None:
                transition, staged_patch = await self._execute_movement_option(
                    movement_option,
                    plan,
                    step,
                    observation,
                    budget,
                    dispatch_context,
                    remaining_run_actions=remaining_run_actions,
                    protected_step_ids=protected_step_ids,
                    token=token,
                )
            elif native_movement_option is not None or approach_option is not None:
                monitored_option = native_movement_option or approach_option
                assert monitored_option is not None
                (
                    transition,
                    staged_patch,
                    monitored_outcome,
                ) = await self._execute_monitored_option(
                    monitored_option,
                    plan,
                    step,
                    observation,
                    budget,
                    dispatch_context,
                    remaining_run_actions=remaining_run_actions,
                    protected_step_ids=protected_step_ids,
                    token=token,
                    step_deadline=step_deadline,
                )
            else:
                transition = await self.environment.dispatch(
                    action,
                    command=dispatch_context,
                    token=token,
                )
        except asyncio.CancelledError:
            budget.commit()
            reason = (
                "Independent safety supervision cancelled the in-flight action; "
                "delivery is uncertain and the reservation remains spent."
            )
            self.state_store.fail_active_command(reason)
            self._event(
                "plan_budget_committed",
                plan,
                observation,
                step=step,
                reason=reason,
            )
            self._event(
                "plan_step_cancelled",
                plan,
                observation,
                step=step,
                reason=reason,
            )
            self._event(
                "plan_aborted",
                plan,
                observation,
                step=step,
                reason=reason,
            )
            raise
        except Exception as exc:
            # An environment error leaves command delivery uncertain. Commit the
            # reservation conservatively so an at-most-once action is not duplicated.
            budget.commit()
            self.state_store.fail_active_command(f"{type(exc).__name__}: {exc}")
            self._event(
                "plan_budget_committed",
                plan,
                observation,
                step=step,
                reason=(
                    "Command delivery is uncertain after an environment error; "
                    "the reservation remains spent."
                ),
            )
            self.logger.write(
                "environment_error",
                step_index=observation.step_index,
                payload={"type": type(exc).__name__, "message": str(exc)},
            )
            return _StepResult(
                observation=observation,
                succeeded=False,
                actions_completed=1,
                reason=(
                    "Environment failed after command dispatch; the reserved action "
                    f"was conservatively committed: {type(exc).__name__}: {exc}"
                ),
            )

        boundary = transition.receipt.input_boundary
        if boundary is not None:
            rejected = boundary.decision is InputBoundaryDecision.REJECTED
            self._event(
                "input_boundary_rejected" if rejected else "input_boundary_revalidated",
                plan,
                observation,
                step=step,
                reason=boundary.reason,
                evidence={
                    "command_id": command.command_id,
                    "decision": boundary.decision.value,
                    "lease_wait_seconds": boundary.lease_wait_seconds,
                    "validated_revision": action_start_revision.model_dump(mode="json"),
                    "boundary_revision": (
                        boundary.boundary_revision.model_dump(mode="json")
                        if boundary.boundary_revision is not None
                        else None
                    ),
                },
            )

        if not transition.receipt.accepted and not transition.receipt.executed:
            budget.release(reserved_risk)
            budget_event = "plan_budget_released"
            reservation_reason = (
                "The environment definitively rejected the action without execution."
            )
        else:
            budget.commit()
            budget_event = "plan_budget_committed"
            reservation_reason = (
                "The environment accepted or may have executed the dispatched action."
            )
        try:
            if transition.receipt.command_id not in {
                None,
                command.command_id,
            }:
                raise CommandCausalityError(
                    "Environment acknowledgement command ID does not match "
                    f"active command {command.command_id!r}."
                )
            latest = self.observe_transition(
                plan,
                step,
                observation,
                transition,
                command.command_id,
                action_start_revision,
            )
            self.state_store.complete_command(
                command.command_id,
                latest.world_revision,
            )
        except CommandCausalityError as exc:
            return _StepResult(
                observation=observation,
                succeeded=False,
                actions_completed=1,
                reason=f"Command causality validation failed: {exc}",
            )
        self._event(
            budget_event,
            plan,
            latest,
            step=step,
            reason=reservation_reason,
        )
        if not transition.receipt.accepted and not transition.receipt.executed:
            return _StepResult(
                observation=latest,
                succeeded=False,
                actions_completed=1,
                reason=(
                    "The environment rejected the action without execution: "
                    f"{transition.receipt.message}"
                ),
                terminated=transition.terminated,
                success=transition.success,
            )
        if (
            monitored_outcome is not None
            and staged_patch is not None
            and staged_patch.interrupts_active_step
            and monitored_outcome.status is OptionStatus.CANCELLED
        ):
            return _StepResult(
                observation=latest,
                succeeded=False,
                actions_completed=1,
                reason=(
                    "The exact active option stopped for a validated strategic "
                    "interruption; control passes only to its pause handoff."
                ),
                terminated=transition.terminated,
                success=transition.success,
                staged_patch=staged_patch,
                interrupted=True,
            )
        if monitored_outcome is not None and monitored_outcome.status is not OptionStatus.SUCCEEDED:
            # The monitored option is this action's authority on arrival. A lost
            # target or a hostile inside threat range is a terminal verdict, not
            # a hint to be overridden by a postcondition that happens to read
            # true on unrelated evidence.
            return _StepResult(
                observation=latest,
                succeeded=False,
                actions_completed=1,
                reason=(
                    "The monitored native option did not reach its terminal success, "
                    f"so the step cannot succeed: {monitored_outcome.reason}"
                ),
                terminated=transition.terminated,
                success=transition.success,
                pause_before_replan=(
                    latest.telemetry is not None
                    and latest.telemetry.game.paused is False
                ),
            )
        contract = contract_for(step.action)
        if contract is not None and contract.controller_verified:
            if monitored_outcome is not None:
                self._event(
                    "plan_step_progress",
                    plan,
                    latest,
                    step=step,
                    reason="Accepted the monitored option's keyed terminal verdict.",
                    evidence={
                        "controller_verified": True,
                        "option_status": monitored_outcome.status.value,
                        "terminal_reason": monitored_outcome.reason,
                    },
                )
                return _StepResult(
                    observation=latest,
                    succeeded=True,
                    actions_completed=1,
                    reason=(
                        "Controller-owned monitored action reached its native "
                        "terminal success."
                    ),
                    terminated=transition.terminated,
                    success=transition.success,
                    staged_patch=staged_patch,
                )
            recovery = (
                transition.receipt.semantic.camera_recovery
                if transition.receipt.semantic is not None
                else None
            )
            if recovery is None:
                return _StepResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=(
                        "The controller-verified action returned no typed terminal "
                        "evidence, so its result cannot be assumed."
                    ),
                    terminated=transition.terminated,
                    success=transition.success,
                )
            succeeded = recovery.status in {
                CameraRecoveryStatus.ALREADY_CLEAR,
                CameraRecoveryStatus.RECOVERED,
            }
            self._event(
                "plan_step_progress",
                plan,
                latest,
                step=step,
                reason="Accepted the controller-owned terminal camera-recovery verdict.",
                evidence={
                    "controller_verified": True,
                    "status": recovery.status.value,
                    "chosen_candidate": recovery.chosen_candidate,
                    "candidate_count": len(recovery.candidates),
                },
            )
            return _StepResult(
                observation=latest,
                succeeded=succeeded,
                actions_completed=1,
                reason=(f"Controller-owned camera recovery returned {recovery.status.value!r}."),
                terminated=transition.terminated,
                success=transition.success,
                staged_patch=staged_patch if succeeded else None,
            )
        while True:
            success_evaluations = evaluate_conditions(
                step.success_conditions,
                latest,
                after_revision=action_start_revision,
            )
            failure_evaluations = evaluate_conditions(
                step.failure_conditions,
                latest,
                after_revision=action_start_revision,
            )
            self._event(
                "plan_step_progress",
                plan,
                latest,
                step=step,
                reason="Evaluated typed postconditions on the latest revision.",
                evidence={
                    "success_conditions": self._evaluations_json(success_evaluations),
                    "failure_conditions": self._evaluations_json(failure_evaluations),
                },
            )

            triggered_failure = next(
                (
                    evaluation
                    for evaluation in failure_evaluations
                    if evaluation.result == ConditionResult.TRUE
                ),
                None,
            )
            if triggered_failure is not None:
                return _StepResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=(f"A typed failure condition became true: {triggered_failure.reason}"),
                    terminated=transition.terminated,
                    success=transition.success,
                )
            if success_evaluations and all(
                evaluation.result == ConditionResult.TRUE for evaluation in success_evaluations
            ):
                return _StepResult(
                    observation=latest,
                    succeeded=True,
                    actions_completed=1,
                    reason="All success conditions are true on a later world revision.",
                    terminated=transition.terminated,
                    success=transition.success,
                    staged_patch=staged_patch,
                )
            if transition.terminated:
                return _StepResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=(
                        "The environment terminated before the step's success "
                        "conditions were verified."
                    ),
                    terminated=True,
                    success=transition.success,
                )
            budget_reason = self._budget_stop_reason(
                plan,
                plan_started_at,
                plan_started_observation,
                latest,
                remaining_run_actions - 1,
                check_action_budget=False,
            )
            if budget_reason is not None:
                return _StepResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=budget_reason,
                )
            if (
                step.observation_policy == ObservationPolicy.AFTER_ACTION
                or self.clock.monotonic() >= step_deadline
            ):
                return _StepResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=_unmet_postcondition_reason(
                        success_evaluations,
                        step_deadline_seconds=step.timeout_seconds,
                    ),
                )

            try:
                remaining_step_seconds = step_deadline - self.clock.monotonic()
                remaining_plan_seconds = plan.max_wall_seconds - (
                    self.clock.monotonic() - plan_started_at
                )
                latest = await self.state_store.wait_for(
                    lambda _: True,
                    after_revision=latest.world_revision,
                    timeout_seconds=min(
                        remaining_step_seconds,
                        remaining_plan_seconds,
                    ),
                )
            except TimeoutError:
                return _StepResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=_unmet_postcondition_reason(
                        success_evaluations,
                        step_deadline_seconds=step.timeout_seconds,
                    ),
                )

    async def _execute_advisor_step(
        self,
        action: ConsultAdvisorAction,
        plan: PlanEnvelope,
        step: PlanStep,
        observation: Observation,
        budget: PlanBudgetLedger,
    ) -> _StepResult:
        """Run one cognitive action without creating a world command."""

        self._event(
            "plan_step_started",
            plan,
            observation,
            step=step,
            reason="Advisor request passed the guard and reserved one plan action.",
            evidence={
                "controller_primitives": 0,
                "world_command_created": False,
                "remaining_actions_before_commit": budget.remaining_actions,
            },
        )
        self._event(
            "advisor_requested",
            plan,
            observation,
            step=step,
            reason=action.question,
            evidence={"focus": action.focus.value},
        )
        if self.consult_advisor is None:
            budget.release((0, 0, 0))
            reason = "No strategic advisor is attached to this runtime."
            self._event(
                "plan_budget_released",
                plan,
                observation,
                step=step,
                reason=reason,
            )
            return _StepResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=reason,
            )

        try:
            result = await self.consult_advisor(
                action,
                observation,
                plan.plan_id,
                plan.plan_version,
                step.step_id,
            )
        except asyncio.CancelledError:
            budget.commit()
            self._event(
                "plan_budget_committed",
                plan,
                observation,
                step=step,
                reason="The advisor request was cancelled after it may have reached the provider.",
            )
            raise

        budget.commit()
        evidence = result.receipt.advisor
        if evidence is None:
            reason = "Advisor execution returned no typed evidence."
            succeeded = False
            status = "missing_evidence"
        else:
            reason = evidence.reason
            succeeded = evidence.status.value == "answered"
            status = evidence.status.value
        terminal_event = (
            "advisor_completed"
            if succeeded
            else "advisor_failed"
            if status == "failed"
            else "advisor_suppressed"
        )
        self._event(
            "plan_budget_committed",
            plan,
            result.observation,
            step=step,
            reason="The cognitive request consumed one bounded plan action.",
        )
        self._event(
            terminal_event,
            plan,
            result.observation,
            step=step,
            reason=reason,
            evidence={
                "status": status,
                "controller_primitives": 0,
                "world_command_created": False,
            },
        )
        return _StepResult(
            observation=result.observation,
            succeeded=succeeded,
            actions_completed=1,
            reason=reason,
        )

    async def _execute_affordance_request_step(
        self,
        action: RequestAffordanceAction,
        plan: PlanEnvelope,
        step: PlanStep,
        observation: Observation,
        budget: PlanBudgetLedger,
    ) -> _StepResult:
        """Retain one capability gap without creating a world command."""

        self._event(
            "plan_step_started",
            plan,
            observation,
            step=step,
            reason="Affordance request passed the guard and reserved one plan action.",
            evidence={
                "controller_primitives": 0,
                "world_command_created": False,
                "remaining_actions_before_commit": budget.remaining_actions,
            },
        )
        if self.request_affordance is None:
            budget.release((0, 0, 0))
            reason = "No affordance-request sink is attached to this runtime."
            self._event(
                "plan_budget_released",
                plan,
                observation,
                step=step,
                reason=reason,
            )
            return _StepResult(
                observation=observation,
                succeeded=False,
                actions_completed=0,
                reason=reason,
            )

        result = await self.request_affordance(
            action,
            observation,
            plan.plan_id,
            plan.plan_version,
            step.step_id,
        )
        budget.commit()
        evidence = result.receipt.affordance_request
        succeeded = evidence is not None
        reason = (
            evidence.reason
            if evidence is not None
            else "Affordance request returned no typed evidence."
        )
        self._event(
            "plan_budget_committed",
            plan,
            result.observation,
            step=step,
            reason="The cognitive request consumed one bounded plan action.",
        )
        self._event(
            "affordance_request_completed" if succeeded else "affordance_request_failed",
            plan,
            result.observation,
            step=step,
            reason=reason,
            evidence={
                "status": (evidence.status.value if evidence is not None else "missing_evidence"),
                "controller_primitives": 0,
                "world_command_created": False,
            },
        )
        return _StepResult(
            observation=result.observation,
            succeeded=succeeded,
            actions_completed=1,
            reason=reason,
        )

    async def _execute_movement_option(
        self,
        option: StatefulMovementOption,
        plan: PlanEnvelope,
        step: PlanStep,
        observation: Observation,
        budget: PlanBudgetLedger,
        command: CommandDispatchContext,
        *,
        remaining_run_actions: int,
        protected_step_ids: set[str],
        token: ExecutionToken | None = None,
    ) -> tuple[Transition, _StagedPatch | None]:
        option_task = option.start(command, token=token)
        self._event(
            "option_started",
            plan,
            observation,
            step=step,
            reason=option.reason,
            evidence={
                "option_id": option.option_id,
                "option_status": option.status.value,
            },
        )
        subscription = self.state_store.subscribe()
        update_task: asyncio.Task[Any] | None = asyncio.create_task(subscription.get())
        planner_task: asyncio.Task[PlannerOutput] | None = None
        planner_observation: Observation | None = None
        planner_started_at: float | None = None
        staged_patch: _StagedPatch | None = None

        if (
            self.planning_config.concurrent_option_planning_enabled
            and self.concurrent_planner is not None
        ):
            planner_observation = observation.model_copy(
                update={
                    "active_plan": ActivePlanContext(
                        plan_id=plan.plan_id,
                        plan_version=plan.plan_version,
                        objective=plan.objective,
                        active_step_id=step.step_id,
                        completed_step_ids=sorted(protected_step_ids - {step.step_id}),
                        remaining_actions=budget.remaining_actions,
                    )
                },
                deep=True,
            )
            planner_started_at = self.clock.monotonic()
            planner_task = asyncio.create_task(
                self.concurrent_planner(planner_observation),
                name=f"kenshi-agent-advisory-{option.option_id}",
            )

        try:
            while not option_task.done():
                waiting: set[asyncio.Task[Any]] = {option_task}
                if update_task is not None:
                    waiting.add(update_task)
                if planner_task is not None:
                    waiting.add(planner_task)
                done, _ = await asyncio.wait(
                    waiting,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if planner_task is not None and planner_task in done:
                    assert planner_observation is not None
                    assert planner_started_at is not None
                    staged_patch = self._consume_concurrent_planner_result(
                        planner_task,
                        plan,
                        step,
                        planner_observation,
                        budget,
                        remaining_run_actions=remaining_run_actions - 1,
                        protected_step_ids=protected_step_ids,
                        planner_latency_seconds=(self.clock.monotonic() - planner_started_at),
                    )
                    planner_task = None

                if update_task is not None and update_task in done:
                    update = update_task.result()
                    progress = option.poll(update)
                    self._event(
                        "option_progress",
                        plan,
                        update.observation,
                        step=step,
                        reason=progress.reason,
                        evidence={
                            "option_id": option.option_id,
                            "option_status": progress.status.value,
                            "sequence_status": update.sequence_status.value,
                            "changed_paths": list(update.delta.changed_paths),
                        },
                    )
                    update_task = (
                        None if option_task.done() else asyncio.create_task(subscription.get())
                    )

            terminal = option.poll()
            latest = self.state_store.latest or observation
            if terminal.status is OptionStatus.SUCCEEDED:
                self._event(
                    "option_succeeded",
                    plan,
                    latest,
                    step=step,
                    reason=terminal.reason,
                    evidence={
                        "option_id": option.option_id,
                        "option_status": terminal.status.value,
                    },
                )
            elif terminal.status is OptionStatus.FAILED:
                self._event(
                    "option_failed",
                    plan,
                    latest,
                    step=step,
                    reason=terminal.reason,
                    evidence={
                        "option_id": option.option_id,
                        "option_status": terminal.status.value,
                    },
                )
            return option.result(), staged_patch
        except asyncio.CancelledError:
            cancelled = await option.cancel(
                "Independent safety supervision cancelled the movement option."
            )
            self._event(
                (
                    "option_cancelled"
                    if cancelled.status is OptionStatus.CANCELLED
                    else "option_failed"
                ),
                plan,
                self.state_store.latest or observation,
                step=step,
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
                self.logger.write(
                    "strategic_planner_call",
                    step_index=observation.step_index,
                    payload={
                        "source": "concurrent_option_cancelled",
                        "planner_latency_seconds": (
                            self.clock.monotonic() - planner_started_at
                            if planner_started_at is not None
                            else 0.0
                        ),
                        "world_revision": observation.world_revision.model_dump(mode="json"),
                        "control_mode": observation.control_mode.value,
                        "output_type": "cancelled",
                    },
                )
                self._event(
                    "concurrent_planner_discarded",
                    plan,
                    self.state_store.latest or observation,
                    step=step,
                    reason="Movement ended before the concurrent advisory completed.",
                    evidence={"option_id": option.option_id},
                )

    async def _execute_monitored_option(
        self,
        option: StatefulApproachOption | StatefulNativeMovementOption,
        plan: PlanEnvelope,
        step: PlanStep,
        observation: Observation,
        budget: PlanBudgetLedger,
        command: CommandDispatchContext,
        *,
        remaining_run_actions: int,
        protected_step_ids: set[str],
        token: ExecutionToken | None = None,
        step_deadline: float,
    ) -> tuple[Transition, _StagedPatch | None, OptionPoll]:
        # Sibling of _execute_movement_option. The one difference is the terminal
        # condition: native dispatch is acknowledged quickly while the character
        # keeps walking, so this loop runs until the option's own authority
        # reports terminal or the step deadline passes, not merely until the
        # dispatch task completes. A target approach watches world state; a
        # targetless direction watches its keyed native acknowledgement.
        option_task = option.start(command, token=token)
        self._event(
            "option_started",
            plan,
            observation,
            step=step,
            reason=option.reason,
            evidence={
                "option_id": option.option_id,
                "option_status": option.status.value,
            },
        )
        subscription = self.state_store.subscribe()
        update_task: asyncio.Task[Any] | None = asyncio.create_task(subscription.get())
        planner_task: asyncio.Task[PlannerOutput] | None = None
        planner_observation: Observation | None = None
        planner_started_at: float | None = None
        staged_patch: _StagedPatch | None = None
        timed_out = False
        interrupted = False

        if (
            self.planning_config.concurrent_option_planning_enabled
            and self.concurrent_planner is not None
        ):
            planner_observation = observation.model_copy(
                update={
                    "active_plan": ActivePlanContext(
                        plan_id=plan.plan_id,
                        plan_version=plan.plan_version,
                        objective=plan.objective,
                        active_step_id=step.step_id,
                        active_step_interrupt_policy=step.interrupt_policy,
                        completed_step_ids=sorted(protected_step_ids - {step.step_id}),
                        remaining_actions=budget.remaining_actions,
                    )
                },
                deep=True,
            )
            planner_started_at = self.clock.monotonic()
            planner_task = asyncio.create_task(
                self.concurrent_planner(planner_observation),
                name=f"kenshi-agent-advisory-{option.option_id}",
            )

        try:
            while option.poll().status is OptionStatus.RUNNING:
                remaining = step_deadline - self.clock.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                waiting: set[asyncio.Task[Any]] = set()
                if update_task is not None:
                    waiting.add(update_task)
                # The dispatch task may already be done (ack received) while the
                # option keeps walking; only wait on it while it is pending.
                if not option_task.done():
                    waiting.add(option_task)
                if planner_task is not None:
                    waiting.add(planner_task)
                if not waiting:
                    # No update source is live and the option is still running;
                    # a further wait cannot make progress, so stop deterministically.
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
                    assert planner_started_at is not None
                    staged_patch = self._consume_concurrent_planner_result(
                        planner_task,
                        plan,
                        step,
                        planner_observation,
                        budget,
                        remaining_run_actions=remaining_run_actions - 1,
                        protected_step_ids=protected_step_ids,
                        planner_latency_seconds=(self.clock.monotonic() - planner_started_at),
                    )
                    planner_task = None

                if update_task is not None and update_task in done:
                    update = update_task.result()
                    progress = option.poll(update)
                    self._event(
                        "option_progress",
                        plan,
                        update.observation,
                        step=step,
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
                    # The dispatch resolved (ack or rejection); fold it into the
                    # option state so a rejected order fails rather than waiting.
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
                    self._event(
                        "option_interrupted",
                        plan,
                        self.state_store.latest or observation,
                        step=step,
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
                    "Monitored native movement exceeded its step timeout before terminal success."
                )

            terminal = option.poll()
            latest = self.state_store.latest or observation
            if terminal.status is OptionStatus.SUCCEEDED:
                self._event(
                    "option_succeeded",
                    plan,
                    latest,
                    step=step,
                    reason=terminal.reason,
                    evidence={
                        "option_id": option.option_id,
                        "option_status": terminal.status.value,
                    },
                )
            elif not interrupted:
                self._event(
                    "option_failed",
                    plan,
                    latest,
                    step=step,
                    reason=(
                        "Monitored native movement timed out before terminal success."
                        if timed_out
                        else terminal.reason
                    ),
                    evidence={
                        "option_id": option.option_id,
                        "option_status": terminal.status.value,
                    },
                )
            # A dispatched order that failed to arrive still delivered input, so
            # return its causal receipt rather than raising. The terminal verdict
            # travels with it: for this action the option *is* the outcome, so a
            # lost target or a hostile in threat range must fail the step even if
            # some postcondition happens to read true. Only a dispatch that never
            # produced a transition raises through option.result().
            if option.transition is not None:
                return option.transition.model_copy(deep=True), staged_patch, terminal
            return option.result(), staged_patch, terminal
        except asyncio.CancelledError:
            cancelled = await option.cancel(
                "Independent safety supervision cancelled the monitored native option."
            )
            self._event(
                (
                    "option_cancelled"
                    if cancelled.status is OptionStatus.CANCELLED
                    else "option_failed"
                ),
                plan,
                self.state_store.latest or observation,
                step=step,
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
                self.logger.write(
                    "strategic_planner_call",
                    step_index=observation.step_index,
                    payload={
                        "source": "concurrent_option_cancelled",
                        "planner_latency_seconds": (
                            self.clock.monotonic() - planner_started_at
                            if planner_started_at is not None
                            else 0.0
                        ),
                        "world_revision": observation.world_revision.model_dump(mode="json"),
                        "control_mode": observation.control_mode.value,
                        "output_type": "cancelled",
                    },
                )
                self._event(
                    "concurrent_planner_discarded",
                    plan,
                    self.state_store.latest or observation,
                    step=step,
                    reason=(
                        "Monitored native option ended before the concurrent advisory completed."
                    ),
                    evidence={"option_id": option.option_id},
                )

    def _consume_concurrent_planner_result(
        self,
        planner_task: asyncio.Task[PlannerOutput],
        plan: PlanEnvelope,
        step: PlanStep,
        planner_observation: Observation,
        budget: PlanBudgetLedger,
        *,
        remaining_run_actions: int,
        protected_step_ids: set[str],
        planner_latency_seconds: float,
    ) -> _StagedPatch | None:
        try:
            output = planner_task.result()
        except Exception as exc:
            self.logger.write(
                "strategic_planner_call",
                step_index=planner_observation.step_index,
                payload={
                    "source": "concurrent_option_error",
                    "planner_latency_seconds": planner_latency_seconds,
                    "world_revision": (planner_observation.world_revision.model_dump(mode="json")),
                    "control_mode": planner_observation.control_mode.value,
                    "output_type": "error",
                },
            )
            self._event(
                "concurrent_planner_discarded",
                plan,
                self.state_store.latest or planner_observation,
                step=step,
                reason=f"Concurrent planner failed: {type(exc).__name__}: {exc}",
            )
            return None

        self.logger.write(
            "strategic_planner_call",
            step_index=planner_observation.step_index,
            payload={
                "source": "concurrent_option",
                "planner_latency_seconds": planner_latency_seconds,
                "world_revision": planner_observation.world_revision.model_dump(mode="json"),
                "control_mode": planner_observation.control_mode.value,
                "output_type": type(output).__name__,
            },
        )
        if not isinstance(output, PlanPatch):
            self._event(
                "concurrent_planner_discarded",
                plan,
                self.state_store.latest or planner_observation,
                step=step,
                reason=(
                    "Concurrent option planning accepts only a typed PlanPatch advisory."
                ),
                evidence={"output_type": type(output).__name__},
            )
            return None

        latest = self.state_store.latest or planner_observation
        interrupts_active_step = output.interrupt_active_step_id is not None
        try:
            validate_future_plan_patch(
                output,
                active_plan=plan,
                planner_observation=planner_observation,
                current_observation=latest,
                config=self.planning_config,
                macros=self.guard.macros,
                budget=budget,
                remaining_run_actions=remaining_run_actions,
                protected_step_ids=protected_step_ids,
                # An explicit interrupt is useful only while the world keeps
                # moving. Its effect is restricted to a guarded pause handoff,
                # then every replacement action is revalidated on latest state.
                require_current_basis=not interrupts_active_step,
            )
        except PlanValidationError as exc:
            self._event(
                "plan_patch_rejected",
                plan,
                latest,
                step=step,
                reason=f"Concurrent future patch was rejected: {exc}",
                evidence={"patch": output.model_dump(mode="json")},
            )
            return None

        self._event(
            "plan_interrupt_staged" if interrupts_active_step else "plan_patch_staged",
            plan,
            latest,
            step=step,
            reason=(
                "Concurrent revision names the exact interruptible active "
                "step and begins with a guarded pause handoff."
                if interrupts_active_step
                else "Concurrent future patch matches the active plan and "
                "immutable planner revision; application awaits option completion."
            ),
            evidence={"patch": output.model_dump(mode="json")},
        )
        return _StagedPatch(
            patch=output.model_copy(deep=True),
            planner_observation=planner_observation.model_copy(deep=True),
        )

    def _budget_stop_reason(
        self,
        plan: PlanEnvelope,
        plan_started_at: float,
        plan_started_observation: Observation,
        current: Observation,
        remaining_run_actions: int,
        *,
        check_action_budget: bool = True,
    ) -> str | None:
        if check_action_budget and remaining_run_actions <= 0:
            return "Run action budget is exhausted."
        wall_elapsed = self.clock.monotonic() - plan_started_at
        if wall_elapsed >= plan.max_wall_seconds:
            return "Plan wall-clock budget is exhausted."
        # A game-time budget bounds how much *game* a plan may consume, which is
        # meaningful only while the game is paused between actions: then time
        # passes solely during the plan's own movement. When the agent plays
        # continuously the world runs while it thinks, so a twenty-second model
        # call spends the budget without the plan doing anything at all - plans
        # were aborting on it repeatedly having executed a single hover. Wall
        # clock and the action budget still bound the plan in that mode.
        if self.planning_config.require_paused_between_actions:
            game_elapsed = game_elapsed_seconds(plan_started_observation, current)
            if game_elapsed is None:
                return "Plan game-time budget cannot be observed safely."
            if game_elapsed >= plan.max_game_seconds:
                return "Plan game-time budget is exhausted."
        return None

    def _abort(
        self,
        plan: PlanEnvelope,
        step: PlanStep,
        observation: Observation,
        actions_completed: int,
        reason: str,
        *,
        evidence: dict[str, object] | None = None,
        terminated: bool = False,
        success: bool | None = None,
        emit_step_cancelled: bool = True,
    ) -> PlanExecutionResult:
        if emit_step_cancelled:
            self._event(
                "plan_step_cancelled",
                plan,
                observation,
                step=step,
                reason=reason,
                evidence=evidence,
            )
        self._event(
            "plan_patch_requested",
            plan,
            observation,
            step=step,
            reason=reason,
            evidence=evidence,
        )
        self._event(
            "plan_aborted",
            plan,
            observation,
            step=step,
            reason=reason,
            evidence=evidence,
        )
        return PlanExecutionResult(
            observation=observation,
            actions_completed=actions_completed,
            completed=False,
            terminated=terminated,
            success=success,
            reason=reason,
        )

    @staticmethod
    def _require_pause_before_replan(
        result: PlanExecutionResult,
        observation: Observation,
        reason: str,
    ) -> PlanExecutionResult:
        telemetry = observation.telemetry
        if telemetry is None or telemetry.game.paused is not False:
            return result
        if "game.pause" in telemetry.capabilities:
            decision = PlannerDecision(
                intent="Recover option ownership before replanning.",
                rationale=(
                    "The active option ended without a terminal handoff while "
                    f"the world was still running: {reason}"
                ),
                action=PauseAction(paused=True),
                confidence=1.0,
            )
        else:
            decision = PlannerDecision(
                intent="Stop after losing safe option ownership.",
                rationale=(
                    "The active option ended while the world was running and "
                    "the pause capability is unavailable."
                ),
                action=StopAction(
                    reason="Cannot confirm a safe pause before replanning."
                ),
                confidence=1.0,
            )
        return PlanExecutionResult(
            observation=result.observation,
            actions_completed=result.actions_completed,
            completed=result.completed,
            terminated=result.terminated,
            success=result.success,
            reason=result.reason,
            reflex_decision=decision,
        )

    def _event(
        self,
        event_type: str,
        plan: PlanEnvelope,
        observation: Observation,
        *,
        reason: str,
        step: PlanStep | None = None,
        evidence: dict[str, object] | None = None,
    ) -> None:
        self.logger.write(
            event_type,
            step_index=observation.step_index,
            payload={
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
                "step_id": step.step_id if step is not None else None,
                "world_revision": observation.world_revision.model_dump(mode="json"),
                "control_mode": observation.control_mode.value,
                "reason": reason,
                "evidence": evidence or {},
            },
        )

    @staticmethod
    def _first_non_true(
        evaluations: list[ConditionEvaluation],
    ) -> ConditionEvaluation | None:
        return next(
            (evaluation for evaluation in evaluations if evaluation.result != ConditionResult.TRUE),
            None,
        )

    @staticmethod
    def _evaluations_json(
        evaluations: list[ConditionEvaluation],
    ) -> list[dict[str, object]]:
        return [evaluation.model_dump(mode="json") for evaluation in evaluations]
