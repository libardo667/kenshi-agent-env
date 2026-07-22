import asyncio
import json
from pathlib import Path

from kenshi_agent.config import MacroConfig, MockConfig, SafetyConfig
from kenshi_agent.env import MockEnvironment
from kenshi_agent.memory import MemoryStore
from kenshi_agent.planners import HeuristicPlanner
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry


def test_full_mock_runtime_survives_one_day(tmp_path: Path) -> None:
    async def scenario() -> None:
        run_id = "runtime-test"
        environment = MockEnvironment(
            MockConfig(seed=11, random_events=False),
            tmp_path / "frames",
            run_id,
        )
        macros = MacroRegistry(
            {"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])}
        )
        safety = SafetyConfig(
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
            max_actions_per_minute=500,
        )
        logger = SessionLogger(tmp_path / "events.jsonl", run_id)
        memory = MemoryStore(tmp_path / "memory.sqlite3", "test")
        try:
            runtime = AgentRuntime(
                run_id=run_id,
                environment=environment,
                planner=HeuristicPlanner(),
                guard=ActionGuard(safety, macros),
                reflexes=ReflexEngine(),
                logger=logger,
                memory=memory,
                memory_limit=12,
                minimum_memory_salience=0.0,
            )
            summary = await runtime.run(max_steps=30)
            assert summary.success is True
            assert summary.steps_completed < 30
            event_lines = (tmp_path / "events.jsonl").read_text().splitlines()
            events = [json.loads(line) for line in event_lines]
            decisions = [event for event in events if event["event_type"] == "decision"]
            assert decisions
            assert decisions[0]["payload"]["planner_latency_seconds"] >= 0.0
        finally:
            logger.close()
            memory.close()

    asyncio.run(scenario())
