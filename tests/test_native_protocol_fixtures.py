"""Golden native-command documents shared with the C++ conformance target."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.models import (
    NativeCommandAcknowledgement,
    NativeCommandRequest,
    WorldTarget,
)

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
    assert request.target_id == "entity-destination"
    assert request.bearing_degrees == 0.0
    assert request.distance_units == 0.0


def test_python_accepts_exact_dialogue_approach_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_approach_request.json").read_bytes()
    )

    assert request.command == "approach_confirmed_vendor"
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


def test_python_accepts_exact_context_action_request_fixture() -> None:
    request = NativeCommandRequest.model_validate_json(
        (FIXTURES / "valid_context_action_request.json").read_bytes()
    )

    assert request.command == "operate_natural_resource"
    assert request.target_id == "entity-natural-resource"
    assert request.bearing_degrees == 0.0
    assert request.distance_units == 0.0


@pytest.mark.parametrize(
    ("fixture", "command"),
    [
        ("valid_resource_production_request.json", "produce_resource_output"),
        ("valid_context_inventory_request.json", "open_context_inventory"),
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
