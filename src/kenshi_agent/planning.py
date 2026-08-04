from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .condition_evaluation import evaluate_conditions
from .config import PlanningConfig
from .core.observation import Observation
from .core.operation import (
    Action,
    ClickAction,
    ControlMode,
    InterruptPolicy,
    MoveCursorAction,
    PauseAction,
    ScrollAction,
    SkillAction,
)
from .core.planning import (
    ConditionEvaluation,
    ConditionKind,
    ConditionOperator,
    ConditionResult,
    PlanEnvelope,
    PlanPatch,
    RiskBudget,
)
from .live_plan_policy import live_plan_policy_errors
from .operation_definitions import definition_for
from .skills import MacroRegistry, UnknownSkillError


class PlanValidationError(ValueError):
    pass


class PlanBudgetError(RuntimeError):
    pass


class PlanningClock(ABC):
    @abstractmethod
    def monotonic(self) -> float:
        raise NotImplementedError

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        raise NotImplementedError


class SystemPlanningClock(PlanningClock):
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _action_risk(
    action: Action,
    macros: MacroRegistry,
) -> tuple[int, int, int]:
    # A contracted action declares its own cost, so risk accounting no longer
    # depends on expanding a macro or recognizing an exact skill name.
    actions = [action]
    native = 0
    if isinstance(action, SkillAction):
        try:
            native = int(macros.requires_native_assisted(action.name))
            actions = macros.expand(action)
        except UnknownSkillError:
            actions = [action]
    else:
        contract = definition_for(action)
        if contract is not None:
            return contract.risk_for(action).as_tuple()
    pointer = sum(
        isinstance(item, (ClickAction, MoveCursorAction, ScrollAction)) for item in actions
    )
    purchase = int(isinstance(action, SkillAction) and action.name == "buy_inspected_shop_item")
    return pointer, purchase, native


def validate_plan(
    plan: PlanEnvelope,
    observation: Observation,
    config: PlanningConfig,
    macros: MacroRegistry,
) -> list[ConditionEvaluation]:
    errors: list[str] = []
    if observation.mode == "live":
        errors.extend(
            live_plan_policy_errors(
                plan,
                max_steps=config.max_plan_steps,
            )
        )
    if plan.control_mode != observation.control_mode:
        errors.append(
            f"control mode {plan.control_mode.value!r} does not match "  # mutation: diagnostic-only
            f"{observation.control_mode.value!r}"
        )
    if not plan.based_on_revision.same_snapshot_as(observation.world_revision):
        errors.append(
            "plan basis is stale relative to the "  # mutation: diagnostic-only
            "current world revision"
        )
    if (
        observation.world_revision.telemetry_sequence is None
        and observation.world_revision.frame_sequence is None
    ):
        errors.append(
            "current observation has no causal revision channel"  # mutation: diagnostic-only
        )
    if (
        observation.telemetry is None
        or observation.telemetry.game.elapsed_minutes is None
        or "game.time" not in observation.telemetry.capabilities
    ):
        errors.append(
            "plan game-time budget cannot be enforced "  # mutation: diagnostic-only
            "from this observation"
        )
    if len(plan.steps) > config.max_plan_steps:
        errors.append(
            f"plan has {len(plan.steps)} steps; "  # mutation: diagnostic-only
            f"maximum is {config.max_plan_steps}"
        )
    if plan.max_actions > config.max_actions_per_plan:
        errors.append(
            f"plan max_actions {plan.max_actions} exceeds "  # mutation: diagnostic-only
            f"{config.max_actions_per_plan}"
        )
    if plan.max_wall_seconds > config.max_plan_wall_seconds:
        errors.append(
            f"plan max_wall_seconds {plan.max_wall_seconds} "  # mutation: diagnostic-only
            f"exceeds {config.max_plan_wall_seconds}"
        )
    if plan.max_game_seconds > config.max_plan_game_seconds:
        errors.append(
            f"plan max_game_seconds {plan.max_game_seconds} "  # mutation: diagnostic-only
            f"exceeds {config.max_plan_game_seconds}"
        )
    if plan.risk_budget.max_pointer_actions > config.max_pointer_actions_per_plan:
        errors.append(
            f"plan declares max_pointer_actions "  # mutation: diagnostic-only
            f"{plan.risk_budget.max_pointer_actions}; declare at most "
            f"{config.max_pointer_actions_per_plan}"
        )
    if plan.risk_budget.max_purchase_actions > config.max_purchase_actions_per_plan:
        errors.append(
            f"plan declares max_purchase_actions "  # mutation: diagnostic-only
            f"{plan.risk_budget.max_purchase_actions}; declare at most "
            f"{config.max_purchase_actions_per_plan}"
        )
    if plan.risk_budget.max_native_assisted_actions > config.max_native_assisted_actions_per_plan:
        errors.append(
            f"plan declares max_native_assisted_actions "  # mutation: diagnostic-only
            f"{plan.risk_budget.max_native_assisted_actions}; declare at most "
            f"{config.max_native_assisted_actions_per_plan}"
        )
    if (
        plan.control_mode == ControlMode.INTERFACE_ONLY
        and plan.risk_budget.max_native_assisted_actions != 0
    ):
        errors.append(
            "interface_only plans declare "  # mutation: diagnostic-only
            "max_native_assisted_actions 0, not "
            f"{plan.risk_budget.max_native_assisted_actions}"
        )

    pointer_risk = 0
    purchase_risk = 0
    native_risk = 0
    for step in plan.steps:
        pointer, purchase, native = _action_risk(step.action, macros)
        attempts = 1 + step.retry_budget
        pointer_risk += pointer * attempts
        purchase_risk += purchase * attempts
        native_risk += native * attempts
    if pointer_risk > plan.risk_budget.max_pointer_actions:
        errors.append(
            f"plan steps cost {pointer_risk} pointer actions but "  # mutation: diagnostic-only
            f"max_pointer_actions is {plan.risk_budget.max_pointer_actions}"
        )
    if purchase_risk > plan.risk_budget.max_purchase_actions:
        errors.append(
            f"plan steps cost {purchase_risk} purchase actions but "  # mutation: diagnostic-only
            f"max_purchase_actions is {plan.risk_budget.max_purchase_actions}"
        )
    if native_risk > plan.risk_budget.max_native_assisted_actions:
        errors.append(
            f"plan steps cost {native_risk} native-assisted actions "  # mutation: diagnostic-only
            f"but max_native_assisted_actions is "
            f"{plan.risk_budget.max_native_assisted_actions}"
        )

    assumption_results = evaluate_conditions(plan.assumptions, observation)
    blocked_assumptions = [
        evaluation for evaluation in assumption_results if evaluation.result != ConditionResult.TRUE
    ]
    if blocked_assumptions:
        errors.append(
            "plan assumptions are not all true: "  # mutation: diagnostic-only
            + "; ".join(
                f"{evaluation.result.value}: {evaluation.reason}"
                for evaluation in blocked_assumptions
            )
        )
    if errors:
        raise PlanValidationError(
            "; ".join(errors)  # mutation: diagnostic-only
        )
    return assumption_results


