"""The Slice 0 interaction catalog covers every route and stays one authority."""

from __future__ import annotations

import json

from kenshi_agent.affordances import AFFORDANCE_ADAPTERS
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


def test_catalog_has_no_hidden_internal_only_operations() -> None:
    audit = audit_interaction_catalog()

    internal = {row.kind for row in audit.rows if not row.planner_visible}

    assert not internal


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


def test_reverse_engineering_claims_link_canonical_research_objects() -> None:
    audit = audit_interaction_catalog()

    assert not audit.invalid_research_refs
    assert not audit.embedded_research_claims
    by_kind = {entry.operation_kind: entry for entry in audit.entries}
    assert by_kind["perform_character_order"].research == (
        "game_sources/research/context_menu_orders",
        "game_sources/research/prospecting_window",
    )
    assert by_kind["shift_into_body"].research == (
        "game_sources/research/body_shift",
    )
    assert by_kind["survey_local_resources"].research == (
        "game_sources/research/prospecting_window",
    )
    assert by_kind["open_trade_window"].research == (
        "game_sources/research/inventory_transfer",
    )
    assert by_kind["transfer_item"].research == (
        "game_sources/research/inventory_transfer",
    )
    assert by_kind["select_dialogue_option"].research == (
        "game_sources/research/dialogue_options",
    )


def test_only_operations_with_a_recorded_live_run_claim_live_proof() -> None:
    """Updated deliberately, which is what the characterization asked for.

    Historical generic `perform_character_order` dispatches remain only
    source-proven, while the exact r7 bundle now proves its PLAYER_TALK_TO
    subcase through later Barman dialogue. The same bundle proves the timed
    survey lifecycle, native interface close, and native pause. The
    2026-08-08 closure runs prove the paired-window and item-transfer route
    against an unconscious body and a resource output. The 2026-08-09 resource
    operator run proves exact native admission independently from selection and
    queued work. The 2026-08-10 mine-to-sale bundle additionally proves the
    current productive monitor, native clock control while it remains active,
    and the exact two-character map-travel leg to The Hub.

    So the assertion is now an allowlist rather than a zero. Any other
    operation claiming live proof has to be added here by someone who has the
    run to point at.
    """

    audit = audit_interaction_catalog()

    live_proven = {
        entry.operation_kind
        for entry in audit.entries
        if entry.proof_status == "live_proven"
    }

    assert live_proven == {
        "open_trade_window",
        "close_active_interface",
        "pause",
        "perform_character_order",
        "perform_context_action",
        "produce_resource_output",
        "select_dialogue_option",
        "set_speed",
        "survey_local_resources",
        "travel_to_map_destination",
        "transfer_item",
    }


def test_native_mine_to_sale_evidence_preserves_the_complete_proof_chain() -> None:
    artifact = json.loads(
        (MANIFEST_PATH.parent / "native_mining_local_trade_20260810.json").read_text(
            encoding="utf-8"
        )
    )

    assert artifact["manifest"]["producer_protocol"] == "2.0.0"
    assert artifact["manifest"]["steps_completed"] == 12
    assert artifact["binary_identity"]["live_candidate"][
        "built_preserved_installed_equal"
    ]
    commands = artifact["binary_identity"]["commands"]
    assert "replacement\\KenshiAgentTelemetry.dll" in commands[
        "reinstall_live_candidate"
    ]
    assert "pre-change\\KenshiAgentTelemetry.dll" in commands["rollback_pre_change"]

    chain = artifact["requests_and_acknowledgements"]
    assert all(entry.get("primitive_actions", 0) == 0 for entry in chain)
    terminals = {
        terminal["reason"]
        for entry in chain
        if (terminal := entry.get("terminal")) is not None
    }
    assert {
        "resource_output_ready_task_released",
        "trade_window_open",
        "item_transferred",
    } <= terminals

    decisive = {frame["sequence"]: frame for frame in artifact["decisive_telemetry"]}
    assert decisive[211]["copper_resource_output"][0]["quantity"] == 1
    assert decisive[231]["resource_item_count"] == 0
    assert decisive[320]["distance_to_barman"] == 16.73
    assert decisive[345]["slowline_copper"]["sell_value"] == 195
    assert decisive[362]["money"] - decisive[345]["money"] == 195
    assert decisive[362]["slowline_item_count"] == 2

    audit = artifact["planner_and_input_audit"]
    assert audit["generic_resource_operate_plans"] == 0
    assert audit["planner_wait_plans"] == 0
    assert audit["action_receipts_with_nonzero_primitive_actions"] == 0
    assert audit["final_cleanup"]["input_attempted"] is False
    assert audit["final_cleanup"]["input_executed"] is False
    assert artifact["final_disposition"]["result"] == "live_proven"
    assert all(len(entry["sha256"]) == 64 for entry in artifact["omitted_raw_files"])


def test_catalog_renders_the_contract_execution_and_native_routes() -> None:
    lines = render_interaction_catalog(audit_interaction_catalog())
    body = "\n".join(lines)

    assert "INTERACTION CONTRACT (resolved from the sole operation registry)" in body
    assert "EXECUTION AND ROUTING" in body
    assert "NATIVE COMMAND ROUTES" in body
    assert "coverage proof          PASS" in body


def test_generated_adapter_prose_describes_current_limits() -> None:
    adapters = {adapter.name: adapter for adapter in AFFORDANCE_ADAPTERS}

    transfers = adapters["item_transfers"].completeness_boundary
    assert "Uncapped" in transfers
    assert "simplified shop pricing" in transfers
    assert "richer trade and theft adjudication is not claimed" in transfers

    context_orders = adapters["context_orders"].completeness_boundary
    assert "Only squad-character first_aid" in context_orders
    assert "indefinite standing job" in context_orders
    assert "produce_resource_output" in context_orders
    assert "withheld" in context_orders
    assert "screen geometry" not in context_orders

    trade_windows = adapters["trade_windows"].completeness_boundary
    assert "greater-than-30-unit distance is withheld" in trade_windows
    assert "before rendering" in trade_windows
    assert "exact trade-range predicate" in trade_windows


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


def test_launch_only_routes_are_exactly_the_native_title_surface() -> None:
    from kenshi_agent.tooling.interaction_catalog import (
        LAUNCH_ONLY_NATIVE_COMMANDS,
        all_native_command_names,
    )

    assert LAUNCH_ONLY_NATIVE_COMMANDS == {
        "continue_game",
        "load_game",
        "new_game",
    }
    assert LAUNCH_ONLY_NATIVE_COMMANDS <= set(all_native_command_names())


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
