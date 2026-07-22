import pytest

from kenshi_agent.config import MacroConfig
from kenshi_agent.models import ClickAction, SkillAction
from kenshi_agent.skills import MacroRegistry


def test_skill_arguments_render_into_actions() -> None:
    registry = MacroRegistry(
        {
            "click_at": MacroConfig(
                actions=[
                    {
                        "kind": "click",
                        "x": "{{x}}",
                        "y": "{{y}}",
                        "space": "normalized",
                    }
                ]
            )
        }
    )
    actions = registry.expand(
        SkillAction(name="click_at", args={"x": 0.2, "y": 0.3})  # type: ignore[arg-type]
    )
    assert len(actions) == 1
    assert isinstance(actions[0], ClickAction)
    assert actions[0].x == 0.2


def test_skill_specs_expose_arguments_and_visual_preconditions() -> None:
    registry = MacroRegistry(
        {
            "move_visible_terrain": MacroConfig(
                description="Fine movement in the world.",
                arguments={"x": "Normalized x coordinate."},
                visual_precondition="The map is closed.",
                movement_pulse_seconds=0.75,
                movement_pulse_min_seconds=0.35,
                movement_pulse_max_seconds=3.0,
                actions=[],
            )
        }
    )

    spec = registry.specs()[0]

    assert spec.name == "move_visible_terrain"
    assert spec.arguments == {"x": "Normalized x coordinate."}
    assert spec.visual_precondition == "The map is closed."
    assert spec.movement_pulse_seconds == 0.75
    assert spec.movement_pulse_min_seconds == 0.35
    assert spec.movement_pulse_max_seconds == 3.0
    assert registry.primitive_count(SkillAction(name="move_visible_terrain")) == 2


def test_movement_duration_must_stay_within_calibrated_range() -> None:
    registry = MacroRegistry(
        {
            "move": MacroConfig(
                movement_pulse_seconds=1.0,
                movement_pulse_min_seconds=0.5,
                movement_pulse_max_seconds=2.0,
            )
        }
    )

    action = SkillAction.model_validate({"name": "move", "args": {"duration_seconds": 1.5}})
    assert registry.resolve_movement_pulse_seconds(action) == 1.5

    too_long = SkillAction.model_validate({"name": "move", "args": {"duration_seconds": 3.0}})
    with pytest.raises(ValueError, match="outside the calibrated range"):
        registry.resolve_movement_pulse_seconds(too_long)
