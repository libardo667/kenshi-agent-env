"""Golden native-command documents shared with the C++ conformance target."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.core.telemetry import (
    NativeCommandAcknowledgement,
    WorldTarget,
)
from kenshi_agent.core.transport import NativeCommandRequest

FIXTURES = Path(__file__).parent / "fixtures" / "native_commands"


def test_python_accepts_targetless_direction_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_direction_request.json").read_bytes()
    )

    assert request.command == "move_in_direction"
    assert request.target_id == ""
    assert request.bearing_degrees == 90.0
    assert request.distance_units == 250.0


def test_python_accepts_targeted_request_fixture_with_no_direction_payload() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_targeted_request.json").read_bytes()
    )

    assert request.command == "move_to_character"
    assert request.selected_character_ids == [
        "entity-selected",
        "entity-companion",
    ]
    assert request.target_id == "entity-destination"
    assert request.bearing_degrees == 0.0
    assert request.distance_units == 0.0


def test_python_accepts_exact_known_map_destination_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_map_travel_request.json").read_bytes()
    )

    assert request.command == "travel_to_map_destination"
    assert request.selected_character_ids == [
        "entity-selected",
        "entity-companion",
    ]
    assert request.target_id == "entity-known-town"
    assert request.bearing_degrees == 0.0
    assert request.distance_units == 0.0


def test_python_accepts_exact_squad_selection_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_squad_selection_request.json").read_bytes()
    )

    assert request.command == "select_squad_member"
    assert request.selected_character_ids == ["entity-bark", "entity-plant"]
    assert request.target_id == "entity-plant"


def test_python_accepts_exact_dialogue_approach_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_approach_request.json").read_bytes()
    )

    assert request.command == "approach_confirmed_vendor"
    assert request.selected_character_ids == [
        "entity-selected",
        "entity-companion",
    ]
    assert request.target_id == "entity-dialogue-target"
    assert request.bearing_degrees == 0.0
    assert request.distance_units == 0.0


def test_python_accepts_parameterless_building_exit_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_exit_building_request.json").read_bytes()
    )

    assert request.command == "exit_current_building"
    assert request.target_id == ""
    assert request.bearing_degrees == 0.0
    assert request.distance_units == 0.0


def test_python_accepts_game_wide_interface_close_with_empty_selection() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_close_active_interface_request.json").read_bytes()
    )

    assert request.command == "close_active_interface"
    assert request.selected_character_ids == []
    assert request.target_id == ""


def test_python_accepts_exact_context_action_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_context_action_request.json").read_bytes()
    )

    assert request.command == "perform_context_action"
    assert request.context_action == "operate"
    assert request.target_id == "entity-natural-resource"
    assert request.bearing_degrees == 0.0
    assert request.distance_units == 0.0


def test_python_accepts_exact_first_aid_context_action_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_first_aid_context_action_request.json").read_bytes()
    )

    assert request.command == "perform_context_action"
    assert request.context_action == "first_aid"
    assert request.target_id == "entity-injured-squadmate"


def test_python_accepts_body_shift_fixture_without_a_selected_recipient() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_body_shift_request.json").read_bytes()
    )

    assert request.command == "shift_into_body"
    assert request.selected_character_ids == []
    assert request.target_id == "entity-body-to-enter"


@pytest.mark.parametrize(
    ("fixture", "command"),
    [
        ("valid_resource_production_request.json", "produce_resource_output"),
    ],
)
def test_python_accepts_exact_resource_workflow_request_fixtures(
    fixture: str,
    command: str,
) -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / fixture).read_bytes()
    )

    assert request.command == command
    assert request.target_id == "entity-natural-resource"
    assert request.bearing_degrees == 0.0
    assert request.distance_units == 0.0


def test_python_accepts_reviewed_natural_resource_fixture() -> None:
    target = WorldTarget.model_validate_json(
        (FIXTURES / "valid_natural_resource.json").read_bytes()
    )

    assert target.id == "entity-natural-resource"
    assert target.context_actions == ["operate"]


def test_python_rejects_direction_request_that_smuggles_a_target() -> None:
    with pytest.raises(ValidationError, match="must not name a target"):
        NativeCommandRequest.model_validate_json(
            (FIXTURES / "invalid_direction_target_request.json").read_bytes()
        )


def test_python_accepts_direction_acknowledgement_with_matching_identity() -> None:
    acknowledgement = NativeCommandAcknowledgement.model_validate_json(
        (FIXTURES / "valid_direction_acknowledgement.json").read_bytes()
    )

    assert acknowledgement.command == "move_in_direction"
    assert acknowledgement.target_id == ""
    assert acknowledgement.bearing_degrees == 90.0
    assert acknowledgement.distance_units == 250.0
