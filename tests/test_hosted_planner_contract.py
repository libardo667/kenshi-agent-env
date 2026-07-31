from __future__ import annotations

import asyncio
import collections
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

import kenshi_agent.planners.context_capacity as context_capacity
from kenshi_agent.config import PlannerConfig
from kenshi_agent.models import (
    ActivePlanContext,
    Condition,
    ContinuityOperationStatus,
    ContinuityOrigin,
    ContinuityReceiptDigest,
    Disposition,
    LiveContinuousPolicy,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
    NearbyEntity,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlanningMode,
    PlanPatch,
    PlanStep,
    RiskBudget,
    SkillAction,
    StopAction,
    TelemetrySnapshot,
    UIState,
    WorldStateRevision,
)
from kenshi_agent.planners.base import (
    HostedPlannerCallDiagnostics,
    HostedPlannerResponseError,
    output_token_budget,
    planner_action_kinds,
    planner_context_manifest,
    structured_output_model,
    validate_planner_output_surface,
    validate_planner_prompt_budget,
)
from kenshi_agent.planners.context_capacity import (
    HostedContextEnvelope,
    HostedModelCapacity,
    _fetch_json,
    _positive_integer,
    hosted_context_envelope,
    resolve_openrouter_model_capacity,
)
from kenshi_agent.planners.openai_planner import OpenAIPlanner
from kenshi_agent.planners.openrouter_planner import OpenRouterPlanner
from kenshi_agent.planners.schema_dialect import projected_response_format


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


def test_openrouter_capacity_comes_from_the_exact_model_metadata() -> None:
    calls: list[tuple[str, dict[str, str], float]] = []

    def fetch(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        calls.append((url, headers, timeout))
        return {
            "data": {
                "id": "google/gemini-3.1-flash-lite",
                "context_length": 1_048_576,
                "top_provider": {"max_completion_tokens": 65_536},
            }
        }

    capacity = resolve_openrouter_model_capacity(
        base_url="https://openrouter.example/api/v1",
        model="google/gemini-3.1-flash-lite",
        api_key="secret",
        timeout_seconds=7.5,
        fetch_json=fetch,
    )

    assert capacity == HostedModelCapacity(
        requested_model="google/gemini-3.1-flash-lite",
        context_window_tokens=1_048_576,
        max_completion_tokens=65_536,
        source="openrouter_models_api",
    )
    assert calls == [
        (
            "https://openrouter.example/api/v1/model/"
            "google/gemini-3.1-flash-lite",
            {
                "Accept": "application/json",
                "Authorization": "Bearer secret",
            },
            7.5,
        )
    ]


def test_default_metadata_fetcher_preserves_request_auth_timeout_and_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, float]] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self) -> bytes:
            return b'{"data": {"context_length": 12345}}'

    def open_request(request: Any, *, timeout: float) -> Response:
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(context_capacity, "urlopen", open_request)

    result = _fetch_json(
        "https://openrouter.example/api/v1/model/provider/model",
        {"Accept": "application/json", "Authorization": "Bearer secret"},
        7.5,
    )

    assert result == {"data": {"context_length": 12_345}}
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == (
        "https://openrouter.example/api/v1/model/provider/model"
    )
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Authorization") == "Bearer secret"
    assert timeout == 7.5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        (2, 2),
        (0, None),
        (-1, None),
        (True, None),
        (False, None),
        (1.5, None),
        ("1", None),
    ],
)
def test_model_capacity_numbers_are_strict_positive_plain_integers(
    value: Any,
    expected: int | None,
) -> None:
    assert _positive_integer(value) == expected


def test_configured_capacity_is_exact_authority_and_skips_metadata() -> None:
    def unexpected_fetch(
        url: str,
        headers: dict[str, str],
        timeout: float,
    ) -> Any:
        del url, headers, timeout
        raise AssertionError("configured authority must skip provider metadata")

    capacity = resolve_openrouter_model_capacity(
        base_url="https://openrouter.example/api/v1/",
        model="provider/model",
        api_key="secret",
        timeout_seconds=3.0,
        configured_context_window_tokens=123_456,
        fetch_json=unexpected_fetch,
    )

    assert capacity == HostedModelCapacity(
        requested_model="provider/model",
        context_window_tokens=123_456,
        max_completion_tokens=None,
        source="configured_override",
        lookup_error=None,
    )


