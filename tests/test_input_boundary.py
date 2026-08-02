"""Final post-input-lease revalidation (P3).

Validation before entering a polite input lease is necessary but not
sufficient: the lease can wait an unbounded interval, so the UI, target,
capability, or control-mode evidence that authorized the action may be obsolete
by the time the first primitive would be emitted. These tests block inside a
fake lease, publish conflicting state, and prove zero input escapes.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_live_env import (
    PulseController,
    PulseTelemetry,
    movement_action,
    movement_registry,
)

from kenshi_agent.config import (
    CaptureConfig,
    ControlsConfig,
    RuntimeConfig,
    SafetyConfig,
)
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.input_boundary import ExecutionToken
from kenshi_agent.models import (
    ActivateVisibleControlAction,
    CharacterState,
    CommandDispatchContext,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    ConditionResult,
    ControlMode,
    InputBoundaryDecision,
    InputBoundaryReport,
    Observation,
    PlannerDecision,
    SkillAction,
    WorldStateRevision,
)
from kenshi_agent.planners.base import Planner
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger


def environment(
    tmp_path: Path,
    telemetry: PulseTelemetry,
    controller: PulseController,
    *,
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
) -> LiveEnvironment:
    return LiveEnvironment(
        run_id="boundary-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        macros=movement_registry(),
        runtime_config=RuntimeConfig(settle_seconds=0.0, objective="Explore nearby."),
        controls_config=ControlsConfig(
            post_input_delay_seconds=0.0,
            calibrated_client_width=1920,
            calibrated_client_height=1080,
        ),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        available_skills=["move_visible_terrain"],
        control_mode=control_mode,
    )


def revision(sequence: int, *, capability_epoch: int = 1) -> WorldStateRevision:
    return WorldStateRevision(
        telemetry_sequence=sequence,
        capability_epoch=capability_epoch,
        observed_at_monotonic=float(sequence),
    )


def observation(
    *,
    sequence: int = 10,
    capability_epoch: int = 1,
    paused: bool = True,
    selected_id: str = "char-1",
    capabilities: tuple[str, ...] = ("game.pause", "squad.basic"),
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    events: tuple[str, ...] = (),
    telemetry_stale: bool = False,
    telemetry_age_seconds: float | None = 0.0,
) -> Observation:
    from kenshi_agent.models import GameState, TelemetrySnapshot, UIState

    return Observation(
        run_id="boundary-test",
        step_index=0,
        mode="live",
        control_mode=control_mode,
        world_revision=revision(sequence, capability_epoch=capability_epoch),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            capabilities=list(capabilities),
            game=GameState(loaded=True, paused=paused),
            ui=UIState(selected_character_id=selected_id),
            squad=[CharacterState(id=selected_id, name="Hep", selected=True, alive=True)],
        ),
        telemetry_stale=telemetry_stale,
        telemetry_age_seconds=telemetry_age_seconds,
        events=list(events),
        objective="Explore nearby.",
    )


def paused_condition() -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=ConditionPath.TELEMETRY_GAME_PAUSED,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=30.0,
        required_capabilities=["game.pause"],
    )


def selection_condition(selected_id: str = "char-1") -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=ConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_ID,
        operator=ConditionOperator.EQUALS,
        expected=selected_id,
        max_age_seconds=30.0,
        required_capabilities=["squad.basic"],
    )


class BlockingLeaseController(PulseController):
    """A controller whose input lease waits, exactly like a polite live wait."""

    def __init__(self, telemetry: PulseTelemetry) -> None:
        super().__init__(telemetry)
        self.lease_entered = asyncio.Event()
        self.release_lease = asyncio.Event()
        self.lease_wait_seconds = 7.5

    @asynccontextmanager
    async def input_lease(self, *, alt_tab_on_restore: bool = False):
        del alt_tab_on_restore
        self.lease_entered.set()
        await self.release_lease.wait()
        yield

    def input_lease_wait_seconds(self) -> float:
        return self.lease_wait_seconds


def token_for(
    latest: list[Observation | None],
    *,
    validated: WorldStateRevision | None = None,
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    assumptions: tuple[Condition, ...] | None = None,
    preconditions: tuple[Condition, ...] | None = None,
    failure_conditions: tuple[Condition, ...] | None = None,
    max_telemetry_age_seconds: float | None = 3.0,
) -> ExecutionToken:
    return ExecutionToken(
        plan_id="boundary-plan",
        plan_version=1,
        step_id="approach",
        command_id="cmd-" + "0" * 32,
        control_mode=control_mode,
        validated_revision=validated or revision(10),
        latest_observation=lambda: latest[0],
        max_telemetry_age_seconds=max_telemetry_age_seconds,
        assumptions=(
            assumptions
            if assumptions is not None
            else (paused_condition(),)
        ),
        preconditions=(
            preconditions
            if preconditions is not None
            else (selection_condition(),)
        ),
        failure_conditions=failure_conditions or (),
    )


def assert_report_identity(
    report: InputBoundaryReport,
    token: ExecutionToken,
    boundary_revision: WorldStateRevision | None,
) -> None:
    assert report.plan_id == token.plan_id
    assert report.plan_version == token.plan_version
    assert report.step_id == token.step_id
    assert report.validated_revision == token.validated_revision
    assert report.boundary_revision == boundary_revision


def test_execution_token_remains_a_frozen_slotted_data_contract() -> None:
    token = token_for([observation()])

    assert is_dataclass(token)
    assert not hasattr(token, "__dict__")
    with pytest.raises(FrozenInstanceError):
        token.plan_id = "changed"  # type: ignore[misc]


def test_every_observation_bound_decision_preserves_authority_evidence() -> None:
    current = observation(sequence=11)
    cases: list[tuple[ExecutionToken, InputBoundaryDecision]] = [
        (
            token_for([observation(sequence=11, telemetry_stale=True)]),
            InputBoundaryDecision.REJECTED,
        ),
        (
            token_for([current], max_telemetry_age_seconds=None),
            InputBoundaryDecision.REJECTED,
        ),
        (
            token_for([observation(sequence=11, telemetry_age_seconds=None)]),
            InputBoundaryDecision.REJECTED,
        ),
        (
            token_for([observation(sequence=11, telemetry_age_seconds=3.001)]),
            InputBoundaryDecision.REJECTED,
        ),
        (
            token_for([observation(sequence=4)], validated=revision(10)),
            InputBoundaryDecision.REJECTED,
        ),
        (
            token_for(
                [observation(sequence=11, control_mode=ControlMode.NATIVE_ASSISTED)]
            ),
            InputBoundaryDecision.REJECTED,
        ),
        (
            token_for([observation(sequence=11, events=("human_input_detected",))]),
            InputBoundaryDecision.REJECTED,
        ),
        (
            token_for(
                [
                    observation(
                        sequence=11,
                        events=("terminal_window_detected: Kenshi has crashed",),
                    )
                ]
            ),
            InputBoundaryDecision.REJECTED,
        ),
        (
            ExecutionToken(
                plan_id="boundary-plan",
                plan_version=1,
                step_id="approach",
                command_id="cmd-" + "0" * 32,
                control_mode=ControlMode.INTERFACE_ONLY,
                validated_revision=revision(10),
                latest_observation=lambda: current,
                max_telemetry_age_seconds=3.0,
                authority_validator=lambda _: "target disappeared",
            ),
            InputBoundaryDecision.REJECTED,
        ),
        (
            token_for([observation(sequence=11, paused=False)]),
            InputBoundaryDecision.REJECTED,
        ),
        (token_for([current]), InputBoundaryDecision.REVALIDATED),
    ]

    for token, decision in cases:
        boundary_observation = token.latest_observation()
        assert boundary_observation is not None
        report = token.revalidate()
        assert report.decision is decision
        assert report.lease_wait_seconds == 0.0
        assert_report_identity(report, token, boundary_observation.world_revision)


def test_condition_rejection_retains_every_evaluation_in_contract_order() -> None:
    assumptions = (paused_condition(),)
    preconditions = (selection_condition(),)
    current = observation(sequence=11, paused=False, selected_id="char-2")
    token = token_for(
        [current],
        assumptions=assumptions,
        preconditions=preconditions,
    )

    report = token.revalidate(lease_wait_seconds=2.25)

    assert report.decision is InputBoundaryDecision.REJECTED
    assert report.lease_wait_seconds == 2.25
    assert_report_identity(report, token, current.world_revision)
    assert [evaluation.condition for evaluation in report.evaluations] == [
        *assumptions,
        *preconditions,
    ]
    assert [evaluation.result for evaluation in report.evaluations] == [
        ConditionResult.FALSE,
        ConditionResult.FALSE,
    ]


def test_telemetry_at_the_exact_age_ceiling_remains_authorized() -> None:
    current = observation(sequence=11, telemetry_age_seconds=3.0)
    token = token_for([current], max_telemetry_age_seconds=3.0)

    report = token.revalidate()

    assert report.decision is InputBoundaryDecision.REVALIDATED
    assert_report_identity(report, token, current.world_revision)


async def dispatch_with_blocking_lease(
    tmp_path: Path,
    *,
    conflict: Observation | None,
    validated: WorldStateRevision | None = None,
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    assumptions: tuple[Condition, ...] | None = None,
    preconditions: tuple[Condition, ...] | None = None,
    failure_conditions: tuple[Condition, ...] | None = None,
    action: object | None = None,
) -> tuple[BlockingLeaseController, object]:
    """Start a dispatch, swap canonical state while the lease waits, release it."""

    telemetry = PulseTelemetry()
    controller = BlockingLeaseController(telemetry)
    live = environment(tmp_path, telemetry, controller, control_mode=control_mode)
    await live.reset()

    latest: list[Observation | None] = [observation(control_mode=control_mode)]
    token = token_for(
        latest,
        validated=validated,
        control_mode=control_mode,
        assumptions=assumptions,
        preconditions=preconditions,
        failure_conditions=failure_conditions,
    )

    task = asyncio.create_task(
        live.dispatch(
            action if action is not None else movement_action(),  # type: ignore[arg-type]
            command=CommandDispatchContext(
                command_id=token.command_id,
                based_on_revision=token.validated_revision,
            ),
            token=token,
        )
    )
    await controller.lease_entered.wait()
    # The authorizing evidence changes while the agent is politely waiting.
    latest[0] = conflict
    controller.release_lease.set()
    transition = await task
    return controller, transition


def test_state_change_during_lease_wait_emits_zero_primitives(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, selected_id="char-2"),
        )

        assert controller.actions == []
        receipt = transition.receipt  # type: ignore[attr-defined]
        assert receipt.accepted is False
        assert receipt.executed is False
        assert receipt.primitive_actions == 0
        assert receipt.error_type == "InputBoundaryRejected"
        boundary = receipt.input_boundary
        assert boundary is not None
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert boundary.lease_wait_seconds == 7.5
        assert boundary.validated_revision.telemetry_sequence == 10
        assert boundary.boundary_revision.telemetry_sequence == 11
        assert "precondition" in boundary.reason

    asyncio.run(scenario())


def test_failure_condition_becoming_true_during_lease_emits_zero_primitives(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, selected_id="char-2"),
            assumptions=(),
            preconditions=(),
            failure_conditions=(selection_condition("char-2"),),
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "failure condition became true" in boundary.reason

    asyncio.run(scenario())


def test_unchanged_state_executes_exactly_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11),
        )

        assert len(controller.actions) == 3
        receipt = transition.receipt  # type: ignore[attr-defined]
        assert receipt.executed is True
        boundary = receipt.input_boundary
        assert boundary is not None
        assert boundary.decision is InputBoundaryDecision.REVALIDATED
        assert boundary.evaluations

    asyncio.run(scenario())


def test_capability_withdrawal_during_lease_blocks_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, capability_epoch=2, capabilities=()),
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "unavailable" in boundary.reason

    asyncio.run(scenario())


def test_unpause_during_lease_blocks_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, paused=False),
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "assumption" in boundary.reason

    asyncio.run(scenario())


def test_human_input_at_boundary_blocks_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, events=("human_input_detected",)),
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "human_input_detected" in boundary.reason

    asyncio.run(scenario())


def test_emergency_stop_at_boundary_blocks_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, events=("emergency_stop_detected",)),
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "emergency_stop_detected" in boundary.reason

    asyncio.run(scenario())


def test_terminal_crash_at_boundary_blocks_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(
                sequence=11,
                events=("terminal_window_detected: Kenshi has crashed",),
            ),
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "Kenshi has crashed" in boundary.reason

    asyncio.run(scenario())


def test_control_mode_change_at_boundary_blocks_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, control_mode=ControlMode.NATIVE_ASSISTED),
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "Control mode changed" in boundary.reason

    asyncio.run(scenario())


def test_revision_regression_at_boundary_blocks_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=4),
            validated=revision(10),
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "regressed" in boundary.reason

    asyncio.run(scenario())


def test_stale_canonical_observation_at_boundary_blocks_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = BlockingLeaseController(telemetry)
        live = environment(tmp_path, telemetry, controller)
        await live.reset()
        latest: list[Observation | None] = [observation()]
        token = token_for(latest, assumptions=(), preconditions=())
        task = asyncio.create_task(
            live.dispatch(
                movement_action(),
                command=CommandDispatchContext(
                    command_id=token.command_id,
                    based_on_revision=token.validated_revision,
                ),
                token=token,
            )
        )
        await controller.lease_entered.wait()
        latest[0] = observation(sequence=11, telemetry_stale=True)
        controller.release_lease.set()
        transition = await task

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "stale" in boundary.reason

    asyncio.run(scenario())


def test_unknown_or_overage_canonical_observation_at_boundary_blocks_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        for age_seconds in (None, 3.001):
            controller, transition = await dispatch_with_blocking_lease(
                tmp_path,
                conflict=observation(
                    sequence=11,
                    telemetry_age_seconds=age_seconds,
                ),
                assumptions=(),
                preconditions=(),
            )

            assert controller.actions == []
            boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
            assert boundary.decision is InputBoundaryDecision.REJECTED
            assert "age" in boundary.reason

    asyncio.run(scenario())


def test_action_authority_change_at_boundary_blocks_input() -> None:
    latest: list[Observation | None] = [observation(sequence=11)]
    token = ExecutionToken(
        plan_id="boundary-plan",
        plan_version=1,
        step_id="operate",
        command_id="cmd-" + "0" * 32,
        control_mode=ControlMode.INTERFACE_ONLY,
        validated_revision=revision(10),
        latest_observation=lambda: latest[0],
        max_telemetry_age_seconds=3.0,
        authority_validator=lambda current: (
            "the exact target disappeared" if current.telemetry is not None else None
        ),
    )

    boundary = token.revalidate()

    assert boundary.decision is InputBoundaryDecision.REJECTED
    assert "exact target disappeared" in boundary.reason


def test_single_step_live_dispatch_carries_boundary_authority(
    tmp_path: Path,
) -> None:
    class OverageOnceTelemetry(PulseTelemetry):
        overage_once = False

        def read(self):  # type: ignore[no-untyped-def]
            result = super().read()
            if not self.overage_once:
                return result
            self.overage_once = False
            return replace(
                result,
                age_seconds=self.max_age_seconds + 0.001,
                stale=False,
            )

    class OneMovePlanner(Planner):
        async def decide(self, current: Observation) -> PlannerDecision:
            del current
            return PlannerDecision(
                intent="Move once.",
                rationale="Exercise single-step boundary authority.",
                action=movement_action(),
                confidence=1.0,
            )

    async def scenario() -> None:
        telemetry = OverageOnceTelemetry()
        controller = BlockingLeaseController(telemetry)
        live = environment(tmp_path, telemetry, controller)
        macros = movement_registry()
        logger = SessionLogger(tmp_path / "single-step-boundary.jsonl", "boundary-test")
        guard = ActionGuard(
            SafetyConfig(
                allow_action_kinds=["skill"],
                allow_skills=["move_visible_terrain"],
                max_actions_per_minute=3,
            ),
            macros,
        )
        runtime = AgentRuntime(
            run_id="boundary-test",
            environment=live,
            planner=OneMovePlanner(),
            guard=guard,
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
        )
        try:
            task = asyncio.create_task(runtime.run(max_steps=1))
            await asyncio.wait_for(controller.lease_entered.wait(), timeout=1.0)
            telemetry.overage_once = True
            controller.release_lease.set()
            await task
        finally:
            logger.close()

        assert controller.actions == []
        events = [
            json.loads(line)
            for line in (tmp_path / "single-step-boundary.jsonl").read_text().splitlines()
        ]
        boundary = next(
            event for event in events if event["event_type"] == "input_boundary_rejected"
        )
        assert boundary["payload"]["decision"] == "rejected"
        receipt = next(event for event in events if event["event_type"] == "action_receipt")
        assert receipt["payload"]["accepted"] is False
        assert receipt["payload"]["primitive_actions"] == 0
        # The boundary proved that none of the three reserved primitives were
        # emitted, so all three must still be available to a later action.
        assert guard.validate(movement_action(), observation()) == movement_action()

    asyncio.run(scenario())


def test_missing_canonical_observation_blocks_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=None,
        )

        assert controller.actions == []
        boundary = transition.receipt.input_boundary  # type: ignore[attr-defined]
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "cannot be proven" in boundary.reason

    asyncio.run(scenario())


def test_dispatch_without_a_token_keeps_legacy_behaviour(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        live = environment(tmp_path, telemetry, controller)
        await live.reset()

        transition = await live.dispatch(
            movement_action(),
            command=CommandDispatchContext(
                command_id="cmd-" + "0" * 32,
                based_on_revision=revision(10),
            ),
        )

        assert len(controller.actions) == 3
        assert transition.receipt.executed is True
        assert transition.receipt.input_boundary is None

    asyncio.run(scenario())


def test_token_records_every_boundary_decision() -> None:
    latest: list[Observation | None] = [observation()]
    token = token_for(latest)

    first = token.revalidate(lease_wait_seconds=0.5)
    latest[0] = observation(sequence=11, selected_id="char-9")
    second = token.revalidate(lease_wait_seconds=1.5)

    assert first.decision is InputBoundaryDecision.REVALIDATED
    assert second.decision is InputBoundaryDecision.REJECTED
    assert [report.decision for report in token.reports] == [
        InputBoundaryDecision.REVALIDATED,
        InputBoundaryDecision.REJECTED,
    ]
    assert [report.lease_wait_seconds for report in token.reports] == [0.5, 1.5]
    assert all(report.step_id == "approach" for report in token.reports)


def test_boundary_report_survives_receipt_serialization(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, selected_id="char-2"),
        )
        payload = transition.receipt.model_dump(mode="json")  # type: ignore[attr-defined]
        assert payload["input_boundary"]["decision"] == "rejected"
        assert payload["input_boundary"]["lease_wait_seconds"] == 7.5

    asyncio.run(scenario())


def test_tokenless_calibration_mismatch_fails_closed_by_raising(tmp_path: Path) -> None:
    """Without a token to carry the rejection, the client-size brake still raises."""

    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry, client_width=1280, client_height=720)
        live = environment(tmp_path, telemetry, controller)
        await live.reset()

        raised = False
        try:
            await live.step(movement_action())
        except RuntimeError as exc:
            raised = "1280x720" in str(exc)

        assert raised
        assert controller.actions == []

    asyncio.run(scenario())


def test_calibration_mismatch_with_a_token_rejects_gracefully(tmp_path: Path) -> None:
    """With a plan token, a client-size mismatch is a clean boundary rejection.

    A raise would be treated by the executor as an ambiguous environment error
    and conservatively spend the reservation. A graceful rejection instead
    releases it, which is correct because we know zero input was emitted.
    """

    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry, client_width=1280, client_height=720)
        live = environment(tmp_path, telemetry, controller)
        await live.reset()

        latest: list[Observation | None] = [observation()]
        token = token_for(latest)
        transition = await live.dispatch(
            movement_action(),
            command=CommandDispatchContext(
                command_id=token.command_id,
                based_on_revision=token.validated_revision,
            ),
            token=token,
        )

        assert controller.actions == []
        receipt = transition.receipt  # type: ignore[attr-defined]
        assert receipt.accepted is False
        assert receipt.executed is False
        assert receipt.error_type == "InputBoundaryRejected"
        boundary = receipt.input_boundary
        assert boundary is not None
        assert boundary.decision is InputBoundaryDecision.REJECTED
        assert "alibration" in boundary.reason
        assert receipt.calibration is not None
        assert receipt.calibration.mismatched_fields  # names the drifted field(s)

    asyncio.run(scenario())


def test_movement_action_is_the_pointer_bearing_case(tmp_path: Path) -> None:
    """Guard the assumption that the exercised skill really emits pointer input."""

    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        live = environment(tmp_path, telemetry, controller)
        await live.reset()
        await live.step(SkillAction.model_validate(movement_action().model_dump()))
        assert controller.actions

    asyncio.run(scenario())


def test_semantic_control_action_is_rejected_at_the_boundary(tmp_path: Path) -> None:
    """The generic fence covers the new actions, not only calibrated macros.

    A control activation resolves its bounds from telemetry, so the interesting
    failure is not a stale coordinate but a plan precondition that stopped being
    true during the polite wait. No click may be emitted in that case.
    """

    async def scenario() -> None:
        controller, transition = await dispatch_with_blocking_lease(
            tmp_path,
            conflict=observation(sequence=11, selected_id="char-2"),
            action=ActivateVisibleControlAction(
                exact_label="Show me your goods.", role="button"
            ),
        )

        assert controller.actions == []
        receipt = transition.receipt  # type: ignore[attr-defined]
        assert receipt.accepted is False
        assert receipt.executed is False
        assert receipt.primitive_actions == 0
        assert receipt.error_type == "InputBoundaryRejected"
        boundary = receipt.input_boundary
        assert boundary is not None
        assert boundary.decision is InputBoundaryDecision.REJECTED
        # Semantic-current bounds mean calibration is never the blocker here.
        assert receipt.calibration is not None
        assert receipt.calibration.action_class.value == "semantic_current"

    asyncio.run(scenario())
