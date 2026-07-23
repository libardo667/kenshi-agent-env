from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .config import PlanningConfig
from .models import (
    Action,
    CharacterState,
    ClickAction,
    Condition,
    ConditionEvaluation,
    ConditionKind,
    ConditionOperator,
    ConditionResult,
    ControlMode,
    MoveCursorAction,
    NoopAction,
    Observation,
    PauseAction,
    PlanEnvelope,
    PlanPatch,
    RiskBudget,
    ScrollAction,
    SetSpeedAction,
    SkillAction,
    WaitAction,
    WorldStateRevision,
)
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


_PATH_CAPABILITY_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "telemetry.game.loaded": ("game.pause",),
    "telemetry.game.paused": ("game.pause",),
    "telemetry.game.speed_multiplier": ("game.speed",),
    "telemetry.game.elapsed_minutes": ("game.time",),
    "telemetry.game.money": ("game.money",),
    "telemetry.game.location_name": ("game.location",),
    "telemetry.game.day": ("game.time",),
    "telemetry.game.hour": ("game.time",),
    "telemetry.game.minute": ("game.time",),
    "telemetry.ui.active_screen": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.modal_open": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.dialogue_open": ("ui.dialogue",),
    "telemetry.ui.context_menu_open": ("ui.inventory", "ui.dialogue"),
    "telemetry.ui.selected_character_id": ("squad.basic",),
    "telemetry.ui.selected_character_count": ("identity.stable_handles",),
    "telemetry.native_control.available": ("control.approach_vendor",),
    "telemetry.native_control.last_command_sequence": ("control.approach_vendor",),
    "telemetry.native_control.last_command": ("control.approach_vendor",),
    "telemetry.native_control.last_result": ("control.approach_vendor",),
    "telemetry.native_control.last_target": ("control.approach_vendor",),
    "telemetry.native_control.last_target_id": ("control.approach_vendor",),
    "selected.alive": ("squad.basic",),
    "selected.conscious": ("squad.basic",),
    "selected.down": ("squad.basic",),
    "selected.in_combat": ("squad.basic",),
    "selected.position.x": ("squad.basic",),
    "selected.position.y": ("squad.basic",),
    "selected.position.z": ("squad.basic",),
    "selected.movement_speed": ("squad.basic",),
    "selected.hunger": ("squad.hunger",),
    "selected.bleeding_rate": ("squad.health",),
    "selected.food_items": ("squad.hunger", "squad.inventory", "squad.basic"),
    "selected.first_aid_kits": ("squad.inventory",),
    "selected.current_goal": ("squad.basic",),
    "target.disposition": ("nearby.characters", "nearby.visible_entities"),
    "target.distance": ("nearby.characters", "nearby.visible_entities"),
    "target.visible": ("nearby.characters", "nearby.visible_entities"),
    "target.conscious": ("nearby.characters", "nearby.visible_entities"),
    "target.has_vendor_list": ("nearby.roles",),
    "target.is_squad_leader": ("nearby.roles",),
    "target.has_dialogue": ("nearby.roles",),
    "target.shop_inventory_owner": ("nearby.shop_owners",),
    "target.talk_task_available": ("nearby.roles",),
}


def _selected_character(observation: Observation) -> CharacterState | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return None
    selected_id = telemetry.ui.selected_character_id
    if selected_id is not None:
        selected = next(
            (character for character in telemetry.squad if character.id == selected_id),
            None,
        )
        if selected is not None:
            return selected
    return next(
        (character for character in telemetry.squad if character.selected),
        telemetry.squad[0] if telemetry.squad else None,
    )


