from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.config import (
    CaptureConfig,
    ControlsConfig,
    PlanningConfig,
    RuntimeConfig,
    SafetyConfig,
)
from kenshi_agent.continuous_executor import ContinuousPlanExecutor
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.dialogue_interaction import dialogue_interaction_policy_errors
from kenshi_agent.env.live import LiveEnvironment
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
    PurchaseEvidence,
    PurchaseItemAction,
    PurchaseStatus,
    RiskBudget,
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


def _bounds(index: int) -> NormalizedPointerBounds:
    top = 0.20 + 0.06 * index
    return NormalizedPointerBounds(
        min_x=0.52,
        min_y=top,
        max_x=0.57,
        max_y=top + 0.05,
    )


class PurchaseTelemetry:
    def __init__(self, *, stock: int, money: int = 1000) -> None:
        self.stock = stock
        self.carried = 0
        self.money = money
        self.sequence = 0
        self.max_age_seconds = 3.0
        self.path = Path("purchase-telemetry.json")

    def read(self) -> TelemetryRead:
        self.sequence += 1
        inventory = (
            [InventoryItem(name="Dried Meat", quantity=self.carried)]
            if self.carried
            else []
        )
        controls = (
            [
                VisibleUIControl(
                    label="Dried Meat",
                    role="item",
                    window="BURN",
                    item_name="Dried Meat",
                    item_base_value=43,
                    item_quantity=self.stock,
                    bounds=_bounds(0),
                )
            ]
            if self.stock
            else []
        )
        snapshot = TelemetrySnapshot(
            sequence=self.sequence,
            captured_at=datetime.now(UTC),
            identity_session_id="session-purchase",
            capabilities=[
                "ui.visible_controls",
                "ui.tooltip",
                "ui.inventory",
                "game.money",
                "game.pause",
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
                    id="character-bark",
                    name="Bark",
                    selected=True,
                    inventory=inventory,
                    inventory_complete=True,
                )
            ],
            nearby_entities=[
                NearbyEntity(
                    id="seller-burn",
                    name="Burn",
                    disposition=Disposition.NEUTRAL,
                    shop_inventory_owner=True,
                )
            ],
            active_shop_trader_count=1,
            ui=UIState(
                active_screen="trade",
                open_inventory_windows=2,
                selected_character_id="character-bark",
                selected_character_ids=["character-bark"],
                visible_controls=controls,
            ),
        )
        return TelemetryRead(
            snapshot=snapshot,
            age_seconds=0.0,
            stale=False,
            path=self.path,
        )


class PurchaseController(InputController):
    def __init__(
        self,
        telemetry: PurchaseTelemetry,
        *,
        inventory_updates: bool = True,
        no_effect: bool = False,
    ) -> None:
        self.telemetry = telemetry
        self.inventory_updates = inventory_updates
        # A right-click that lands on a live cell and moves nothing at all.
        # Observed in `live-price-check-20260730-132702` with the item both
        # affordable and in stock, and distinct from a partial transfer.
        self.no_effect = no_effect
        self.actions: list[PrimitiveInputAction] = []

    def focus_window(self) -> None:
        return None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        self.actions.append(action)
        if (
            isinstance(action, ClickAction)
            and action.button is MouseButton.RIGHT
            and not self.no_effect
        ):
            assert self.telemetry.stock > 0
            self.telemetry.stock -= 1
            if self.inventory_updates:
                self.telemetry.carried += 1
            # Charges the cell's own price. An earlier fixture debited 87
            # for a cell exporting 43, encoding a belief that the exported
            # value was a non-authoritative estimate of an unknowable charge.
            # It was simply the wrong side of the trade - the sell value - and
            # three live purchases have since debited the buy price exactly.
            self.telemetry.money -= 43
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


