"""Target behaviour for the interaction-scope and order-lifecycle stage.

These encode where the architecture is going. Each starts as
`xfail(strict=True)`, so the suite goes red the moment the behaviour lands and
the test stops being a prediction. That is the intended signal: retire the
marker in the slice that implements it rather than discovering months later
that a gate has been silently passing.

Slice 1's three targets and Slice 1b's lifecycle-vocabulary target have been
retired and now assert plainly.

Nothing here weakens an existing test. Every assertion is about the target
model; the current model is pinned by the suite as it stands.
"""

from __future__ import annotations

import typing

import pytest

from kenshi_agent.core.telemetry import NativeControlState, TelemetrySnapshot
from kenshi_agent.core.transport import NativeCommandRequest

SLICE_2 = "Slice 2: protocol 2.0 roster, platoons, plural command records"
SLICE_3 = "Slice 3: native captured-recipient command registry"
SLICE_4 = "Slice 4: immutable dispatch basis at the input boundary"


def test_selection_requirement_is_absent_from_production_code() -> None:
    """Section 12: the old cardinality model must not survive or grow back."""

    from kenshi_agent import operation_definitions

    assert not hasattr(operation_definitions, "SelectionRequirement")


def test_every_operation_definition_resolves_one_interaction_contract() -> None:
    """Section 4.7: exactly one of a static contract or a resolver."""

    from kenshi_agent.operation_definitions import OPERATION_DEFINITION_LIST

    for definition in OPERATION_DEFINITION_LIST:
        static = getattr(definition, "interaction", None)
        resolver = getattr(definition, "resolve_interaction", None)
        assert (static is None) != (resolver is None), definition.kind


def test_transport_holds_no_command_name_cardinality_exception_set() -> None:
    """Section 3.1: recipient scope is not decided at the transport edge.

    `validate_native_fences` used to refuse a multi-character basis unless the
    command appeared in a hardcoded name set - a second selection authority
    living in transport validation, able to disagree with the definition it was
    supposedly enforcing. Scope now has one owner.
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


def test_monitor_disposition_is_distinct_from_order_disposition() -> None:
    """Completed Slice 1b: detaching a monitor is not clearing a Kenshi order."""

    from kenshi_agent.core.lifecycle import MonitorDisposition, OrderDisposition

    monitor_values = {member.value for member in MonitorDisposition}
    order_values = {member.value for member in OrderDisposition}

    assert "detached_after_timeout" in monitor_values
    assert "retained_at_last_observation" in order_values
    assert not monitor_values & order_values
    assert "cancelled" not in order_values


@pytest.mark.xfail(strict=True, reason=SLICE_3)
def test_native_command_request_carries_its_resolved_recipients() -> None:
    """Section 5: the wire request names recipients, not just a selection."""

    fields = NativeCommandRequest.model_fields
    assert "recipient_character_ids" in fields



def test_wire_command_vocabulary_has_exactly_one_python_definition() -> None:
    """One vocabulary, not five copies that drift apart.

    The request schema, the acknowledgement schema, and three Kenshi-surface
    signatures each spelled this list out. Adding `survey_local_resources` to
    the request and missing the acknowledgement meant the plug-in accepted and
    executed a command Python could not read back - and the readback failure
    invalidated the entire telemetry snapshot, not one field.
    """


    from kenshi_agent.core.telemetry import NativeCommandAcknowledgement, NativeWireCommand

    vocabulary = set(typing.get_args(NativeWireCommand))
    assert vocabulary

    request = NativeCommandRequest.model_fields["command"].annotation
    acknowledgement = NativeCommandAcknowledgement.model_fields["command"].annotation

    assert set(typing.get_args(request)) == vocabulary
    assert set(typing.get_args(acknowledgement)) == vocabulary


def test_no_module_redeclares_the_wire_vocabulary_as_a_type() -> None:
    """A second `Literal[...]` spelling of the vocabulary is the defect.

    Deliberately not a search for the command *names*: nine of eleven wire
    commands share a name with an operation kind, so counting string
    occurrences flags every module that lists operation kinds and proves
    nothing. What matters is whether a module declares the vocabulary itself.
    """

    import pathlib
    import re

    from kenshi_agent.core.telemetry import NativeWireCommand
    from kenshi_agent.operation_definitions import OPERATION_DEFINITIONS

    names = set(typing.get_args(NativeWireCommand))
    # Names that exist only on the wire. Most wire commands share a name with
    # an operation kind, so a block listing operation kinds is not a
    # respelling; one carrying a wire-only name is.
    wire_only = names - set(OPERATION_DEFINITIONS)
    assert wire_only, "expected at least one wire-only command name"

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "kenshi_agent"
    literal_block = re.compile(r"Literal\[([^\]]*)\]", re.DOTALL)

    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "telemetry.py":
            continue
        for block in literal_block.findall(path.read_text(encoding="utf-8")):
            spelled = {name for name in names if f'"{name}"' in block}
            # One name is a typed constant for that command, which is fine.
            # Several, including a wire-only name, is the vocabulary again.
            if len(spelled) > 1 and spelled & wire_only:
                offenders.append(
                    f"{path.name} redeclares {len(spelled)} wire commands"
                )

    assert not offenders, offenders


def test_recipient_cardinality_is_never_decided_by_command_name() -> None:
    """Section 3.1, enforced at every edge rather than asserted once.

    Four copies of this fence existed: the Python request schema, the native
    parser, the native dispatch allowlist, and the acknowledgement schema. Each
    was found only when a group-scoped command hit it, one at a time.
    """

    import inspect
    import pathlib

    from kenshi_agent.core.telemetry import NativeCommandAcknowledgement

    for validator in (
        NativeCommandRequest.validate_native_fences,
        NativeCommandAcknowledgement.validate_causal_lifecycle,
    ):
        source = inspect.getsource(validator)
        assert "requires exactly one selected character" not in source

    native = pathlib.Path(__file__).resolve().parents[1] / "native" / "KenshiAgentTelemetry"
    for name in ("NativeCommandProtocol.cpp", "KenshiAgentTelemetry.cpp"):
        source = (native / name).read_text(encoding="utf-8", errors="replace")
        assert "allowsGroupSelection" not in source, name
