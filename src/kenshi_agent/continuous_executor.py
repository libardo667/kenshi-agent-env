from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .affordances import terminal_affordance_receipt
from .condition_evaluation import evaluate_conditions
from .config import PlanningConfig
from .core.affordance import (
    AffordanceExecution,
    AffordanceLifecycleStatus,
)
from .core.observation import Observation
from .core.operation import (
    PauseAction,
    StopAction,
)
from .core.planning import (
    ConditionEvaluation,
    ConditionResult,
    PlanEnvelope,
    PlannerDecision,
    PlanStep,
)
from .operation_execution import OperationExecutionService
from .plan_events import PlanEventReporter
from .planning import (
    PlanBudgetLedger,
    PlanningClock,
    game_elapsed_seconds,
)
from .reflexes import ReflexEngine
from .session_log import SessionLogger
from .world_state import WorldStateStore


@dataclass(frozen=True, slots=True)
class PlanExecutionResult:
    observation: Observation
    actions_completed: int
    completed: bool
    terminated: bool
    success: bool | None
    reason: str
    reflex_decision: PlannerDecision | None = None
    # Which steps actually finished, so the plan outcome can say what was done
    # rather than only why the plan stopped.
    completed_step_ids: tuple[str, ...] = ()


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
        operations: OperationExecutionService,
        reflexes: ReflexEngine,
        logger: SessionLogger,
        clock: PlanningClock,
        state_store: WorldStateStore,
        planning_config: PlanningConfig,
        event: PlanEventReporter,
    ) -> None:
        self.operations = operations
        self.reflexes = reflexes
        self.logger = logger
        self.clock = clock
        self.state_store = state_store
        self.planning_config = planning_config
        self._event = event
        # Which steps of the plan currently in flight actually finished. One
        # executor owns one plan, so this is that plan's answer, and every
        # terminal result reports it rather than only why the plan stopped.
        self._completed_step_ids: tuple[str, ...] = ()
        self._closed_affordance_steps: set[tuple[str, int, str]] = set()

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
        self._completed_step_ids = ()

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
                    completed_step_ids=self._completed_step_ids,
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

            failure_conditions = evaluate_conditions(
                step.failure_conditions,
                observation,
            )
            active_failure = next(
                (
                    evaluation
                    for evaluation in failure_conditions
                    if evaluation.result is not ConditionResult.FALSE
                ),
                None,
            )
            if active_failure is not None:
                reason = (
                    "Step failure condition is already true before dispatch: "
                    f"{active_failure.reason}"
                    if active_failure.result is ConditionResult.TRUE
                    else (
                        "Step failure condition is not definitively false before "
                        f"dispatch: {active_failure.reason}"
                    )
                )
                return self._abort(
                    plan,
                    step,
                    observation,
                    actions_completed,
                    reason,
                    evidence={"failure_conditions": self._evaluations_json(failure_conditions)},
                )

            self._event(
                "plan_step_ready",
                plan,
                observation,
                step=step,
                reason=(
                    "All assumptions, capabilities, and preconditions are true; "
                    "all declared failure conditions are false."
                ),
                evidence={
                    "preconditions": self._evaluations_json(preconditions),
                    "failure_conditions": self._evaluations_json(failure_conditions),
                },
            )

            retries_remaining = step.retry_budget
            affordance_execution_started = False
            affordance_monitoring_started = False
            while True:
                step_result = await self.operations.submit(
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
                if step_result.actions_completed > 0:
                    affordance_execution_started = True
                    affordance_monitoring_started = bool(
                        step.affordance is not None
                        and step.affordance.execution is not AffordanceExecution.IMMEDIATE
                    )

                if step_result.interrupted:
                    self._close_affordance(
                        plan,
                        step,
                        observation,
                        status=AffordanceLifecycleStatus.INTERRUPTED,
                        reason=step_result.reason,
                        execution_started=affordance_execution_started,
                        monitoring_started=affordance_monitoring_started,
                    )
                    assert step_result.staged_patch is not None
                    self._event(
                        "plan_step_interrupted",
                        plan,
                        observation,
                        step=step,
                        reason=step_result.reason,
                        evidence={"patch": step_result.staged_patch.patch.model_dump(mode="json")},
                    )
                    budget_reason = self._budget_stop_reason(
                        plan,
                        plan_started_at,
                        plan_started_observation,
                        observation,
                        remaining_run_actions - actions_completed,
                    )
                    resolution = self.operations.activate_future_patch(
                        step_result.staged_patch,
                        active_plan=plan,
                        current_observation=observation,
                        budget=budget,
                        remaining_run_actions=(remaining_run_actions - actions_completed),
                        protected_step_ids=completed_step_ids | {step.step_id},
                        step_id=step.step_id,
                        budget_reason=budget_reason,
                        interrupted=True,
                    )
                    if resolution.rejection_reason is not None:
                        reason = resolution.rejection_reason
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
                    assert resolution.plan is not None
                    plan = resolution.plan
                    by_id = {item.step_id: item for item in plan.steps}
                    step_id = plan.entry_step_id
                    break

                if step_result.succeeded:
                    self._close_affordance(
                        plan,
                        step,
                        observation,
                        status=AffordanceLifecycleStatus.SUCCEEDED,
                        reason=step_result.reason,
                        execution_started=affordance_execution_started,
                        monitoring_started=affordance_monitoring_started,
                    )
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
                            completed_step_ids=tuple(sorted(completed_step_ids | {step.step_id})),
                        )
                    completed_step_ids.add(step.step_id)
                    self._completed_step_ids = tuple(sorted(completed_step_ids))
                    if step_result.staged_patch is not None:
                        budget_reason = self._budget_stop_reason(
                            plan,
                            plan_started_at,
                            plan_started_observation,
                            observation,
                            remaining_run_actions - actions_completed,
                        )
                        resolution = self.operations.activate_future_patch(
                            step_result.staged_patch,
                            active_plan=plan,
                            current_observation=observation,
                            budget=budget,
                            remaining_run_actions=(remaining_run_actions - actions_completed),
                            protected_step_ids=completed_step_ids,
                            step_id=step.step_id,
                            budget_reason=budget_reason,
                            interrupted=False,
                        )
                        if resolution.plan is not None:
                            plan = resolution.plan
                            by_id = {item.step_id: item for item in plan.steps}
                            step_id = plan.entry_step_id
                            break
                    step_id = step.on_success
                    break

                if step_result.pause_before_replan:
                    self._close_affordance(
                        plan,
                        step,
                        observation,
                        status=AffordanceLifecycleStatus.FAILED,
                        reason=step_result.reason,
                        execution_started=affordance_execution_started,
                        monitoring_started=affordance_monitoring_started,
                    )
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

                if (
                    retries_remaining > 0
                    and step_result.retry_authorized
                    and not step_result.terminated
                ):
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
                        self._close_affordance(
                            plan,
                            step,
                            observation,
                            status=AffordanceLifecycleStatus.FAILED,
                            reason=reason,
                            execution_started=affordance_execution_started,
                            monitoring_started=affordance_monitoring_started,
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

                self._close_affordance(
                    plan,
                    step,
                    observation,
                    status=AffordanceLifecycleStatus.FAILED,
                    reason=step_result.reason,
                    execution_started=affordance_execution_started,
                    monitoring_started=affordance_monitoring_started,
                )
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
            completed_step_ids=self._completed_step_ids,
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
        self._close_affordance(
            plan,
            step,
            observation,
            status=AffordanceLifecycleStatus.REJECTED,
            reason=reason,
        )
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
            completed_step_ids=self._completed_step_ids,
        )

    def _close_affordance(
        self,
        plan: PlanEnvelope,
        step: PlanStep,
        observation: Observation,
        *,
        status: AffordanceLifecycleStatus,
        reason: str,
        execution_started: bool = False,
        monitoring_started: bool = False,
    ) -> None:
        if step.affordance is None:
            return
        key = (plan.plan_id, plan.plan_version, step.step_id)
        if key in self._closed_affordance_steps:
            return
        telemetry_sequence = (
            observation.telemetry.sequence if observation.telemetry is not None else None
        )
        receipt = terminal_affordance_receipt(
            step.affordance,
            status=status,
            message=reason,
            telemetry_sequence=telemetry_sequence,
            execution_started=execution_started,
            monitoring_started=monitoring_started,
        )
        self._closed_affordance_steps.add(key)
        self.logger.write(
            "affordance_receipt",
            step_index=observation.step_index,
            payload={
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
                "step_id": step.step_id,
                "receipt": receipt.model_dump(mode="json"),
            },
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
                action=StopAction(reason="Cannot confirm a safe pause before replanning."),
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
            completed_step_ids=result.completed_step_ids,
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