def test_capacity_endpoint_normalizes_base_and_quotes_model_name() -> None:
    calls: list[str] = []

    def fetch(
        url: str,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        del headers, timeout
        calls.append(url)
        return {"data": {"context_length": 123_456}}

    resolve_openrouter_model_capacity(
        base_url="https://openrouter.example/api/vX/",
        model="provider/model name",
        api_key="secret",
        timeout_seconds=3.0,
        fetch_json=fetch,
    )

    assert calls == [
        "https://openrouter.example/api/vX/model/provider/model%20name"
    ]


def test_capacity_lookup_outage_does_not_invent_a_local_limit() -> None:
    def unavailable(
        url: str,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        del url, headers, timeout
        raise OSError("metadata route unavailable")

    capacity = resolve_openrouter_model_capacity(
        base_url="https://openrouter.example/api/v1",
        model="provider/model",
        api_key="secret",
        timeout_seconds=3.0,
        fetch_json=unavailable,
    )
    envelope = hosted_context_envelope(
        capacity,
        output_tokens=4_096,
        system_text="system",
        schema_text="schema",
        request_text="request",
        screenshot_included=False,
    )

    assert capacity == HostedModelCapacity(
        requested_model="provider/model",
        context_window_tokens=None,
        max_completion_tokens=None,
        source="provider_metadata_unavailable",
        lookup_error="OSError: metadata route unavailable",
    )
    assert envelope == HostedContextEnvelope(
        capacity=capacity,
        compaction_target_tokens=None,
        hard_observation_tokens=None,
        reserved_output_tokens=4_096,
        reserved_static_tokens=len(b"systemschemarequest"),
        reserved_image_tokens=0,
        proactive_headroom_tokens=4_096,
    )


@pytest.mark.parametrize(
    ("document", "expected_error"),
    [
        (
            {"data": []},
            "TypeError: model metadata data must be an object",
        ),
        (
            {"data": {}},
            "ValueError: model metadata has no positive context_length",
        ),
        (
            {"data": {"context_length": True}},
            "ValueError: model metadata has no positive context_length",
        ),
    ],
)
def test_malformed_provider_capacity_fails_open_without_inventing_a_limit(
    document: dict[str, Any],
    expected_error: str,
) -> None:
    capacity = resolve_openrouter_model_capacity(
        base_url="https://openrouter.example/api/v1/",
        model="provider/model with space",
        api_key="secret",
        timeout_seconds=3.0,
        fetch_json=lambda url, headers, timeout: document,
    )

    assert capacity == HostedModelCapacity(
        requested_model="provider/model with space",
        context_window_tokens=None,
        max_completion_tokens=None,
        source="provider_metadata_unavailable",
        lookup_error=expected_error,
    )


def test_capacity_lookup_error_detail_is_bounded() -> None:
    detail = "x" * 500

    def unavailable(
        url: str,
        headers: dict[str, str],
        timeout: float,
    ) -> Any:
        del url, headers, timeout
        raise OSError(detail)

    capacity = resolve_openrouter_model_capacity(
        base_url="https://openrouter.example/api/v1",
        model="provider/model",
        api_key="secret",
        timeout_seconds=3.0,
        fetch_json=unavailable,
    )

    assert capacity.lookup_error == "OSError: " + ("x" * 291)
    assert len(capacity.lookup_error) == 300


def test_context_envelope_reserves_response_static_image_and_headroom() -> None:
    capacity = HostedModelCapacity(
        requested_model="provider/model",
        context_window_tokens=100_000,
        max_completion_tokens=8_192,
        source="configured_override",
    )

    envelope = hosted_context_envelope(
        capacity,
        output_tokens=4_096,
        system_text="system",
        schema_text="schema",
        request_text="request",
        screenshot_included=True,
    )

    static = len(b"systemschemarequest")
    assert envelope.reserved_static_tokens == static
    assert envelope.reserved_output_tokens == 4_096
    assert envelope.reserved_image_tokens == 4_096
    assert envelope.hard_observation_tokens == 100_000 - 4_096 - 4_096 - static
    assert envelope.compaction_target_tokens == (
        envelope.hard_observation_tokens - 4_096
    )


def test_context_envelope_accepts_exact_completion_limit_and_one_token_payload() -> None:
    capacity = HostedModelCapacity(
        requested_model="provider/model",
        context_window_tokens=10,
        max_completion_tokens=4,
        source="configured_override",
    )

    envelope = hosted_context_envelope(
        capacity,
        output_tokens=4,
        system_text="12345",
        schema_text="",
        request_text="",
        screenshot_included=False,
    )

    assert envelope == HostedContextEnvelope(
        capacity=capacity,
        compaction_target_tokens=1,
        hard_observation_tokens=1,
        reserved_output_tokens=4,
        reserved_static_tokens=5,
        reserved_image_tokens=0,
        proactive_headroom_tokens=1,
    )


def test_context_envelope_rejects_output_over_model_completion_limit() -> None:
    capacity = HostedModelCapacity(
        requested_model="provider/model",
        context_window_tokens=100,
        max_completion_tokens=4,
        source="configured_override",
    )

    with pytest.raises(
        ValueError,
        match=(
            "requested output allowance 5 exceeds provider/model "
            "maximum completion allowance 4"
        ),
    ):
        hosted_context_envelope(
            capacity,
            output_tokens=5,
            system_text="",
            schema_text="",
            request_text="",
            screenshot_included=False,
        )


def test_context_envelope_rejects_zero_room_after_static_reservations() -> None:
    capacity = HostedModelCapacity(
        requested_model="provider/model",
        context_window_tokens=10,
        max_completion_tokens=None,
        source="configured_override",
    )

    with pytest.raises(
        ValueError,
        match=(
            "static request reservations consume the 10-token context window "
            "for provider/model"
        ),
    ):
        hosted_context_envelope(
            capacity,
            output_tokens=4,
            system_text="123456",
            schema_text="",
            request_text="",
            screenshot_included=False,
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
    # Even a plan reporting no remaining actions still needs room for one
    # bounded replacement step.
    assert (
        output_token_budget(
            config,
            observation(
                active_plan=ActivePlanContext(
                    plan_id="active-plan",
                    plan_version=1,
                    objective="Repair the empty future.",
                    active_step_id="move",
                    remaining_actions=0,
                )
            ),
            max_plan_steps=4,
        )
        == 6144
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
        context_window_tokens=80_000,
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
    assert len(planner_payload) > 4_000
    assert len(parsed_payload["events"]) == 20
    assert "observation_budget" not in parsed_payload


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
        context_window_tokens=80_000,
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
    assert len(planner_payload) > 4_000
    assert len(parsed_payload["events"]) == 20
    assert "observation_budget" not in parsed_payload


def test_openrouter_request_carries_its_configured_generation_contract() -> None:
    current = observation(planning_mode=PlanningMode.CONTINUOUS)
    fresh = Condition.model_validate(
        {
            "kind": "telemetry_fresh",
            "operator": "equals",
            "expected": True,
            "max_age_seconds": 3.0,
        }
    )

    class FakeCompletions:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def create(self, **kwargs: Any) -> SimpleNamespace:
            self.kwargs = kwargs
            plan = PlanEnvelope(
                schema_version="1.0",
                plan_id="generation-contract",
                plan_version=1,
                objective="Prove the continuous response budget reaches OpenRouter.",
                control_mode=current.control_mode,
                based_on_revision=current.world_revision,
                assumptions=[fresh],
                steps=[
                    PlanStep(
                        step_id="finish",
                        action=StopAction(reason="Test complete."),
                        preconditions=[fresh],
                        timeout_seconds=2.0,
                    )
                ],
                entry_step_id="finish",
                max_actions=1,
                max_wall_seconds=5.0,
                max_game_seconds=5.0,
                risk_budget=RiskBudget(
                    max_pointer_actions=0,
                    max_purchase_actions=0,
                    max_native_assisted_actions=0,
                ),
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=plan.model_dump_json())
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=1200,
                    completion_tokens=200,
                    total_tokens=1400,
                    prompt_tokens_details=SimpleNamespace(
                        cached_tokens=900,
                        cache_write_tokens=0,
                    ),
                ),
            )

    completions = FakeCompletions()
    planner = object.__new__(OpenRouterPlanner)
    planner.config = PlannerConfig(
        include_screenshot=False,
        reasoning_effort="high",
        temperature=0.1,
        max_output_tokens_base=2048,
        max_output_tokens_per_plan_step=1024,
        max_output_tokens_ceiling=8192,
        openrouter_require_parameters=True,
    )
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )

    result = asyncio.run(planner.decide(current))

    assert isinstance(result, PlanEnvelope)
    assert "reasoning_effort" not in completions.kwargs
    assert completions.kwargs["max_tokens"] == 6144
    assert completions.kwargs["temperature"] == 0.1
    assert completions.kwargs["extra_body"]["provider"]["require_parameters"] is True
    assert completions.kwargs["extra_body"]["reasoning"] == {"effort": "high"}
    assert completions.kwargs["extra_body"]["session_id"] == "kenshi:hosted-contract"
    system_blocks = completions.kwargs["messages"][0]["content"]
    assert system_blocks[-1]["cache_control"] == {"type": "ephemeral"}
    sent_schema = completions.kwargs["response_format"]["json_schema"]["schema"]
    assert _schema_action_kinds(sent_schema, PlanEnvelope) == planner_action_kinds(
        current
    )
    diagnostics = planner.take_call_diagnostics()
    assert diagnostics is not None
    assert diagnostics.cached_tokens == 900
    assert diagnostics.cache_write_tokens == 0


def test_openrouter_rejects_an_unadvertised_action_even_if_the_model_emits_it() -> None:
    current = observation(planning_mode=PlanningMode.SINGLE_STEP)

    class IgnoresProjectedSchema:
        async def create(self, **kwargs: Any) -> SimpleNamespace:
            del kwargs
            decision = PlannerDecision(
                intent="Use an unavailable macro.",
                rationale="The provider ignored its projected action schema.",
                action=SkillAction(name="not_advertised"),
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=decision.model_dump_json())
                    )
                ]
            )

    planner = object.__new__(OpenRouterPlanner)
    planner.config = PlannerConfig(include_screenshot=False)
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(
        chat=SimpleNamespace(completions=IgnoresProjectedSchema()),
    )

    with pytest.raises(HostedPlannerResponseError) as captured:
        asyncio.run(planner.decide(current))

    # A well-formed plan naming an action the observation does not allow is a
    # different failure from JSON that does not fit the model, and needs a
    # different answer. They arrived as one category until
    # live-hub-survival-pair-20260729-r1 died on three of them at step zero with
    # nothing recorded about which had happened.
    assert captured.value.category == "disallowed_action_surface"
    assert captured.value.detail
    assert captured.value.response_excerpt


