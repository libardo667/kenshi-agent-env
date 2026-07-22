import asyncio
import json
from pathlib import Path

from kenshi_agent.env import ReplayEnvironment
from kenshi_agent.models import NoopAction, Observation


def test_replay_environment_returns_recorded_observations(tmp_path: Path) -> None:
    observations = [
        Observation(run_id="source", step_index=0, mode="mock"),
        Observation(run_id="source", step_index=1, mode="mock", events=["next"]),
    ]
    path = tmp_path / "events.jsonl"
    lines = [
        json.dumps(
            {
                "event_type": "observation",
                "payload": observation.model_dump(mode="json"),
            }
        )
        for observation in observations
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def scenario() -> None:
        environment = ReplayEnvironment(path)
        first = await environment.reset()
        assert first.mode == "replay"
        transition = await environment.step(NoopAction())
        assert transition.observation.step_index == 1
        assert transition.terminated

    asyncio.run(scenario())
