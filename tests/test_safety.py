from pathlib import Path

import pytest

from kenshi_agent.config import MacroConfig, SafetyConfig
from kenshi_agent.models import ClickAction, Observation, SkillAction, TelemetrySnapshot, WaitAction
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


def test_live_skill_must_be_configured_and_allowlisted() -> None:
    macros = MacroRegistry(
        {"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])}
    )
    guard = ActionGuard(safety_config(), macros)
    observation = Observation(run_id="run", step_index=0, mode="live")
    action = guard.validate(SkillAction(name="open_map"), observation)
    assert action.kind == "skill"


def test_wait_limit() -> None:
    guard = ActionGuard(safety_config(), MacroRegistry({}))
    observation = Observation(run_id="run", step_index=0, mode="mock")
    with pytest.raises(SafetyViolation):
        guard.validate(WaitAction(seconds=4), observation)