def test_openai_rejects_an_unadvertised_action_after_sdk_parsing() -> None:
    current = observation(planning_mode=PlanningMode.SINGLE_STEP)
    decision = PlannerDecision(
        intent="Use an unavailable macro.",
        rationale="The SDK schema is broader than the authored context.",
        action=SkillAction(name="not_advertised"),
    )

    class ParsedUnavailableAction:
        async def parse(self, **kwargs: Any) -> SimpleNamespace:
            del kwargs
            return SimpleNamespace(output_parsed=decision, output_text="")

    planner = object.__new__(OpenAIPlanner)
    planner.config = PlannerConfig(include_screenshot=False)
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(responses=ParsedUnavailableAction())
    planner.max_plan_steps = 4

    with pytest.raises(ValueError, match="not authorable.*skill"):
        asyncio.run(planner.decide(current))


def test_openrouter_output_limit_is_typed_and_retains_provider_evidence() -> None:
    """An EOF is not attributable unless the provider terminal survives parsing."""

    class TruncatedCompletion:
        async def create(self, **kwargs: Any) -> SimpleNamespace:
            del kwargs
            return SimpleNamespace(
                id="generation-cut-short",
                model="google/gemini-3.1-flash-lite",
                provider="Google",
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        message=SimpleNamespace(
                            content='{"schema_version":"1.0","plan_id":"cut-short",'
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=19_000,
                    completion_tokens=12_288,
                    total_tokens=31_288,
                    completion_tokens_details=SimpleNamespace(
                        reasoning_tokens=11_700,
                    ),
                ),
            )

    planner = object.__new__(OpenRouterPlanner)
    planner.config = PlannerConfig(
        include_screenshot=False,
        context_window_tokens=80_000,
        openrouter_model="google/gemini-3.1-flash-lite",
        max_output_continuations=0,
    )
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(
        chat=SimpleNamespace(completions=TruncatedCompletion()),
    )

    with pytest.raises(HostedPlannerResponseError) as captured:
        asyncio.run(planner.decide(observation()))

    error = captured.value
    assert error.category == "output_truncated"
    assert error.failure_signature == (
        "openrouter:output_truncated:PlanEnvelope:length"
    )
    assert "one compact PlanEnvelope" in error.retry_feedback

    diagnostics = planner.take_call_diagnostics()
    assert diagnostics is not None
    assert diagnostics.finish_reason == "length"
    assert diagnostics.requested_model == "google/gemini-3.1-flash-lite"
    assert diagnostics.response_model == "google/gemini-3.1-flash-lite"
    assert diagnostics.provider_name == "Google"
    assert diagnostics.max_output_tokens == 12_288
    assert diagnostics.prompt_tokens == 19_000
    assert diagnostics.completion_tokens == 12_288
    assert diagnostics.reasoning_tokens == 11_700
    assert diagnostics.total_tokens == 31_288
    assert diagnostics.response_characters == 46
    assert diagnostics.schema_in_prompt is False


