from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from kenshi_agent.config import MacroConfig, PlanningConfig, SafetyConfig
from kenshi_agent.env import AgentEnvironment
from kenshi_agent.evals import evaluate_log, replay_plan_lifecycle
from kenshi_agent.models import (
    Action,
    ActionReceipt,
    Condition,
    ConditionKind,
    ConditionOperator,
    ControlMode,
    Disposition,
    GameState,
    IdempotencyPolicy,
    NearbyEntity,
    Observation,
    PauseAction,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanStep,
    RiskBudget,
    SetSpeedAction,
    StopAction,
    TelemetrySnapshot,
    Transition,
    WorldStateRevision,
)
from kenshi_agent.planners.base import Planner
from kenshi_agent.planning import PlanningClock
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry


class FakeClock(PlanningClock):
    def __init__(self) -> None:
        self.now = 1.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class RevisionEnvironment(AgentEnvironment):
    def __init__(
        self,
        *,
        clock: FakeClock,
        change_money_after_first_action: bool = False,
        advance_revision: bool = True,
        threat_after_first_action: bool = False,
    ) -> None:
        self.clock = clock
        self.change_money_after_first_action = change_money_after_first_action
        self.advance_revision = advance_revision
        self.threat_after_first_action = threat_after_first_action
        self.sequence = 1
        self.step_index = 0
        self.paused = True
        self.speed = 1.0
        self.money = 180
        self.actions: list[Action] = []

    def observation(self) -> Observation:
        return Observation(
            run_id="continuous",
            step_index=self.step_index,
            mode="mock",
            control_mode=ControlMode.INTERFACE_ONLY,
            planning_mode=PlanningMode.CONTINUOUS,
            world_revision=WorldStateRevision(
                telemetry_sequence=self.sequence,
                frame_sequence=self.sequence,
                capability_epoch=1,
                observed_at_monotonic=self.clock.monotonic(),
            ),
            telemetry=TelemetrySnapshot(
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                capabilities=["game.pause", "game.speed", "game.money", "game.time"],
                game=GameState(
                    loaded=True,
                    paused=self.paused,
                    speed_multiplier=self.speed,
                    money=self.money,
                    elapsed_minutes=0.0,
                ),
                nearby_entities=(
                    [
                        NearbyEntity(
                            id="threat",
                            name="Immediate threat",
                            disposition=Disposition.HOSTILE,
                            distance=10.0,
                            visible=True,
                        )
                    ]
                    if self.threat_after_first_action and self.actions
                    else []
                ),
            ),
            telemetry_age_seconds=0.0,
            telemetry_stale=False,
        )

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        return self.observation()

    async def observe(self) -> Observation:
        return self.observation()

    async def step(self, action: Action) -> Transition:
        self.actions.append(action)
        if isinstance(action, PauseAction):
            self.paused = action.paused
        elif isinstance(action, SetSpeedAction):
            self.speed = float(action.speed)
        self.step_index += 1
        if self.change_money_after_first_action and len(self.actions) == 1:
            self.money = 0
        if self.advance_revision:
            self.sequence += 1
        receipt = ActionReceipt(
            action=action,
            control_mode=ControlMode.INTERFACE_ONLY,
            accepted=True,
            executed=not isinstance(action, StopAction),
            dry_run=False,
            primitive_actions=0 if isinstance(action, StopAction) else 1,
            message="fake execution",
        )
        return Transition(
            receipt=receipt,
            observation=self.observation(),
            terminated=isinstance(action, StopAction),
        )

    async def close(self) -> None:
        return None


def condition(
    path: str,
    expected: str | int | float | bool,
    capability: str | None = None,
) -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=path,
        operator=ConditionOperator.EQUALS,
        expected=expected,
        max_age_seconds=3.0,
        required_capabilities=[capability] if capability else [],
    )


def fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def two_step_plan(
    observation: Observation,
    *,
    second_preconditions: list[Condition] | None = None,
    first_timeout_seconds: float = 1.0,
) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="two-step-proof",
        plan_version=1,
        objective="Resume and accelerate the mock world.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="resume",
                action=PauseAction(paused=False),
                preconditions=[condition("telemetry.game.paused", True, "game.pause")],
                success_conditions=[condition("telemetry.game.paused", False, "game.pause")],
                failure_conditions=[],
                timeout_seconds=first_timeout_seconds,
                retry_budget=0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                on_success="accelerate",
            ),
            PlanStep(
                step_id="accelerate",
                action=SetSpeedAction(speed=3),
                preconditions=second_preconditions
                or [condition("telemetry.game.paused", False, "game.pause")],
                success_conditions=[
                    condition(
                        "telemetry.game.speed_multiplier",
                        3.0,
                        "game.speed",
                    )
                ],
                failure_conditions=[],
                timeout_seconds=1.0,
                retry_budget=0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            ),
        ],
        entry_step_id="resume",
        max_actions=2,
        max_wall_seconds=4.0,
        max_game_seconds=5.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )


class PlanThenStopPlanner(Planner):
    def __init__(
        self,
        *,
        second_preconditions: list[Condition] | None = None,
        stale_basis: bool = False,
        first_timeout_seconds: float = 1.0,
    ) -> None:
        self.second_preconditions = second_preconditions
        self.stale_basis = stale_basis
        self.first_timeout_seconds = first_timeout_seconds
        self.calls = 0
        self.observations: list[Observation] = []

    async def decide(self, observation: Observation) -> PlannerOutput:
        self.calls += 1
        self.observations.append(observation)
        if self.calls > 1:
            return PlannerDecision(
                intent="Stop after the bounded plan cannot continue.",
                rationale="The continuous executor requested a safe replan.",
                action=StopAction(reason="Continuous test complete."),
                confidence=1.0,
            )
        plan = two_step_plan(
            observation,
            second_preconditions=self.second_preconditions,
            first_timeout_seconds=self.first_timeout_seconds,
        )
        if self.stale_basis:
            plan = plan.model_copy(
                update={
                    "based_on_revision": plan.based_on_revision.model_copy(
                        update={"telemetry_sequence": 0, "frame_sequence": 0}
                    )
                }
            )
        return plan


def runtime_for(
    tmp_path: Path,
    environment: RevisionEnvironment,
    planner: Planner,
    clock: FakeClock,
) -> tuple[AgentRuntime, SessionLogger]:
    macros = MacroRegistry({"unused": MacroConfig(actions=[{"kind": "key", "key": "u"}])})
    safety = SafetyConfig(
        allow_action_kinds=["pause", "set_speed", "stop"],
        max_actions_per_minute=500,
    )
    logger = SessionLogger(tmp_path / "events.jsonl", "continuous")
    runtime = AgentRuntime(
        run_id="continuous",
        environment=environment,
        planner=planner,
        guard=ActionGuard(safety, macros),
        reflexes=ReflexEngine(),
        logger=logger,
        memory=None,
        memory_limit=0,
        minimum_memory_salience=0.0,
        planning_config=PlanningConfig(
            mode=PlanningMode.CONTINUOUS,
            max_plan_steps=4,
            max_actions_per_plan=8,
            max_plan_wall_seconds=30.0,
            max_plan_game_seconds=12.0,
            observation_poll_seconds=0.1,
        ),
        planning_clock=clock,
    )
    return runtime, logger


def read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_one_strategic_call_executes_two_guarded_actions_and_replays(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = PlanThenStopPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert summary.steps_completed == 2
        assert planner.calls == 1
        assert [type(action) for action in environment.actions] == [
            PauseAction,
            SetSpeedAction,
        ]
        assert planner.observations[0].planning_mode is PlanningMode.CONTINUOUS

        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.strategic_planner_calls == 1
        assert metrics.plans_completed == 1
        assert metrics.plan_steps_succeeded == 2
        assert metrics.actions_per_strategic_planner_call == 2.0

        replayed = replay_plan_lifecycle(tmp_path / "events.jsonl")
        assert replayed["two-step-proof"].status == "completed"
        assert replayed["two-step-proof"].succeeded_step_ids == [
            "resume",
            "accelerate",
        ]

    asyncio.run(scenario())


def test_changed_precondition_cancels_future_action_before_execution(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(
            clock=clock,
            change_money_after_first_action=True,
        )
        planner = PlanThenStopPlanner(
            second_preconditions=[condition("telemetry.game.money", 180, "game.money")]
        )
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=3)
        finally:
            logger.close()

        assert summary.terminated
        assert not any(isinstance(action, SetSpeedAction) for action in environment.actions)
        events = read_events(tmp_path / "events.jsonl")
        cancelled = [event for event in events if event["event_type"] == "plan_step_cancelled"]
        assert len(cancelled) == 1
        payload = cancelled[0]["payload"]
        assert isinstance(payload, dict)
        assert payload["step_id"] == "accelerate"
        assert "precondition" in str(payload["reason"])

    asyncio.run(scenario())


def test_old_but_fresh_revision_cannot_confirm_postcondition(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock, advance_revision=False)
        planner = PlanThenStopPlanner(first_timeout_seconds=0.2)
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert environment.paused is False
        assert not any(isinstance(action, SetSpeedAction) for action in environment.actions)
        events = read_events(tmp_path / "events.jsonl")
        failed = [event for event in events if event["event_type"] == "plan_step_failed"]
        assert len(failed) == 1
        assert "later world revision" in str(failed[0]["payload"])

    asyncio.run(scenario())


def test_stale_plan_output_is_rejected_without_executing_an_action(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = PlanThenStopPlanner(stale_basis=True)
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert summary.terminated
        assert environment.actions == []
        events = read_events(tmp_path / "events.jsonl")
        rejected = [event for event in events if event["event_type"] == "plan_rejected"]
        assert len(rejected) == 1
        assert "stale" in str(rejected[0]["payload"])

    asyncio.run(scenario())


def test_reflex_preempts_a_future_plan_step_before_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(
            clock=clock,
            threat_after_first_action=True,
        )
        planner = PlanThenStopPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert summary.steps_completed == 2
        assert planner.calls == 1
        assert [
            action.paused for action in environment.actions if isinstance(action, PauseAction)
        ] == [False, True]
        assert not any(isinstance(action, SetSpeedAction) for action in environment.actions)
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "safety_preempted" for event in events) == 1
        assert sum(event["event_type"] == "plan_aborted" for event in events) == 1

    asyncio.run(scenario())


def test_continuous_mode_refuses_live_labeled_environment(tmp_path: Path) -> None:
    class LiveLabelEnvironment(RevisionEnvironment):
        def observation(self) -> Observation:
            return super().observation().model_copy(update={"mode": "live"})

    async def scenario() -> None:
        clock = FakeClock()
        environment = LiveLabelEnvironment(clock=clock)
        planner = PlanThenStopPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert summary.terminated
        assert planner.calls == 0
        assert environment.actions == []
        assert "restricted to mock" in summary.stop_reason

    asyncio.run(scenario())
