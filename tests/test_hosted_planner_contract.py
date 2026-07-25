from __future__ import annotations

import asyncio
import json
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
from kenshi_agent.planners.openrouter_planner import OpenRouterPlanner


def observation(
    *,
    planning_mode: PlanningMode = PlanningMode.CONTINUOUS,
    screen: str = "world",
    policy: LiveContinuousPolicy = LiveContinuousPolicy.DIALOGUE_INTERACTION_V1,
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
    """The budget scales with how much plan the model is being asked for.

    It used to vary by Kenshi screen, encoding how many steps the calibrated
    food recipe needed from each phase. With that recipe retired the only honest
    inputs are the planning mode and how much plan is actually outstanding.
    """

    config = PlannerConfig()

    # One decision needs only the base budget.
    assert (
        output_token_budget(
            config,
            observation(planning_mode=PlanningMode.SINGLE_STEP),
            max_plan_steps=4,
        )
        == 4096
    )

    # A fresh continuous plan may use every step it is allowed.
    assert output_token_budget(config, observation(screen="trade"), max_plan_steps=1) == 6144
    assert output_token_budget(config, observation(screen="world"), max_plan_steps=2) == 8192
    # ...up to the configured ceiling.
    assert output_token_budget(config, observation(screen="world"), max_plan_steps=8) == 12288

    # The screen no longer changes the answer; only the step allowance does.
    for screen in ("trade", "dialogue", "world"):
        assert (
            output_token_budget(config, observation(screen=screen), max_plan_steps=2) == 8192
        )

    # A patch only has to replace what remains.
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
    planner.config = PlannerConfig(
        include_screenshot=False,
        # Above the irreducible safety envelope, which grows as actions are
        # added to the catalog; real profiles budget 24k-30k.
        max_observation_chars=4000,
    )
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(responses=responses)
    planner.max_plan_steps = 4

    oversized = observation(planning_mode=PlanningMode.SINGLE_STEP).model_copy(
        update={"events": ["nested Unicode 食料 " + "x" * 500 for _ in range(20)]}
    )
    result = asyncio.run(planner.decide(oversized))

    assert isinstance(result, PlannerDecision)
    assert responses.kwargs["max_output_tokens"] == 4096
    assert responses.kwargs["reasoning"] == {"effort": "low"}
    input_text = responses.kwargs["input"][0]["content"][0]["text"]
    planner_payload = input_text.split("\n\n", maxsplit=1)[1]
    parsed_payload = json.loads(planner_payload)
    assert len(planner_payload) <= 4000
    assert parsed_payload["observation_budget"]["truncated"] is True


def test_openrouter_request_receives_the_same_valid_budgeted_json() -> None:
    class FakeCompletions:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def create(self, **kwargs: Any) -> SimpleNamespace:
            self.kwargs = kwargs
            decision = PlannerDecision(
                intent="Stop safely.",
                rationale="The fake hosted response is complete.",
                action=StopAction(reason="Test complete."),
                confidence=1.0,
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=decision.model_dump_json())
                    )
                ]
            )

    completions = FakeCompletions()
    planner = object.__new__(OpenRouterPlanner)
    planner.config = PlannerConfig(
        include_screenshot=False,
        # Above the irreducible safety envelope, which grows as actions are
        # added to the catalog; real profiles budget 24k-30k.
        max_observation_chars=4000,
    )
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )

    oversized = observation(planning_mode=PlanningMode.SINGLE_STEP).model_copy(
        update={"events": ["nested Unicode 食料 " + "x" * 500 for _ in range(20)]}
    )
    result = asyncio.run(planner.decide(oversized))

    assert isinstance(result, PlannerDecision)
    input_text = completions.kwargs["messages"][1]["content"][0]["text"]
    planner_payload = input_text.split("\n\n", maxsplit=1)[1]
    parsed_payload = json.loads(planner_payload)
    assert len(planner_payload) <= 4000
    assert parsed_payload["observation_budget"]["truncated"] is True


