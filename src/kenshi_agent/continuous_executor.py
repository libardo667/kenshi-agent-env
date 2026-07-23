from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from .env import AgentEnvironment
from .models import (
    ConditionEvaluation,
    ConditionResult,
    Observation,
    ObservationPolicy,
    PlanEnvelope,
    PlannerDecision,
    PlanStep,
    Transition,
    WorldStateRevision,
)
from .planning import (
    PlanBudgetError,
    PlanBudgetLedger,
    PlanningClock,
    evaluate_conditions,
    game_elapsed_seconds,
)
from .reflexes import ReflexEngine
from .safety import ActionGuard, SafetyViolation
from .session_log import SessionLogger
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
class _StepResult:
    observation: Observation
    succeeded: bool
    actions_completed: int
    reason: str
    terminated: bool = False
    success: bool | None = None


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
    ) -> None:
        self.environment = environment
        self.guard = guard
        self.reflexes = reflexes
        self.logger = logger
        self.clock = clock
        self.state_store = state_store
        self.observe_transition = observe_transition

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
                )
                observation = step_result.observation
                actions_completed += step_result.actions_completed

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
                    step_id = step.on_success
                    break

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

        action_start_revision = observation.world_revision
        command = self.state_store.begin_command(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            step_id=step.step_id,
            action_kind=action.kind,
            start_revision=action_start_revision,
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

        try:
            transition = await self.environment.step(action)
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
                stale_evidence = any(
                    evaluation.result == ConditionResult.STALE for evaluation in success_evaluations
                )
                reason = (
                    "Step timed out without a causally later world revision "
                    "satisfying postconditions."
                    if stale_evidence
                    else "Step timed out before its success conditions became true."
                )
                return _StepResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=reason,
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
                stale_evidence = any(
                    evaluation.result == ConditionResult.STALE for evaluation in success_evaluations
                )
                return _StepResult(
                    observation=latest,
                    succeeded=False,
                    actions_completed=1,
                    reason=(
                        "Step timed out without a causally later world revision "
                        "satisfying postconditions."
                        if stale_evidence
                        else "Step timed out before its success conditions became true."
                    ),
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
