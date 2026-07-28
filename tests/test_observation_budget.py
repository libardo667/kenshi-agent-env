from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import pytest

from kenshi_agent import observation_budget
from kenshi_agent.models import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActivePlanContext,
    CharacterState,
    ContextActionKind,
    ContinuityOperationStatus,
    ContinuityOrigin,
    ContinuityReceiptDigest,
    ControlMode,
    GameState,
    InventoryItem,
    LiveContinuousPolicy,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NativeControlState,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PlanningMode,
    SkillAction,
    SkillArgument,
    SkillSpec,
    TelemetrySnapshot,
    UIState,
    Vec3,
    VisibleUIControl,
    WorldStateRevision,
    WorldTarget,
)
from kenshi_agent.observation_budget import (
    PlannerPayloadBudgetError,
    budget_observation_payload,
    irreducible_payload,
)

_NOW = datetime(2026, 7, 23, 20, 0, tzinfo=UTC)
_SELECTED_ID = "entity-selected-" + "a" * 80
_TARGET_ID = "entity-target-" + "b" * 80
_OUTCOME_TARGET_ID = "entity-outcome-target-" + "o" * 80
_ACTIVE_COMMAND_ID = "cmd-" + "c" * 32
_T = TypeVar("_T")


def _oversized_observation(*, reverse_low_priority: bool = False) -> Observation:
    active_acknowledgement = NativeCommandAcknowledgement(
        command_id=_ACTIVE_COMMAND_ID,
        command="approach_confirmed_vendor",
        status=NativeCommandStatus.ACCEPTED,
        reason="Exact target accepted; movement is still bounded and monitored.",
        target_id=_TARGET_ID,
        selected_character_ids=[_SELECTED_ID],
        based_on_telemetry_sequence=38,
        acknowledged_at_telemetry_sequence=39,
        accepted_at_telemetry_sequence=40,
    )
    old_acknowledgement = NativeCommandAcknowledgement(
        command_id="cmd-" + "d" * 32,
        command="approach_confirmed_vendor",
        status=NativeCommandStatus.COMPLETED,
        reason="Earlier exact command completed.",
        target_id="entity-old-target",
        selected_character_ids=[_SELECTED_ID],
        based_on_telemetry_sequence=20,
        acknowledged_at_telemetry_sequence=21,
        accepted_at_telemetry_sequence=21,
        terminal_at_telemetry_sequence=25,
    )
    capabilities = [
        "game.pause",
        "game.money",
        "game.time",
        "identity.stable_handles",
        "nearby.characters",
        "nearby.roles",
        "ui.dialogue",
        "ui.dialogue.options",
        "ui.tooltip",
    ]
    controls = [
        VisibleUIControl(
            label=f"Trade option {index} — 食料",
            role="button",
            bounds=NormalizedPointerBounds(
                min_x=0.1,
                max_x=0.4,
                min_y=0.1 + index * 0.01,
                max_y=0.11 + index * 0.01,
            ),
        )
        for index in range(12)
    ]
    selected = CharacterState(
        id=_SELECTED_ID,
        name="Hep",
        selected=True,
        alive=True,
        conscious=True,
        down=False,
        hunger=2.255,
        food_items=0,
        current_goal="Hold position safely — 安全確認 " * 30,
        inventory=[
            InventoryItem(
                name=f"Inventory item {index} — 包帯 " + "x" * 80,
                quantity=index + 1,
            )
            for index in range(8)
        ],
    )
    target = NearbyEntity(
        id=_TARGET_ID,
        name="Barman",
        kind="character",
        trader_squad=True,
        has_vendor_list=True,
        is_squad_leader=True,
        has_dialogue=True,
        faction="Trade Ninjas",
        distance=14.25,
        visible=True,
        conscious=True,
    )
    outcome_target = NearbyEntity(
        id=_OUTCOME_TARGET_ID,
        name="Previously approached guard",
        kind="character",
        distance=18.0,
        visible=True,
        conscious=True,
    )
    unrelated = [
        NearbyEntity(
            id=f"entity-unrelated-{index:03d}",
            name=f"Unrelated wanderer {index} — 通行人 " + "z" * 100,
            kind="character",
            distance=100.0 + index,
            visible=False,
        )
        for index in range(30)
    ]
    warnings = [
        f"Low-priority warning {index}: " + "w" * 120 for index in range(12)
    ]

    telemetry = TelemetrySnapshot(
        protocol_version="0.5.0",
        sequence=42,
        captured_at=_NOW,
        source="semantic-budget-test",
        identity_session_id="session-budget-test",
        capabilities=_maybe_reversed(capabilities, reverse_low_priority),
        game=GameState(
            loaded=True,
            paused=True,
            speed_multiplier=0,
            money=1000,
            elapsed_minutes=2065.25,
            location_name="The Hub",
        ),
        ui=UIState(
            active_screen="dialogue",
            modal_open=True,
            dialogue_open=True,
            dialogue_target_id=_TARGET_ID,
            dialogue_options=[
                f"Dialogue option {index} — 選択肢 " + "q" * 100
                for index in range(16)
            ],
            tooltip_visible=True,
            tooltip_text="Dried Meat — 乾燥肉 " + "t" * 1000,
            visible_controls=_maybe_reversed(controls, reverse_low_priority),
            selected_character_id=_SELECTED_ID,
            selected_character_ids=[_SELECTED_ID],
            client_width=1920,
            client_height=1080,
        ),
        native_control=NativeControlState(
            available=True,
            active_command_id=_ACTIVE_COMMAND_ID,
            acknowledgements=[old_acknowledgement, active_acknowledgement],
            last_command_sequence=38,
            last_command="approach_confirmed_vendor",
            last_result="accepted",
            last_target="Barman",
            last_target_id=_TARGET_ID,
        ),
        squad=[selected],
        active_shop_trader_count=1,
        nearby_entities=_maybe_reversed(
            [target, outcome_target, *unrelated],
            reverse_low_priority,
        ),
        world_targets=[
            WorldTarget(
                id="entity-copper",
                name="Copper Resource",
                kind="natural_resource",
                position=Vec3(x=10.0, y=0.0, z=20.0),
                distance=30.0,
                context_actions=[ContextActionKind.OPERATE],
                default_task="operate_machinery",
                mining_resource_level=0.8,
            )
        ],
        warnings=_maybe_reversed(warnings, reverse_low_priority),
    )

    outcomes = [
        ActionOutcome(
            outcome_id=f"ao-{index + 1}",
            run_id="budget",
            plan_id="single-step",
            plan_version=1,
            step_id=f"step-{index}",
            recorded_at=_NOW,
            step_index=index,
            intent=f"Outcome intent {index}: " + "i" * 300,
            action=SkillAction(
                name="approach_vendor",
                args=[
                    SkillArgument(
                        name="target_id",
                        value=_OUTCOME_TARGET_ID if index == 5 else _TARGET_ID,
                    )
                ],
            ),
            executed=True,
            receipt_message=f"Receipt {index}: " + "r" * 500,
            assessment=ActionOutcomeAssessment.CHANGED,
            feedback=f"Causal feedback {index}: " + "f" * 300,
            telemetry_changes=[
                f"telemetry change {item} — 変更 " + "c" * 60 for item in range(8)
            ],
            selected_character_name="Hep",
        )
        for index in range(6)
    ]
    events = [f"event-{index:02d}: " + "e" * 250 for index in range(24)]
    skill_specs = [
        SkillSpec(
            name=f"bounded_skill_{index:02d}",
            description="Machine-enforced constraints — 制約 " + "s" * 300,
            arguments={"target_id": "Exact stable entity ID."},
            visual_precondition="The exact target and UI phase remain current.",
        )
        for index in range(16)
    ]
    memories = [
        MemoryRecord(
            memory_id=f"mem-{index:04d}",
            campaign_id="test",
            status=MemoryStatus.ACTIVE,
            created_run_id="budget-run",
            kind=MemoryKind.FACT,
            content=f"Memory {index} — 記憶 " + "m" * 400,
            salience=index / 20,
            grounding="Deterministic test grounding.",
            created_at=_NOW,
            last_delivered_at=_NOW,
        )
        for index in range(20)
    ]
    return Observation(
        run_id="budget-run",
        step_index=17,
        observed_at=_NOW,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        planning_mode=PlanningMode.CONTINUOUS,
        live_execution_policy=LiveContinuousPolicy.DIALOGUE_INTERACTION_V1,
        world_revision=WorldStateRevision(
            telemetry_sequence=42,
            frame_sequence=9,
            capability_epoch=3,
            observed_at_monotonic=1234.5,
        ),
        telemetry=telemetry,
        telemetry_stale=False,
        telemetry_age_seconds=0.125,
        screenshot_sha256="e" * 64,
        events=_maybe_reversed(events, reverse_low_priority),
        objective="Buy exactly one verified food item without losing safety.",
        active_plan=ActivePlanContext(
            plan_id="food-chain",
            plan_version=4,
            objective="Complete the exact bounded Barman chain.",
            active_step_id="approach",
            completed_step_ids=["select"],
            remaining_actions=3,
        ),
        recent_action_outcomes=outcomes,
        available_skills=_maybe_reversed(
            [item.name for item in skill_specs],
            reverse_low_priority,
        ),
        skill_specs=_maybe_reversed(skill_specs, reverse_low_priority),
        memories=_maybe_reversed(memories, reverse_low_priority),
    )