def test_only_the_active_policy_section_reaches_the_model() -> None:
    """A generic run must not be shipped the Barman recipe.

    Every policy's rules used to go out on every call: wasted tokens, and an
    invitation to anchor on a scenario the run is not in.
    """

    from pathlib import Path

    from kenshi_agent.models import LiveContinuousPolicy
    from kenshi_agent.planners.base import instructions_for_policy

    root = Path(__file__).resolve().parents[1]
    instructions = (root / "prompts" / "planner_system.md").read_text(encoding="utf-8")

    generic = instructions_for_policy(
        instructions, LiveContinuousPolicy.DIALOGUE_INTERACTION_V1
    )
    disabled = instructions_for_policy(instructions, LiveContinuousPolicy.DISABLED)

    # The generic run sees its own rules and none of the recipe.
    assert "approach_dialogue_target" in generic
    assert "Show me your goods." not in generic

    # The generic run is also spared the legacy macro guidance, since that
    # policy rejects SkillAction outright.
    assert "move_visible_terrain" not in generic

    # `disabled` means no *continuous* live policy, but single-step live runs
    # still author macros, so legacy skill guidance belongs there.
    assert "move_visible_terrain" in disabled
    assert "approach_dialogue_target" not in disabled

    # Shared guidance survives in all three, and no markers leak to the model.
    for rendered in (generic, disabled):
        assert "Your priorities, in order:" in rendered
        assert "<!-- policy:" not in rendered
        assert "<!-- /policy -->" not in rendered

    # Each rendering is a strict subset of the document: it carries its own
    # policy's section and drops the others. Their relative sizes are not a
    # property worth pinning — the generic section grows as actions are added.
    assert len(generic) < len(instructions)
    assert len(disabled) < len(instructions)


def test_the_planner_schema_avoids_keywords_providers_reject() -> None:
    """Each of these cost a live run to discover, so pin them here.

    Providers reject a whole request over how a constraint is spelled:
    Google refuses `const` and any non-string `enum`, Anthropic refuses
    `minimum`/`maximum` on integers. The meaning has to survive anyway, so a
    dropped bound moves into the field description.
    """
    from kenshi_agent.planners.schema_dialect import portable_response_format

    for model in (PlanEnvelope, PlanPatch, PlannerDecision):
        schema = portable_response_format(model)["json_schema"]["schema"]
        blob = json.dumps(schema)
        for rejected in ("const", "minimum", "maximum", "pattern", "multipleOf"):
            assert f'"{rejected}"' not in blob, f"{model.__name__} still emits {rejected}"

        def walk(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
                return
            if not isinstance(node, dict):
                return
            values = node.get("enum")
            if values is not None:
                assert all(isinstance(value, str) for value in values), (
                    f"non-string enum survived: {values}"
                )
            for value in node.values():
                walk(value)

        walk(schema)


def test_a_discriminator_stays_required_and_fully_specified() -> None:
    """The union only resolves if every branch pins its own `kind`."""
    from kenshi_agent.planners.schema_dialect import portable_response_format

    schema = portable_response_format(PlanEnvelope)["json_schema"]["schema"]
    branches = schema["$defs"]["PlanStep"]["properties"]["action"]["anyOf"]
    assert branches, "the action union lost its branches"
    for branch in branches:
        name = branch["$ref"].rsplit("/", maxsplit=1)[-1]
        definition = schema["$defs"][name]
        kind = definition["properties"]["kind"]
        assert kind["enum"] and isinstance(kind["enum"][0], str), name
        assert "kind" in definition["required"], name


def test_a_dropped_bound_is_still_described_to_the_model() -> None:
    from kenshi_agent.planners.schema_dialect import _sanitize

    result = _sanitize({"type": "integer", "minimum": 1, "maximum": 3})
    assert "minimum" not in result and "maximum" not in result
    assert result["description"] == "Must be at least 1, at most 3."

    kept = _sanitize({"type": "string", "description": "Why.", "maxLength": 500})
    assert kept["description"] == "Why. Must be at most 500 characters."


def test_a_fenced_reply_is_still_read_as_json() -> None:
    """A model that wraps its JSON should not cost a whole planning round trip."""
    from kenshi_agent.planners.openrouter_planner import _json_body

    assert json.loads(_json_body('```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(_json_body('{"a": 1}')) == {"a": 1}
    assert json.loads(_json_body('Here you go:\n{"a": 1}')) == {"a": 1}
