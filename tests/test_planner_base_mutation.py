from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import kenshi_agent.planners.base as planner_base
from kenshi_agent.core.continuity import (
    ContinuityOperationStatus,
    ContinuityOrigin,
    ContinuityReceiptDigest,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
)
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.planner_context import PlannerContextManifest
from kenshi_agent.core.telemetry import (
    CharacterState,
    TelemetrySnapshot,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.planners.base import planner_context_manifest, prepared_budgeted_input

_NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)
_TARGET_IDS = [f"entity-{index}" for index in range(1, 11)]


def _observation() -> Observation:
    current = Observation(
        run_id="manifest-run",
        step_index=0,
        mode="live",
        world_revision=WorldStateRevision(
            telemetry_sequence=17,
            frame_sequence=19,
            capability_epoch=3,
            observed_at_monotonic=23.0,
        ),
        telemetry=TelemetrySnapshot(
            sequence=17,
            squad=[
                CharacterState(id=target_id, name=f"Character {index}")
                for index, target_id in enumerate(_TARGET_IDS, start=1)
            ],
        ),
        memories=[
            MemoryRecord(
                memory_id="mem-visible",
                campaign_id="manifest",
                kind=MemoryKind.FACT,
                status=MemoryStatus.ACTIVE,
                content="Visible fact.",
                salience=0.5,
                created_run_id="manifest-run",
                created_at=_NOW,
            )
        ],
        recent_continuity_receipts=[
            ContinuityReceiptDigest(
                receipt_id="cor-" + "1" * 32,
                origin=ContinuityOrigin.PLAN,
                operation="keep",
                status=ContinuityOperationStatus.ACCEPTED,
                reason="The cited memory was retained.",
                memory_id="mem-receipt",
                memory_status=MemoryStatus.ACTIVE,
                authored_context_id="pc-1",
                authored_revision=WorldStateRevision(telemetry_sequence=16),
                commit_revision=WorldStateRevision(telemetry_sequence=17),
                recorded_at=_NOW,
            )
        ],
    ).model_copy(
        update={
            "recent_action_outcomes": [SimpleNamespace(outcome_id="ao-2")],
            "recent_plan_outcomes": [SimpleNamespace(plan_outcome_id="po-2")],
            "memory_search": SimpleNamespace(
                receipt_id="mrr-" + "1" * 32,
                records=[
                    MemoryRecord(
                        memory_id="mem-search",
                        campaign_id="manifest",
                        kind=MemoryKind.HYPOTHESIS,
                        status=MemoryStatus.ACTIVE,
                        content="Searched hypothesis.",
                        salience=0.4,
                        created_run_id="manifest-run",
                        created_at=_NOW,
                    )
                ],
                action_outcomes=[SimpleNamespace(outcome_id="ao-1")],
                plan_outcomes=[SimpleNamespace(plan_outcome_id="po-1")],
            ),
            "fieldbook_projects": [
                SimpleNamespace(project_id="fbp-" + "1" * 32)
            ],
            "active_fieldbook_project": SimpleNamespace(
                project_id="fbp-" + "2" * 32
            ),
            "recent_fieldbook_receipts": [
                SimpleNamespace(
                    receipt_id="fbor-" + "1" * 32,
                    project_id="fbp-" + "3" * 32,
                    entry_id="fbe-" + "3" * 32,
                )
            ],
            "fieldbook_read": SimpleNamespace(
                receipt_id="fbr-" + "1" * 32,
                project_ids=["fbp-" + "4" * 32],
                entry_ids=["fbe-" + "4" * 32],
            ),
            "advisor": SimpleNamespace(
                latest_brief=SimpleNamespace(brief_id="advisor-" + "a" * 32)
            ),
        }
    )
    assert current.current_memory_target_ids() == set(_TARGET_IDS)
    return current


def _freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDatetime:
        @classmethod
        def now(cls, timezone: object) -> datetime:
            assert timezone is UTC
            return _NOW

    monkeypatch.setattr(planner_base, "datetime", FixedDatetime)


