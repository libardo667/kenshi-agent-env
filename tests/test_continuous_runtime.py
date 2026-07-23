from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from kenshi_agent.config import MacroConfig, PlanningConfig, SafetyConfig
from kenshi_agent.env import AgentEnvironment
from kenshi_agent.evals import evaluate_log, replay_plan_lifecycle
from kenshi_agent.models import (
    Action,
    ActionReceipt,
    CommandDispatchContext,
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
    PlanPatch,
    PlanStep,
    RiskBudget,
    SetSpeedAction,
    SkillAction,
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
        self.dispatch_contexts: list[CommandDispatchContext] = []

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

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
    ) -> Transition:
        self.dispatch_contexts.append(command)
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


def runtime_for(
    tmp_path: Path,
    environment: RevisionEnvironment,
    planner: Planner,
    clock: FakeClock,
    *,
    observation_pump_enabled: bool = False,
    observation_clock: PlanningClock | None = None,
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
        }
    )
    safety = SafetyConfig(
        allow_action_kinds=["pause", "set_speed", "skill", "stop"],
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
            observation_pump_enabled=observation_pump_enabled,
        ),
        planning_clock=clock,
        observation_clock=observation_clock,
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
        assert metrics.command_receipts == 2
        assert metrics.command_receipts_with_post_revision == 2
        assert metrics.receipts_with_post_command_revision_percentage == 100.0

        events = read_events(tmp_path / "events.jsonl")
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


def test_independent_supervisor_preempts_a_blocked_planner_and_confirms_pause(
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

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = UnsafeObserveEnvironment(clock=plan_clock)
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
        assert environment.paused is True
        assert [
            action.paused for action in environment.actions if isinstance(action, PauseAction)
        ] == [True]
        events = read_events(tmp_path / "events.jsonl")
        assert sum(event["event_type"] == "strategic_planner_cancelled" for event in events) == 1
        assert sum(event["event_type"] == "safety_cleanup_completed" for event in events) == 1
        terminal = [
            event for event in events if event["event_type"] == "safety_supervisor_terminal"
        ]
        assert len(terminal) == 1
        assert terminal[0]["payload"]["status"] == "safe_paused"
        receipts = [event["payload"] for event in events if event["event_type"] == "action_receipt"]
        assert len(receipts) == 1
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
        assert metrics.safety_supervisor_terminals == 1
        assert metrics.safety_supervisor_safe_paused == 1
        assert metrics.safety_cleanup_success_percentage == 100.0

    asyncio.run(scenario())


def test_supervisor_cancels_blocked_movement_then_performs_one_safe_cleanup(
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
                            "nearby_entities": [
                                NearbyEntity(
                                    id="threat",
                                    name="Hungry Bandit",
                                    disposition=Disposition.HOSTILE,
                                    distance=8.0,
                                    visible=True,
                                )
                            ]
                        }
                    )
                }
            )

        async def observe_without_capture(self) -> Observation:
            self.sequence += 1
            self.unsafe = True
            return self.observation().model_copy(update={"events": ["human_input_detected"]})

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
        async def decide(self, current: Observation) -> PlannerOutput:
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
        runtime, logger = runtime_for(
            tmp_path,
            environment,
            MovementPlanner(),
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
        assert sum(event["event_type"] == "safety_supervisor_terminal" for event in events) == 1
        preemption = next(
            event for event in events if event["event_type"] == "safety_supervisor_preempted"
        )
        assert preemption["payload"]["cause"] == "human_input"
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


def test_stale_concurrent_patch_is_rejected_and_original_future_step_runs(
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

    class StalePatchPlanner(Planner):
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
                        step_id="stale-speed",
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
                rationale="This advisory is intentionally stale.",
            )

    async def scenario() -> None:
        plan_clock = FakeClock()
        pump_clock = ManualPumpClock()
        environment = AdvancingMovementEnvironment(clock=plan_clock)
        planner = StalePatchPlanner()
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
        assert environment.actions[1].speed == 2
        events = read_events(tmp_path / "events.jsonl")
        rejected = [event for event in events if event["event_type"] == "plan_patch_rejected"]
        assert len(rejected) == 1
        assert "stale" in str(rejected[0]["payload"])
        assert sum(event["event_type"] == "plan_patched" for event in events) == 0
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.plan_patches_staged == 0
        assert metrics.plan_patches_applied == 0
        assert metrics.plan_patches_rejected == 1

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

        assert summary.terminated
        assert environment.actions == []
        events = read_events(tmp_path / "events.jsonl")
        rejected = [event for event in events if event["event_type"] == "plan_rejected"]
        assert len(rejected) == 1
        assert "stale" in str(rejected[0]["payload"])

    asyncio.run(scenario())
