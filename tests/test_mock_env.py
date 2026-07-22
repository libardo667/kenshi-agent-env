import asyncio
from pathlib import Path

from kenshi_agent.config import MockConfig
from kenshi_agent.env import MockEnvironment
from kenshi_agent.models import PauseAction, SetSpeedAction, SkillAction


def test_mock_environment_state_transitions(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment = MockEnvironment(MockConfig(random_events=False), tmp_path, "run")
        observation = await environment.reset()
        assert observation.telemetry is not None
        assert observation.telemetry.game.paused is True

        await environment.step(PauseAction(paused=False))
        await environment.step(SetSpeedAction(speed=3))
        transition = await environment.step(SkillAction(name="work_for_cats"))
        assert transition.observation.telemetry is not None
        assert transition.observation.telemetry.game.money == 300
        assert transition.observation.telemetry.game.elapsed_minutes == 180
        assert transition.observation.screenshot_path is not None
        assert transition.observation.screenshot_path.exists()

    asyncio.run(scenario())