def test_full_manifest_is_the_exact_delivered_identity_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_clock(monkeypatch)
    current = _observation()

    manifest = planner_context_manifest(
        current,
        context_id="pc-2",
        input_kind="full_observation",
    )

    assert manifest == PlannerContextManifest(
        context_id="pc-2",
        run_id="manifest-run",
        authored_revision=current.world_revision,
        current_observation_delivered=True,
        telemetry_was_fresh=True,
        input_kind="full_observation",
        current_target_ids=sorted(_TARGET_IDS),
        action_outcome_ids=["ao-1", "ao-2"],
        plan_outcome_ids=["po-1", "po-2"],
        memory_ids=["mem-receipt", "mem-search", "mem-visible"],
        continuity_receipt_ids=["cor-" + "1" * 32],
        memory_read_receipt_ids=["mrr-" + "1" * 32],
        fieldbook_project_ids=[
            "fbp-" + "1" * 32,
            "fbp-" + "2" * 32,
            "fbp-" + "3" * 32,
            "fbp-" + "4" * 32,
        ],
        fieldbook_entry_ids=[
            "fbe-" + "3" * 32,
            "fbe-" + "4" * 32,
        ],
        fieldbook_receipt_ids=["fbor-" + "1" * 32],
        fieldbook_read_receipt_ids=["fbr-" + "1" * 32],
        advisor_brief_ids=["advisor-" + "a" * 32],
        candidate_memory_count=3,
        payload_characters=None,
        created_at=_NOW,
    )


def test_budgeted_manifest_is_the_exact_final_json_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_clock(monkeypatch)
    current = _observation()
    payload = {
        "world_revision": {"telemetry_sequence": 17},
        "telemetry": {
            "squad": [{"id": "entity-1"}, {"id": 3}, "invalid"],
            "nearby_entities": [{"id": "entity-2"}],
            "world_targets": [{"id": "entity-3"}],
            "ui": {
                "dialogue_target_id": "entity-4",
                "selected_character_id": "entity-5",
                "context_inventory_target_id": "entity-6",
                "selected_character_ids": ["entity-7", 8],
            },
        },
        "affordances": [
            {"target": {"target_id": "entity-8"}},
            {"target": {"target_id": "entity-9"}},
            {"target": {"target_id": "entity-10"}},
            {"target": {"target_id": "not-current"}},
            {"target": None},
        ],
        "recent_action_outcomes": [{"outcome_id": "ao-4"}],
        "recent_plan_outcomes": [{"plan_outcome_id": "po-4"}],
        "memories": [{"memory_id": "mem-payload"}],
        "memory_search": {
            "receipt_id": "mrr-" + "2" * 32,
            "records": [{"memory_id": "mem-payload-search"}],
            "action_outcomes": [{"outcome_id": "ao-3"}],
            "plan_outcomes": [{"plan_outcome_id": "po-3"}],
        },
        "recent_continuity_receipts": [
            {
                "receipt_id": "cor-" + "2" * 32,
                "memory_id": "mem-payload-receipt",
            },
            {"receipt_id": 4, "memory_id": 5},
            "invalid",
        ],
        "fieldbook_projects": [
            {"project_id": "fbp-" + "5" * 32},
            {"project_id": 6},
        ],
        "active_fieldbook_project": {
            "project_id": "fbp-" + "6" * 32,
        },
        "fieldbook_read": {
            "receipt_id": "fbr-" + "2" * 32,
            "project": {"project_id": "fbp-" + "7" * 32},
            "entries": [
                {
                    "project_id": "fbp-" + "7" * 32,
                    "entry_id": "fbe-" + "7" * 32,
                }
            ],
        },
        "recent_fieldbook_receipts": [
            {
                "receipt_id": "fbor-" + "2" * 32,
                "project_id": "fbp-" + "8" * 32,
                "entry_id": "fbe-" + "8" * 32,
            }
        ],
        "advisor": {
            "latest_brief": {"brief_id": "advisor-" + "b" * 32},
        },
    }

    manifest = planner_context_manifest(
        current,
        context_id="pc-3",
        input_kind="budgeted_json",
        payload=payload,
        payload_characters=1234,
    )

    assert manifest == PlannerContextManifest(
        context_id="pc-3",
        run_id="manifest-run",
        authored_revision=current.world_revision,
        current_observation_delivered=True,
        telemetry_was_fresh=True,
        input_kind="budgeted_json",
        current_target_ids=sorted(_TARGET_IDS),
        action_outcome_ids=["ao-3", "ao-4"],
        plan_outcome_ids=["po-3", "po-4"],
        memory_ids=[
            "mem-payload",
            "mem-payload-receipt",
            "mem-payload-search",
        ],
        continuity_receipt_ids=["cor-" + "2" * 32],
        memory_read_receipt_ids=["mrr-" + "2" * 32],
        fieldbook_project_ids=[
            "fbp-" + "5" * 32,
            "fbp-" + "6" * 32,
            "fbp-" + "7" * 32,
            "fbp-" + "8" * 32,
        ],
        fieldbook_entry_ids=[
            "fbe-" + "7" * 32,
            "fbe-" + "8" * 32,
        ],
        fieldbook_receipt_ids=["fbor-" + "2" * 32],
        fieldbook_read_receipt_ids=["fbr-" + "2" * 32],
        advisor_brief_ids=["advisor-" + "b" * 32],
        candidate_memory_count=3,
        payload_characters=1234,
        created_at=_NOW,
    )