def _resolve_field(condition: Condition, observation: Observation) -> object | None:
    telemetry = observation.telemetry
    path = condition.path
    if path == "control_mode":
        return observation.control_mode.value
    if path == "telemetry_stale":
        return observation.telemetry_stale
    if telemetry is None or path is None:
        return None

    direct_paths: dict[str, object | None] = {
        "telemetry.game.loaded": telemetry.game.loaded,
        "telemetry.game.paused": telemetry.game.paused,
        "telemetry.game.speed_multiplier": telemetry.game.speed_multiplier,
        "telemetry.game.elapsed_minutes": telemetry.game.elapsed_minutes,
        "telemetry.game.money": telemetry.game.money,
        "telemetry.game.location_name": telemetry.game.location_name,
        "telemetry.game.day": telemetry.game.day,
        "telemetry.game.hour": telemetry.game.hour,
        "telemetry.game.minute": telemetry.game.minute,
        "telemetry.ui.active_screen": telemetry.ui.active_screen,
        "telemetry.ui.modal_open": telemetry.ui.modal_open,
        "telemetry.ui.dialogue_open": telemetry.ui.dialogue_open,
        "telemetry.ui.context_menu_open": telemetry.ui.context_menu_open,
        "telemetry.ui.selected_character_id": telemetry.ui.selected_character_id,
        "telemetry.ui.selected_character_count": len(telemetry.ui.selected_character_ids),
        "telemetry.native_control.available": telemetry.native_control.available,
        "telemetry.native_control.last_command_sequence": (
            telemetry.native_control.last_command_sequence
        ),
        "telemetry.native_control.last_command": telemetry.native_control.last_command,
        "telemetry.native_control.last_result": telemetry.native_control.last_result,
        "telemetry.native_control.last_target": telemetry.native_control.last_target,
        "telemetry.native_control.last_target_id": telemetry.native_control.last_target_id,
    }
    if path in direct_paths:
        return direct_paths[path]

    if path.startswith("selected."):
        selected = _selected_character(observation)
        if selected is None:
            return None
        selected_paths = {
            "selected.alive": selected.alive,
            "selected.conscious": selected.conscious,
            "selected.down": selected.down,
            "selected.in_combat": selected.in_combat,
            "selected.movement_speed": selected.movement_speed,
            "selected.hunger": selected.hunger,
            "selected.bleeding_rate": selected.bleeding_rate,
            "selected.food_items": selected.food_items,
            "selected.first_aid_kits": selected.first_aid_kits,
            "selected.current_goal": selected.current_goal,
            "selected.position.x": (selected.position.x if selected.position is not None else None),
            "selected.position.y": (selected.position.y if selected.position is not None else None),
            "selected.position.z": (selected.position.z if selected.position is not None else None),
        }
        return selected_paths[path]

    if path.startswith("target."):
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == condition.target_id),
            None,
        )
        if target is None:
            return None
        target_paths = {
            "target.disposition": target.disposition.value,
            "target.distance": target.distance,
            "target.visible": target.visible,
            "target.conscious": target.conscious,
            "target.has_vendor_list": target.has_vendor_list,
            "target.is_squad_leader": target.is_squad_leader,
            "target.has_dialogue": target.has_dialogue,
            "target.shop_inventory_owner": target.shop_inventory_owner,
            "target.talk_task_available": target.talk_task_available,
        }
        return target_paths[path]
    return None


def _is_telemetry_condition(condition: Condition) -> bool:
    return bool(
        condition.kind in {ConditionKind.CAPABILITY, ConditionKind.TELEMETRY_FRESH}
        or condition.required_capabilities
        or (
            condition.path is not None
            and (
                condition.path.startswith("telemetry.")
                or condition.path.startswith("selected.")
                or condition.path.startswith("target.")
            )
        )
    )


def _evaluation(
    condition: Condition,
    result: ConditionResult,
    reason: str,
    *,
    actual: object | None = None,
) -> ConditionEvaluation:
    scalar = actual if isinstance(actual, (str, int, float, bool)) else None
    return ConditionEvaluation(
        condition=condition,
        result=result,
        actual=scalar,
        reason=reason,
    )


