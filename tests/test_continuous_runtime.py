from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kenshi_agent.action_contracts import (
    ACTIVATE_VISIBLE_CONTROL_CONTRACT,
    NATIVE_WALK_DESTINATION_REACHED_RESULT,
)
from kenshi_agent.campaign import CampaignScope, CampaignScopeOrigin
from kenshi_agent.config import MacroConfig, PlanningConfig, SafetyConfig
from kenshi_agent.env import AgentEnvironment
from kenshi_agent.evals import evaluate_log, replay_plan_lifecycle
from kenshi_agent.input_boundary import ExecutionToken
from kenshi_agent.memory import MemoryStore
from kenshi_agent.models import (
    GAME_SPEED_MULTIPLIER_BY_GEAR,
    Action,
    ActionReceipt,
    ActivateVisibleControlAction,
    AffordanceExecution,
    AffordanceSource,
    ApproachDialogueTargetAction,
    BoundAffordance,
    CharacterState,
    CommandDispatchContext,
    Condition,
    ConditionKind,
    ConditionOperator,
    ControlMode,
    CreateFieldbookProjectOperation,
    Disposition,
    FieldbookEntryKind,
    FieldbookProjectKind,
    GameBinding,
    GameState,
    IdempotencyPolicy,
    InputBoundaryDecision,
    InputBoundaryReport,
    InterruptPolicy,
    MoveInDirectionAction,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NativeControlState,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PauseAction,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanPatch,
    PlanStep,
    ReadFieldbookAction,
    RespondToImmediateThreatAction,
    RiskBudget,
    SemanticActionReceipt,
    SetSpeedAction,
    SkillAction,
    SkillArgument,
    StopAction,
    TelemetrySnapshot,
    ThreatResponseStrategy,
    Transition,
    UIState,
    UseGameBindingAction,
    Vec3,
    VisibleUIControl,
    WorldStateRevision,
)
from kenshi_agent.planners.base import (
    HostedPlannerCallDiagnostics,
    HostedPlannerResponseError,
    Planner,
)
from kenshi_agent.planning import PlanningClock
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.reporting import ConsoleDecisionReporter
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry

COMMAND_ID_PATTERN = re.compile(r"^cmd-[0-9a-f]{32}$")


class FakeClock(PlanningClock):
    def __init__(self) -> None:
        self.now = 1.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class ManualPumpClock(PlanningClock):
    def __init__(self) -> None:
        self.now = 1.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        deadline = self.now + seconds
        future = asyncio.get_running_loop().create_future()
        self._sleepers.append((deadline, future))
        await future

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in self._sleepers:
            if deadline <= self.now and not future.done():
                future.set_result(None)
        self._sleepers = [
            (deadline, future) for deadline, future in self._sleepers if not future.done()
        ]


class RevisionEnvironment(AgentEnvironment):
    def __init__(
        self,
        *,
        clock: PlanningClock,
        change_money_after_first_action: bool = False,
        advance_revision: bool = True,
        threat_after_first_action: bool = False,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    ) -> None:
        self.clock = clock
        self.change_money_after_first_action = change_money_after_first_action
        self.advance_revision = advance_revision
        self.threat_after_first_action = threat_after_first_action
        self.control_mode = control_mode
        self.sequence = 1
        self.step_index = 0
        self.paused = True
        self.speed = 1.0
        self.money = 180
        self.open_inventory_windows = 0
        self.actions: list[Action] = []
        self.dispatch_contexts: list[CommandDispatchContext] = []
        self.dispatch_tokens: list[ExecutionToken | None] = []

    def observation(self) -> Observation:
        return Observation(
            run_id="continuous",
            step_index=self.step_index,
            mode="mock",
            control_mode=self.control_mode,
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
                capabilities=[
                    "game.pause",
                    "game.speed",
                    "game.money",
                    "game.time",
                    "ui.inventory",
                ],
                game=GameState(
                    loaded=True,
                    paused=self.paused,
                    speed_multiplier=self.speed,
                    money=self.money,
                    elapsed_minutes=0.0,
                ),
                ui=UIState(
                    open_inventory_windows=self.open_inventory_windows,
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
            self.paused = False
            self.speed = GAME_SPEED_MULTIPLIER_BY_GEAR[action.speed]
        elif (
            isinstance(action, UseGameBindingAction)
            and action.binding is GameBinding.TOGGLE_INVENTORY
        ):
            self.open_inventory_windows = int(not self.open_inventory_windows)
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

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None = None,
    ) -> Transition:
        self.dispatch_contexts.append(command)
        self.dispatch_tokens.append(token)
        return await self.step(action)

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


def patchable_movement_plan(observation: Observation) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="patchable-movement",
        plan_version=1,
        objective="Move, then choose the latest safe speed.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="move",
                action=SkillAction(name="mock_move"),
                preconditions=[
                    condition(
                        "telemetry.game.paused",
                        True,
                        "game.pause",
                    )
                ],
                success_conditions=[
                    condition(
                        "telemetry.game.paused",
                        True,
                        "game.pause",
                    )
                ],
                failure_conditions=[],
                timeout_seconds=3.0,
                retry_budget=0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                on_success="old-speed",
            ),
            PlanStep(
                step_id="old-speed",
                action=SetSpeedAction(speed=2),
                preconditions=[
                    condition(
                        "telemetry.game.paused",
                        True,
                        "game.pause",
                    )
                ],
                success_conditions=[
                    condition(
                        "telemetry.game.speed_multiplier",
                        2.0,
                        "game.speed",
                    )
                ],
                failure_conditions=[],
                timeout_seconds=1.0,
                retry_budget=0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            ),
        ],
        entry_step_id="move",
        max_actions=2,
        max_wall_seconds=5.0,
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


class BlockedPlanner(Planner):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def decide(self, observation: Observation) -> PlannerOutput:
        del observation
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("Blocked planner unexpectedly resumed.")


class BlockedThenStopPlanner(Planner):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.observations: list[Observation] = []

    async def decide(self, observation: Observation) -> PlannerOutput:
        self.observations.append(observation)
        if len(self.observations) > 1:
            return PlannerDecision(
                intent="Stop after proving automated safety replanning.",
                rationale="The cancelled planner call must never resume.",
                action=StopAction(reason="Automated safety replan proof complete."),
                confidence=1.0,
            )
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("Blocked planner unexpectedly resumed.")


def runtime_for(
    tmp_path: Path,
    environment: RevisionEnvironment,
    planner: Planner,
    clock: PlanningClock,
    *,
    observation_pump_enabled: bool = False,
    observation_clock: PlanningClock | None = None,
    automatic_takeover_enabled: bool = False,
    concurrent_option_planning_enabled: bool = True,
    concurrent_option_planning_delay_seconds: float = 0.0,
    stateful_approach_options_enabled: bool = False,
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    max_native_assisted_actions_per_plan: int = 0,
    max_actions_per_minute: int = 500,
    memory: MemoryStore | None = None,
    reporter: ConsoleDecisionReporter | None = None,
) -> tuple[AgentRuntime, SessionLogger]:
    macros = MacroRegistry(
        {
            "unused": MacroConfig(actions=[{"kind": "key", "key": "u"}]),
            "mock_move": MacroConfig(
                actions=[],
                movement_pulse_seconds=0.5,
                movement_pulse_min_seconds=0.1,
                movement_pulse_max_seconds=1.0,
            ),
            "mock_approach": MacroConfig(
                actions=[],
                approach_arrival_distance=5.0,
                approach_threat_distance=15.0,
            ),
        }
    )
    safety = SafetyConfig(
        allow_action_kinds=[
            "pause",
            "set_speed",
            "use_game_binding",
            "skill",
            "stop",
            "approach_dialogue_target",
            "move_in_direction",
            "respond_to_immediate_threat",
            "activate_visible_control",
        ],
        max_actions_per_minute=max_actions_per_minute,
        automatic_takeover_enabled=automatic_takeover_enabled,
        human_control_quiet_seconds=0.1,
        takeover_countdown_seconds=0.3,
        takeover_poll_seconds=0.1,
    )
    logger = SessionLogger(tmp_path / "events.jsonl", "continuous")
    runtime = AgentRuntime(
        run_id="continuous",
        environment=environment,
        planner=planner,
        guard=ActionGuard(safety, macros, control_mode=control_mode),
        reflexes=ReflexEngine(),
        logger=logger,
        memory=memory,
        memory_limit=0 if memory is None else 12,
        minimum_memory_salience=0.0,
        planning_config=PlanningConfig(
            mode=PlanningMode.CONTINUOUS,
            max_plan_steps=4,
            max_actions_per_plan=8,
            max_plan_wall_seconds=30.0,
            max_plan_game_seconds=12.0,
            observation_pump_enabled=observation_pump_enabled,
            concurrent_option_planning_enabled=concurrent_option_planning_enabled,
            concurrent_option_planning_delay_seconds=(
                concurrent_option_planning_delay_seconds
            ),
            stateful_approach_options_enabled=stateful_approach_options_enabled,
            max_native_assisted_actions_per_plan=max_native_assisted_actions_per_plan,
        ),
        planning_clock=clock,
        observation_clock=observation_clock,
        reporter=reporter,
    )
    return runtime, logger


def read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class ThreatResponseEnvironment(RevisionEnvironment):
    def __init__(
        self,
        *,
        clock: PlanningClock,
        reach_health_floor: bool = False,
    ) -> None:
        super().__init__(clock=clock, control_mode=ControlMode.NATIVE_ASSISTED)
        self.threatened = True
        self.in_combat = True
        self.blood = 100.0
        self.reach_health_floor = reach_health_floor
        self.response_started = asyncio.Event()
        self.observations_after_response = 0

    def observation(self) -> Observation:
        current = super().observation()
        assert current.telemetry is not None
        return current.model_copy(
            update={
                "telemetry": current.telemetry.model_copy(
                    update={
                        "capabilities": [
                            *current.telemetry.capabilities,
                            "control.move_in_direction",
                            "nearby.visible_entities",
                            "squad.health",
                        ],
                        "ui": UIState(
                            selected_character_id="entity-bark",
                            selected_character_ids=["entity-bark"],
                        ),
                        "squad": [
                            CharacterState(
                                id="entity-bark",
                                name="Bark",
                                selected=True,
                                alive=True,
                                conscious=True,
                                down=False,
                                blood=self.blood,
                                in_combat=self.in_combat,
                                position=Vec3(x=10.0, y=0.0, z=0.0),
                            )
                        ],
                        "nearby_entities": (
                            [
                                NearbyEntity(
                                    id="entity-bandit",
                                    name="Dust Bandit",
                                    disposition=Disposition.HOSTILE,
                                    distance=8.0,
                                    visible=True,
                                    position=Vec3(x=0.0, y=0.0, z=0.0),
                                )
                            ]
                            if self.threatened
                            else []
                        ),
                    }
                )
            },
            deep=True,
        )

    async def step(self, action: Action) -> Transition:
        if isinstance(action, RespondToImmediateThreatAction):
            self.actions.append(action)
            self.paused = False
            self.speed = 1.0
            self.step_index += 1
            self.sequence += 1
            self.response_started.set()
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.NATIVE_ASSISTED,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=1,
                    message="normal-speed engagement started",
                ),
                observation=self.observation(),
            )
        return await super().step(action)

    async def observe_without_capture(self) -> Observation:
        self.sequence += 1
        if self.response_started.is_set():
            self.observations_after_response += 1
            if self.reach_health_floor:
                self.blood = 50.0
            elif self.observations_after_response >= 2:
                self.threatened = False
                self.in_combat = False
        return self.observation()


class ThreatResponsePlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, observation: Observation) -> PlannerOutput:
        self.calls += 1
        if self.calls > 1:
            return PlannerDecision(
                intent="Stop after the threat response proof.",
                rationale="The response completed safely.",
                action=StopAction(reason="threat response proof complete"),
                confidence=1.0,
            )
        return PlanEnvelope(
            schema_version="1.0",
            plan_id="threat-response-proof",
            plan_version=1,
            objective="Engage the immediate threat.",
            control_mode=observation.control_mode,
            based_on_revision=observation.world_revision,
            assumptions=[fresh()],
            steps=[
                PlanStep(
                    step_id="respond",
                    action=RespondToImmediateThreatAction(
                        actor_id="entity-bark",
                        strategy=ThreatResponseStrategy.ENGAGE,
                    ),
                    preconditions=[fresh()],
                    success_conditions=[],
                    failure_conditions=[],
                    timeout_seconds=5.0,
                    retry_budget=0,
                    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                )
            ],
            entry_step_id="respond",
            max_actions=1,
            max_wall_seconds=10.0,
            max_game_seconds=10.0,
            risk_budget=RiskBudget(
                max_pointer_actions=0,
                max_purchase_actions=0,
                max_native_assisted_actions=1,
            ),
        )


def test_threat_response_runs_under_monitoring_without_reflex_loop(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = ThreatResponseEnvironment(clock=clock)
        planner = ThreatResponsePlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            concurrent_option_planning_enabled=False,
            control_mode=ControlMode.NATIVE_ASSISTED,
            max_native_assisted_actions_per_plan=1,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.response_started.wait(), timeout=1.0)
            for _ in range(12):
                pump_clock.advance(0.1)
                await asyncio.sleep(0)
                if run.done():
                    break
            summary = await asyncio.wait_for(run, timeout=2.0)
        finally:
            logger.close()

        assert summary.stop_reason == "threat response proof complete"
        assert environment.paused is True
        assert [action.kind for action in environment.actions] == [
            "respond_to_immediate_threat",
            "pause",
            "stop",
        ]
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "option_succeeded" for event in events) == 1
        assert not any(
            event["event_type"] == "safety_supervisor_preempted"
            for event in events
        )
        response_receipt = next(
            event["payload"]
            for event in events
            if event["event_type"] == "action_receipt"
            and event["payload"]["action"]["kind"]
            == "respond_to_immediate_threat"
        )
        assert response_receipt["semantic"]["action_kind"] == (
            "respond_to_immediate_threat"
        )
        assert response_receipt["semantic"]["option_id"] == (
            "threat-response-threat-response-proof-1-respond"
        )

    asyncio.run(scenario())


