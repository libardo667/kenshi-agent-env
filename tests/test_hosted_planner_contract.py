from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from kenshi_agent.config import PlannerConfig
from kenshi_agent.models import (
    ActivePlanContext,
    LiveContinuousPolicy,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlanningMode,
    PlanPatch,
    StopAction,
    TelemetrySnapshot,
    UIState,
)
from kenshi_agent.planners.base import output_token_budget, structured_output_model
from kenshi_agent.planners.openai_planner import OpenAIPlanner


def observation(
    *,
    planning_mode: PlanningMode = PlanningMode.CONTINUOUS,
    screen: str = "world",
    policy: LiveContinuousPolicy = LiveContinuousPolicy.FOOD_PROCUREMENT_V1,
    active_plan: ActivePlanContext | None = None,
) -> Observation:
    return Observation(
        run_id="hosted-contract",
        step_index=0,
        mode="live",
        planning_mode=planning_mode,
        live_execution_policy=policy,
        telemetry=TelemetrySnapshot(ui=UIState(active_screen=screen)),
        active_plan=active_plan,
    )


def test_hosted_output_model_switches_to_future_only_patch_for_active_plan() -> None:
    assert (
        structured_output_model(observation(planning_mode=PlanningMode.SINGLE_STEP))
        is PlannerDecision
    )
    assert structured_output_model(observation()) is PlanEnvelope
    assert (
        structured_output_model(
            observation(
                active_plan=ActivePlanContext(
                    plan_id="active-plan",
                    plan_version=2,
                    objective="Continue the bounded option.",
                    active_step_id="move",
                    remaining_actions=2,
                )
            )
        )
        is PlanPatch
    )


def test_output_token_budget_tracks_structured_response_complexity() -> None:
    config = PlannerConfig()
    assert (
        output_token_budget(
            config,
            observation(planning_mode=PlanningMode.SINGLE_STEP),
            max_plan_steps=4,
        )
        == 4096
    )
    assert output_token_budget(config, observation(screen="trade"), max_plan_steps=4) == 6144
    assert (
        output_token_budget(config, observation(screen="dialogue"), max_plan_steps=4)
        == 8192
    )
    assert output_token_budget(config, observation(screen="world"), max_plan_steps=4) == 10240
    assert (
        output_token_budget(
            config,
            observation(policy=LiveContinuousPolicy.DISABLED),
            max_plan_steps=4,
        )
        == 12288
    )
    assert (
        output_token_budget(
            config,
            observation(
                active_plan=ActivePlanContext(
                    plan_id="active-plan",
                    plan_version=1,
                    objective="Patch only the future.",
                    active_step_id="move",
                    remaining_actions=2,
                )
            ),
            max_plan_steps=4,
        )
        == 8192
    )


def test_openai_request_receives_the_computed_output_token_limit() -> None:
    class FakeResponses:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def parse(self, **kwargs: Any) -> SimpleNamespace:
            self.kwargs = kwargs
            return SimpleNamespace(
                output_parsed=PlannerDecision(
                    intent="Stop safely.",
                    rationale="The fake hosted response is complete.",
                    action=StopAction(reason="Test complete."),
                    confidence=1.0,
                ),
                output_text="",
            )

    responses = FakeResponses()
    planner = object.__new__(OpenAIPlanner)
    planner.config = PlannerConfig(include_screenshot=False)
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(responses=responses)
    planner.max_plan_steps = 4

    result = asyncio.run(
        planner.decide(observation(planning_mode=PlanningMode.SINGLE_STEP))
    )

    assert isinstance(result, PlannerDecision)
    assert responses.kwargs["max_output_tokens"] == 4096
    assert responses.kwargs["reasoning"] == {"effort": "low"}