def evaluate_condition(
    condition: Condition,
    observation: Observation,
    *,
    after_revision: WorldStateRevision | None = None,
) -> ConditionEvaluation:
    telemetry_condition = _is_telemetry_condition(condition)
    if after_revision is not None:
        if telemetry_condition:
            current_sequence = observation.world_revision.telemetry_sequence
            prior_sequence = after_revision.telemetry_sequence
            if (
                current_sequence is None
                or prior_sequence is None
                or current_sequence <= prior_sequence
            ):
                return _evaluation(
                    condition,
                    ConditionResult.STALE,
                    "No later telemetry revision exists after the action start.",
                )
        elif not observation.world_revision.is_later_than(after_revision):
            return _evaluation(
                condition,
                ConditionResult.STALE,
                "No later world revision exists after the action start.",
            )

    if telemetry_condition:
        if observation.telemetry is None:
            return _evaluation(
                condition,
                ConditionResult.UNAVAILABLE,
                "Telemetry is unavailable.",
            )
        if observation.telemetry_stale:
            return _evaluation(
                condition,
                ConditionResult.STALE,
                "Telemetry is marked stale.",
            )
        age = observation.telemetry_age_seconds
        if age is not None and age > condition.max_age_seconds:
            return _evaluation(
                condition,
                ConditionResult.STALE,
                f"Telemetry age {age:.3f}s exceeds {condition.max_age_seconds:.3f}s.",
            )
        missing = sorted(
            set(condition.required_capabilities) - set(observation.telemetry.capabilities)
        )
        if missing:
            return _evaluation(
                condition,
                ConditionResult.UNAVAILABLE,
                f"Required capabilities are unavailable: {missing}.",
            )
        alternatives = (
            _PATH_CAPABILITY_ALTERNATIVES.get(condition.path)
            if condition.path is not None
            else None
        )
        if alternatives is not None and not any(
            capability in observation.telemetry.capabilities for capability in alternatives
        ):
            return _evaluation(
                condition,
                ConditionResult.UNAVAILABLE,
                "The field's authoritative capability is unavailable; expected "
                f"one of {list(alternatives)}.",
            )

    if condition.kind == ConditionKind.TELEMETRY_FRESH:
        if observation.telemetry_age_seconds is None:
            return _evaluation(
                condition,
                ConditionResult.UNKNOWN,
                "Telemetry age is unknown.",
            )
        actual: object = not observation.telemetry_stale
    elif condition.kind == ConditionKind.CAPABILITY:
        if observation.telemetry is None:
            return _evaluation(
                condition,
                ConditionResult.UNAVAILABLE,
                "Telemetry capabilities are unavailable.",
            )
        actual = condition.path in observation.telemetry.capabilities
    else:
        actual = _resolve_field(condition, observation)

    if actual is None:
        return _evaluation(
            condition,
            ConditionResult.UNKNOWN,
            f"Condition value for {condition.path or condition.kind.value!r} is unknown.",
        )

    if condition.operator == ConditionOperator.EXISTS:
        result = ConditionResult.TRUE
    elif condition.operator == ConditionOperator.EQUALS:
        result = ConditionResult.TRUE if actual == condition.expected else ConditionResult.FALSE
    elif condition.operator == ConditionOperator.NOT_EQUALS:
        result = ConditionResult.TRUE if actual != condition.expected else ConditionResult.FALSE
    elif (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(condition.expected, (int, float))
        and not isinstance(condition.expected, bool)
    ):
        if condition.operator == ConditionOperator.LESS_THAN:
            passed = actual < condition.expected
        elif condition.operator == ConditionOperator.LESS_THAN_OR_EQUAL:
            passed = actual <= condition.expected
        elif condition.operator == ConditionOperator.GREATER_THAN:
            passed = actual > condition.expected
        elif condition.operator == ConditionOperator.GREATER_THAN_OR_EQUAL:
            passed = actual >= condition.expected
        else:
            return _evaluation(
                condition,
                ConditionResult.UNKNOWN,
                "The operator is not defined for this value.",
                actual=actual,
            )
        result = ConditionResult.TRUE if passed else ConditionResult.FALSE
    else:
        return _evaluation(
            condition,
            ConditionResult.UNKNOWN,
            "Ordered comparison requires observed numeric values.",
            actual=actual,
        )
    return _evaluation(
        condition,
        result,
        f"Observed {actual!r}; expected {condition.operator.value} {condition.expected!r}.",
        actual=actual,
    )


def evaluate_conditions(
    conditions: list[Condition],
    observation: Observation,
    *,
    after_revision: WorldStateRevision | None = None,
) -> list[ConditionEvaluation]:
    return [
        evaluate_condition(
            condition,
            observation,
            after_revision=after_revision,
        )
        for condition in conditions
    ]


