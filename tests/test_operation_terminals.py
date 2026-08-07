from __future__ import annotations

from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    ControlMode,
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





def test_receipt_terminal_controls_do_not_need_a_fictional_world_effect() -> None:
    completion = runtime_control_terminal(WaitAction(seconds=0.5))

    assert completion is not None
    assert completion.owner is TerminalOwner.CONTROLLER_TERMINAL
    assert completion.conditions == ()