def test_threat_health_boundary_pauses_and_returns_to_planning(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = ThreatResponseEnvironment(
            clock=clock,
            reach_health_floor=True,
        )
        planner = ThreatResponsePlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            concurrent_option_planning_enabled=False,
            control_mode=ControlMode.NATIVE_ASSISTED,
            max_native_assisted_actions_per_plan=1,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.response_started.wait(), timeout=1.0)
            for _ in range(12):
                pump_clock.advance(0.1)
                await asyncio.sleep(0)
                if run.done():
                    break
            summary = await asyncio.wait_for(run, timeout=2.0)
        finally:
            logger.close()

        assert summary.stop_reason == "threat response proof complete"
        assert planner.calls == 2
        assert environment.paused is True
        events = read_events(tmp_path / "events.jsonl")
        assert any(event["event_type"] == "option_failed" for event in events)
        assert any(event["event_type"] == "plan_aborted" for event in events)

    asyncio.run(scenario())


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
        assert metrics.command_receipts == 2
        assert metrics.command_receipts_with_post_revision == 2
        assert metrics.receipts_with_post_command_revision_percentage == 100.0

        events = read_events(tmp_path / "events.jsonl")
        started = next(
            event for event in events if event["event_type"] == "run_started"
        )
        assert (
            started["payload"]["memory_retrieval_policy"]
            == "deterministic"
        )
        receipts = [
            event["payload"]
            for event in events
            if event["event_type"] == "action_receipt"
            and event["payload"]["command_id"] is not None
        ]
        command_ids = [receipt["command_id"] for receipt in receipts]
        assert len(set(command_ids)) == 2
        assert all(
            isinstance(command_id, str) and COMMAND_ID_PATTERN.fullmatch(command_id)
            for command_id in command_ids
        )
        assert [context.command_id for context in environment.dispatch_contexts] == command_ids
        assert [
            context.based_on_revision.telemetry_sequence
            for context in environment.dispatch_contexts
        ] == [1, 2]
        assert [
            receipt["started_after_revision"]["telemetry_sequence"] for receipt in receipts
        ] == [1, 2]
        assert [receipt["completed_at_revision"]["telemetry_sequence"] for receipt in receipts] == [
            2,
            3,
        ]
        assert all(receipt["causal_revision_advanced"] is True for receipt in receipts)

        replayed = replay_plan_lifecycle(tmp_path / "events.jsonl")
        assert replayed["two-step-proof"].status == "completed"
        assert replayed["two-step-proof"].succeeded_step_ids == [
            "resume",
            "accelerate",
        ]

    asyncio.run(scenario())


def test_continuous_reporter_narrates_plan_and_actions(tmp_path: Path) -> None:
    class Narrator:
        def __init__(self) -> None:
            self.utterances: list[str] = []

        def say(self, text: str, *, key: str | None = None) -> None:
            del key
            self.utterances.append(text)

        def close(self) -> None:
            pass

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        narrator = Narrator()
        reporter = ConsoleDecisionReporter(
            run_id="continuous",
            planner_name="scripted",
            model_name=None,
            narrator=narrator,
        )
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            PlanThenStopPlanner(),
            clock,
            reporter=reporter,
        )
        try:
            await runtime.run(max_steps=2)
        finally:
            logger.close()

        spoken = " ".join(narrator.utterances)
        assert "My plan is to resume and accelerate the mock world." in spoken
        assert "Starting the game." in spoken
        assert "Setting the game speed to five times." in spoken

    asyncio.run(scenario())


def test_executor_uses_dispatch_time_completion_without_model_restatement(
    tmp_path: Path,
) -> None:
    class MechanicalCompletionPlanner(Planner):
        async def decide(self, current: Observation) -> PlannerOutput:
            return PlanEnvelope(
                schema_version="1.0",
                plan_id="mechanical-completion",
                plan_version=1,
                objective="Select 5x playback without restating motor semantics.",
                control_mode=current.control_mode,
                based_on_revision=current.world_revision,
                assumptions=[fresh()],
                steps=[
                    PlanStep(
                        step_id="accelerate",
                        action=SetSpeedAction(speed=3),
                        preconditions=[fresh()],
                        success_conditions=[],
                        failure_conditions=[],
                        timeout_seconds=1.0,
                    )
                ],
                entry_step_id="accelerate",
                max_actions=1,
                max_wall_seconds=3.0,
                max_game_seconds=3.0,
                risk_budget=RiskBudget(
                    max_pointer_actions=0,
                    max_purchase_actions=0,
                    max_native_assisted_actions=0,
                ),
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            MechanicalCompletionPlanner(),
            clock,
        )
        try:
            summary = await runtime.run(max_steps=1)
        finally:
            logger.close()

        assert summary.steps_completed == 1
        assert environment.speed == 5.0
        progress = [
            event
            for event in read_events(tmp_path / "events.jsonl")
            if event["event_type"] == "plan_step_progress"
        ]
        assert any(
            event["payload"]["evidence"].get("completion_owner")
            == "runtime_conditions"
            for event in progress
        )

    asyncio.run(scenario())


def test_sequential_toggles_derive_distinct_dispatch_time_baselines(
    tmp_path: Path,
) -> None:
    class SequentialTogglePlanner(Planner):
        async def decide(self, current: Observation) -> PlannerOutput:
            return PlanEnvelope(
                schema_version="1.0",
                plan_id="sequential-toggles",
                plan_version=1,
                objective="Open and then close the same inventory.",
                control_mode=current.control_mode,
                based_on_revision=current.world_revision,
                assumptions=[fresh()],
                steps=[
                    PlanStep(
                        step_id="open",
                        action=UseGameBindingAction(
                            binding=GameBinding.TOGGLE_INVENTORY,
                            expected_effect="open inventory",
                        ),
                        preconditions=[fresh()],
                        success_conditions=[],
                        timeout_seconds=1.0,
                        on_success="close",
                    ),
                    PlanStep(
                        step_id="close",
                        action=UseGameBindingAction(
                            binding=GameBinding.TOGGLE_INVENTORY,
                            expected_effect="close inventory",
                        ),
                        preconditions=[fresh()],
                        success_conditions=[],
                        timeout_seconds=1.0,
                    ),
                ],
                entry_step_id="open",
                max_actions=2,
                max_wall_seconds=3.0,
                max_game_seconds=3.0,
                risk_budget=RiskBudget(
                    max_pointer_actions=0,
                    max_purchase_actions=0,
                    max_native_assisted_actions=0,
                ),
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            SequentialTogglePlanner(),
            clock,
        )
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert summary.steps_completed == 2
        assert environment.open_inventory_windows == 0
        started = [
            event
            for event in read_events(tmp_path / "events.jsonl")
            if event["event_type"] == "plan_step_started"
        ]
        expected_baselines = [
            event["payload"]["evidence"]["completion_conditions"][0]["expected"]
            for event in started
        ]
        assert expected_baselines == [0, 1]

    asyncio.run(scenario())


def test_later_step_with_active_failure_condition_dispatches_no_input(
    tmp_path: Path,
) -> None:
    class ActiveFailurePlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls > 1:
                return PlannerDecision(
                    intent="Stop after the invalid future step is rejected.",
                    rationale="The runtime must not dispatch through an active failure.",
                    action=StopAction(reason="Failure preflight proof complete."),
                    confidence=1.0,
                )
            return PlanEnvelope(
                schema_version="1.0",
                plan_id="active-failure-preflight",
                plan_version=1,
                objective="Open inventory, but never dispatch an already-failed next step.",
                control_mode=current.control_mode,
                based_on_revision=current.world_revision,
                assumptions=[fresh()],
                steps=[
                    PlanStep(
                        step_id="open",
                        action=UseGameBindingAction(
                            binding=GameBinding.TOGGLE_INVENTORY,
                            expected_effect="open inventory",
                        ),
                        preconditions=[fresh()],
                        success_conditions=[],
                        timeout_seconds=1.0,
                        on_success="invalid-close",
                    ),
                    PlanStep(
                        step_id="invalid-close",
                        action=UseGameBindingAction(
                            binding=GameBinding.TOGGLE_INVENTORY,
                            expected_effect="close inventory",
                        ),
                        preconditions=[fresh()],
                        success_conditions=[],
                        failure_conditions=[fresh()],
                        timeout_seconds=1.0,
                    ),
                ],
                entry_step_id="open",
                max_actions=2,
                max_wall_seconds=3.0,
                max_game_seconds=3.0,
                risk_budget=RiskBudget(
                    max_pointer_actions=0,
                    max_purchase_actions=0,
                    max_native_assisted_actions=0,
                ),
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            ActiveFailurePlanner(),
            clock,
        )
        try:
            await runtime.run(max_steps=2)
        finally:
            logger.close()

        toggles = [
            action
            for action in environment.actions
            if isinstance(action, UseGameBindingAction)
        ]
        assert len(toggles) == 1
        assert environment.open_inventory_windows == 1
        aborted = [
            event
            for event in read_events(tmp_path / "events.jsonl")
            if event["event_type"] == "plan_aborted"
        ]
        assert any(
            "failure condition is already true before dispatch"
            in str(event["payload"]["reason"])
            for event in aborted
        )

    asyncio.run(scenario())


def test_continuous_actions_reach_the_next_planner_outcome_ledger(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = PlanThenStopPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            await runtime.run(max_steps=3)
        finally:
            logger.close()

        assert planner.calls == 2
        outcome_kinds = [
            outcome.action.kind
            for outcome in planner.observations[1].recent_action_outcomes
        ]
        assert outcome_kinds == [
            "pause",
            "set_speed",
        ]
        assert all(
            outcome.executed
            for outcome in planner.observations[1].recent_action_outcomes
        )

    asyncio.run(scenario())


class BoundaryRejectingEnvironment(RevisionEnvironment):
    """Reproduce a live post-lease rejection without a real input lease.

    `LiveEnvironment` emits zero primitives and reports the rejection on the
    receipt when the state that authorized the action changed while the polite
    input lease was pending. This fake returns that exact shape so the
    executor's reservation, event, and metric handling can be asserted
    deterministically.
    """

    def __init__(self, *, reject_after: int = 1, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reject_after = reject_after
        self.dispatches = 0

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None = None,
    ) -> Transition:
        self.dispatches += 1
        self.dispatch_contexts.append(command)
        self.dispatch_tokens.append(token)
        if token is None or self.dispatches != self.reject_after:
            return await self.step(action)

        report = InputBoundaryReport(
            decision=InputBoundaryDecision.REJECTED,
            reason="A plan assumption or step precondition is no longer true.",
            lease_wait_seconds=6.25,
            plan_id=token.plan_id,
            plan_version=token.plan_version,
            step_id=token.step_id,
            validated_revision=token.validated_revision,
            boundary_revision=self.observation().world_revision,
        )
        return Transition(
            receipt=ActionReceipt(
                action=action,
                control_mode=ControlMode.INTERFACE_ONLY,
                accepted=False,
                executed=False,
                dry_run=False,
                primitive_actions=0,
                message="No input was emitted at the boundary.",
                error_type="InputBoundaryRejected",
                input_boundary=report,
                command_id=command.command_id,
            ),
            observation=self.observation(),
        )


class MismatchedRejectingEnvironment(BoundaryRejectingEnvironment):
    """Return a zero-input receipt that belongs to a different command."""

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None = None,
    ) -> Transition:
        transition = await super().dispatch(
            action,
            command=command,
            token=token,
        )
        if transition.receipt.accepted or transition.receipt.executed:
            return transition
        return transition.model_copy(
            update={
                "receipt": transition.receipt.model_copy(
                    update={"command_id": "cmd-" + ("f" * 32)}
                )
            }
        )


def test_execution_token_carries_plan_authorization_into_dispatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = PlanThenStopPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            await runtime.run(max_steps=2)
        finally:
            logger.close()

        tokens = [token for token in environment.dispatch_tokens if token is not None]
        assert len(tokens) == 2
        assert [token.step_id for token in tokens] == ["resume", "accelerate"]
        assert all(token.plan_id == "two-step-proof" for token in tokens)
        assert all(token.plan_version == 1 for token in tokens)
        assert all(token.control_mode is ControlMode.INTERFACE_ONLY for token in tokens)
        assert all(token.authority_validator is not None for token in tokens)
        live_observation = environment.observation().model_copy(update={"mode": "live"})
        assert tokens[0].authority_validator is not None
        assert "unpause" in (tokens[0].authority_validator(live_observation) or "")
        # The token must carry the same typed conditions the executor checked,
        # so the boundary re-uses the plan's authority rather than its own rule.
        assert all(token.assumptions for token in tokens)
        assert all(token.preconditions for token in tokens)
        assert [
            token.command_id for token in tokens
        ] == [context.command_id for context in environment.dispatch_contexts]
        assert [
            token.validated_revision.telemetry_sequence for token in tokens
        ] == [1, 2]

    asyncio.run(scenario())


def test_post_lease_boundary_rejection_releases_budget_and_is_attributable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = BoundaryRejectingEnvironment(clock=clock, reject_after=1)
        planner = PlanThenStopPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            max_actions_per_minute=1,
        )
        try:
            await runtime.run(max_steps=2)
        finally:
            logger.close()

        # The rejected dispatch never reached the environment's action path.
        # Only the planner's later explicit Stop follows.
        assert [type(action) for action in environment.actions] == [StopAction]

        events = read_events(tmp_path / "events.jsonl")
        event_types = [event["event_type"] for event in events]
        assert "input_boundary_rejected" in event_types
        assert "input_boundary_revalidated" not in event_types
        rejected = next(
            event for event in events if event["event_type"] == "input_boundary_rejected"
        )
        evidence = rejected["payload"]["evidence"]
        assert evidence["decision"] == "rejected"
        assert evidence["lease_wait_seconds"] == 6.25
        assert evidence["validated_revision"]["telemetry_sequence"] == 1
        assert rejected["payload"]["step_id"] == "resume"

        # A proven non-dispatch releases its reservation instead of spending it.
        assert "plan_budget_released" in event_types
        assert event_types.index("input_boundary_rejected") < event_types.index(
            "plan_budget_released"
        )

        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.input_boundary_rejections == 1
        assert metrics.input_boundary_revalidations == 0
        assert metrics.budget_releases == 1
        assert metrics.plan_steps_succeeded == 0

    asyncio.run(scenario())


def test_mismatched_rejection_receipt_keeps_budgets_spent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = MismatchedRejectingEnvironment(clock=clock, reject_after=1)
        planner = PlanThenStopPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            max_actions_per_minute=1,
        )
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        # A receipt for another command cannot prove this command emitted
        # nothing. The rate slot therefore remains spent and blocks the later
        # Stop instead of treating the mismatched rejection as authority.
        assert environment.actions == []
        assert "rate limit" in summary.stop_reason
        event_types = [
            event["event_type"] for event in read_events(tmp_path / "events.jsonl")
        ]
        assert "plan_budget_committed" in event_types
        assert "plan_budget_released" not in event_types

    asyncio.run(scenario())


def test_long_planner_validation_error_stops_without_masking_original_failure(
    tmp_path: Path,
) -> None:
    class LongFailurePlanner(Planner):
        async def decide(self, observation: Observation) -> PlannerOutput:
            del observation
            raise ValueError("invalid structured output " + ("x" * 20_000))

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            LongFailurePlanner(),
            clock,
        )
        try:
            summary = await runtime.run(max_steps=1)
        finally:
            logger.close()

        # A malformed response is retried; the run ends only once the planner
        # has failed more times in a row than the replan limit allows. What must
        # not happen is the original failure being lost or logged unbounded.
        assert summary.terminated is True
        assert "unusable responses in a row" in summary.stop_reason
        assert len(summary.stop_reason) < 20_000
        events = read_events(tmp_path / "events.jsonl")
        stalled = [event for event in events if event["event_type"] == "replan_stalled"]
        assert len(stalled) == 1
        assert stalled[0]["payload"]["identical_failures"] == 3
        planner_error = next(
            event for event in events if event["event_type"] == "planner_error"
        )
        payload = planner_error["payload"]
        assert payload["error_type"] == "ValueError"
        assert payload["message_characters"] > 20_000
        assert payload["message_truncated"] is True
        assert len(payload["message"]) == AgentRuntime._PLANNER_ERROR_LOG_MAX_CHARS
        planner_call = next(
            event for event in events if event["event_type"] == "strategic_planner_call"
        )
        assert planner_call["payload"]["source"] == "planner_error"

    asyncio.run(scenario())