def test_openrouter_continues_a_length_terminal_with_preserved_reasoning() -> None:
    current = observation()
    fresh = Condition.model_validate(
        {
            "kind": "telemetry_fresh",
            "operator": "equals",
            "expected": True,
            "max_age_seconds": 3.0,
        }
    )
    plan = PlanEnvelope(
        schema_version="1.0",
        plan_id="continued-plan",
        plan_version=1,
        objective="Finish the same thought without regenerating it.",
        control_mode=current.control_mode,
        based_on_revision=current.world_revision,
        assumptions=[fresh],
        steps=[
            PlanStep(
                step_id="finish",
                action=StopAction(reason="Continuation complete."),
                preconditions=[fresh],
                timeout_seconds=2.0,
            )
        ],
        entry_step_id="finish",
        max_actions=1,
        max_wall_seconds=5.0,
        max_game_seconds=5.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )
    encoded = plan.model_dump_json()
    split_at = len(encoded) // 2
    prefix = encoded[:split_at]
    suffix = encoded[split_at:]
    reasoning_details = [
        {
            "type": "reasoning.encrypted",
            "data": "opaque-provider-thought",
            "format": "google-gemini-v1",
        }
    ]

    class Continues:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(
                    id="segment-1",
                    model="google/gemini-3.1-flash-lite",
                    provider="Google",
                    choices=[
                        SimpleNamespace(
                            finish_reason="length",
                            message=SimpleNamespace(
                                content=prefix,
                                reasoning_details=reasoning_details,
                            ),
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=19_000,
                        completion_tokens=12_288,
                        total_tokens=31_288,
                        completion_tokens_details=SimpleNamespace(
                            reasoning_tokens=11_700,
                        ),
                    ),
                )
            return SimpleNamespace(
                id="segment-2",
                model="google/gemini-3.1-flash-lite",
                provider="Google",
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=suffix,
                            reasoning_details=[],
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=31_500,
                    completion_tokens=900,
                    total_tokens=32_400,
                    completion_tokens_details=SimpleNamespace(
                        reasoning_tokens=100,
                    ),
                ),
            )

    completions = Continues()
    planner = object.__new__(OpenRouterPlanner)
    planner.config = PlannerConfig(
        include_screenshot=False,
        context_window_tokens=80_000,
        openrouter_model="google/gemini-3.1-flash-lite",
    )
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )

    result = asyncio.run(planner.decide(current))

    assert result == plan
    assert len(completions.calls) == 2
    continuation = completions.calls[1]
    assert "response_format" not in continuation
    assistant = continuation["messages"][-2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == prefix
    assert assistant["reasoning_details"] == reasoning_details
    assert "exact next character" in continuation["messages"][-1]["content"][0]["text"]

    diagnostics = planner.take_call_diagnostics()
    assert diagnostics is not None
    assert diagnostics.finish_reason == "stop"
    assert diagnostics.continuation_count == 1
    assert diagnostics.segment_finish_reasons == ("length", "stop")
    assert diagnostics.response_ids == ("segment-1", "segment-2")
    assert diagnostics.completion_tokens == 13_188
    assert diagnostics.reasoning_tokens == 11_800
    assert diagnostics.response_characters == len(encoded)


def test_openrouter_continuation_budget_is_exact() -> None:
    class NeverFinishes:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs: Any) -> SimpleNamespace:
            del kwargs
            self.calls += 1
            return SimpleNamespace(
                id=f"segment-{self.calls}",
                model="google/gemini-3.1-flash-lite",
                provider="Google",
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        message=SimpleNamespace(
                            content="{",
                            reasoning_details=[],
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=200,
                    total_tokens=300,
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=150),
                ),
            )

    completions = NeverFinishes()
    planner = object.__new__(OpenRouterPlanner)
    planner.config = PlannerConfig(
        include_screenshot=False,
        context_window_tokens=80_000,
        openrouter_model="google/gemini-3.1-flash-lite",
        max_output_continuations=2,
    )
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )

    with pytest.raises(HostedPlannerResponseError) as captured:
        asyncio.run(planner.decide(observation()))

    assert captured.value.category == "output_truncated"
    assert completions.calls == 3
    diagnostics = planner.take_call_diagnostics()
    assert diagnostics is not None
    assert diagnostics.continuation_count == 2
    assert diagnostics.segment_finish_reasons == ("length", "length", "length")
    assert diagnostics.response_characters == 3


