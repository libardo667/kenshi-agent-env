from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kenshi_agent.config import MacroConfig, PlanningConfig, SafetyConfig
from kenshi_agent.env import AgentEnvironment
from kenshi_agent.evals import evaluate_log, replay_plan_lifecycle
from kenshi_agent.input_boundary import ExecutionToken
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
    InputBoundaryDecision,
    InputBoundaryReport,
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
    SkillArgument,
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
        self.dispatch_tokens: list[ExecutionToken | None] = []

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


def runtime_for(
    tmp_path: Path,
    environment: RevisionEnvironment,
    planner: Planner,
    clock: FakeClock,
    *,
    observation_pump_enabled: bool = False,
    observation_clock: PlanningClock | None = None,
    automatic_takeover_enabled: bool = False,
    concurrent_option_planning_enabled: bool = True,
    stateful_approach_options_enabled: bool = False,
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
        allow_action_kinds=["pause", "set_speed", "skill", "stop"],
        max_actions_per_minute=500,
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
            concurrent_option_planning_enabled=concurrent_option_planning_enabled,
            stateful_approach_options_enabled=stateful_approach_options_enabled,
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
            ),
            observation=self.observation(),
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
        runtime, logger = runtime_for(tmp_path, environment, planner, clock)
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

        assert summary.terminated is True
        assert summary.stop_reason == "Planner failure."
        events = read_events(tmp_path / "events.jsonl")
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
        assert "policy is disabled" in summary.stop_reason

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
