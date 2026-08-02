from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.action_contracts import SELL_ITEM_CONTRACT
from kenshi_agent.config import (
    CaptureConfig,
    ControlsConfig,
    PlanningConfig,
    RuntimeConfig,
    SafetyConfig,
)
from kenshi_agent.continuous_executor import ContinuousPlanExecutor
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.live_plan_policy import live_plan_policy_errors
from kenshi_agent.models import (
    ActionReceipt,
    CharacterState,
    ClickAction,
    Condition,
    ConditionKind,
    ConditionOperator,
    ControlMode,
    Disposition,
    GameState,
    IdempotencyPolicy,
    InventoryItem,
    MouseButton,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PlanEnvelope,
    PlanningMode,
    PlanStep,
    RiskBudget,
    SaleEvidence,
    SaleStatus,
    SellItemAction,
    TelemetrySnapshot,
    Transition,
    UIState,
    VisibleUIControl,
    WorldStateRevision,
)
from kenshi_agent.planning import PlanningClock
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry
from kenshi_agent.telemetry import TelemetryRead
from kenshi_agent.world_state import WorldStateStore


class FakeClock(PlanningClock):
    def __init__(self) -> None:
        self.now = 1.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _bounds(index: int, *, buyer: bool = False) -> NormalizedPointerBounds:
    top = 0.20 + 0.06 * index
    return NormalizedPointerBounds(
        min_x=0.72 if buyer else 0.32,
        min_y=top,
        max_x=0.77 if buyer else 0.37,
        max_y=top + 0.05,
    )


class SaleTelemetry:
    def __init__(self, *, carried: int, stacked: bool = False) -> None:
        self.carried = carried
        self.stacked = stacked
        self.money = 1000
        self.sequence = 0
        self.max_age_seconds = 3.0
        self.path = Path("sale-telemetry.json")

    def _inventory(self) -> list[InventoryItem]:
        if not self.carried:
            return []
        if self.stacked:
            return [
                InventoryItem(
                    name="Dried Meat",
                    item_name="Dried Meat",
                    item_quantity=self.carried,
                )
            ]
        return [
            InventoryItem(
                name="Dried Meat",
                item_name="Dried Meat",
                item_quantity=1,
            )
            for _ in range(self.carried)
        ]

    def _own_cells(self) -> list[VisibleUIControl]:
        quantities = [self.carried] if self.stacked and self.carried else [
            1 for _ in range(self.carried)
        ]
        return [
            VisibleUIControl(
                label="Dried Meat",
                role="item",
                window="JAGLONGER",
                item_name="Dried Meat",
                item_base_value=43,
                item_quantity=quantity,
                bounds=_bounds(index),
            )
            for index, quantity in enumerate(quantities)
        ]

    def read(self) -> TelemetryRead:
        self.sequence += 1
        snapshot = TelemetrySnapshot(
            sequence=self.sequence,
            captured_at=datetime.now(UTC),
            identity_session_id="session-sale",
            capabilities=[
                "ui.visible_controls",
                "ui.inventory",
                "game.money",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.shop_owners",
                "squad.basic",
                "squad.inventory",
            ],
            game=GameState(
                loaded=True,
                paused=True,
                money=self.money,
                elapsed_minutes=0.0,
            ),
            squad=[
                CharacterState(
                    id="character-jaglonger",
                    name="Jaglonger",
                    selected=True,
                    inventory=self._inventory(),
                    inventory_complete=True,
                )
            ],
            nearby_entities=[
                NearbyEntity(
                    id="buyer-burn",
                    name="Burn",
                    disposition=Disposition.NEUTRAL,
                    shop_inventory_owner=True,
                )
            ],
            active_shop_trader_count=1,
            ui=UIState(
                active_screen="trade",
                open_inventory_windows=2,
                selected_character_id="character-jaglonger",
                selected_character_ids=["character-jaglonger"],
                visible_controls=[
                    *self._own_cells(),
                    VisibleUIControl(
                        label="Water",
                        role="item",
                        window="BURN",
                        item_name="Water",
                        item_base_value=25,
                        item_quantity=12,
                        bounds=_bounds(0, buyer=True),
                    ),
                ],
            ),
        )
        return TelemetryRead(
            snapshot=snapshot,
            age_seconds=0.0,
            stale=False,
            path=self.path,
        )


