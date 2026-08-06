"""The Slice 0 interaction catalog covers every route and stays one authority."""

from __future__ import annotations

import json

from kenshi_agent.operation_definitions import OPERATION_DEFINITION_LIST
from kenshi_agent.tooling.interaction_catalog import (
    MANIFEST_PATH,
    PROOF_STATUSES,
    REGISTRY_OWNED_FIELDS,
    audit_interaction_catalog,
    native_command_names,
    native_command_owner,
    render_interaction_catalog,
)


def test_every_operation_appears_exactly_once_in_the_catalog() -> None:
    audit = audit_interaction_catalog()

    assert audit.passed
    assert not audit.uncatalogued_operations
    assert not audit.unknown_manifest_operations
    assert not audit.duplicate_manifest_keys
    assert {row.kind for row in audit.rows} == {
        definition.kind for definition in OPERATION_DEFINITION_LIST
    }


def test_internal_only_operations_are_named_rather_than_omitted() -> None:
    audit = audit_interaction_catalog()

    internal = {row.kind for row in audit.rows if not row.planner_visible}
    covered = {entry.operation_kind for entry in audit.entries}

    assert internal
    assert internal <= covered


def test_every_native_command_route_has_an_owning_operation() -> None:
    audit = audit_interaction_catalog()

    assert not audit.native_commands_without_definition
    kinds = {definition.kind for definition in OPERATION_DEFINITION_LIST}
    for command in native_command_names():
        assert native_command_owner(command) in kinds


def test_proof_manifest_does_not_restate_registry_owned_fields() -> None:
    """The manifest may propose and record proof; it may not own semantics.

    Slice 1 moves the proposed contract into `OperationDefinition`. From that
    point the `proposed_*` keys must disappear from this manifest rather than
    becoming a second copy of the contract.
    """

    audit = audit_interaction_catalog()

    assert not audit.manifest_restates_registry

    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        assert not REGISTRY_OWNED_FIELDS.intersection(entry)


def test_proof_status_is_typed_and_claims_carry_evidence() -> None:
    audit = audit_interaction_catalog()

    assert not audit.invalid_proof_statuses
    assert not audit.entries_without_evidence
    for entry in audit.entries:
        assert entry.proof_status in PROOF_STATUSES
        if entry.proof_status != "unproven":
            assert entry.evidence


def test_no_group_scope_behaviour_is_recorded_as_live_proven() -> None:
    """Slice 0 characterization: group semantics are proposals, not evidence.

    This is a factual record of the starting point, not a permanent rule. When
    a live proof from section 13 lands, that run's entry moves to `live_proven`
    and this assertion is the thing that must be updated deliberately.
    """

    audit = audit_interaction_catalog()

    assert audit.status_counts()["live_proven"] == 0


def test_catalog_renders_the_contract_execution_and_native_routes() -> None:
    lines = render_interaction_catalog(audit_interaction_catalog())
    body = "\n".join(lines)

    assert "INTERACTION CONTRACT (resolved from the sole operation registry)" in body
    assert "EXECUTION AND ROUTING" in body
    assert "NATIVE COMMAND ROUTES" in body
    assert "coverage proof          PASS" in body


def test_diagnostic_only_routes_are_exactly_the_reviewed_set() -> None:
    """An unowned wire command is a hole in the catalog, so name every one.

    `shift_body_platoon` is the body-shift probe: the plug-in answers it, but no
    affordance offers it and no operation issues it, so the "every route has an
    owning operation" check would otherwise force a real operation into
    existence purely to satisfy a catalog. Pinning the set keeps that escape
    hatch one entry wide.
    """

    from kenshi_agent.tooling.interaction_catalog import (
        DIAGNOSTIC_ONLY_NATIVE_COMMANDS,
        all_native_command_names,
    )

    assert DIAGNOSTIC_ONLY_NATIVE_COMMANDS == {"shift_body_platoon"}
    assert DIAGNOSTIC_ONLY_NATIVE_COMMANDS <= set(all_native_command_names())


def test_no_operation_can_issue_a_diagnostic_only_route() -> None:
    """The exemption is only honest while nothing agent-reachable uses it."""

    from kenshi_agent.operation_definitions import (
        OPERATION_DEFINITION_LIST,
        native_wire_command_for,
    )
    from kenshi_agent.tooling.interaction_catalog import (
        DIAGNOSTIC_ONLY_NATIVE_COMMANDS,
    )

    issued = {
        native_wire_command_for(definition) for definition in OPERATION_DEFINITION_LIST
    }
    assert not (issued & DIAGNOSTIC_ONLY_NATIVE_COMMANDS)
