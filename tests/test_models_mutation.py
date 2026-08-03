"""Behavioral invariants for the observation and planner data contract."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from kenshi_agent.models import (
    AdvisorAvailability,
    CharacterState,
    ClickAction,
    Condition,
    ConditionKind,
    ContextActionKind,
    ContinuityOperationReceipt,
    ContinuityOperationStatus,
    ContinuityOrigin,
    ControlMode,
    Disposition,
    GameState,
    KeepMemoryOperation,
    KnownMapDestination,
    MemoryKind,
    MemoryStatus,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PerformContextAction,
    PlanningMode,
    TelemetrySnapshot,
    UIState,
    Vec3,
    VisibleUIControl,
    WorldStateRevision,
    WorldTarget,
    _nearest_first,
    budgeted_visible_controls,
    group_controls_by_window,
    is_semantic_action,
    normalize_control_label,
)

NOW = datetime(2026, 7, 28, 1, 2, 3, tzinfo=UTC)


def _assert_plain_json_values(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert type(key) is str
            _assert_plain_json_values(child)
    elif isinstance(value, list):
        for child in value:
            _assert_plain_json_values(child)
    else:
        assert type(value) in {str, int, float, bool, type(None)}


def _control(
    label: str,
    role: str,
    *,
    window: str = "",
    item_name: str | None = None,
    item_base_value: int | None = None,
    item_quantity: int | None = None,
    selected_inventory_accepts_item: bool | None = None,
    section: str = "",
) -> VisibleUIControl:
    return VisibleUIControl(
        label=label,
        role=role,
        window=window,
        bounds=NormalizedPointerBounds(
            min_x=0.1,
            max_x=0.2,
            min_y=0.3,
            max_y=0.4,
        ),
        item_name=item_name,
        item_base_value=item_base_value,
        item_quantity=item_quantity,
        selected_inventory_accepts_item=selected_inventory_accepts_item,
        section=section,
    )


def _rich_observation(*, item_control_count: int = 61) -> Observation:
    item_controls = [
        _control(
            f"cell {index}",
            "item",
            window="Vendor Stock",
            item_name=f"item-{index}",
            item_base_value=1000 + index,
            item_quantity=2000 + index,
            section=f"section-{index}",
        )
        for index in range(item_control_count)
    ]
    controls = [
        *item_controls,
        _control("Confirm", "button", window="Decision"),
    ]
    return Observation(
        run_id="rich-run",
        step_index=17,
        observed_at=NOW,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        planning_mode=PlanningMode.CONTINUOUS,
        world_revision=WorldStateRevision(
            telemetry_sequence=20,
            frame_sequence=21,
            capability_epoch=22,
            observed_at_monotonic=23.0,
        ),
        telemetry=TelemetrySnapshot(
            protocol_version="1.1.0",
            sequence=20,
            captured_at=NOW,
            source="rich-fixture",
            identity_session_id="identity-rich",
            capabilities=["ui.visible_controls"],
            game=GameState(
                loaded=True,
                paused=False,
                speed_multiplier=2.0,
                day=3,
                hour=4,
                minute=5,
                elapsed_minutes=6.5,
                money=789,
                location_name="The Hub",
            ),
            ui=UIState(
                active_screen="trade",
                modal_open=False,
                dialogue_open=True,
                dialogue_target_id="entity-dialogue",
                tooltip_visible=True,
                visible_controls=controls,
                visible_controls_complete=False,
                context_inventory_target_id="entity-machine",
                open_inventory_windows=2,
                management_screen_open=True,
                management_tab=3,
                selected_character_id="entity-hep",
                selected_character_ids=["entity-hep"],
            ),
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    in_combat=True,
                    indoors=False,
                    position=Vec3(x=1.0, y=2.0, z=3.0),
                    hunger=2.5,
                    blood=77.0,
                    bleeding_rate=0.25,
                    food_items=4,
                    inventory_complete=True,
                )
            ],
            active_shop_trader_count=5,
            nearby_entities=[
                NearbyEntity(
                    id="entity-vendor",
                    name="Vendor Stock",
                    kind="character",
                    is_animal=False,
                    has_dialogue=True,
                    has_vendor_list=True,
                    is_squad_leader=True,
                    shop_inventory_owner=True,
                    disposition=Disposition.FRIENDLY,
                    distance=12.0,
                    visible=True,
                    camera_bearing_degrees=34.0,
                ),
                NearbyEntity(
                    id="entity-other",
                    name="Other",
                    kind="character",
                    is_animal=True,
                    has_dialogue=False,
                    disposition=Disposition.HOSTILE,
                    distance=56.0,
                ),
            ],
            world_targets=[
                WorldTarget(
                    id="entity-machine",
                    name="Machine",
                    kind="natural_resource",
                    position=Vec3(x=7.0, y=8.0, z=9.0),
                    distance=10.0,
                    context_actions=[ContextActionKind.OPERATE],
                    default_task="operate_machinery",
                    mining_resource_level=0.75,
                )
            ],
            known_map_destinations=[
                KnownMapDestination(
                    id="entity-known-town",
                    name="The Hub",
                    distance=1250.0,
                )
            ],
            warnings=["fixture-warning"],
        ),
        telemetry_stale=True,
        telemetry_age_seconds=4.25,
        screenshot_sha256="a" * 64,
        events=["event-one", "event-two"],
        objective="Exercise every digest field.",
        advisor=AdvisorAvailability(
            enabled=True,
            may_request=True,
            suggested=True,
            reason="Fixture advice is available.",
            calls_used=2,
            max_calls=3,
            cooldown_steps_remaining=4,
            corpus_version="fixture-corpus",
        ),
    )


def test_continuity_receipt_digest_conserves_every_planner_field_and_bounds_evidence() -> None:
    authored = WorldStateRevision(
        telemetry_sequence=1,
        frame_sequence=2,
        capability_epoch=3,
        observed_at_monotonic=4.0,
    )
    committed = WorldStateRevision(
        telemetry_sequence=5,
        frame_sequence=6,
        capability_epoch=7,
        observed_at_monotonic=8.0,
    )
    receipt = ContinuityOperationReceipt(
        receipt_id="cor-" + "a" * 32,
        origin=ContinuityOrigin.PLAN,
        status=ContinuityOperationStatus.FAILED,
        operation=KeepMemoryOperation(
            kind=MemoryKind.FACT,
            content="A fact.",
        ),
        reason="Storage unavailable.",
        memory_id="mem-receipt",
        memory_status=MemoryStatus.ACTIVE,
        evidence="e" * 501,
        plan_id="plan-a",
        plan_version=3,
        step_id="step-a",
        authored_context_id="pc-9",
        authored_revision=authored,
        commit_revision=committed,
        writes_degraded=True,
        recorded_at=NOW,
    )

    assert receipt.digest().model_dump(mode="json") == {
        "receipt_id": "cor-" + "a" * 32,
        "origin": "plan",
        "operation": "keep",
        "status": "failed",
        "reason": "Storage unavailable.",
        "memory_id": "mem-receipt",
        "memory_status": "active",
        "authored_context_id": "pc-9",
        "authored_revision": authored.model_dump(mode="json"),
        "commit_revision": committed.model_dump(mode="json"),
        "plan_id": "plan-a",
        "plan_version": 3,
        "step_id": "step-a",
        "evidence_summary": "e" * 500,
        "writes_degraded": True,
        "recorded_at": NOW.isoformat().replace("+00:00", "Z"),
    }


@pytest.mark.parametrize(
    "field",
    [
        "continuity_reads_degraded_reason",
        "continuity_writes_degraded_reason",
    ],
)
def test_continuity_store_health_is_bounded_and_planner_visible(field: str) -> None:
    reason = "x" * 1000
    observation = Observation(
        run_id="store-health",
        step_index=0,
        observed_at=NOW,
        mode="mock",
        **{field: reason},
    )

    assert getattr(observation, field) == reason
    assert json.loads(observation.planner_payload(max_chars=100_000))[field] == reason

    with pytest.raises(ValueError):
        Observation(
            run_id="store-health",
            step_index=0,
            observed_at=NOW,
            mode="mock",
            **{field: reason + "x"},
        )


def test_current_memory_target_ids_are_exactly_fresh_current_identities() -> None:
    observation = _rich_observation(item_control_count=1)
    observation.telemetry_stale = False

    assert observation.current_memory_target_ids() == {
        "entity-hep",
        "entity-vendor",
        "entity-other",
        "entity-machine",
        "entity-dialogue",
        "entity-known-town",
    }

    observation.telemetry_stale = True
    assert observation.current_memory_target_ids() == set()
    observation.telemetry = None
    assert observation.current_memory_target_ids() == set()


def test_world_revision_order_matches_the_componentwise_partial_order() -> None:
    before = WorldStateRevision(
        telemetry_sequence=10,
        frame_sequence=20,
        capability_epoch=30,
        observed_at_monotonic=40.0,
    )

    for telemetry, frame, capability, observed_at in itertools.product(
        (None, 9, 10, 11),
        (None, 19, 20, 21),
        (29, 30, 31),
        (39.0, 40.0, 41.0),
    ):
        after = WorldStateRevision(
            telemetry_sequence=telemetry,
            frame_sequence=frame,
            capability_epoch=capability,
            observed_at_monotonic=observed_at,
        )
        telemetry_regressed = telemetry is not None and telemetry < 10
        frame_regressed = frame is not None and frame < 20
        expected = (
            not telemetry_regressed
            and not frame_regressed
            and capability >= 30
            and (telemetry == 11 or frame == 21 or capability == 31)
            and observed_at >= 40.0
        )
        assert after.is_later_than(before) is expected


def test_normalized_pointer_bounds_are_closed_on_every_edge() -> None:
    bounds = NormalizedPointerBounds(min_x=0.2, max_x=0.8, min_y=0.3, max_y=0.7)

    for x, y in itertools.product(
        (0.19, 0.2, 0.5, 0.8, 0.81),
        (0.29, 0.3, 0.5, 0.7, 0.71),
    ):
        expected = 0.2 <= x <= 0.8 and 0.3 <= y <= 0.7
        assert bounds.contains(x, y) is expected


def test_nearest_first_treats_unknown_distance_as_farther_than_every_number() -> None:
    entities = [
        NearbyEntity(id="unknown", name="Unknown", distance=None),
        NearbyEntity(id="far", name="Far", distance=100.0),
        NearbyEntity(id="near", name="Near", distance=1.0),
    ]

    assert [entity.id for entity in _nearest_first(entities)] == [
        "near",
        "far",
        "unknown",
    ]


def _expected_role_balanced_controls(
    controls: list[VisibleUIControl],
    limit: int,
) -> list[VisibleUIControl]:
    if limit <= 0:
        return []
    roles = list(dict.fromkeys(control.role for control in controls))
    buckets = {
        role: [control for control in controls if control.role == role]
        for role in roles
    }
    selected: list[VisibleUIControl] = []
    for round_index in range(len(controls)):
        for role in roles:
            if round_index < len(buckets[role]):
                selected.append(buckets[role][round_index])
                if len(selected) == min(limit, len(controls)):
                    positions = {id(control): index for index, control in enumerate(controls)}
                    return sorted(selected, key=lambda item: positions[id(item)])
    return selected


def test_visible_control_budget_is_role_balanced_bounded_and_order_preserving() -> None:
    controls = [
        _control("button-1", "button"),
        _control("button-2", "button"),
        _control("item-1", "item", item_name="Ore"),
        _control("text-1", "text"),
        _control("item-2", "item", item_name="Food"),
        _control("button-3", "button"),
    ]

    for limit in range(-1, len(controls) + 3):
        assert budgeted_visible_controls(controls, limit) == (
            _expected_role_balanced_controls(controls, limit)
        )
        assert budgeted_visible_controls([], limit) == []


def test_control_groups_preserve_missing_window_identity_and_attach_exact_owner() -> None:
    entries = [
        {"exact_label": "Unowned", "role": "button"},
        {"exact_label": "Empty", "role": "text", "window": ""},
        {"exact_label": "Stock", "role": "item", "window": "VENDOR"},
    ]
    owners = {
        "vendor": {
            "belongs_to": "vendor",
            "seller_id": "entity-vendor",
        }
    }

    assert group_controls_by_window(entries, owners) == [
        {
            "window": "",
            "controls": [
                {"exact_label": "Unowned", "role": "button"},
                {"exact_label": "Empty", "role": "text"},
            ],
        },
        {
            "window": "VENDOR",
            "belongs_to": "vendor",
            "seller_id": "entity-vendor",
            "controls": [{"exact_label": "Stock", "role": "item"}],
        },
    ]


def test_condition_constructor_accepts_direct_and_root_wrapped_payloads_identically() -> None:
    payload = {
        "kind": ConditionKind.TELEMETRY_FRESH,
        "max_age_seconds": 1.5,
    }

    direct = Condition(**payload)
    wrapped = Condition(root=payload)

    assert direct == wrapped
    assert direct.model_dump(mode="json") == {
        "kind": "telemetry_fresh",
        "operator": "equals",
        "expected": True,
        "max_age_seconds": 1.5,
    }


def test_semantic_action_classifier_separates_cognitive_from_controller_actions() -> None:
    semantic = PerformContextAction(
        target_id="entity-machine",
        context_action=ContextActionKind.OPERATE,
    )
    controller = ClickAction(x=0.5, y=0.5)

    assert is_semantic_action(semantic) is True
    assert is_semantic_action(controller) is False


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("  Vendor   Stock ", "vendor stock"),
        ("ONE\tTWO\nTHREE", "one two three"),
        ("Already-normal", "already-normal"),
    ],
)
def test_control_label_normalization_has_one_space_between_casefolded_words(
    raw: str,
    normalized: str,
) -> None:
    assert normalize_control_label(raw) == normalized


def test_open_windows_ignore_empty_captions_and_preserve_first_appearance() -> None:
    observation = _rich_observation(item_control_count=0)
    assert observation.telemetry is not None
    observation.telemetry.ui.visible_controls = [
        _control("empty", "text"),
        _control("first", "button", window="First"),
        _control("first-again", "text", window="First"),
        _control("second", "button", window="Second"),
    ]

    assert observation.open_window_captions() == ["First", "Second"]


def test_window_ownership_requires_positive_vendor_evidence_and_squad_wins() -> None:
    observation = _rich_observation(item_control_count=0)
    assert observation.telemetry is not None
    observation.telemetry.nearby_entities = [
        NearbyEntity(
            id="not-owner",
            name="Unowned",
            shop_inventory_owner=False,
        ),
        NearbyEntity(
            id="vendor",
            name="Shared",
            shop_inventory_owner=True,
        ),
    ]
    observation.telemetry.squad = [
        CharacterState(id="player", name="Shared"),
    ]

    assert observation.window_owners() == {
        "shared": {"belongs_to": "you"},
    }


def test_dialogue_digest_conserves_every_exact_target_field() -> None:
    observation = _rich_observation(item_control_count=0)

    assert observation.dialogue_target_digest() == [
        {
            "id": "entity-vendor",
            "name": "Vendor Stock",
            "distance": 12.0,
            "visible": True,
            "camera_bearing_degrees": 34.0,
            "is_vendor": True,
        }
    ]


def test_travel_digest_excludes_unaddressable_and_talkable_entities_and_caps_at_eight() -> None:
    observation = _rich_observation(item_control_count=0)
    assert observation.telemetry is not None
    observation.telemetry.nearby_entities.extend(
        [
            NearbyEntity(
                id=f"traveler-{index}",
                name=f"Traveler {index}",
                distance=None if index == 0 else float(index),
            )
            for index in range(10)
        ]
    )
    observation.telemetry.nearby_entities.extend(
        [
            NearbyEntity(id="", name="No ID", distance=1000.0),
            NearbyEntity(id="no-name", name="", distance=1001.0),
        ]
    )

    assert observation.travel_destination_digest() == [
        {"id": "entity-other", "name": "Other", "distance": 56.0},
        {"id": "traveler-9", "name": "Traveler 9", "distance": 9.0},
        {"id": "traveler-8", "name": "Traveler 8", "distance": 8.0},
        {"id": "traveler-7", "name": "Traveler 7", "distance": 7.0},
        {"id": "traveler-6", "name": "Traveler 6", "distance": 6.0},
        {"id": "traveler-5", "name": "Traveler 5", "distance": 5.0},
        {"id": "traveler-4", "name": "Traveler 4", "distance": 4.0},
        {"id": "traveler-3", "name": "Traveler 3", "distance": 3.0},
    ]


def test_travel_digest_orders_known_positive_distance_before_unknown_distance() -> None:
    observation = _rich_observation(item_control_count=0)
    assert observation.telemetry is not None
    observation.telemetry.nearby_entities = [
        NearbyEntity(id="unknown", name="Unknown", distance=None),
        NearbyEntity(id="known", name="Known", distance=0.5),
    ]

    assert observation.travel_destination_digest() == [
        {"id": "known", "name": "Known", "distance": 0.5},
        {"id": "unknown", "name": "Unknown", "distance": None},
    ]


def test_context_target_digest_is_sorted_attemptable_and_bounded() -> None:
    observation = _rich_observation(item_control_count=0)
    assert observation.telemetry is not None
    observation.telemetry.world_targets = [
        WorldTarget(
            id=f"target-{index:02}",
            name=f"Target {20 - index:02}",
            kind="machine",
            position=Vec3(x=float(index), y=0.0, z=0.0),
            distance=float(20 - index),
            context_actions=(
                [ContextActionKind.OPERATE] if index != 10 else []
            ),
            default_task="operate",
            mining_resource_level=float(index) / 20,
        )
        for index in range(20)
    ]

    expected_targets = sorted(
        (
            target
            for target in observation.telemetry.world_targets
            if target.context_actions
        ),
        key=lambda target: (target.distance, target.name, target.id),
    )[:16]
    assert observation.context_target_digest() == [
        {
            "id": target.id,
            "name": target.name,
            "kind": target.kind,
            "distance": target.distance,
            "context_actions": ["operate"],
            "mining_resource_level": target.mining_resource_level,
        }
        for target in expected_targets
    ]


def test_visible_control_digest_preserves_item_metadata_and_button_ambiguity() -> None:
    observation = _rich_observation(item_control_count=0)
    assert observation.telemetry is not None
    observation.telemetry.ui.visible_controls = [
        _control(
            "cell 1",
            "item",
            window="Stock",
            item_name="Copper",
            item_base_value=123,
            item_quantity=4,
            selected_inventory_accepts_item=False,
            section="backpack",
        ),
        _control("Same", "button", window="Choice"),
        _control("Same", "button", window="Choice"),
    ]

    assert observation.visible_control_digest() == [
        {
            "exact_label": "cell 1",
            "role": "item",
            "window": "Stock",
            "ambiguous": False,
            "item_name": "Copper",
            "buy_price": 123,
            "sell_price": None,
            "item_quantity": 4,
            "selected_inventory_accepts_item": False,
            "section": "backpack",
        },
        {
            "exact_label": "Same",
            "role": "button",
            "window": "Choice",
            "ambiguous": True,
        },
        {
            "exact_label": "Same",
            "role": "button",
            "window": "Choice",
            "ambiguous": True,
        },
    ]


def test_visible_control_digest_keeps_unnamed_item_cells_distinct() -> None:
    observation = _rich_observation(item_control_count=0)
    assert observation.telemetry is not None
    observation.telemetry.ui.visible_controls = [
        _control("cell", "item", window="Stock"),
        _control("cell", "item", window="Stock"),
    ]

    assert [
        entry["ambiguous"] for entry in observation.visible_control_digest()
    ] == [True, True]


def test_affordance_digest_is_plain_json_without_runtime_mechanics() -> None:
    observation = _rich_observation(item_control_count=0).model_copy(
        update={"telemetry_stale": False, "telemetry_age_seconds": 0.1}
    )

    digest = observation.affordance_digest()
    assert digest
    assert json.loads(json.dumps(digest)) == digest
    assert all("operation_kind" not in entry for entry in digest)
    assert all("policy" not in entry for entry in digest)
    _assert_plain_json_values(digest)


def test_log_digest_conserves_the_complete_bounded_logging_contract() -> None:
    observation = _rich_observation()
    assert observation.telemetry is not None
    telemetry = observation.telemetry

    digest = observation.log_digest()
    _assert_plain_json_values(digest)

    assert set(digest) == {
        "run_id",
        "step_index",
        "mode",
        "control_mode",
        "planning_mode",
        "world_revision",
        "telemetry_stale",
        "telemetry_age_seconds",
        "events",
        "objective",
        "advisor",
        "digest",
        "telemetry",
    }
    assert {
        key: digest[key]
        for key in set(digest) - {"telemetry"}
    } == {
        "run_id": "rich-run",
        "step_index": 17,
        "mode": "live",
        "control_mode": "native_assisted",
        "planning_mode": "continuous",
        "world_revision": observation.world_revision.model_dump(mode="json"),
        "telemetry_stale": True,
        "telemetry_age_seconds": 4.25,
        "events": ["event-one", "event-two"],
        "objective": "Exercise every digest field.",
        "advisor": observation.advisor.model_dump(mode="json"),
        "digest": True,
    }

    telemetry_digest = digest["telemetry"]
    assert isinstance(telemetry_digest, dict)
    assert set(telemetry_digest) == {
        "sequence",
        "source",
        "identity_session_id",
        "capabilities",
        "game",
        "ui",
        "native_control",
        "active_shop_trader_count",
        "nearby_entity_count",
        "dialogue_target_count",
        "world_target_count",
        "context_targets",
        "selected",
    }
    assert {
        key: telemetry_digest[key]
        for key in set(telemetry_digest) - {"game", "ui", "selected"}
    } == {
        "sequence": 20,
        "source": "rich-fixture",
        "identity_session_id": "identity-rich",
        "capabilities": ["ui.visible_controls"],
        "native_control": telemetry.native_control.model_dump(mode="json"),
        "active_shop_trader_count": 5,
        "nearby_entity_count": 2,
        "dialogue_target_count": 1,
        "world_target_count": 1,
        "context_targets": [
            {
                "id": "entity-machine",
                "name": "Machine",
                "kind": "natural_resource",
                "distance": 10.0,
                "context_actions": ["operate"],
                "mining_resource_level": 0.75,
            }
        ],
    }
    assert telemetry_digest["game"] == {
        "loaded": True,
        "paused": False,
        "money": 789,
        "elapsed_minutes": 6.5,
        "location_name": "The Hub",
    }
    assert telemetry_digest["ui"] == {
        "active_screen": "trade",
        "modal_open": False,
        "dialogue_open": True,
        "dialogue_target_id": "entity-dialogue",
        "tooltip_visible": True,
        "open_inventory_windows": 2,
        "management_screen_open": True,
        "management_tab": 3,
        "selected_character_id": "entity-hep",
        "context_inventory_target_id": "entity-machine",
        "visible_controls_complete": False,
        "visible_control_count": 62,
        "item_cells": [
            {
                "label": f"cell {index}",
                "window": "Vendor Stock",
                "section": f"section-{index}",
                "item_name": f"item-{index}",
                "item_base_value": 1000 + index,
                "item_sell_value": None,
                "item_quantity": 2000 + index,
                "selected_inventory_accepts_item": None,
            }
            for index in range(60)
        ],
        "open_windows": ["Vendor Stock", "Decision"],
    }
    assert telemetry_digest["selected"] == {
        "id": "entity-hep",
        "name": "Hep",
        "hunger": 2.5,
        "food_items": 4,
        "in_combat": True,
        "indoors": False,
        "inventory_complete": True,
        "blood": 77.0,
        "bleeding_rate": 0.25,
        "position": {"x": 1.0, "y": 2.0, "z": 3.0},
    }


def test_log_digest_marks_absent_telemetry_without_inventing_nested_state() -> None:
    observation = Observation(
        run_id="no-telemetry",
        step_index=0,
        observed_at=NOW,
        mode="mock",
        telemetry=None,
    )

    digest = observation.log_digest()
    assert digest["telemetry"] is None
    assert set(digest) == {
        "run_id",
        "step_index",
        "mode",
        "control_mode",
        "planning_mode",
        "world_revision",
        "telemetry_stale",
        "telemetry_age_seconds",
        "events",
        "objective",
        "advisor",
        "digest",
        "telemetry",
    }


def test_planner_payload_default_and_rendering_are_exact_public_contracts() -> None:
    observation = _rich_observation(item_control_count=1)
    observation.objective = "See café inventory."
    assert observation.planner_payload() == observation.planner_payload(
        max_chars=100_000
    )
    payload_text = observation.planner_payload()
    payload = json.loads(payload_text)

    assert payload["affordances"] == observation.affordance_digest()
    for superseded in (
        "semantic_actions",
        "dialogue_targets",
        "travel_destinations",
        "context_targets",
        "visible_controls",
    ):
        assert superseded not in payload
    assert "\\u00e9" not in payload_text
    assert payload_text == json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_planner_payload_interprets_nutrition_reserve_for_the_whole_squad() -> None:
    observation = _rich_observation(item_control_count=0)
    assert observation.telemetry is not None
    empty = observation.model_copy(
        update={
            "telemetry": observation.telemetry.model_copy(update={"squad": []})
        }
    )
    assert empty.squad_nutrition_digest() == {}
    observation.telemetry = observation.telemetry.model_copy(
        update={
            "squad": [
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    hunger=2.775886,
                ),
                CharacterState(id="entity-boundary", name="Boundary", hunger=2.5),
                CharacterState(id="entity-eating", name="Eating", hunger=2.4),
                CharacterState(id="entity-two", name="Two", hunger=2.0),
                CharacterState(id="entity-malnourished", name="Mal", hunger=1.9),
                CharacterState(id="entity-one", name="One", hunger=1.0),
                CharacterState(id="entity-starving", name="Starving", hunger=0.9),
                CharacterState(id="entity-unknown", name="Unknown", hunger=None),
            ]
        }
    )
    expected = {
        "scale": {
            "direction": "counts_down_from_full_to_starving",
            "full": 3.0,
            "ordinary_food_automatic_eating_below": 2.5,
            "edible_ingredient_automatic_eating_below": 2.0,
            "malnutrition_below": 2.0,
            "starvation_fainting_baseline_below": 1.0,
            "starvation_fainting_onset_uses_knockout_point": True,
        },
        "members": [
            {
                "id": "entity-hep",
                "name": "Hep",
                "selected": True,
                "nutrition_reserve": 2.775886,
                "status": "well_fed",
            },
            {
                "id": "entity-boundary",
                "name": "Boundary",
                "selected": False,
                "nutrition_reserve": 2.5,
                "status": "well_fed",
            },
            {
                "id": "entity-eating",
                "name": "Eating",
                "selected": False,
                "nutrition_reserve": 2.4,
                "status": "ordinary_food_automatic_eating_range",
            },
            {
                "id": "entity-two",
                "name": "Two",
                "selected": False,
                "nutrition_reserve": 2.0,
                "status": "ordinary_food_automatic_eating_range",
            },
            {
                "id": "entity-malnourished",
                "name": "Mal",
                "selected": False,
                "nutrition_reserve": 1.9,
                "status": "malnourished",
            },
            {
                "id": "entity-one",
                "name": "One",
                "selected": False,
                "nutrition_reserve": 1.0,
                "status": "malnourished",
            },
            {
                "id": "entity-starving",
                "name": "Starving",
                "selected": False,
                "nutrition_reserve": 0.9,
                "status": "starvation_fainting_risk",
            },
            {
                "id": "entity-unknown",
                "name": "Unknown",
                "selected": False,
                "nutrition_reserve": None,
                "status": "unknown",
            },
        ],
    }

    full_payload = json.loads(observation.planner_payload())
    assert full_payload["squad_nutrition"] == expected
    model_facing_squad = full_payload["telemetry"]["squad"]
    assert [member["nutrition_reserve"] for member in model_facing_squad] == [
        2.775886,
        2.5,
        2.4,
        2.0,
        1.9,
        1.0,
        0.9,
        None,
    ]
    assert all("hunger" not in member for member in model_facing_squad)
    assert json.loads(
        observation.planner_payload(max_chars=1, max_context_chars=1_000_000)
    )["squad_nutrition"] == expected


def test_planner_payload_passes_compaction_target_and_hard_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kenshi_agent import models as models_module

    observation = _rich_observation(item_control_count=4)
    passed_budgets: list[tuple[int, int | None]] = []

    def capture_budget(
        payload: dict[str, Any],
        *,
        full_text: str,
        max_chars: int,
        hard_max_chars: int | None = None,
        measure: Any = len,
        measurement: str = "characters",
    ) -> str:
        assert payload
        assert full_text
        assert measure(full_text) > 0
        assert measurement == "characters"
        passed_budgets.append((max_chars, hard_max_chars))
        return "{}"

    monkeypatch.setattr(
        models_module,
        "budget_observation_payload",
        capture_budget,
    )

    observation.planner_payload(
        max_chars=8_000,
        max_context_chars=1_000_000,
    )
    observation.planner_payload(
        max_chars=12_000,
        max_context_chars=900_000,
    )

    assert passed_budgets == [(8_000, 1_000_000), (12_000, 900_000)]


def test_planner_payload_fails_closed_below_the_complete_affordance_ceiling() -> None:
    observation = _rich_observation(item_control_count=20)
    from kenshi_agent.observation_budget import PlannerPayloadContextError

    with pytest.raises(PlannerPayloadContextError):
        observation.planner_payload(max_chars=1, max_context_chars=1)

    exact = json.loads(
        observation.planner_payload(
            max_chars=100_000,
            max_context_chars=100_000,
        )
    )
    assert exact["affordances"] == observation.affordance_digest()

    with pytest.raises(PlannerPayloadContextError):
        observation.planner_payload(
            max_chars=1_000,
            max_context_chars=1_000,
        )
