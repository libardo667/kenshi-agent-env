from __future__ import annotations

import re
from collections.abc import Iterable

from .models import (
    Condition,
    ConditionKind,
    ConditionOperator,
    ControlMode,
    IdempotencyPolicy,
    Observation,
    PlanEnvelope,
    PlanStep,
    SkillAction,
)

FOOD_PROCUREMENT_CAPABILITIES = frozenset(
    {
        "control.approach_vendor",
        "game.money",
        "game.pause",
        "game.time",
        "identity.stable_handles",
        "nearby.characters",
        "nearby.roles",
        "nearby.shop_owners",
        "squad.basic",
        "squad.hunger",
        "ui.dialogue",
        "ui.dialogue.options",
        "ui.dialogue.target",
        "ui.inventory",
        "ui.tooltip",
    }
)
SHOW_GOODS_TEXT = "Show me your goods."


def _condition_matches(
    condition: Condition,
    *,
    path: str,
    expected: str | int | float | bool,
    operator: ConditionOperator = ConditionOperator.EQUALS,
    target_id: str | None = None,
) -> bool:
    return bool(
        condition.kind == ConditionKind.FIELD
        and condition.path == path
        and condition.operator == operator
        and condition.expected == expected
        and condition.target_id == target_id
    )


def _requires(
    errors: list[str],
    conditions: Iterable[Condition],
    *,
    label: str,
    path: str,
    expected: str | int | float | bool,
    operator: ConditionOperator = ConditionOperator.EQUALS,
    target_id: str | None = None,
) -> None:
    if not any(
        _condition_matches(
            condition,
            path=path,
            expected=expected,
            operator=operator,
            target_id=target_id,
        )
        for condition in conditions
    ):
        errors.append(
            f"{label} must require {path} {operator.value} {expected!r}"
            + (f" for exact target {target_id!r}" if target_id is not None else "")
        )


def _selected_food_items(observation: Observation) -> int | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return None
    selected_id = telemetry.ui.selected_character_id
    selected = next(
        (
            character
            for character in telemetry.squad
            if selected_id is not None and character.id == selected_id
        ),
        next(
            (character for character in telemetry.squad if character.selected),
            telemetry.squad[0] if telemetry.squad else None,
        ),
    )
    return selected.food_items if selected is not None else None


def _skill(step: PlanStep, errors: list[str]) -> SkillAction | None:
    if not isinstance(step.action, SkillAction):
        errors.append(f"step {step.step_id!r} must use a named food-procurement skill")
        return None
    if (
        step.retry_budget != 0
        or step.idempotency != IdempotencyPolicy.AT_MOST_ONCE
    ):
        errors.append(
            f"step {step.step_id!r} must be an at-most-once action with zero retries"
        )
    return step.action


def _target_ids(actions: list[SkillAction]) -> list[str]:
    return [
        target
        for action in actions
        if isinstance((target := action.argument_map().get("target_id")), str)
        and target
    ]


