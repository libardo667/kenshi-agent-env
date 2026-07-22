import pytest

from kenshi_agent.config import MacroConfig, SafetyConfig
from kenshi_agent.models import (
    ClickAction,
    CoordinateSpace,
    GameState,
    MoveCursorAction,
    Observation,
    PauseAction,
    SkillAction,
    TelemetrySnapshot,
    UIState,
    WaitAction,
)
from kenshi_agent.safety import ActionGuard, SafetyViolation
from kenshi_agent.skills import MacroRegistry


def safety_config() -> SafetyConfig:
    return SafetyConfig(
        allow_action_kinds=[
            "noop",
            "stop",
            "pause",
            "set_speed",
            "wait",
            "key",
            "hotkey",
            "click",
            "move_cursor",
            "skill",
        ],
        allow_skills=["open_map"],
        max_wait_seconds=3.0,
        max_actions_per_minute=100,
    )


def test_normalized_click_outside_bounds_is_blocked() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(run_id="run", step_index=0, mode="mock")
    with pytest.raises(SafetyViolation):
        guard.validate(ClickAction(x=1.1, y=0.5), observation)


def test_stale_live_click_is_blocked() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(),
        telemetry_stale=True,
    )
    with pytest.raises(SafetyViolation):
        guard.validate(ClickAction(x=0.5, y=0.5), observation)


def test_live_screen_space_pointer_action_is_blocked() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="Screen-space"):
        guard.validate(
            ClickAction(x=100, y=100, space=CoordinateSpace.SCREEN), observation
        )


def test_live_move_cursor_uses_the_same_bounds_as_clicks() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="outside the Kenshi window"):
        guard.validate(
            MoveCursorAction(x=1280, y=20, space=CoordinateSpace.CLIENT), observation
        )


def test_live_client_pointer_requires_known_window_dimensions() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(
        run_id="run", step_index=0, mode="live", telemetry=TelemetrySnapshot()
    )
    with pytest.raises(SafetyViolation, match="dimensions are unknown"):
        guard.validate(ClickAction(x=20, y=20, space=CoordinateSpace.CLIENT), observation)


def test_live_pause_requires_known_current_state() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    unknown = Observation(
        run_id="run", step_index=0, mode="live", telemetry=TelemetrySnapshot()
    )
    with pytest.raises(SafetyViolation, match="pause state is unknown"):
        guard.validate(PauseAction(paused=True), unknown)

    known = unknown.model_copy(
        update={"telemetry": TelemetrySnapshot(game=GameState(paused=False))}
    )
    assert guard.validate(PauseAction(paused=True), known).paused is True


def test_live_skill_must_be_configured_and_allowlisted() -> None:
    macros = MacroRegistry(
        {"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])}
    )
    guard = ActionGuard(safety_config(), macros)
    observation = Observation(run_id="run", step_index=0, mode="live")
    action = guard.validate(SkillAction(name="open_map"), observation)
    assert action.kind == "skill"


def test_live_skill_primitives_receive_pointer_validation() -> None:
    macros = MacroRegistry(
        {
            "open_map": MacroConfig(
                actions=[
                    {"kind": "click", "x": 100, "y": 100, "space": "screen"}
                ]
            )
        }
    )
    guard = ActionGuard(safety_config(), macros)
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="Screen-space"):
        guard.validate(SkillAction(name="open_map"), observation)


def test_wait_limit() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(run_id="run", step_index=0, mode="mock")
    with pytest.raises(SafetyViolation):
        guard.validate(WaitAction(seconds=4), observation)