def test_hosted_manifests_name_only_memories_in_the_final_budgeted_json() -> None:
    memories = [
        MemoryRecord(
            memory_id=f"mem-{index}",
            campaign_id="test",
            kind=MemoryKind.FACT,
            status=MemoryStatus.ACTIVE,
            content=f"Fact {index}: " + "x" * 1200,
            salience=index / 10,
            created_run_id="run-a",
            created_at=datetime.now(UTC),
        )
        for index in range(8)
    ]
    oversized = observation(planning_mode=PlanningMode.SINGLE_STEP).model_copy(
        update={
            "events": ["nested Unicode 食料 " + "x" * 500 for _ in range(20)],
            "memories": memories,
        }
    )

    for planner_type in (OpenAIPlanner, OpenRouterPlanner):
        planner = object.__new__(planner_type)
        planner.config = PlannerConfig(
            include_screenshot=False,
            context_window_tokens=52_000,
        )
        planner.instructions = "Return the requested schema."
        planner.max_plan_steps = 4

        prepared = planner.prepare_input(oversized, context_id="pc-1")

        assert prepared.payload is not None
        payload = json.loads(prepared.payload)
        included = {record["memory_id"] for record in payload["memories"]}
        assert set(prepared.context.manifest.memory_ids) == included
        assert included < {record.memory_id for record in memories}
        assert prepared.context.manifest.payload_characters == len(prepared.payload)
        assert prepared.context.manifest.context_capacity_source == (
            "configured_override"
        )
        assert prepared.context.manifest.context_window_tokens == 52_000
        assert prepared.context.manifest.compaction_target_tokens is not None


def test_memory_text_cannot_smuggle_target_authority_into_a_budgeted_input() -> None:
    """Only world-facing fields can deliver an entity as currently present."""

    telemetry = TelemetrySnapshot(
        sequence=7,
        nearby_entities=[
            NearbyEntity(
                id="entity-remembered",
                name="Remembered drifter",
                disposition=Disposition.NEUTRAL,
                distance=5.0,
                visible=True,
            )
        ],
    )
    current = observation(planning_mode=PlanningMode.SINGLE_STEP).model_copy(
        update={
            "world_revision": WorldStateRevision(telemetry_sequence=7),
            "telemetry": telemetry,
        }
    )
    assert current.current_memory_target_ids() == {"entity-remembered"}
    payload = {
        "world_revision": current.world_revision.model_dump(mode="json"),
        "memories": [
            {
                "memory_id": "mem-remembered",
                "target_id": "entity-remembered",
                "content": "I once saw this entity.",
            }
        ],
    }

    manifest = planner_context_manifest(
        current,
        context_id="pc-1",
        input_kind="budgeted_json",
        payload=payload,
    )

    assert manifest.memory_ids == ["mem-remembered"]
    assert manifest.current_target_ids == []


def test_manifest_names_only_continuity_receipts_in_the_final_payload() -> None:
    current = observation(planning_mode=PlanningMode.SINGLE_STEP)
    receipts = [
        ContinuityReceiptDigest(
            receipt_id=f"cor-{index:032x}",
            origin=ContinuityOrigin.PLAN,
            operation="keep",
            status=status,
            reason=reason,
            memory_id=memory_id,
            memory_status=(
                MemoryStatus.ACTIVE if memory_id is not None else None
            ),
            authored_context_id="pc-1",
            authored_revision=current.world_revision,
            commit_revision=current.world_revision,
            recorded_at=datetime.now(UTC),
        )
        for index, status, reason, memory_id in (
            (
                1,
                ContinuityOperationStatus.REJECTED,
                "The fact lacked evidence.",
                None,
            ),
            (
                2,
                ContinuityOperationStatus.ACCEPTED,
                "The commitment was kept.",
                "mem-accepted",
            ),
        )
    ]
    current = current.model_copy(
        update={"recent_continuity_receipts": receipts}
    )
    payload = {
        "world_revision": current.world_revision.model_dump(mode="json"),
        "recent_continuity_receipts": [
            receipts[1].model_dump(mode="json"),
        ],
    }

    manifest = planner_context_manifest(
        current,
        context_id="pc-2",
        input_kind="budgeted_json",
        payload=payload,
    )

    assert manifest.continuity_receipt_ids == [receipts[1].receipt_id]
    assert manifest.memory_ids == ["mem-accepted"]
    assert receipts[0].receipt_id not in manifest.continuity_receipt_ids


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


def test_every_code_derived_static_prompt_surface_stays_inside_the_budget() -> None:
    """A new rule or action must pay for itself instead of silently expanding."""

    from pathlib import Path

    from kenshi_agent.action_contracts import ACTION_CONTRACTS
    from kenshi_agent.models import PLANNER_CONTROL_ACTION_KINDS
    from kenshi_agent.planners.base import instructions_for_policy

    root = Path(__file__).resolve().parents[1]
    instructions = (root / "prompts" / "planner_system.md").read_text(encoding="utf-8")
    generic_actions = frozenset(
        set(PLANNER_CONTROL_ACTION_KINDS)
        | {
            kind
            for kind, contract in ACTION_CONTRACTS.items()
            if contract.planner_visible
        }
    )
    policy_actions = {
        LiveContinuousPolicy.DIALOGUE_INTERACTION_V1: generic_actions,
        LiveContinuousPolicy.DISABLED: generic_actions | {"skill"},
    }

    for policy, allowed_action_kinds in policy_actions.items():
        system_characters = len(instructions_for_policy(instructions, policy))
        for model in (PlannerDecision, PlanEnvelope, PlanPatch):
            schema = projected_response_format(
                model,
                allowed_action_kinds=allowed_action_kinds,
            )["json_schema"]["schema"]
            validate_planner_prompt_budget(
                policy=policy,
                system_characters=system_characters,
                schema_characters=len(json.dumps(schema)),
            )


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