def _maybe_reversed(items: list[_T], reverse: bool) -> list[_T]:
    return list(reversed(items)) if reverse else items


def _minimum_fitting_budget(observation: Observation) -> tuple[int, str]:
    budget = 1000
    for _ in range(8):
        try:
            return budget, observation.planner_payload(max_chars=budget)
        except PlannerPayloadBudgetError as exc:
            assert exc.required_chars > budget
            budget = exc.required_chars
    raise AssertionError("irreducible planner payload size did not converge")


def _path(document: dict[str, object], path: str) -> object:
    current: object = document
    for part in path.split("."):
        assert isinstance(current, dict)
        current = current[part]
    return current


def _assert_critical_envelope(document: dict[str, object]) -> None:
    assert document["control_mode"] == "native_assisted"
    assert document["planning_mode"] == "continuous"
    assert document["live_execution_policy"] == "dialogue_interaction_v1"
    assert _path(document, "world_revision.telemetry_sequence") == 42
    assert _path(document, "world_revision.capability_epoch") == 3
    assert _path(document, "active_plan.plan_id") == "food-chain"
    assert _path(document, "active_plan.plan_version") == 4
    assert _path(document, "active_plan.active_step_id") == "approach"
    assert _path(document, "telemetry.sequence") == 42
    assert _path(document, "telemetry.game.paused") is True
    assert _path(document, "telemetry.native_control.active_command_id") == (
        _ACTIVE_COMMAND_ID
    )
    assert _path(document, "telemetry.native_control.last_target_id") == _TARGET_ID

    acknowledgements = _path(
        document,
        "telemetry.native_control.acknowledgements",
    )
    assert isinstance(acknowledgements, list)
    assert any(item["command_id"] == _ACTIVE_COMMAND_ID for item in acknowledgements)

    squad = _path(document, "telemetry.squad")
    assert isinstance(squad, list)
    assert [item["id"] for item in squad] == [_SELECTED_ID]
    nearby = _path(document, "telemetry.nearby_entities")
    assert isinstance(nearby, list)
    assert {item["id"] for item in nearby} == {_TARGET_ID, _OUTCOME_TARGET_ID}
    context_targets = document["context_targets"]
    assert isinstance(context_targets, list)
    assert context_targets == [
        {
            "id": "entity-copper",
            "name": "Copper Resource",
            "kind": "natural_resource",
            "distance": 30.0,
            "context_actions": ["operate"],
            "mining_resource_level": 0.8,
        }
    ]

    outcomes = document["recent_action_outcomes"]
    assert isinstance(outcomes, list)
    assert outcomes[-1]["step_index"] == 5
    assert outcomes[-1]["action"]["args"][0]["value"] == _OUTCOME_TARGET_ID