def test_scripted_manifest_discloses_no_observation_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_clock(monkeypatch)
    current = _observation()

    manifest = planner_context_manifest(
        current,
        context_id="pc-4",
        input_kind="scripted",
        payload={"world_revision": {"telemetry_sequence": 17}},
        payload_characters=99,
    )

    assert manifest == PlannerContextManifest(
        context_id="pc-4",
        run_id="manifest-run",
        authored_revision=current.world_revision,
        current_observation_delivered=False,
        telemetry_was_fresh=True,
        input_kind="scripted",
        current_target_ids=[],
        action_outcome_ids=[],
        plan_outcome_ids=[],
        memory_ids=[],
        continuity_receipt_ids=[],
        memory_read_receipt_ids=[],
        advisor_brief_ids=[],
        candidate_memory_count=3,
        payload_characters=99,
        created_at=_NOW,
    )


def test_manifest_freshness_fails_closed() -> None:
    current = _observation()

    assert planner_context_manifest(
        current,
        context_id="pc-5",
        input_kind="scripted",
    ).telemetry_was_fresh
    assert not planner_context_manifest(
        current.model_copy(update={"telemetry_stale": True}),
        context_id="pc-6",
        input_kind="scripted",
    ).telemetry_was_fresh
    assert not planner_context_manifest(
        current.model_copy(update={"telemetry": None}),
        context_id="pc-7",
        input_kind="scripted",
    ).telemetry_was_fresh


def test_payload_identity_extractors_reject_malformed_and_historical_shapes() -> None:
    current = _observation()
    payload = {
        "telemetry": {
            "squad": [{"id": "entity-1"}, {"id": 4}, None],
            "nearby_entities": [{"id": "entity-2"}],
            "world_targets": [{"id": "entity-3"}],
            "ui": {
                "dialogue_target_id": "entity-4",
                "selected_character_id": "entity-5",
                "context_inventory_target_id": "entity-6",
                "selected_character_ids": ["entity-7", None],
            },
        },
        "affordances": [
            {"target": {"target_id": "entity-8"}},
            {"target": {"target_id": "entity-9"}},
            {"target": {"target_id": "entity-10"}},
            {"target": {"target_id": "historical-only"}},
            {"target": {"target_id": 12}},
            "invalid",
        ],
    }

    assert planner_base._payload_target_ids(payload, current) == set(_TARGET_IDS)
    assert planner_base._string_ids(
        [{"identity": "one"}, {"identity": 2}, None],
        "identity",
    ) == {"one"}
    assert planner_base._string_ids(None, "identity") == set()
    assert planner_base._payload_target_ids(
        {
            "telemetry": [],
            "dialogue_targets": None,
            "travel_destinations": "invalid",
            "context_targets": {},
        },
        current,
    ) == set()


def test_budgeted_input_rejects_every_non_object_with_exact_diagnostic() -> None:
    for payload in ("null", "[]", '"text"', "4"):
        with pytest.raises(
            ValueError,
            match=r"^Planner observation payload must be one JSON object\.$",
        ):
            prepared_budgeted_input(
                Observation(run_id="input", step_index=0, mode="mock"),
                context_id="pc-1",
                payload=payload,
            )
