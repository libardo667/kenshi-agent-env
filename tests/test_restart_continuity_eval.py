"""A real process restart must conserve authority without leaking identity."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime, tzinfo
from pathlib import Path

import pytest

from kenshi_agent.tooling.evals import restart_continuity as restart_eval
from kenshi_agent.tooling.evals.restart_continuity import (
    BACKGROUND_COMMITMENT,
    CAMPAIGN_ID,
    COMMITMENT_CONTENT,
    OPEN_HYPOTHESIS,
    RestartTreatment,
    _metrics,
    _phase_one,
    _phase_two,
    main,
    run_restart_evaluation,
)


@pytest.fixture(scope="module")
def process_evaluation_bundle(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, object]:
    output = (
        tmp_path_factory.mktemp("restart-eval")
        / "missing-parent"
        / "evidence"
    )
    return run_restart_evaluation(output)


@pytest.fixture(scope="module")
def evaluation_bundle(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("restart-eval-direct")
    treatments: dict[str, object] = {}
    for treatment in RestartTreatment:
        directory = root / treatment.value
        directory.mkdir()
        database = directory / "continuity.sqlite3"
        first = _phase_one(
            treatment=treatment,
            database_path=database,
            campaign_id=CAMPAIGN_ID,
        )
        second = _phase_two(
            treatment=treatment,
            database_path=database,
            campaign_id=CAMPAIGN_ID,
        )
        treatments[treatment.value] = {
            "phase_one": first,
            "phase_two": second,
            "metrics": _metrics(
                phase_one=first,
                phase_two=second,
            ),
        }
    return {
        "schema_version": 1,
        "evidence_level": "synthetic_portable",
        "retrieval_policy": "deterministic",
        "cargo": {
            "item_name": "Sealed Cargo",
            "quantity": 6,
        },
        "treatments": treatments,
        "comparison": {
            "semantic_retrieval": "not_available_in_this_build",
        },
        "claims": [
            "This is synthetic portable evidence.",
            "It proves continuity authority across a real process restart.",
            "It does not prove live Kenshi control or general game competence.",
        ],
    }


def _treatment(
    bundle: dict[str, object],
    treatment: RestartTreatment,
) -> dict[str, object]:
    treatments = bundle["treatments"]
    assert isinstance(treatments, dict)
    result = treatments[treatment.value]
    assert isinstance(result, dict)
    return result


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def _integer(value: object) -> int:
    assert isinstance(value, int)
    return value


_RUNTIME_ID = re.compile(
    r"\b(?P<prefix>cor|mem|fbp|fbe|fbor|fbr)-[0-9a-f]{32}\b"
)
def _normalized_evidence(value: object) -> object:
    """Preserve the complete contract while replacing runtime-owned entropy."""

    def replace_runtime_ids(text: str) -> str:
        return _RUNTIME_ID.sub(
            lambda match: f"<{match.group('prefix')}-id>",
            text,
        )

    def visit(item: object, *, key: str | None = None) -> object:
        if key == "pid":
            return "<pid>"
        if key == "evidence_path":
            return "<evidence-path>"
        if key is not None and key.endswith("_at"):
            return "<runtime-time>"
        if isinstance(item, dict):
            return {
                replace_runtime_ids(child_key): visit(
                    item[child_key],
                    key=child_key,
                )
                for child_key in sorted(item)
            }
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, str):
            return replace_runtime_ids(item)
        return item

    return visit(value)


_PHASE_ONE_KEYS = {
    "action_outcomes",
    "campaign_id",
    "canonical_state",
    "commitment",
    "continuity_receipts",
    "correction_after_rejection",
    "fieldbook_project_ids",
    "fieldbook_route_entry_ids",
    "manifests",
    "observations",
    "old_target_memory_id",
    "phase",
    "pid",
    "plans",
    "rejected_resolution",
    "run_id",
    "treatment",
}
_PHASE_TWO_KEYS = {
    "campaign_id",
    "canonical_state",
    "continuity_receipts",
    "delivery",
    "elective_fieldbook_read",
    "identity_boundary",
    "manifests",
    "observations",
    "other_campaign",
    "phase",
    "pid",
    "planner_payload_characters",
    "replay",
    "replay_log_name",
    "resolution",
    "restart_context",
    "run_id",
    "stale_note_correction",
    "treatment",
}
_METRIC_KEYS = {
    "correction_after_rejection",
    "cross_campaign_leaks",
    "eventual_delivery_status",
    "evidence_reference_rejection_rate",
    "evidence_reference_rejections",
    "exact_delivered_memory_counts",
    "fieldbook_reads",
    "planner_payload_characters",
    "repeated_no_ops",
    "restart_continuity",
    "resumed_commitments",
    "stale_memory_corrections",
    "unsupported_success_claims",
}


def _assert_treatment_contract(bundle: dict[str, object]) -> None:
    treatments = _mapping(bundle["treatments"])
    assert set(treatments) == {treatment.value for treatment in RestartTreatment}
    for treatment in RestartTreatment:
        result = _mapping(treatments[treatment.value])
        assert set(result) == {"phase_one", "phase_two", "metrics"}
        assert set(_mapping(result["phase_one"])) == _PHASE_ONE_KEYS
        assert set(_mapping(result["phase_two"])) == _PHASE_TWO_KEYS
        assert set(_mapping(result["metrics"])) == _METRIC_KEYS


def test_complete_evidence_contract_is_observable(
    evaluation_bundle: dict[str, object],
) -> None:
    assert set(evaluation_bundle) == {
        "schema_version",
        "evidence_level",
        "retrieval_policy",
        "cargo",
        "treatments",
        "comparison",
        "claims",
    }
    _assert_treatment_contract(evaluation_bundle)


def test_real_process_bundle_matches_the_complete_contract(
    process_evaluation_bundle: dict[str, object],
    evaluation_bundle: dict[str, object],
) -> None:
    assert set(process_evaluation_bundle) == {
        "schema_version",
        "evidence_level",
        "generated_at",
        "campaign_id",
        "retrieval_policy",
        "cargo",
        "treatments",
        "comparison",
        "claims",
        "artifact_files",
        "evidence_path",
    }
    _assert_treatment_contract(process_evaluation_bundle)
    assert _normalized_evidence(
        process_evaluation_bundle["treatments"]
    ) == _normalized_evidence(evaluation_bundle["treatments"])


def test_restart_evaluation_uses_distinct_processes_and_one_campaign(
    process_evaluation_bundle: dict[str, object],
) -> None:
    evaluation_bundle = process_evaluation_bundle
    assert evaluation_bundle["schema_version"] == 1
    assert evaluation_bundle["evidence_level"] == "synthetic_portable"
    assert evaluation_bundle["retrieval_policy"] == "deterministic"
    assert evaluation_bundle["cargo"] == {
        "item_name": "Sealed Cargo",
        "quantity": 6,
    }
    assert set(_mapping(evaluation_bundle["treatments"])) == {
        treatment.value for treatment in RestartTreatment
    }

    for treatment in RestartTreatment:
        result = _treatment(evaluation_bundle, treatment)
        first = _mapping(result["phase_one"])
        second = _mapping(result["phase_two"])
        assert first["campaign_id"] == "ladle-restart-eval"
        assert second["campaign_id"] == first["campaign_id"]
        assert first["run_id"] != second["run_id"]
        assert first["pid"] != second["pid"]
        assert first["pid"] != os.getpid()
        assert second["pid"] != os.getpid()


def test_process_two_receives_only_the_exact_unresolved_continuity(
    evaluation_bundle: dict[str, object],
) -> None:
    result = _treatment(
        evaluation_bundle,
        RestartTreatment.MEMORY_PLUS_FIELDBOOK,
    )
    first = _mapping(result["phase_one"])
    second = _mapping(result["phase_two"])

    commitment = _mapping(first["commitment"])
    assert commitment["kind"] == "commitment"
    assert commitment["content"] == (
        "Deliver exactly 6 Sealed Cargo units to the destination."
    )
    assert commitment["status"] == "active"

    start = _mapping(second["restart_context"])
    manifest = _mapping(start["manifest"])
    assert start["commitment_status"] == "active"
    assert manifest["memory_ids"] == [commitment["memory_id"]]
    assert len(_sequence(manifest["fieldbook_project_ids"])) == 1
    assert start["fieldbook_index_count"] == 1
    assert start["fieldbook_index_truncated"] is True
    assert start["recall_tiers"] == {
        commitment["memory_id"]: "commitment",
    }
    assert start["recall_omitted"] == {
        "commitment": 1,
        "current_target": 0,
        "open_hypothesis": 1,
        "general": 1,
    }

    identity = _mapping(second["identity_boundary"])
    assert identity == {
        "old_entity_id": "entity-quartermaster-old",
        "new_entity_id": "entity-quartermaster-new",
        "shared_name": "Quartermaster",
        "old_target_memory_id": first["old_target_memory_id"],
        "old_target_memory_recalled": False,
    }
    other = _mapping(second["other_campaign"])
    assert other["memory_ids"] == []
    assert other["fieldbook_project_ids"] == []
    assert other["checked"] is True


def test_runtime_owned_ids_remain_referentially_exact(
    evaluation_bundle: dict[str, object],
) -> None:
    for treatment in RestartTreatment:
        result = _treatment(evaluation_bundle, treatment)
        first = _mapping(result["phase_one"])
        second = _mapping(result["phase_two"])
        first_manifests = [
            _mapping(manifest) for manifest in _sequence(first["manifests"])
        ]
        restart_manifest = _mapping(
            _mapping(second["restart_context"])["manifest"]
        )
        delivery = _mapping(second["delivery"])
        delivery_manifest = _mapping(delivery["manifest"])
        commitment = _mapping(first["commitment"])
        old_target_memory_id = first["old_target_memory_id"]

        assert first_manifests[0]["action_outcome_ids"] == ["ao-1", "ao-2"]
        assert delivery_manifest["action_outcome_ids"] == [
            delivery["outcome_id"]
        ]
        assert _mapping(second["replay"])["delivery_outcome_ids"] == [
            delivery["outcome_id"]
        ]

        if treatment is RestartTreatment.CONTINUITY_DISABLED:
            assert commitment["memory_id"] is None
            assert old_target_memory_id is None
            assert restart_manifest["memory_ids"] == []
            assert delivery_manifest["memory_ids"] == []
            continue

        commitment_id = commitment["memory_id"]
        assert isinstance(commitment_id, str)
        assert isinstance(old_target_memory_id, str)
        kept_receipts = [
            _mapping(receipt)
            for receipt in _sequence(first["continuity_receipts"])[:4]
        ]
        assert set(_sequence(first_manifests[1]["memory_ids"])) == {
            receipt["memory_id"] for receipt in kept_receipts
        }
        assert commitment_id in _sequence(first_manifests[1]["memory_ids"])
        assert old_target_memory_id in _sequence(first_manifests[1]["memory_ids"])
        assert restart_manifest["memory_ids"] == [commitment_id]
        assert delivery_manifest["memory_ids"] == [commitment_id]
        assert old_target_memory_id not in _sequence(
            restart_manifest["memory_ids"]
        )

        rejected = _mapping(first["rejected_resolution"])
        corrected = _mapping(first["correction_after_rejection"])
        assert first_manifests[-1]["continuity_receipt_ids"] == [
            rejected["receipt_id"]
        ]
        assert corrected["cites_receipt_id"] == rejected["receipt_id"]
        assert corrected["memory_id"] not in {
            commitment_id,
            old_target_memory_id,
        }

        if treatment is RestartTreatment.MEMORY_PLUS_FIELDBOOK:
            project_ids = _sequence(first["fieldbook_project_ids"])
            entry_ids = _sequence(first["fieldbook_route_entry_ids"])
            read = _mapping(second["elective_fieldbook_read"])
            read_manifest = _mapping(read["next_manifest"])
            assert len(set(project_ids)) == 2
            assert len(set(entry_ids)) == 2
            assert set(_sequence(first_manifests[-1]["fieldbook_project_ids"])) == set(
                project_ids
            )
            assert set(_sequence(first_manifests[-1]["fieldbook_entry_ids"])) == set(
                entry_ids
            )
            assert _sequence(read["project_ids"])[0] in project_ids
            assert _sequence(read["entry_ids"])[0] in entry_ids
            assert read_manifest["fieldbook_read_receipt_ids"] == [
                read["receipt_id"]
            ]
            assert read_manifest["fieldbook_entry_ids"] == read["entry_ids"]


def test_elective_read_and_current_telemetry_override_the_old_note(
    evaluation_bundle: dict[str, object],
) -> None:
    result = _treatment(
        evaluation_bundle,
        RestartTreatment.MEMORY_PLUS_FIELDBOOK,
    )
    second = _mapping(result["phase_two"])
    read = _mapping(second["elective_fieldbook_read"])
    assert read["status"] == "completed"
    assert read["controller_primitives"] == 0
    assert read["world_command_created"] is False
    assert read["matched"] == 2
    assert read["truncated"] is True
    assert len(_sequence(read["entry_ids"])) == 1
    read_manifest = _mapping(read["next_manifest"])
    assert read_manifest["fieldbook_read_receipt_ids"] == [read["receipt_id"]]
    assert read_manifest["fieldbook_entry_ids"] == read["entry_ids"]

    correction = _mapping(second["stale_note_correction"])
    assert correction == {
        "old_note_location": "Old Yard",
        "current_telemetry_location": "New Yard",
        "winning_source": "current_observation",
        "status": "accepted",
        "memory_id": correction["memory_id"],
    }
    assert isinstance(correction["memory_id"], str)


def test_rejection_changes_the_next_operation_and_delivery_alone_closes(
    evaluation_bundle: dict[str, object],
) -> None:
    result = _treatment(
        evaluation_bundle,
        RestartTreatment.MEMORY_PLUS_FIELDBOOK,
    )
    first = _mapping(result["phase_one"])
    second = _mapping(result["phase_two"])

    rejected = _mapping(first["rejected_resolution"])
    corrected = _mapping(first["correction_after_rejection"])
    assert rejected["operation"] == "resolve"
    assert rejected["status"] == "rejected"
    assert rejected["evidence_authority"] == "attempt_no_op"
    assert corrected["operation"] == "keep"
    assert corrected["status"] == "accepted"
    assert corrected["cites_receipt_id"] == rejected["receipt_id"]
    assert corrected["repeated_rejected_operation"] is False

    delivery = _mapping(second["delivery"])
    assert delivery["transfer_status"] == "transferred"
    assert delivery["source_quantity_before"] == 6
    assert delivery["source_quantity_after"] == 0
    assert delivery["destination_quantity_before"] == 0
    assert delivery["destination_quantity_after"] == 6
    assert delivery["controller_verified"] is True

    resolution = _mapping(second["resolution"])
    assert resolution["status"] == "accepted"
    assert resolution["memory_status"] == "resolved"
    assert resolution["cited_outcome_id"] == delivery["outcome_id"]
    assert resolution["evidence_authority"] == "verified_world_effect"
    assert resolution["commitment_was_active_before_delivery"] is True


def test_canonical_state_preserves_exact_operation_and_evidence_provenance(
    evaluation_bundle: dict[str, object],
) -> None:
    result = _treatment(
        evaluation_bundle,
        RestartTreatment.MEMORY_PLUS_FIELDBOOK,
    )
    first = _mapping(result["phase_one"])
    second = _mapping(result["phase_two"])
    first_state = _mapping(first["canonical_state"])
    second_state = _mapping(second["canonical_state"])
    first_records = {
        _mapping(record)["content"]: _mapping(record)
        for record in _sequence(first_state["memory_records"])
    }
    second_records = {
        _mapping(record)["content"]: _mapping(record)
        for record in _sequence(second_state["memory_records"])
    }

    assert set(first_records) == {
        BACKGROUND_COMMITMENT,
        COMMITMENT_CONTENT,
        OPEN_HYPOTHESIS,
        "Quartermaster was observed at Old Yard before the restart.",
        "The first delivery attempt was a no-op; the commitment remains open.",
    }
    assert first_records[BACKGROUND_COMMITMENT]["status"] == "active"
    assert first_records[OPEN_HYPOTHESIS]["kind"] == "hypothesis"
    assert first_records[OPEN_HYPOTHESIS]["status"] == "active"

    episode = first_records[
        "The first delivery attempt was a no-op; the commitment remains open."
    ]
    episode_provenance = _mapping(episode["latest_provenance"])
    assert episode_provenance["authored_context_id"] == "pc-3"
    assert episode_provenance["plan_id"] == "attempt-delivery"
    assert episode_provenance["step_id"] == "record-no-op"
    assert episode_provenance["references"] == [
        {"source": "action_outcome", "outcome_id": "ao-1"}
    ]
    assert _mapping(
        _sequence(episode_provenance["resolved_evidence"])[0]
    )["authority"] == "attempt_no_op"

    entries = {
        _mapping(entry)["kind"]: _mapping(entry)
        for entry in _sequence(first_state["fieldbook_entries"])
    }
    assert set(entries) == {"route_entry", "incident"}
    incident_provenance = _mapping(entries["incident"]["provenance"])
    assert incident_provenance["step_id"] == "record-route"
    incident_evidence = _mapping(
        _sequence(incident_provenance["resolved_evidence"])[0]
    )
    assert incident_evidence["source_id"] == "ao-2"
    assert incident_evidence["authority"] == "attempt_unknown"

    resolved = second_records[COMMITMENT_CONTENT]
    assert resolved["status"] == "resolved"
    resolved_provenance = _mapping(resolved["latest_provenance"])
    assert resolved_provenance["plan_id"] == "resume-delivery"
    assert resolved_provenance["step_id"] == "close-after-transfer"
    closure_evidence = _mapping(
        _sequence(resolved_provenance["resolved_evidence"])[0]
    )
    assert closure_evidence["source_id"] == "ao-1"
    assert closure_evidence["authority"] == "verified_world_effect"
    assert closure_evidence["semantic_status"] == "transferred"

    assert {
        _mapping(entry)["entry_id"]
        for entry in _sequence(first_state["fieldbook_entries"])
    } == {
        _mapping(entry)["entry_id"]
        for entry in _sequence(second_state["fieldbook_entries"])
    }


def test_comparison_reports_effects_without_claiming_semantic_or_live_evidence(
    evaluation_bundle: dict[str, object],
) -> None:
    disabled = _mapping(
        _treatment(
            evaluation_bundle,
            RestartTreatment.CONTINUITY_DISABLED,
        )["metrics"]
    )
    memory = _mapping(
        _treatment(
            evaluation_bundle,
            RestartTreatment.LIFECYCLE_MEMORY,
        )["metrics"]
    )
    fieldbook = _mapping(
        _treatment(
            evaluation_bundle,
            RestartTreatment.MEMORY_PLUS_FIELDBOOK,
        )["metrics"]
    )

    assert disabled["resumed_commitments"] == 0
    assert disabled["restart_continuity"] is False
    assert disabled["fieldbook_reads"] == 0
    assert memory["resumed_commitments"] == 1
    assert memory["restart_continuity"] is True
    assert memory["fieldbook_reads"] == 0
    assert fieldbook["resumed_commitments"] == 1
    assert fieldbook["restart_continuity"] is True
    assert fieldbook["fieldbook_reads"] == 1
    assert (
        _integer(disabled["planner_payload_characters"])
        < _integer(memory["planner_payload_characters"])
        < _integer(fieldbook["planner_payload_characters"])
    )

    for metrics in (disabled, memory, fieldbook):
        assert metrics["repeated_no_ops"] == 0
        assert metrics["unsupported_success_claims"] == 0
        assert metrics["cross_campaign_leaks"] == 0
        assert metrics["eventual_delivery_status"] == "transferred"
    assert fieldbook["stale_memory_corrections"] == 1
    assert fieldbook["correction_after_rejection"] == 1
    assert _mapping(evaluation_bundle["comparison"])[
        "semantic_retrieval"
    ] == "not_available_in_this_build"
    assert evaluation_bundle["claims"] == [
        "This is synthetic portable evidence.",
        "It proves continuity authority across a real process restart.",
        "It does not prove live Kenshi control or general game competence.",
    ]


def test_transfer_fixture_encodes_both_sides_of_quantity_conservation() -> None:
    before = restart_eval._transfer_observation(
        run_id="transfer-test",
        sequence=1,
        source_quantity=1,
        destination_quantity=0,
    )
    after = restart_eval._transfer_observation(
        run_id="transfer-test",
        sequence=2,
        source_quantity=0,
        destination_quantity=1,
    )
    assert before.telemetry is not None
    assert after.telemetry is not None
    assert [
        (control.item_name, control.item_quantity, control.section)
        for control in before.telemetry.ui.visible_controls
        if control.role == "item"
    ] == [("Sealed Cargo", 1, "out")]
    assert len(after.telemetry.ui.visible_controls) == 1
    assert after.telemetry.ui.visible_controls[0].role == "button"
    assert before.telemetry.roster[0].inventory == []
    assert [
        (item.item_name, item.item_quantity, item.section)
        for item in after.telemetry.roster[0].inventory
    ] == [("Sealed Cargo", 1, "main")]


def test_each_treatment_replays_the_exact_restart_sequence_without_input(
    evaluation_bundle: dict[str, object],
) -> None:
    for treatment in RestartTreatment:
        second = _mapping(_treatment(evaluation_bundle, treatment)["phase_two"])
        replay = _mapping(second["replay"])
        assert replay["status"] == "passed"
        assert replay["observation_count"] == 3
        assert replay["modes"] == ["replay", "replay", "replay"]
        assert replay["actions_executed"] == [False, False]
        assert replay["current_telemetry_location"] == "New Yard"
        restart_manifest = _mapping(
            _mapping(second["restart_context"])["manifest"]
        )
        assert replay["restart_memory_ids"] == restart_manifest["memory_ids"]
        assert replay["restart_fieldbook_project_ids"] == restart_manifest[
            "fieldbook_project_ids"
        ]
        assert replay["restart_fieldbook_read_receipt_ids"] == []
        assert replay["delivery_outcome_ids"] == [
            _mapping(second["delivery"])["outcome_id"]
        ]


def test_bundle_is_written_exactly_and_existing_evidence_is_not_overwritten(
    process_evaluation_bundle: dict[str, object],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    evaluation_bundle = process_evaluation_bundle
    output_path = Path(str(evaluation_bundle["evidence_path"]))
    assert json.loads(output_path.read_text(encoding="utf-8")) == evaluation_bundle

    existing = tmp_path_factory.mktemp("restart-eval-existing") / "evidence"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("operator evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_restart_evaluation(existing)
    assert marker.read_text(encoding="utf-8") == "operator evidence"


def test_process_bundle_contains_only_the_declared_evidence_tree(
    process_evaluation_bundle: dict[str, object],
) -> None:
    evidence_path = Path(str(process_evaluation_bundle["evidence_path"]))
    output = evidence_path.parent
    relative_files = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert relative_files == {
        "continuity_disabled/phase-one.json",
        "continuity_disabled/phase-two.json",
        "continuity_disabled/replay-events.jsonl",
        "lifecycle_memory/continuity.sqlite3",
        "lifecycle_memory/phase-one.json",
        "lifecycle_memory/phase-two.json",
        "lifecycle_memory/replay-events.jsonl",
        "memory_plus_fieldbook/continuity.sqlite3",
        "memory_plus_fieldbook/phase-one.json",
        "memory_plus_fieldbook/phase-two.json",
        "memory_plus_fieldbook/replay-events.jsonl",
        "evidence.json",
    }
    assert process_evaluation_bundle["artifact_files"] == sorted(relative_files)
    generated_at = str(process_evaluation_bundle["generated_at"])
    assert generated_at.endswith("+00:00")


def test_helper_boundaries_preserve_context_identity_and_exact_commitment(
    tmp_path: Path,
) -> None:
    observation = restart_eval._observation(
        run_id="helper-test",
        sequence=1,
        location="Helper Yard",
        entity_id="entity-helper",
    )
    context = restart_eval._context(observation, context_id="pc-1")
    assert context.observation is observation
    assert context.manifest.context_id == "pc-1"
    assert context.manifest.authored_revision == observation.world_revision

    ledger = restart_eval.ContinuityLedger(
        run_id="helper-test",
        action_outcome_limit=1,
    )
    authority = restart_eval._authority(
        run_id="helper-test",
        store=None,
        ledger=ledger,
        logger=restart_eval._DiscardLogger(),
    )
    assert authority.run_id == "helper-test"
    assert authority.store is None
    assert authority.ledger is ledger
    assert authority.advisor_brief_ids() == set()
    evaluation_ledger = restart_eval._evaluation_ledger("restart-ledger")
    assert evaluation_ledger.run_id == "restart-ledger"
    assert evaluation_ledger.action_outcome_limit == 8

    with restart_eval._open_store(
        tmp_path / "continuity.sqlite3",
        CAMPAIGN_ID,
    ) as store:
        cargo = store.keep(
            "helper-test",
            kind=restart_eval.MemoryKind.COMMITMENT,
            content=COMMITMENT_CONTENT,
            salience=1.0,
            grounding=None,
        )
        store.keep(
            "helper-test",
            kind=restart_eval.MemoryKind.FACT,
            content=COMMITMENT_CONTENT,
            salience=0.5,
            grounding="fixture",
        )
        store.keep(
            "helper-test",
            kind=restart_eval.MemoryKind.COMMITMENT,
            content="Deliver a different parcel.",
            salience=0.5,
            grounding=None,
        )
        exact_target = store.keep(
            "helper-test",
            kind=restart_eval.MemoryKind.FACT,
            content="Quartermaster runtime identity fixture.",
            salience=0.5,
            grounding="fixture",
            target_id=restart_eval.OLD_ENTITY_ID,
        )
        assert restart_eval._single_active_commitment(store) == cargo
        records = store.all_records()
        assert (
            restart_eval._single_exact_target_record(
                records,
                target_id=restart_eval.OLD_ENTITY_ID,
            )
            == exact_target
        )
        with pytest.raises(restart_eval.RestartEvaluationError):
            restart_eval._single_exact_target_record(
                records,
                target_id="entity-not-present",
            )

    calls: list[tuple[object, object]] = []
    sentinel = object()

    class RecallStub:
        def recall_tiered(
            self,
            *,
            budget: object,
            target_ids: object,
        ) -> object:
            calls.append((budget, target_ids))
            return sentinel

    assert restart_eval._restart_recall(RecallStub()) is sentinel  # type: ignore[arg-type,return-value]
    budget, target_ids = calls[0]
    assert isinstance(budget, restart_eval.RecallBudget)
    assert (
        budget.commitments,
        budget.current_target,
        budget.open_hypotheses,
        budget.general,
    ) == (1, 1, 0, 0)
    assert target_ids == ["entity-quartermaster-new"]


def test_cross_campaign_guard_rejects_either_kind_of_leak() -> None:
    restart_eval._require_empty_cross_campaign(memory_ids=[], project_ids=[])
    with pytest.raises(restart_eval.RestartEvaluationError):
        restart_eval._require_empty_cross_campaign(
            memory_ids=["mem-leak"],
            project_ids=[],
        )
    with pytest.raises(restart_eval.RestartEvaluationError):
        restart_eval._require_empty_cross_campaign(
            memory_ids=[],
            project_ids=["fbp-leak"],
        )


def test_generated_timestamp_requests_utc_explicitly() -> None:
    requested: list[tzinfo] = []

    def fixed_now(zone: tzinfo) -> datetime:
        requested.append(zone)
        return datetime(2026, 7, 28, 12, 0, tzinfo=zone)

    assert restart_eval._generated_at(fixed_now) == "2026-07-28T12:00:00+00:00"
    assert requested == [UTC]


def test_metrics_count_real_reference_attempts_and_exact_correction_pair() -> None:
    phase_one: dict[str, object] = {
        "rejected_resolution": {
            "operation": "resolve",
            "status": "rejected",
            "receipt_id": "cor-rejected",
        },
        "correction_after_rejection": {
            "operation": "keep",
            "status": "accepted",
            "cites_receipt_id": "cor-rejected",
        },
        "continuity_receipts": [
            {
                "status": "accepted",
                "operation": {
                    "operation": "keep",
                    "references": [{"source": "current_observation"}],
                },
            },
            {
                "status": "rejected",
                "operation": {
                    "operation": "resolve",
                    "references": [{"source": "action_outcome"}],
                },
            },
            {
                "status": "no_op",
                "operation": {
                    "operation": "keep",
                    "references": [{"source": "current_observation"}],
                },
            },
        ],
    }
    phase_two: dict[str, object] = {
        "restart_context": {"commitment_status": "active"},
        "elective_fieldbook_read": {"status": "completed"},
        "stale_note_correction": {"status": "accepted"},
        "delivery": {"transfer_status": "transferred"},
        "other_campaign": {
            "memory_ids": ["leak-one"],
            "fieldbook_project_ids": ["leak-two"],
        },
        "planner_payload_characters": 123,
        "manifests": [
            {"memory_ids": ["mem-one"]},
            {"memory_ids": ["mem-one", "mem-two"]},
        ],
        "continuity_receipts": [],
    }
    metrics = _metrics(
        phase_one=phase_one,
        phase_two=phase_two,
    )
    assert metrics["evidence_reference_rejections"] == 1
    assert metrics["evidence_reference_rejection_rate"] == 0.5
    assert metrics["correction_after_rejection"] == 1
    assert metrics["cross_campaign_leaks"] == 2
    assert metrics["exact_delivered_memory_counts"] == [1, 2]

    _mapping(phase_one["correction_after_rejection"])[
        "cites_receipt_id"
    ] = "cor-unrelated"
    unpaired = _metrics(
        phase_one=phase_one,
        phase_two=phase_two,
    )
    assert unpaired["correction_after_rejection"] == 0

    _mapping(phase_one["correction_after_rejection"])[
        "cites_receipt_id"
    ] = "cor-rejected"
    _mapping(phase_one["correction_after_rejection"])["operation"] = "resolve"
    unchanged = _metrics(
        phase_one=phase_one,
        phase_two=phase_two,
    )
    assert unchanged["correction_after_rejection"] == 0

    phase_two["continuity_receipts"] = [{}, "not-a-receipt"]
    with pytest.raises(restart_eval.RestartEvaluationError):
        _metrics(
            phase_one=phase_one,
            phase_two=phase_two,
        )


@pytest.mark.parametrize(
    "phase_two",
    [
        {},
        {"manifests": [None]},
        {"manifests": [{}]},
        {"manifests": [{"memory_ids": "mem-one"}]},
    ],
)
def test_manifest_memory_counts_fail_closed_on_malformed_evidence(
    phase_two: dict[str, object],
) -> None:
    with pytest.raises(restart_eval.RestartEvaluationError):
        restart_eval._manifest_memory_counts(phase_two)


def test_public_main_routes_operator_and_worker_modes_without_reparsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operator_output = tmp_path / "operator"
    worker_output = tmp_path / "worker.json"
    database = tmp_path / "continuity.sqlite3"
    calls: list[tuple[str, object]] = []

    def fake_run(path: Path) -> dict[str, object]:
        calls.append(("operator", path))
        return {"evidence_path": str(path / "evidence.json")}

    def fake_worker(**kwargs: object) -> None:
        calls.append(("worker", kwargs))

    monkeypatch.setattr(restart_eval, "run_restart_evaluation", fake_run)
    monkeypatch.setattr(restart_eval, "_worker", fake_worker)

    assert main(["--output", str(operator_output)]) == 0
    assert capsys.readouterr().out.strip() == str(
        operator_output / "evidence.json"
    )
    assert main(
        [
            "--worker-phase",
            "one",
            "--treatment",
            RestartTreatment.LIFECYCLE_MEMORY.value,
            "--database",
            str(database),
            "--campaign",
            CAMPAIGN_ID,
            "--worker-output",
            str(worker_output),
        ]
    ) == 0
    assert calls == [
        ("operator", operator_output),
        (
            "worker",
            {
                "phase": "one",
                "treatment": RestartTreatment.LIFECYCLE_MEMORY,
                "database_path": database,
                "campaign_id": CAMPAIGN_ID,
                "output_path": worker_output,
            },
        ),
    ]
    with pytest.raises(SystemExit, match="--output is required"):
        main([])
    with pytest.raises(SystemExit, match="all hidden worker arguments"):
        main(["--worker-phase", "one"])


def test_worker_writes_each_phase_once_and_rejects_unknown_phase(
    tmp_path: Path,
) -> None:
    database = tmp_path / "continuity.sqlite3"
    phase_one_path = tmp_path / "nested" / "phase-one.json"
    phase_two_path = tmp_path / "phase-two.json"
    restart_eval._worker(
        phase="one",
        treatment=RestartTreatment.LIFECYCLE_MEMORY,
        database_path=database,
        campaign_id=CAMPAIGN_ID,
        output_path=phase_one_path,
    )
    restart_eval._worker(
        phase="two",
        treatment=RestartTreatment.LIFECYCLE_MEMORY,
        database_path=database,
        campaign_id=CAMPAIGN_ID,
        output_path=phase_two_path,
    )
    assert json.loads(phase_one_path.read_text(encoding="utf-8"))["phase"] == "one"
    assert json.loads(phase_two_path.read_text(encoding="utf-8"))["phase"] == "two"
    with pytest.raises(FileExistsError):
        restart_eval._worker(
            phase="one",
            treatment=RestartTreatment.LIFECYCLE_MEMORY,
            database_path=database,
            campaign_id=CAMPAIGN_ID,
            output_path=phase_one_path,
        )
    with pytest.raises(ValueError):
        restart_eval._worker(
            phase="unknown",
            treatment=RestartTreatment.LIFECYCLE_MEMORY,
            database_path=database,
            campaign_id=CAMPAIGN_ID,
            output_path=tmp_path / "unknown.json",
        )


def test_worker_invocation_rejects_failed_or_mislabeled_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "continuity.sqlite3"
    output = tmp_path / "phase.json"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def successful_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", successful_run)
    monkeypatch.setattr(
        restart_eval,
        "_read_json",
        lambda path: {
            "phase": "one",
            "treatment": RestartTreatment.LIFECYCLE_MEMORY.value,
        },
    )
    assert restart_eval._invoke_worker(
        python_executable="python-exact",
        treatment=RestartTreatment.LIFECYCLE_MEMORY,
        phase="one",
        database_path=database,
        output_path=output,
    ) == {
        "phase": "one",
        "treatment": RestartTreatment.LIFECYCLE_MEMORY.value,
    }
    assert calls == [
        (
            [
                "python-exact",
                "-m",
                "kenshi_agent.tooling.evals.restart_continuity",
                "--worker-phase",
                "one",
                "--treatment",
                "lifecycle_memory",
                "--database",
                str(database),
                "--campaign",
                CAMPAIGN_ID,
                "--worker-output",
                str(output),
            ],
            {
                "cwd": Path(restart_eval.__file__).resolve().parents[3],
                "text": True,
                "capture_output": True,
                "check": False,
            },
        )
    ]

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=4,
            stderr="worker failed",
        ),
    )
    with pytest.raises(restart_eval.RestartEvaluationError, match="worker failed"):
        restart_eval._invoke_worker(
            python_executable="python",
            treatment=RestartTreatment.LIFECYCLE_MEMORY,
            phase="one",
            database_path=database,
            output_path=output,
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
        ),
    )
    monkeypatch.setattr(
        restart_eval,
        "_read_json",
        lambda path: {
            "phase": "two",
            "treatment": RestartTreatment.LIFECYCLE_MEMORY.value,
        },
    )
    with pytest.raises(restart_eval.RestartEvaluationError, match="wrong phase"):
        restart_eval._invoke_worker(
            python_executable="python",
            treatment=RestartTreatment.LIFECYCLE_MEMORY,
            phase="one",
            database_path=database,
            output_path=output,
        )

    monkeypatch.setattr(
        restart_eval,
        "_read_json",
        lambda path: {
            "phase": "one",
            "treatment": RestartTreatment.CONTINUITY_DISABLED.value,
        },
    )
    with pytest.raises(
        restart_eval.RestartEvaluationError,
        match="wrong treatment",
    ):
        restart_eval._invoke_worker(
            python_executable="python",
            treatment=RestartTreatment.LIFECYCLE_MEMORY,
            phase="one",
            database_path=database,
            output_path=output,
        )