def test_semantic_budget_preserves_critical_fields_and_reports_omissions() -> None:
    observation = _oversized_observation()
    budget, payload = _minimum_fitting_budget(observation)
    document = json.loads(payload)

    assert len(payload) <= budget
    _assert_critical_envelope(document)
    metadata = document["observation_budget"]
    assert metadata["truncated"] is True
    assert metadata["strategy"] == "semantic-v1"
    assert metadata["max_chars"] == budget
    assert metadata["original_chars"] > budget

    counts = metadata["omitted"]["collections"]
    assert counts["events"] == {"original": 24, "retained": 0}
    assert counts["memories"] == {"original": 20, "retained": 0}
    assert counts["telemetry.nearby_entities"] == {
        "original": 32,
        "retained": 2,
    }
    for path, count in counts.items():
        retained = _path(document, path)
        assert isinstance(retained, list)
        assert count["retained"] == len(retained)
        assert count["original"] >= count["retained"]


def test_semantic_budget_never_discards_a_current_target_memory() -> None:
    observation = _oversized_observation()
    target_memory = MemoryRecord(
        memory_id="mem-0100",
        campaign_id="test",
        status=MemoryStatus.ACTIVE,
        created_run_id="budget-run",
        kind=MemoryKind.FACT,
        content="This exact barman has no affordable work.",
        salience=0.0,
        grounding="Earlier dialogue reached its terminal unaffordable branch.",
        target_id=_TARGET_ID,
        created_at=_NOW,
        last_delivered_at=_NOW,
    )
    observation = observation.model_copy(
        update={"memories": [target_memory, *observation.memories]}
    )

    budget, payload = _minimum_fitting_budget(observation)
    document = json.loads(payload)

    assert len(payload) <= budget
    assert [
        (memory["target_id"], memory["content"]) for memory in document["memories"]
    ] == [(_TARGET_ID, target_memory.content)]


@pytest.mark.parametrize(
    "adverse_status",
    [
        ContinuityOperationStatus.REJECTED,
        ContinuityOperationStatus.FAILED,
    ],
)
def test_semantic_budget_preserves_the_latest_adverse_continuity_receipt(
    adverse_status: ContinuityOperationStatus,
) -> None:
    receipts = [
        ContinuityReceiptDigest(
            receipt_id=f"cor-{index:032x}",
            origin=ContinuityOrigin.PLAN,
            operation="keep",
            status=status,
            reason=reason,
            authored_context_id="pc-1",
            authored_revision=WorldStateRevision(telemetry_sequence=index),
            commit_revision=WorldStateRevision(telemetry_sequence=index),
            recorded_at=_NOW + timedelta(seconds=index),
        )
        for index, status, reason in (
            (1, adverse_status, "The continuity operation needs correction."),
            (2, ContinuityOperationStatus.ACCEPTED, "A commitment was kept."),
            (3, ContinuityOperationStatus.ACCEPTED, "A hypothesis was kept."),
        )
    ]
    observation = _oversized_observation().model_copy(
        update={"recent_continuity_receipts": receipts}
    )

    _, payload = _minimum_fitting_budget(observation)
    document = json.loads(payload)

    assert [
        (receipt["receipt_id"], receipt["status"])
        for receipt in document["recent_continuity_receipts"]
    ] == [(receipts[0].receipt_id, adverse_status.value)]
    assert document["observation_budget"]["omitted"]["collections"][
        "recent_continuity_receipts"
    ] == {"original": 3, "retained": 1}


