from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.affordance_requests import aggregate_affordance_requests
from kenshi_agent.config import MockConfig, PlanningConfig, SafetyConfig
from kenshi_agent.env import MockEnvironment
from kenshi_agent.evals import evaluate_log
from kenshi_agent.models import (
    AffordanceIntentClass,
    AffordanceRequestRecord,
    AffordanceRequestStatus,
    AffordanceUrgency,
    Condition,
    ConditionKind,
    ConditionOperator,
    NoopAction,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanStep,
    RequestAffordanceAction,
    RiskBudget,
    StopAction,
    WorldStateRevision,
)
from kenshi_agent.planners.base import Planner
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import MAX_RETAINED_AFFORDANCE_REQUESTS, AgentRuntime
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry


def fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def request_action(
    *,
    capability_slug: str = "operate_world_target",
    capability_description: str = (
        "Perform the contextual task on an exact world target."
    ),
    blocked_goal: str = "Work an ore resource to earn something sellable.",
    why_needed: str = (
        "The authorable surface has movement and interaction controls "
        "but no exact-target world-task intention."
    ),
    evidence: str = (
        "Kenshi exposes mining and other object tasks through "
        "targeted right-click interaction."
    ),
    urgency: AffordanceUrgency = AffordanceUrgency.BLOCKS_CURRENT_GOAL,
) -> RequestAffordanceAction:
    return RequestAffordanceAction(
        intent_class=AffordanceIntentClass.INTERACT,
        capability_slug=capability_slug,
        capability_description=capability_description,
        blocked_goal=blocked_goal,
        why_needed=why_needed,
        evidence=evidence,
        available_workaround="Trade existing inventory near town.",
        urgency=urgency,
    )


def candidate_plan(
    current: Observation,
    *,
    plan_id: str,
    capability_description: str = (
        "Perform the contextual task on an exact world target."
    ),
) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id=plan_id,
        plan_version=1,
        objective="Continue safely while surfacing one candidate gap.",
        control_mode=current.control_mode,
        based_on_revision=current.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="continue",
                action=NoopAction(reason="No world input is needed for this proof."),
                preconditions=[fresh()],
                success_conditions=[],
                timeout_seconds=5.0,
            )
        ],
        entry_step_id="continue",
        max_actions=1,
        max_wall_seconds=10.0,
        max_game_seconds=10.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
        affordance_candidates=[
            request_action(capability_description=capability_description)
        ],
    )


class CandidateTwiceThenStopPlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0
        self.observations: list[Observation] = []

    async def decide(self, current: Observation) -> PlannerOutput:
        self.calls += 1
        self.observations.append(current)
        if self.calls <= 2:
            description = (
                "Perform the contextual task on an exact world target."
                if self.calls == 1
                else "Work a selected world object through its contextual action."
            )
            return candidate_plan(
                current,
                plan_id=f"candidate-{self.calls}",
                capability_description=description,
            )
        plan = candidate_plan(current, plan_id="candidate-stop")
        return plan.model_copy(
            update={
                "steps": [
                    PlanStep(
                        step_id="stop",
                        action=StopAction(reason="Candidate integration proved."),
                        preconditions=[fresh()],
                        timeout_seconds=5.0,
                    )
                ],
                "entry_step_id": "stop",
                "affordance_candidates": [],
            },
            deep=True,
        )


class CandidateAndStopPlanner(Planner):
    def __init__(self, *, stale_basis: bool = False) -> None:
        self.stale_basis = stale_basis

    async def decide(self, current: Observation) -> PlannerOutput:
        plan = candidate_plan(current, plan_id="automatic-candidate")
        basis = (
            WorldStateRevision(telemetry_sequence=999_999)
            if self.stale_basis
            else current.world_revision
        )
        return plan.model_copy(
            update={
                "based_on_revision": basis,
                "objective": "Continue safely while surfacing one candidate gap.",
                "steps": [
                    PlanStep(
                        step_id="stop",
                        action=StopAction(reason="Bounded candidate proof complete."),
                        preconditions=[fresh()],
                        success_conditions=[],
                        timeout_seconds=5.0,
                    )
                ],
                "entry_step_id": "stop",
                "affordance_candidates": [request_action()],
            },
            deep=True,
        )


