from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.config import MacroConfig, PlanningConfig, SafetyConfig
from kenshi_agent.env import AgentEnvironment
from kenshi_agent.evals import evaluate_log
from kenshi_agent.food_procurement import (
    FOOD_PROCUREMENT_CAPABILITIES,
    canonicalize_food_procurement_plan,
    food_procurement_rebase_errors,
)
from kenshi_agent.input_boundary import ExecutionToken
from kenshi_agent.models import (
    Action,
    ActionReceipt,
    CommandDispatchContext,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionResult,
    ControlMode,
    IdempotencyPolicy,
    LiveContinuousPolicy,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    ObservationPolicy,
    PlanEnvelope,
    PlannerOutput,
    PlanningMode,
    PlanStep,
    RiskBudget,
    SkillAction,
    Transition,
    UIState,
    WorldStateRevision,
)
from kenshi_agent.planners.base import Planner
from kenshi_agent.planning import PlanValidationError, evaluate_condition, validate_plan
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import ActionGuard, SafetyViolation
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry

TARGET_ID = "nearby:barman"
FOOD_NAME = "Dried Meat"
FOOD_PRICE = 649
SHOW_GOODS_TEXT = "Show me your goods."
CAPABILITIES = [
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
]


def field(
    path: str,
    expected: str | int | float | bool,
    *,
    operator: ConditionOperator = ConditionOperator.EQUALS,
    target_id: str | None = None,
) -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=path,
        operator=operator,
        expected=expected,
        target_id=target_id,
        max_age_seconds=3.0,
    )


def fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
        required_capabilities=CAPABILITIES,
    )


def revision(sequence: int = 10) -> WorldStateRevision:
    return WorldStateRevision(
        telemetry_sequence=sequence,
        frame_sequence=sequence,
        capability_epoch=1,
        observed_at_monotonic=float(sequence),
    )


def observation(
    *,
    screen: str = "world",
    dialogue_target_id: str | None = None,
    dialogue_options: list[str] | None = None,
    tooltip_text: str | None = None,
    tooltip_bounds: NormalizedPointerBounds | None = None,
    money: int = 1000,
    food_items: int = 0,
    active_native_approach: bool = False,
) -> Observation:
    trade_open = screen == "trade"
    return Observation.model_validate(
        {
            "run_id": "p6-policy",
            "step_index": 0,
            "mode": "live",
            "control_mode": "native_assisted",
            "planning_mode": "continuous",
            "world_revision": revision(),
            "telemetry_age_seconds": 0.0,
            "telemetry_stale": False,
            "telemetry": {
                "sequence": 10,
                "captured_at": datetime.now(UTC),
                "identity_session_id": "session-p6",
                "capabilities": CAPABILITIES,
                "game": {
                    "loaded": True,
                    "paused": True,
                    "elapsed_minutes": 123.0,
                    "money": money,
                },
                "native_control": (
                    {
                        "available": True,
                        "active_command_id": (
                            "cmd-0123456789abcdef0123456789abcdef"
                        ),
                        "acknowledgements": [
                            {
                                "command_id": (
                                    "cmd-0123456789abcdef0123456789abcdef"
                                ),
                                "command": "approach_confirmed_vendor",
                                "status": "accepted",
                                "reason": "issued",
                                "target_id": TARGET_ID,
                                "selected_character_ids": ["player:1"],
                                "based_on_telemetry_sequence": 7,
                                "acknowledged_at_telemetry_sequence": 8,
                                "accepted_at_telemetry_sequence": 8,
                            }
                        ],
                    }
                    if active_native_approach
                    else {"available": True}
                ),
                "ui": {
                    "active_screen": screen,
                    "dialogue_open": screen == "dialogue",
                    "dialogue_target_id": dialogue_target_id,
                    "dialogue_options": dialogue_options,
                    "tooltip_visible": tooltip_text is not None,
                    "tooltip_text": tooltip_text,
                    "tooltip_source_bounds": (
                        tooltip_bounds.model_dump() if tooltip_bounds is not None else None
                    ),
                    "selected_character_id": "player:1",
                    "selected_character_ids": ["player:1"],
                },
                "squad": [
                    {
                        "id": "player:1",
                        "name": "Green",
                        "selected": True,
                        "food_items": food_items,
                    }
                ],
                "active_shop_trader_count": 1 if trade_open else 0,
                "nearby_entities": [
                    {
                        "id": TARGET_ID,
                        "name": "Barman",
                        "is_animal": False,
                        "has_vendor_list": True,
                        "is_squad_leader": True,
                        "has_dialogue": True,
                        "shop_inventory_owner": trade_open,
                        "conscious": True,
                        "disposition": "neutral",
                    }
                ],
            },
        }
    )


