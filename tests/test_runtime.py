import asyncio
import json
from pathlib import Path

from PIL import Image

from kenshi_agent.config import MacroConfig, MockConfig, SafetyConfig
from kenshi_agent.env import AgentEnvironment, MockEnvironment
from kenshi_agent.memory import MemoryStore
from kenshi_agent.models import (
    Action,
    ActionReceipt,
    Observation,
    PlannerDecision,
    SkillAction,
    TelemetrySnapshot,
    Transition,
    WorldStateRevision,
)
from kenshi_agent.planners import HeuristicPlanner
from kenshi_agent.planners.base import Planner
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
        macros = MacroRegistry({"open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}])})
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
            assert summary.control_mode == "interface_only"
            assert summary.steps_completed < 30
            event_lines = (tmp_path / "events.jsonl").read_text().splitlines()
            events = [json.loads(line) for line in event_lines]
            decisions = [event for event in events if event["event_type"] == "decision"]
            assert decisions
            assert decisions[0]["payload"]["planner_latency_seconds"] >= 0.0
            started = next(event for event in events if event["event_type"] == "run_started")
            finished = next(event for event in events if event["event_type"] == "run_finished")
            receipt = next(event for event in events if event["event_type"] == "action_receipt")
            assert started["payload"]["control_mode"] == "interface_only"
            assert finished["payload"]["control_mode"] == "interface_only"
            assert receipt["payload"]["control_mode"] == "interface_only"
        finally:
            logger.close()
            memory.close()

    asyncio.run(scenario())


def test_runtime_carries_bounded_noop_feedback_between_decisions(
    tmp_path: Path,
) -> None:
    class RepeatingPlanner(Planner):
        def __init__(self) -> None:
            self.observations: list[Observation] = []

        async def decide(self, observation: Observation) -> PlannerDecision:
            self.observations.append(observation)
            return PlannerDecision(
                intent="Try the same camera recovery.",
                rationale="The view still looks obstructed.",
                action=SkillAction(name="camera_recovery"),
                confidence=0.9,
            )

    class UnchangingEnvironment(AgentEnvironment):
        def __init__(self, screenshot_path: Path) -> None:
            self.step_index = 0
            self.actions: list[Action] = []
            self.screenshot_path = screenshot_path

        def observation(self) -> Observation:
            return Observation(
                run_id="stagnation-test",
                step_index=self.step_index,
                mode="mock",
                world_revision=WorldStateRevision(
                    frame_sequence=self.step_index,
                ),
                screenshot_path=self.screenshot_path,
                screenshot_sha256="unchanged-frame",
                available_skills=["camera_recovery"],
            )

        async def reset(self, *, seed: int | None = None) -> Observation:
            return self.observation()

        async def observe(self) -> Observation:
            return self.observation()

        async def step(self, action: Action) -> Transition:
            self.actions.append(action)
            self.step_index += 1
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                ),
                observation=self.observation(),
            )

        async def close(self) -> None:
            return None

    async def scenario() -> None:
        run_id = "stagnation-test"
        screenshot_path = tmp_path / "unchanged.png"
        Image.new("RGB", (320, 180), "black").save(screenshot_path)
        environment = UnchangingEnvironment(screenshot_path)
        planner = RepeatingPlanner()
        macros = MacroRegistry(
            {"camera_recovery": MacroConfig(actions=[{"kind": "key", "key": "f"}])}
        )
        safety = SafetyConfig(
            allow_action_kinds=["skill", "stop"],
            allow_skills=["camera_recovery"],
            max_actions_per_minute=500,
        )
        logger = SessionLogger(tmp_path / "stagnation-events.jsonl", run_id)
        try:
            runtime = AgentRuntime(
                run_id=run_id,
                environment=environment,
                planner=planner,
                guard=ActionGuard(safety, macros),
                reflexes=ReflexEngine(),
                logger=logger,
                memory=None,
                memory_limit=0,
                minimum_memory_salience=0.0,
                action_outcome_limit=2,
            )
            summary = await runtime.run(max_steps=4)
        finally:
            logger.close()

        assert not summary.terminated
        assert len(environment.actions) == 4
        assert len(planner.observations[0].recent_action_outcomes) == 0
        assert len(planner.observations[1].recent_action_outcomes) == 1
        assert len(planner.observations[-1].recent_action_outcomes) == 2
        latest = planner.observations[-1].recent_action_outcomes[-1]
        assert latest.assessment == "no_op"
        assert latest.visual_change_fraction == 0.0
        assert "do not repeat" in latest.feedback
        events = [
            json.loads(line)
            for line in (tmp_path / "stagnation-events.jsonl").read_text().splitlines()
        ]
        outcomes = [event for event in events if event["event_type"] == "action_outcome"]
        assert len(outcomes) == 4
        assert outcomes[-1]["payload"]["assessment"] == "no_op"

    asyncio.run(scenario())


def test_interaction_requires_movement_or_dialogue_not_ambient_frame_change() -> None:
    receipt = ActionReceipt(
        action=SkillAction(name="interact_visible_person"),
        accepted=True,
        executed=True,
        dry_run=False,
    )

    assessment, feedback = AgentRuntime._assess_outcome(
        receipt,
        None,
        visual_change=0.5,
        telemetry_changes=["visible entities disappeared: Nomad"],
        movement_distance=0.0,
    )

    assert assessment == "no_op"
    assert "opened no dialogue or trade" in feedback


def test_telemetry_changes_report_vendor_route_progress() -> None:
    before = TelemetrySnapshot.model_validate(
        {
            "nearby_entities": [
                {
                    "id": "nearby:3",
                    "name": "Barman",
                    "kind": "character",
                    "is_animal": False,
                    "has_vendor_list": True,
                    "is_squad_leader": True,
                    "has_dialogue": True,
                    "faction": "Trade Ninjas",
                    "disposition": "neutral",
                    "distance": 96.0,
                    "camera_bearing_degrees": -70.0,
                }
            ]
        }
    )
    after = TelemetrySnapshot.model_validate(
        {
            "nearby_entities": [
                {
                    "id": "nearby:8",
                    "name": "Barman",
                    "kind": "character",
                    "is_animal": False,
                    "has_vendor_list": True,
                    "is_squad_leader": True,
                    "has_dialogue": True,
                    "faction": "Trade Ninjas",
                    "disposition": "neutral",
                    "distance": 82.0,
                    "camera_bearing_degrees": -25.0,
                }
            ]
        }
    )

    changes = AgentRuntime._telemetry_changes(before, after)

    assert "distance to Barman: 96.00 -> 82.00 (14.00 closer)" in changes
    assert "camera bearing to Barman: -70.0 -> -25.0 degrees" in changes


def test_purchase_outcome_requires_money_and_food_confirmation() -> None:
    receipt = ActionReceipt(
        action=SkillAction(name="buy_inspected_shop_item"),
        accepted=True,
        executed=True,
        dry_run=False,
    )

    verified = AgentRuntime._assess_outcome(
        receipt,
        TelemetrySnapshot(),
        visual_change=0.1,
        telemetry_changes=["money: 1000 -> 351", "food items: 0 -> 1"],
        movement_distance=0.0,
    )
    unverified = AgentRuntime._assess_outcome(
        receipt,
        TelemetrySnapshot(),
        visual_change=0.1,
        telemetry_changes=["money: 1000 -> 351"],
        movement_distance=0.0,
    )

    assert verified[0] == "changed"
    assert "Purchase verified" in verified[1]
    assert unverified[0] == "no_op"