def test_affordance_demand_is_one_sidecar_and_not_a_planner_action() -> None:
    with pytest.raises(ValidationError):
        PlanStep(
            step_id="legacy-request",
            action=request_action(),
            preconditions=[fresh()],
            timeout_seconds=5.0,
        )

    current = Observation(run_id="candidate-shape", step_index=0, mode="mock")
    plan = candidate_plan(current, plan_id="candidate-shape")
    payload = plan.model_dump(mode="json")
    payload["affordance_candidates"] = [request_action(), request_action()]
    with pytest.raises(ValidationError):
        PlanEnvelope.model_validate(payload)


def test_accepted_output_automatically_records_a_candidate_without_an_action(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment = MockEnvironment(
            MockConfig(random_events=False),
            tmp_path,
            "automatic-candidate",
        )
        log_path = tmp_path / "automatic-candidate.jsonl"
        logger = SessionLogger(log_path, "automatic-candidate")
        runtime = AgentRuntime(
            run_id="automatic-candidate",
            environment=environment,
            planner=CandidateAndStopPlanner(),
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["stop"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                observation_pump_enabled=False,
                max_plan_steps=1,
                max_actions_per_plan=1,
            ),
        )
        try:
            await runtime.run(max_steps=2)
        finally:
            logger.close()

        events = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        candidates = [
            event
            for event in events
            if event["event_type"] == "affordance_request"
        ]
        assert len(candidates) == 1
        payload = candidates[0]["payload"]
        assert payload["source"] == "planner_sidecar"
        assert payload["classification"] == "needs_engineering_review"
        assert payload["world_command_created"] is False
        assert payload["controller_primitives"] == 0
        assert not [
            event
            for event in events
            if event["event_type"] == "action_receipt"
            and event["payload"]["action"]["kind"] == "request_affordance"
        ]

    asyncio.run(scenario())


def test_accepted_single_step_decision_records_its_candidate_sidecar(
    tmp_path: Path,
) -> None:
    class CandidateDecisionPlanner(Planner):
        async def decide(self, current: Observation) -> PlannerOutput:
            return PlannerDecision(
                intent="Stop after reporting the grounded capability gap.",
                rationale="The candidate sidecar does not require a game action.",
                action=StopAction(reason="Bounded candidate proof complete."),
                affordance_candidates=[request_action()],
            )

    async def scenario() -> None:
        environment = MockEnvironment(
            MockConfig(random_events=False),
            tmp_path,
            "decision-candidate",
        )
        log_path = tmp_path / "decision-candidate.jsonl"
        logger = SessionLogger(log_path, "decision-candidate")
        runtime = AgentRuntime(
            run_id="decision-candidate",
            environment=environment,
            planner=CandidateDecisionPlanner(),
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["stop"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
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
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        candidates = [
            event["payload"]
            for event in events
            if event["event_type"] == "affordance_request"
        ]
        assert len(candidates) == 1
        assert candidates[0]["source"] == "planner_sidecar"
        assert candidates[0]["origin"] == "decision"
        assert candidates[0]["step_id"] == "step-0"

    asyncio.run(scenario())


def test_rejected_output_does_not_turn_its_sidecar_or_failure_into_a_candidate(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment = MockEnvironment(
            MockConfig(random_events=False),
            tmp_path,
            "rejected-candidate",
        )
        log_path = tmp_path / "rejected-candidate.jsonl"
        logger = SessionLogger(log_path, "rejected-candidate")
        runtime = AgentRuntime(
            run_id="rejected-candidate",
            environment=environment,
            planner=CandidateAndStopPlanner(stale_basis=True),
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["stop"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                observation_pump_enabled=False,
                max_plan_steps=1,
                max_actions_per_plan=1,
                max_consecutive_replans=0,
            ),
        )
        try:
            await runtime.run(max_steps=1)
        finally:
            logger.close()

        events = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        assert any(event["event_type"] == "plan_rejected" for event in events)
        assert not [
            event
            for event in events
            if event["event_type"] == "affordance_request"
        ]

    asyncio.run(scenario())


def test_runtime_retains_and_deduplicates_affordance_requests_without_dispatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        planner = CandidateTwiceThenStopPlanner()
        environment = MockEnvironment(
            MockConfig(random_events=False),
            tmp_path,
            "affordance-runtime",
        )
        log_path = tmp_path / "events.jsonl"
        logger = SessionLogger(log_path, "affordance-runtime")
        runtime = AgentRuntime(
            run_id="affordance-runtime",
            environment=environment,
            planner=planner,
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["noop", "stop"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                observation_pump_enabled=False,
                max_plan_steps=1,
                max_actions_per_plan=1,
            ),
        )
        try:
            summary = await runtime.run(max_steps=3)
        finally:
            logger.close()

        assert summary.steps_completed == 3
        assert planner.calls == 3
        assert len(planner.observations[1].affordance_requests) == 1
        assert len(planner.observations[2].affordance_requests) == 1
        assert (
            planner.observations[2].affordance_requests[0].aggregation_key
            == "kenshi:interact:operate_world_target"
        )

        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        request_receipts = [
            event
            for event in events
            if event["event_type"] == "action_receipt"
            and event["payload"]["action"]["kind"] == "request_affordance"
        ]
        assert request_receipts == []

        request_events = [event for event in events if event["event_type"] == "affordance_request"]
        assert [event["payload"]["evidence"]["status"] for event in request_events] == [
            "retained",
            "duplicate",
        ]
        assert all(event["payload"]["world_command_created"] is False for event in request_events)

        metrics = evaluate_log(log_path)
        assert metrics.affordance_requests == 2
        assert metrics.affordance_request_duplicates == 1
        aggregate = aggregate_affordance_requests([log_path])
        assert aggregate.classified_events == 2
        assert len(aggregate.candidates) == 1
        assert (
            aggregate.candidates[0].aggregation_key
            == "kenshi:interact:operate_world_target"
        )

    asyncio.run(scenario())


def test_capability_slug_refuses_free_prose_or_unstable_case() -> None:
    for invalid in (
        "Operate world target",
        "operate-world-target",
        "OPERATE_WORLD_TARGET",
        "operate",
    ):
        with pytest.raises(ValidationError):
            request_action(capability_slug=invalid)


def test_retained_aggregation_key_cannot_drift_from_its_typed_action() -> None:
    with pytest.raises(ValidationError, match="must match its typed action"):
        AffordanceRequestRecord(
            request_number=1,
            action=request_action(),
            based_on_revision=WorldStateRevision(),
            aggregation_key="kenshi:move:operate_world_target",
        )


def test_duplicate_suppression_only_ever_cites_a_visible_request(tmp_path: Path) -> None:
    """Suppression must never point at a record the planner cannot see.

    The invariant, not the instance: whatever answers "is this a duplicate?" has
    to be the same collection the planner reads. A parallel index answered it
    while the visible list was capped at 32, so the 33rd distinct gap evicted the
    first one and every later attempt to raise it again was suppressed as a
    duplicate of a record that no longer existed.
    """

    async def scenario() -> None:
        environment = MockEnvironment(MockConfig(random_events=False), tmp_path, "affordance-cap")
        logger = SessionLogger(tmp_path / "cap.jsonl", "affordance-cap")
        runtime = AgentRuntime(
            run_id="affordance-cap",
            environment=environment,
            planner=CandidateTwiceThenStopPlanner(),
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["request_affordance", "stop"],
                    max_actions_per_minute=1000,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            planning_config=PlanningConfig(observation_pump_enabled=False),
        )
        observation = await environment.reset()
        try:
            overflow = MAX_RETAINED_AFFORDANCE_REQUESTS + 8
            for index in range(overflow):
                await runtime._execute_affordance_request_action(
                    request_action(
                        capability_slug=f"missing_control_{index}",
                        capability_description=f"Missing control {index}.",
                        blocked_goal=f"goal {index}",
                        why_needed=f"why {index}",
                        evidence=f"evidence {index}",
                    ),
                    observation,
                    "plan",
                    1,
                    f"step-{index}",
                )
                assert len(runtime._affordance_requests) <= MAX_RETAINED_AFFORDANCE_REQUESTS

                # Re-raise every gap so far and hold the invariant on each verdict.
                for earlier in range(index + 1):
                    result = await runtime._execute_affordance_request_action(
                        request_action(
                            capability_slug=f"missing_control_{earlier}",
                            capability_description=f"Alternative prose {earlier}.",
                            blocked_goal=f"goal {earlier}",
                            why_needed=f"why {earlier}",
                            evidence=f"evidence {earlier}",
                        ),
                        observation,
                        "plan",
                        1,
                        f"again-{earlier}",
                    )
                    evidence = result.receipt.affordance_request
                    assert evidence is not None
                    visible = {
                        record.request_number: record.aggregation_key
                        for record in runtime._affordance_requests
                    }
                    if evidence.status is AffordanceRequestStatus.DUPLICATE:
                        assert evidence.request_number in visible, (
                            f"suppressed request #{evidence.request_number} as a "
                            "duplicate of a record the planner cannot see"
                        )
                        assert visible[evidence.request_number] == evidence.aggregation_key
                    else:
                        assert evidence.aggregation_key in visible.values()
        finally:
            logger.close()

    asyncio.run(scenario())