def step(
    step_id: str,
    action: SkillAction,
    *,
    preconditions: list[Condition],
    success_conditions: list[Condition],
    on_success: str | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=action,
        preconditions=preconditions,
        success_conditions=success_conditions,
        timeout_seconds=8.0,
        retry_budget=0,
        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
        on_success=on_success,
    )


def procurement_plan(current: Observation) -> PlanEnvelope:
    paused = field("telemetry.game.paused", True)
    selected_one = field("telemetry.ui.selected_character_count", 1)
    approach = step(
        "approach",
        SkillAction(
            name="approach_confirmed_vendor",
            args={"target_id": TARGET_ID, "duration_seconds": 8.0},
        ),
        preconditions=[
            paused,
            selected_one,
            field("telemetry.ui.active_screen", "world"),
            field("target.shop_inventory_owner", False, target_id=TARGET_ID),
        ],
        success_conditions=[
            paused,
            selected_one,
            field("telemetry.ui.dialogue_target_id", TARGET_ID),
        ],
        on_success="show_goods",
    )
    show_goods = step(
        "show_goods",
        SkillAction(name="choose_show_goods", args={"target_id": TARGET_ID}),
        preconditions=[
            paused,
            selected_one,
            field("telemetry.ui.dialogue_target_id", TARGET_ID),
            field("telemetry.ui.dialogue_option_0", "Show me your goods."),
        ],
        success_conditions=[
            paused,
            selected_one,
            field("telemetry.ui.active_screen", "trade"),
            field("telemetry.active_shop_trader_count", 1),
            field("target.shop_inventory_owner", True, target_id=TARGET_ID),
        ],
        on_success="inspect",
    )
    inspect = step(
        "inspect",
        SkillAction(
            name="inspect_shop_item",
            args={"target_id": TARGET_ID, "x": 0.316, "y": 0.357},
        ),
        preconditions=[
            paused,
            selected_one,
            field("telemetry.ui.active_screen", "trade"),
            field("telemetry.active_shop_trader_count", 1),
            field("target.shop_inventory_owner", True, target_id=TARGET_ID),
        ],
        success_conditions=[
            paused,
            selected_one,
            field("telemetry.ui.tooltip_visible", True),
            field("telemetry.active_shop_trader_count", 1),
            field("target.shop_inventory_owner", True, target_id=TARGET_ID),
        ],
    )
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="food-chain",
        objective="Open the exact vendor trade screen and inspect one candidate.",
        control_mode=ControlMode.NATIVE_ASSISTED,
        based_on_revision=current.world_revision,
        assumptions=[fresh()],
        steps=[approach, show_goods, inspect],
        entry_step_id="approach",
        max_actions=3,
        max_wall_seconds=30.0,
        max_game_seconds=540.0,
        risk_budget=RiskBudget(
            max_pointer_actions=2,
            max_purchase_actions=0,
            max_native_assisted_actions=1,
        ),
    )


def purchase_plan(current: Observation) -> PlanEnvelope:
    purchase = step(
        "purchase",
        SkillAction(
            name="buy_inspected_shop_item",
            args={
                "target_id": TARGET_ID,
                "item_name": FOOD_NAME,
                "expected_price": FOOD_PRICE,
                "x": 0.316,
                "y": 0.357,
            },
        ),
        preconditions=[
            field("telemetry.game.paused", True),
            field("telemetry.ui.selected_character_count", 1),
            field("telemetry.ui.active_screen", "trade"),
            field("telemetry.active_shop_trader_count", 1),
            field("target.shop_inventory_owner", True, target_id=TARGET_ID),
            field("telemetry.ui.tooltip_visible", True),
            field(
                "telemetry.ui.tooltip_text",
                FOOD_NAME,
                operator=ConditionOperator.CONTAINS,
            ),
            field(
                "telemetry.ui.tooltip_text",
                "[Food]",
                operator=ConditionOperator.CONTAINS,
            ),
            field(
                "telemetry.ui.tooltip_text",
                f"c.{FOOD_PRICE}",
                operator=ConditionOperator.CONTAINS,
            ),
            field("telemetry.game.money", 1000),
            field("selected.food_items", 0),
        ],
        success_conditions=[
            field("telemetry.game.paused", True),
            field("telemetry.ui.selected_character_count", 1),
            field("telemetry.game.money", 351),
            field("selected.food_items", 1),
        ],
    )
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="food-purchase",
        objective="Buy the exact inspected food item once.",
        control_mode=ControlMode.NATIVE_ASSISTED,
        based_on_revision=current.world_revision,
        assumptions=[fresh()],
        steps=[purchase],
        entry_step_id="purchase",
        max_actions=1,
        max_wall_seconds=10.0,
        max_game_seconds=3.0,
        risk_budget=RiskBudget(
            max_pointer_actions=1,
            max_purchase_actions=1,
            max_native_assisted_actions=0,
        ),
    )


