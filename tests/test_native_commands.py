from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.models import (
    ControlMode,
    NativeCommandAcknowledgement,
    NativeCommandRequest,
    NativeCommandStatus,
    NativeControlState,
    WorldStateRevision,
)
from kenshi_agent.native_commands import write_native_command_request_atomic

COMMAND_ID = "cmd-0123456789abcdef0123456789abcdef"


def revision(sequence: int | None = 7) -> WorldStateRevision:
    return WorldStateRevision(
        telemetry_sequence=sequence,
        frame_sequence=3,
        capability_epoch=2,
        observed_at_monotonic=10.0,
    )


def request() -> NativeCommandRequest:
    return NativeCommandRequest(
        schema_version="1.2",
        command_id=COMMAND_ID,
        command="approach_confirmed_vendor",
        control_mode=ControlMode.NATIVE_ASSISTED,
        identity_session_id="session-0000000000000001-0000000000000001",
        based_on_revision=revision(),
        selected_character_ids=["entity-selected"],
        target_id="entity-vendor",
    )


def test_native_request_is_strict_exact_and_telemetry_revision_bound() -> None:
    valid = request()

    assert valid.based_on_revision.telemetry_sequence == 7
    with pytest.raises(ValidationError):
        NativeCommandRequest.model_validate(
            valid.model_dump(mode="python") | {"command_id": "cmd-000001"}
        )
    with pytest.raises(ValidationError):
        NativeCommandRequest.model_validate(
            valid.model_dump(mode="python") | {"control_mode": ControlMode.INTERFACE_ONLY}
        )
    with pytest.raises(ValidationError):
        NativeCommandRequest.model_validate(
            valid.model_dump(mode="python")
            | {"selected_character_ids": ["entity-selected", "entity-other"]}
        )
    with pytest.raises(ValidationError, match="telemetry sequence"):
        NativeCommandRequest.model_validate(
            valid.model_dump(mode="python") | {"based_on_revision": revision(None)}
        )
    with pytest.raises(ValidationError):
        NativeCommandRequest.model_validate(valid.model_dump(mode="python") | {"unexpected": True})


def test_group_selection_is_allowed_only_for_set_aware_native_commands() -> None:
    valid = request().model_dump(mode="python")
    group = ["entity-selected", "entity-companion"]

    move = NativeCommandRequest.model_validate(
        valid
        | {
            "command": "move_to_character",
            "selected_character_ids": group,
        }
    )
    assert move.selected_character_ids == group

    with pytest.raises(ValidationError, match="exactly one selected character"):
        NativeCommandRequest.model_validate(
            valid | {"selected_character_ids": group}
        )


def test_only_resource_production_may_request_a_larger_bounded_yield() -> None:
    valid = request()
    production = NativeCommandRequest.model_validate(
        valid.model_dump(mode="python")
        | {
            "command": "produce_resource_output",
            "minimum_output_quantity": 5,
        }
    )

    assert production.minimum_output_quantity == 5
    with pytest.raises(ValidationError, match="only resource production"):
        NativeCommandRequest.model_validate(
            valid.model_dump(mode="python") | {"minimum_output_quantity": 2}
        )


def test_context_action_request_requires_a_reviewed_semantic() -> None:
    valid = request().model_dump(mode="python")
    context = NativeCommandRequest.model_validate(
        valid
        | {
            "command": "perform_context_action",
            "target_id": "entity-injured-squadmate",
            "context_action": "first_aid",
        }
    )

    assert context.context_action == "first_aid"
    with pytest.raises(ValidationError, match="requires its reviewed semantic"):
        NativeCommandRequest.model_validate(
            valid | {"command": "perform_context_action"}
        )
    with pytest.raises(ValidationError, match="only a context-action command"):
        NativeCommandRequest.model_validate(
            valid | {"context_action": "first_aid"}
        )


def test_native_telemetry_snapshot_identity_ignores_capture_and_observation_time() -> None:
    basis = revision()
    same_telemetry = basis.model_copy(
        update={
            "frame_sequence": None,
            "observed_at_monotonic": 11.0,
        }
    )

    assert basis.same_telemetry_snapshot_as(same_telemetry)
    assert not basis.same_snapshot_as(same_telemetry)
    assert not basis.same_telemetry_snapshot_as(
        same_telemetry.model_copy(update={"telemetry_sequence": 8})
    )
    assert not basis.same_telemetry_snapshot_as(
        same_telemetry.model_copy(update={"capability_epoch": 3})
    )


def test_native_acknowledgement_requires_causal_sequences_for_each_status() -> None:
    accepted = NativeCommandAcknowledgement(
        command_id=COMMAND_ID,
        command="approach_confirmed_vendor",
        status=NativeCommandStatus.ACCEPTED,
        reason="issued",
        target_id="entity-vendor",
        selected_character_ids=["entity-selected"],
        based_on_telemetry_sequence=7,
        acknowledged_at_telemetry_sequence=8,
        accepted_at_telemetry_sequence=8,
    )
    completed = accepted.model_copy(
        update={
            "status": NativeCommandStatus.COMPLETED,
            "reason": "exact_dialogue_target_open",
            "terminal_at_telemetry_sequence": 10,
        }
    )
    rejected = accepted.model_copy(
        update={
            "status": NativeCommandStatus.REJECTED,
            "reason": "stale_revision",
            "accepted_at_telemetry_sequence": None,
            "terminal_at_telemetry_sequence": 8,
        }
    )

    assert completed.terminal_at_telemetry_sequence == 10
    assert rejected.accepted_at_telemetry_sequence is None
    with pytest.raises(ValidationError, match="later than the request basis"):
        NativeCommandAcknowledgement.model_validate(
            accepted.model_dump(mode="python") | {"acknowledged_at_telemetry_sequence": 7}
        )
    with pytest.raises(ValidationError, match="accepted_at_telemetry_sequence"):
        NativeCommandAcknowledgement.model_validate(
            accepted.model_dump(mode="python") | {"accepted_at_telemetry_sequence": None}
        )
    with pytest.raises(ValidationError, match="terminal_at_telemetry_sequence"):
        NativeCommandAcknowledgement.model_validate(
            completed.model_dump(mode="python") | {"terminal_at_telemetry_sequence": None}
        )
    with pytest.raises(ValidationError, match="must not report acceptance"):
        NativeCommandAcknowledgement.model_validate(
            rejected.model_dump(mode="python") | {"accepted_at_telemetry_sequence": 8}
        )


def test_native_control_lookup_is_command_id_specific() -> None:
    acknowledgement = NativeCommandAcknowledgement(
        command_id=COMMAND_ID,
        command="approach_confirmed_vendor",
        status=NativeCommandStatus.ACCEPTED,
        reason="issued",
        target_id="entity-vendor",
        selected_character_ids=["entity-selected"],
        based_on_telemetry_sequence=7,
        acknowledged_at_telemetry_sequence=8,
        accepted_at_telemetry_sequence=8,
    )
    state = NativeControlState(acknowledgements=[acknowledgement])

    assert state.acknowledgement_for(COMMAND_ID) == acknowledgement
    assert state.acknowledgement_for("cmd-ffffffffffffffffffffffffffffffff") is None


def test_native_request_writer_atomically_replaces_one_bounded_json_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native_command.request.json"

    write_native_command_request_atomic(path, request())

    parsed = NativeCommandRequest.model_validate_json(path.read_bytes())
    assert parsed.command_id == COMMAND_ID
    assert list(tmp_path.iterdir()) == [path]