def _schema_action_kinds(schema: dict[str, Any], model: type[Any]) -> set[str]:
    if model is PlannerDecision:
        action_schema = schema["properties"]["action"]
    else:
        action_schema = schema["$defs"]["PlanStep"]["properties"]["action"]
    return {
        schema["$defs"][branch["$ref"].rsplit("/", 1)[-1]]
        ["properties"]["kind"]["enum"][0]
        for branch in action_schema["anyOf"]
    }


def test_projected_schema_matches_the_exact_authorable_action_surface() -> None:
    """Decoding must not advertise actions the observation would reject."""

    current = observation().model_copy(
        update={
            "telemetry": TelemetrySnapshot(
                ui=UIState(active_screen="world"),
                capabilities=["ui.visible_controls"],
            )
        }
    )
    allowed = planner_action_kinds(current)
    schema = projected_response_format(
        PlanEnvelope,
        allowed_action_kinds=allowed,
    )["json_schema"]["schema"]

    assert _schema_action_kinds(schema, PlanEnvelope) == allowed
    assert "activate_visible_control" in allowed
    assert "move_in_direction" not in allowed
    assert "skill" not in allowed


def test_projected_schema_retains_only_reachable_definitions() -> None:
    """Removing an action branch must remove its otherwise-dead schema too."""

    current = observation()
    schema = projected_response_format(
        PlanEnvelope,
        allowed_action_kinds=planner_action_kinds(current),
    )["json_schema"]["schema"]
    definitions = schema["$defs"]

    def references(node: Any) -> set[str]:
        if isinstance(node, list):
            return set().union(*(references(item) for item in node), set())
        if not isinstance(node, dict):
            return set()
        found = {
            value.rsplit("/", 1)[-1]
            for key, value in node.items()
            if key == "$ref"
            and isinstance(value, str)
            and value.startswith("#/$defs/")
        }
        return found | set().union(
            *(references(value) for key, value in node.items() if key != "$defs"),
            set(),
        )

    root = {key: value for key, value in schema.items() if key != "$defs"}
    reachable = references(root)
    frontier = set(reachable)
    while frontier:
        name = frontier.pop()
        discovered = references(definitions[name]) - reachable
        reachable.update(discovered)
        frontier.update(discovered)

    assert set(definitions) == reachable
    assert "SkillAction" not in definitions


def test_output_surface_check_rejects_a_schema_valid_unadvertised_action() -> None:
    current = observation(planning_mode=PlanningMode.SINGLE_STEP)
    output = PlannerDecision(
        intent="Use an unavailable macro.",
        rationale="This is structurally valid but not currently authorable.",
        action=SkillAction(name="not_advertised"),
    )

    with pytest.raises(ValueError, match="not authorable.*skill"):
        validate_planner_output_surface(
            output,
            allowed_action_kinds=planner_action_kinds(current),
        )


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


def test_condition_schema_cannot_author_comparisons_the_runtime_must_reject() -> None:
    """Every condition admitted by hosted decoding must have executable meaning.

    A live collection turn spent 17 hosted calls emitting combinations the flat
    schema advertised but runtime validation rejected: null comparison values,
    field paths used as capabilities, and capability names used as fields.
    """
    from kenshi_agent.planners.schema_dialect import portable_response_format

    schema = portable_response_format(PlanEnvelope)["json_schema"]["schema"]
    condition = schema["$defs"]["Condition"]
    branches = [schema["$defs"][item["$ref"].rsplit("/", 1)[-1]] for item in condition["anyOf"]]
    by_kind = {
        branch["properties"]["kind"]["enum"][0]: branch
        for branch in branches
    }

    assert set(by_kind) == {"field", "capability", "telemetry_fresh"}

    for kind, branch in by_kind.items():
        expected = branch["properties"]["expected"]
        assert "expected" in branch["required"], kind
        assert {"type": "null"} not in expected.get("anyOf", []), kind

    field_path = by_kind["field"]["properties"]["path"]
    field_values = set(schema["$defs"][field_path["$ref"].rsplit("/", 1)[-1]]["enum"])
    assert field_values
    assert all(
        value in {"control_mode", "telemetry_stale"}
        or value.startswith(("telemetry.", "selected.", "target."))
        for value in field_values
    )

    capability_path = by_kind["capability"]["properties"]["path"]
    assert capability_path["type"] == "string"
    assert "null" not in json.dumps(capability_path)

    assert "path" not in by_kind["telemetry_fresh"]["properties"]


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


