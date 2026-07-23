from __future__ import annotations

import re
from collections.abc import Iterable

from .models import (
    CharacterState,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    ControlMode,
    IdempotencyPolicy,
    NativeCommandStatus,
    Observation,
    ObservationPolicy,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
    SkillAction,
)

NATIVE_VENDOR_APPROACH_SKILLS = frozenset(
    {
        "approach_confirmed_vendor",
        "continue_confirmed_vendor_approach",
    }
)
MAX_GAME_SECONDS_PER_APPROACH_WALL_SECOND = 60.0
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
    selected = _selected_character(observation)
    return selected.food_items if selected is not None else None


def _selected_character(observation: Observation) -> CharacterState | None:
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
    return selected


def _food_rebase_fence(
    observation: Observation,
    target_id: str,
) -> dict[str, object] | None:
    telemetry = observation.telemetry
    selected = _selected_character(observation)
    if telemetry is None or selected is None:
        return None
    target = next(
        (entity for entity in telemetry.nearby_entities if entity.id == target_id),
        None,
    )
    if target is None:
        return None
    return {
        "mode": observation.mode,
        "control_mode": observation.control_mode.value,
        "telemetry_stale": observation.telemetry_stale,
        "identity_session_id": telemetry.identity_session_id,
        "capabilities": tuple(sorted(telemetry.capabilities)),
        "game": telemetry.game.model_dump(mode="json"),
        "ui": telemetry.ui.model_dump(
            mode="json",
            exclude={"client_width", "client_height"},
        ),
        "active_shop_trader_count": telemetry.active_shop_trader_count,
        "native_control": telemetry.native_control.model_dump(mode="json"),
        "selected": selected.model_dump(mode="json"),
        "target": target.model_dump(mode="json"),
    }


def food_procurement_rebase_errors(
    plan: PlanEnvelope,
    planner_observation: Observation,
    current_observation: Observation,
) -> list[str]:
    """Permit only sequence-only rebases across an unchanged live food fence."""

    errors: list[str] = []
    if not plan.based_on_revision.same_snapshot_as(
        planner_observation.world_revision
    ):
        errors.append("plan basis does not match its immutable planner snapshot")
    if not current_observation.world_revision.is_later_than(
        planner_observation.world_revision
    ):
        errors.append("current world revision is not causally later than the planner snapshot")

    actions = [
        step.action for step in plan.steps if isinstance(step.action, SkillAction)
    ]
    target_ids = _target_ids(actions)
    if (
        len(actions) != len(plan.steps)
        or len(target_ids) != len(actions)
        or len(set(target_ids)) != 1
    ):
        errors.append("stale food plan lacks one exact stable target fence")
        return errors

    target_id = target_ids[0]
    before = _food_rebase_fence(planner_observation, target_id)
    after = _food_rebase_fence(current_observation, target_id)
    if before is None or after is None:
        errors.append("food rebase fence is unavailable")
    elif before != after:
        changed = sorted(
            key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
        )
        errors.append(
            "food rebase fence changed during planning: " + ", ".join(changed)
        )
    return errors


def _field(
    path: str,
    expected: str | int | float | bool,
    *,
    target_id: str | None = None,
    operator: ConditionOperator = ConditionOperator.EQUALS,
) -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=ConditionPath(path),
        operator=operator,
        expected=expected,
        target_id=target_id,
        max_age_seconds=3.0,
    )


def _fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
        required_capabilities=sorted(FOOD_PROCUREMENT_CAPABILITIES),
    )


def _continuable_vendor_target_id(observation: Observation) -> str | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return None
    active_id = telemetry.native_control.active_command_id
    if active_id is None:
        return None
    acknowledgement = telemetry.native_control.acknowledgement_for(active_id)
    selected_ids = telemetry.ui.selected_character_ids
    if (
        acknowledgement is None
        or acknowledgement.status != NativeCommandStatus.ACCEPTED
        or acknowledgement.selected_character_ids != selected_ids
        or len(selected_ids) != 1
    ):
        return None
    return acknowledgement.target_id