def test_orphaned_plan_patch_is_rejected_then_fresh_planning_continues(
    tmp_path: Path,
) -> None:
    class OrphanedPatchThenStopPlanner(Planner):
        def __init__(self) -> None:
            self.observations: list[Observation] = []

        async def decide(self, observation: Observation) -> PlannerOutput:
            self.observations.append(observation)
            if len(self.observations) == 1:
                assert observation.active_plan is None
                return PlanPatch(
                    schema_version="1.0",
                    plan_id="already-finished-plan",
                    based_on_plan_version=1,
                    based_on_revision=observation.world_revision,
                    replace_future_steps=[
                        PlanStep(
                            step_id="orphaned-future",
                            action=StopAction(
                                reason="This action must never inherit authority."
                            ),
                            preconditions=[fresh()],
                            timeout_seconds=1.0,
                        )
                    ],
                    rationale="This patch has no active plan to revise.",
                )
            return PlannerDecision(
                intent="Stop after recovering from the orphaned patch.",
                rationale="Fresh planning remained available.",
                action=StopAction(reason="Orphaned patch recovery complete."),
                confidence=1.0,
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = OrphanedPatchThenStopPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=1)
        finally:
            logger.close()

        assert summary.terminated is True
        assert len(planner.observations) == 2
        assert planner.observations[1].active_plan is None
        feedback = planner.observations[1].planner_feedback
        assert feedback is not None
        assert "no active plan" in feedback
        assert "fresh PlanEnvelope or StopAction" in feedback
        assert [type(action) for action in environment.actions] == [StopAction]

        events = read_events(tmp_path / "events.jsonl")
        rejected = [
            event for event in events if event["event_type"] == "plan_rejected"
        ]
        assert len(rejected) == 1
        assert rejected[0]["payload"]["plan_id"] == "already-finished-plan"
        assert "no matching active plan" in rejected[0]["payload"]["reason"]
        assert not any(
            event["event_type"] == "replan_stalled" for event in events
        )

    asyncio.run(scenario())


def test_semantically_identical_orphaned_patches_share_one_bounded_failure(
    tmp_path: Path,
) -> None:
    class RepeatingOrphanedPatchPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, observation: Observation) -> PlannerOutput:
            self.calls += 1
            return PlanPatch(
                schema_version="1.0",
                plan_id=f"orphaned-plan-{self.calls}",
                based_on_plan_version=self.calls,
                based_on_revision=observation.world_revision,
                replace_future_steps=[
                    PlanStep(
                        step_id=f"future-{self.calls}",
                        action=StopAction(reason=f"Never execute patch {self.calls}."),
                        preconditions=[fresh()],
                        timeout_seconds=1.0,
                    )
                ],
                rationale=f"Incidental response wording {self.calls}.",
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = RepeatingOrphanedPatchPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=1)
        finally:
            logger.close()

        assert summary.terminated is True
        assert planner.calls == AgentRuntime._IDENTICAL_REPLAN_FAILURE_LIMIT
        assert environment.actions == []
        assert "same orphaned plan patch" in summary.stop_reason

        events = read_events(tmp_path / "events.jsonl")
        assert sum(
            event["event_type"] == "plan_rejected" for event in events
        ) == AgentRuntime._IDENTICAL_REPLAN_FAILURE_LIMIT
        stalled = [
            event for event in events if event["event_type"] == "replan_stalled"
        ]
        assert len(stalled) == 1
        assert stalled[0]["payload"]["reason"] == "plan_patch_without_active_plan"

    asyncio.run(scenario())


def test_continuous_retry_preserves_typed_hosted_terminal_and_compact_feedback(
    tmp_path: Path,
) -> None:
    class TruncatedThenStopPlanner(Planner):
        def __init__(self) -> None:
            self.observations: list[Observation] = []
            self.pending_diagnostics: HostedPlannerCallDiagnostics | None = None

        async def decide(self, observation: Observation) -> PlannerOutput:
            self.observations.append(observation)
            if len(self.observations) == 1:
                diagnostics = HostedPlannerCallDiagnostics(
                    provider_kind="openrouter",
                    output_model="PlanEnvelope",
                    requested_model="google/gemini-3.1-flash-lite",
                    response_model="google/gemini-3.1-flash-lite",
                    provider_name="Google",
                    response_id="generation-cut-short",
                    finish_reason="length",
                    max_output_tokens=12_288,
                    prompt_tokens=19_000,
                    completion_tokens=12_288,
                    reasoning_tokens=11_700,
                    total_tokens=31_288,
                    response_characters=1_870,
                    system_characters=45_000,
                    observation_characters=29_900,
                    schema_characters=61_000,
                    request_text_characters=75_000,
                    schema_in_prompt=False,
                    screenshot_included=True,
                )
                self.pending_diagnostics = diagnostics
                raise HostedPlannerResponseError("output_truncated", diagnostics)
            return PlannerDecision(
                intent="Stop after proving typed hosted recovery.",
                rationale="The retry received the exact attributable terminal.",
                action=StopAction(reason="Hosted recovery proof complete."),
                confidence=1.0,
            )

        def take_call_diagnostics(self) -> HostedPlannerCallDiagnostics | None:
            diagnostics = self.pending_diagnostics
            self.pending_diagnostics = None
            return diagnostics

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = TruncatedThenStopPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=1)
        finally:
            logger.close()

        assert summary.terminated is True
        assert len(planner.observations) == 2
        feedback = planner.observations[1].planner_feedback
        assert feedback is not None
        assert "one compact PlanEnvelope" in feedback
        assert "strategic intent" in feedback
        assert "one step only" not in feedback

        events = read_events(tmp_path / "events.jsonl")
        transport = [
            event for event in events if event["event_type"] == "planner_transport"
        ]
        assert len(transport) == 1
        assert transport[0]["payload"]["finish_reason"] == "length"
        assert transport[0]["payload"]["reasoning_tokens"] == 11_700
        assert transport[0]["payload"]["structured_output_accepted"] is False

        planner_error = next(
            event for event in events if event["event_type"] == "planner_error"
        )
        assert planner_error["payload"]["failure_category"] == "output_truncated"
        assert planner_error["payload"]["failure_signature"] == (
            "openrouter:output_truncated:PlanEnvelope:length"
        )
        planner_call = next(
            event
            for event in events
            if event["event_type"] == "strategic_planner_call"
            and event["payload"]["source"] == "planner_error"
        )
        assert planner_call["payload"]["failure_category"] == "output_truncated"

        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.planner_errors == 1
        assert metrics.planner_failure_categories == {"output_truncated": 1}

    asyncio.run(scenario())


def test_independent_supervisor_replans_after_confirming_a_catastrophic_pause(
    tmp_path: Path,
) -> None:
    class UnsafeObserveEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: FakeClock) -> None:
            super().__init__(clock=clock)
            self.unsafe = False

        def observation(self) -> Observation:
            current = super().observation()
            if not self.unsafe or current.telemetry is None:
                return current
            return current.model_copy(
                update={
                    "telemetry": current.telemetry.model_copy(
                        update={
                            "squad": [
                                CharacterState(
                                    id="entity-bark",
                                    name="Bark",
                                    alive=True,
                                    conscious=True,
                                    getting_eaten=True,
                                )
                            ]
                        }
                    )
                }
            )

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            self.paused = False
            self.unsafe = True
            return self.observation()

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = UnsafeObserveEnvironment(clock=plan_clock)
        planner = BlockedThenStopPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=3))
            await planner.started.wait()
            pump_clock.advance(0.1)
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert planner.cancelled.is_set()
        assert len(planner.observations) == 2
        assert summary.terminated
        assert summary.stop_reason == "Automated safety replan proof complete."
        assert environment.paused is True
        replanning_observation = planner.observations[1]
        assert replanning_observation.telemetry is not None
        assert replanning_observation.telemetry.game.paused is True
        assert replanning_observation.telemetry_stale is False
        assert replanning_observation.planner_feedback is not None
        assert "reflex" in replanning_observation.planner_feedback
        assert "being eaten" in replanning_observation.planner_feedback
        assert [
            action.paused for action in environment.actions if isinstance(action, PauseAction)
        ] == [True]
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "strategic_planner_cancelled" for event in events) == 1
        assert sum(event["event_type"] == "safety_cleanup_completed" for event in events) == 1
        assert (
            sum(
                event["event_type"] == "safety_supervisor_replan_requested"
                for event in events
            )
            == 1
        )
        assert sum(event["event_type"] == "safety_supervisor_terminal" for event in events) == 0
        receipts = [event["payload"] for event in events if event["event_type"] == "action_receipt"]
        assert len(receipts) == 2
        assert isinstance(receipts[0]["command_id"], str)
        assert COMMAND_ID_PATTERN.fullmatch(receipts[0]["command_id"])
        assert receipts[0]["causal_revision_advanced"] is True
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.safety_supervisor_preemptions == 1
        assert metrics.strategic_planner_cancellations == 1
        assert metrics.plan_execution_cancellations == 0
        assert metrics.safety_cleanups_started == 1
        assert metrics.safety_cleanups_completed == 1
        assert metrics.safety_cleanups_failed == 0
        assert metrics.safety_supervisor_terminals == 0
        assert metrics.safety_supervisor_safe_paused == 0
        assert metrics.safety_cleanup_success_percentage == 100.0

    asyncio.run(scenario())


def test_emergency_stop_remains_terminal_after_confirmed_pause(
    tmp_path: Path,
) -> None:
    class EmergencyStopEnvironment(RevisionEnvironment):
        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            self.paused = False
            return self.observation().model_copy(
                update={"events": ["emergency_stop_detected"]}
            )

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = EmergencyStopEnvironment(clock=plan_clock)
        planner = BlockedThenStopPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=3))
            await planner.started.wait()
            pump_clock.advance(0.1)
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert planner.cancelled.is_set()
        assert len(planner.observations) == 1
        assert summary.terminated
        assert "Emergency stop ended continuous execution" in summary.stop_reason
        assert environment.paused is True
        assert [
            action.paused for action in environment.actions if isinstance(action, PauseAction)
        ] == [True]
        events = read_events(tmp_path / "events.jsonl")
        assert sum(
            event["event_type"] == "safety_supervisor_replan_requested"
            for event in events
        ) == 0
        terminal = [
            event for event in events if event["event_type"] == "safety_supervisor_terminal"
        ]
        assert len(terminal) == 1
        assert terminal[0]["payload"]["cause"] == "emergency_stop"
        assert terminal[0]["payload"]["status"] == "safe_paused"

    asyncio.run(scenario())


def test_host_terminal_never_relabels_frozen_paused_telemetry_as_safe(
    tmp_path: Path,
) -> None:
    class CrashedEnvironment(RevisionEnvironment):
        async def observe_without_capture(self) -> Observation:
            return self.observation().model_copy(
                update={"events": ["terminal_window_detected: Kenshi has crashed"]}
            )

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = CrashedEnvironment(clock=plan_clock)
        planner = BlockedPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=3))
            await planner.started.wait()
            pump_clock.advance(0.1)
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert planner.cancelled.is_set()
        assert summary.terminated
        assert "Kenshi has crashed" in summary.stop_reason
        assert environment.actions == []
        terminal = [
            event
            for event in read_events(tmp_path / "events.jsonl")
            if event["event_type"] == "safety_supervisor_terminal"
        ]
        assert len(terminal) == 1
        assert terminal[0]["payload"]["cause"] == "host_terminal"
        assert terminal[0]["payload"]["status"] == "terminal_failure"

    asyncio.run(scenario())


def test_supervisor_cancels_blocked_plan_then_replans_from_automated_pause(
    tmp_path: Path,
) -> None:
    class BlockingMovementEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: FakeClock) -> None:
            super().__init__(clock=clock)
            self.movement_started = asyncio.Event()
            self.movement_cancelled = asyncio.Event()
            self.unsafe = False

        def observation(self) -> Observation:
            current = super().observation()
            if not self.unsafe or current.telemetry is None:
                return current
            return current.model_copy(
                update={
                    "telemetry": current.telemetry.model_copy(
                        update={
                            "squad": [
                                CharacterState(
                                    id="entity-bark",
                                    name="Bark",
                                    alive=True,
                                    conscious=True,
                                    getting_eaten=True,
                                )
                            ]
                        }
                    )
                }
            )

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            self.unsafe = True
            return self.observation()

        async def step(self, action: Action) -> Transition:
            if isinstance(action, SkillAction):
                self.actions.append(action)
                self.paused = False
                self.sequence += 1
                self.movement_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.movement_cancelled.set()
                    raise
                raise AssertionError("Blocked movement unexpectedly resumed.")
            return await super().step(action)

    class MovementPlanner(Planner):
        def __init__(self) -> None:
            self.observations: list[Observation] = []

        async def decide(self, current: Observation) -> PlannerOutput:
            self.observations.append(current)
            if len(self.observations) > 1:
                return PlannerDecision(
                    intent="Stop after proving cancelled-plan replanning.",
                    rationale="The interrupted movement plan must never resume.",
                    action=StopAction(reason="Cancelled-plan replan proof complete."),
                    confidence=1.0,
                )
            return PlanEnvelope(
                schema_version="1.0",
                plan_id="blocked-movement",
                plan_version=1,
                objective="Exercise cancellable movement supervision.",
                control_mode=current.control_mode,
                based_on_revision=current.world_revision,
                assumptions=[fresh()],
                steps=[
                    PlanStep(
                        step_id="move",
                        action=SkillAction(name="mock_move"),
                        preconditions=[
                            condition(
                                "telemetry.game.paused",
                                True,
                                "game.pause",
                            )
                        ],
                        success_conditions=[
                            condition(
                                "telemetry.game.paused",
                                True,
                                "game.pause",
                            )
                        ],
                        failure_conditions=[],
                        timeout_seconds=3.0,
                        retry_budget=0,
                        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                    )
                ],
                entry_step_id="move",
                max_actions=1,
                max_wall_seconds=4.0,
                max_game_seconds=5.0,
                risk_budget=RiskBudget(
                    max_pointer_actions=0,
                    max_purchase_actions=0,
                    max_native_assisted_actions=0,
                ),
            )

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = BlockingMovementEnvironment(clock=plan_clock)
        planner = MovementPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=3))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            pump_clock.advance(0.1)
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert environment.movement_cancelled.is_set()
        assert summary.terminated
        assert summary.stop_reason == "Cancelled-plan replan proof complete."
        # The one-action plan has no future authority to revise. Safety cancels
        # it, then exactly one fresh strategic call replans from the pause.
        assert len(planner.observations) == 2
        replanning_observation = planner.observations[-1]
        assert replanning_observation.planner_feedback is not None
        assert "do not resume the cancelled plan" in replanning_observation.planner_feedback
        assert len(replanning_observation.recent_plan_outcomes) == 1
        interrupted_outcome = replanning_observation.recent_plan_outcomes[0]
        assert interrupted_outcome.plan_id == "blocked-movement"
        assert interrupted_outcome.disposition.value == "abandoned"
        assert "Safety preempted the plan (reflex)" in interrupted_outcome.reason
        assert environment.paused is True
        assert (
            len([action for action in environment.actions if isinstance(action, SkillAction)]) == 1
        )
        assert [
            action.paused for action in environment.actions if isinstance(action, PauseAction)
        ] == [True]
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "plan_execution_cancelled" for event in events) == 1
        assert (
            sum(
                event["event_type"] == "world_state_event"
                and event["payload"]["event_type"] == "command_inconclusive"
                for event in events
            )
            == 1
        )
        assert sum(event["event_type"] == "safety_cleanup_completed" for event in events) == 1
        preemption = next(
            event for event in events if event["event_type"] == "safety_supervisor_preempted"
        )
        assert preemption["payload"]["cause"] == "reflex"
        assert (
            sum(
                event["event_type"] == "safety_supervisor_replan_requested"
                for event in events
            )
            == 1
        )
        assert sum(event["event_type"] == "safety_supervisor_terminal" for event in events) == 0
        assert sum(event["event_type"] == "option_prepared" for event in events) == 1
        assert sum(event["event_type"] == "option_started" for event in events) == 1
        assert sum(event["event_type"] == "option_cancelled" for event in events) == 1
        assert not [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("kenshi-agent-option-")
        ]
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.plan_execution_cancellations == 1

    asyncio.run(scenario())