def planning_config(
    policy: LiveContinuousPolicy = LiveContinuousPolicy.FOOD_PROCUREMENT_V1,
) -> PlanningConfig:
    return PlanningConfig(
        mode=PlanningMode.CONTINUOUS,
        live_execution_policy=policy,
        observation_pump_enabled=False,
        concurrent_option_planning_enabled=False,
        max_plan_game_seconds=540.0,
        max_native_assisted_actions_per_plan=1,
    )


def macros() -> MacroRegistry:
    return MacroRegistry(
        {
            "approach_confirmed_vendor": MacroConfig(
                movement_pulse_seconds=2.0,
                movement_pulse_min_seconds=0.5,
                movement_pulse_max_seconds=8.0,
                requires_native_assisted=True,
                actions=[{"kind": "hotkey", "keys": ["ctrl", "shift", "f10"]}],
            ),
            "continue_confirmed_vendor_approach": MacroConfig(
                movement_pulse_seconds=2.0,
                movement_pulse_min_seconds=0.5,
                movement_pulse_max_seconds=8.0,
                requires_native_assisted=True,
            ),
            "choose_show_goods": MacroConfig(
                actions=[{"kind": "click", "x": 0.5, "y": 0.812}]
            ),
            "inspect_shop_item": MacroConfig(
                actions=[
                    {
                        "kind": "move_cursor",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                    }
                ]
            ),
            "buy_inspected_shop_item": MacroConfig(
                actions=[
                    {
                        "kind": "click",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                        "button": "right",
                    }
                ]
            ),
        }
    )


def test_dialogue_options_preserve_unknown_instead_of_inventing_an_empty_list() -> None:
    assert UIState().dialogue_options is None


def test_food_policy_requires_only_capabilities_emitted_by_live_protocol_04() -> None:
    assert FOOD_PROCUREMENT_CAPABILITIES == frozenset(CAPABILITIES)
    assert "squad.hunger" not in FOOD_PROCUREMENT_CAPABILITIES


def test_global_conditions_canonicalize_redundant_target_ids() -> None:
    global_condition = field(
        "telemetry.ui.dialogue_target_id",
        TARGET_ID,
        target_id=TARGET_ID,
    )
    target_condition = field(
        "target.shop_inventory_owner",
        False,
        target_id=TARGET_ID,
    )

    assert global_condition.target_id is None
    assert target_condition.target_id == TARGET_ID


def test_live_food_plan_rebases_only_across_an_unchanged_exact_fence() -> None:
    planner_observation = observation()
    planner_telemetry = planner_observation.telemetry
    assert planner_telemetry is not None
    planner_observation = planner_observation.model_copy(
        update={
            "telemetry": planner_telemetry.model_copy(
                update={
                    "ui": planner_telemetry.ui.model_copy(
                        update={"client_width": 1920, "client_height": 1080}
                    )
                }
            )
        },
        deep=True,
    )
    plan = procurement_plan(planner_observation)
    telemetry = planner_observation.telemetry
    assert telemetry is not None
    later = planner_observation.model_copy(
        update={
            "world_revision": revision(11),
            "telemetry": telemetry.model_copy(
                update={
                    "sequence": 11,
                    "ui": telemetry.ui.model_copy(
                        update={"client_width": None, "client_height": None}
                    ),
                }
            ),
        },
        deep=True,
    )

    assert food_procurement_rebase_errors(plan, planner_observation, later) == []

    changed_telemetry = later.telemetry
    assert changed_telemetry is not None
    changed = later.model_copy(
        update={
            "telemetry": changed_telemetry.model_copy(
                update={"game": changed_telemetry.game.model_copy(update={"money": 999})}
            )
        },
        deep=True,
    )
    assert food_procurement_rebase_errors(plan, planner_observation, changed) == [
        "food rebase fence changed during planning: game"
    ]

    changed_ui_telemetry = later.telemetry
    assert changed_ui_telemetry is not None
    changed_ui = later.model_copy(
        update={
            "telemetry": changed_ui_telemetry.model_copy(
                update={
                    "ui": changed_ui_telemetry.ui.model_copy(
                        update={"active_screen": "dialogue"}
                    )
                }
            )
        },
        deep=True,
    )
    assert food_procurement_rebase_errors(plan, planner_observation, changed_ui) == [
        "food rebase fence changed during planning: ui"
    ]