def _approach_game_budget_seconds(actions: Iterable[SkillAction]) -> float:
    for action in actions:
        if action.name not in NATIVE_VENDOR_APPROACH_SKILLS:
            continue
        duration = action.argument_map().get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            return 0.0
        return (float(duration) + 1.0) * MAX_GAME_SECONDS_PER_APPROACH_WALL_SECOND
    return 0.0


def _expected_action_names(observation: Observation) -> list[str]:
    telemetry = observation.telemetry
    if telemetry is None:
        return []
    tooltip_ready = bool(
        telemetry.ui.tooltip_visible is True
        and telemetry.ui.tooltip_text
        and telemetry.ui.tooltip_source_bounds is not None
    )
    if telemetry.ui.active_screen == "world":
        approach_skill = (
            "continue_confirmed_vendor_approach"
            if _continuable_vendor_target_id(observation) is not None
            else "approach_confirmed_vendor"
        )
        return [
            approach_skill,
            "choose_show_goods",
            "inspect_shop_item",
        ]
    if telemetry.ui.active_screen == "dialogue":
        return ["choose_show_goods", "inspect_shop_item"]
    if telemetry.ui.active_screen == "trade" and tooltip_ready:
        return ["buy_inspected_shop_item"]
    if telemetry.ui.active_screen == "trade":
        return ["inspect_shop_item"]
    return []