def test_human_handoff_countdown_replans_instead_of_resuming_cancelled_plan(
    tmp_path: Path,
) -> None:
    class OneHumanInterruptionEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: FakeClock) -> None:
            super().__init__(clock=clock)
            self.movement_started = asyncio.Event()
            self.movement_cancelled = asyncio.Event()
            self.reported_human_input = False

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            events: list[str] = []
            if not self.reported_human_input:
                self.reported_human_input = True
                events.append("human_input_detected")
            return self.observation().model_copy(update={"events": events})

        async def step(self, action: Action) -> Transition:
            if isinstance(action, SkillAction):
                self.actions.append(action)
                self.paused = False
                self.sequence += 1
                self.movement_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.movement_cancelled.set()
                    raise
                raise AssertionError("Cancelled movement unexpectedly resumed.")
            return await super().step(action)

    class ReplanningMovementPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls > 1:
                return PlannerDecision(
                    intent="Stop after proving fresh post-handoff replanning.",
                    rationale="The cancelled movement plan must never resume.",
                    action=StopAction(reason="Handoff replan proof complete."),
                    confidence=1.0,
                )
            return PlanEnvelope(
                schema_version="1.0",
                plan_id="handoff-cancelled-plan",
                plan_version=1,
                objective="Exercise human handoff cancellation.",
                control_mode=current.control_mode,
                based_on_revision=current.world_revision,
                assumptions=[fresh()],
                steps=[
                    PlanStep(
                        step_id="move",
                        action=SkillAction(name="mock_move"),
                        preconditions=[
                            condition(
                                "telemetry.game.paused",
                                True,
                                "game.pause",
                            )
                        ],
                        success_conditions=[
                            condition(
                                "telemetry.game.paused",
                                True,
                                "game.pause",
                            )
                        ],
                        failure_conditions=[],
                        timeout_seconds=3.0,
                        retry_budget=0,
                        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                    )
                ],
                entry_step_id="move",
                max_actions=1,
                max_wall_seconds=4.0,
                max_game_seconds=5.0,
                risk_budget=RiskBudget(
                    max_pointer_actions=0,
                    max_purchase_actions=0,
                    max_native_assisted_actions=0,
                ),
            )

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = OneHumanInterruptionEnvironment(clock=plan_clock)
        planner = ReplanningMovementPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            automatic_takeover_enabled=True,
            concurrent_option_planning_enabled=False,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=3))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            pump_clock.advance(0.1)
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert environment.movement_cancelled.is_set()
        assert planner.calls == 2
        assert summary.terminated
        assert environment.paused is True
        assert (
            len([action for action in environment.actions if isinstance(action, SkillAction)]) == 1
        )
        events = read_events(tmp_path / "events.jsonl")
        ownership = [
            event
            for event in events
            if event["event_type"]
            in {
                "control_ownership_changed",
                "agent_takeover_countdown",
                "agent_takeover_ready",
            }
        ]
        assert [
            event["payload"]["state"]
            for event in ownership
            if event["event_type"] == "control_ownership_changed"
        ] == ["human_control", "takeover_pending", "agent_active"]
        assert any(
            event["event_type"] == "agent_takeover_countdown"
            for event in ownership
        )
        assert any(event["event_type"] == "agent_takeover_ready" for event in ownership)
        assert sum(
            event["event_type"] == "safety_supervisor_finished"
            for event in events
        ) == 2

    asyncio.run(scenario())


def test_human_input_during_a_confirmed_safety_pause_yields_then_replans(
    tmp_path: Path,
) -> None:
    class PausedHandoffEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: PlanningClock) -> None:
            super().__init__(clock=clock)
            self.movement_started = asyncio.Event()
            self.movement_cancelled = asyncio.Event()
            self.emit_catastrophe = False
            self.emit_human_input = False
            self.unsafe = False

        def observation(self) -> Observation:
            current = super().observation()
            if not self.unsafe or current.telemetry is None:
                return current
            return current.model_copy(
                update={
                    "telemetry": current.telemetry.model_copy(
                        update={
                            "squad": [
                                CharacterState(
                                    id="entity-bark",
                                    name="Bark",
                                    alive=True,
                                    conscious=True,
                                    getting_eaten=True,
                                )
                            ]
                        }
                    )
                }
            )

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            events: list[str] = []
            if self.emit_catastrophe:
                self.emit_catastrophe = False
                self.unsafe = True
            if self.emit_human_input:
                self.emit_human_input = False
                events.append("human_input_detected")
            return self.observation().model_copy(update={"events": events})

        async def step(self, action: Action) -> Transition:
            if not isinstance(action, SkillAction):
                return await super().step(action)
            self.actions.append(action)
            self.paused = False
            self.sequence += 1
            self.movement_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.movement_cancelled.set()
                raise
            raise AssertionError("Cancelled movement unexpectedly resumed.")

    class PausedHandoffPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0
            self.safety_replan_started = asyncio.Event()
            self.safety_replan_cancelled = asyncio.Event()

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                plan = patchable_movement_plan(current)
                movement = plan.steps[0].model_copy(update={"on_success": None})
                return plan.model_copy(
                    update={"steps": [movement], "max_actions": 1},
                    deep=True,
                )
            if self.calls == 2:
                self.safety_replan_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.safety_replan_cancelled.set()
                    raise
                raise AssertionError("Safety replan unexpectedly resumed.")
            return PlannerDecision(
                intent="Stop after proving the paused human handoff.",
                rationale="Fresh planning resumed only after control returned.",
                action=StopAction(reason="Paused handoff proof complete."),
                confidence=1.0,
            )

    async def scenario() -> None:
        plan_clock = ManualPumpClock()
        pump_clock = ManualPumpClock()
        environment = PausedHandoffEnvironment(clock=plan_clock)
        planner = PausedHandoffPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            automatic_takeover_enabled=True,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=4))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            environment.emit_catastrophe = True
            pump_clock.advance(0.1)
            await asyncio.wait_for(planner.safety_replan_started.wait(), timeout=1.0)

            environment.emit_human_input = True
            pump_clock.advance(0.1)
            await asyncio.wait_for(planner.safety_replan_cancelled.wait(), timeout=1.0)
            for _ in range(5):
                await asyncio.sleep(0)

            # Give handback revalidation a causally later paused observation,
            # then complete the visible quiet interval and countdown.
            pump_clock.advance(0.1)
            for _ in range(5):
                await asyncio.sleep(0)
            plan_clock.advance(0.1)
            for _ in range(5):
                await asyncio.sleep(0)
            plan_clock.advance(0.3)
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert environment.movement_cancelled.is_set()
        assert summary.terminated
        assert summary.stop_reason == "Paused handoff proof complete."
        assert planner.calls == 3
        assert [
            action.paused
            for action in environment.actions
            if isinstance(action, PauseAction)
        ] == [True]
        events = read_events(tmp_path / "events.jsonl")
        human_preemption = next(
            event
            for event in events
            if event["event_type"] == "safety_supervisor_preempted"
            and event["payload"]["cause"] == "human_input"
        )
        assert human_preemption["payload"]["decision"]["action"] == {
            "kind": "pause",
            "paused": True,
        }
        assert sum(
            event["event_type"] == "safety_pause_already_confirmed"
            for event in events
        ) == 1
        assert not any(
            event["event_type"] == "safety_supervisor_terminal"
            and event["payload"]["cause"] == "human_input"
            for event in events
        )
        ownership_states = [
            event["payload"]["state"]
            for event in events
            if event["event_type"] == "control_ownership_changed"
        ]
        assert ownership_states[-3:] == [
            "human_control",
            "takeover_pending",
            "agent_active",
        ]

    asyncio.run(scenario())


def test_movement_option_overlaps_and_applies_a_valid_future_patch(
    tmp_path: Path,
) -> None:
    class PatchableMovementEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: FakeClock) -> None:
            super().__init__(clock=clock)
            self.movement_started = asyncio.Event()
            self.release_movement = asyncio.Event()

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            return self.observation()

        async def step(self, action: Action) -> Transition:
            if not isinstance(action, SkillAction):
                return await super().step(action)
            self.actions.append(action)
            self.movement_started.set()
            await self.release_movement.wait()
            self.step_index += 1
            self.sequence += 1
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.INTERFACE_ONLY,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=2,
                    message="fake movement completed and remained paused",
                ),
                observation=self.observation(),
            )

    class PatchingPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0
            self.advisory_returned = asyncio.Event()

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return patchable_movement_plan(current)
            assert current.active_plan is not None
            assert current.active_plan.active_step_id == "move"
            self.advisory_returned.set()
            return PlanPatch(
                schema_version="1.0",
                plan_id=current.active_plan.plan_id,
                based_on_plan_version=current.active_plan.plan_version,
                based_on_revision=current.world_revision,
                replace_future_steps=[
                    PlanStep(
                        step_id="patched-speed",
                        action=SetSpeedAction(speed=3),
                        preconditions=[
                            condition(
                                "telemetry.game.paused",
                                True,
                                "game.pause",
                            )
                        ],
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
                    )
                ],
                rationale="The future speed choice can be updated without restarting movement.",
            )

    async def scenario() -> None:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = PatchableMovementEnvironment(clock=clock)
        planner = PatchingPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            await asyncio.wait_for(planner.advisory_returned.wait(), timeout=1.0)
            for _ in range(5):
                await asyncio.sleep(0)
                if any(
                    event["event_type"] == "plan_patch_staged"
                    for event in read_events(tmp_path / "events.jsonl")
                ):
                    break
            assert any(
                event["event_type"] == "plan_patch_staged"
                for event in read_events(tmp_path / "events.jsonl")
            )
            assert not any(isinstance(action, SetSpeedAction) for action in environment.actions)
            pump_clock.advance(0.1)
            await asyncio.sleep(0)
            environment.release_movement.set()
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert summary.steps_completed == 2
        assert planner.calls == 2
        assert [type(action) for action in environment.actions] == [
            SkillAction,
            SetSpeedAction,
        ]
        assert isinstance(environment.actions[1], SetSpeedAction)
        assert environment.actions[1].speed == 3
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "option_prepared" for event in events) == 1
        assert sum(event["event_type"] == "option_started" for event in events) == 1
        assert sum(event["event_type"] == "option_progress" for event in events) >= 1
        assert sum(event["event_type"] == "option_succeeded" for event in events) == 1
        staged_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "plan_patch_staged"
        )
        succeeded_index = next(
            index for index, event in enumerate(events) if event["event_type"] == "option_succeeded"
        )
        patched_index = next(
            index for index, event in enumerate(events) if event["event_type"] == "plan_patched"
        )
        assert staged_index < succeeded_index < patched_index
        assert sum(event["event_type"] == "plan_patch_rejected" for event in events) == 0
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.strategic_planner_calls == 2
        assert metrics.plan_patches_staged == 1
        assert metrics.plan_patches_applied == 1
        assert metrics.plan_patches_rejected == 0
        assert metrics.option_progress_updates >= 1
        assert metrics.options_succeeded == 1
        assert metrics.option_success_percentage == 100.0
        replayed = replay_plan_lifecycle(tmp_path / "events.jsonl")
        assert replayed["patchable-movement"].plan_version == 2
        assert replayed["patchable-movement"].status == "completed"
        assert replayed["patchable-movement"].succeeded_step_ids == [
            "move",
            "patched-speed",
        ]

    asyncio.run(scenario())


def test_short_option_finishes_before_concurrent_planner_holdoff(
    tmp_path: Path,
) -> None:
    class ShortMovementEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: FakeClock) -> None:
            super().__init__(clock=clock)
            self.movement_started = asyncio.Event()
            self.release_movement = asyncio.Event()

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            return self.observation()

        async def step(self, action: Action) -> Transition:
            if not isinstance(action, SkillAction):
                return await super().step(action)
            self.actions.append(action)
            self.movement_started.set()
            await self.release_movement.wait()
            self.step_index += 1
            self.sequence += 1
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.INTERFACE_ONLY,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=2,
                ),
                observation=self.observation(),
            )

    class CountingPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return patchable_movement_plan(current)
            raise AssertionError("short movement must not start a concurrent planner call")

    async def scenario() -> None:
        environment_clock = FakeClock()
        planning_clock = ManualPumpClock()
        pump_clock = ManualPumpClock()
        environment = ShortMovementEnvironment(clock=environment_clock)
        planner = CountingPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            planning_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            concurrent_option_planning_delay_seconds=20.0,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            for _ in range(5):
                await asyncio.sleep(0)
            pump_clock.advance(0.1)
            await asyncio.sleep(0)
            environment.release_movement.set()
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert summary.steps_completed == 2
        assert planner.calls == 1
        events = read_events(tmp_path / "events.jsonl")
        assert not any(
            event["event_type"] == "concurrent_planner_discarded"
            for event in events
        )

    asyncio.run(scenario())


