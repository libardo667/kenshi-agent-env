from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from operation_test_support import execute_operation, operation_port, plan_executor

from kenshi_agent.config import (
    CaptureConfig,
    ControlsConfig,
    PlanningConfig,
    RuntimeConfig,
    SafetyConfig,
)
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.live_plan_policy import live_plan_policy_errors
from kenshi_agent.models import (
    ActionOutcome,
    ActionOutcomeAssessment,
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
from kenshi_agent.non_progress import retry_state_fingerprint
from kenshi_agent.planning import PlanningClock
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.safety import OperationPolicy
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
    def __init__(
        self,
        *,
        stock: int,
        money: int = 1000,
        unit_price: int = 43,
        selected_inventory_accepts_item: bool = True,
        group_selection: bool = False,
    ) -> None:
        self.stock = stock
        self.carried = 0
        self.money = money
        self.unit_price = unit_price
        self.selected_inventory_accepts_item = selected_inventory_accepts_item
        self.group_selection = group_selection
        self.message_text: str | None = None
        self.sequence = 0
        self.max_age_seconds = 3.0
        self.path = Path("purchase-telemetry.json")

    def read(self) -> TelemetryRead:
        self.sequence += 1
        inventory = (
            [InventoryItem(name="Dried Meat", quantity=self.carried)] if self.carried else []
        )
        controls: list[VisibleUIControl] = [
            VisibleUIControl(
                label="BARK",
                role="text",
                window="BARK",
                bounds=_bounds(2),
            )
        ]
        controls.extend(
            (
                VisibleUIControl(
                    label="Dried Meat",
                    role="item",
                    window="BURN",
                    item_name="Dried Meat",
                    item_base_value=self.unit_price,
                    item_quantity=self.stock,
                    selected_inventory_accepts_item=(self.selected_inventory_accepts_item),
                    bounds=_bounds(0),
                ),
            )
            if self.stock
            else ()
        )
        if self.message_text is not None:
            controls.append(
                VisibleUIControl(
                    label=self.message_text,
                    role="text",
                    widget_name="ABC_MessageTextBox",
                    widget_type="EditBox",
                    bounds=_bounds(1),
                )
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
                *(
                    [
                        CharacterState(
                            id="character-plant",
                            name="Plant",
                            selected=True,
                            inventory=[InventoryItem(name="Dried Meat", quantity=9)],
                            inventory_complete=True,
                        )
                    ]
                    if self.group_selection
                    else []
                ),
                CharacterState(
                    id="character-bark",
                    name="Bark",
                    selected=True,
                    inventory=inventory,
                    inventory_complete=True,
                ),
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
                selected_character_id=(
                    "character-plant" if self.group_selection else "character-bark"
                ),
                selected_character_ids=(
                    ["character-bark", "character-plant"]
                    if self.group_selection
                    else ["character-bark"]
                ),
                visible_controls=controls,
                visible_controls_complete=True,
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
        message_on_no_effect: str | None = None,
        charged_unit_price: int | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.inventory_updates = inventory_updates
        # A right-click that lands on a live cell and moves nothing at all.
        # Observed in `live-price-check-20260730-132702` with the item both
        # affordable and in stock, and distinct from a partial transfer.
        self.no_effect = no_effect
        self.message_on_no_effect = message_on_no_effect
        self.charged_unit_price = (
            telemetry.unit_price if charged_unit_price is None else charged_unit_price
        )
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
            self.telemetry.money -= self.charged_unit_price
        elif (
            isinstance(action, ClickAction)
            and action.button is MouseButton.RIGHT
            and self.no_effect
        ):
            self.telemetry.message_text = self.message_on_no_effect
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
    unit_price: int = 43,
    charged_unit_price: int | None = None,
    no_effect: bool = False,
    message_on_no_effect: str | None = None,
    selected_inventory_accepts_item: bool = True,
    group_selection: bool = False,
) -> tuple[LiveEnvironment, PurchaseTelemetry, PurchaseController]:
    telemetry = PurchaseTelemetry(
        stock=stock,
        money=money,
        unit_price=unit_price,
        selected_inventory_accepts_item=selected_inventory_accepts_item,
        group_selection=group_selection,
    )
    controller = PurchaseController(
        telemetry,
        inventory_updates=inventory_updates,
        no_effect=no_effect,
        message_on_no_effect=message_on_no_effect,
        charged_unit_price=charged_unit_price,
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


def _purchase(*, quantity: int, expected_price: int = 43) -> PurchaseItemAction:
    return PurchaseItemAction(
        cell_label="Dried Meat",
        item_name="Dried Meat",
        expected_price=expected_price,
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
                    or (status is PurchaseStatus.NOT_PURCHASED and purchased_quantity == 0)
                )
                arguments = {
                    "status": status,
                    "seller_id": "seller-burn",
                    "selected_character_id": "character-bark",
                    "item_name": "Dried Meat",
                    "expected_price": 43,
                    "requested_quantity": requested_quantity,
                    "purchased_quantity": purchased_quantity,
                    "money_before": 1000,
                    "money_after": 1000 - 43 * purchased_quantity,
                    "inventory_quantity_before": 0,
                    "inventory_quantity_after": purchased_quantity,
                    "reason": "Finite-state invariant.",
                }
                if valid:
                    assert PurchaseEvidence(**arguments).status is status
                else:
                    with pytest.raises(ValueError):
                        PurchaseEvidence(**arguments)


def test_known_purchase_evidence_conserves_every_bounded_price_and_quantity() -> None:
    for expected_price in range(0, 6):
        for purchased_quantity in range(0, 6):
            requested_quantity = 5
            status = (
                PurchaseStatus.NOT_PURCHASED
                if purchased_quantity == 0
                else PurchaseStatus.PURCHASED
                if purchased_quantity == requested_quantity
                else PurchaseStatus.PARTIALLY_PURCHASED
            )
            evidence = PurchaseEvidence(
                status=status,
                seller_id="seller-burn",
                selected_character_id="character-bark",
                item_name="Dried Meat",
                expected_price=expected_price,
                requested_quantity=requested_quantity,
                purchased_quantity=purchased_quantity,
                money_before=100,
                money_after=100 - expected_price * purchased_quantity,
                inventory_quantity_before=7,
                inventory_quantity_after=7 + purchased_quantity,
                reason="Exact bounded conservation.",
            )
            assert evidence.purchased_quantity == purchased_quantity
            assert evidence.money_after is not None
            assert evidence.inventory_quantity_after is not None

            for field, wrong_value in (
                ("money_after", evidence.money_after - 1),
                ("inventory_quantity_after", evidence.inventory_quantity_after + 1),
            ):
                with pytest.raises(ValueError, match="known purchase"):
                    PurchaseEvidence.model_validate(
                        {**evidence.model_dump(mode="python"), field: wrong_value}
                    )


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
        transition = await execute_operation(environment, action)

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
        assert (
            len(
                [
                    item
                    for item in controller.actions
                    if isinstance(item, ClickAction) and item.button is MouseButton.RIGHT
                ]
            )
            == 3
        )

    asyncio.run(scenario())


def test_purchase_conservation_follows_the_open_inventory_owner_in_a_group(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, _ = purchase_environment(
            tmp_path,
            stock=1,
            group_selection=True,
        )
        await environment.reset()

        transition = await execute_operation(environment, _purchase(quantity=1))

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.purchase
        assert evidence is not None
        assert evidence.status is PurchaseStatus.PURCHASED
        assert evidence.selected_character_id == "character-bark"
        assert evidence.inventory_quantity_before == 0
        assert evidence.inventory_quantity_after == 1
        assert telemetry.carried == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("unit_price", "charged_unit_price", "expected_status"),
    [
        (43, 43, PurchaseStatus.PURCHASED),
        (0, 0, PurchaseStatus.PURCHASED),
        (43, 42, PurchaseStatus.OUTCOME_UNKNOWN),
        (0, 1, PurchaseStatus.OUTCOME_UNKNOWN),
    ],
)
def test_vendor_acquisition_conserves_the_exact_quoted_charge(
    tmp_path: Path,
    unit_price: int,
    charged_unit_price: int,
    expected_status: PurchaseStatus,
) -> None:
    async def scenario() -> None:
        action = _purchase(quantity=1, expected_price=unit_price)
        environment, _, controller = purchase_environment(
            tmp_path,
            stock=1,
            unit_price=unit_price,
            charged_unit_price=charged_unit_price,
        )
        environment._PURCHASE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        await environment.reset()
        transition = await execute_operation(environment, action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.purchase
        assert evidence is not None
        assert evidence.status is expected_status
        assert evidence.purchased_quantity == (
            1 if expected_status is PurchaseStatus.PURCHASED else 0
        )
        assert (
            len(
                [
                    primitive
                    for primitive in controller.actions
                    if isinstance(primitive, ClickAction) and primitive.button is MouseButton.RIGHT
                ]
            )
            == 1
        )

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
        assert live_plan_policy_errors(plan) == []

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

        executor = plan_executor(
            environment=environment,
            operation_port=operation_port(environment),
            policy=OperationPolicy(
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
        assert expected_status in (tmp_path / f"{expected_status}.jsonl").read_text(
            encoding="utf-8"
        )
        if expected_completed:
            assert result.reason == "Plan completed."
        else:
            assert expected_status in result.reason

    asyncio.run(scenario())


def test_operation_binding_rechecks_no_op_barrier_between_plan_steps(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        action = _purchase(quantity=1)
        environment, _, controller = purchase_environment(
            tmp_path,
            stock=3,
            no_effect=True,
            message_on_no_effect="No room for that item.",
            selected_inventory_accepts_item=False,
        )
        environment._PURCHASE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        observation = await environment.reset()
        first = (
            _purchase_plan(observation, action).steps[0].model_copy(update={"on_failure": "retry"})
        )
        second = first.model_copy(update={"step_id": "retry", "on_failure": None})
        plan = _purchase_plan(observation, action).model_copy(
            update={
                "steps": [first, second],
                "max_actions": 2,
                "risk_budget": RiskBudget(
                    max_pointer_actions=2,
                    max_purchase_actions=2,
                    max_native_assisted_actions=0,
                ),
            }
        )

        store = WorldStateStore(clock=clock)
        store.publish(observation)
        logger = SessionLogger(
            tmp_path / "same-plan-no-op.jsonl",
            "same-plan-no-op",
        )

        def observe_transition(
            active_plan: PlanEnvelope,
            step: PlanStep,
            before: Observation,
            transition: Transition,
            command_id: str,
            action_start_revision: WorldStateRevision,
        ) -> Observation:
            del before, command_id
            purchase = transition.receipt.semantic
            assert purchase is not None
            assert purchase.purchase is not None
            candidate = transition.observation
            fingerprint = retry_state_fingerprint(action, candidate)
            assert fingerprint is not None
            outcome = ActionOutcome(
                outcome_id="ao-1",
                run_id=candidate.run_id,
                plan_id=active_plan.plan_id,
                plan_version=active_plan.plan_version,
                step_id=step.step_id,
                step_index=candidate.step_index,
                intent="Attempt the bounded purchase.",
                action=action,
                executed=True,
                assessment=ActionOutcomeAssessment.NO_OP,
                causal_revision_advanced=True,
                semantic_status=purchase.purchase.status.value,
                feedback=purchase.purchase.reason,
                started_after_revision=action_start_revision,
                completed_at_revision=candidate.world_revision,
                identity_session_id="session-purchase",
                retry_state_fingerprint=fingerprint,
            )
            decorated = candidate.model_copy(update={"recent_action_outcomes": [outcome]})
            store.publish(candidate)
            return store.decorate_latest(decorated)

        executor = plan_executor(
            environment=environment,
            operation_port=operation_port(environment),
            policy=OperationPolicy(
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
                max_purchase_actions_per_plan=2,
            ),
        )
        try:
            result = await executor.execute(
                plan,
                observation,
                remaining_run_actions=2,
            )
        finally:
            logger.close()

        right_clicks = [
            primitive
            for primitive in controller.actions
            if isinstance(primitive, ClickAction) and primitive.button is MouseButton.RIGHT
        ]
        assert len(right_clicks) == 1
        assert result.actions_completed == 1
        assert "definitive no-op" in result.reason

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
        transition = await execute_operation(environment, action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.purchase
        assert evidence is not None
        assert evidence.status.value == "partially_purchased"
        assert evidence.requested_quantity == 3
        assert evidence.purchased_quantity == 2
        assert evidence.inventory_quantity_after == 2
        assert telemetry.stock == 0
        assert (
            len(
                [
                    item
                    for item in controller.actions
                    if isinstance(item, ClickAction) and item.button is MouseButton.RIGHT
                ]
            )
            == 2
        )

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
        transition = await execute_operation(environment, action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.purchase
        assert evidence is not None
        assert evidence.status.value == "outcome_unknown"
        assert evidence.purchased_quantity == 0
        assert evidence.money_before == 1000
        assert evidence.money_after == 957
        assert evidence.inventory_quantity_before == 0
        assert evidence.inventory_quantity_after == 0
        assert (
            len(
                [
                    item
                    for item in controller.actions
                    if isinstance(item, ClickAction) and item.button is MouseButton.RIGHT
                ]
            )
            == 1
        )

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
        transition = await execute_operation(environment, action)

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

    The numbers are stated in the past tense on purpose. They come from the
    binding, which can be a full conservation timeout stale by the time this
    is written - a live run produced this message claiming a cell held 1 while
    telemetry showed the trade window holding no item cells at all.
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
        transition = await execute_operation(environment, action)

        message = transition.receipt.message or ""
        assert "When the click was sent the purse held 1000" in message
        assert "price of 43" in message
        assert "neither the purse nor the shelf explains this" in message
        # It really did try, unlike the unaffordable case.
        assert [
            item
            for item in controller.actions
            if isinstance(item, ClickAction) and item.button is MouseButton.RIGHT
        ]

    asyncio.run(scenario())


def test_a_purchase_preserves_the_causally_new_game_refusal(
    tmp_path: Path,
) -> None:
    """The game explained this no-delta result; the controller must not erase it."""

    async def scenario() -> None:
        action = _purchase(quantity=1)
        environment, _, _ = purchase_environment(
            tmp_path,
            stock=3,
            no_effect=True,
            message_on_no_effect="No room for that item.",
        )
        environment._PURCHASE_OBSERVATION_TIMEOUT_SECONDS = 0.02
        await environment.reset()
        transition = await execute_operation(environment, action)

        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.purchase
        assert evidence is not None
        assert evidence.status is PurchaseStatus.NOT_PURCHASED
        assert "Kenshi refused the purchase: No room for that item." in evidence.reason
        assert "neither the purse nor the shelf explains this" not in evidence.reason

    asyncio.run(scenario())
