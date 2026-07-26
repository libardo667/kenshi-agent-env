from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kenshi_agent.config import MockConfig, PlanningConfig, SafetyConfig
from kenshi_agent.dialogue_interaction import dialogue_interaction_policy_errors
from kenshi_agent.env import MockEnvironment
from kenshi_agent.evals import evaluate_log
from kenshi_agent.models import (
    AffordanceUrgency,
    Condition,
    ConditionKind,
    ConditionOperator,
    IdempotencyPolicy,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanStep,
    RequestAffordanceAction,
    RiskBudget,
    StopAction,
)
from kenshi_agent.planners.base import Planner
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
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


def request_plan(current: Observation, *, plan_id: str) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id=plan_id,
        plan_version=1,
        objective="Report the missing deliberate-combat control.",
        control_mode=current.control_mode,
        based_on_revision=current.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="request",
                action=RequestAffordanceAction(
                    capability="perform the contextual task on an exact world target",
                    blocked_goal="Work an ore resource to earn something sellable.",
                    why_needed=(
                        "The authorable surface has movement and interaction controls "
                        "but no exact-target world-task intention."
                    ),
                    evidence=(
                        "Kenshi exposes mining and other object tasks through "
                        "targeted right-click interaction."
                    ),
                    available_workaround="Trade existing inventory near town.",
                    urgency=AffordanceUrgency.BLOCKS_CURRENT_GOAL,
                ),
                preconditions=[fresh()],
                success_conditions=[],
                timeout_seconds=5.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        entry_step_id="request",
        max_actions=1,
        max_wall_seconds=10.0,
        max_game_seconds=10.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )


class RequestTwiceThenStopPlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0
        self.observations: list[Observation] = []

    async def decide(self, current: Observation) -> PlannerOutput:
        self.calls += 1
        self.observations.append(current)
        if self.calls <= 2:
            return request_plan(current, plan_id=f"request-tool-{self.calls}")
        return PlannerDecision(
            intent="End the bounded affordance-request proof.",
            rationale="The request is retained and its duplicate was suppressed.",
            action=StopAction(reason="Affordance request integration proved."),
            confidence=1.0,
        )


def test_generic_policy_does_not_compose_steps_after_an_affordance_request(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment = MockEnvironment(MockConfig(random_events=False), tmp_path, "policy")
        try:
            current = await environment.reset()
        finally:
            await environment.close()

        plan = request_plan(current, plan_id="request-then-act")
        request_step = plan.steps[0].model_copy(update={"on_success": "request-again"})
        second_step = plan.steps[0].model_copy(
            update={"step_id": "request-again", "on_success": None}
        )
        composed = plan.model_copy(
            update={
                "steps": [request_step, second_step],
                "max_actions": 2,
            }
        )

        errors = dialogue_interaction_policy_errors(composed, current)
        assert any("request_affordance must be the plan's only step" in error for error in errors)

    asyncio.run(scenario())


def test_runtime_retains_and_deduplicates_affordance_requests_without_dispatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        planner = RequestTwiceThenStopPlanner()
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
                    allow_action_kinds=["request_affordance", "stop"],
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
            planner.observations[2].affordance_requests[0].action.capability
            == "perform the contextual task on an exact world target"
        )

        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        request_receipts = [
            event
            for event in events
            if event["event_type"] == "action_receipt"
            and event["payload"]["action"]["kind"] == "request_affordance"
        ]
        assert len(request_receipts) == 2
        assert all(event["payload"]["primitive_actions"] == 0 for event in request_receipts)
        assert all(event["payload"]["command_id"] is None for event in request_receipts)

        request_events = [event for event in events if event["event_type"] == "affordance_request"]
        assert [event["payload"]["evidence"]["status"] for event in request_events] == [
            "retained",
            "duplicate",
        ]
        assert all(event["payload"]["world_command_created"] is False for event in request_events)

        metrics = evaluate_log(log_path)
        assert metrics.affordance_requests == 2
        assert metrics.affordance_request_duplicates == 1

    asyncio.run(scenario())