def test_single_action_plan_never_starts_a_concurrent_planner_call(
    tmp_path: Path,
) -> None:
    class BlockingMovementEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: FakeClock) -> None:
            super().__init__(clock=clock)
            self.movement_started = asyncio.Event()
            self.release_movement = asyncio.Event()

        async def step(self, action: Action) -> Transition:
            if not isinstance(action, SkillAction):
                return await super().step(action)
            self.actions.append(action)
            self.movement_started.set()
            await self.release_movement.wait()
            self.step_index += 1
            self.sequence += 1
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.INTERFACE_ONLY,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=2,
                ),
                observation=self.observation(),
            )

    class CountingPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls != 1:
                raise AssertionError("a spent plan has no future authority to revise")
            plan = patchable_movement_plan(current)
            movement = plan.steps[0].model_copy(update={"on_success": None})
            return plan.model_copy(
                update={"steps": [movement], "max_actions": 1},
                deep=True,
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = BlockingMovementEnvironment(clock=clock)
        planner = CountingPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            run = asyncio.create_task(runtime.run(max_steps=1))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            for _ in range(5):
                await asyncio.sleep(0)
            environment.release_movement.set()
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert summary.steps_completed == 1
        assert planner.calls == 1
        events = read_events(tmp_path / "events.jsonl")
        assert not any(
            event["event_type"] == "strategic_planner_call"
            and event["payload"].get("source") == "concurrent_option"
            for event in events
        )

    asyncio.run(scenario())


def approach_plan(observation: Observation) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="approach-proof",
        plan_version=1,
        objective="Approach the confirmed Barman and open dialogue.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="approach",
                action=SkillAction(
                    name="mock_approach",
                    args=[SkillArgument(name="target_id", value="entity-barman")],
                ),
                preconditions=[condition("telemetry.game.paused", True, "game.pause")],
                success_conditions=[
                    condition("telemetry.ui.dialogue_open", True, "ui.dialogue")
                ],
                failure_conditions=[],
                timeout_seconds=5.0,
                retry_budget=0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        entry_step_id="approach",
        max_actions=2,
        max_wall_seconds=10.0,
        max_game_seconds=10.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )


class ApproachEnvironment(RevisionEnvironment):
    """The Barman closes distance across pump updates, then dialogue opens."""

    def __init__(self, *, clock: FakeClock) -> None:
        super().__init__(clock=clock)
        self.barman_distance = 40.0
        self.dispatched = asyncio.Event()
        self._closes = [18.0, 3.0]

    def observation(self) -> Observation:
        obs = super().observation()
        telemetry = obs.telemetry
        assert telemetry is not None
        dialogue_open = self.barman_distance <= 5.0
        barman = NearbyEntity(
            id="entity-barman",
            name="Barman",
            is_animal=False,
            has_vendor_list=True,
            is_squad_leader=True,
            has_dialogue=True,
            disposition=Disposition.NEUTRAL,
            distance=self.barman_distance,
        )
        new_telemetry = telemetry.model_copy(
            update={
                "nearby_entities": [barman],
                "capabilities": [
                    *telemetry.capabilities,
                    "control.approach_vendor",
                    "nearby.roles",
                    "ui.dialogue",
                ],
                "ui": telemetry.ui.model_copy(
                    update={
                        "dialogue_open": dialogue_open,
                        "dialogue_target_id": ("entity-barman" if dialogue_open else None),
                    }
                ),
            }
        )
        return obs.model_copy(update={"telemetry": new_telemetry}, deep=True)

    async def observe_without_capture(self) -> Observation:
        self.sequence += 1
        if self.dispatched.is_set() and self._closes:
            self.barman_distance = self._closes.pop(0)
        return self.observation()

    async def step(self, action: Action) -> Transition:
        if isinstance(action, SkillAction) and action.name == "mock_approach":
            self.actions.append(action)
            self.dispatched.set()
            self.sequence += 1
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.INTERFACE_ONLY,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=0,
                    message="approach order issued",
                ),
                observation=self.observation(),
            )
        return await super().step(action)


def test_approach_option_reaches_success_by_closing_distance_and_dialogue(
    tmp_path: Path,
) -> None:
    class ApproachPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return approach_plan(current)
            return PlannerDecision(
                intent="stop",
                rationale="Approach reached dialogue; the test is complete.",
                action=StopAction(reason="approach test complete"),
                confidence=1.0,
            )

    async def scenario() -> None:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = ApproachEnvironment(clock=clock)
        planner = ApproachPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            concurrent_option_planning_enabled=False,
            stateful_approach_options_enabled=True,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.dispatched.wait(), timeout=1.0)
            for _ in range(8):
                pump_clock.advance(0.1)
                await asyncio.sleep(0)
                if any(
                    event["event_type"] == "option_succeeded"
                    for event in read_events(tmp_path / "events.jsonl")
                ):
                    break
            await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        events = read_events(tmp_path / "events.jsonl")
        # It ran as the approach option, not movement or plain dispatch.
        started = [e for e in events if e["event_type"] == "option_started"]
        assert len(started) == 1
        assert "approach-" in started[0]["payload"]["evidence"]["option_id"]
        assert sum(e["event_type"] == "option_prepared" for e in events) == 1
        assert sum(e["event_type"] == "option_progress" for e in events) >= 1
        assert sum(e["event_type"] == "option_succeeded" for e in events) == 1
        assert sum(e["event_type"] == "option_failed" for e in events) == 0
        # The approach order was issued exactly once (no duplicate on arrival).
        assert [a.name for a in environment.actions if isinstance(a, SkillAction)] == [
            "mock_approach"
        ]

    asyncio.run(scenario())


class NativeDirectionEnvironment(RevisionEnvironment):
    """Accept a bare-point order, then publish its keyed arrival."""

    def __init__(
        self,
        *,
        clock: FakeClock,
        complete_on_observe: bool = True,
    ) -> None:
        super().__init__(clock=clock, control_mode=ControlMode.NATIVE_ASSISTED)
        self.dispatched = asyncio.Event()
        self.command: CommandDispatchContext | None = None
        self.complete_on_observe = complete_on_observe
        self.completed = False
        self.cancelled = False

    def _acknowledgement(
        self,
        status: NativeCommandStatus,
    ) -> NativeCommandAcknowledgement:
        assert self.command is not None
        terminal = status in {
            NativeCommandStatus.COMPLETED,
            NativeCommandStatus.CANCELLED,
        }
        return NativeCommandAcknowledgement(
            command_id=self.command.command_id,
            command="move_in_direction",
            status=status,
            reason=(
                NATIVE_WALK_DESTINATION_REACHED_RESULT
                if status is NativeCommandStatus.COMPLETED
                else "plan_patch_interrupted"
                if status is NativeCommandStatus.CANCELLED
                else "issued"
            ),
            target_id="",
            bearing_degrees=90.0,
            distance_units=250.0,
            selected_character_ids=["entity-hep"],
            based_on_telemetry_sequence=(
                self.command.based_on_revision.telemetry_sequence or 0
            ),
            acknowledged_at_telemetry_sequence=2,
            accepted_at_telemetry_sequence=2,
            terminal_at_telemetry_sequence=self.sequence if terminal else None,
        )

    def observation(self) -> Observation:
        obs = super().observation()
        telemetry = obs.telemetry
        assert telemetry is not None
        acknowledgement = (
            self._acknowledgement(
                NativeCommandStatus.CANCELLED
                if self.cancelled
                else NativeCommandStatus.COMPLETED
                if self.completed
                else NativeCommandStatus.ACCEPTED
            )
            if self.command is not None
            else None
        )
        native_control = NativeControlState(
            active_command_id=(
                self.command.command_id
                if self.command is not None
                and not self.completed
                and not self.cancelled
                else None
            ),
            acknowledgements=(
                [acknowledgement] if acknowledgement is not None else []
            ),
            last_command_sequence=1 if acknowledgement is not None else 0,
            last_command=(
                "move_in_direction" if acknowledgement is not None else None
            ),
            last_result=(
                "plan_patch_interrupted"
                if self.cancelled
                else NATIVE_WALK_DESTINATION_REACHED_RESULT
                if self.completed
                else ("issued" if acknowledgement is not None else None)
            ),
        )
        return obs.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "capabilities": [
                            *telemetry.capabilities,
                            "control.move_in_direction",
                            "squad.health",
                        ],
                        "game": telemetry.game.model_copy(
                            update={"loaded": True}
                        ),
                        "ui": telemetry.ui.model_copy(
                            update={
                                "selected_character_id": "entity-hep",
                                "selected_character_ids": ["entity-hep"],
                            }
                        ),
                        "squad": [
                            CharacterState(
                                id="entity-hep",
                                name="Hep",
                                selected=True,
                                alive=True,
                            )
                        ],
                        "native_control": native_control,
                    }
                )
            },
            deep=True,
        )

    async def observe_without_capture(self) -> Observation:
        self.sequence += 1
        if self.complete_on_observe and self.dispatched.is_set():
            self.completed = True
        return self.observation()

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None = None,
    ) -> Transition:
        del token
        if isinstance(action, PauseAction):
            self.actions.append(action)
            self.paused = action.paused
            self.cancelled = action.paused
            self.sequence += 1
            acknowledgement = (
                self._acknowledgement(NativeCommandStatus.CANCELLED)
                if self.command is not None
                else None
            )
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.NATIVE_ASSISTED,
                    command_id=command.command_id,
                    started_after_revision=command.based_on_revision,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=1,
                    message="paused and cancelled the active native order",
                    native_acknowledgement=acknowledgement,
                ),
                observation=self.observation(),
            )
        assert isinstance(action, MoveInDirectionAction)
        self.command = command
        self.actions.append(action)
        if not self.complete_on_observe:
            self.paused = False
        self.sequence = 2
        self.dispatched.set()
        acknowledgement = self._acknowledgement(NativeCommandStatus.ACCEPTED)
        return Transition(
            receipt=ActionReceipt(
                action=action,
                control_mode=ControlMode.NATIVE_ASSISTED,
                command_id=command.command_id,
                started_after_revision=command.based_on_revision,
                accepted=True,
                executed=True,
                dry_run=False,
                primitive_actions=1,
                message="targetless direction order issued",
                native_acknowledgement=acknowledgement,
            ),
            observation=self.observation(),
        )


def test_targetless_direction_is_owned_until_its_native_arrival(
    tmp_path: Path,
) -> None:
    class DirectionPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return PlanEnvelope(
                    schema_version="1.0",
                    plan_id="direction-proof",
                    plan_version=1,
                    objective="Walk east without inventing a target character.",
                    control_mode=current.control_mode,
                    based_on_revision=current.world_revision,
                    assumptions=[fresh()],
                    steps=[
                        PlanStep(
                            step_id="walk-east",
                            action=MoveInDirectionAction(
                                bearing_degrees=90.0,
                                distance_units=250.0,
                                expected_effect="leave the current building",
                            ),
                            preconditions=[
                                condition(
                                    "telemetry.game.paused",
                                    True,
                                    "game.pause",
                                )
                            ],
                            success_conditions=[
                                condition(
                                    "telemetry.native_control.last_result",
                                    NATIVE_WALK_DESTINATION_REACHED_RESULT,
                                    "control.move_in_direction",
                                )
                            ],
                            failure_conditions=[],
                            timeout_seconds=5.0,
                            retry_budget=0,
                            idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                        )
                    ],
                    entry_step_id="walk-east",
                    max_actions=1,
                    max_wall_seconds=10.0,
                    max_game_seconds=10.0,
                    risk_budget=RiskBudget(
                        max_pointer_actions=0,
                        max_purchase_actions=0,
                        max_native_assisted_actions=1,
                    ),
                )
            return PlannerDecision(
                intent="stop",
                rationale="The targetless movement reached its native terminal state.",
                action=StopAction(reason="direction test complete"),
                confidence=1.0,
            )

    async def scenario() -> None:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = NativeDirectionEnvironment(clock=clock)
        planner = DirectionPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            control_mode=ControlMode.NATIVE_ASSISTED,
            max_native_assisted_actions_per_plan=1,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.dispatched.wait(), timeout=1.0)
            for _ in range(8):
                pump_clock.advance(0.1)
                await asyncio.sleep(0)
                if run.done():
                    break
            await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        events = read_events(tmp_path / "events.jsonl")
        started = [event for event in events if event["event_type"] == "option_started"]
        assert len(started) == 1
        assert "native-movement-" in started[0]["payload"]["evidence"]["option_id"]
        assert sum(event["event_type"] == "option_succeeded" for event in events) == 1
        assert sum(event["event_type"] == "plan_step_succeeded" for event in events) == 1
        assert planner.calls == 2
        assert not any(
            event["event_type"] == "strategic_planner_call"
            and event["payload"].get("source") == "concurrent_option"
            for event in events
        )
        directions = [
            action
            for action in environment.actions
            if isinstance(action, MoveInDirectionAction)
        ]
        assert len(directions) == 1
        assert directions[0].bearing_degrees == 90.0

    asyncio.run(scenario())


def test_interruptible_native_move_applies_a_pause_handoff_before_replanning(
    tmp_path: Path,
) -> None:
    class InterruptingPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0
            self.advisory_started = asyncio.Event()
            self.release_advisory = asyncio.Event()

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return PlanEnvelope(
                    schema_version="1.0",
                    plan_id="responsive-direction",
                    plan_version=1,
                    objective="Change course safely when the situation changes.",
                    control_mode=current.control_mode,
                    based_on_revision=current.world_revision,
                    assumptions=[fresh()],
                    steps=[
                        PlanStep(
                            step_id="walk-east",
                            action=MoveInDirectionAction(
                                bearing_degrees=90.0,
                                distance_units=250.0,
                                expected_effect="move east until interrupted",
                            ),
                            preconditions=[
                                condition(
                                    "telemetry.game.paused",
                                    True,
                                    "game.pause",
                                )
                            ],
                            success_conditions=[
                                condition(
                                    "telemetry.native_control.last_result",
                                    NATIVE_WALK_DESTINATION_REACHED_RESULT,
                                    "control.move_in_direction",
                                )
                            ],
                            failure_conditions=[],
                            timeout_seconds=5.0,
                            retry_budget=0,
                            idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                            interrupt_policy=(
                                InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH
                            ),
                        )
                    ],
                    entry_step_id="walk-east",
                    # One action is executing; the second is reserved for the
                    # deterministic pause handoff an interrupt requires.
                    max_actions=2,
                    max_wall_seconds=10.0,
                    max_game_seconds=10.0,
                    risk_budget=RiskBudget(
                        max_pointer_actions=0,
                        max_purchase_actions=0,
                        max_native_assisted_actions=1,
                    ),
                )
            if current.active_plan is not None:
                assert current.active_plan.active_step_id == "walk-east"
                assert (
                    current.active_plan.active_step_interrupt_policy
                    is InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH
                )
                self.advisory_started.set()
                await self.release_advisory.wait()
                return PlanPatch(
                    schema_version="1.0",
                    plan_id=current.active_plan.plan_id,
                    based_on_plan_version=current.active_plan.plan_version,
                    based_on_revision=current.world_revision,
                    interrupt_active_step_id=current.active_plan.active_step_id,
                    replace_future_steps=[
                        PlanStep(
                            step_id="pause-interrupted-walk",
                            action=PauseAction(paused=True),
                            preconditions=[
                                condition(
                                    "telemetry.game.paused",
                                    False,
                                    "game.pause",
                                )
                            ],
                            success_conditions=[
                                condition(
                                    "telemetry.game.paused",
                                    True,
                                    "game.pause",
                                ),
                                condition(
                                    "telemetry.native_control.command_active",
                                    False,
                                    "control.move_in_direction",
                                ),
                            ],
                            failure_conditions=[],
                            timeout_seconds=2.0,
                            retry_budget=0,
                            idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                        )
                    ],
                    rationale=(
                        "Interrupt the exact active walk, pause it causally, then "
                        "replan from the stopped state."
                    ),
                )
            return PlannerDecision(
                intent="stop",
                rationale="The interruption handoff was proven.",
                action=StopAction(reason="responsive movement proof complete"),
                confidence=1.0,
            )

    async def scenario() -> None:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = NativeDirectionEnvironment(
            clock=clock,
            complete_on_observe=False,
        )
        planner = InterruptingPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            control_mode=ControlMode.NATIVE_ASSISTED,
            max_native_assisted_actions_per_plan=1,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=3))
            await asyncio.wait_for(environment.dispatched.wait(), timeout=1.0)
            await asyncio.wait_for(planner.advisory_started.wait(), timeout=1.0)
            # Make the immutable planner snapshot older while the exact same
            # plan and step remain active. Responsiveness cannot require the
            # world to freeze while the planner thinks.
            pump_clock.advance(0.1)
            await asyncio.sleep(0)
            planner.release_advisory.set()
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert summary.terminated
        assert environment.cancelled
        assert environment.paused
        assert [type(action) for action in environment.actions[:2]] == [
            MoveInDirectionAction,
            PauseAction,
        ]
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "plan_interrupt_staged" for event in events) == 1
        assert sum(event["event_type"] == "plan_step_interrupted" for event in events) == 1
        assert sum(event["event_type"] == "plan_patched" for event in events) == 1
        assert sum(event["event_type"] == "option_succeeded" for event in events) == 0
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.plan_patches_staged == 1
        assert metrics.plan_steps_cancelled == 1
        assert metrics.options_cancelled == 1

    asyncio.run(scenario())


