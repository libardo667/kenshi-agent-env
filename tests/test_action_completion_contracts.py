from __future__ import annotations

from kenshi_agent.action_contracts import (
    CompletionOwner,
    completion_contract_for,
)
from kenshi_agent.models import (
    Action,
    ActivateVisibleControlAction,
    ConditionOperator,
    ControlMode,
    DismissScreenAction,
    GameBinding,
    GameState,
    Observation,
    PauseAction,
    PurchaseItemAction,
    SellItemAction,
    SetSpeedAction,
    TelemetrySnapshot,
    UIState,
    UseGameBindingAction,
    WaitAction,
    WorldStateRevision,
)


def observation() -> Observation:
    return Observation(
        run_id="completion-contract",
        step_index=1,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(
            telemetry_sequence=7,
            capability_epoch=1,
        ),
        telemetry=TelemetrySnapshot(
            sequence=7,
            capabilities=[
                "game.money",
                "game.pause",
                "game.speed",
                "ui.inventory",
            ],
            game=GameState(
                loaded=True,
                paused=False,
                speed_multiplier=1.0,
                money=1000,
                elapsed_minutes=10.0,
            ),
            ui=UIState(
                active_screen="trade",
                open_inventory_windows=2,
                management_screen_open=False,
                stats_window_open=False,
            ),
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.1,
    )


def assert_one_runtime_condition(
    action: Action,
    *,
    path: str,
    operator: ConditionOperator,
    expected: int | float | bool,
) -> None:
    completion = completion_contract_for(action, observation())

    assert completion.owner is CompletionOwner.RUNTIME_CONDITIONS
    assert len(completion.conditions) == 1
    condition = completion.conditions[0]
    assert condition.path == path
    assert condition.operator is operator
    assert condition.expected == expected


def test_mechanical_effects_are_owned_by_one_completion_boundary() -> None:
    """Actions do not make the planner restate effects the runtime already knows."""

    purchase = completion_contract_for(
        PurchaseItemAction(
            cell_label="item_0",
            item_name="Dried Meat",
            expected_price=38,
            quantity=3,
            window="BARMAN",
            seller_id="entity-barman",
        ),
        observation(),
    )
    assert purchase.owner is CompletionOwner.CONTROLLER_TERMINAL
    assert purchase.conditions == ()
    sale = completion_contract_for(
        SellItemAction(
            cell_label="item_4",
            item_name="Iron Club",
            quantity=3,
            window="HIROTO",
            buyer_id="entity-barman",
        ),
        observation(),
    )
    assert sale.owner is CompletionOwner.CONTROLLER_TERMINAL
    assert sale.conditions == ()
    assert_one_runtime_condition(
        DismissScreenAction(expected_screen="trade", window="BARMAN"),
        path="telemetry.ui.open_inventory_windows",
        operator=ConditionOperator.LESS_THAN,
        expected=2,
    )
    assert_one_runtime_condition(
        UseGameBindingAction(
            binding=GameBinding.TOGGLE_INVENTORY,
            expected_effect="open the selected character inventory",
        ),
        path="telemetry.ui.open_inventory_windows",
        operator=ConditionOperator.NOT_EQUALS,
        expected=2,
    )
    assert_one_runtime_condition(
        PauseAction(paused=True),
        path="telemetry.game.paused",
        operator=ConditionOperator.EQUALS,
        expected=True,
    )

    speed = completion_contract_for(SetSpeedAction(speed=3), observation())
    assert speed.owner is CompletionOwner.RUNTIME_CONDITIONS
    assert [
        (condition.path, condition.operator, condition.expected)
        for condition in speed.conditions
    ] == [
        (
            "telemetry.game.paused",
            ConditionOperator.EQUALS,
            False,
        ),
        (
            "telemetry.game.speed_multiplier",
            ConditionOperator.EQUALS,
            5.0,
        ),
    ]


def test_only_genuinely_ambiguous_internal_effects_need_explicit_step_conditions() -> None:
    state = observation()

    assert completion_contract_for(
        ActivateVisibleControlAction(exact_label="Goodbye.", role="button"),
        state,
    ).owner is CompletionOwner.STEP_CONDITIONS
    assert completion_contract_for(
        UseGameBindingAction(
            binding=GameBinding.CAMERA_FORWARD,
            expected_effect="move the camera forward",
        ),
        state,
    ).owner is CompletionOwner.STEP_CONDITIONS
    assert completion_contract_for(
        DismissScreenAction(expected_screen="trade"),
        state,
    ).owner is CompletionOwner.STEP_CONDITIONS


def test_selected_affordance_never_delegates_ambiguous_completion_to_model() -> None:
    completion = completion_contract_for(
        ActivateVisibleControlAction(exact_label="Goodbye.", role="button"),
        observation(),
        selected_affordance=True,
    )

    assert completion.owner is CompletionOwner.AFFORDANCE_DELIVERY
    assert completion.conditions == ()


def test_receipt_terminal_controls_do_not_need_a_fictional_world_effect() -> None:
    completion = completion_contract_for(WaitAction(seconds=0.5), observation())

    assert completion.owner is CompletionOwner.CONTROLLER_TERMINAL
    assert completion.conditions == ()