def food_procurement_policy_errors(
    plan: PlanEnvelope,
    observation: Observation,
) -> list[str]:
    """Validate the one deliberately narrow continuous-live action grammar."""

    errors: list[str] = []
    telemetry = observation.telemetry
    if observation.mode != "live":
        return errors
    if plan.control_mode != ControlMode.NATIVE_ASSISTED:
        errors.append("food procurement requires native_assisted control mode")
    if telemetry is None:
        return ["food procurement requires authoritative telemetry"]

    fresh_assumptions = [
        condition
        for condition in plan.assumptions
        if condition.kind == ConditionKind.TELEMETRY_FRESH
    ]
    declared_capabilities = set().union(
        *(set(condition.required_capabilities) for condition in fresh_assumptions)
    )
    missing_declared = FOOD_PROCUREMENT_CAPABILITIES - declared_capabilities
    if missing_declared:
        errors.append(
            "food procurement plan must declare all authoritative capabilities: "
            + ", ".join(sorted(missing_declared))
        )

    actions = [action for step in plan.steps if (action := _skill(step, errors))]
    if len(actions) != len(plan.steps):
        return errors
    names = [action.name for action in actions]
    tooltip_ready = bool(
        telemetry.ui.tooltip_visible is True
        and telemetry.ui.tooltip_text
        and telemetry.ui.tooltip_source_bounds is not None
    )
    if telemetry.ui.active_screen == "world":
        expected_names = [
            "approach_confirmed_vendor",
            "choose_show_goods",
            "inspect_shop_item",
        ]
    elif telemetry.ui.active_screen == "dialogue":
        expected_names = ["choose_show_goods", "inspect_shop_item"]
    elif telemetry.ui.active_screen == "trade" and tooltip_ready:
        expected_names = ["buy_inspected_shop_item"]
    elif telemetry.ui.active_screen == "trade":
        expected_names = ["inspect_shop_item"]
    else:
        expected_names = []
    if names != expected_names:
        errors.append(
            "food procurement actions do not match the current authoritative phase; "
            f"expected {expected_names}, observed {names}"
        )

    target_ids = _target_ids(actions)
    if len(target_ids) != len(actions) or len(set(target_ids)) != 1:
        errors.append("all food procurement actions must bind to the same exact target")
        return errors
    target_id = target_ids[0]
    target = next(
        (entity for entity in telemetry.nearby_entities if entity.id == target_id),
        None,
    )
    if target is None:
        errors.append("food procurement exact target is absent from current telemetry")
        return errors
    if (
        target.is_animal is not False
        or target.has_vendor_list is not True
        or target.is_squad_leader is not True
        or target.has_dialogue is not True
        or target.conscious is not True
        or target.disposition.value not in {"friendly", "neutral"}
    ):
        errors.append("food procurement exact target lacks the required safe vendor roles")

    for step, action in zip(plan.steps, actions, strict=True):
        _requires(
            errors,
            step.preconditions,
            label=step.step_id,
            path="telemetry.game.paused",
            expected=True,
        )
        _requires(
            errors,
            step.preconditions,
            label=step.step_id,
            path="telemetry.ui.selected_character_count",
            expected=1,
        )
        _requires(
            errors,
            step.success_conditions,
            label=step.step_id,
            path="telemetry.game.paused",
            expected=True,
        )

        if action.name == "approach_confirmed_vendor":
            _requires(
                errors,
                step.preconditions,
                label=step.step_id,
                path="telemetry.ui.active_screen",
                expected="world",
            )
            _requires(
                errors,
                step.preconditions,
                label=step.step_id,
                path="target.shop_inventory_owner",
                expected=False,
                target_id=target_id,
            )
            _requires(
                errors,
                step.success_conditions,
                label=step.step_id,
                path="telemetry.ui.dialogue_target_id",
                expected=target_id,
            )
        elif action.name == "choose_show_goods":
            _requires(
                errors,
                step.preconditions,
                label=step.step_id,
                path="telemetry.ui.dialogue_target_id",
                expected=target_id,
            )
            _requires(
                errors,
                step.preconditions,
                label=step.step_id,
                path="telemetry.ui.dialogue_option_0",
                expected=SHOW_GOODS_TEXT,
            )
            _requires(
                errors,
                step.success_conditions,
                label=step.step_id,
                path="telemetry.ui.active_screen",
                expected="trade",
            )
            _requires(
                errors,
                step.success_conditions,
                label=step.step_id,
                path="telemetry.active_shop_trader_count",
                expected=1,
            )
            _requires(
                errors,
                step.success_conditions,
                label=step.step_id,
                path="target.shop_inventory_owner",
                expected=True,
                target_id=target_id,
            )
        elif action.name == "inspect_shop_item":
            _requires(
                errors,
                step.preconditions,
                label=step.step_id,
                path="telemetry.ui.active_screen",
                expected="trade",
            )
            _requires(
                errors,
                step.preconditions,
                label=step.step_id,
                path="telemetry.active_shop_trader_count",
                expected=1,
            )
            _requires(
                errors,
                step.preconditions,
                label=step.step_id,
                path="target.shop_inventory_owner",
                expected=True,
                target_id=target_id,
            )
            _requires(
                errors,
                step.success_conditions,
                label=step.step_id,
                path="telemetry.ui.tooltip_visible",
                expected=True,
            )
            _requires(
                errors,
                step.success_conditions,
                label=step.step_id,
                path="telemetry.active_shop_trader_count",
                expected=1,
            )
            _requires(
                errors,
                step.success_conditions,
                label=step.step_id,
                path="target.shop_inventory_owner",
                expected=True,
                target_id=target_id,
            )
        elif action.name == "buy_inspected_shop_item":
            _validate_purchase_step(errors, step, action, observation, target_id)

    return errors


def _validate_purchase_step(
    errors: list[str],
    step: PlanStep,
    action: SkillAction,
    observation: Observation,
    target_id: str,
) -> None:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    arguments = action.argument_map()
    item_name = arguments.get("item_name")
    expected_price = arguments.get("expected_price")
    if not isinstance(item_name, str) or not item_name.strip():
        errors.append("purchase requires the exact current tooltip item_name")
        return
    if (
        isinstance(expected_price, bool)
        or not isinstance(expected_price, int)
        or expected_price <= 0
    ):
        errors.append("purchase requires the exact positive tooltip price")
        return

    tooltip_text = telemetry.ui.tooltip_text or ""
    price_pattern = rf"(?<![A-Za-z0-9])c\.{expected_price}(?![0-9])"
    if (
        item_name not in tooltip_text
        or "[Food]" not in tooltip_text
        or re.search(price_pattern, tooltip_text) is None
    ):
        errors.append("purchase arguments do not match the current authoritative food tooltip")

    for path, expected in (
        ("telemetry.ui.active_screen", "trade"),
        ("telemetry.active_shop_trader_count", 1),
        ("telemetry.ui.tooltip_visible", True),
    ):
        _requires(
            errors,
            step.preconditions,
            label=step.step_id,
            path=path,
            expected=expected,
        )
    _requires(
        errors,
        step.preconditions,
        label=step.step_id,
        path="target.shop_inventory_owner",
        expected=True,
        target_id=target_id,
    )
    for token in (item_name, "[Food]", f"c.{expected_price}"):
        _requires(
            errors,
            step.preconditions,
            label=step.step_id,
            path="telemetry.ui.tooltip_text",
            expected=token,
            operator=ConditionOperator.CONTAINS,
        )

    money = telemetry.game.money
    food_items = _selected_food_items(observation)
    if money is None or food_items is None:
        errors.append("purchase requires exact current money and selected food counts")
        return
    _requires(
        errors,
        step.preconditions,
        label=step.step_id,
        path="telemetry.game.money",
        expected=money,
    )
    _requires(
        errors,
        step.preconditions,
        label=step.step_id,
        path="selected.food_items",
        expected=food_items,
    )
    before_error_count = len(errors)
    _requires(
        errors,
        step.success_conditions,
        label=step.step_id,
        path="telemetry.game.money",
        expected=money - expected_price,
    )
    _requires(
        errors,
        step.success_conditions,
        label=step.step_id,
        path="selected.food_items",
        expected=food_items + 1,
    )
    if len(errors) > before_error_count:
        errors.append("purchase must require the exact money delta and one food item")