def test_native_move_timeout_pauses_before_the_planner_can_run_again(
    tmp_path: Path,
) -> None:
    class TimeoutPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0
            self.replanned_from_paused: bool | None = None

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return PlanEnvelope(
                    schema_version="1.0",
                    plan_id="timeout-direction",
                    objective="Bound an obstructed directional walk.",
                    control_mode=current.control_mode,
                    based_on_revision=current.world_revision,
                    assumptions=[fresh()],
                    steps=[
                        PlanStep(
                            step_id="walk-east",
                            action=MoveInDirectionAction(
                                bearing_degrees=90.0,
                                distance_units=250.0,
                                expected_effect="move east if the path is open",
                            ),
                            preconditions=[
                                condition(
                                    "telemetry.game.paused",
                                    True,
                                    "game.pause",
                                )
                            ],
                            success_conditions=[
                                condition(
                                    "telemetry.native_control.last_result",
                                    NATIVE_WALK_DESTINATION_REACHED_RESULT,
                                    "control.move_in_direction",
                                )
                            ],
                            failure_conditions=[],
                            timeout_seconds=0.01,
                            retry_budget=0,
                            idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                        )
                    ],
                    entry_step_id="walk-east",
                    max_actions=1,
                    max_wall_seconds=1.0,
                    max_game_seconds=10.0,
                    risk_budget=RiskBudget(
                        max_pointer_actions=0,
                        max_purchase_actions=0,
                        max_native_assisted_actions=1,
                    ),
                )
            assert current.telemetry is not None
            self.replanned_from_paused = current.telemetry.game.paused
            return PlannerDecision(
                intent="stop",
                rationale="The timeout recovery pause was proven.",
                action=StopAction(reason="timeout ownership proof complete"),
                confidence=1.0,
            )

    async def scenario() -> None:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = NativeDirectionEnvironment(
            clock=clock,
            complete_on_observe=False,
        )
        planner = TimeoutPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            concurrent_option_planning_enabled=False,
            control_mode=ControlMode.NATIVE_ASSISTED,
            max_native_assisted_actions_per_plan=1,
        )
        try:
            summary = await asyncio.wait_for(runtime.run(max_steps=3), timeout=1.0)
        finally:
            logger.close()

        assert summary.terminated
        assert planner.replanned_from_paused is True
        assert [type(action) for action in environment.actions[:2]] == [
            MoveInDirectionAction,
            PauseAction,
        ]
        events = read_events(tmp_path / "events.jsonl")
        failed = [
            event for event in events if event["event_type"] == "option_failed"
        ]
        assert len(failed) == 1
        assert "timed out" in str(failed[0]["payload"]).lower()

    asyncio.run(scenario())


def test_concurrent_future_patch_revalidates_after_unrelated_world_advance(
    tmp_path: Path,
) -> None:
    class AdvancingMovementEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: FakeClock) -> None:
            super().__init__(clock=clock)
            self.movement_started = asyncio.Event()
            self.release_movement = asyncio.Event()

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            return self.observation()

        async def step(self, action: Action) -> Transition:
            if not isinstance(action, SkillAction):
                return await super().step(action)
            self.actions.append(action)
            self.movement_started.set()
            await self.release_movement.wait()
            self.step_index += 1
            self.sequence += 1
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.INTERFACE_ONLY,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=2,
                ),
                observation=self.observation(),
            )

    class RebasingPatchPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0
            self.advisory_returned = asyncio.Event()
            self.advisory_started = asyncio.Event()
            self.release_advisory = asyncio.Event()

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return patchable_movement_plan(current)
            assert current.active_plan is not None
            self.advisory_started.set()
            await self.release_advisory.wait()
            self.advisory_returned.set()
            return PlanPatch(
                schema_version="1.0",
                plan_id=current.active_plan.plan_id,
                based_on_plan_version=current.active_plan.plan_version,
                based_on_revision=current.world_revision,
                replace_future_steps=[
                    PlanStep(
                        step_id="rebased-speed",
                        action=SetSpeedAction(speed=3),
                        preconditions=[
                            condition(
                                "telemetry.game.paused",
                                True,
                                "game.pause",
                            )
                        ],
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
                    )
                ],
                rationale="Future intent remains valid after unrelated movement updates.",
            )

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = AdvancingMovementEnvironment(clock=plan_clock)
        planner = RebasingPatchPlanner()
        stream = StringIO()
        reporter = ConsoleDecisionReporter(
            run_id="continuous",
            planner_name="scripted",
            model_name=None,
            stream=stream,
        )
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            reporter=reporter,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            await asyncio.wait_for(planner.advisory_started.wait(), timeout=1.0)
            pump_clock.advance(0.1)
            await asyncio.sleep(0)
            planner.release_advisory.set()
            await asyncio.wait_for(planner.advisory_returned.wait(), timeout=1.0)
            await asyncio.sleep(0)
            environment.release_movement.set()
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert summary.steps_completed == 2
        assert isinstance(environment.actions[1], SetSpeedAction)
        assert environment.actions[1].speed == 3
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "plan_patch_staged" for event in events) == 1
        assert sum(event["event_type"] == "plan_patched" for event in events) == 1
        assert sum(event["event_type"] == "plan_patch_rejected" for event in events) == 0
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.plan_patches_staged == 1
        assert metrics.plan_patches_applied == 1
        assert metrics.plan_patches_rejected == 0
        assert "!!! PLAN PATCH REJECTED !!!" not in stream.getvalue()

    asyncio.run(scenario())


def test_supervisor_reports_failure_when_pause_cannot_be_confirmed(
    tmp_path: Path,
) -> None:
    class UnconfirmablePauseEnvironment(RevisionEnvironment):
        def __init__(self, *, clock: FakeClock) -> None:
            super().__init__(clock=clock)
            self.unsafe = False

        def observation(self) -> Observation:
            current = super().observation()
            if not self.unsafe or current.telemetry is None:
                return current
            return current.model_copy(
                update={
                    "telemetry": current.telemetry.model_copy(
                        update={
                            "nearby_entities": [
                                NearbyEntity(
                                    id="threat",
                                    name="Hungry Bandit",
                                    disposition=Disposition.HOSTILE,
                                    distance=10.0,
                                    visible=True,
                                )
                            ]
                        }
                    )
                }
            )

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            self.paused = False
            self.unsafe = True
            return self.observation()

        async def step(self, action: Action) -> Transition:
            if not isinstance(action, PauseAction):
                return await super().step(action)
            self.actions.append(action)
            self.step_index += 1
            self.sequence += 1
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.INTERFACE_ONLY,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=1,
                    message="fake input without confirmed effect",
                ),
                observation=self.observation(),
            )

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = UnconfirmablePauseEnvironment(clock=plan_clock)
        planner = BlockedPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=3))
            await planner.started.wait()
            pump_clock.advance(0.1)
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            logger.close()

        assert planner.cancelled.is_set()
        assert summary.terminated
        assert environment.paused is False
        assert [
            action.paused for action in environment.actions if isinstance(action, PauseAction)
        ] == [True]
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "safety_cleanup_completed" for event in events) == 0
        assert sum(event["event_type"] == "safety_cleanup_failed" for event in events) == 1
        terminal = [
            event for event in events if event["event_type"] == "safety_supervisor_terminal"
        ]
        assert len(terminal) == 1
        assert terminal[0]["payload"]["status"] == "cleanup_failed"
        assert "causally later confirmed paused" in summary.stop_reason
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.safety_cleanups_started == 1
        assert metrics.safety_cleanups_completed == 0
        assert metrics.safety_cleanups_failed == 1
        assert metrics.safety_cleanup_success_percentage == 0.0

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
        receipts = [
            event["payload"]
            for event in events
            if event["event_type"] == "action_receipt"
            and event["payload"]["command_id"] is not None
        ]
        assert len(receipts) == 1
        assert isinstance(receipts[0]["command_id"], str)
        assert COMMAND_ID_PATTERN.fullmatch(receipts[0]["command_id"])
        assert receipts[0]["causal_revision_advanced"] is False
        assert receipts[0]["completed_at_revision"] == receipts[0]["started_after_revision"]
        outcomes = [
            event["payload"]
            for event in events
            if event["event_type"] == "action_outcome"
            and event["payload"]["action"]["kind"] == "pause"
        ]
        assert len(outcomes) == 1
        assert outcomes[0]["assessment"] == "unknown"
        assert "causally later" in outcomes[0]["feedback"]

    asyncio.run(scenario())


def test_stale_plan_output_is_rejected_without_executing_an_action(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = PlanThenStopPlanner(stale_basis=True)
        stream = StringIO()
        reporter = ConsoleDecisionReporter(
            run_id="continuous",
            planner_name="scripted",
            model_name=None,
            stream=stream,
        )
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            reporter=reporter,
        )
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert summary.terminated
        # A rejected plan now yields a replan rather than ending the session, so
        # the planner's own Stop may run. What must not happen is any action
        # that touches the game.
        assert not [
            action for action in environment.actions if not isinstance(action, StopAction)
        ]
        events = read_events(tmp_path / "events.jsonl")
        rejected = [event for event in events if event["event_type"] == "plan_rejected"]
        assert len(rejected) == 1
        assert "stale" in str(rejected[0]["payload"])
        assert "!!! PLAN REJECTED !!!" in stream.getvalue()
        assert "stale" in stream.getvalue()

    asyncio.run(scenario())


def test_hostility_does_not_preempt_a_normal_future_plan_step(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(
            clock=clock,
            threat_after_first_action=True,
        )
        planner = PlanThenStopPlanner()
        stream = StringIO()
        reporter = ConsoleDecisionReporter(
            run_id="continuous",
            planner_name="scripted",
            model_name=None,
            stream=stream,
        )
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            reporter=reporter,
        )
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert summary.steps_completed == 2
        assert planner.calls == 1
        assert [
            action.paused for action in environment.actions if isinstance(action, PauseAction)
        ] == [False]
        assert any(isinstance(action, SetSpeedAction) for action in environment.actions)
        events = read_events(tmp_path / "events.jsonl")
        assert not any(event["event_type"] == "safety_preempted" for event in events)
        assert not any(event["event_type"] == "plan_aborted" for event in events)
        assert "!!! PLAN ABORTED !!!" not in stream.getvalue()

    asyncio.run(scenario())


def test_planner_output_that_becomes_stale_during_call_is_rejected(
    tmp_path: Path,
) -> None:
    class AdvancingObserveEnvironment(RevisionEnvironment):
        async def observe(self) -> Observation:
            self.sequence += 1
            return self.observation()

    class BlockingPlanner(Planner):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.basis: WorldStateRevision | None = None

        async def decide(self, current: Observation) -> PlannerOutput:
            self.basis = current.world_revision
            self.started.set()
            await self.release.wait()
            return two_step_plan(current)

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = AdvancingObserveEnvironment(clock=plan_clock)
        planner = BlockingPlanner()
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await planner.started.wait()
            await asyncio.sleep(0)
            pump_clock.advance(0.1)
            await asyncio.sleep(0)
            planner.release.set()
            summary = await run
        finally:
            logger.close()

        # The plan that went stale during the planner call is rejected, and the
        # session continues rather than ending — for an agent meant to run
        # continuously, one unusable plan is not a reason to stop. The sibling
        # test covers that a rejected plan itself executes nothing.
        events = read_events(tmp_path / "events.jsonl")
        rejected = [event for event in events if event["event_type"] == "plan_rejected"]
        assert len(rejected) == 1
        assert "stale" in str(rejected[0]["payload"])
        assert summary.stop_reason

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Composable semantic-action chain (generic dialogue milestone)
#
# The proof this milestone actually owes: one bounded plan composing two
# reusable typed actions, with no strategic call between them and no macro
# name, vendor role, or fixed coordinate anywhere in the plan.
# ---------------------------------------------------------------------------

SEMANTIC_CAPABILITIES = (
    "control.approach_vendor",
    "identity.stable_handles",
    "nearby.characters",
    "nearby.roles",
    "ui.dialogue",
    "ui.dialogue.target",
    "ui.visible_controls",
)


def semantic_chain_plan(
    observation: Observation,
    *,
    target_id: str,
    label: str,
    runtime_owned_activation_completion: bool = False,
) -> PlanEnvelope:
    """Approach any valid target, then activate any advertised control."""

    return PlanEnvelope(
        schema_version="1.0",
        plan_id="composable-dialogue",
        plan_version=1,
        objective="Open dialogue with a valid current target and activate one control.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="approach",
                action=ApproachDialogueTargetAction(target_id=target_id),
                preconditions=[condition("telemetry.game.paused", True, "game.pause")],
                success_conditions=[
                    condition("telemetry.ui.dialogue_target_id", target_id, "ui.dialogue.target")
                ],
                failure_conditions=[],
                timeout_seconds=5.0,
                retry_budget=0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                on_success="activate",
            ),
            PlanStep(
                step_id="activate",
                action=ActivateVisibleControlAction(exact_label=label, role="button"),
                affordance=(
                    BoundAffordance(
                        affordance_id="aff-00000000000000000000",
                        source=AffordanceSource.DIALOGUE,
                        semantic="choose_dialogue",
                        execution=AffordanceExecution.IMMEDIATE,
                        operation_kind="activate_visible_control",
                        offered_at_telemetry_sequence=(
                            observation.world_revision.telemetry_sequence
                        ),
                    )
                    if runtime_owned_activation_completion
                    else None
                ),
                preconditions=[
                    condition("telemetry.ui.dialogue_open", True, "ui.dialogue")
                ],
                success_conditions=(
                    []
                    if runtime_owned_activation_completion
                    else [condition("telemetry.ui.active_screen", "trade", "ui.dialogue")]
                ),
                failure_conditions=[],
                timeout_seconds=5.0,
                retry_budget=0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            ),
        ],
        entry_step_id="approach",
        max_actions=3,
        max_wall_seconds=20.0,
        max_game_seconds=10.0,
        risk_budget=RiskBudget(
            max_pointer_actions=1,
            max_purchase_actions=0,
            max_native_assisted_actions=1,
        ),
    )


