"""Planner-context authority for private fieldbook transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kenshi_agent.campaign import CampaignScope, CampaignScopeOrigin
from kenshi_agent.continuity import ContinuityAuthority, ContinuityLedger
from kenshi_agent.fieldbook import FieldbookNoOp
from kenshi_agent.fieldbook_authority import FieldbookAuthority
from kenshi_agent.memory import MemoryStore
from kenshi_agent.models import (
    AdvisorBriefEvidence,
    AppendFieldbookEntryOperation,
    AuthoredPlannerContext,
    ContinuityOperationStatus,
    ContinuityOrigin,
    CreateFieldbookProjectOperation,
    CurrentObservationEvidence,
    EvidenceAuthority,
    FieldbookEntryKind,
    FieldbookLifecycleEvent,
    FieldbookProjectKind,
    FieldbookProjectStatus,
    MemoryEvidence,
    MemoryKind,
    Observation,
    PlanDisposition,
    PlannerContextManifest,
    PlanOutcomeEvidence,
    ResolvedEvidenceSnapshot,
    SelectFieldbookProjectOperation,
    SetFieldbookProjectStatusOperation,
    UpdateFieldbookSummaryOperation,
    WorldStateRevision,
)

BRIEF_ID = "advisor-" + "1" * 32


def open_store(path: Path) -> MemoryStore:
    return MemoryStore(
        path,
        CampaignScope(
            campaign_id="campaign-a",
            origin=CampaignScopeOrigin.CONFIGURED,
        ),
    )


def observation(*, run_id: str = "run-a") -> Observation:
    return Observation(
        run_id=run_id,
        step_index=3,
        mode="mock",
        world_revision=WorldStateRevision(
            telemetry_sequence=7,
            frame_sequence=5,
        ),
    )


def context(
    current: Observation,
    *,
    fieldbook_project_ids: list[str] | None = None,
    plan_outcome_ids: list[str] | None = None,
    memory_ids: list[str] | None = None,
    advisor_brief_ids: list[str] | None = None,
) -> AuthoredPlannerContext:
    return AuthoredPlannerContext(
        manifest=PlannerContextManifest(
            context_id="pc-1",
            run_id=current.run_id,
            authored_revision=current.world_revision,
            current_observation_delivered=True,
            telemetry_was_fresh=True,
            input_kind="full_observation",
            fieldbook_project_ids=fieldbook_project_ids or [],
            plan_outcome_ids=plan_outcome_ids or [],
            memory_ids=memory_ids or [],
            advisor_brief_ids=advisor_brief_ids or [],
        ),
        observation=current,
    )


def authority(
    store: MemoryStore | None,
    *,
    ledger: ContinuityLedger | None = None,
    brief_ids: set[str] | None = None,
) -> tuple[
    FieldbookAuthority,
    ContinuityAuthority,
    list[tuple[str, dict[str, Any]]],
]:
    events: list[tuple[str, dict[str, Any]]] = []

    def write(event_type: str, **kwargs: Any) -> None:
        events.append((event_type, kwargs))

    continuity = ContinuityAuthority(
        run_id="run-a",
        store=store,
        ledger=ledger
        or ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        logger=SimpleNamespace(write=write),
        advisor_brief_ids=lambda: brief_ids or set(),
    )
    return (
        FieldbookAuthority(continuity=continuity),
        continuity,
        events,
    )


def apply_one(
    engine: FieldbookAuthority,
    operation: Any,
    current: Observation,
    *,
    project_ids: list[str] | None = None,
) -> Any:
    return engine.apply(
        [operation],
        origin=ContinuityOrigin.PLAN,
        authored_context=context(
            current,
            fieldbook_project_ids=project_ids,
        ),
        commit_observation=current,
        plan_id="plan-a",
        plan_version=1,
    )[0]


def test_create_issues_runtime_identity_and_typed_logged_receipt(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        engine, _, events = authority(store)
        operation = CreateFieldbookProjectOperation(
            kind=FieldbookProjectKind.DELIVERY_DOCKET,
            title="Six-canister delivery",
            summary="Acquire and deliver six sealed canisters.",
        )
        authored = observation()
        commit = observation().model_copy(
            update={
                "step_index": 9,
                "world_revision": WorldStateRevision(
                    telemetry_sequence=11,
                    frame_sequence=8,
                ),
            }
        )

        receipt = engine.apply(
            [operation],
            origin=ContinuityOrigin.PATCH,
            authored_context=context(authored),
            commit_observation=commit,
            plan_id="plan-fieldbook",
            plan_version=3,
            step_id="create-project",
        )[0]

        assert receipt.status is ContinuityOperationStatus.ACCEPTED
        assert receipt.receipt_id.startswith("fbor-")
        assert receipt.project_id is not None
        assert receipt.project_id.startswith("fbp-")
        assert receipt.origin is ContinuityOrigin.PATCH
        assert receipt.operation == operation
        assert receipt.reason == (
            "create_project applied to fieldbook project "
            f"{receipt.project_id} (active)."
        )
        assert receipt.entry_id is None
        assert receipt.resolved_evidence == []
        assert receipt.plan_id == "plan-fieldbook"
        assert receipt.plan_version == 3
        assert receipt.step_id == "create-project"
        assert receipt.authored_context_id == "pc-1"
        assert receipt.authored_revision == authored.world_revision
        assert receipt.commit_revision == commit.world_revision
        assert not receipt.writes_degraded
        project = store.fieldbook.get_project(receipt.project_id)
        assert project is not None
        assert project.latest_provenance is not None
        assert project.latest_provenance.model_dump(mode="json") == {
            "schema_version": 1,
            "operation": operation.model_dump(mode="json"),
            "origin": "patch",
            "run_id": "run-a",
            "authored_context_id": "pc-1",
            "authored_revision": authored.world_revision.model_dump(mode="json"),
            "commit_revision": commit.world_revision.model_dump(mode="json"),
            "references": [],
            "resolved_evidence": [],
            "plan_id": "plan-fieldbook",
            "plan_version": 3,
            "step_id": "create-project",
            "rendered_grounding": None,
            "transition_result": "applied",
        }
        assert project.created_at.tzinfo is not None
        assert project.updated_at == project.created_at
        history = store.fieldbook.history(project.project_id)
        assert len(history) == 1
        assert history[0].entry_id is None
        assert set(history[0].payload) == {"project", "provenance"}
        assert history[0].payload["project"] == project.model_dump(mode="json")
        assert history[0].payload["provenance"] == (
            project.latest_provenance.model_dump(mode="json")
        )
        assert events[-1][0] == "fieldbook_receipt"
        assert events[-1][1] == {
            "step_index": 9,
            "payload": receipt.model_dump(mode="json"),
        }


def test_project_mutation_requires_identity_from_exact_authored_manifest(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        engine, _, _ = authority(store)
        created = apply_one(
            engine,
            CreateFieldbookProjectOperation(
                kind=FieldbookProjectKind.JOURNAL,
                title="Journal",
                summary="Private notes.",
            ),
            observation(),
        )
        assert created.project_id is not None

        rejected = apply_one(
            engine,
            AppendFieldbookEntryOperation(
                project_id=created.project_id,
                kind=FieldbookEntryKind.QUESTION,
                content="This ID was not in the planner input.",
            ),
            observation(),
            project_ids=[],
        )

        assert rejected.status is ContinuityOperationStatus.REJECTED
        assert rejected.reason == (
            f"Fieldbook project {created.project_id!r} was not delivered in "
            "planner context pc-1."
        )
        assert rejected.project_id is None
        assert rejected.entry_id is None
        assert rejected.resolved_evidence == []
        assert rejected.operation.project_id == created.project_id
        assert rejected.plan_id == "plan-a"
        assert rejected.plan_version == 1
        assert not rejected.writes_degraded
        assert store.fieldbook.entries(created.project_id) == []


def test_foreign_run_context_is_rejected_with_complete_causal_receipt() -> None:
    engine, _, events = authority(None)
    foreign = observation(run_id="run-b")
    commit = observation()
    operation = CreateFieldbookProjectOperation(
        kind=FieldbookProjectKind.GENERIC,
        title="Foreign",
        summary="This context belongs to another run.",
    )

    receipt = engine.apply(
        [operation],
        origin=ContinuityOrigin.DECISION,
        authored_context=context(foreign),
        commit_observation=commit,
        plan_id="plan-foreign",
        plan_version=2,
        step_id="reject-foreign",
    )[0]

    assert receipt.origin is ContinuityOrigin.DECISION
    assert receipt.status is ContinuityOperationStatus.REJECTED
    assert receipt.operation == operation
    assert receipt.reason == "The planner context belongs to another run."
    assert receipt.project_id is None
    assert receipt.entry_id is None
    assert receipt.resolved_evidence == []
    assert receipt.plan_id == "plan-foreign"
    assert receipt.plan_version == 2
    assert receipt.step_id == "reject-foreign"
    assert receipt.authored_context_id == "pc-1"
    assert receipt.authored_revision == foreign.world_revision
    assert receipt.commit_revision == commit.world_revision
    assert not receipt.writes_degraded
    assert events == [
        (
            "fieldbook_receipt",
            {
                "step_index": commit.step_index,
                "payload": receipt.model_dump(mode="json"),
            },
        )
    ]


def evidence_snapshot(authority: EvidenceAuthority) -> ResolvedEvidenceSnapshot:
    return ResolvedEvidenceSnapshot(
        source="current_observation",
        source_id="pc-1:current_observation",
        authority=authority,
        authored_context_id="pc-1",
        run_id="run-a",
        compact_summary="typed evidence",
    )


@pytest.mark.parametrize(
    "kind",
    [
        FieldbookEntryKind.OBSERVATION,
        FieldbookEntryKind.MANIFEST,
        FieldbookEntryKind.EXPENSE,
    ],
)
def test_fact_entries_require_fact_capable_evidence(
    kind: FieldbookEntryKind,
) -> None:
    operation = AppendFieldbookEntryOperation(
        project_id="fbp-" + "1" * 32,
        kind=kind,
        content="A purported fact.",
    )

    assert FieldbookAuthority._admissibility_error(operation, []) == (
        f"A fieldbook {kind.value} entry requires fresh or causally verified "
        "world evidence."
    )
    assert (
        FieldbookAuthority._admissibility_error(
            operation,
            [evidence_snapshot(EvidenceAuthority.FRESH_WORLD_OBSERVATION)],
        )
        is None
    )
    assert FieldbookAuthority._admissibility_error(
        operation,
        [evidence_snapshot(EvidenceAuthority.ADVICE)],
    ) is not None


@pytest.mark.parametrize(
    "kind",
    [
        FieldbookEntryKind.INCIDENT,
        FieldbookEntryKind.ROUTE_ENTRY,
    ],
)
def test_episode_entries_require_episode_capable_evidence(
    kind: FieldbookEntryKind,
) -> None:
    operation = AppendFieldbookEntryOperation(
        project_id="fbp-" + "1" * 32,
        kind=kind,
        content="A purported episode.",
    )

    assert FieldbookAuthority._admissibility_error(operation, []) == (
        f"A fieldbook {kind.value} entry requires a current observation, "
        "action attempt, or plan lifecycle outcome."
    )
    assert (
        FieldbookAuthority._admissibility_error(
            operation,
            [evidence_snapshot(EvidenceAuthority.PLAN_DISPOSITION)],
        )
        is None
    )
    assert FieldbookAuthority._admissibility_error(
        operation,
        [evidence_snapshot(EvidenceAuthority.ADVICE)],
    ) is not None


def test_observational_entry_requires_capable_delivered_evidence(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        engine, _, _ = authority(store)
        created = apply_one(
            engine,
            CreateFieldbookProjectOperation(
                kind=FieldbookProjectKind.INCIDENT_LOG,
                title="Incident log",
                summary="Observed incidents only.",
            ),
            observation(),
        )
        assert created.project_id is not None
        without_evidence = apply_one(
            engine,
            AppendFieldbookEntryOperation(
                project_id=created.project_id,
                kind=FieldbookEntryKind.OBSERVATION,
                content="A dust bandit patrol passed the gate.",
            ),
            observation(),
            project_ids=[created.project_id],
        )
        with_evidence = apply_one(
            engine,
            AppendFieldbookEntryOperation(
                project_id=created.project_id,
                kind=FieldbookEntryKind.OBSERVATION,
                content="The gate was visible in the fresh observation.",
                references=[CurrentObservationEvidence()],
            ),
            observation(),
            project_ids=[created.project_id],
        )

        assert without_evidence.status is ContinuityOperationStatus.REJECTED
        assert without_evidence.reason == (
            "A fieldbook observation entry requires fresh or causally "
            "verified world evidence."
        )
        assert with_evidence.status is ContinuityOperationStatus.ACCEPTED
        assert with_evidence.entry_id is not None
        assert with_evidence.project_id == created.project_id
        assert with_evidence.reason == (
            f"append_entry added fieldbook entry {with_evidence.entry_id} "
            f"to project {created.project_id}."
        )
        assert len(with_evidence.resolved_evidence) == 1
        assert with_evidence.resolved_evidence[0].authority is (
            EvidenceAuthority.FRESH_WORLD_OBSERVATION
        )
        assert not with_evidence.writes_degraded
        entries = store.fieldbook.entries(created.project_id)
        assert len(entries) == 1
        assert entries[0].provenance is not None
        assert entries[0].provenance.operation == (
            AppendFieldbookEntryOperation(
                project_id=created.project_id,
                kind=FieldbookEntryKind.OBSERVATION,
                content="The gate was visible in the fresh observation.",
                references=[CurrentObservationEvidence()],
            )
        )
        project = store.fieldbook.get_project(created.project_id)
        assert project is not None
        assert project.latest_provenance == entries[0].provenance
        history = store.fieldbook.history(created.project_id)
        assert history[-1].entry_id == with_evidence.entry_id
        assert set(history[-1].payload) == {"entry"}
        provenance = history[-1].payload["entry"]["provenance"]
        assert provenance["authored_context_id"] == "pc-1"
        assert provenance["resolved_evidence"][0]["authority"] == (
            "fresh_world_observation"
        )


def test_every_delivered_evidence_identity_reaches_receipt_and_provenance(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    plan_outcome = ledger.record_plan_outcome(
        plan_id="plan-prior",
        plan_version=2,
        objective="Scout a route.",
        disposition=PlanDisposition.COMPLETED,
        reason="The route was scouted.",
        completed_step_ids=["scout"],
        actions_completed=1,
        terminal_revision=WorldStateRevision(
            telemetry_sequence=6,
            frame_sequence=4,
        ),
        started_at=now,
        finished_at=now,
    )
    with open_store(tmp_path / "continuity.sqlite3") as store:
        memory = store.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="A prior private belief.",
            salience=0.5,
            grounding=None,
        )
        engine, _, _ = authority(
            store,
            ledger=ledger,
            brief_ids={BRIEF_ID},
        )
        created = apply_one(
            engine,
            CreateFieldbookProjectOperation(
                kind=FieldbookProjectKind.JOURNAL,
                title="Evidence journal",
                summary="Grounded private notes.",
            ),
            observation(),
        )
        assert created.project_id is not None
        current = observation()
        references = [
            PlanOutcomeEvidence(plan_outcome_id=plan_outcome.plan_outcome_id),
            MemoryEvidence(memory_id=memory.memory_id),
            AdvisorBriefEvidence(brief_id=BRIEF_ID),
            CurrentObservationEvidence(),
        ]
        authored = context(
            current,
            fieldbook_project_ids=[created.project_id],
            plan_outcome_ids=[plan_outcome.plan_outcome_id],
            memory_ids=[memory.memory_id],
            advisor_brief_ids=[BRIEF_ID],
        )
        advice_only = AppendFieldbookEntryOperation(
            project_id=created.project_id,
            kind=FieldbookEntryKind.OBSERVATION,
            content="Advice alone cannot establish a world fact.",
            references=[AdvisorBriefEvidence(brief_id=BRIEF_ID)],
        )
        rejected = engine.apply(
            [advice_only],
            origin=ContinuityOrigin.PLAN,
            authored_context=authored,
            commit_observation=current,
            plan_id="plan-fieldbook",
            plan_version=4,
            step_id="reject-advice",
        )[0]
        operation = AppendFieldbookEntryOperation(
            project_id=created.project_id,
            kind=FieldbookEntryKind.NOTE,
            content="All four delivered sources remain attributable.",
            references=references,
        )

        receipt = engine.apply(
            [operation],
            origin=ContinuityOrigin.PLAN,
            authored_context=authored,
            commit_observation=current,
            plan_id="plan-fieldbook",
            plan_version=4,
            step_id="append-grounded-note",
        )[0]

        assert rejected.status is ContinuityOperationStatus.REJECTED
        assert rejected.reason == (
            "A fieldbook observation entry requires fresh or causally "
            "verified world evidence."
        )
        assert [item.authority for item in rejected.resolved_evidence] == [
            EvidenceAuthority.ADVICE
        ]
        assert receipt.status is ContinuityOperationStatus.ACCEPTED
        assert receipt.operation == operation
        assert receipt.project_id == created.project_id
        assert receipt.entry_id is not None
        assert receipt.reason == (
            f"append_entry added fieldbook entry {receipt.entry_id} "
            f"to project {created.project_id}."
        )
        assert receipt.plan_id == "plan-fieldbook"
        assert receipt.plan_version == 4
        assert receipt.step_id == "append-grounded-note"
        assert [item.source for item in receipt.resolved_evidence] == [
            "plan_outcome",
            "memory",
            "advisor_brief",
            "current_observation",
        ]
        assert [item.source_id for item in receipt.resolved_evidence] == [
            plan_outcome.plan_outcome_id,
            memory.memory_id,
            BRIEF_ID,
            "pc-1:current_observation",
        ]
        assert [item.authority for item in receipt.resolved_evidence] == [
            EvidenceAuthority.PLAN_DISPOSITION,
            EvidenceAuthority.AGENT_BELIEF,
            EvidenceAuthority.ADVICE,
            EvidenceAuthority.FRESH_WORLD_OBSERVATION,
        ]
        entry = store.fieldbook.entries(created.project_id)[0]
        assert entry.entry_id == receipt.entry_id
        assert entry.provenance is not None
        assert entry.provenance.operation == operation
        assert entry.provenance.references == references
        assert entry.provenance.resolved_evidence == receipt.resolved_evidence
        assert entry.provenance.plan_id == "plan-fieldbook"
        assert entry.provenance.plan_version == 4
        assert entry.provenance.step_id == "append-grounded-note"
        assert entry.provenance.rendered_grounding == (
            "plan_outcome(po-1: completed); "
            f"memory {memory.memory_id}; "
            f"advisor_brief({BRIEF_ID}, advice not world evidence); "
            "current_observation(telemetry_sequence=7, frame_sequence=5)"
        )


def test_disabled_fieldbook_is_an_explicit_no_op() -> None:
    engine, _, _ = authority(None)
    project_id = "fbp-" + "1" * 32
    operation = AppendFieldbookEntryOperation(
        project_id=project_id,
        kind=FieldbookEntryKind.NOTE,
        content="This cannot be written without the durable store.",
        references=[CurrentObservationEvidence()],
    )

    receipt = apply_one(
        engine,
        operation,
        observation(),
        project_ids=[project_id],
    )

    assert receipt.status is ContinuityOperationStatus.NO_OP
    assert receipt.project_id is None
    assert receipt.entry_id is None
    assert receipt.operation == operation
    assert receipt.reason == (
        "The durable fieldbook is disabled for this run; nothing was written."
    )
    assert len(receipt.resolved_evidence) == 1
    assert receipt.resolved_evidence[0].authority is (
        EvidenceAuthority.FRESH_WORLD_OBSERVATION
    )
    assert receipt.plan_id == "plan-a"
    assert receipt.plan_version == 1
    assert not receipt.writes_degraded


def test_unresolvable_delivered_reference_is_rejected_before_disabled_no_op() -> None:
    engine, _, _ = authority(None)
    current = observation()
    project_id = "fbp-" + "1" * 32
    memory_id = "mem-missing"
    operation = AppendFieldbookEntryOperation(
        project_id=project_id,
        kind=FieldbookEntryKind.NOTE,
        content="A missing memory cannot ground this entry.",
        references=[MemoryEvidence(memory_id=memory_id)],
    )

    receipt = engine.apply(
        [operation],
        origin=ContinuityOrigin.PLAN,
        authored_context=context(
            current,
            fieldbook_project_ids=[project_id],
            memory_ids=[memory_id],
        ),
        commit_observation=current,
        plan_id="plan-missing-evidence",
        plan_version=1,
        step_id="resolve-missing-memory",
    )[0]

    assert receipt.status is ContinuityOperationStatus.REJECTED
    assert receipt.operation == operation
    assert receipt.reason == (
        "Durable memory is unavailable, so memory mem-missing cannot be cited."
    )
    assert receipt.project_id is None
    assert receipt.entry_id is None
    assert receipt.resolved_evidence == []
    assert receipt.plan_id == "plan-missing-evidence"
    assert receipt.plan_version == 1
    assert receipt.step_id == "resolve-missing-memory"
    assert not receipt.writes_degraded


def test_evidence_is_conserved_if_an_evidence_bearing_transition_no_ops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        project = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.JOURNAL,
            title="No-op evidence",
            summary="Grounding must survive every terminal receipt.",
            provenance=None,
        )
        engine, _, _ = authority(store)
        operation = AppendFieldbookEntryOperation(
            project_id=project.project_id,
            kind=FieldbookEntryKind.NOTE,
            content="The transition reports a deliberate no-op.",
            references=[CurrentObservationEvidence()],
        )

        def no_op_transition(*_: Any) -> Any:
            raise FieldbookNoOp("Injected evidence-bearing no-op.")

        monkeypatch.setattr(
            FieldbookAuthority,
            "_transition",
            no_op_transition,
        )

        receipt = apply_one(
            engine,
            operation,
            observation(),
            project_ids=[project.project_id],
        )

        assert receipt.status is ContinuityOperationStatus.NO_OP
        assert receipt.reason == "Injected evidence-bearing no-op."
        assert receipt.project_id == project.project_id
        assert len(receipt.resolved_evidence) == 1
        assert receipt.resolved_evidence[0].authority is (
            EvidenceAuthority.FRESH_WORLD_OBSERVATION
        )
        assert store.fieldbook.entries(project.project_id) == []


def test_projection_and_history_retain_provenance_for_every_project_transition(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        engine, _, _ = authority(store)
        create_operation = CreateFieldbookProjectOperation(
            kind=FieldbookProjectKind.EQUIPMENT_PLAN,
            title="Equipment",
            summary="Acquire one backpack.",
        )
        created = apply_one(
            engine,
            create_operation,
            observation(),
        )
        assert created.project_id is not None
        operations = [
            UpdateFieldbookSummaryOperation(
                project_id=created.project_id,
                summary="Acquire a trader backpack.",
            ),
            SelectFieldbookProjectOperation(project_id=created.project_id),
            SelectFieldbookProjectOperation(project_id=None),
            SetFieldbookProjectStatusOperation(
                project_id=created.project_id,
                status=FieldbookProjectStatus.PAUSED,
            ),
        ]
        receipts = []
        for operation in operations:
            receipts.append(
                apply_one(
                    engine,
                    operation,
                    observation(),
                    project_ids=[created.project_id],
                )
            )
            current = store.fieldbook.get_project(created.project_id)
            assert current is not None
            assert current.latest_provenance is not None
            assert current.latest_provenance.operation == operation

        assert all(
            receipt.status is ContinuityOperationStatus.ACCEPTED
            for receipt in receipts
        )
        assert [receipt.project_id for receipt in receipts] == [
            created.project_id,
            created.project_id,
            None,
            created.project_id,
        ]
        assert [receipt.entry_id for receipt in receipts] == [None, None, None, None]
        assert [receipt.reason for receipt in receipts] == [
            (
                "update_summary applied to fieldbook project "
                f"{created.project_id} (active)."
            ),
            (
                "select_project applied to fieldbook project "
                f"{created.project_id} (active)."
            ),
            "select_project cleared the selected fieldbook project.",
            (
                "set_project_status applied to fieldbook project "
                f"{created.project_id} (paused)."
            ),
        ]
        project = store.fieldbook.get_project(created.project_id)
        assert project is not None
        assert project.summary == "Acquire a trader backpack."
        assert project.status is FieldbookProjectStatus.PAUSED
        assert not project.selected
        assert project.latest_provenance is not None
        assert project.latest_provenance.operation == operations[-1]
        history = store.fieldbook.history(created.project_id)
        assert [event.event.value for event in history] == [
            "create_project",
            "update_summary",
            "select_project",
            "clear_selection",
            "set_project_status",
        ]
        assert set(history[0].payload) == {"project", "provenance"}
        assert set(history[1].payload) == {"summary", "provenance"}
        assert history[1].payload["summary"] == "Acquire a trader backpack."
        assert set(history[2].payload) == {"provenance"}
        assert set(history[3].payload) == {"provenance"}
        assert set(history[4].payload) == {"status", "provenance"}
        assert history[4].payload["status"] == "paused"
        assert [event.payload["provenance"]["operation"] for event in history] == [
            operation.model_dump(mode="json")
            for operation in [create_operation, *operations]
        ]
        assert project.updated_at == history[-1].recorded_at

        repeated = apply_one(
            engine,
            SetFieldbookProjectStatusOperation(
                project_id=created.project_id,
                status=FieldbookProjectStatus.PAUSED,
            ),
            observation(),
            project_ids=[created.project_id],
        )
        refused_operation = AppendFieldbookEntryOperation(
            project_id=created.project_id,
            kind=FieldbookEntryKind.NOTE,
            content="A paused project refuses new entries.",
            references=[CurrentObservationEvidence()],
        )
        refused = apply_one(
            engine,
            refused_operation,
            observation(),
            project_ids=[created.project_id],
        )
        assert repeated.status is ContinuityOperationStatus.NO_OP
        assert repeated.reason == (
            f"Fieldbook project {created.project_id!r} is already paused."
        )
        assert repeated.project_id == created.project_id
        assert repeated.resolved_evidence == []
        assert not repeated.writes_degraded
        assert refused.status is ContinuityOperationStatus.REJECTED
        assert refused.operation == refused_operation
        assert refused.reason == (
            f"Fieldbook project {created.project_id!r} is paused; "
            "resume it before appending."
        )
        assert refused.project_id == created.project_id
        assert len(refused.resolved_evidence) == 1
        assert refused.resolved_evidence[0].authority is (
            EvidenceAuthority.FRESH_WORLD_OBSERVATION
        )
        assert not refused.writes_degraded


def test_project_switch_attributes_both_sides_to_the_same_authored_operation(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        engine, _, _ = authority(store)
        first_receipt = apply_one(
            engine,
            CreateFieldbookProjectOperation(
                kind=FieldbookProjectKind.JOURNAL,
                title="First",
                summary="First private project.",
            ),
            observation(),
        )
        second_receipt = apply_one(
            engine,
            CreateFieldbookProjectOperation(
                kind=FieldbookProjectKind.ROUTE_ATLAS,
                title="Second",
                summary="Second private project.",
            ),
            observation(),
        )
        assert first_receipt.project_id is not None
        assert second_receipt.project_id is not None
        project_ids = [first_receipt.project_id, second_receipt.project_id]
        apply_one(
            engine,
            SelectFieldbookProjectOperation(project_id=first_receipt.project_id),
            observation(),
            project_ids=project_ids,
        )
        switch = SelectFieldbookProjectOperation(
            project_id=second_receipt.project_id
        )

        receipt = apply_one(
            engine,
            switch,
            observation(),
            project_ids=project_ids,
        )

        assert receipt.status is ContinuityOperationStatus.ACCEPTED
        first = store.fieldbook.get_project(first_receipt.project_id)
        second = store.fieldbook.get_project(second_receipt.project_id)
        assert first is not None
        assert second is not None
        assert not first.selected
        assert second.selected
        assert first.latest_provenance is not None
        assert second.latest_provenance is not None
        assert first.latest_provenance.operation == switch
        assert second.latest_provenance.operation == switch
        first_event = store.fieldbook.history(first.project_id)[-1]
        second_event = store.fieldbook.history(second.project_id)[-1]
        assert first_event.event is FieldbookLifecycleEvent.CLEAR_SELECTION
        assert second_event.event is FieldbookLifecycleEvent.SELECT_PROJECT
        assert first_event.payload["provenance"] == (
            first.latest_provenance.model_dump(mode="json")
        )
        assert second_event.payload["provenance"] == (
            second.latest_provenance.model_dump(mode="json")
        )


def test_unexpected_store_failure_is_typed_and_quarantines_later_writes(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        engine, continuity, _ = authority(store)
        created = apply_one(
            engine,
            CreateFieldbookProjectOperation(
                kind=FieldbookProjectKind.JOURNAL,
                title="Journal",
                summary="Open until the store fails.",
            ),
            observation(),
        )
        assert created.project_id is not None
        store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TEMP TRIGGER fail_fieldbook_entry
            BEFORE INSERT ON fieldbook_entries
            BEGIN SELECT RAISE(ABORT, 'injected fieldbook failure'); END
            """
        )

        failed = apply_one(
            engine,
            AppendFieldbookEntryOperation(
                project_id=created.project_id,
                kind=FieldbookEntryKind.NOTE,
                content="This transition rolls back.",
                references=[CurrentObservationEvidence()],
            ),
            observation(),
            project_ids=[created.project_id],
        )
        quarantined = apply_one(
            engine,
            CreateFieldbookProjectOperation(
                kind=FieldbookProjectKind.GENERIC,
                title="Later write",
                summary="This is refused without touching SQLite.",
            ),
            observation(),
        )

        assert failed.status is ContinuityOperationStatus.FAILED
        assert failed.project_id == created.project_id
        assert failed.entry_id is None
        assert len(failed.resolved_evidence) == 1
        assert failed.resolved_evidence[0].authority is (
            EvidenceAuthority.FRESH_WORLD_OBSERVATION
        )
        assert "IntegrityError: injected fieldbook failure" in failed.reason
        assert failed.writes_degraded
        assert quarantined.status is ContinuityOperationStatus.FAILED
        assert quarantined.project_id is None
        assert quarantined.entry_id is None
        assert quarantined.resolved_evidence == []
        assert quarantined.writes_degraded
        assert continuity.writes_degraded_reason == failed.reason
        assert quarantined.reason == failed.reason
        assert store.fieldbook.entries(created.project_id) == []


