from __future__ import annotations

import pytest

from kenshi_agent.config import PlanningConfig
from kenshi_agent.models import (
    ControlMode,
    IdempotencyPolicy,
    Observation,
    PlanningMode,
    TelemetrySnapshot,
    UIState,
    WorldStateRevision,
)
from kenshi_agent.planners.plan_proposal import compile_plan_proposal


def test_runtime_compiles_model_intent_and_quarantines_bad_sidecars() -> None:
    observation = Observation(
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
        telemetry=TelemetrySnapshot(ui=UIState(active_screen="world")),
    )
    proposal = {
        "objective": "Observe twice without pretending envelope bookkeeping is strategy.",
        "steps": [
            {
                "step_id": "model-authored-id",
                "action": {"kind": "noop", "reason": "Reconcile current evidence."},
                "preconditions": [],
                "success_conditions": [],
                "failure_conditions": [],
                "timeout_seconds": 0,
                "retry_budget": 2,
                "idempotency": "at_most_once",
                "on_success": "missing-step",
            },
            {
                "action": {"kind": "wait", "seconds": 0.5},
                "expected_outcomes": [],
            },
        ],
        "schema_version": "invented",
        "plan_id": "model-plan",
        "control_mode": "interface_only",
        "based_on_revision": {"telemetry_sequence": 1},
        "max_actions": 1,
        "max_wall_seconds": 0,
        "risk_budget": {
            "max_pointer_actions": 32,
            "max_purchase_actions": 8,
            "max_native_assisted_actions": 8,
        },
        "continuity_operations": [
            {
                "operation": "resolve",
                "memory_id": "mem-stale",
                "reason": "The completed plan settled this commitment.",
                "disposition": "completed",
                "references": [
                    {"source": "plan_outcome", "outcome_id": "po-16"},
                ],
            },
            {
                "operation": "reinforce",
                "memory_id": None,
                "salience": 0.9,
                "evidence_ids": [],
            },
        ],
        "fieldbook_operations": [],
    }
    planning = PlanningConfig(
        max_plan_steps=4,
        max_actions_per_plan=8,
        max_plan_wall_seconds=90,
        max_plan_game_seconds=600,
    )

    compiled = compile_plan_proposal(
        proposal,
        observation=observation,
        context_id="pc-7",
        planning=planning,
    )

    plan = compiled.plan
    assert plan.schema_version == "1.0"
    assert plan.plan_id == "plan-pc-7"
    assert plan.plan_version == 1
    assert plan.control_mode is ControlMode.NATIVE_ASSISTED
    assert plan.based_on_revision == observation.world_revision
    assert plan.max_actions == 2
    assert plan.max_wall_seconds == 90
    assert plan.max_game_seconds == 600
    assert [step.step_id for step in plan.steps] == ["step-1", "step-2"]
    assert plan.steps[0].on_success == "step-2"
    assert plan.steps[1].on_success is None
    assert all(step.retry_budget == 0 for step in plan.steps)
    assert all(step.idempotency is IdempotencyPolicy.AT_MOST_ONCE for step in plan.steps)
    assert all(
        [condition.kind.value for condition in step.preconditions] == ["telemetry_fresh"]
        for step in plan.steps
    )
    assert all(
        step.preconditions[0].max_age_seconds == 3.0
        for step in plan.steps
    )
    assert plan.risk_budget.max_pointer_actions == 0
    assert plan.risk_budget.max_purchase_actions == 0
    assert plan.risk_budget.max_native_assisted_actions == 0

    assert len(plan.continuity_operations) == 1
    resolved = plan.continuity_operations[0]
    assert resolved.operation == "resolve"
    assert resolved.references[0].source == "plan_outcome"
    assert resolved.references[0].plan_outcome_id == "po-16"
    assert len(compiled.rejected_sidecars) == 1
    assert compiled.rejected_sidecars[0].surface == "continuity_operations"
    assert compiled.rejected_sidecars[0].index == 1


