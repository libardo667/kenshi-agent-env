from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from kenshi_agent.affordances import offered_affordances, selection_for
from kenshi_agent.config import PLANNER_OUTPUT_POLICY, PlannerOutputPolicy, PlanningConfig
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    ControlMode,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    ContextActionKind,
    GameState,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    Vec3,
    WorldTarget,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.planners.base import (
    PLANNER_OUTPUT_POLICY_MARKER,
    planner_request_text,
    render_planner_instructions,
)
from kenshi_agent.planners.plan_proposal import (
    PlanProposal,
    compile_plan_proposal,
)


def _bounds(row: int) -> NormalizedPointerBounds:
    return NormalizedPointerBounds(
        min_x=0.1,
        max_x=0.3,
        min_y=row / 20,
        max_y=row / 20 + 0.03,
    )


def _observation(
    *,
    capabilities: list[str] | None = None,
    ui: UIState | None = None,
    squad: list[CharacterState] | None = None,
    nearby: list[NearbyEntity] | None = None,
    targets: list[WorldTarget] | None = None,
    active_shop_trader_count: int = 0,
) -> Observation:
    return Observation(
        run_id="proposal-contract",
        step_index=4,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(
            telemetry_sequence=41,
            frame_sequence=7,
            capability_epoch=2,
            observed_at_monotonic=123.5,
        ),
        telemetry=TelemetrySnapshot(
            sequence=41,
            identity_session_id="proposal-session",
            capabilities=capabilities or [],
            game=GameState(loaded=True, paused=True, speed_multiplier=1.0, money=1000),
            ui=ui or UIState(active_screen="world"),
            squad=squad or [],
            nearby_entities=nearby or [],
            world_targets=targets or [],
            active_shop_trader_count=active_shop_trader_count,
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.1,
    )


def _selected(
    observation: Observation,
    semantic: str,
    **parameters: Any,
) -> dict[str, Any]:
    offers = [
        offer
        for offer in offered_affordances(observation)
        if offer.semantic == semantic
    ]
    assert len(offers) == 1, [
        (offer.semantic, offer.operation_kind) for offer in offered_affordances(observation)
    ]
    return selection_for(offers[0], **parameters).model_dump(mode="json")


def test_runtime_compiles_only_model_choice_and_owns_envelope_bookkeeping() -> None:
    observation = _observation()
    observe = _selected(observation, "observe")
    proposal = {
        "objective": "Reconcile current evidence once, then observe again.",
        "steps": [
            {
                "selection": observe,
                "step_id": "model-authored-id",
                "preconditions": [],
                "success_conditions": [],
                "timeout_seconds": 0,
                "retry_budget": 2,
                "on_success": "invented",
            },
        ],
        "schema_version": "invented",
        "plan_id": "model-plan",
        "control_mode": "interface_only",
        "max_actions": 1,
        "max_wall_seconds": 0,
    }

    compiled = compile_plan_proposal(
        proposal,
        observation=observation,
        context_id="pc-7",
        planning=PlanningConfig(
            max_runtime_plan_steps=4,
            max_actions_per_plan=8,
            max_plan_wall_seconds=90,
            max_plan_game_seconds=600,
        ),
    )

    plan = compiled.plan
    assert plan.plan_id == "plan-pc-7"
    assert plan.control_mode is ControlMode.NATIVE_ASSISTED
    assert plan.based_on_revision == observation.world_revision
    assert [step.step_id for step in plan.steps] == ["step-1"]
    assert plan.steps[0].on_success is None
    assert all(step.action.kind == "noop" for step in plan.steps)
    assert all(
        step.affordance is not None
        and step.affordance.semantic == observe["semantic"]
        for step in plan.steps
    )
    assert all(step.success_conditions == [] for step in plan.steps)
    assert all(step.retry_budget == 0 for step in plan.steps)
    assert plan.max_wall_seconds == 90
    assert plan.max_game_seconds == 600


def test_context_order_compiles_through_generic_target_adapter() -> None:
    actor = CharacterState(id="actor-1", name="Bark", selected=True)
    target = WorldTarget(
        id="resource-1",
        name="Iron Resource",
        kind="natural_resource",
        position=Vec3(x=10, y=0, z=20),
        distance=25,
        context_actions=[ContextActionKind.OPERATE],
        default_task="operate",
    )
    observation = _observation(
        capabilities=[
            "control.perform_context_action",
            "world.context_targets",
            "game.pause",
            "identity.stable_handles",
        ],
        ui=UIState(
            active_screen="world",
            modal_open=False,
            dialogue_open=False,
            selected_character_id=actor.id,
            selected_character_ids=[actor.id],
        ),
        squad=[actor],
        targets=[target],
    )
    selected = _selected(observation, "operate")

    plan = compile_plan_proposal(
        {"objective": "Operate the exact offered target.", "steps": [{"selection": selected}]},
        observation=observation,
        context_id="pc-context",
        planning=PlanningConfig(),
    ).plan

    step = plan.steps[0]
    assert step.action.kind == "perform_context_action"
    assert step.action.target_id == target.id
    assert step.affordance is not None
    assert step.affordance.target is not None
    assert step.affordance.target.target_id == target.id
    assert plan.risk_budget.max_native_assisted_actions == 1
    assert step.timeout_seconds == 30


def test_a_choice_that_is_not_offered_fails_closed() -> None:
    """Naming is the authority now, so naming is what must fail closed.

    This asserted that a corrupted `affordance_id` is refused. Under the current
    contract a handle is provenance rather than authority - a model is not asked
    to reproduce one, precisely because it cannot check an invented hash against
    anything - so the property worth holding is that an unoffered *name* or an
    unoffered *target* cannot execute.
    """

    observation = _observation()

    unknown_name = _selected(observation, "observe")
    unknown_name["semantic"] = "disassemble_the_moon"
    unknown_name.pop("affordance_id", None)
    with pytest.raises(ValueError, match="no current choice is named"):
        compile_plan_proposal(
            {"objective": "Invent nothing.", "steps": [{"selection": unknown_name}]},
            observation=observation,
            context_id="pc-unknown-name",
            planning=PlanningConfig(),
        )

    unknown_target = _selected(observation, "observe")
    unknown_target["target_id"] = "entity-that-is-not-here"
    unknown_target.pop("affordance_id", None)
    with pytest.raises(ValueError, match="not on"):
        compile_plan_proposal(
            {"objective": "Invent nothing.", "steps": [{"selection": unknown_target}]},
            observation=observation,
            context_id="pc-unknown-target",
            planning=PlanningConfig(),
        )


def test_the_wire_offers_no_handle_to_invent() -> None:
    """The live failure, prevented by construction rather than tolerated.

    A run stopped at step zero three times because the model emitted
    `aff-9f556b8eaba80dbfd68c` - a plausible id that existed in no observation
    of any run. It was required to send one and had nothing real to copy.

    A field that is not on the wire cannot be fabricated, and cannot be echoed
    back to the model by its own retry feedback either.
    """

    selection = _selected(_observation(), "observe")

    assert "affordance_id" not in selection
    assert selection["semantic"] == "observe"


def test_compiler_preserves_valid_sidecars_and_quarantines_invalid_siblings() -> None:
    observation = _observation()
    proposal = {
        "objective": "Reconcile continuity without game input.",
        "steps": [{"selection": _selected(observation, "observe")}],
        "continuity_operations": [
            {
                "operation": "keep",
                "kind": "commitment",
                "content": "Find a safe food source.",
                "evidence_ids": ["current_observation"],
            },
            {
                "operation": "keep",
                "kind": "fact",
                "content": "Unsupported evidence.",
                "evidence_ids": ["not-an-evidence-id"],
            },
        ],
        "fieldbook_operations": [
            {
                "operation": "create_project",
                "kind": "journal",
                "title": "Journey",
                "summary": "Track the current journey.",
            },
            None,
        ],
    }

    compiled = compile_plan_proposal(
        proposal,
        observation=observation,
        context_id="pc-sidecars",
        planning=PlanningConfig(),
    )

    assert [item.operation for item in compiled.plan.continuity_operations] == ["keep"]
    assert [item.operation for item in compiled.plan.fieldbook_operations] == [
        "create_project"
    ]
    assert [(item.surface, item.index) for item in compiled.rejected_sidecars] == [
        ("continuity_operations", 1),
        ("fieldbook_operations", 1),
    ]


def test_compiler_rejects_more_than_one_choice_even_when_runtime_plan_ceiling_is_larger() -> None:
    observation = _observation()
    step = {"selection": _selected(observation, "observe")}
    proposal = {"objective": "Too long.", "steps": [step, step]}
    with pytest.raises(ValueError, match=PLANNER_OUTPUT_POLICY.cardinality_phrase):
        compile_plan_proposal(
            proposal,
            observation=observation,
            context_id="pc-limit",
            planning=PlanningConfig(max_runtime_plan_steps=4, max_actions_per_plan=8),
        )


def test_hosted_plan_schema_has_one_selection_contract_and_no_action_union() -> None:
    schema = PlanProposal.model_json_schema()
    assert (
        schema["properties"]["steps"]["maxItems"]
        == PLANNER_OUTPUT_POLICY.current_affordances_per_deliberation
    )
    step = schema["$defs"]["ProposedPlanStep"]
    assert set(step["properties"]) == {"selection"}
    selection = schema["$defs"]["AffordanceSelection"]
    # Named, not hashed. `semantic` is what the model must supply; the handle is
    # optional provenance, and the two disambiguators are only needed when a
    # name and target do not pick out one offer.
    # No handle on the wire at all. Strict projection marks every property
    # required, so an optional id would still have been demanded of the model -
    # and a model asked for a twenty-hex hash it does not have invents one.
    assert set(selection["properties"]) == {"semantic", "target_id", "parameters"}
    assert "affordance_id" not in selection["properties"]
    assert "anyOf" not in step["properties"]["selection"]


def test_typed_output_policy_structurally_owns_every_hosted_surface() -> None:
    """Prompt, schema, compiler, example, and config cannot drift independently."""

    policy = PLANNER_OUTPUT_POLICY
    assert PlanningConfig().planner_output_policy == policy
    with pytest.raises(ValidationError):
        PlannerOutputPolicy(current_affordances_per_deliberation=2)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        policy.current_affordances_per_deliberation = 2  # type: ignore[misc]

    schema = PlanProposal.model_json_schema()
    steps = schema["properties"]["steps"]
    expected = policy.current_affordances_per_deliberation
    assert steps["minItems"] == steps["maxItems"] == expected
    assert steps["description"] == policy.schema_description
    assert len(steps["examples"][0]) == expected

    root = Path(__file__).resolve().parents[1]
    template = (root / "prompts" / "planner_system.md").read_text(encoding="utf-8")
    assert template.count(PLANNER_OUTPUT_POLICY_MARKER) == 1
    rendered = render_planner_instructions(template, policy)
    assert PLANNER_OUTPUT_POLICY_MARKER not in rendered
    assert policy.cardinality_phrase in rendered
    assert policy.request_text in planner_request_text(PlanProposal, policy)
    for superseded in (
        "up to eight",
        "one to four useful selections",
        "Copy its `affordance_id`",
        "future-only patch",
    ):
        assert superseded not in rendered

    observation = _observation()
    selected = {"selection": _selected(observation, "observe")}
    with pytest.raises(ValueError) as captured:
        compile_plan_proposal(
            {
                "objective": "Keep the objective broad without reserving its future.",
                "steps": [selected, selected],
            },
            observation=observation,
            context_id="pc-policy-drift",
            planning=PlanningConfig(),
        )
    assert str(captured.value) == policy.cardinality_error(2)


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {"objective": None, "steps": [{}]},
        {"objective": " ", "steps": [{}]},
        {"objective": "Missing selections.", "steps": None},
        {"objective": "Missing selections.", "steps": []},
        {"objective": "Invalid selection.", "steps": [None]},
        {
            "objective": "Old planner action is gone.",
            "steps": [{"action": {"kind": "noop"}}],
        },
    ],
)
def test_compiler_rejects_missing_or_superseded_gameplay_contract(
    document: object,
) -> None:
    with pytest.raises(ValueError):
        compile_plan_proposal(
            document,
            observation=_observation(),
            context_id="pc-invalid",
            planning=PlanningConfig(),
        )