def validate_future_plan_patch(
    patch: PlanPatch,
    *,
    active_plan: PlanEnvelope,
    planner_observation: Observation,
    current_observation: Observation,
    config: PlanningConfig,
    macros: MacroRegistry,
    budget: PlanBudgetLedger,
    remaining_run_actions: int,
    protected_step_ids: set[str],
) -> PlanEnvelope:
    errors: list[str] = []
    if patch.plan_id != active_plan.plan_id:
        errors.append(
            f"patch plan_id {patch.plan_id!r} does not "  # mutation: diagnostic-only
            f"match {active_plan.plan_id!r}"
        )
    if patch.based_on_plan_version != active_plan.plan_version:
        errors.append(
            f"patch version {patch.based_on_plan_version} "  # mutation: diagnostic-only
            "does not match active "
            f"version {active_plan.plan_version}"
        )
    if not patch.based_on_revision.same_snapshot_as(planner_observation.world_revision):
        errors.append(
            "patch basis does not match its immutable "  # mutation: diagnostic-only
            "planner snapshot"
        )
    if patch.interrupt_active_step_id is not None:
        active_context = planner_observation.active_plan
        if (
            active_context is None
            or patch.interrupt_active_step_id != active_context.active_step_id
        ):
            errors.append(
                "interrupt patch does not name the exact "  # mutation: diagnostic-only
                "active step from its "
                "immutable planner observation"
            )
        active_step = next(
            (step for step in active_plan.steps if step.step_id == patch.interrupt_active_step_id),
            None,
        )
        if (
            active_step is None
            or active_step.interrupt_policy is not InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH
            or active_context is None
            or active_context.active_step_interrupt_policy
            is not InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH
        ):
            errors.append(
                "the exact active step does not permit "  # mutation: diagnostic-only
                "planner interruption"
            )
        pause_step = patch.replace_future_steps[0]
        has_paused_terminal = any(
            condition.kind is ConditionKind.FIELD
            and condition.path == "telemetry.game.paused"
            and condition.operator is ConditionOperator.EQUALS
            and condition.expected is True
            for condition in pause_step.success_conditions
        )
        has_command_terminal = any(
            condition.kind is ConditionKind.FIELD
            and condition.path == "telemetry.native_control.command_active"
            and condition.operator is ConditionOperator.EQUALS
            and condition.expected is False
            for condition in pause_step.success_conditions
        )
        if (
            not isinstance(pause_step.action, PauseAction)
            or pause_step.action.paused is not True
            or not has_paused_terminal
            or not has_command_terminal
        ):
            errors.append(
                "interrupt replacement must begin with a "  # mutation: diagnostic-only
                "confirmed pause handoff "
                "that proves the world paused and the native command ended"
            )
    replacement_ids = {step.step_id for step in patch.replace_future_steps}
    conflicts = sorted(replacement_ids & protected_step_ids)
    if conflicts:
        errors.append(
            "patch attempts to replace active or "  # mutation: diagnostic-only
            f"completed steps: {conflicts}"
        )
    remaining_actions = min(budget.remaining_actions, remaining_run_actions)
    if errors:
        raise PlanValidationError(
            "; ".join(errors)  # mutation: diagnostic-only
        )

    try:
        candidate = PlanEnvelope(
            schema_version=active_plan.schema_version,
            plan_id=active_plan.plan_id,
            plan_version=active_plan.plan_version + 1,
            objective=active_plan.objective,
            control_mode=active_plan.control_mode,
            based_on_revision=current_observation.world_revision,
            assumptions=active_plan.assumptions,
            steps=patch.replace_future_steps,
            entry_step_id=patch.replace_future_steps[0].step_id,
            max_actions=remaining_actions,
            max_wall_seconds=active_plan.max_wall_seconds,
            max_game_seconds=active_plan.max_game_seconds,
            risk_budget=RiskBudget(
                max_pointer_actions=budget.remaining_pointer_actions,
                max_purchase_actions=budget.remaining_purchase_actions,
                max_native_assisted_actions=(budget.remaining_native_assisted_actions),
            ),
        )
    except ValueError as exc:
        raise PlanValidationError(
            f"replacement graph is invalid: {exc}"  # mutation: diagnostic-only
        ) from exc
    validate_plan(candidate, current_observation, config, macros)
    return candidate


