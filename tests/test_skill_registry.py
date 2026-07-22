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
    actions = registry.expand(SkillAction(name="click_at", args={"x": 0.2, "y": 0.3}))
    assert len(actions) == 1
    assert isinstance(actions[0], ClickAction)
    assert actions[0].x == 0.2