def test_semantic_budget_never_hides_a_quarantined_continuity_store() -> None:
    observation = _oversized_observation().model_copy(
        update={
            "continuity_reads_degraded_reason": "Recall is quarantined.",
            "continuity_writes_degraded_reason": "Writes are quarantined.",
        }
    )

    _, payload = _minimum_fitting_budget(observation)
    document = json.loads(payload)

    assert document["continuity_reads_degraded_reason"] == "Recall is quarantined."
    assert document["continuity_writes_degraded_reason"] == "Writes are quarantined."


def test_semantic_budget_is_valid_json_across_tight_budgets() -> None:
    observation = _oversized_observation()
    minimum, _ = _minimum_fitting_budget(observation)

    for budget in (minimum, minimum + 1, minimum + 37, minimum + 500, minimum + 2500):
        payload = observation.planner_payload(max_chars=budget)
        document = json.loads(payload)
        assert len(payload) <= budget
        _assert_critical_envelope(document)

    # Relative to the envelope rather than absolute: the irreducible payload
    # grows whenever a new preserved digest is added, and a hardcoded floor here
    # rots into a failure about a number rather than about behaviour.
    for headroom in (0, 6000, 12000, 18000):
        document = json.loads(observation.planner_payload(max_chars=minimum + headroom))
        available = set(document["available_skills"])
        specified = {item["name"] for item in document["skill_specs"]}
        assert available == specified


def test_semantic_budget_rejects_budget_below_irreducible_envelope() -> None:
    observation = _oversized_observation()

    for budget in (0, 1, 100, 1000):
        with pytest.raises(PlannerPayloadBudgetError) as raised:
            observation.planner_payload(max_chars=budget)

        assert raised.value.max_chars == budget
        assert raised.value.required_chars > raised.value.max_chars
        assert "irreducible safety envelope" in str(raised.value)


def test_low_priority_reordering_does_not_change_budgeted_payload() -> None:
    original = _oversized_observation()
    reordered = _oversized_observation(reverse_low_priority=True)
    minimum, _ = _minimum_fitting_budget(original)
    budget = minimum + 1500

    assert original.planner_payload(max_chars=budget) == reordered.planner_payload(
        max_chars=budget
    )


def test_full_payload_keeps_original_contract_when_it_fits() -> None:
    observation = Observation(
        run_id="small",
        step_index=0,
        observed_at=_NOW,
        mode="mock",
        telemetry=TelemetrySnapshot(captured_at=_NOW),
    )

    payload = observation.planner_payload(max_chars=24000)
    document = json.loads(payload)

    assert "observation_budget" not in document
    assert document["run_id"] == "small"


def _canonical_counter(values: list[object]) -> Counter[str]:
    return Counter(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        for value in values
    )


def _assert_semantic_conservation(original: object, retained: object) -> None:
    """Every value survives when the semantic reducer has room for everything."""

    if isinstance(original, dict):
        assert isinstance(retained, dict)
        assert retained.keys() == original.keys()
        for key, value in original.items():
            _assert_semantic_conservation(value, retained[key])
        return
    if isinstance(original, list):
        assert isinstance(retained, list)
        assert _canonical_counter(retained) == _canonical_counter(original)
        return
    assert retained == original