@dataclass(slots=True)
class PlanBudgetLedger:
    remaining_actions: int
    remaining_pointer_actions: int
    remaining_purchase_actions: int
    remaining_native_assisted_actions: int
    reserved_actions: int = 0
    committed_actions: int = 0
    released_actions: int = 0

    @classmethod
    def from_plan(cls, plan: PlanEnvelope) -> PlanBudgetLedger:
        return cls(
            remaining_actions=plan.max_actions,
            remaining_pointer_actions=plan.risk_budget.max_pointer_actions,
            remaining_purchase_actions=plan.risk_budget.max_purchase_actions,
            remaining_native_assisted_actions=(plan.risk_budget.max_native_assisted_actions),
        )

    def reserve(self, action: Action, macros: MacroRegistry) -> tuple[int, int, int]:
        pointer, purchase, native = _action_risk(action, macros)
        if self.remaining_actions < 1:
            raise PlanBudgetError("Plan action budget is exhausted.")
        if pointer > self.remaining_pointer_actions:
            raise PlanBudgetError("Plan pointer-action budget is exhausted.")
        if purchase > self.remaining_purchase_actions:
            raise PlanBudgetError("Plan purchase budget is exhausted.")
        if native > self.remaining_native_assisted_actions:
            raise PlanBudgetError("Plan native-assisted budget is exhausted.")
        self.remaining_actions -= 1
        self.remaining_pointer_actions -= pointer
        self.remaining_purchase_actions -= purchase
        self.remaining_native_assisted_actions -= native
        self.reserved_actions += 1
        return pointer, purchase, native

    def commit(self) -> None:
        if self.reserved_actions <= self.committed_actions + self.released_actions:
            raise PlanBudgetError("No action reservation is available to commit.")
        self.committed_actions += 1

    def release(self, risk: tuple[int, int, int]) -> None:
        if self.reserved_actions <= self.committed_actions + self.released_actions:
            raise PlanBudgetError("No action reservation is available to release.")
        pointer, purchase, native = risk
        self.remaining_actions += 1
        self.remaining_pointer_actions += pointer
        self.remaining_purchase_actions += purchase
        self.remaining_native_assisted_actions += native
        self.released_actions += 1


def game_elapsed_seconds(
    start: Observation,
    current: Observation,
) -> float | None:
    if start.telemetry is None or current.telemetry is None:
        return None
    before = start.telemetry.game.elapsed_minutes
    after = current.telemetry.game.elapsed_minutes
    if before is None or after is None:
        return None
    return max(0.0, (after - before) * 60.0)