def test_food_policy_compiles_safety_scaffolding_around_valid_actions() -> None:
    current = observation()
    proposal = procurement_plan(current)
    proposal.steps[1].success_conditions = [
        field("telemetry.game.paused", True),
        field("telemetry.ui.selected_character_count", 1),
        field("telemetry.ui.active_screen", "trade"),
    ]
    proposal.steps[2].success_conditions = [
        field("telemetry.game.paused", True),
        field("telemetry.ui.selected_character_count", 1),
        field("telemetry.ui.tooltip_visible", True),
    ]
    proposal.max_game_seconds = 0.5

    compiled = canonicalize_food_procurement_plan(proposal, current)

    validate_plan(compiled, current, planning_config(), macros())
    assert compiled.max_game_seconds == 540.0
    assert compiled.steps[0].observation_policy is ObservationPolicy.UNTIL_TERMINAL
    assert any(
        condition.path == "target.shop_inventory_owner"
        and condition.expected is True
        for condition in compiled.steps[1].success_conditions
    )
    assert any(
        condition.path == "telemetry.active_shop_trader_count"
        and condition.expected == 1
        for condition in compiled.steps[2].success_conditions
    )


def test_food_policy_continues_exact_active_native_approach_without_reissue() -> None:
    current = observation(active_native_approach=True)
    proposal = procurement_plan(current)
    proposal.steps[0].action = SkillAction(
        name="continue_confirmed_vendor_approach",
        args={"target_id": TARGET_ID, "duration_seconds": 2.0},
    )

    compiled = canonicalize_food_procurement_plan(proposal, current)

    validate_plan(compiled, current, planning_config(), macros())
    assert compiled.steps[0].action.name == "continue_confirmed_vendor_approach"
    assert compiled.max_game_seconds == 180.0


def test_contains_condition_is_five_valued_and_capability_gated() -> None:
    current = observation(
        screen="trade",
        tooltip_text=f"{FOOD_NAME}\n[Food]\nValue: c.{FOOD_PRICE}",
    )
    condition = field(
        "telemetry.ui.tooltip_text",
        "[Food]",
        operator=ConditionOperator.CONTAINS,
    )
    assert evaluate_condition(condition, current).result is ConditionResult.TRUE

    unknown = current.model_copy(
        update={
            "telemetry": current.telemetry.model_copy(
                update={"ui": current.telemetry.ui.model_copy(update={"tooltip_text": None})}
            )
        }
    )
    assert evaluate_condition(condition, unknown).result is ConditionResult.UNKNOWN


def test_food_policy_accepts_one_response_three_action_open_and_inspect_chain() -> None:
    current = observation()
    validate_plan(procurement_plan(current), current, planning_config(), macros())


def test_food_policy_is_disabled_by_default_and_rejects_target_drift() -> None:
    current = observation()
    with pytest.raises(PlanValidationError, match="disabled"):
        validate_plan(
            procurement_plan(current),
            current,
            planning_config(LiveContinuousPolicy.DISABLED),
            macros(),
        )

    changed = procurement_plan(current)
    changed.steps[1].action = SkillAction(
        name="choose_show_goods",
        args={"target_id": "nearby:someone-else"},
    )
    with pytest.raises(PlanValidationError, match="same exact target"):
        validate_plan(changed, current, planning_config(), macros())


