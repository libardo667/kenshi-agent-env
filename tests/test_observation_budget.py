from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TypeVar

import pytest

from kenshi_agent.models import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActivePlanContext,
    CharacterState,
    ControlMode,
    GameState,
    InventoryItem,
    LiveContinuousPolicy,
    MemoryKind,
    MemoryRecord,
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
    VisibleUIControl,
    WorldStateRevision,
)
from kenshi_agent.observation_budget import PlannerPayloadBudgetError

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
        hunger=225.5,
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
        warnings=_maybe_reversed(warnings, reverse_low_priority),
    )

    outcomes = [
        ActionOutcome(
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
            id=index,
            namespace="test",
            run_id="budget-run",
            kind=MemoryKind.FACT,
            content=f"Memory {index} — 記憶 " + "m" * 400,
            salience=index / 20,
            evidence="Deterministic test evidence.",
            created_at=_NOW,
            last_accessed_at=_NOW,
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
        live_execution_policy=LiveContinuousPolicy.FOOD_PROCUREMENT_V1,
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
    assert document["live_execution_policy"] == "food_procurement_v1"
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


def test_semantic_budget_is_valid_json_across_tight_budgets() -> None:
    observation = _oversized_observation()
    minimum, _ = _minimum_fitting_budget(observation)

    for budget in (minimum, minimum + 1, minimum + 37, minimum + 500, minimum + 2500):
        payload = observation.planner_payload(max_chars=budget)
        document = json.loads(payload)
        assert len(payload) <= budget
        _assert_critical_envelope(document)

    for budget in (12000, 18000, 24000, 30000):
        document = json.loads(observation.planner_payload(max_chars=budget))
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