def test_a_provider_that_will_not_compile_the_schema_is_asked_in_the_prompt() -> None:
    """Anthropic caps the grammar it will compile, and this catalog is over it.

    The request itself is fine, so the planner asks again with the schema in
    the prompt instead of losing the run. It only pays to discover this once.
    """

    class Refuses:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            if "response_format" in kwargs:
                error = Exception(
                    "Error code: 400 - The compiled grammar is too large, which "
                    "would cause performance issues."
                )
                error.status_code = 400  # type: ignore[attr-defined]
                raise error
            decision = PlannerDecision(
                intent="Stop safely.",
                rationale="Answered without a compiled grammar.",
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

    completions = Refuses()
    planner = object.__new__(OpenRouterPlanner)
    planner.config = PlannerConfig(
        include_screenshot=False,
        context_window_tokens=80_000,
    )
    planner.instructions = "Return the requested schema."
    planner.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    single_step = observation(planning_mode=PlanningMode.SINGLE_STEP)
    assert isinstance(asyncio.run(planner.decide(single_step)), PlannerDecision)

    # Constrained first, then the same ask with the schema in the prompt.
    assert len(completions.calls) == 2
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]
    prompted = completions.calls[1]["messages"][-1]["content"][0]["text"]
    assert "JSON Schema" in prompted and '"properties"' in prompted
    assert completions.calls[1]["messages"][-1]["content"][0][
        "cache_control"
    ] == {"type": "ephemeral"}

    # The lesson sticks: the second decision does not retry the refused form.
    assert isinstance(asyncio.run(planner.decide(single_step)), PlannerDecision)
    assert len(completions.calls) == 3
    assert "response_format" not in completions.calls[2]

    # A different projected action surface gets its own constrained-decoding
    # attempt; one oversized schema must not poison every smaller schema.
    expanded = single_step.model_copy(
        update={
            "telemetry": TelemetrySnapshot(
                ui=UIState(active_screen="world"),
                capabilities=["ui.visible_controls"],
            )
        }
    )
    assert isinstance(asyncio.run(planner.decide(expanded)), PlannerDecision)
    assert len(completions.calls) == 5
    assert "response_format" in completions.calls[3]
    assert "response_format" not in completions.calls[4]


def test_an_unrelated_bad_request_is_not_retried_as_a_schema_problem() -> None:
    from kenshi_agent.planners.openrouter_planner import _is_schema_refusal

    grammar = Exception("Error code: 400 - The compiled grammar is too large")
    grammar.status_code = 400  # type: ignore[attr-defined]
    assert _is_schema_refusal(grammar)

    credit = Exception("Error code: 400 - Insufficient credits for this request")
    credit.status_code = 400  # type: ignore[attr-defined]
    assert not _is_schema_refusal(credit)

    rate_limited = Exception("Error code: 429 - schema")
    rate_limited.status_code = 429  # type: ignore[attr-defined]
    assert not _is_schema_refusal(rate_limited)


