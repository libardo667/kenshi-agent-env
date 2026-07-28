from __future__ import annotations

import asyncio
import collections
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from kenshi_agent.config import PlannerConfig
from kenshi_agent.models import (
    ActivePlanContext,
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
    StopAction,
    TelemetrySnapshot,
    UIState,
    WorldStateRevision,
)
from kenshi_agent.planners.base import (
    output_token_budget,
    planner_context_manifest,
    structured_output_model,
)
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
            max_observation_chars=4000,
        )

        prepared = planner.prepare_input(oversized, context_id="pc-1")

        assert prepared.payload is not None
        payload = json.loads(prepared.payload)
        included = {record["memory_id"] for record in payload["memories"]}
        assert set(prepared.context.manifest.memory_ids) == included
        assert included < {record.memory_id for record in memories}
        assert prepared.context.manifest.payload_characters == len(prepared.payload)


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
    planner.config = PlannerConfig(include_screenshot=False, max_observation_chars=4000)
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

    # The lesson sticks: the second decision does not retry the refused form.
    assert isinstance(asyncio.run(planner.decide(single_step)), PlannerDecision)
    assert len(completions.calls) == 3
    assert "response_format" not in completions.calls[2]


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
    the button exists. `max_observation_chars` is a spending preference, so the
    action surface is rendered whole even when it costs more than that; only
    the model's real context ceiling may cut it, and that says so out loud.
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
            item_value=value,
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
    assert [entry["item_value"] for entry in by_window["BARMAN"]] == [30, 12]


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
