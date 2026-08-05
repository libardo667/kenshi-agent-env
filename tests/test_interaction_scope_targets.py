"""Target behaviour for the interaction-scope and order-lifecycle stage.

These encode where the architecture is going, and they fail today on purpose.
Each is `xfail(strict=True)`, so the suite goes red the moment the behaviour
lands and the test stops being a prediction. That is the intended signal: flip
the marker off in the slice that implements it rather than discovering months
later that a gate has been silently passing.

Nothing here weakens an existing test. Every assertion is about the target
model; the current model is pinned by the suite as it stands.
"""

from __future__ import annotations

import typing

import pytest

from kenshi_agent.core.telemetry import NativeControlState, TelemetrySnapshot
from kenshi_agent.core.transport import NativeCommandRequest

SLICE_1 = "Slice 1: interaction contract replaces selection cardinality"
SLICE_2 = "Slice 2: protocol 2.0 roster, platoons, plural command records"
SLICE_3 = "Slice 3: native captured-recipient command registry"
SLICE_4 = "Slice 4: immutable dispatch basis at the input boundary"


@pytest.mark.xfail(strict=True, reason=SLICE_1)
def test_selection_requirement_is_absent_from_production_code() -> None:
    """Section 12: the old cardinality model must not survive or grow back."""

    from kenshi_agent import operation_definitions

    assert not hasattr(operation_definitions, "SelectionRequirement")


@pytest.mark.xfail(strict=True, reason=SLICE_1)
def test_every_operation_definition_resolves_one_interaction_contract() -> None:
    """Section 4.7: exactly one of a static contract or a resolver."""

    from kenshi_agent.operation_definitions import OPERATION_DEFINITION_LIST

    for definition in OPERATION_DEFINITION_LIST:
        static = getattr(definition, "interaction", None)
        resolver = getattr(definition, "resolve_interaction", None)
        assert (static is None) != (resolver is None), definition.kind


@pytest.mark.xfail(strict=True, reason=SLICE_1)
def test_transport_holds_no_command_name_cardinality_exception_set() -> None:
    """Section 3.1: recipient scope is not decided at the transport edge.

    `NativeCommandRequest.validate_native_fences` currently refuses a
    multi-character basis unless the command appears in a hardcoded name set.
    That is a second selection authority living in transport validation.
    """

    import inspect

    source = inspect.getsource(NativeCommandRequest.validate_native_fences)

    assert "requires exactly one selected character" not in source


@pytest.mark.xfail(strict=True, reason=SLICE_2)
def test_player_roster_is_not_serialised_under_a_squad_field() -> None:
    """Section 9.1: roster, platoons, primary, and selection are distinct."""

    assert "squad" not in TelemetrySnapshot.model_fields
    assert "roster" in TelemetrySnapshot.model_fields
    assert "platoons" in TelemetrySnapshot.model_fields


@pytest.mark.xfail(strict=True, reason=SLICE_2)
def test_native_control_state_has_no_singular_active_command_id() -> None:
    """Section 9.4: plural command records replace the singular field."""

    assert "active_command_id" not in NativeControlState.model_fields


@pytest.mark.xfail(strict=True, reason=SLICE_3)
def test_two_disjoint_recipient_commands_can_be_retained_at_once() -> None:
    """Section 13.1: A/B travel and C mining coexist as retained orders.

    The portable half of the disjoint-concurrency proof. The live half is the
    supervised run in section 13.1; this pins that the *model* can express two
    simultaneously retained commands on disjoint recipients, which the single
    `g_activeNativeCommand` and singular `active_command_id` cannot.
    """

    records = getattr(NativeControlState, "model_fields", {}).get("commands")
    assert records is not None

    state = NativeControlState.model_validate(
        {
            "available": True,
            "commands": [
                {
                    "command_id": "cmd-" + "a" * 32,
                    "recipient_character_ids": ["char-a", "char-b"],
                    "status": "accepted",
                },
                {
                    "command_id": "cmd-" + "b" * 32,
                    "recipient_character_ids": ["char-c"],
                    "status": "accepted",
                },
            ],
        }
    )
    retained = state.commands  # type: ignore[attr-defined]

    assert len(retained) == 2
    first, second = (set(record.recipient_character_ids) for record in retained)
    assert not first & second


@pytest.mark.xfail(strict=True, reason=SLICE_4)
def test_dispatch_basis_is_available_as_an_immutable_capture() -> None:
    """Section 5: recipients are captured at the input boundary, then frozen."""

    from kenshi_agent.core import transport

    basis = getattr(transport, "DispatchBasis", None)
    assert basis is not None
    assert "recipient_character_ids" in basis.model_fields
    assert "interaction_contract_fingerprint" in basis.model_fields


@pytest.mark.xfail(strict=True, reason=SLICE_4)
def test_monitor_disposition_is_distinct_from_native_order_disposition() -> None:
    """Section 3.4: detaching a monitor is not clearing a Kenshi order."""

    from kenshi_agent.core import operation

    monitor = getattr(operation, "MonitorDisposition", None)
    order = getattr(operation, "NativeOrderDisposition", None)
    assert monitor is not None
    assert order is not None

    monitor_values = {member.value for member in monitor}
    order_values = {member.value for member in order}

    assert "detached_timeout" in monitor_values
    assert "retained" in order_values
    # "cancelled" is the word that currently lies about both.
    assert "cancelled" not in order_values


@pytest.mark.xfail(strict=True, reason=SLICE_3)
def test_native_command_request_carries_its_resolved_recipients() -> None:
    """Section 5: the wire request names recipients, not just a selection."""

    fields = NativeCommandRequest.model_fields
    assert "recipient_character_ids" in fields


def test_native_command_names_remain_the_known_ten() -> None:
    """Characterization: the Slice 0 native route inventory.

    Not a target - a baseline. If this changes, the catalog and the proof
    manifest must change with it in the same edit.
    """

    annotation = NativeCommandRequest.model_fields["command"].annotation

    assert set(typing.get_args(annotation)) == {
        "approach_confirmed_vendor",
        "move_to_character",
        "select_squad_member",
        "regroup_with_squad_member",
        "move_in_direction",
        "travel_to_map_destination",
        "exit_current_building",
        "perform_context_action",
        "produce_resource_output",
        "open_context_inventory",
    }