def _action_risk(
    action: Action,
    macros: MacroRegistry,
) -> tuple[int, int, int]:
    actions = [action]
    native = 0
    if isinstance(action, SkillAction):
        try:
            native = int(macros.requires_native_assisted(action.name))
            actions = macros.expand(action)
        except UnknownSkillError:
            actions = [action]
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
    if plan.control_mode != observation.control_mode:
        errors.append(
            f"control mode {plan.control_mode.value!r} does not match "
            f"{observation.control_mode.value!r}"
        )
    if not plan.based_on_revision.same_snapshot_as(observation.world_revision):
        errors.append("plan basis is stale relative to the current world revision")
    if (
        observation.world_revision.telemetry_sequence is None
        and observation.world_revision.frame_sequence is None
    ):
        errors.append("current observation has no causal revision channel")
    if (
        observation.telemetry is None
        or observation.telemetry.game.elapsed_minutes is None
        or "game.time" not in observation.telemetry.capabilities
    ):
        errors.append("plan game-time budget cannot be enforced from this observation")
    if len(plan.steps) > config.max_plan_steps:
        errors.append(f"plan has {len(plan.steps)} steps; maximum is {config.max_plan_steps}")
    if plan.max_actions > config.max_actions_per_plan:
        errors.append(f"plan max_actions {plan.max_actions} exceeds {config.max_actions_per_plan}")
    if plan.max_wall_seconds > config.max_plan_wall_seconds:
        errors.append(
            f"plan max_wall_seconds {plan.max_wall_seconds} exceeds {config.max_plan_wall_seconds}"
        )
    if plan.max_game_seconds > config.max_plan_game_seconds:
        errors.append(
            f"plan max_game_seconds {plan.max_game_seconds} exceeds {config.max_plan_game_seconds}"
        )
    if plan.risk_budget.max_pointer_actions > config.max_pointer_actions_per_plan:
        errors.append("plan pointer risk budget exceeds configured maximum")
    if plan.risk_budget.max_purchase_actions > config.max_purchase_actions_per_plan:
        errors.append("plan purchase risk budget exceeds configured maximum")
    if plan.risk_budget.max_native_assisted_actions > config.max_native_assisted_actions_per_plan:
        errors.append("plan native-assisted risk budget exceeds configured maximum")
    if (
        plan.control_mode == ControlMode.INTERFACE_ONLY
        and plan.risk_budget.max_native_assisted_actions != 0
    ):
        errors.append("interface_only plans must have zero native-assisted risk budget")

    pointer_risk = 0
    purchase_risk = 0
    native_risk = 0
    for step in plan.steps:
        pointer, purchase, native = _action_risk(step.action, macros)
        pointer_risk += pointer * (1 + step.retry_budget)
        purchase_risk += purchase * (1 + step.retry_budget)
        native_risk += native * (1 + step.retry_budget)
        if step.retry_budget and not isinstance(
            step.action,
            (NoopAction, WaitAction, PauseAction, SetSpeedAction),
        ):
            errors.append(f"step {step.step_id!r} retries an action not proven idempotent")
    if pointer_risk > plan.risk_budget.max_pointer_actions:
        errors.append("plan actions exceed the declared pointer risk budget")
    if purchase_risk > plan.risk_budget.max_purchase_actions:
        errors.append("plan actions exceed the declared purchase risk budget")
    if native_risk > plan.risk_budget.max_native_assisted_actions:
        errors.append("plan actions exceed the declared native-assisted risk budget")

    assumption_results = evaluate_conditions(plan.assumptions, observation)
    blocked_assumptions = [
        evaluation for evaluation in assumption_results if evaluation.result != ConditionResult.TRUE
    ]
    if blocked_assumptions:
        errors.append(
            "plan assumptions are not all true: "
            + "; ".join(
                f"{evaluation.result.value}: {evaluation.reason}"
                for evaluation in blocked_assumptions
            )
        )
    if errors:
        raise PlanValidationError("; ".join(errors))
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
    require_current_basis: bool,
) -> PlanEnvelope:
    errors: list[str] = []
    if patch.plan_id != active_plan.plan_id:
        errors.append(
            f"patch plan_id {patch.plan_id!r} does not match {active_plan.plan_id!r}"
        )
    if patch.based_on_plan_version != active_plan.plan_version:
        errors.append(
            f"patch version {patch.based_on_plan_version} does not match active "
            f"version {active_plan.plan_version}"
        )
    if not patch.based_on_revision.same_snapshot_as(planner_observation.world_revision):
        errors.append("patch basis does not match its immutable planner snapshot")
    if require_current_basis and not patch.based_on_revision.same_snapshot_as(
        current_observation.world_revision
    ):
        errors.append("patch became stale while the concurrent planner was running")
    replacement_ids = {step.step_id for step in patch.replace_future_steps}
    conflicts = sorted(replacement_ids & protected_step_ids)
    if conflicts:
        errors.append(f"patch attempts to replace active or completed steps: {conflicts}")
    remaining_actions = min(budget.remaining_actions, remaining_run_actions)
    if remaining_actions <= 0:
        errors.append("no action budget remains for replacement steps")
    if errors:
        raise PlanValidationError("; ".join(errors))

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
                max_native_assisted_actions=(
                    budget.remaining_native_assisted_actions
                ),
            ),
        )
    except ValueError as exc:
        raise PlanValidationError(f"replacement graph is invalid: {exc}") from exc
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