class SemanticChainEnvironment(RevisionEnvironment):
    """A target that closes distance, then a dialogue panel with two controls."""

    def __init__(
        self,
        *,
        clock: FakeClock,
        target_id: str = "entity-wanderer",
        vendor: bool = False,
        lose_target: bool = False,
        hostile_after_dispatch: bool = False,
    ) -> None:
        super().__init__(clock=clock, control_mode=ControlMode.NATIVE_ASSISTED)
        self.target_id = target_id
        self.vendor = vendor
        self.lose_target = lose_target
        self.hostile_after_dispatch = hostile_after_dispatch
        self.distance = 40.0
        self.dispatched = asyncio.Event()
        self.activated: list[Action] = []
        self._closes = [18.0, 3.0]

    def observation(self) -> Observation:
        obs = super().observation()
        telemetry = obs.telemetry
        assert telemetry is not None
        target_gone = self.lose_target and self.dispatched.is_set()
        # Dialogue cannot be open with someone who is no longer there.
        dialogue_open = self.distance <= 5.0 and not target_gone
        entities: list[NearbyEntity] = []
        if not target_gone:
            entities.append(
                NearbyEntity(
                    id=self.target_id,
                    name="Nomad Wanderer" if not self.vendor else "Barman",
                    is_animal=False,
                    has_vendor_list=self.vendor,
                    is_squad_leader=self.vendor,
                    has_dialogue=True,
                    disposition=Disposition.NEUTRAL,
                    distance=self.distance,
                    conscious=True,
                )
            )
        if self.hostile_after_dispatch and self.dispatched.is_set():
            entities.append(
                NearbyEntity(
                    id="entity-bandit",
                    name="Dust Bandit",
                    is_animal=False,
                    disposition=Disposition.HOSTILE,
                    distance=4.0,
                    conscious=True,
                )
            )
        controls = (
            [
                VisibleUIControl(
                    label="Show me your goods.",
                    role="button",
                    bounds=NormalizedPointerBounds(
                        min_x=0.1, max_x=0.4, min_y=0.5, max_y=0.55
                    ),
                ),
                VisibleUIControl(
                    label="Goodbye.",
                    role="button",
                    bounds=NormalizedPointerBounds(
                        min_x=0.1, max_x=0.4, min_y=0.6, max_y=0.65
                    ),
                ),
            ]
            if dialogue_open
            else None
        )
        new_telemetry = telemetry.model_copy(
            update={
                "identity_session_id": "session-semantic-chain",
                "nearby_entities": entities,
                "capabilities": [*telemetry.capabilities, *SEMANTIC_CAPABILITIES],
                "ui": telemetry.ui.model_copy(
                    update={
                        "active_screen": "trade" if self.activated else "world",
                        "dialogue_open": dialogue_open,
                        "dialogue_target_id": (self.target_id if dialogue_open else None),
                        "visible_controls": controls,
                    }
                ),
            }
        )
        return obs.model_copy(update={"telemetry": new_telemetry}, deep=True)

    async def observe_without_capture(self) -> Observation:
        self.sequence += 1
        if self.dispatched.is_set() and self._closes:
            self.distance = self._closes.pop(0)
        return self.observation()

    async def step(self, action: Action) -> Transition:
        if isinstance(action, ApproachDialogueTargetAction):
            self.actions.append(action)
            self.dispatched.set()
            self.sequence += 1
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.NATIVE_ASSISTED,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    primitive_actions=0,
                    message="native approach order issued",
                    semantic=SemanticActionReceipt(
                        action_kind=action.kind,
                        contract_version="1.0",
                        target_id=action.target_id,
                        revalidation="Bound to the exact stable dialogue target.",
                    ),
                ),
                observation=self.observation(),
            )
        if isinstance(action, ActivateVisibleControlAction):
            self.actions.append(action)
            self.activated.append(action)
            self.sequence += 1
            binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(action, self.observation())
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    control_mode=ControlMode.NATIVE_ASSISTED,
                    accepted=binding.bound,
                    executed=binding.bound,
                    dry_run=False,
                    primitive_actions=1 if binding.bound else 0,
                    message=binding.reason,
                    semantic=SemanticActionReceipt(
                        action_kind=action.kind,
                        contract_version="1.0",
                        resolved_label=binding.resolved_label,
                        resolved_role=binding.resolved_role,
                        resolved_bounds=binding.resolved_bounds,
                        revalidation=binding.reason,
                    ),
                ),
                observation=self.observation(),
            )
        return await super().step(action)


class SemanticChainPlanner(Planner):
    """One strategic call yields the whole composed chain; the rest just stops."""

    def __init__(
        self,
        *,
        target_id: str,
        label: str,
        runtime_owned_activation_completion: bool = False,
    ) -> None:
        self.target_id = target_id
        self.label = label
        self.runtime_owned_activation_completion = runtime_owned_activation_completion
        self.calls = 0

    async def decide(self, current: Observation) -> PlannerOutput:
        self.calls += 1
        if self.calls == 1:
            return semantic_chain_plan(
                current,
                target_id=self.target_id,
                label=self.label,
                runtime_owned_activation_completion=(
                    self.runtime_owned_activation_completion
                ),
            )
        return PlannerDecision(
            intent="stop",
            rationale="The composed chain finished.",
            action=StopAction(reason="semantic chain complete"),
            confidence=1.0,
        )


def _run_semantic_chain(
    tmp_path: Path,
    environment: SemanticChainEnvironment,
    *,
    target_id: str,
    label: str,
    runtime_owned_activation_completion: bool = False,
) -> tuple[list[dict[str, object]], SemanticChainPlanner]:
    async def scenario() -> SemanticChainPlanner:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        planner = SemanticChainPlanner(
            target_id=target_id,
            label=label,
            runtime_owned_activation_completion=runtime_owned_activation_completion,
        )
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            concurrent_option_planning_enabled=False,
            control_mode=ControlMode.NATIVE_ASSISTED,
            max_native_assisted_actions_per_plan=1,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=3))
            await asyncio.wait_for(environment.dispatched.wait(), timeout=1.0)
            for _ in range(16):
                pump_clock.advance(0.1)
                await asyncio.sleep(0)
                if run.done():
                    break
            await asyncio.wait_for(run, timeout=2.0)
        finally:
            logger.close()
        return planner

    planner = asyncio.run(scenario())
    return read_events(tmp_path / "events.jsonl"), planner


def test_one_plan_composes_approach_and_control_activation(tmp_path: Path) -> None:
    """The milestone's core proof: two reusable actions, one strategic call."""

    environment = SemanticChainEnvironment(clock=FakeClock(), target_id="entity-wanderer")
    events, planner = _run_semantic_chain(
        tmp_path,
        environment,
        target_id="entity-wanderer",
        label="Show me your goods.",
    )

    # The approach ran as a monitored option and issued its order exactly once.
    started = [e for e in events if e["event_type"] == "option_started"]
    assert len(started) == 1
    assert "approach-" in started[0]["payload"]["evidence"]["option_id"]
    assert sum(e["event_type"] == "option_succeeded" for e in events) == 1
    approaches = [a for a in environment.actions if isinstance(a, ApproachDialogueTargetAction)]
    assert len(approaches) == 1

    # The control activation followed in the same plan.
    activations = [a for a in environment.actions if isinstance(a, ActivateVisibleControlAction)]
    assert [a.exact_label for a in activations] == ["Show me your goods."]

    # One strategic call produced the whole chain; the second call only stopped.
    assert sum(e["event_type"] == "strategic_planner_called" for e in events) <= planner.calls
    assert sum(e["event_type"] == "plan_step_succeeded" for e in events) == 2
    assert sum(e["event_type"] == "plan_completed" for e in events) == 1


def test_the_same_actions_compose_for_a_vendor_target_and_another_label(
    tmp_path: Path,
) -> None:
    """Reuse, not a second implementation: different target, different label."""

    environment = SemanticChainEnvironment(
        clock=FakeClock(), target_id="entity-barman", vendor=True
    )
    events, _ = _run_semantic_chain(
        tmp_path,
        environment,
        target_id="entity-barman",
        label="Goodbye.",
    )

    approaches = [a for a in environment.actions if isinstance(a, ApproachDialogueTargetAction)]
    activations = [a for a in environment.actions if isinstance(a, ActivateVisibleControlAction)]
    assert [a.target_id for a in approaches] == ["entity-barman"]
    assert [a.exact_label for a in activations] == ["Goodbye."]
    assert sum(e["event_type"] == "plan_completed" for e in events) == 1


def test_selected_visible_control_uses_runtime_delivery_terminal(tmp_path: Path) -> None:
    environment = SemanticChainEnvironment(clock=FakeClock(), target_id="entity-wanderer")
    events, _ = _run_semantic_chain(
        tmp_path,
        environment,
        target_id="entity-wanderer",
        label="Show me your goods.",
        runtime_owned_activation_completion=True,
    )

    delivery = [
        event
        for event in events
        if event["event_type"] == "plan_step_progress"
        and event["payload"].get("evidence", {}).get("completion_owner")
        == "affordance_delivery"
    ]
    assert len(delivery) == 1
    assert delivery[0]["payload"]["evidence"] == {
        "completion_owner": "affordance_delivery",
        "accepted": True,
        "executed": True,
        "causal_revision_advanced": True,
        "effect_verified": False,
    }
    assert sum(e["event_type"] == "plan_completed" for e in events) == 1


def test_target_loss_fails_the_approach_option(tmp_path: Path) -> None:
    environment = SemanticChainEnvironment(
        clock=FakeClock(), target_id="entity-wanderer", lose_target=True
    )
    events, _ = _run_semantic_chain(
        tmp_path,
        environment,
        target_id="entity-wanderer",
        label="Show me your goods.",
    )

    assert sum(e["event_type"] == "option_failed" for e in events) == 1
    assert sum(e["event_type"] == "option_succeeded" for e in events) == 0
    # No control was activated after the approach failed.
    assert not [a for a in environment.actions if isinstance(a, ActivateVisibleControlAction)]


def test_hostile_in_threat_range_fails_the_approach_option(tmp_path: Path) -> None:
    environment = SemanticChainEnvironment(
        clock=FakeClock(),
        target_id="entity-wanderer",
        hostile_after_dispatch=True,
    )
    events, _ = _run_semantic_chain(
        tmp_path,
        environment,
        target_id="entity-wanderer",
        label="Show me your goods.",
    )

    failed = [e for e in events if e["event_type"] == "option_failed"]
    assert len(failed) == 1
    assert "hostile" in str(failed[0]["payload"]).lower()
    assert not [a for a in environment.actions if isinstance(a, ActivateVisibleControlAction)]


