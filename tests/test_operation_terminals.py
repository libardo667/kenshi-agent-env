from __future__ import annotations

from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    ActivateVisibleControlAction,
    ControlMode,
    DismissScreenAction,
    GameBinding,
    UseGameBindingAction,
    WaitAction,
)
from kenshi_agent.core.planning import ConditionOperator
from kenshi_agent.core.telemetry import (
    GameState,
    TelemetrySnapshot,
    UIState,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.operation_definitions import (
    ACTIVATE_VISIBLE_CONTROL_DEFINITION,
    DISMISS_SCREEN_DEFINITION,
    USE_GAME_BINDING_DEFINITION,
    OperationTerminal,
    TerminalOwner,
    runtime_control_terminal,
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
    completion: OperationTerminal,
    *,
    path: str,
    operator: ConditionOperator,
    expected: int | float | bool,
) -> None:
    assert completion.owner is TerminalOwner.RUNTIME_CONDITIONS
    assert len(completion.conditions) == 1
    condition = completion.conditions[0]
    assert condition.path == path
    assert condition.operator is operator
    assert condition.expected == expected



def test_only_genuinely_ambiguous_internal_effects_need_explicit_step_conditions() -> None:
    state = observation()

    visible = ActivateVisibleControlAction(exact_label="Goodbye.", role="button")
    assert (
        ACTIVATE_VISIBLE_CONTROL_DEFINITION.resolve_terminal(visible, state).owner
        is TerminalOwner.STEP_CONDITIONS
    )
    binding = UseGameBindingAction(
        binding=GameBinding.CAMERA_FORWARD,
        expected_effect="move the camera forward",
    )
    assert (
        USE_GAME_BINDING_DEFINITION.resolve_terminal(binding, state).owner
        is TerminalOwner.STEP_CONDITIONS
    )
    dismiss = DismissScreenAction(expected_screen="trade")
    assert (
        DISMISS_SCREEN_DEFINITION.resolve_terminal(dismiss, state).owner
        is TerminalOwner.STEP_CONDITIONS
    )


def test_selected_affordance_never_delegates_ambiguous_completion_to_model() -> None:
    action = ActivateVisibleControlAction(exact_label="Goodbye.", role="button")
    completion = ACTIVATE_VISIBLE_CONTROL_DEFINITION.resolve_terminal(
        action,
        observation(),
        selected_affordance=True,
    )

    assert completion.owner is TerminalOwner.AFFORDANCE_DELIVERY
    assert completion.conditions == ()


def test_receipt_terminal_controls_do_not_need_a_fictional_world_effect() -> None:
    completion = runtime_control_terminal(WaitAction(seconds=0.5))

    assert completion is not None
    assert completion.owner is TerminalOwner.CONTROLLER_TERMINAL
    assert completion.conditions == ()
