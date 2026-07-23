from pathlib import Path

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from kenshi_agent.models import (
    CharacterState,
    ClickAction,
    NearbyEntity,
    Observation,
    PlannerDecision,
    ScrollAction,
    SkillAction,
    SkillSpec,
    TelemetrySnapshot,
    UIState,
    parse_action,
)
from kenshi_agent.schema_export import export_schemas


def test_nearby_entity_visibility_is_unknown_until_observed() -> None:
    entity = NearbyEntity(id="nearby:0", name="Bar Trader", kind="character")

    assert entity.visible is None
    assert entity.position is None
    assert entity.camera_bearing_degrees is None
    assert entity.screen_position is None
    assert entity.shop_inventory_owner is None


def test_stable_identity_snapshot_requires_consistent_selection_and_unique_ids() -> None:
    snapshot = TelemetrySnapshot(
        protocol_version="0.2.0",
        identity_session_id="session-process-1",
        capabilities=["squad.basic", "nearby.characters", "identity.stable_handles"],
        ui=UIState(
            selected_character_id="entity-player",
            selected_character_ids=["entity-player"],
        ),
        squad=[
            CharacterState(
                id="entity-player",
                name="Wanderer",
                selected=True,
            )
        ],
        nearby_entities=[
            NearbyEntity(id="entity-vendor", name="Barman", kind="character")
        ],
    )

    assert snapshot.identity_session_id == "session-process-1"

    invalid_selection = snapshot.model_dump(mode="python")
    invalid_selection["ui"] = UIState(
        selected_character_id="entity-missing",
        selected_character_ids=["entity-missing"],
    )
    with pytest.raises(ValidationError, match="must refer to current squad IDs"):
        TelemetrySnapshot.model_validate(invalid_selection)

    with pytest.raises(ValidationError, match="must be unique"):
        TelemetrySnapshot(
            protocol_version="0.2.0",
            identity_session_id="session-process-1",
            capabilities=["identity.stable_handles"],
            squad=[CharacterState(id="entity-shared", name="Wanderer")],
            nearby_entities=[
                NearbyEntity(id="entity-shared", name="Barman", kind="character")
            ],
        )


def test_action_discriminator_parses_click() -> None:
    action = parse_action(
        {
            "kind": "click",
            "x": 0.25,
            "y": 0.75,
            "space": "normalized",
            "button": "left",
        }
    )
    assert isinstance(action, ClickAction)
    assert action.x == 0.25
    assert action.hold_seconds == 0.0


def test_click_hold_is_bounded() -> None:
    assert ClickAction(x=0.5, y=0.5, hold_seconds=0.12).hold_seconds == 0.12
    with pytest.raises(ValidationError):
        ClickAction(x=0.5, y=0.5, hold_seconds=0.51)


def test_action_discriminator_parses_bounded_scroll() -> None:
    action = parse_action(
        {
            "kind": "scroll",
            "x": 0.5,
            "y": 0.45,
            "space": "normalized",
            "notches": 1,
        }
    )

    assert isinstance(action, ScrollAction)
    assert action.notches == 1


def test_scroll_rejects_zero_and_excessive_notches() -> None:
    with pytest.raises(ValidationError):
        ScrollAction(x=0.5, y=0.5, notches=0)
    with pytest.raises(ValidationError):
        ScrollAction(x=0.5, y=0.5, notches=9)


def test_unknown_action_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_action({"kind": "wait", "seconds": 1, "surprise": True})


def test_skill_action_accepts_legacy_argument_mapping() -> None:
    action = SkillAction(name="click_at", args={"x": 0.2, "y": 0.3})  # type: ignore[arg-type]

    assert action.argument_map() == {"x": 0.2, "y": 0.3}


def test_planner_decision_is_an_openai_compatible_strict_schema() -> None:
    schema = to_strict_json_schema(PlannerDecision)

    def assert_supported_nodes(value: object) -> None:
        if isinstance(value, dict):
            assert value
            assert "oneOf" not in value
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
            for child in value.values():
                assert_supported_nodes(child)
        elif isinstance(value, list):
            for child in value:
                assert_supported_nodes(child)

    assert schema["type"] == "object"
    assert_supported_nodes(schema)


def test_observation_planner_payload_omits_screenshot_path() -> None:
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="mock",
        telemetry=TelemetrySnapshot(),
        screenshot_path=Path("secret-frame.png"),
        objective="Explore nearby.",
        skill_specs=[
            SkillSpec(
                name="move_on_map",
                arguments={"x": "Normalized x."},
                visual_precondition="The map is open.",
            )
        ],
    )
    payload = observation.planner_payload()
    assert "secret-frame.png" not in payload
    assert '"run_id": "run"' in payload
    assert '"objective": "Explore nearby."' in payload
    assert '"name": "move_on_map"' in payload
    assert '"visual_precondition": "The map is open."' in payload


def test_schema_export_includes_continuous_plan_contracts(tmp_path: Path) -> None:
    exported = {path.name for path in export_schemas(tmp_path)}

    assert "plan.schema.json" in exported
    assert "plan_patch.schema.json" in exported
    assert "receipt.schema.json" in exported