def purchase_environment(
    tmp_path: Path,
    *,
    stock: int,
    inventory_updates: bool = True,
    money: int = 1000,
    no_effect: bool = False,
) -> tuple[LiveEnvironment, PurchaseTelemetry, PurchaseController]:
    telemetry = PurchaseTelemetry(stock=stock, money=money)
    controller = PurchaseController(
        telemetry,
        inventory_updates=inventory_updates,
        no_effect=no_effect,
    )
    environment = LiveEnvironment(
        run_id="purchase-option-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        macros=MacroRegistry({}),
        runtime_config=RuntimeConfig(settle_seconds=0.0, objective="Buy supplies."),
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


def _purchase(*, quantity: int) -> PurchaseItemAction:
    return PurchaseItemAction(
        cell_label="Dried Meat",
        item_name="Dried Meat",
        expected_price=43,
        quantity=quantity,
        window="BURN",
        seller_id="seller-burn",
    )


def _fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def _purchase_plan(
    observation: Observation,
    action: PurchaseItemAction,
) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="purchase-controller-verdict",
        plan_version=1,
        objective="Buy one bounded quantity under one strategic choice.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[_fresh()],
        steps=[
            PlanStep(
                step_id="purchase",
                action=action,
                preconditions=[_fresh()],
                success_conditions=[],
                timeout_seconds=30.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        entry_step_id="purchase",
        max_actions=1,
        max_wall_seconds=60.0,
        max_game_seconds=60.0,
        risk_budget=RiskBudget(
            max_pointer_actions=action.quantity,
            max_purchase_actions=action.quantity,
            max_native_assisted_actions=0,
        ),
    )


def test_purchase_terminal_status_matches_every_bounded_quantity_pair() -> None:
    for requested_quantity in range(1, 6):
        for purchased_quantity in range(0, 6):
            for status in PurchaseStatus:
                valid = purchased_quantity <= requested_quantity and (
                    (
                        status is PurchaseStatus.OUTCOME_UNKNOWN
                        and purchased_quantity < requested_quantity
                    )
                    or (
                        status is PurchaseStatus.PURCHASED
                        and purchased_quantity == requested_quantity
                    )
                    or (
                        status is PurchaseStatus.PARTIALLY_PURCHASED
                        and 0 < purchased_quantity < requested_quantity
                    )
                    or (
                        status is PurchaseStatus.NOT_PURCHASED
                        and purchased_quantity == 0
                    )
                )
                arguments = {
                    "status": status,
                    "seller_id": "seller-burn",
                    "selected_character_id": "character-bark",
                    "item_name": "Dried Meat",
                    "requested_quantity": requested_quantity,
                    "purchased_quantity": purchased_quantity,
                    "money_before": 1000,
                    "inventory_quantity_before": 0,
                    "reason": "Finite-state invariant.",
                }
                if valid:
                    assert PurchaseEvidence(**arguments).status is status
                else:
                    with pytest.raises(ValueError):
                        PurchaseEvidence(**arguments)


def test_one_purchase_intent_transfers_its_bounded_quantity(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        action = _purchase(quantity=3)
        environment, telemetry, controller = purchase_environment(
            tmp_path,
            stock=3,
        )
        await environment.reset()
        transition = await environment.step(action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.purchase
        assert evidence is not None
        assert evidence.status.value == "purchased"
        assert evidence.requested_quantity == 3
        assert evidence.purchased_quantity == 3
        assert evidence.money_before == 1000
        assert evidence.money_after == 871
        assert evidence.inventory_quantity_before == 0
        assert evidence.inventory_quantity_after == 3
        assert transition.receipt.primitive_actions == 6
        assert telemetry.stock == 0
        assert telemetry.carried == 3
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
    ("stock", "inventory_updates", "expected_completed", "expected_status"),
    [
        (3, True, True, "purchased"),
        (2, True, False, "partially_purchased"),
        (3, False, False, "outcome_unknown"),
    ],
)
def test_continuous_executor_completes_only_the_full_purchase_terminal(
    tmp_path: Path,
    stock: int,
    inventory_updates: bool,
    expected_completed: bool,
    expected_status: str,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        action = _purchase(quantity=3)
        environment, _, _ = purchase_environment(
            tmp_path,
            stock=stock,
            inventory_updates=inventory_updates,
        )
        if not inventory_updates:
            environment._PURCHASE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        observation = await environment.reset()
        plan = _purchase_plan(observation, action)
        assert dialogue_interaction_policy_errors(plan, observation) == []

        store = WorldStateStore(clock=clock)
        store.publish(observation)
        logger = SessionLogger(
            tmp_path / f"{expected_status}.jsonl",
            "purchase-controller-verdict",
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
                    allow_action_kinds=["purchase_item"],
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


def test_stock_exhaustion_returns_partial_without_an_unbound_click(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        action = _purchase(quantity=3)
        environment, telemetry, controller = purchase_environment(
            tmp_path,
            stock=2,
        )
        await environment.reset()
        transition = await environment.step(action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.purchase
        assert evidence is not None
        assert evidence.status.value == "partially_purchased"
        assert evidence.requested_quantity == 3
        assert evidence.purchased_quantity == 2
        assert evidence.inventory_quantity_after == 2
        assert telemetry.stock == 0
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
        action = _purchase(quantity=3)
        environment, _, controller = purchase_environment(
            tmp_path,
            stock=3,
            inventory_updates=False,
        )
        environment._PURCHASE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        await environment.reset()
        transition = await environment.step(action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.purchase
        assert evidence is not None
        assert evidence.status.value == "outcome_unknown"
        assert evidence.purchased_quantity == 0
        assert evidence.money_before == 1000
        assert evidence.money_after == 957
        assert evidence.inventory_quantity_before == 0
        assert evidence.inventory_quantity_after == 0
        assert len(
            [
                item
                for item in controller.actions
                if isinstance(item, ClickAction)
                and item.button is MouseButton.RIGHT
            ]
        ) == 1

    asyncio.run(scenario())


def test_an_unaffordable_purchase_names_the_shortfall_and_sends_no_input(
    tmp_path: Path,
) -> None:
    """A purchase the purse cannot cover must not be discovered by clicking.

    The failure this replaces reported "later telemetry showed no purse or
    selected-inventory change" - the same sentence an out-of-stock cell, a
    stale binding and a right-click that simply missed all produce. An agent
    reading it cannot tell which of those to do something about, and one live
    run burned its remaining steps re-attempting a purchase for that reason.
    """

    async def scenario() -> None:
        action = _purchase(quantity=1)
        environment, _, controller = purchase_environment(
            tmp_path,
            stock=5,
            money=10,
        )
        await environment.reset()
        transition = await environment.step(action)

        message = transition.receipt.message or ""
        assert "costs 43" in message
        assert "purse holds 10" in message
        # No input at all: the answer was already in telemetry.
        assert not [
            item
            for item in controller.actions
            if isinstance(item, ClickAction) and item.button is MouseButton.RIGHT
        ]

    asyncio.run(scenario())


def test_a_purchase_that_clicks_and_moves_nothing_says_so_explicitly(
    tmp_path: Path,
) -> None:
    """Preconditions holding is itself the finding worth reporting.

    When the purse and the shelf were both fine and the transfer still did not
    happen, the fault is the mechanism rather than the resources, and the
    message has to say which so the next attempt is not a blind retry.
    """

    async def scenario() -> None:
        action = _purchase(quantity=1)
        environment, _, controller = purchase_environment(
            tmp_path,
            stock=3,
            no_effect=True,
        )
        environment._PURCHASE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        await environment.reset()
        transition = await environment.step(action)

        message = transition.receipt.message or ""
        assert "purse held 1000 against a price of 43" in message
        assert "the right-click itself moved nothing" in message
        # It really did try, unlike the unaffordable case.
        assert [
            item
            for item in controller.actions
            if isinstance(item, ClickAction) and item.button is MouseButton.RIGHT
        ]

    asyncio.run(scenario())