def test_runtime_derives_contract_risk_spend_and_completion_ownership() -> None:
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(
            ui=UIState(
                active_screen="trade",
                open_inventory_windows=0,
            )
        ),
    )
    proposal = {
        "objective": "Buy three units and then use one ambiguous interface control.",
        "steps": [
            {
                "action": {
                    "kind": "purchase_item",
                    "cell_label": "cell 2",
                    "item_name": "Dried Meat",
                    "expected_price": 125,
                    "quantity": 3,
                    "window": "Barman",
                    "seller_id": "entity-vendor",
                },
                "expected_outcomes": [
                    {
                        "path": "telemetry.ui.dialogue_open",
                        "operator": "equals",
                        "expected": False,
                    }
                ],
            },
            {
                "action": {
                    "kind": "activate_visible_control",
                    "exact_label": "Goodbye.",
                    "role": "text",
                    "window": "",
                },
                "expected_outcomes": [
                    {
                        "path": "target.visible",
                        "operator": "equals",
                        "expected": False,
                        "target_id": "entity-vendor",
                    }
                ],
            },
            {
                "action": {
                    "kind": "open_screen",
                    "screen": "inventory",
                },
                "expected_outcomes": [
                    {
                        "path": "target.visible",
                        "operator": "equals",
                        "expected": False,
                        "target_id": "entity-vendor",
                    }
                ],
            },
        ],
        "continuity_operations": [],
        "fieldbook_operations": [],
    }

    plan = compile_plan_proposal(
        proposal,
        observation=observation,
        context_id="pc-8",
        planning=PlanningConfig(
            max_plan_steps=4,
            max_actions_per_plan=8,
            max_plan_wall_seconds=360,
            max_plan_game_seconds=300,
            max_pointer_actions_per_plan=8,
            max_purchase_actions_per_plan=5,
        ),
    ).plan

    assert plan.risk_budget.max_pointer_actions == 4
    assert plan.risk_budget.max_purchase_actions == 3
    assert plan.risk_budget.max_native_assisted_actions == 0
    assert plan.risk_budget.max_spend == 375
    assert plan.steps[0].success_conditions == []
    assert len(plan.steps[1].success_conditions) == 1
    expected = plan.steps[1].success_conditions[0]
    assert expected.kind.value == "field"
    assert expected.path == "target.visible"
    assert expected.operator.value == "equals"
    assert expected.expected is False
    assert expected.max_age_seconds == 3.0
    assert expected.target_id == "entity-vendor"
    assert plan.steps[2].success_conditions == []
    assert all(step.timeout_seconds == 300 for step in plan.steps)


def test_compiler_preserves_each_valid_memory_transition() -> None:
    proposal = {
        "objective": "Reconcile durable memory without spending a game action.",
        "steps": [{"action": {"kind": "noop", "reason": "Memory reconciliation."}}],
        "continuity_operations": [
            {
                "operation": "keep",
                "kind": "commitment",
                "content": "Find a safe food source.",
                "evidence_ids": ["current_observation"],
            },
            {
                "operation": "reinforce",
                "memory_id": "mem-active",
                "salience": 0.9,
                "evidence_ids": ["ao-2"],
            },
            {
                "operation": "resolve",
                "memory_id": "mem-finished",
                "reason": "The route is complete.",
                "disposition": "completed",
                "evidence_ids": ["po-3"],
            },
            {
                "operation": "supersede",
                "memory_id": "mem-old",
                "kind": "fact",
                "content": "The replacement observation is current.",
                "evidence_ids": [
                    "mem-other",
                    "advisor-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                ],
            },
            {
                "operation": "retract",
                "memory_id": "mem-wrong",
                "reason": "Contradicted by current evidence.",
                "evidence_ids": [],
            },
        ],
        "fieldbook_operations": [],
    }
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )

    compiled = compile_plan_proposal(
        proposal,
        observation=observation,
        context_id="pc-9",
        planning=PlanningConfig(),
    )

    assert [item.operation for item in compiled.plan.continuity_operations] == [
        "keep",
        "reinforce",
        "resolve",
        "supersede",
        "retract",
    ]
    keep, reinforce, resolve, supersede, retract = (
        compiled.plan.continuity_operations
    )
    assert keep.salience == 0.5
    assert keep.references[0].source == "current_observation"
    assert reinforce.salience == 0.9
    assert reinforce.references[0].outcome_id == "ao-2"
    assert resolve.disposition.value == "completed"
    assert resolve.references[0].plan_outcome_id == "po-3"
    assert [reference.source for reference in supersede.references] == [
        "memory",
        "advisor_brief",
    ]
    assert supersede.references[0].memory_id == "mem-other"
    assert (
        supersede.references[1].brief_id
        == "advisor-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert retract.reason == "Contradicted by current evidence."
    assert compiled.rejected_sidecars == ()


@pytest.mark.parametrize(
    "operation",
    [
        {
            "operation": "create_project",
            "kind": "journal",
            "title": "Journey",
            "summary": "Track the current journey.",
            "evidence_ids": [],
        },
        {
            "operation": "append_entry",
            "project_id": "fbp-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "kind": "observation",
            "content": "The route changed.",
            "evidence_ids": ["current_observation"],
        },
        {
            "operation": "update_summary",
            "project_id": "fbp-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "summary": "The route is now current.",
            "evidence_ids": [],
        },
        {
            "operation": "select_project",
            "project_id": "fbp-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "evidence_ids": [],
        },
        {
            "operation": "set_project_status",
            "project_id": "fbp-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "status": "completed",
            "evidence_ids": [],
        },
    ],
)
def test_compiler_preserves_each_valid_fieldbook_transition(
    operation: dict[str, object],
) -> None:
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )

    compiled = compile_plan_proposal(
        {
            "objective": "Maintain private journey context.",
            "steps": [{"action": {"kind": "noop", "reason": "Fieldbook update."}}],
            "continuity_operations": [],
            "fieldbook_operations": [operation],
        },
        observation=observation,
        context_id="pc-10",
        planning=PlanningConfig(),
    )

    assert len(compiled.plan.fieldbook_operations) == 1
    compiled_operation = compiled.plan.fieldbook_operations[0]
    assert compiled_operation.operation == operation["operation"]
    rendered = compiled_operation.model_dump(mode="json")
    for key, value in operation.items():
        if key == "evidence_ids":
            continue
        assert rendered[key] == value
    if operation.get("evidence_ids"):
        assert rendered["references"] == [{"source": "current_observation"}]
    assert compiled.rejected_sidecars == ()


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {"objective": None, "steps": [{"action": {"kind": "noop"}}]},
        {"objective": " ", "steps": [{"action": {"kind": "noop"}}]},
        {"objective": "Missing actions.", "steps": None},
        {"objective": "Missing actions.", "steps": []},
        {"objective": "Invalid action.", "steps": [None]},
    ],
)
def test_compiler_rejects_missing_gameplay_intent(document: object) -> None:
    with pytest.raises(ValueError):
        compile_plan_proposal(
            document,
            observation=Observation(
                run_id="proposal-contract",
                step_index=0,
                mode="live",
                planning_mode=PlanningMode.CONTINUOUS,
                telemetry=TelemetrySnapshot(),
            ),
            context_id="pc-11",
            planning=PlanningConfig(),
        )