def test_the_action_surface_is_not_traded_away_for_a_smaller_payload() -> None:
    """A control the planner was not shown is one it cannot press.

    Truncating the control list does not buy a smaller observation, it buys an
    agent that cannot press a button it is looking at and has no way to learn
    the button exists. A proactive target therefore cannot cut this surface;
    only the hard request envelope may do so, and that says so out loud.
    """
    from kenshi_agent.models import NormalizedPointerBounds, VisibleUIControl

    controls = [
        VisibleUIControl(
            label=f"{role}-{index}",
            role=role,  # type: ignore[arg-type]
            window="SHOP",
            bounds=NormalizedPointerBounds(min_x=0.1, min_y=0.1, max_x=0.2, max_y=0.2),
        )
        for index in range(60)
        for role in ("button", "item", "text")
    ]
    crowded = observation().model_copy(
        update={
            "telemetry": TelemetrySnapshot(
                ui=UIState(active_screen="trade", visible_controls=controls),
                capabilities=["ui.visible_controls"],
            )
        }
    )

    def shown(max_chars: int, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("max_context_chars", 1_000_000)
        return json.loads(crowded.planner_payload(max_chars=max_chars, **kwargs))

    def listed(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            entry for group in payload["visible_controls"] for entry in group["controls"]
        ]

    # A budget far below what the controls cost does not cost the agent one.
    tight = shown(12000)
    assert len(listed(tight)) == len(controls)
    assert "visible_controls_truncated" not in tight
    assert len(listed(shown(60000))) == len(controls)

    # Controls arrive grouped by their window, which is the difference between
    # buying from a shop and selling your own coat, and the window is stated
    # once per group rather than repeated on every entry.
    assert [group["window"] for group in tight["visible_controls"]] == ["SHOP"]
    assert all("window" not in entry for entry in listed(tight))

    # The model's real ceiling does cut it - and never silently.
    squeezed = shown(12000, max_context_chars=9000)
    cut = listed(squeezed)
    assert 0 < len(cut) < len(controls)
    notice = squeezed["visible_controls_truncated"]
    assert notice["shown"] == len(cut) and notice["total"] == len(controls)

    # Even cut, no role is starved entirely.
    roles = collections.Counter(entry["role"] for entry in cut)
    assert len(roles) == 3, f"a role was starved entirely: {roles}"
    assert max(roles.values()) - min(roles.values()) <= 1, roles


def test_two_open_inventories_stay_distinguishable() -> None:
    """In a trade, which window a cell sits in decides buy versus sell.

    The same right-click buys from the shop's window and sells from your own,
    so a flat list of cells is not just verbose, it is the shape that once let
    a probe sell a character's clothes and weapon. Grouping makes the
    distinction structural rather than a field to be noticed.
    """
    from kenshi_agent.models import (
        CharacterState,
        NearbyEntity,
        NormalizedPointerBounds,
        VisibleUIControl,
    )

    def cell(label: str, window: str, value: int) -> VisibleUIControl:
        return VisibleUIControl(
            label=label,
            role="item",
            window=window,
            item_name=label,
            item_base_value=value,
            bounds=NormalizedPointerBounds(min_x=0.1, min_y=0.1, max_x=0.2, max_y=0.2),
        )

    trading = observation().model_copy(
        update={
            "telemetry": TelemetrySnapshot(
                ui=UIState(
                    active_screen="trade",
                    visible_controls=[
                        cell("Water", "BARMAN", 30),
                        cell("Rag Loincloth", "BARMAN", 12),
                        cell("Hep's Shirt", "HEP", 40),
                    ],
                ),
                # Kenshi captions the window "HEP" while the character is "Hep",
                # so ownership has to survive the case difference.
                squad=[CharacterState(id="hep-1", name="Hep", selected=True)],
                nearby_entities=[
                    NearbyEntity(
                        id="barman-1", name="Barman", shop_inventory_owner=True
                    )
                ],
                capabilities=["ui.visible_controls"],
            )
        }
    )

    groups = json.loads(trading.planner_payload(max_chars=30000))["visible_controls"]
    by_window = {group["window"]: group["controls"] for group in groups}
    assert set(by_window) == {"BARMAN", "HEP"}

    # Whose window it is arrives as a fact, not as a name the planner must
    # match, and the vendor's group carries the id purchase_item asks for.
    owners = {group["window"]: group for group in groups}
    assert owners["HEP"]["belongs_to"] == "you"
    assert owners["BARMAN"]["belongs_to"] == "vendor"
    assert owners["BARMAN"]["seller_id"] == "barman-1"
    assert "seller_id" not in owners["HEP"]
    assert [entry["item_name"] for entry in by_window["BARMAN"]] == [
        "Water",
        "Rag Loincloth",
    ]
    assert [entry["item_name"] for entry in by_window["HEP"]] == ["Hep's Shirt"]
    # Prices travel with the cell, so affording a thing needs no extra step.
    assert [entry["buy_price"] for entry in by_window["BARMAN"]] == [30, 12]


def test_somewhere_to_go_survives_the_payload_budget() -> None:
    """Being able to travel is useless while every destination is trimmed away.

    Talkable people arrive as a curated top-level digest that survives budgeting
    whole. Movement destinations lived only in `telemetry.nearby_entities`, a
    budgeted collection trimmed before anything else, so eighteen nearby
    characters became one in the payload - and that one was in the room the
    agent was already standing in.
    """
    from kenshi_agent.models import NearbyEntity

    crowd = [
        NearbyEntity(
            id=f"entity-{index}",
            name=f"Wanderer {index}",
            kind="character",
            distance=float(index * 20),
            has_dialogue=index < 3,
            disposition="neutral",
        )
        for index in range(1, 19)
    ]
    busy = observation().model_copy(
        update={"telemetry": TelemetrySnapshot(nearby_entities=crowd)}
    )
    payload = json.loads(busy.planner_payload(max_chars=30000))
    destinations = payload["travel_destinations"]

    assert destinations, "the agent must be shown somewhere it could walk to"
    distances = [entry["distance"] for entry in destinations]
    assert distances == sorted(distances, reverse=True), "furthest first"

    # No entry duplicates dialogue_targets: a second copy of the people it can
    # already talk to is not somewhere new to go.
    talkable = {target["id"] for target in payload["dialogue_targets"]}
    assert not talkable & {entry["id"] for entry in destinations}


def _diagnostics(*, finish_reason: str) -> HostedPlannerCallDiagnostics:
    return HostedPlannerCallDiagnostics(
        provider_kind="openrouter",
        output_model="PlanEnvelope",
        requested_model="m",
        response_model="m",
        provider_name="p",
        response_id="r",
        finish_reason=finish_reason,
        max_output_tokens=1,
        prompt_tokens=1,
        completion_tokens=1,
        reasoning_tokens=0,
        total_tokens=2,
        response_characters=1,
        system_characters=1,
        observation_characters=1,
        schema_characters=1,
        request_text_characters=1,
        schema_in_prompt=True,
        screenshot_included=False,
        continuation_count=0,
        segment_finish_reasons=(finish_reason,),
        response_ids=("r",),
        schema_refusal_count=0,
        native_finish_reason=None,
        segment_native_finish_reasons=(),
        provider_error_type=None,
        cached_tokens=0,
        cache_write_tokens=0,
    )


def test_a_schema_rejection_tells_the_model_what_to_fix() -> None:
    """"Malformed JSON" for a well-formed plan leaves nothing to correct.

    live-hub-survival-pair-20260729-r2 ended at step two with a sound plan —
    close the leftover screens, go mine iron — rejected three times for setting
    `retry_budget` on steps that are not `safe_to_retry`, and told each time
    only that its JSON was malformed. It was not; a field constraint failed. So
    the model returned the same plan and the run died.
    """

    diagnostics = _diagnostics(finish_reason="stop")
    error = HostedPlannerResponseError(
        "malformed_structured_output",
        diagnostics,
        detail="steps.0 Value error, retry_budget requires idempotency=safe_to_retry",
        response_excerpt='{"plan_id": "x"}',
    )

    assert "retry_budget requires idempotency=safe_to_retry" in error.retry_feedback
    assert error.detail
    assert error.response_excerpt


def test_a_disallowed_action_is_not_reported_as_broken_json() -> None:
    diagnostics = _diagnostics(finish_reason="stop")
    error = HostedPlannerResponseError(
        "disallowed_action_surface",
        diagnostics,
        detail="action 'harvest_resource' is not available in this observation",
        response_excerpt="{}",
    )

    assert "does not offer" in error.retry_feedback
    assert "harvest_resource" in error.retry_feedback
    assert "malformed" not in error.retry_feedback.lower()
