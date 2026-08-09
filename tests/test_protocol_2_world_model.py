"""Executable contract for the Protocol 2.0 world-model specification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.core.protocol_2 import (
    CollectionCompleteness,
    Protocol2WorldModel,
    UnownedWorkChannel,
)

FIXTURES = Path(__file__).parent / "fixtures" / "protocol_2"
NATIVE_CONFORMANCE = (
    Path(__file__).resolve().parents[1]
    / "native"
    / "KenshiAgentTelemetry"
    / "NativeCommandProtocolTests.cpp"
)
NATIVE_BUILD = Path(__file__).resolve().parents[1] / "scripts" / "build_native.ps1"


def test_valid_fixture_expresses_multiple_platoons_and_simultaneous_commands() -> None:
    model = Protocol2WorldModel.model_validate_json(
        (FIXTURES / "valid_multiple_platoons_and_commands.json").read_bytes()
    )

    assert [platoon.id for platoon in model.platoons.items] == [
        "platoon-alpha",
        "platoon-beta",
    ]
    assert model.active_platoon_id == "platoon-alpha"
    assert model.primary_character_id == "character-a"
    assert model.selected_character_ids.items == ["character-a", "character-b"]
    assert {
        command.command_id
        for command in model.controller_commands.retained_commands.items
    } == {
        "cmd-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "cmd-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }
    assert {
        command.dispatch_basis.active_platoon_id
        for command in model.controller_commands.retained_commands.items
    } == {"platoon-alpha", "platoon-beta"}
    assert model.roster.items[2].work is not None
    assert model.roster.items[2].work.jobs_enabled is True
    assert model.roster.items[2].work.jobs.items[0].task_name == "OPERATE_MACHINERY"
    assert model.roster.items[3].work is not None
    assert model.roster.items[3].work.permanent_jobs.items[0].task_name == "JOB_MEDIC"
    assert (
        model.observed_unowned_kenshi_work.items[0].channel
        is UnownedWorkChannel.PERMANENT_JOB
    )


def test_valid_fixture_can_say_a_collection_was_truncated_without_guessing_total() -> None:
    model = Protocol2WorldModel.model_validate_json(
        (FIXTURES / "valid_truncated_world_model.json").read_bytes()
    )

    assert model.roster.completeness is CollectionCompleteness.TRUNCATED
    assert model.roster.known_total == 3
    assert model.platoons.items[0].member_ids.completeness is (
        CollectionCompleteness.TRUNCATED
    )
    assert model.platoons.items[0].member_ids.known_total is None
    assert model.controller_commands.recent_terminal_commands.completeness is (
        CollectionCompleteness.TRUNCATED
    )


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("invalid_contradictory_membership.json", "membership disagree"),
        ("invalid_complete_collection_count.json", "known_total equal"),
    ],
)
def test_invalid_protocol_2_fixtures_are_rejected(fixture: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Protocol2WorldModel.model_validate_json((FIXTURES / fixture).read_bytes())


def test_the_old_1_x_shape_is_explicitly_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        Protocol2WorldModel.model_validate_json(
            (FIXTURES / "invalid_old_1_x_shape.json").read_bytes()
        )

    errors = caught.value.errors()
    assert any(error["loc"] == ("protocol_version",) for error in errors)
    assert any(
        error["loc"] == ("squad",) and error["type"] == "extra_forbidden"
        for error in errors
    )
    assert any(
        error["loc"] == ("native_control",) and error["type"] == "extra_forbidden"
        for error in errors
    )
    assert "active_command_id" in (
        FIXTURES / "invalid_old_1_x_shape.json"
    ).read_text(encoding="utf-8")


def test_exported_schema_has_only_the_resolved_2_0_names() -> None:
    schema = Protocol2WorldModel.model_json_schema()
    properties = schema["properties"]

    assert "roster" in properties
    assert "platoons" in properties
    assert "active_platoon_id" in properties
    assert "primary_character_id" in properties
    assert "selected_character_ids" in properties
    assert "controller_commands" in properties
    assert "observed_unowned_kenshi_work" in properties
    rendered = json.dumps(schema)
    assert '"squad"' not in rendered
    assert "active_command_id" not in rendered
    assert schema["additionalProperties"] is False


def test_native_conformance_target_reads_the_same_boundary_fixtures() -> None:
    conformance = NATIVE_CONFORMANCE.read_text(encoding="utf-8")
    build = NATIVE_BUILD.read_text(encoding="utf-8")

    assert "TestProtocol2WorldModelFixtures" in conformance
    assert "valid_multiple_platoons_and_commands.json" in conformance
    assert "invalid_old_1_x_shape.json" in conformance
    assert '"tests\\fixtures\\protocol_2"' in build
    assert "$protocolTests $fixtures $research $protocol2Fixtures" in build