class SaleController(InputController):
    def __init__(
        self,
        telemetry: SaleTelemetry,
        *,
        inventory_updates: bool = True,
        reverse_transfer: bool = False,
    ) -> None:
        self.telemetry = telemetry
        self.inventory_updates = inventory_updates
        self.reverse_transfer = reverse_transfer
        self.actions: list[PrimitiveInputAction] = []

    def focus_window(self) -> None:
        return None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        self.actions.append(action)
        if isinstance(action, ClickAction) and action.button is MouseButton.RIGHT:
            assert self.telemetry.carried > 0
            if self.reverse_transfer:
                self.telemetry.carried += 1
                self.telemetry.money -= 50
            elif self.inventory_updates:
                self.telemetry.carried -= 1
                self.telemetry.money += 50
            else:
                self.telemetry.money += 50
        now = datetime.now(UTC)
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=now,
            finished_at=now,
            primitive_actions=1,
        )

    def emergency_stop_pressed(self, key: str) -> bool:
        del key
        return False

    def user_input_detected(self) -> bool:
        return False

    def client_rect(self) -> WindowRect:
        return WindowRect(left=0, top=0, right=640, bottom=360)


def sale_environment(
    tmp_path: Path,
    *,
    carried: int,
    stacked: bool = False,
    inventory_updates: bool = True,
    reverse_transfer: bool = False,
) -> tuple[LiveEnvironment, SaleTelemetry, SaleController]:
    telemetry = SaleTelemetry(carried=carried, stacked=stacked)
    controller = SaleController(
        telemetry,
        inventory_updates=inventory_updates,
        reverse_transfer=reverse_transfer,
    )
    environment = LiveEnvironment(
        run_id="sale-option-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        macros=MacroRegistry({}),
        runtime_config=RuntimeConfig(settle_seconds=0.0, objective="Sell supplies."),
        controls_config=ControlsConfig(
            post_input_delay_seconds=0.0,
            item_cell_hover_seconds=0.0,
        ),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    return environment, telemetry, controller


def _sale(*, quantity: int) -> SellItemAction:
    return SellItemAction(
        cell_label="Dried Meat",
        item_name="Dried Meat",
        quantity=quantity,
        window="JAGLONGER",
        buyer_id="buyer-burn",
    )


def _fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def _sale_plan(
    observation: Observation,
    action: SellItemAction,
) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="sale-controller-verdict",
        plan_version=1,
        objective="Sell one bounded quantity under one strategic choice.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[_fresh()],
        steps=[
            PlanStep(
                step_id="sale",
                action=action,
                preconditions=[_fresh()],
                success_conditions=[],
                timeout_seconds=30.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        entry_step_id="sale",
        max_actions=1,
        max_wall_seconds=60.0,
        max_game_seconds=60.0,
        risk_budget=RiskBudget(
            max_pointer_actions=action.quantity,
            max_purchase_actions=action.quantity,
            max_native_assisted_actions=0,
        ),
    )


def test_sale_terminal_status_matches_every_bounded_quantity_pair() -> None:
    for requested_quantity in range(1, 6):
        for sold_quantity in range(0, 6):
            for status in SaleStatus:
                valid = sold_quantity <= requested_quantity and (
                    (
                        status is SaleStatus.OUTCOME_UNKNOWN
                        and sold_quantity < requested_quantity
                    )
                    or (
                        status is SaleStatus.SOLD
                        and sold_quantity == requested_quantity
                    )
                    or (
                        status is SaleStatus.PARTIALLY_SOLD
                        and 0 < sold_quantity < requested_quantity
                    )
                    or (
                        status is SaleStatus.NOT_SOLD
                        and sold_quantity == 0
                    )
                )
                arguments = {
                    "status": status,
                    "buyer_id": "buyer-burn",
                    "selected_character_id": "character-jaglonger",
                    "item_name": "Dried Meat",
                    "requested_quantity": requested_quantity,
                    "sold_quantity": sold_quantity,
                    "money_before": 1000,
                    "inventory_quantity_before": 3,
                    "reason": "Finite-state invariant.",
                }
                if valid:
                    assert SaleEvidence(**arguments).status is status
                else:
                    with pytest.raises(ValueError):
                        SaleEvidence(**arguments)


def test_sale_contract_reserves_every_requested_unit() -> None:
    action = _sale(quantity=3)

    assert SELL_ITEM_CONTRACT.controller_verified
    assert SELL_ITEM_CONTRACT.risk_for(action).as_tuple() == (3, 3, 0)
    assert SELL_ITEM_CONTRACT.primitive_action_bound_for(action) == 6


@pytest.mark.parametrize("stacked", [False, True])
def test_one_sale_intent_transfers_its_bounded_quantity(
    tmp_path: Path,
    stacked: bool,
) -> None:
    async def scenario() -> None:
        action = _sale(quantity=3)
        environment, telemetry, controller = sale_environment(
            tmp_path,
            carried=3,
            stacked=stacked,
        )
        await environment.reset()
        transition = await environment.step(action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.sale
        assert evidence is not None
        assert evidence.status.value == "sold"
        assert evidence.requested_quantity == 3
        assert evidence.sold_quantity == 3
        assert evidence.money_before == 1000
        assert evidence.money_after == 1150
        assert evidence.inventory_quantity_before == 3
        assert evidence.inventory_quantity_after == 0
        assert transition.receipt.primitive_actions == 6
        assert telemetry.carried == 0
        assert len(
            [
                item
                for item in controller.actions
                if isinstance(item, ClickAction)
                and item.button is MouseButton.RIGHT
            ]
        ) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("carried", "inventory_updates", "expected_completed", "expected_status"),
    [
        (3, True, True, "sold"),
        (2, True, False, "partially_sold"),
        (3, False, False, "outcome_unknown"),
    ],
)
def test_continuous_executor_completes_only_the_full_sale_terminal(
    tmp_path: Path,
    carried: int,
    inventory_updates: bool,
    expected_completed: bool,
    expected_status: str,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        action = _sale(quantity=3)
        environment, _, _ = sale_environment(
            tmp_path,
            carried=carried,
            inventory_updates=inventory_updates,
        )
        if not inventory_updates:
            environment._SALE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        observation = await environment.reset()
        plan = _sale_plan(observation, action)
        assert live_plan_policy_errors(plan, observation) == []

        store = WorldStateStore(clock=clock)
        store.publish(observation)
        logger = SessionLogger(
            tmp_path / f"{expected_status}.jsonl",
            "sale-controller-verdict",
        )

        def observe_transition(
            plan: PlanEnvelope,
            step: PlanStep,
            before: Observation,
            transition: Transition,
            command_id: str,
            action_start_revision: WorldStateRevision,
        ) -> Observation:
            del plan, step, before, command_id, action_start_revision
            store.publish(transition.observation)
            return transition.observation

        executor = ContinuousPlanExecutor(
            environment=environment,
            guard=ActionGuard(
                SafetyConfig(
                    allow_action_kinds=["sell_item"],
                    max_actions_per_minute=100,
                ),
                MacroRegistry({}),
                control_mode=ControlMode.NATIVE_ASSISTED,
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            clock=clock,
            state_store=store,
            observe_transition=observe_transition,
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                max_purchase_actions_per_plan=3,
            ),
        )
        try:
            result = await executor.execute(
                plan,
                observation,
                remaining_run_actions=1,
            )
        finally:
            logger.close()

        assert result.actions_completed == 1
        assert result.completed is expected_completed
        assert expected_status in (
            tmp_path / f"{expected_status}.jsonl"
        ).read_text(encoding="utf-8")
        if expected_completed:
            assert result.reason == "Plan completed."
        else:
            assert expected_status in result.reason

    asyncio.run(scenario())


def test_inventory_exhaustion_returns_partial_without_an_unbound_click(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        action = _sale(quantity=3)
        environment, telemetry, controller = sale_environment(
            tmp_path,
            carried=2,
        )
        await environment.reset()
        transition = await environment.step(action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.sale
        assert evidence is not None
        assert evidence.status.value == "partially_sold"
        assert evidence.requested_quantity == 3
        assert evidence.sold_quantity == 2
        assert evidence.inventory_quantity_after == 0
        assert telemetry.carried == 0
        assert len(
            [
                item
                for item in controller.actions
                if isinstance(item, ClickAction)
                and item.button is MouseButton.RIGHT
            ]
        ) == 2

    asyncio.run(scenario())


def test_mismatched_money_and_inventory_evidence_stops_without_retry(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        action = _sale(quantity=3)
        environment, _, controller = sale_environment(
            tmp_path,
            carried=3,
            inventory_updates=False,
        )
        environment._SALE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        await environment.reset()
        transition = await environment.step(action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.sale
        assert evidence is not None
        assert evidence.status.value == "outcome_unknown"
        assert evidence.sold_quantity == 0
        assert evidence.money_before == 1000
        assert evidence.money_after == 1050
        assert evidence.inventory_quantity_before == 3
        assert evidence.inventory_quantity_after == 3
        assert len(
            [
                item
                for item in controller.actions
                if isinstance(item, ClickAction)
                and item.button is MouseButton.RIGHT
            ]
        ) == 1

    asyncio.run(scenario())


def test_reverse_money_and_inventory_changes_are_unknown_not_a_noop(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        action = _sale(quantity=3)
        environment, _, controller = sale_environment(
            tmp_path,
            carried=3,
            reverse_transfer=True,
        )
        environment._SALE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        await environment.reset()
        transition = await environment.step(action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.sale
        assert evidence is not None
        assert evidence.status is SaleStatus.OUTCOME_UNKNOWN
        assert evidence.sold_quantity == 0
        assert evidence.money_after == 950
        assert evidence.inventory_quantity_after == 4
        assert len(
            [
                item
                for item in controller.actions
                if isinstance(item, ClickAction)
                and item.button is MouseButton.RIGHT
            ]
        ) == 1

    asyncio.run(scenario())