def canonicalize_food_procurement_plan(
    plan: PlanEnvelope,
    observation: Observation,
) -> PlanEnvelope:
    """Compile trusted safety scaffolding around one structurally valid proposal."""

    telemetry = observation.telemetry
    actions = [
        step.action for step in plan.steps if isinstance(step.action, SkillAction)
    ]
    names = [action.name for action in actions]
    if (
        telemetry is None
        or len(actions) != len(plan.steps)
        or names != _expected_action_names(observation)
    ):
        return plan
    target_ids = _target_ids(actions)
    if len(target_ids) != len(actions) or len(set(target_ids)) != 1:
        return plan
    target_id = target_ids[0]

    paused = _field("telemetry.game.paused", True)
    selected_one = _field("telemetry.ui.selected_character_count", 1)
    compiled_steps: list[PlanStep] = []
    for index, (step, action) in enumerate(zip(plan.steps, actions, strict=True)):
        preconditions = [paused, selected_one]
        success_conditions = [paused, selected_one]
        if action.name in NATIVE_VENDOR_APPROACH_SKILLS:
            preconditions.extend(
                [
                    _field("telemetry.ui.active_screen", "world"),
                    _field(
                        "target.shop_inventory_owner",
                        False,
                        target_id=target_id,
                    ),
                ]
            )
            success_conditions.append(
                _field("telemetry.ui.dialogue_target_id", target_id)
            )
        elif action.name == "choose_show_goods":
            preconditions.extend(
                [
                    _field("telemetry.ui.dialogue_target_id", target_id),
                    _field("telemetry.ui.dialogue_option_0", SHOW_GOODS_TEXT),
                ]
            )
            success_conditions.extend(
                [
                    _field("telemetry.ui.active_screen", "trade"),
                    _field("telemetry.active_shop_trader_count", 1),
                    _field(
                        "target.shop_inventory_owner",
                        True,
                        target_id=target_id,
                    ),
                ]
            )
        elif action.name == "inspect_shop_item":
            preconditions.extend(
                [
                    _field("telemetry.ui.active_screen", "trade"),
                    _field("telemetry.active_shop_trader_count", 1),
                    _field(
                        "target.shop_inventory_owner",
                        True,
                        target_id=target_id,
                    ),
                ]
            )
            success_conditions.extend(
                [
                    _field("telemetry.ui.tooltip_visible", True),
                    _field("telemetry.active_shop_trader_count", 1),
                    _field(
                        "target.shop_inventory_owner",
                        True,
                        target_id=target_id,
                    ),
                ]
            )
        else:
            arguments = action.argument_map()
            item_name = arguments.get("item_name")
            expected_price = arguments.get("expected_price")
            money = telemetry.game.money
            food_items = _selected_food_items(observation)
            if (
                not isinstance(item_name, str)
                or isinstance(expected_price, bool)
                or not isinstance(expected_price, int)
                or money is None
                or food_items is None
            ):
                return plan
            preconditions.extend(
                [
                    _field("telemetry.ui.active_screen", "trade"),
                    _field("telemetry.active_shop_trader_count", 1),
                    _field(
                        "target.shop_inventory_owner",
                        True,
                        target_id=target_id,
                    ),
                    _field("telemetry.ui.tooltip_visible", True),
                    _field(
                        "telemetry.ui.tooltip_text",
                        item_name,
                        operator=ConditionOperator.CONTAINS,
                    ),
                    _field(
                        "telemetry.ui.tooltip_text",
                        "[Food]",
                        operator=ConditionOperator.CONTAINS,
                    ),
                    _field(
                        "telemetry.ui.tooltip_text",
                        f"c.{expected_price}",
                        operator=ConditionOperator.CONTAINS,
                    ),
                    _field("telemetry.game.money", money),
                    _field("selected.food_items", food_items),
                ]
            )
            success_conditions.extend(
                [
                    _field("telemetry.game.money", money - expected_price),
                    _field("selected.food_items", food_items + 1),
                ]
            )

        compiled_steps.append(
            step.model_copy(
                update={
                    "preconditions": preconditions,
                    "success_conditions": success_conditions,
                    "failure_conditions": [],
                    "timeout_seconds": (
                        12.0
                        if action.name in NATIVE_VENDOR_APPROACH_SKILLS
                        else 8.0
                    ),
                    "retry_budget": 0,
                    "idempotency": IdempotencyPolicy.AT_MOST_ONCE,
                    "on_success": (
                        plan.steps[index + 1].step_id
                        if index + 1 < len(plan.steps)
                        else None
                    ),
                    "on_failure": None,
                    "observation_policy": ObservationPolicy.UNTIL_TERMINAL,
                },
                deep=True,
            )
        )

    has_approach = any(name in NATIVE_VENDOR_APPROACH_SKILLS for name in names)
    has_purchase = "buy_inspected_shop_item" in names
    pointer_actions = sum(
        name in {"choose_show_goods", "inspect_shop_item", "buy_inspected_shop_item"}
        for name in names
    )
    return plan.model_copy(
        update={
            "assumptions": [_fresh()],
            "steps": compiled_steps,
            "entry_step_id": compiled_steps[0].step_id,
            "max_actions": len(compiled_steps),
            "max_wall_seconds": 30.0 if has_approach else 10.0 * len(compiled_steps),
            "max_game_seconds": (
                _approach_game_budget_seconds(actions) if has_approach else 3.0
            ),
            "risk_budget": RiskBudget(
                max_pointer_actions=pointer_actions,
                max_purchase_actions=int(has_purchase),
                max_native_assisted_actions=int(has_approach),
            ),
        },
        deep=True,
    )


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
    expected_names = _expected_action_names(observation)
    if names != expected_names:
        errors.append(
            "food procurement actions do not match the current authoritative phase; "
            f"expected {expected_names}, observed {names}"
        )
    approach_names = [
        name for name in names if name in NATIVE_VENDOR_APPROACH_SKILLS
    ]
    if approach_names:
        approach_action = actions[names.index(approach_names[0])]
        duration = approach_action.argument_map().get("duration_seconds")
        required_game_seconds = _approach_game_budget_seconds([approach_action])
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or plan.max_game_seconds < required_game_seconds
        ):
            errors.append(
                "food approach plan game-time budget must cover the bounded "
                "wall-to-game-time conversion"
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
    if (
        "continue_confirmed_vendor_approach" in names
        and _continuable_vendor_target_id(observation) != target_id
    ):
        errors.append(
            "food continuation must match the exact active accepted native vendor command"
        )

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
        _requires(
            errors,
            step.success_conditions,
            label=step.step_id,
            path="telemetry.ui.selected_character_count",
            expected=1,
        )

        if action.name in NATIVE_VENDOR_APPROACH_SKILLS:
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