def test_semantic_reduction_conserves_every_value_when_everything_fits() -> None:
    observation = _oversized_observation(reverse_low_priority=True)
    original = json.loads(observation.planner_payload(max_chars=1_000_000))
    original["recent_plan_outcomes"] = [
        {"plan_outcome_id": "po-1", "objective": "Earlier objective."},
        {"plan_outcome_id": "po-2", "objective": "Middle objective."},
        {"plan_outcome_id": "po-3", "objective": "Current objective."},
    ]
    original["recent_continuity_receipts"] = [
        {"receipt_id": "cor-1", "status": "rejected"},
        {"receipt_id": "cor-2", "status": "accepted"},
        {"receipt_id": "cor-3", "status": "failed"},
    ]
    original["telemetry"]["camera"] = {
        "mode": "follow",
        "pitch": 0.25,
    }
    original["telemetry"]["ui"]["tooltip_source_bounds"] = {
        "min_x": 0.1,
        "max_x": 0.2,
        "min_y": 0.3,
        "max_y": 0.4,
    }
    original["telemetry"]["squad"].extend(
        [
            {"id": "squad-z", "selected": False},
            {"id": "squad-a", "selected": False},
        ]
    )
    original["available_skills"].append("skill_without_spec")
    original["skill_specs"].extend(
        [
            {
                "name": "orphan_skill",
                "description": "z orphan",
                "arguments": {},
                "visual_precondition": None,
            },
            {
                "name": "bounded_skill_00",
                "description": "z duplicate",
                "arguments": {},
                "visual_precondition": None,
            },
            {
                "name": "bounded_skill_00",
                "description": "a duplicate",
                "arguments": {},
                "visual_precondition": None,
            },
        ]
    )
    original["memories"].extend(
        [
            {
                "memory_id": "mem-critical",
                "kind": "commitment",
                "target_id": None,
                "salience": 0.85,
                "created_at": "2026-07-28T00:00:00Z",
            },
            {
                "memory_id": "mem-current-target",
                "kind": "fact",
                "target_id": _TARGET_ID,
                "salience": 0.05,
                "created_at": "2026-07-28T00:00:01Z",
            },
        ]
    )
    original["telemetry"]["native_control"]["last_target_id"] = "world-ref-z"
    active_acknowledgement = original["telemetry"]["native_control"][
        "acknowledgements"
    ][-1]
    active_acknowledgement["target_id"] = "world-ref-a"
    active_acknowledgement["acknowledged_at_telemetry_sequence"] = 1
    latest_acknowledgement = deepcopy(active_acknowledgement)
    latest_acknowledgement.update(
        {
            "command_id": "cmd-" + "f" * 32,
            "target_id": "world-ref-z",
            "acknowledged_at_telemetry_sequence": 50,
        }
    )
    original["telemetry"]["native_control"]["acknowledgements"].append(
        latest_acknowledgement
    )
    original["telemetry"]["world_targets"].extend(
        [
            {"id": "world-unrelated-z", "distance": 9.0},
            {"id": "world-ref-z", "distance": 8.0},
            {"id": "world-unrelated-a", "distance": 7.0},
            {"id": "world-ref-a", "distance": 6.0},
        ]
    )

    reduced = json.loads(
        budget_observation_payload(
            original,
            full_text="x" * 1_000_001,
            max_chars=1_000_000,
        )
    )
    metadata = reduced.pop("observation_budget")

    assert metadata == {
        "truncated": True,
        "strategy": "semantic-v1",
        "max_chars": 1_000_000,
        "original_chars": 1_000_001,
        "omitted": {"collections": {}, "fields": []},
    }
    _assert_semantic_conservation(original, reduced)

    assert reduced["events"] == sorted(original["events"])
    assert reduced["recent_action_outcomes"] == original["recent_action_outcomes"]
    assert reduced["recent_plan_outcomes"] == original["recent_plan_outcomes"]
    assert (
        reduced["recent_continuity_receipts"]
        == original["recent_continuity_receipts"]
    )
    assert reduced["available_skills"] == sorted(original["available_skills"])
    assert reduced["skill_specs"] == sorted(
        original["skill_specs"],
        key=lambda item: (
            item["name"],
            json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        ),
    )
    current_target_ids = {
        item["id"]
        for collection_name in ("squad", "nearby_entities", "world_targets")
        for item in original["telemetry"][collection_name]
    }
    critical_memories = [
        item
        for item in original["memories"]
        if item["kind"] == "commitment"
        or item.get("target_id") in current_target_ids
    ]
    optional_memories = [
        item for item in original["memories"] if item not in critical_memories
    ]
    def memory_key(item: dict[str, object]) -> tuple[float, str, str]:
        return (
            float(item["salience"]),  # type: ignore[arg-type]
            str(item["created_at"]),
            str(item["memory_id"]),
        )
    assert reduced["memories"] == [
        *sorted(critical_memories, key=memory_key, reverse=True),
        *sorted(optional_memories, key=memory_key, reverse=True),
    ]
    assert reduced["telemetry"]["squad"] == [
        *sorted(
            (
                item
                for item in original["telemetry"]["squad"]
                if item["selected"]
                or item["id"]
                in original["telemetry"]["ui"]["selected_character_ids"]
            ),
            key=lambda item: (
                str(item["id"]),
                json.dumps(
                    item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        ),
        *sorted(
            (
                item
                for item in original["telemetry"]["squad"]
                if not item["selected"]
                and item["id"]
                not in original["telemetry"]["ui"]["selected_character_ids"]
            ),
            key=lambda item: (
                str(item["id"]),
                json.dumps(
                    item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        ),
    ]
    referenced_world_ids = {"world-ref-a", "world-ref-z"}
    def world_key(item: dict[str, object]) -> tuple[float, str, str]:
        return (
            float(item["distance"]),  # type: ignore[arg-type]
            str(item["id"]),
            json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def entity_key(item: dict[str, object]) -> tuple[str, str]:
        return (
            str(item["id"]),
            json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    assert reduced["telemetry"]["world_targets"] == [
        *sorted(
            (
                item
                for item in original["telemetry"]["world_targets"]
                if item["id"] in referenced_world_ids
            ),
            key=entity_key,
        ),
        *sorted(
            (
                item
                for item in original["telemetry"]["world_targets"]
                if item["id"] not in referenced_world_ids
            ),
            key=world_key,
        ),
    ]
    assert reduced["telemetry"]["capabilities"] == sorted(
        original["telemetry"]["capabilities"]
    )
    assert reduced["telemetry"]["warnings"] == sorted(
        original["telemetry"]["warnings"]
    )
    assert reduced["telemetry"]["ui"]["visible_controls"] == sorted(
        original["telemetry"]["ui"]["visible_controls"],
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    assert [
        item["command_id"]
        for item in reduced["telemetry"]["native_control"]["acknowledgements"]
    ] == [_ACTIVE_COMMAND_ID, "cmd-" + "f" * 32, "cmd-" + "d" * 32]


def test_irreducible_selection_is_exact_ordered_and_detached() -> None:
    original = json.loads(
        _oversized_observation().planner_payload(max_chars=1_000_000)
    )
    original["recent_action_outcomes"] = [
        {
            "outcome_id": "ao-current",
            "action": {
                "kind": "skill",
                "args": [{"name": "target_id", "value": "near-outcome"}],
            },
        }
    ]
    original["recent_plan_outcomes"] = [
        {"plan_outcome_id": "po-first"},
        {"plan_outcome_id": "po-middle"},
        {"plan_outcome_id": "po-last"},
    ]
    original["recent_continuity_receipts"] = [
        {"receipt_id": "cor-first", "status": "accepted"},
        {"receipt_id": "cor-middle", "status": "accepted"},
        {"receipt_id": "cor-last", "status": "accepted"},
    ]
    original["memories"] = [
        {
            "memory_id": "mem-low",
            "kind": "commitment",
            "target_id": None,
            "salience": 0.2,
            "created_at": "2026-07-28T00:00:00Z",
        },
        {
            "memory_id": "mem-high",
            "kind": "commitment",
            "target_id": None,
            "salience": 0.9,
            "created_at": "2026-07-28T00:00:01Z",
        },
        {
            "memory_id": "mem-current-target",
            "kind": "fact",
            "target_id": "near-dialogue",
            "salience": 0.1,
            "created_at": "2026-07-28T00:00:02Z",
        },
        {
            "memory_id": "mem-absent-target",
            "kind": "fact",
            "target_id": "not-current",
            "salience": 1.0,
            "created_at": "2026-07-28T00:00:03Z",
        },
    ]
    telemetry = original["telemetry"]
    telemetry["ui"]["selected_character_ids"] = ["selected-list"]
    telemetry["ui"]["selected_character_id"] = "selected-single"
    telemetry["ui"]["dialogue_target_id"] = "near-dialogue"
    telemetry["native_control"].update(
        {
            "active_command_id": "cmd-active",
            "last_target_id": "near-last",
            "acknowledgements": [
                {
                    "command_id": "cmd-latest",
                    "acknowledged_at_telemetry_sequence": 5,
                    "selected_character_ids": ["selected-latest-ack"],
                    "target_id": "world-latest",
                },
                {
                    "command_id": "cmd-active",
                    "acknowledged_at_telemetry_sequence": 2,
                    "selected_character_ids": ["selected-active-ack"],
                    "target_id": "world-active",
                },
                {
                    "command_id": "cmd-old",
                    "acknowledged_at_telemetry_sequence": 1,
                    "selected_character_ids": ["selected-old-ack"],
                    "target_id": "near-old",
                },
            ],
        }
    )
    selected_ids = [
        "selected-list",
        "selected-single",
        "selected-latest-ack",
        "selected-active-ack",
        "selected-flag",
    ]
    telemetry["squad"] = [
        {"id": "unselected", "selected": False},
        *[
            {"id": character_id, "selected": character_id == "selected-flag"}
            for character_id in reversed(selected_ids)
        ],
    ]
    referenced_nearby = [
        "near-dialogue",
        "near-last",
        "near-outcome",
    ]
    telemetry["nearby_entities"] = [
        {"id": "near-unrelated", "distance": 1.0},
        *[
            {"id": entity_id, "distance": float(index + 2)}
            for index, entity_id in enumerate(reversed(referenced_nearby))
        ],
    ]
    telemetry["world_targets"] = [
        {"id": "world-unrelated", "distance": 1.0},
        {"id": "world-latest", "distance": 3.0},
        {"id": "world-active", "distance": 2.0},
    ]

    retained = irreducible_payload(original)

    assert retained["recent_action_outcomes"] == [
        original["recent_action_outcomes"][-1]
    ]
    assert retained["recent_plan_outcomes"] == [
        original["recent_plan_outcomes"][-1]
    ]
    assert retained["recent_continuity_receipts"] == [
        original["recent_continuity_receipts"][-1]
    ]
    assert [item["memory_id"] for item in retained["memories"]] == [
        "mem-high",
        "mem-low",
        "mem-current-target",
    ]
    assert [
        item["command_id"]
        for item in retained["telemetry"]["native_control"]["acknowledgements"]
    ] == ["cmd-active", "cmd-latest"]
    assert [item["id"] for item in retained["telemetry"]["squad"]] == sorted(
        selected_ids
    )
    assert [
        item["id"] for item in retained["telemetry"]["nearby_entities"]
    ] == sorted(referenced_nearby)
    assert [
        item["id"] for item in retained["telemetry"]["world_targets"]
    ] == ["world-active", "world-latest"]
    assert retained["telemetry"]["capabilities"] == []
    assert "camera" not in retained["telemetry"]
    assert retained["telemetry"]["warnings"] == []
    assert retained["telemetry"]["ui"]["dialogue_options"] == []
    assert retained["telemetry"]["ui"]["visible_controls"] == []
    assert "tooltip_text" not in retained["telemetry"]["ui"]
    assert "tooltip_source_bounds" not in retained["telemetry"]["ui"]
    assert (
        retained["telemetry"]["native_control"]["last_target_id"]
        == "near-last"
    )

    original["recent_continuity_receipts"][-1]["status"] = "failed"
    original["telemetry"]["world_targets"][-1]["distance"] = 999.0
    assert retained["recent_continuity_receipts"][0]["status"] == "accepted"
    assert retained["telemetry"]["world_targets"][0]["distance"] == 2.0

    without_memories = irreducible_payload(
        original,
        preserve_current_target_memories=False,
    )
    assert without_memories["memories"] == []
    assert irreducible_payload(
        {"events": [], "recent_action_outcomes": [], "recent_plan_outcomes": [],
         "recent_continuity_receipts": [], "available_skills": [],
         "skill_specs": [], "memories": [], "telemetry": None}
    )["telemetry"] is None


def test_target_identity_extractors_cover_each_authoritative_shape() -> None:
    payload = {
        "telemetry_stale": False,
        "telemetry": {
            "squad": [{"id": "squad-a"}, {"name": "missing-id"}, "invalid"],
            "nearby_entities": [{"id": "nearby-a"}],
            "world_targets": [{"id": "world-a"}],
            "ui": {"dialogue_target_id": "dialogue-a"},
        },
    }
    assert observation_budget._current_memory_target_ids(payload) == {
        "squad-a",
        "nearby-a",
        "world-a",
        "dialogue-a",
    }
    stale = deepcopy(payload)
    stale["telemetry_stale"] = True
    assert observation_budget._current_memory_target_ids(stale) == set()
    assert observation_budget._current_memory_target_ids({"telemetry": None}) == set()
    malformed_first_collection = deepcopy(payload)
    malformed_first_collection["telemetry"]["squad"] = None
    assert observation_budget._current_memory_target_ids(
        malformed_first_collection
    ) == {"nearby-a", "world-a", "dialogue-a"}
    no_dialogue = deepcopy(payload)
    no_dialogue["telemetry"]["ui"] = {}
    assert observation_budget._current_memory_target_ids(no_dialogue) == {
        "squad-a",
        "nearby-a",
        "world-a",
    }

    assert observation_budget._outcome_target_ids(
        {"action": {"kind": "click", "target_id": "semantic-a"}}
    ) == {"semantic-a"}
    assert observation_budget._outcome_target_ids(
        {
            "action": {
                "kind": "skill",
                "target_id": "semantic-a",
                "args": [
                    {"name": "target_id", "value": "argument-a"},
                    {"name": "other", "value": "ignored"},
                    {"name": "target_id", "value": 3},
                    "invalid",
                ],
            }
        }
    ) == {"semantic-a", "argument-a"}
    assert observation_budget._outcome_target_ids({"action": None}) == set()
    assert observation_budget._outcome_target_ids(
        {"action": {"kind": "skill", "args": None}}
    ) == set()
    assert observation_budget._outcome_target_ids(
        {"action": {"kind": "click", "target_id": 3}}
    ) == set()


def test_omission_helpers_report_exact_paths_counts_and_meaning() -> None:
    original = {
        "events": ["one", "two"],
        "telemetry": {
            "warnings": ["warning"],
            "ui": {
                "tooltip_text": "Visible",
                "empty_text": "",
                "false_value": False,
                "zero_value": 0,
                "nested": {"present": "yes", "empty": None},
            },
        },
    }
    retained = {
        "events": ["one"],
        "telemetry": {
            "warnings": [],
            "ui": {
                "empty_text": "",
                "false_value": False,
                "zero_value": 0,
                "nested": {"empty": None},
            },
        },
    }

    assert observation_budget._omission_metadata(original, retained) == {
        "collections": {
            "events": {"original": 2, "retained": 1},
            "telemetry.warnings": {"original": 1, "retained": 0},
        },
        "fields": [
            "telemetry.ui.nested.present",
            "telemetry.ui.tooltip_text",
        ],
    }
    assert observation_budget._omitted_field_paths(original, retained) == {
        "telemetry.ui.nested.present",
        "telemetry.ui.tooltip_text",
    }
    assert observation_budget._omission_metadata(
        {"events": ["one"]},
        {"events": None},
    ) == {
        "collections": {"events": {"original": 1, "retained": 0}},
        "fields": [],
    }
    assert observation_budget._omitted_field_paths(
        {"first": {"value": "one"}, "second": {"value": "two"}},
        {"first": {}, "second": {}},
    ) == {"first.value", "second.value"}
    for empty in (None, "", [], {}):
        assert not observation_budget._has_meaningful_value(empty)
    for meaningful in (False, 0, "value", [0], {"nested": 0}):
        assert observation_budget._has_meaningful_value(meaningful)


def test_budget_primitives_preserve_values_and_define_stable_priority() -> None:
    document = {"outer": {"items": [{"id": "existing"}], "value": "old"}}
    source = {"nested": ["value"]}
    observation_budget._set_path(document, "outer.value", source)
    observation_budget._append_path(document, "outer.items", source)
    observation_budget._prepend_path(document, "outer.items", {"id": "first"})
    source["nested"].append("later")

    assert observation_budget._get_path(document, "outer.value") == {
        "nested": ["value"]
    }
    assert observation_budget._get_path(document, "outer.missing") is None
    assert document["outer"]["items"] == [
        {"id": "first"},
        {"id": "existing"},
        {"nested": ["value"]},
    ]
    with pytest.raises(TypeError, match="not a retained collection"):
        observation_budget._append_path(document, "outer.value", "invalid")
    with pytest.raises(TypeError, match="not a retained collection"):
        observation_budget._prepend_path(document, "outer.value", "invalid")

    candidate = {"items": [], "names": [], "specs": []}
    observation_budget._prepend_mutator("items", {"id": "later"})(candidate)
    observation_budget._append_mutator("items", {"id": "last"})(candidate)
    observation_budget._set_mutator("names", ["replaced"])(candidate)
    observation_budget._skill_contract_mutator(
        "bounded",
        [{"name": "bounded", "description": "exact"}],
    )({"available_skills": candidate["names"], "skill_specs": candidate["specs"]})
    assert candidate == {
        "items": [{"id": "later"}, {"id": "last"}],
        "names": ["replaced", "bounded"],
        "specs": [{"name": "bounded", "description": "exact"}],
    }

    assert observation_budget._canonical_json({"β": 2, "a": 1}) == (
        '{"a":1,"β":2}'
    )
    assert observation_budget._entity_sort_key({"id": "b", "value": 1}) == (
        "b",
        '{"id":"b","value":1}',
    )
    assert observation_budget._nearby_sort_key({"id": "b", "distance": None}) == (
        float("inf"),
        "b",
        '{"distance":null,"id":"b"}',
    )
    assert observation_budget._world_target_sort_key(
        {"id": "b", "distance": 2}
    ) == (2.0, "b", '{"distance":2,"id":"b"}')
    assert observation_budget._acknowledgement_sort_key(
        {"command_id": "cmd-b", "acknowledged_at_telemetry_sequence": 4}
    ) == (4, "cmd-b")
    assert observation_budget._memory_sort_key(
        {"salience": 0.5, "created_at": "2026", "memory_id": "mem-b"}
    ) == (0.5, "2026", "mem-b")
    assert observation_budget._nearby_sort_key(
        {"id": "a", "distance": 2.5}
    ) == (2.5, "a", '{"distance":2.5,"id":"a"}')
    assert observation_budget._critical_acknowledgements(
        {
            "active_command_id": "cmd-active",
            "acknowledgements": [
                {
                    "command_id": "cmd-latest",
                    "acknowledged_at_telemetry_sequence": 5,
                },
                {
                    "command_id": "cmd-active",
                    "acknowledged_at_telemetry_sequence": 2,
                },
                {
                    "command_id": "cmd-old",
                    "acknowledged_at_telemetry_sequence": 1,
                },
            ],
        }
    ) == [
        {
            "command_id": "cmd-active",
            "acknowledged_at_telemetry_sequence": 2,
        },
        {
            "command_id": "cmd-latest",
            "acknowledged_at_telemetry_sequence": 5,
        },
    ]
    assert observation_budget._decision_critical(
        {"kind": "commitment", "target_id": None},
        set(),
    )
    assert observation_budget._decision_critical(
        {"kind": "fact", "target_id": "entity-a"},
        {"entity-a"},
    )
    assert not observation_budget._decision_critical(
        {"kind": "fact", "target_id": "entity-b"},
        {"entity-a"},
    )


def test_budget_error_reports_the_exact_unsatisfied_envelope() -> None:
    error = PlannerPayloadBudgetError(max_chars=10, required_chars=25)

    assert error.max_chars == 10
    assert error.required_chars == 25
    assert str(error) == (
        "Planner observation budget is too small for the irreducible safety "
        "envelope: max_chars=10, required_chars=25"
    )


def test_budget_boundaries_are_inclusive_for_full_and_semantic_payloads() -> None:
    optional_event = "one optional event " + "x" * 1000
    minimal = {
        "events": [optional_event],
        "recent_action_outcomes": [],
        "recent_plan_outcomes": [],
        "recent_continuity_receipts": [],
        "available_skills": [],
        "skill_specs": [],
        "memories": [],
        "telemetry": None,
    }
    full_text = json.dumps(minimal, ensure_ascii=False)
    assert budget_observation_payload(
        minimal,
        full_text=full_text,
        max_chars=len(full_text),
    ) == full_text

    candidate = irreducible_payload(minimal)
    candidate["events"] = [optional_event]
    boundary = 1000
    for _ in range(8):
        boundary = len(
            observation_budget._serialize_budgeted(
                minimal,
                candidate,
                max_chars=boundary,
                original_chars=10_000,
            )
        )
    candidate_text = observation_budget._serialize_budgeted(
        minimal,
        candidate,
        max_chars=boundary,
        original_chars=10_000,
    )
    assert len(candidate_text) == boundary

    reduced = json.loads(
        budget_observation_payload(
            minimal,
            full_text="x" * 10_000,
            max_chars=boundary,
        )
    )
    assert reduced["events"] == [optional_event]
