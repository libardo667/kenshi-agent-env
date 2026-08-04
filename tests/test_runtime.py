import asyncio
import json
from pathlib import Path

import pytest
from operation_test_support import operation_port

from kenshi_agent.config import SafetyConfig
from kenshi_agent.core.evidence import (
    CameraFrameScore,
    CameraRecoveryEvidence,
    CameraRecoveryStatus,
    SemanticActionReceipt,
)
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    Action,
    MoveInDirectionAction,
    PauseAction,
    RecoverCameraViewAction,
    StopAction,
)
from kenshi_agent.core.planning import PlannerDecision
from kenshi_agent.core.telemetry import (
    GameState,
    TelemetrySnapshot,
)
from kenshi_agent.core.transport import (
    ActionReceipt,
    Transition,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.env.base import AgentEnvironment
from kenshi_agent.final_safe_state import (
    FinalSafeStateOutcome,
    FinalSafeStateStatus,
)
from kenshi_agent.outcome_recorder import OutcomeRecorder, TelemetryChange
from kenshi_agent.planners.base import Planner
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import OperationPolicy
from kenshi_agent.session_log import SessionLogger


@pytest.mark.parametrize(
    "exit_kind",
    [
        "budget",
        "stop",
        "environment_error",
        "reset_exception",
        "cancellation",
    ],
)
def test_every_runtime_exit_has_one_durable_final_state_owner(
    tmp_path: Path,
    exit_kind: str,
) -> None:
    class ExitEnvironment(AgentEnvironment):
        def __init__(self) -> None:
            self.close_calls = 0

        def observation(self) -> Observation:
            return Observation(
                run_id=exit_kind,
                step_index=0,
                mode="mock",
                world_revision=WorldStateRevision(
                    telemetry_sequence=0,
                    capability_epoch=1,
                ),
            )

        async def reset(self, *, seed: int | None = None) -> Observation:
            del seed
            if exit_kind == "reset_exception":
                raise RuntimeError("reset failed")
            return self.observation()

        async def observe(self) -> Observation:
            return self.observation()

        async def step(self, action: Action) -> Transition:
            if exit_kind == "environment_error":
                raise RuntimeError("dispatch failed")
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    accepted=True,
                    executed=False,
                    dry_run=True,
                ),
                observation=self.observation(),
                terminated=isinstance(action, StopAction),
            )

        async def close(self) -> FinalSafeStateOutcome:
            self.close_calls += 1
            return FinalSafeStateOutcome(
                status=FinalSafeStateStatus.PAUSE_CONFIRMED,
                reason="Confirmed by the exit invariant.",
                initial_sequence=10,
                confirmed_sequence=11,
            )

    class ExitPlanner(Planner):
        def __init__(self) -> None:
            self.entered = asyncio.Event()

        async def decide(self, observation: Observation) -> PlannerDecision:
            del observation
            if exit_kind == "cancellation":
                self.entered.set()
                await asyncio.Event().wait()
            return PlannerDecision(
                intent="Stop.",
                rationale="Exercise the selected runtime exit.",
                action=StopAction(reason="done"),
                confidence=1.0,
            )

    async def scenario() -> None:
        environment = ExitEnvironment()
        planner = ExitPlanner()
        logger = SessionLogger(tmp_path / f"{exit_kind}.jsonl", exit_kind)
        runtime = AgentRuntime(
            run_id=exit_kind,
            environment=environment,
            operation_port=operation_port(environment),
            planner=planner,
            policy=OperationPolicy(
                SafetyConfig(
                    allow_action_kinds=["stop"],
                    max_actions_per_minute=500,
                ),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
        )
        try:
            if exit_kind == "cancellation":
                task = asyncio.create_task(runtime.run(max_steps=1))
                await planner.entered.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif exit_kind == "budget":
                await runtime.run(max_steps=0)
            elif exit_kind == "reset_exception":
                with pytest.raises(RuntimeError, match="reset failed"):
                    await runtime.run(max_steps=1)
            else:
                await runtime.run(max_steps=1)
        finally:
            logger.close()

        assert environment.close_calls == 1
        events = [
            json.loads(line) for line in (tmp_path / f"{exit_kind}.jsonl").read_text().splitlines()
        ]
        final_events = [event for event in events if event["event_type"] == "run_finished_safety"]
        assert len(final_events) == 1
        assert final_events[0]["payload"]["status"] == "pause_confirmed"

    asyncio.run(scenario())


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

    changes = OutcomeRecorder._telemetry_changes(before, after)

    assert "distance to Barman: 96.00 -> 82.00 (14.00 closer)" in changes
    assert "camera bearing to Barman: -70.0 -> -25.0 degrees" in changes


def _camera_recovery_receipt(status: CameraRecoveryStatus) -> ActionReceipt:
    candidate = CameraFrameScore(
        candidate="controller_candidate",
        screenshot_path=Path("candidate.png"),
        screenshot_sha256="0" * 64,
        telemetry_sequence=12,
        frame_sequence=3,
        floor=0,
        score=0.9,
        edge_density=0.9,
        contrast=0.9,
        color_diversity=0.9,
        nonflat_fraction=0.9,
        inverse_dominant_color=0.9,
        selected_world_label_visible=True,
        anchor_distance=0.0,
        clear=status is not CameraRecoveryStatus.FAILED_AFTER_BOUNDED_ATTEMPTS,
    )
    return ActionReceipt(
        action=RecoverCameraViewAction(),
        accepted=True,
        executed=True,
        dry_run=False,
        semantic=SemanticActionReceipt(
            action_kind="recover_camera_view",
            contract_version="1.0",
            revalidation="Revalidated for the test.",
            camera_recovery=CameraRecoveryEvidence(
                status=status,
                selected_character_id="char-puhat",
                selected_character_name="Puhat",
                initial_floor=0,
                final_floor=0,
                clear_score_threshold=0.72,
                anchor_max_distance=30.0,
                paused_for_recovery=False,
                primitive_actions=(0 if status is CameraRecoveryStatus.ALREADY_CLEAR else 4),
                follow_method=(
                    "already_anchored"
                    if status is CameraRecoveryStatus.ALREADY_CLEAR
                    else "portrait_double_click"
                ),
                chosen_candidate=candidate.candidate,
                candidates=[candidate],
            ),
        ),
    )


def test_displacement_without_new_choices_is_not_progress() -> None:
    """A move that reveals nothing is a no-op, however far it travelled.

    Live run live-trade-surface-20260729-r1 assessed five blind
    `move_in_direction` hops as `changed` because the actor's coordinates and
    the option's own pause/speed transitions differed. The planner was told
    five times that it had produced an observed change while the choice set
    never moved, and it kept walking.
    """

    receipt = ActionReceipt(
        action=MoveInDirectionAction(
            bearing_degrees=270.0,
            distance_units=100.0,
            expected_effect="Move west to explore The Hub for the bar.",
        ),
        accepted=True,
        executed=True,
        dry_run=False,
    )

    assessment, feedback = OutcomeRecorder._assess_outcome(
        receipt,
        TelemetrySnapshot(),
        visual_change=0.6,
        telemetry_changes=[
            TelemetryChange("paused: True -> False", decision_relevant=False),
            TelemetryChange("speed: 0.0 -> 1.0", decision_relevant=False),
            TelemetryChange("Puhat moved 103.69 world units", decision_relevant=False),
        ],
        movement_distance=103.69,
    )

    assert assessment == "no_op"
    assert "moved" in feedback
    assert "do not repeat" in feedback.lower()


def test_displacement_that_reveals_a_new_choice_is_progress() -> None:
    receipt = ActionReceipt(
        action=MoveInDirectionAction(
            bearing_degrees=0.0,
            distance_units=50.0,
            expected_effect="Move north to search for the bar.",
        ),
        accepted=True,
        executed=True,
        dry_run=False,
    )

    assessment, _ = OutcomeRecorder._assess_outcome(
        receipt,
        TelemetrySnapshot(),
        visual_change=0.6,
        telemetry_changes=[
            TelemetryChange("Puhat moved 49.10 world units", decision_relevant=False),
            TelemetryChange("visible entities appeared: Hesric"),
        ],
        movement_distance=49.10,
    )

    assert assessment == "changed"


def test_world_time_transition_alone_is_not_progress() -> None:
    receipt = ActionReceipt(
        action=PauseAction(paused=True),
        accepted=True,
        executed=True,
        dry_run=False,
    )

    assessment, feedback = OutcomeRecorder._assess_outcome(
        receipt,
        TelemetrySnapshot(),
        visual_change=0.0,
        telemetry_changes=[
            TelemetryChange("paused: False -> True", decision_relevant=False),
            TelemetryChange("speed: 1.0 -> 0.0", decision_relevant=False),
        ],
        movement_distance=0.0,
    )

    assert assessment == "no_op"
    assert "world time" in feedback.lower()


def test_camera_recovery_that_found_nothing_to_do_is_not_progress() -> None:
    already_clear = OutcomeRecorder._assess_outcome(
        _camera_recovery_receipt(CameraRecoveryStatus.ALREADY_CLEAR),
        TelemetrySnapshot(),
        visual_change=0.0,
        telemetry_changes=[],
        movement_distance=0.0,
    )
    recovered = OutcomeRecorder._assess_outcome(
        _camera_recovery_receipt(CameraRecoveryStatus.RECOVERED),
        TelemetrySnapshot(),
        visual_change=0.0,
        telemetry_changes=[],
        movement_distance=0.0,
    )

    assert already_clear[0] == "no_op"
    assert "already" in already_clear[1].lower()
    assert recovered[0] == "changed"


def test_telemetry_changes_mark_mechanical_deltas_as_not_decision_relevant() -> None:
    before = TelemetrySnapshot.model_validate(
        {
            "game": {"paused": True, "speed_multiplier": 0.0},
            "squad": [
                {
                    "id": "char-puhat",
                    "name": "Puhat",
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                }
            ],
            "ui": {"selected_character_id": "char-puhat"},
        }
    )
    after = TelemetrySnapshot.model_validate(
        {
            "game": {"paused": False, "speed_multiplier": 1.0},
            "squad": [
                {
                    "id": "char-puhat",
                    "name": "Puhat",
                    "position": {"x": 0.0, "y": 0.0, "z": 50.0},
                }
            ],
            "ui": {"selected_character_id": "char-puhat"},
        }
    )

    changes = OutcomeRecorder._telemetry_changes_detailed(before, after)
    relevant = {change.label for change in changes if change.decision_relevant}
    mechanical = {change.label for change in changes if not change.decision_relevant}

    assert mechanical == {
        "paused: True -> False",
        "speed: 0.0 -> 1.0",
        "Puhat moved 50.00 world units",
    }
    assert not relevant
    assert [change.label for change in changes] == OutcomeRecorder._telemetry_changes(
        before,
        after,
    )


def test_telemetry_changes_name_nutrition_by_its_model_facing_meaning() -> None:
    before = TelemetrySnapshot.model_validate(
        {"squad": [{"id": "char-hep", "name": "Hep", "selected": True, "hunger": 2.8}]}
    )
    after = TelemetrySnapshot.model_validate(
        {"squad": [{"id": "char-hep", "name": "Hep", "selected": True, "hunger": 2.6}]}
    )

    labels = OutcomeRecorder._telemetry_changes(before, after)

    assert "nutrition reserve: 2.80 -> 2.60" in labels
    assert not any(label.startswith("hunger") for label in labels)


def test_a_recorded_outcome_remembers_the_game_session_it_happened_in(
    tmp_path: Path,
) -> None:
    """Without this stamp, evidence outlives the world a load discarded.

    `run_id` is unchanged by a load, so it cannot answer "is this outcome still
    true?" once the agent can quickload for itself. The session is the only
    thing that rotates.
    """

    session = "session-AAAA0000AAAA0000-0000000000000002"

    class SessionEnvironment(AgentEnvironment):
        def observation(self) -> Observation:
            return Observation(
                run_id="session-run",
                step_index=0,
                mode="live",
                world_revision=WorldStateRevision(
                    telemetry_sequence=3,
                    capability_epoch=1,
                ),
                telemetry=TelemetrySnapshot(
                    sequence=3,
                    identity_session_id=session,
                    game=GameState(loaded=True, paused=True),
                ),
            )

        async def reset(self, *, seed: int | None = None) -> Observation:
            del seed
            return self.observation()

        async def observe(self) -> Observation:
            return self.observation()

        async def step(self, action: Action) -> Transition:
            return Transition(
                receipt=ActionReceipt(
                    action=action,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                ),
                observation=self.observation(),
                terminated=isinstance(action, StopAction),
            )

        async def close(self) -> FinalSafeStateOutcome:
            return FinalSafeStateOutcome(
                status=FinalSafeStateStatus.PAUSE_CONFIRMED,
                reason="Confirmed by the test.",
                initial_sequence=3,
                confirmed_sequence=4,
            )

    class PausePlanner(Planner):
        async def decide(self, observation: Observation) -> PlannerDecision:
            del observation
            return PlannerDecision(
                intent="Pause.",
                rationale="Exercise one recorded outcome.",
                action=PauseAction(paused=True),
                confidence=1.0,
            )

    async def scenario() -> None:
        logger = SessionLogger(tmp_path / "session.jsonl", "session-run")
        environment = SessionEnvironment()
        runtime = AgentRuntime(
            run_id="session-run",
            environment=environment,
            operation_port=operation_port(environment),
            planner=PausePlanner(),
            policy=OperationPolicy(
                SafetyConfig(allow_action_kinds=["pause"], max_actions_per_minute=500),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
        )
        try:
            await runtime.run(max_steps=1)
        finally:
            logger.close()

        events = [
            json.loads(line) for line in (tmp_path / "session.jsonl").read_text().splitlines()
        ]
        outcomes = [e for e in events if e["event_type"] == "action_outcome"]
        assert outcomes, "expected one recorded action outcome"
        assert outcomes[0]["payload"]["identity_session_id"] == session

    asyncio.run(scenario())