def test_compiler_enforces_runtime_action_and_step_ceilings() -> None:
    proposal = {
        "objective": "This proposal is intentionally too long.",
        "steps": [
            {"action": {"kind": "noop", "reason": "First."}},
            {"action": {"kind": "noop", "reason": "Second."}},
        ],
    }
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )

    with pytest.raises(ValueError):
        compile_plan_proposal(
            proposal,
            observation=observation,
            context_id="pc-12",
            planning=PlanningConfig(
                max_plan_steps=1,
                max_actions_per_plan=2,
            ),
        )
    with pytest.raises(ValueError):
        compile_plan_proposal(
            proposal,
            observation=observation,
            context_id="pc-13",
            planning=PlanningConfig(
                max_plan_steps=2,
                max_actions_per_plan=1,
            ),
        )

    accepted = compile_plan_proposal(
        proposal,
        observation=observation,
        context_id="pc-13-exact",
        planning=PlanningConfig(
            max_plan_steps=2,
            max_actions_per_plan=2,
        ),
    )
    assert len(accepted.plan.steps) == 2


def test_compiler_accumulates_native_and_spend_risk_across_steps() -> None:
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )
    compiled = compile_plan_proposal(
        {
            "objective": "Retain the whole plan's cumulative authority ceiling.",
            "steps": [
                {
                    "action": {
                        "kind": "approach_dialogue_target",
                        "target_id": "entity-first",
                    }
                },
                {
                    "action": {
                        "kind": "approach_dialogue_target",
                        "target_id": "entity-second",
                    }
                },
                {
                    "action": {
                        "kind": "purchase_item",
                        "cell_label": "cell 1",
                        "item_name": "Water",
                        "expected_price": 90,
                        "quantity": 2,
                        "window": "Barman",
                        "seller_id": "entity-vendor",
                    }
                },
                {
                    "action": {
                        "kind": "purchase_item",
                        "cell_label": "cell 2",
                        "item_name": "Foodcube",
                        "expected_price": 300,
                        "quantity": 1,
                        "window": "Barman",
                        "seller_id": "entity-vendor",
                    }
                },
            ],
        },
        observation=observation,
        context_id="pc-13-risk",
        planning=PlanningConfig(
            max_plan_steps=4,
            max_actions_per_plan=4,
            max_native_assisted_actions_per_plan=4,
            max_pointer_actions_per_plan=8,
            max_purchase_actions_per_plan=4,
        ),
    )

    risk = compiled.plan.risk_budget
    assert risk.max_native_assisted_actions == 2
    assert risk.max_purchase_actions == 3
    assert risk.max_spend == 480


def test_compiler_derives_safe_retry_idempotency_from_action_contract() -> None:
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )
    compiled = compile_plan_proposal(
        {
            "objective": "Rotate once without asking the model for retry mechanics.",
            "steps": [
                {
                    "action": {
                        "kind": "rotate_camera",
                        "direction": "left",
                    }
                }
            ],
        },
        observation=observation,
        context_id="pc-13-idempotency",
        planning=PlanningConfig(),
    )

    assert (
        compiled.plan.steps[0].idempotency
        is IdempotencyPolicy.SAFE_TO_RETRY
    )