def test_a_malformed_planner_response_is_retried_not_fatal(tmp_path: Path) -> None:
    """One bad answer must not end a session meant to run continuously.

    A schema slip, a single action where a plan was needed, or a patch with no
    plan to patch are all *bad answers*. Ending the run on the first one meant a
    stream died to a transient model mistake; the replan limit is what bounds a
    planner that genuinely cannot produce a usable response.
    """

    class FlakyThenGoodPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, observation: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                raise ValueError("invalid structured output")
            if self.calls == 2:
                # A single action where continuous mode needs a plan.
                return PlannerDecision(
                    intent="move",
                    rationale="Wrong shape for continuous mode.",
                    action=SetSpeedAction(speed=2),
                    confidence=1.0,
                )
            return PlannerDecision(
                intent="stop",
                rationale="Recovered and finished.",
                action=StopAction(reason="done after recovering"),
                confidence=1.0,
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = FlakyThenGoodPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            summary = await runtime.run(max_steps=5)
        finally:
            logger.close()

        # It kept going through both malformed responses and reached the third.
        assert planner.calls == 3
        assert summary.stop_reason == "done after recovering"

        events = read_events(tmp_path / "events.jsonl")
        # The failed call is still in the replay record.
        sources = [
            event["payload"]["source"]
            for event in events
            if event["event_type"] == "strategic_planner_call"
        ]
        assert "planner_error" in sources
        assert any(event["event_type"] == "planner_error" for event in events)

    asyncio.run(scenario())


def test_an_accepted_plan_leaves_a_trace_the_next_plan_can_read(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Continuity was structurally impossible, not merely neglected.

    Continuity operations existed only on `PlannerDecision`, which single-step
    runs use, so a continuous run recalled the memory store into every
    observation and could never put anything into it. An intention died with
    the plan that held it, and the next plan re-derived a goal from whatever
    was on screen - which in a bar is the barman, every time.

    The plan's own operations are durable. Its objective is not: it used to be
    written as an automatic "Set out to…" episode, which filed a claim about
    unfinished work under the kind reserved for things that happened. Purpose
    is working history now, and it is recorded once the plan has ended.
    """
    from datetime import UTC, datetime

    from kenshi_agent.continuity import ContinuityAuthority, ContinuityLedger
    from kenshi_agent.models import (
        AuthoredPlannerContext,
        CurrentObservationEvidence,
        KeepMemoryOperation,
        MemoryKind,
        PlanDisposition,
        PlannerContextManifest,
    )
    from kenshi_agent.runtime import AgentRuntime

    store = MemoryStore(
        tmp_path / "memory.sqlite3",
        CampaignScope(campaign_id="test", origin=CampaignScopeOrigin.CONFIGURED),
    )
    ledger = ContinuityLedger(run_id="continuity", action_outcome_limit=4)
    runner = object.__new__(AgentRuntime)
    runner.memory = store
    runner.run_id = "continuity"
    runner.logger = SimpleNamespace(write=lambda *a, **k: None)
    runner._ledger = ledger
    runner._continuity_receipts = []
    runner._continuity = ContinuityAuthority(
        run_id="continuity",
        store=store,
        ledger=ledger,
        logger=runner.logger,
        advisor_brief_ids=set,
    )

    basis = Observation(
        run_id="continuity",
        step_index=0,
        mode="mock",
        world_revision=WorldStateRevision(telemetry_sequence=1),
        telemetry=TelemetrySnapshot(sequence=1),
    )
    plan = two_step_plan(basis).model_copy(
        update={
            "objective": "Leave the bar and look for paying work in town.",
            "continuity_operations": [
                KeepMemoryOperation(
                    kind=MemoryKind.FACT,
                    content="The barman offers no work.",
                    salience=0.8,
                    references=[CurrentObservationEvidence()],
                )
            ],
        }
    )
    runner._apply_plan_continuity(
        plan,
        basis,
        authored_context=AuthoredPlannerContext(
            manifest=PlannerContextManifest(
                context_id="pc-1",
                run_id="continuity",
                authored_revision=basis.world_revision,
                current_observation_delivered=True,
                telemetry_was_fresh=True,
                input_kind="full_observation",
            ),
            observation=basis,
        ),
    )

    # Recalled at the live profile's floor, so it actually reaches a planner.
    recalled = [record.content for record in store.recall(limit=16, minimum_salience=0.2)]
    assert recalled == ["The barman offers no work."]
    assert not any(item.startswith("Set out to") for item in recalled)

    started = datetime.now(UTC)
    runner._record_plan_outcome(
        plan,
        disposition=PlanDisposition.FAILED,
        reason="The exit was never reached.",
        completed_step_ids=["first"],
        actions_completed=1,
        observation=basis,
        started_at=started,
    )
    outcome = ledger.recent_plan_outcomes[-1]

    assert outcome.objective == "Leave the bar and look for paying work in town."
    assert outcome.reason == "The exit was never reached."
    assert outcome.disposition is PlanDisposition.FAILED
    # Working history, not durable belief: nothing new reached the store.
    assert [record.content for record in store.recall(limit=16)] == [
        "The barman offers no work."
    ]


def test_a_handback_sets_a_stopped_world_running_again() -> None:
    """The takeover pauses for the human; handing back should undo that.

    Otherwise the agent resumes into a world it never stopped, and every walk
    it orders sits there going nowhere: one run spent 1412 of its 1443
    observations paused and moved eighty units in total.
    """
    from kenshi_agent.models import GameState, PauseAction
    from kenshi_agent.runtime import AgentRuntime

    dispatched: list[object] = []

    class FakeEnvironment:
        async def dispatch(self, action, *, command):  # type: ignore[no-untyped-def]
            dispatched.append(action)
            resumed = Observation(
                run_id="handback",
                step_index=1,
                mode="mock",
                world_revision=WorldStateRevision(telemetry_sequence=2),
                telemetry=TelemetrySnapshot(
                    sequence=2, game=GameState(loaded=True, paused=False)
                ),
            )
            return SimpleNamespace(observation=resumed)

    class FakeStore:
        def begin_command(self, **kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(command_id="cmd-" + "0" * 32)

        def complete_command(self, command_id, revision):  # type: ignore[no-untyped-def]
            self.completed = command_id

    runtime = object.__new__(AgentRuntime)
    runtime.environment = FakeEnvironment()
    runtime.logger = SimpleNamespace(write=lambda *a, **k: None)
    store = FakeStore()

    stopped = Observation(
        run_id="handback",
        step_index=1,
        mode="mock",
        world_revision=WorldStateRevision(telemetry_sequence=1),
        telemetry=TelemetrySnapshot(sequence=1, game=GameState(loaded=True, paused=True)),
    )
    resumed = asyncio.run(runtime._restore_running_world(store, stopped))

    assert dispatched == [PauseAction(paused=False)], "the world must be set running"
    assert resumed.telemetry is not None and resumed.telemetry.game.paused is False
    assert store.completed.startswith("cmd-"), "the command must not be left open"


def test_a_handback_does_not_disturb_a_world_already_running() -> None:
    """Nothing to restore, so nothing should be sent."""
    from kenshi_agent.models import GameState
    from kenshi_agent.runtime import AgentRuntime

    class Unused:
        async def dispatch(self, action, *, command):  # type: ignore[no-untyped-def]
            raise AssertionError("a running world needs no resume")

    runtime = object.__new__(AgentRuntime)
    runtime.environment = Unused()
    runtime.logger = SimpleNamespace(write=lambda *a, **k: None)

    already = Observation(
        run_id="handback",
        step_index=1,
        mode="mock",
        world_revision=WorldStateRevision(telemetry_sequence=1),
        telemetry=TelemetrySnapshot(sequence=1, game=GameState(loaded=True, paused=False)),
    )
    assert asyncio.run(runtime._restore_running_world(None, already)) is already


def _keep_a_route_lesson() -> object:
    """One grounded keep, cited against the observation the patch was written on."""

    from kenshi_agent.models import (
        CurrentObservationEvidence,
        KeepMemoryOperation,
        MemoryKind,
    )

    return KeepMemoryOperation(
        kind=MemoryKind.FACT,
        content="The speed change had to be revised mid-option.",
        salience=0.8,
        references=[CurrentObservationEvidence()],
    )


class _PatchMemoryEnvironment(RevisionEnvironment):
    def __init__(self, *, clock: FakeClock) -> None:
        super().__init__(clock=clock)
        self.movement_started = asyncio.Event()
        self.release_movement = asyncio.Event()

    async def observe_without_capture(self) -> Observation:
        self.sequence += 1
        return self.observation()

    async def step(self, action: Action) -> Transition:
        if not isinstance(action, SkillAction):
            return await super().step(action)
        self.actions.append(action)
        self.movement_started.set()
        await self.release_movement.wait()
        self.step_index += 1
        self.sequence += 1
        return Transition(
            receipt=ActionReceipt(
                action=action,
                control_mode=ControlMode.INTERFACE_ONLY,
                accepted=True,
                executed=True,
                dry_run=False,
                primitive_actions=2,
                message="fake movement completed and remained paused",
            ),
            observation=self.observation(),
        )


def _future_speed_patch(current: Observation, step_id: str) -> PlanPatch:
    assert current.active_plan is not None
    return PlanPatch(
        schema_version="1.0",
        plan_id=current.active_plan.plan_id,
        based_on_plan_version=current.active_plan.plan_version,
        based_on_revision=current.world_revision,
        replace_future_steps=[
            PlanStep(
                step_id=step_id,
                action=SetSpeedAction(speed=3),
                preconditions=[condition("telemetry.game.paused", True, "game.pause")],
                success_conditions=[
                    condition("telemetry.game.speed_multiplier", 3.0, "game.speed")
                ],
                failure_conditions=[],
                timeout_seconds=1.0,
                retry_budget=0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        rationale="The future speed choice can be updated without restarting movement.",
        continuity_operations=[_keep_a_route_lesson()],  # type: ignore[list-item]
        fieldbook_operations=[
            CreateFieldbookProjectOperation(
                kind=FieldbookProjectKind.ROUTE_ATLAS,
                title="Revised movement route",
                summary="The movement option required a mid-route revision.",
            )
        ],
    )


def test_an_applied_patch_commits_its_continuity_exactly_once(tmp_path: Path) -> None:
    """A patch's continuity was in the schema and was committed nowhere."""

    class PatchingPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0
            self.advisory_returned = asyncio.Event()

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return patchable_movement_plan(current)
            self.advisory_returned.set()
            return _future_speed_patch(current, "patched-speed")

    async def scenario() -> None:
        clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = _PatchMemoryEnvironment(clock=clock)
        planner = PatchingPlanner()
        store = MemoryStore(
            tmp_path / "memory.sqlite3",
            CampaignScope(campaign_id="patched", origin=CampaignScopeOrigin.CONFIGURED),
        )
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            memory=store,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            await asyncio.wait_for(planner.advisory_returned.wait(), timeout=1.0)
            for _ in range(5):
                await asyncio.sleep(0)
            pump_clock.advance(0.1)
            await asyncio.sleep(0)
            environment.release_movement.set()
            await asyncio.wait_for(run, timeout=1.0)
            kept = [record.content for record in store.recall(limit=8)]
            projects = store.fieldbook.list_projects()
        finally:
            store.close()
            logger.close()

        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "plan_patched" for event in events) == 1
        assert kept == ["The speed change had to be revised mid-option."]
        assert [project.title for project in projects] == [
            "Revised movement route"
        ]
        receipts = [
            event["payload"]
            for event in events
            if event["event_type"] == "continuity_receipt"
        ]
        contexts = [
            event["payload"]
            for event in events
            if event["event_type"] == "planner_context_prepared"
        ]
        assert [receipt["origin"] for receipt in receipts] == ["patch"]
        assert receipts[0]["status"] == "accepted"
        assert receipts[0]["authored_context_id"] == contexts[1]["context_id"]
        assert receipts[0]["authored_revision"] == contexts[1]["authored_revision"]
        assert receipts[0]["commit_revision"] != receipts[0]["authored_revision"]
        fieldbook_receipts = [
            event["payload"]
            for event in events
            if event["event_type"] == "fieldbook_receipt"
        ]
        assert [receipt["origin"] for receipt in fieldbook_receipts] == ["patch"]
        assert fieldbook_receipts[0]["status"] == "accepted"
        assert fieldbook_receipts[0]["authored_context_id"] == (
            contexts[1]["context_id"]
        )

    asyncio.run(scenario())


def test_a_rejected_patch_with_mismatched_authored_basis_writes_nothing_durable(
    tmp_path: Path,
) -> None:
    """The patch that never took effect must leave no trace in memory."""

    class MismatchedBasisPatchPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0
            self.advisory_returned = asyncio.Event()
            self.advisory_started = asyncio.Event()
            self.release_advisory = asyncio.Event()

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return patchable_movement_plan(current)
            self.advisory_started.set()
            await self.release_advisory.wait()
            self.advisory_returned.set()
            patch = _future_speed_patch(current, "mismatched-basis-speed")
            sequence = patch.based_on_revision.telemetry_sequence or 0
            return patch.model_copy(
                update={
                    "based_on_revision": patch.based_on_revision.model_copy(
                        update={"telemetry_sequence": sequence + 100}
                    )
                },
                deep=True,
            )

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = _PatchMemoryEnvironment(clock=plan_clock)
        planner = MismatchedBasisPatchPlanner()
        store = MemoryStore(
            tmp_path / "memory.sqlite3",
            CampaignScope(
                campaign_id="mismatched-basis",
                origin=CampaignScopeOrigin.CONFIGURED,
            ),
        )
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            plan_clock,
            observation_pump_enabled=True,
            observation_clock=pump_clock,
            memory=store,
        )
        try:
            run = asyncio.create_task(runtime.run(max_steps=2))
            await asyncio.wait_for(environment.movement_started.wait(), timeout=1.0)
            await asyncio.wait_for(planner.advisory_started.wait(), timeout=1.0)
            pump_clock.advance(0.1)
            await asyncio.sleep(0)
            planner.release_advisory.set()
            await asyncio.wait_for(planner.advisory_returned.wait(), timeout=1.0)
            await asyncio.sleep(0)
            environment.release_movement.set()
            await asyncio.wait_for(run, timeout=1.0)
            kept = store.recall(limit=8)
            projects = store.fieldbook.list_projects()
        finally:
            store.close()
            logger.close()

        events = read_events(tmp_path / "events.jsonl")
        rejected = [
            event for event in events if event["event_type"] == "plan_patch_rejected"
        ]
        assert len(rejected) == 1
        assert "immutable planner snapshot" in str(rejected[0]["payload"])
        assert sum(event["event_type"] == "plan_patched" for event in events) == 0
        assert kept == []
        assert projects == []
        assert not any(
            event["event_type"] == "continuity_receipt" for event in events
        )
        assert not any(
            event["event_type"] == "fieldbook_receipt" for event in events
        )

    asyncio.run(scenario())


def test_a_continuous_fieldbook_read_reaches_the_replacing_planner_without_game_input(
    tmp_path: Path,
) -> None:
    seen_reads: list[object] = []

    class ReadingPlanner(Planner):
        def __init__(self, project_id: str) -> None:
            self.project_id = project_id
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            seen_reads.append(current.fieldbook_read)
            if self.calls == 1:
                return PlanEnvelope(
                    schema_version="1.0",
                    plan_id="read-fieldbook",
                    plan_version=1,
                    objective="Reopen the bounded route entry.",
                    control_mode=current.control_mode,
                    based_on_revision=current.world_revision,
                    assumptions=[fresh()],
                    steps=[
                        PlanStep(
                            step_id="read-route",
                            action=ReadFieldbookAction(
                                project_id=self.project_id,
                                max_entries=2,
                            ),
                            preconditions=[fresh()],
                            success_conditions=[],
                            failure_conditions=[],
                            timeout_seconds=1.0,
                            retry_budget=0,
                            idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                        )
                    ],
                    entry_step_id="read-route",
                    max_actions=1,
                    max_wall_seconds=3.0,
                    max_game_seconds=3.0,
                    risk_budget=RiskBudget(
                        max_pointer_actions=0,
                        max_purchase_actions=0,
                        max_native_assisted_actions=0,
                    ),
                )
            return PlannerDecision(
                intent="Stop after receiving the read.",
                rationale="The route entry reached this exact planner call.",
                action=StopAction(reason="done"),
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        store = MemoryStore(
            tmp_path / "memory.sqlite3",
            CampaignScope(
                campaign_id="fieldbook-read",
                origin=CampaignScopeOrigin.CONFIGURED,
            ),
        )
        project = store.fieldbook.create_project(
            run_id="operator",
            kind=FieldbookProjectKind.ROUTE_ATLAS,
            title="Squin route",
            summary="Known route details.",
            provenance=None,
        )
        entry = store.fieldbook.append_entry(
            run_id="operator",
            project_id=project.project_id,
            kind=FieldbookEntryKind.QUESTION,
            content="Does the western gate close at night?",
            provenance=None,
        )
        planner = ReadingPlanner(project.project_id)
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            planner,
            clock,
            memory=store,
        )
        runtime.guard.config.allow_action_kinds.append("read_fieldbook")
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            store.close()
            logger.close()

        assert summary.steps_completed == 2
        assert seen_reads[0] is None
        received = seen_reads[1]
        assert received is not None
        assert received.entry_ids == [entry.entry_id]  # type: ignore[union-attr]
        assert [type(action) for action in environment.actions] == [StopAction]
        events = read_events(tmp_path / "events.jsonl")
        read_event = next(
            event
            for event in events
            if event["event_type"] == "fieldbook_read"
        )
        assert read_event["payload"]["controller_primitives"] == 0
        assert read_event["payload"]["world_command_created"] is False

    asyncio.run(scenario())


def test_a_finished_plan_hands_its_purpose_to_the_next_planner(tmp_path: Path) -> None:
    """The next plan must not have to reconstruct purpose from "Execute step X"."""

    seen: list[list[dict[str, object]]] = []

    class TwoPlanPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            seen.append(
                [
                    outcome.model_dump(mode="json")
                    for outcome in current.recent_plan_outcomes
                ]
            )
            if self.calls == 1:
                return two_step_plan(current).model_copy(
                    update={"objective": "Leave the bar and look for paying work."}
                )
            return PlannerDecision(
                intent="Stop after one plan.",
                rationale="One plan is enough for this scenario.",
                action=StopAction(reason="done"),
                confidence=1.0,
            )

    async def scenario() -> None:
        clock = FakeClock()
        environment = RevisionEnvironment(clock=clock)
        planner = TwoPlanPlanner()
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
        try:
            await runtime.run(max_steps=4)
        finally:
            logger.close()

        assert seen[0] == []
        assert seen[1], "the second planner call saw no plan outcome at all"
        first = seen[1][0]
        assert first["objective"] == "Leave the bar and look for paying work."
        assert first["plan_outcome_id"] == "po-1"
        assert first["disposition"] in {"completed", "failed", "terminated"}
        assert first["reason"]

        events = read_events(tmp_path / "events.jsonl")
        outcomes = [
            event["payload"] for event in events if event["event_type"] == "plan_outcome"
        ]
        assert len(outcomes) == 1
        assert evaluate_log(tmp_path / "events.jsonl").plan_outcomes == 1

    asyncio.run(scenario())