def test_evidence_read_failure_quarantines_reads_and_writes(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "continuity.sqlite3")
    memory = store.keep(
        "run-a",
        kind=MemoryKind.FACT,
        content="This identity was delivered before the store failed.",
        salience=0.5,
        grounding=None,
    )
    engine, continuity, _ = authority(store)
    project = store.fieldbook.create_project(
        run_id="run-a",
        kind=FieldbookProjectKind.JOURNAL,
        title="Read failure",
        summary="No transition may follow an untrustworthy evidence read.",
        provenance=None,
    )
    current = observation()
    operation = AppendFieldbookEntryOperation(
        project_id=project.project_id,
        kind=FieldbookEntryKind.NOTE,
        content="This operation must fail before writing.",
        references=[MemoryEvidence(memory_id=memory.memory_id)],
    )
    authored = context(
        current,
        fieldbook_project_ids=[project.project_id],
        memory_ids=[memory.memory_id],
    )
    store.close()

    receipt = engine.apply(
        [operation],
        origin=ContinuityOrigin.PLAN,
        authored_context=authored,
        commit_observation=current,
        plan_id="plan-read-failure",
        plan_version=1,
        step_id="resolve-memory",
    )[0]

    assert receipt.status is ContinuityOperationStatus.FAILED
    assert receipt.operation == operation
    assert receipt.project_id is None
    assert receipt.entry_id is None
    assert receipt.resolved_evidence == []
    assert receipt.plan_id == "plan-read-failure"
    assert receipt.plan_version == 1
    assert receipt.step_id == "resolve-memory"
    assert receipt.writes_degraded
    assert "ProgrammingError: Cannot operate on a closed database" in receipt.reason
    assert continuity.reads_degraded_reason == receipt.reason
    assert continuity.writes_degraded_reason == receipt.reason