def test_food_policy_accepts_only_exact_tooltip_bound_purchase_delta() -> None:
    current = observation(
        screen="trade",
        tooltip_text=f"{FOOD_NAME}\n[Food]\nValue: c.{FOOD_PRICE}",
        tooltip_bounds=NormalizedPointerBounds(
            min_x=0.30,
            max_x=0.34,
            min_y=0.34,
            max_y=0.38,
        ),
    )
    validate_plan(purchase_plan(current), current, planning_config(), macros())

    unsafe = purchase_plan(current)
    unsafe.steps[0].success_conditions[2] = field("telemetry.game.money", 350)
    with pytest.raises(PlanValidationError, match="exact money delta"):
        validate_plan(unsafe, current, planning_config(), macros())


def test_purchase_guard_binds_click_to_exact_owner_and_current_tooltip_source() -> None:
    current = observation(
        screen="trade",
        tooltip_text=f"{FOOD_NAME}\n[Food]\nValue: c.{FOOD_PRICE}",
        tooltip_bounds=NormalizedPointerBounds(
            min_x=0.30,
            max_x=0.34,
            min_y=0.34,
            max_y=0.38,
        ),
    )
    config = SafetyConfig(
        allow_action_kinds=["skill", "click"],
        allow_skills=["buy_inspected_shop_item"],
        max_purchase_price=750,
        min_money_after_purchase=250,
        max_purchases_per_run=1,
    )
    action = purchase_plan(current).steps[0].action
    assert isinstance(action, SkillAction)
    assert (
        ActionGuard(config, macros(), control_mode=ControlMode.NATIVE_ASSISTED).validate(
            action, current
        )
        == action
    )

    wrong_coordinate = action.model_copy(
        update={
            "args": SkillAction(
                name=action.name,
                args={**action.argument_map(), "x": 0.50},
            ).args
        }
    )
    with pytest.raises(SafetyViolation, match="current tooltip source"):
        ActionGuard(config, macros(), control_mode=ControlMode.NATIVE_ASSISTED).validate(
            wrong_coordinate,
            current,
        )

    wrong_owner = current.model_copy(
        update={
            "telemetry": current.telemetry.model_copy(
                update={
                    "nearby_entities": [
                        NearbyEntity(
                            id="nearby:someone-else",
                            name="Someone Else",
                            shop_inventory_owner=True,
                            disposition="neutral",
                        )
                    ]
                }
            )
        }
    )
    with pytest.raises(SafetyViolation, match="exact target"):
        ActionGuard(config, macros(), control_mode=ControlMode.NATIVE_ASSISTED).validate(
            action,
            wrong_owner,
        )


class FoodChainEnvironment(AgentEnvironment):
    def __init__(self) -> None:
        self.sequence = 10
        self.step_index = 0
        self.stage = "world"
        self.money = 1000
        self.food_items = 0
        self.actions: list[Action] = []

    def current(self) -> Observation:
        tooltip_ready = self.stage in {"tooltip", "purchased"}
        current = observation(
            screen=(
                "dialogue"
                if self.stage == "dialogue"
                else "trade"
                if self.stage in {"trade", "tooltip", "purchased"}
                else "world"
            ),
            dialogue_target_id=TARGET_ID if self.stage == "dialogue" else None,
            dialogue_options=(
                [SHOW_GOODS_TEXT] if self.stage == "dialogue" else None
            ),
            tooltip_text=(
                f"{FOOD_NAME}\n[Food]\nValue: c.{FOOD_PRICE}"
                if tooltip_ready
                else None
            ),
            tooltip_bounds=(
                NormalizedPointerBounds(
                    min_x=0.30,
                    max_x=0.34,
                    min_y=0.34,
                    max_y=0.38,
                )
                if tooltip_ready
                else None
            ),
            money=self.money,
            food_items=self.food_items,
        )
        telemetry = current.telemetry
        assert telemetry is not None
        return current.model_copy(
            update={
                "step_index": self.step_index,
                "world_revision": revision(self.sequence),
                "telemetry": telemetry.model_copy(update={"sequence": self.sequence}),
            }
        )

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return self.current()

    async def observe(self) -> Observation:
        return self.current()

    async def step(self, action: Action) -> Transition:
        assert isinstance(action, SkillAction)
        expected = {
            "world": "approach_confirmed_vendor",
            "dialogue": "choose_show_goods",
            "trade": "inspect_shop_item",
            "tooltip": "buy_inspected_shop_item",
        }
        assert action.name == expected[self.stage]
        self.actions.append(action)
        self.stage = {
            "world": "dialogue",
            "dialogue": "trade",
            "trade": "tooltip",
            "tooltip": "purchased",
        }[self.stage]
        if self.stage == "purchased":
            self.money -= FOOD_PRICE
            self.food_items += 1
        self.sequence += 1
        self.step_index += 1
        return Transition(
            receipt=ActionReceipt(
                action=action,
                control_mode=ControlMode.NATIVE_ASSISTED,
                accepted=True,
                executed=True,
                dry_run=False,
                primitive_actions=1,
                message="deterministic live-shaped transition",
            ),
            observation=self.current(),
        )

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None = None,
    ) -> Transition:
        del command, token
        return await self.step(action)

    async def close(self) -> None:
        return None


class FoodChainPlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, current: Observation) -> PlannerOutput:
        self.calls += 1
        return (
            procurement_plan(current)
            if current.telemetry is not None
            and current.telemetry.ui.active_screen == "world"
            else purchase_plan(current)
        )


def test_live_runtime_rebases_sequence_only_planner_latency_before_execution(
    tmp_path: Path,
) -> None:
    class AdvancingFoodEnvironment(FoodChainEnvironment):
        async def observe(self) -> Observation:
            self.sequence += 1
            return self.current()

    class DelayedFoodPlanner(FoodChainPlanner):
        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            await asyncio.sleep(0.03)
            return procurement_plan(current)

    async def scenario() -> None:
        environment = AdvancingFoodEnvironment()
        planner = DelayedFoodPlanner()
        registry = macros()
        logger = SessionLogger(tmp_path / "events.jsonl", "p6-rebase")
        safety = SafetyConfig(
            supervisor_enabled=False,
            allow_action_kinds=["skill", "click", "move_cursor", "hotkey"],
            allow_skills=[
                "approach_confirmed_vendor",
                "choose_show_goods",
                "inspect_shop_item",
                "buy_inspected_shop_item",
            ],
            max_actions_per_minute=500,
        )
        config = planning_config().model_copy(
            update={
                "observation_pump_enabled": True,
                "observation_pump_seconds": 0.005,
            }
        )
        runtime = AgentRuntime(
            run_id="p6-rebase",
            environment=environment,
            planner=planner,
            guard=ActionGuard(
                safety,
                registry,
                control_mode=ControlMode.NATIVE_ASSISTED,
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            control_mode=ControlMode.NATIVE_ASSISTED,
            planning_config=config,
        )
        try:
            summary = await runtime.run(max_steps=1)
        finally:
            logger.close()

        assert summary.steps_completed == 1
        assert planner.calls == 1
        assert [action.name for action in environment.actions] == [
            "approach_confirmed_vendor"
        ]
        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert sum(event["event_type"] == "plan_rebased" for event in events) == 1
        assert sum(event["event_type"] == "plan_accepted" for event in events) == 1

    asyncio.run(scenario())


def test_live_shaped_runtime_executes_four_actions_from_two_strategic_calls(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment = FoodChainEnvironment()
        planner = FoodChainPlanner()
        registry = macros()
        logger = SessionLogger(tmp_path / "events.jsonl", "p6-policy")
        safety = SafetyConfig(
            supervisor_enabled=False,
            allow_action_kinds=["skill", "click", "move_cursor", "hotkey"],
            allow_skills=[
                "approach_confirmed_vendor",
                "choose_show_goods",
                "inspect_shop_item",
                "buy_inspected_shop_item",
            ],
            max_actions_per_minute=500,
            max_purchase_price=750,
            min_money_after_purchase=250,
            max_purchases_per_run=1,
        )
        runtime = AgentRuntime(
            run_id="p6-policy",
            environment=environment,
            planner=planner,
            guard=ActionGuard(
                safety,
                registry,
                control_mode=ControlMode.NATIVE_ASSISTED,
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            control_mode=ControlMode.NATIVE_ASSISTED,
            planning_config=planning_config(),
        )
        try:
            summary = await runtime.run(max_steps=4)
        finally:
            logger.close()

        assert summary.steps_completed == 4
        assert planner.calls == 2
        assert [action.name for action in environment.actions] == [
            "approach_confirmed_vendor",
            "choose_show_goods",
            "inspect_shop_item",
            "buy_inspected_shop_item",
        ]
        assert environment.money == 351
        assert environment.food_items == 1
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.strategic_planner_calls == 2
        assert metrics.plans_completed == 2
        assert metrics.plan_steps_succeeded == 4
        assert metrics.actions_per_strategic_planner_call == 2.0

    asyncio.run(scenario())