def test_compiler_reports_bad_sidecars_without_losing_gameplay() -> None:
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )
    compiled = compile_plan_proposal(
        {
            "objective": "Keep the valid no-op while quarantining sidecars.",
            "steps": [{"action": {"kind": "noop", "reason": "Still valid."}}],
            "continuity_operations": [
                {
                    "operation": "keep",
                    "kind": "fact",
                    "content": "Unsupported evidence.",
                    "evidence_ids": ["not-an-evidence-id"],
                },
                {
                    "operation": "keep",
                    "kind": "fact",
                    "content": "Unsupported legacy wrapper.",
                    "references": [{"source": "memory", "memory_id": "mem-old"}],
                },
            ],
            "fieldbook_operations": {"operation": "select_project"},
        },
        observation=observation,
        context_id="pc-14",
        planning=PlanningConfig(),
    )

    assert compiled.plan.steps[0].action.kind == "noop"
    assert compiled.plan.continuity_operations == []
    assert compiled.plan.fieldbook_operations == []
    assert [
        (item.surface, item.index)
        for item in compiled.rejected_sidecars
    ] == [
        ("continuity_operations", 0),
        ("continuity_operations", 1),
        ("fieldbook_operations", 0),
    ]
    assert "unknown evidence ID" in compiled.rejected_sidecars[0].detail
    assert "evidence must use evidence_ids" in compiled.rejected_sidecars[1].detail
    assert "must be a list" in compiled.rejected_sidecars[2].detail


def test_compiler_attributes_a_non_list_continuity_sidecar() -> None:
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )
    compiled = compile_plan_proposal(
        {
            "objective": "Retain gameplay when memory syntax is malformed.",
            "steps": [{"action": {"kind": "noop", "reason": "Reobserve."}}],
            "continuity_operations": {"operation": "keep"},
        },
        observation=observation,
        context_id="pc-14-continuity",
        planning=PlanningConfig(),
    )

    assert compiled.plan.steps[0].action.kind == "noop"
    assert len(compiled.rejected_sidecars) == 1
    rejected = compiled.rejected_sidecars[0]
    assert rejected.surface == "continuity_operations"
    assert rejected.index == 0
    assert "must be a list" in rejected.detail


def test_compiler_quarantines_invalid_items_at_their_exact_sidecar() -> None:
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )
    compiled = compile_plan_proposal(
        {
            "objective": "Keep valid sidecars beside malformed siblings.",
            "steps": [{"action": {"kind": "noop", "reason": "Reconcile."}}],
            "continuity_operations": [
                None,
                {
                    "operation": "keep",
                    "kind": "fact",
                    "content": "Valid memory.",
                },
            ],
            "fieldbook_operations": [
                None,
                {
                    "operation": "select_project",
                    "project_id": "fbp-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "ignored_model_bookkeeping": "discard me",
                },
            ],
        },
        observation=observation,
        context_id="pc-15",
        planning=PlanningConfig(),
    )

    assert len(compiled.plan.continuity_operations) == 1
    assert len(compiled.plan.fieldbook_operations) == 1
    assert [
        (item.surface, item.index)
        for item in compiled.rejected_sidecars
    ] == [
        ("continuity_operations", 0),
        ("fieldbook_operations", 0),
    ]
    assert "JSON object" in compiled.rejected_sidecars[0].detail
    assert "JSON object" in compiled.rejected_sidecars[1].detail


def test_compiler_accepts_legacy_planner_owned_success_condition() -> None:
    observation = Observation(
        run_id="proposal-contract",
        step_index=0,
        mode="live",
        planning_mode=PlanningMode.CONTINUOUS,
        telemetry=TelemetrySnapshot(),
    )
    compiled = compile_plan_proposal(
        {
            "objective": "Preserve an unambiguous effect during migration.",
            "steps": [
                {
                    "action": {
                        "kind": "activate_visible_control",
                        "exact_label": "Goodbye.",
                        "role": "text",
                        "window": "",
                    },
                    "success_conditions": [
                        {
                            "kind": "field",
                            "path": "target.visible",
                            "operator": "equals",
                            "expected": False,
                            "max_age_seconds": 99,
                            "target_id": "entity-vendor",
                        }
                    ],
                }
            ],
        },
        observation=observation,
        context_id="pc-16",
        planning=PlanningConfig(),
    )

    condition = compiled.plan.steps[0].success_conditions[0]
    assert condition.path == "target.visible"
    assert condition.max_age_seconds == 3.0
    assert condition.target_id == "entity-vendor"
