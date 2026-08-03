from __future__ import annotations

from typing import Any

import pytest

from kenshi_agent.affordances import offered_affordances, selection_for
from kenshi_agent.config import PlanningConfig
from kenshi_agent.models import (
    CharacterState,
    ContextActionKind,
    ControlMode,
    Disposition,
    GameState,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PlanningMode,
    TelemetrySnapshot,
    UIState,
    Vec3,
    VisibleUIControl,
    WorldStateRevision,
    WorldTarget,
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
        planning_mode=PlanningMode.CONTINUOUS,
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
        "objective": "Reconcile current evidence twice.",
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
            {"selection": observe},
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
            max_plan_steps=4,
            max_actions_per_plan=8,
            max_plan_wall_seconds=90,
            max_plan_game_seconds=600,
        ),
    )

    plan = compiled.plan
    assert plan.plan_id == "plan-pc-7"
    assert plan.control_mode is ControlMode.NATIVE_ASSISTED
    assert plan.based_on_revision == observation.world_revision
    assert [step.step_id for step in plan.steps] == ["step-1", "step-2"]
    assert plan.steps[0].on_success == "step-2"
    assert plan.steps[1].on_success is None
    assert all(step.action.kind == "noop" for step in plan.steps)
    assert all(
        step.affordance is not None
        and step.affordance.affordance_id == observe["affordance_id"]
        for step in plan.steps
    )
    assert all(step.success_conditions == [] for step in plan.steps)
    assert all(step.retry_budget == 0 for step in plan.steps)
    assert plan.max_wall_seconds == 90
    assert plan.max_game_seconds == 600


def test_context_order_compiles_through_generic_target_adapter() -> None:
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
        ui=UIState(active_screen="world", modal_open=False, dialogue_open=False),
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


def test_inventory_selection_exposes_quantity_but_derives_identity_and_price() -> None:
    actor = CharacterState(
        id="actor-1",
        name="Bark",
        selected=True,
        alive=True,
        conscious=True,
        down=False,
    )
    vendor = NearbyEntity(
        id="vendor-1",
        name="Barman",
        is_animal=False,
        disposition=Disposition.NEUTRAL,
        distance=10,
        has_vendor_list=True,
        shop_inventory_owner=True,
    )
    cell = VisibleUIControl(
        label="cell 2",
        role="item",
        window="BARMAN",
        bounds=_bounds(2),
        item_name="Dried Meat",
        item_base_value=125,
        item_quantity=4,
        selected_inventory_accepts_item=True,
    )
    observation = _observation(
        capabilities=[
            "ui.visible_controls",
            "ui.tooltip",
            "ui.inventory",
            "ui.inventory_cell_item",
            "ui.inventory_cell_item_value",
            "ui.inventory_cell_item_quantity",
            "ui.inventory_cell_acceptance",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
            "nearby.shop_owners",
            "squad.basic",
            "game.money",
            "game.pause",
        ],
        ui=UIState(
            active_screen="trade",
            visible_controls=[cell],
            visible_controls_complete=True,
            selected_character_id=actor.id,
            selected_character_ids=[actor.id],
            open_inventory_windows=1,
        ),
        squad=[actor],
        nearby=[vendor],
        active_shop_trader_count=1,
    )
    selected = _selected(observation, "buy", quantity=3)

    plan = compile_plan_proposal(
        {"objective": "Buy three portions.", "steps": [{"selection": selected}]},
        observation=observation,
        context_id="pc-buy",
        planning=PlanningConfig(
            max_pointer_actions_per_plan=8,
            max_purchase_actions_per_plan=5,
        ),
    ).plan

    action = plan.steps[0].action
    assert action.kind == "purchase_item"
    assert action.item_name == "Dried Meat"
    assert action.expected_price == 125
    assert action.seller_id == vendor.id
    assert action.quantity == 3
    assert plan.risk_budget.max_pointer_actions == 3
    assert plan.risk_budget.max_purchase_actions == 3
    assert plan.risk_budget.max_spend == 375


def test_absent_stale_or_mismatched_offer_fails_closed() -> None:
    observation = _observation()
    selection = _selected(observation, "observe")
    selection["affordance_id"] = "aff-00000000000000000000"
    with pytest.raises(ValueError, match="absent"):
        compile_plan_proposal(
            {"objective": "Invent nothing.", "steps": [{"selection": selection}]},
            observation=observation,
            context_id="pc-invalid",
            planning=PlanningConfig(),
        )


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


def test_compiler_enforces_runtime_plan_ceilings() -> None:
    observation = _observation()
    step = {"selection": _selected(observation, "observe")}
    proposal = {"objective": "Too long.", "steps": [step, step]}
    with pytest.raises(ValueError, match="runtime permits"):
        compile_plan_proposal(
            proposal,
            observation=observation,
            context_id="pc-limit",
            planning=PlanningConfig(max_plan_steps=1, max_actions_per_plan=2),
        )


def test_hosted_plan_schema_has_one_selection_contract_and_no_action_union() -> None:
    schema = PlanProposal.model_json_schema()
    step = schema["$defs"]["ProposedPlanStep"]
    assert set(step["properties"]) == {"selection"}
    selection = schema["$defs"]["AffordanceSelection"]
    assert set(selection["properties"]) == {
        "affordance_id",
        "target_id",
        "parameters",
    }
    assert "anyOf" not in step["properties"]["selection"]


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
