"""Authority and timing for planner-authored continuity.

Three layers must not blur: world evidence, working continuity, and durable
kept memory. These tests hold that seam — continuity carries runtime-owned
identity, a plan cannot remember work it has not done yet, and merely reading
memory changes nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kenshi_agent.campaign import CampaignScope, CampaignScopeOrigin
from kenshi_agent.continuity import (
    COMMITMENT_CLOSURE_AUTHORITIES,
    EPISODE_AUTHORITIES,
    FACT_AUTHORITIES,
    ContinuityAuthority,
    ContinuityLedger,
    EvidenceResolutionError,
    resolve_evidence_reference,
)
from kenshi_agent.continuity import (
    render_evidence_reference as _render_evidence_reference,
)
from kenshi_agent.memory import MemoryStore, RecallBudget
from kenshi_agent.models import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionOutcomeDigest,
    ActionOutcomeEvidence,
    AdvisorBriefEvidence,
    AuthoredPlannerContext,
    ContinuityOperationStatus,
    ContinuityOrigin,
    ControlMode,
    CurrentObservationEvidence,
    EvidenceAuthority,
    KeepMemoryOperation,
    MemoryEvidence,
    MemoryKind,
    MemoryLifecycleEvent,
    MemoryReadReceipt,
    MemoryReadStatus,
    MemoryRecord,
    MemoryResolutionDisposition,
    MemoryStatus,
    NearbyEntity,
    Observation,
    PlanDisposition,
    PlannerContextManifest,
    PlanOutcomeDigest,
    PlanOutcomeEvidence,
    ReinforceMemoryOperation,
    ResolvedEvidenceSnapshot,
    ResolveMemoryOperation,
    RetractMemoryOperation,
    StopAction,
    SupersedeMemoryOperation,
    TelemetrySnapshot,
    WorldStateRevision,
)

BRIEF_ID = "advisor-" + "0" * 32
OTHER_BRIEF_ID = "advisor-" + "f" * 32


def observation(
    *,
    run_id: str = "run-a",
    target_ids: tuple[str, ...] = (),
    stale: bool = False,
) -> Observation:
    return Observation(
        run_id=run_id,
        step_index=0,
        mode="mock",
        world_revision=WorldStateRevision(telemetry_sequence=3, frame_sequence=2),
        telemetry_stale=stale,
        telemetry=TelemetrySnapshot(
            sequence=3,
            nearby_entities=[
                NearbyEntity(id=target_id, name="Barman") for target_id in target_ids
            ],
        ),
    )


def action_outcome(outcome_id: str = "ao-1") -> ActionOutcome:
    now = datetime.now(UTC)
    return ActionOutcome(
        outcome_id=outcome_id,
        run_id="run-a",
        plan_id="single-step",
        plan_version=1,
        step_id="step-0",
        step_index=0,
        intent="Stop.",
        action=StopAction(reason="done"),
        executed=True,
        assessment=ActionOutcomeAssessment.NO_OP,
        feedback="Nothing changed.",
        recorded_at=now,
    )


def ledger_with_evidence() -> ContinuityLedger:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(action_outcome("ao-1"))
    ledger.record_plan_outcome(
        plan_id="plan-a",
        plan_version=1,
        objective="Find work.",
        disposition=PlanDisposition.COMPLETED,
        reason="Done.",
        completed_step_ids=[],
        actions_completed=0,
        terminal_revision=None,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    return ledger


def keep(
    kind: MemoryKind,
    content: str,
    *,
    references: list[Any] | None = None,
    target_id: str | None = None,
    salience: float = 0.5,
) -> KeepMemoryOperation:
    return KeepMemoryOperation(
        kind=kind,
        content=content,
        references=references or [],
        target_id=target_id,
        salience=salience,
    )


def authority(
    store: MemoryStore | None,
    ledger: ContinuityLedger,
    *,
    brief_ids: set[str] | None = None,
) -> tuple[ContinuityAuthority, list[tuple[str, dict[str, Any]]]]:
    written: list[tuple[str, dict[str, Any]]] = []

    def write(event_type: str, **kwargs: Any) -> None:
        written.append((event_type, kwargs))

    return (
        ContinuityAuthority(
            run_id="run-a",
            store=store,
            ledger=ledger,
            logger=SimpleNamespace(write=write),
            advisor_brief_ids=lambda: brief_ids or set(),
        ),
        written,
    )


def planner_context(
    current: Observation,
    *,
    ledger: ContinuityLedger,
    store: MemoryStore | None,
    brief_ids: set[str],
    action_outcome_ids: tuple[str, ...] | None = None,
    plan_outcome_ids: tuple[str, ...] | None = None,
    memory_ids: tuple[str, ...] | None = None,
    advisor_brief_ids: tuple[str, ...] | None = None,
) -> AuthoredPlannerContext:
    """A test planner input containing exactly the records named here."""

    return AuthoredPlannerContext(
        manifest=PlannerContextManifest(
            context_id="pc-1",
            run_id=current.run_id,
            authored_revision=current.world_revision,
            current_observation_delivered=True,
            telemetry_was_fresh=(
                current.telemetry is not None and not current.telemetry_stale
            ),
            input_kind="full_observation",
            current_target_ids=sorted(current.current_memory_target_ids()),
            action_outcome_ids=list(
                action_outcome_ids
                if action_outcome_ids is not None
                else tuple(
                    outcome.outcome_id for outcome in ledger.recent_action_outcomes
                )
            ),
            plan_outcome_ids=list(
                plan_outcome_ids
                if plan_outcome_ids is not None
                else tuple(
                    outcome.plan_outcome_id for outcome in ledger.recent_plan_outcomes
                )
            ),
            memory_ids=list(
                memory_ids
                if memory_ids is not None
                else tuple(
                    record.memory_id
                    for record in (store.recall(limit=128) if store is not None else [])
                )
            ),
            advisor_brief_ids=list(
                advisor_brief_ids
                if advisor_brief_ids is not None
                else tuple(sorted(brief_ids))
            ),
        ),
        observation=current,
    )


def render_evidence_reference(
    reference: Any,
    *,
    observation: Observation,
    ledger: ContinuityLedger,
    store: MemoryStore | None,
    advisor_brief_ids: set[str],
    action_outcome_ids: tuple[str, ...] | None = None,
    plan_outcome_ids: tuple[str, ...] | None = None,
) -> str:
    return _render_evidence_reference(
        reference,
        authored_context=planner_context(
            observation,
            ledger=ledger,
            store=store,
            brief_ids=advisor_brief_ids,
            action_outcome_ids=action_outcome_ids,
            plan_outcome_ids=plan_outcome_ids,
        ),
        ledger=ledger,
        store=store,
        advisor_brief_ids=advisor_brief_ids,
    )


def apply_operations(
    engine: ContinuityAuthority,
    operations: list[Any],
    *,
    origin: ContinuityOrigin,
    observation: Observation,
    plan_id: str | None = None,
    plan_version: int | None = None,
    step_id: str | None = None,
    action_outcome_ids: tuple[str, ...] | None = None,
) -> list[Any]:
    brief_ids = engine.advisor_brief_ids()
    return engine.apply(
        operations,
        origin=origin,
        authored_context=planner_context(
            observation,
            ledger=engine.ledger,
            store=engine.store,
            brief_ids=brief_ids,
            action_outcome_ids=action_outcome_ids,
        ),
        commit_observation=observation,
        plan_id=plan_id,
        plan_version=plan_version,
        step_id=step_id,
    )


def open_store(path: Path, campaign_id: str = "test") -> MemoryStore:
    identities = iter(f"mem-{index:04d}" for index in range(1, 500))
    return MemoryStore(
        path,
        CampaignScope(campaign_id=campaign_id, origin=CampaignScopeOrigin.CONFIGURED),
        memory_id_factory=lambda: next(identities),
    )


def keep_fact(
    store: MemoryStore,
    content: str,
    *,
    target_id: str | None = None,
    salience: float = 0.5,
    run_id: str = "run-a",
) -> MemoryRecord:
    return store.keep(
        run_id,
        kind=MemoryKind.FACT,
        content=content,
        salience=salience,
        grounding=None,
        target_id=target_id,
    )


# --------------------------------------------------------------------------
# Runtime-owned evidence identity
# --------------------------------------------------------------------------


def test_every_action_outcome_receives_one_stable_runtime_owned_identity() -> None:
    """Evidence a planner may cite must be named by the runtime, not the model."""

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)

    ids = [ledger.next_action_outcome_id() for _ in range(3)]

    assert ids == ["ao-1", "ao-2", "ao-3"]


def test_a_bounded_ledger_still_answers_for_the_evidence_it_evicted() -> None:
    """A trimmed working ledger must not silently un-prove an outcome.

    The bound is on what the planner is shown, not on what the runtime admits
    happened. Treating eviction as "no such outcome" would reject an honest
    citation as an invention.
    """

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=2)
    for index in range(4):
        ledger.record_action_outcome(action_outcome(f"ao-{index + 1}"))

    assert [outcome.outcome_id for outcome in ledger.recent_action_outcomes] == [
        "ao-3",
        "ao-4",
    ]
    assert ledger.has_action_outcome("ao-1")
    assert not ledger.has_action_outcome("ao-9")
    digest = ledger.action_outcome_digest("ao-1")
    assert digest is not None
    assert digest.assessment is ActionOutcomeAssessment.NO_OP
    assert digest.action_kind == "stop"
    assert digest.executed is True


def test_an_action_digest_conserves_every_field_needed_to_judge_its_authority() -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=1)
    started = WorldStateRevision(telemetry_sequence=8, frame_sequence=4)
    completed = WorldStateRevision(telemetry_sequence=9, frame_sequence=5)
    outcome = action_outcome("ao-1").model_copy(
        update={
            "plan_id": "plan-a",
            "plan_version": 7,
            "step_id": "deliver",
            "command_id": "cmd-deliver",
            "assessment": ActionOutcomeAssessment.CHANGED,
            "causal_revision_advanced": True,
            "controller_verified": True,
            "semantic_status": "context_task_started",
            "target_id": "ore-node-7",
            "started_after_revision": started,
            "completed_at_revision": completed,
            "feedback": "x" * 501,
        }
    )

    ledger.record_action_outcome(outcome)

    assert ledger.action_outcome_digest("ao-1") == ActionOutcomeDigest(
        outcome_id="ao-1",
        run_id="run-a",
        plan_id="plan-a",
        plan_version=7,
        step_id="deliver",
        command_id="cmd-deliver",
        action_kind="stop",
        assessment=ActionOutcomeAssessment.CHANGED,
        executed=True,
        causal_revision_advanced=True,
        controller_verified=True,
        semantic_status="context_task_started",
        target_id="ore-node-7",
        started_after_revision=started,
        completed_at_revision=completed,
        evidence_summary="x" * 500,
        recorded_at=outcome.recorded_at,
    )


def test_a_zero_length_ledger_shows_nothing_and_still_proves_everything() -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=0)
    ledger.record_action_outcome(action_outcome("ao-1"))

    assert ledger.recent_action_outcomes == []
    assert ledger.has_action_outcome("ao-1")


def test_an_explicit_outcome_read_resurfaces_an_evicted_digest_for_citation() -> None:
    from kenshi_agent.planners.base import planner_context_manifest

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=1)
    ledger.record_action_outcome(action_outcome("ao-1"))
    ledger.record_action_outcome(action_outcome("ao-2"))

    result = ledger.search_outcomes(query="ao-1", limit=1)
    read_receipt = MemoryReadReceipt(
        **result.model_dump(),
        receipt_id="mrr-" + "1" * 32,
        source="working_outcomes",
        status=MemoryReadStatus.COMPLETED,
        action_outcome_ids=["ao-1"],
        plan_id="single-step",
        plan_version=1,
        step_id="step-0",
    )
    recalled = observation().model_copy(
        update={
            "memory_search": read_receipt,
            "recent_action_outcomes": ledger.recent_action_outcomes,
        }
    )
    manifest = planner_context_manifest(
        recalled,
        context_id="pc-1",
        input_kind="full_observation",
    )

    assert [item.outcome_id for item in result.action_outcomes] == ["ao-1"]
    assert manifest.action_outcome_ids == ["ao-1", "ao-2"]
    assert manifest.memory_read_receipt_ids == ["mrr-" + "1" * 32]


@pytest.mark.parametrize(
    ("overridden_field", "invented_id"),
    [
        ("action_outcome_ids", "ao-invented"),
        ("plan_outcome_ids", "po-invented"),
    ],
)
def test_a_read_receipt_cannot_advertise_working_ids_it_did_not_return(
    overridden_field: str,
    invented_id: str,
) -> None:
    ledger = ledger_with_evidence()
    result = ledger.search_outcomes(query="plan", limit=8)
    values = {
        "action_outcome_ids": [
            outcome.outcome_id for outcome in result.action_outcomes
        ],
        "plan_outcome_ids": [
            outcome.plan_outcome_id for outcome in result.plan_outcomes
        ],
    }
    values[overridden_field] = [invented_id]

    with pytest.raises(ValueError, match=overridden_field):
        MemoryReadReceipt(
            **result.model_dump(),
            receipt_id="mrr-" + "1" * 32,
            source="working_outcomes",
            status=MemoryReadStatus.COMPLETED,
            plan_id="single-step",
            plan_version=1,
            step_id="step-0",
            **values,
        )


def test_a_working_outcome_receipt_cannot_claim_a_durable_campaign() -> None:
    with pytest.raises(ValueError, match="campaign_id"):
        MemoryReadReceipt(
            query="plan",
            receipt_id="mrr-" + "1" * 32,
            source="working_outcomes",
            status=MemoryReadStatus.COMPLETED,
            campaign_id="campaign-a",
            plan_id="single-step",
            plan_version=1,
            step_id="step-0",
        )


@pytest.mark.parametrize(
    ("source", "status", "campaign_id"),
    [
        ("durable_memory", MemoryReadStatus.COMPLETED, None),
        ("durable_memory", MemoryReadStatus.FAILED, None),
        ("durable_memory", MemoryReadStatus.UNAVAILABLE, "campaign-a"),
        ("working_outcomes", MemoryReadStatus.UNAVAILABLE, None),
        ("working_outcomes", MemoryReadStatus.FAILED, None),
    ],
)
def test_a_read_receipt_rejects_impossible_source_status_scope_combinations(
    source: str,
    status: MemoryReadStatus,
    campaign_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="source, status, and campaign_id"):
        MemoryReadReceipt(
            query="gate",
            receipt_id="mrr-" + "1" * 32,
            source=source,  # type: ignore[arg-type]
            status=status,
            campaign_id=campaign_id,
            plan_id="single-step",
            plan_version=1,
            step_id="step-0",
        )


def test_outcome_search_reports_matches_across_both_ledgers_under_one_bound() -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=1)
    ledger.record_action_outcome(
        action_outcome("ao-1").model_copy(
            update={
                "plan_id": "plan-search",
                "step_id": "harvest",
                "command_id": "cmd-search",
                "semantic_status": "ore_ready",
                "target_id": "copper-node",
            }
        )
    )
    ledger.record_plan_outcome(
        plan_id="plan-search",
        plan_version=2,
        objective="Harvest copper.",
        disposition=PlanDisposition.FAILED,
        reason="Inventory was full.",
        completed_step_ids=[],
        actions_completed=1,
        terminal_revision=WorldStateRevision(telemetry_sequence=12),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )

    both = ledger.search_outcomes(query="plan-search", limit=1)
    exact_bound = ledger.search_outcomes(query="ore_ready", limit=1)
    action_phrase = ledger.search_outcomes(query="ao-1 plan-search", limit=2)
    plan_phrase = ledger.search_outcomes(query="po-1 plan-search", limit=2)
    plan_only = ledger.search_outcomes(query="FAILED", limit=2)
    command_only = ledger.search_outcomes(query="cmd-search", limit=2)
    action_summary = ledger.search_outcomes(query="Nothing changed", limit=2)
    plan_objective = ledger.search_outcomes(query="Harvest copper", limit=2)
    plan_reason = ledger.search_outcomes(query="Inventory was full", limit=2)
    missing = ledger.search_outcomes(query="not-here", limit=2)

    assert [item.outcome_id for item in both.action_outcomes] == ["ao-1"]
    assert both.plan_outcomes == []
    assert both.matched == 2
    assert both.truncated is True
    assert exact_bound.matched == 1
    assert exact_bound.truncated is False
    assert exact_bound.reason == "1 retained working outcomes match 'ore_ready'; 1 shown."
    assert [item.outcome_id for item in action_phrase.action_outcomes] == ["ao-1"]
    assert [item.plan_outcome_id for item in plan_phrase.plan_outcomes] == ["po-1"]
    assert [item.plan_outcome_id for item in plan_only.plan_outcomes] == ["po-1"]
    assert [item.outcome_id for item in command_only.action_outcomes] == ["ao-1"]
    assert [item.outcome_id for item in action_summary.action_outcomes] == ["ao-1"]
    assert [item.plan_outcome_id for item in plan_objective.plan_outcomes] == ["po-1"]
    assert [item.plan_outcome_id for item in plan_reason.plan_outcomes] == ["po-1"]
    assert missing.action_outcomes == []
    assert missing.plan_outcomes == []
    assert missing.matched == 0
    assert missing.truncated is False
    with pytest.raises(ValueError):
        ledger.search_outcomes(query="anything", limit=0)


def test_plan_outcomes_carry_the_original_objective_and_terminal_reason() -> None:
    """"Execute step X" is not a purpose. The next plan needs the real one."""

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    started = datetime.now(UTC)
    terminal_revision = WorldStateRevision(telemetry_sequence=9)

    outcome = ledger.record_plan_outcome(
        plan_id="plan-a",
        plan_version=2,
        objective="Deliver six sealed slop canisters.",
        disposition=PlanDisposition.FAILED,
        reason="The gate was closed.",
        completed_step_ids=["walk", "open"],
        actions_completed=2,
        terminal_revision=terminal_revision,
        started_at=started,
        finished_at=started,
    )

    assert outcome.plan_outcome_id == "po-1"
    assert outcome.objective == "Deliver six sealed slop canisters."
    assert outcome.reason == "The gate was closed."
    assert outcome.completed_step_ids == ["walk", "open"]
    assert outcome.disposition is PlanDisposition.FAILED
    assert ledger.has_plan_outcome("po-1")
    assert not ledger.has_plan_outcome("po-2")
    assert ledger.plan_outcome_digest("po-1") == PlanOutcomeDigest(
        plan_outcome_id="po-1",
        run_id="run-a",
        plan_id="plan-a",
        plan_version=2,
        objective="Deliver six sealed slop canisters.",
        disposition=PlanDisposition.FAILED,
        reason_digest="The gate was closed.",
        completed_step_ids=["walk", "open"],
        actions_completed=2,
        terminal_revision=terminal_revision,
        started_at=started,
        finished_at=started,
    )


def test_a_reset_run_forgets_its_working_continuity_entirely() -> None:
    ledger = ledger_with_evidence()

    ledger.reset()

    assert ledger.recent_action_outcomes == []
    assert ledger.recent_plan_outcomes == []
    assert not ledger.has_action_outcome("ao-1")
    assert not ledger.has_plan_outcome("po-1")
    assert ledger.next_action_outcome_id() == "ao-1"


# --------------------------------------------------------------------------
# Evidence references
# --------------------------------------------------------------------------


def test_each_evidence_kind_renders_exactly_what_its_authority_says(
    tmp_path: Path,
) -> None:
    """Exact strings, not substrings: the rendered grounding is the audit trail,
    so an outcome's assessment or a plan's disposition getting lost must show."""

    ledger = ledger_with_evidence()
    current = observation()

    with open_store(tmp_path / "memory.sqlite3") as store:
        memory_id = keep_fact(store, "A remembered fact.", run_id="run-a").memory_id
        expectations = (
            (
                CurrentObservationEvidence(),
                "current_observation(telemetry_sequence=3, frame_sequence=2)",
            ),
            (ActionOutcomeEvidence(outcome_id="ao-1"), "action_outcome(ao-1: no_op)"),
            (
                PlanOutcomeEvidence(plan_outcome_id="po-1"),
                "plan_outcome(po-1: completed)",
            ),
            (MemoryEvidence(memory_id=memory_id), f"memory {memory_id}"),
            (
                AdvisorBriefEvidence(brief_id=BRIEF_ID),
                f"advisor_brief({BRIEF_ID}, advice not world evidence)",
            ),
        )
        for reference, expected in expectations:
            rendered = render_evidence_reference(
                reference,
                observation=current,
                ledger=ledger,
                store=store,
                advisor_brief_ids={BRIEF_ID},
            )
            assert rendered == expected


def test_each_evidence_reference_resolves_to_its_complete_typed_snapshot(
    tmp_path: Path,
) -> None:
    current = observation()
    revision = current.world_revision
    action_revision = WorldStateRevision(telemetry_sequence=8, frame_sequence=6)
    plan_revision = WorldStateRevision(telemetry_sequence=10, frame_sequence=7)
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=1)
    ledger.record_action_outcome(
        action_outcome("ao-1").model_copy(
            update={
                "assessment": ActionOutcomeAssessment.CHANGED,
                "causal_revision_advanced": True,
                "controller_verified": True,
                "semantic_status": "context_task_started",
                "target_id": "ore-node-7",
                "completed_at_revision": action_revision,
            }
        )
    )
    ledger.record_plan_outcome(
        plan_id="plan-a",
        plan_version=1,
        objective="Find work.",
        disposition=PlanDisposition.ABANDONED,
        reason="No work was available.",
        completed_step_ids=[],
        actions_completed=1,
        terminal_revision=plan_revision,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )

    with open_store(tmp_path / "memory.sqlite3") as store:
        memory_id = keep_fact(
            store,
            "The barman declined.",
            run_id="run-a",
            target_id="barman-1",
        ).memory_id
        context = planner_context(
            current,
            ledger=ledger,
            store=store,
            brief_ids={BRIEF_ID},
            memory_ids=(memory_id,),
        )

        snapshots = [
            resolve_evidence_reference(
                reference,
                authored_context=context,
                ledger=ledger,
                store=store,
                advisor_brief_ids={BRIEF_ID},
            )
            for reference in (
                CurrentObservationEvidence(),
                ActionOutcomeEvidence(outcome_id="ao-1"),
                PlanOutcomeEvidence(plan_outcome_id="po-1"),
                MemoryEvidence(memory_id=memory_id),
                AdvisorBriefEvidence(brief_id=BRIEF_ID),
            )
        ]

    assert snapshots == [
        ResolvedEvidenceSnapshot(
            source="current_observation",
            source_id="pc-1:current_observation",
            authority=EvidenceAuthority.FRESH_WORLD_OBSERVATION,
            authored_context_id="pc-1",
            run_id="run-a",
            world_revision=revision,
            compact_summary=(
                "current_observation(telemetry_sequence=3, frame_sequence=2)"
            ),
        ),
        ResolvedEvidenceSnapshot(
            source="action_outcome",
            source_id="ao-1",
            authority=EvidenceAuthority.VERIFIED_WORLD_EFFECT,
            authored_context_id="pc-1",
            run_id="run-a",
            world_revision=action_revision,
            assessment=ActionOutcomeAssessment.CHANGED,
            action_kind="stop",
            executed=True,
            causal_revision_advanced=True,
            controller_verified=True,
            semantic_status="context_task_started",
            target_id="ore-node-7",
            compact_summary="action_outcome(ao-1: changed)",
        ),
        ResolvedEvidenceSnapshot(
            source="plan_outcome",
            source_id="po-1",
            authority=EvidenceAuthority.PLAN_DISPOSITION,
            authored_context_id="pc-1",
            run_id="run-a",
            world_revision=plan_revision,
            plan_disposition=PlanDisposition.ABANDONED,
            compact_summary="plan_outcome(po-1: abandoned)",
        ),
        ResolvedEvidenceSnapshot(
            source="memory",
            source_id=memory_id,
            authority=EvidenceAuthority.AGENT_BELIEF,
            authored_context_id="pc-1",
            run_id="run-a",
            memory_kind=MemoryKind.FACT,
            memory_status=MemoryStatus.ACTIVE,
            target_id="barman-1",
            compact_summary=f"memory {memory_id}",
        ),
        ResolvedEvidenceSnapshot(
            source="advisor_brief",
            source_id=BRIEF_ID,
            authority=EvidenceAuthority.ADVICE,
            authored_context_id="pc-1",
            run_id="run-a",
            compact_summary=(
                f"advisor_brief({BRIEF_ID}, advice not world evidence)"
            ),
        ),
    ]


@pytest.mark.parametrize(
    ("assessment", "executed", "causal", "verified", "expected"),
    [
        (
            ActionOutcomeAssessment.CHANGED,
            True,
            True,
            True,
            EvidenceAuthority.VERIFIED_WORLD_EFFECT,
        ),
        (
            ActionOutcomeAssessment.CHANGED,
            False,
            True,
            True,
            EvidenceAuthority.ATTEMPT_NOT_EXECUTED,
        ),
        (
            ActionOutcomeAssessment.NO_OP,
            True,
            False,
            True,
            EvidenceAuthority.ATTEMPT_NO_OP,
        ),
        (
            ActionOutcomeAssessment.CHANGED,
            True,
            True,
            False,
            EvidenceAuthority.ATTEMPT_CHANGED,
        ),
        (
            ActionOutcomeAssessment.CHANGED,
            True,
            False,
            False,
            EvidenceAuthority.OBSERVED_CHANGE,
        ),
        (
            ActionOutcomeAssessment.NOT_EXECUTED,
            False,
            False,
            False,
            EvidenceAuthority.ATTEMPT_NOT_EXECUTED,
        ),
        (
            ActionOutcomeAssessment.UNKNOWN,
            True,
            None,
            False,
            EvidenceAuthority.ATTEMPT_UNKNOWN,
        ),
        (
            ActionOutcomeAssessment.UNKNOWN,
            False,
            None,
            False,
            EvidenceAuthority.ATTEMPT_NOT_EXECUTED,
        ),
    ],
)
def test_action_authority_is_the_exact_conjunction_of_controller_and_effect(
    assessment: ActionOutcomeAssessment,
    executed: bool,
    causal: bool | None,
    verified: bool,
    expected: EvidenceAuthority,
) -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=1)
    ledger.record_action_outcome(
        action_outcome().model_copy(
            update={
                "assessment": assessment,
                "executed": executed,
                "causal_revision_advanced": causal,
                "controller_verified": verified,
            }
        )
    )
    context = planner_context(
        observation(),
        ledger=ledger,
        store=None,
        brief_ids=set(),
    )

    snapshot = resolve_evidence_reference(
        ActionOutcomeEvidence(outcome_id="ao-1"),
        authored_context=context,
        ledger=ledger,
        store=None,
        advisor_brief_ids=set(),
    )

    assert snapshot.authority is expected


@pytest.mark.parametrize(
    "reference",
    [
        ActionOutcomeEvidence(outcome_id="ao-1"),
        PlanOutcomeEvidence(plan_outcome_id="po-1"),
        MemoryEvidence(memory_id="mem-missing"),
    ],
)
def test_a_manifest_identity_without_its_authority_record_fails_closed(
    reference: Any,
    tmp_path: Path,
) -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=1)
    with open_store(tmp_path / "memory.sqlite3") as store:
        context = planner_context(
            observation(),
            ledger=ledger,
            store=store,
            brief_ids=set(),
            action_outcome_ids=("ao-1",),
            plan_outcome_ids=("po-1",),
            memory_ids=("mem-missing",),
        )
        with pytest.raises(EvidenceResolutionError):
            resolve_evidence_reference(
                reference,
                authored_context=context,
                ledger=ledger,
                store=store,
                advisor_brief_ids=set(),
            )


def test_evidence_that_scrolled_out_of_the_window_renders_as_evicted() -> None:
    """Eviction may hide detail from context, but cannot erase what happened."""

    ledger = ContinuityLedger(
        run_id="run-a",
        action_outcome_limit=1,
        plan_outcome_limit=1,
    )
    ledger.record_action_outcome(action_outcome("ao-1"))
    ledger.record_action_outcome(action_outcome("ao-2"))
    for index in range(2):
        ledger.record_plan_outcome(
            plan_id="plan-a",
            plan_version=1,
            objective=f"Objective {index}.",
            disposition=PlanDisposition.ABANDONED,
            reason="Stopped.",
            completed_step_ids=[],
            actions_completed=0,
            terminal_revision=None,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )

        def render(reference: Any) -> str:
            return render_evidence_reference(
                reference,
                observation=observation(),
                ledger=ledger,
                store=None,
                advisor_brief_ids=set(),
                action_outcome_ids=("ao-1", "ao-2"),
                plan_outcome_ids=("po-1", "po-2"),
            )

    assert render(ActionOutcomeEvidence(outcome_id="ao-1")) == (
        "action_outcome(ao-1: no_op)"
    )
    assert render(ActionOutcomeEvidence(outcome_id="ao-2")) == (
        "action_outcome(ao-2: no_op)"
    )
    assert render(PlanOutcomeEvidence(plan_outcome_id="po-1")) == (
        "plan_outcome(po-1: abandoned)"
    )
    assert render(PlanOutcomeEvidence(plan_outcome_id="po-2")) == (
        "plan_outcome(po-2: abandoned)"
    )


def test_the_ledger_returns_the_record_that_was_asked_for() -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    for assessment in (
        ActionOutcomeAssessment.CHANGED,
        ActionOutcomeAssessment.NOT_EXECUTED,
    ):
        ledger.record_action_outcome(
            action_outcome(f"ao-{len(ledger.recent_action_outcomes) + 1}").model_copy(
                update={"assessment": assessment}
            )
        )
    for disposition in (PlanDisposition.COMPLETED, PlanDisposition.FAILED):
        ledger.record_plan_outcome(
            plan_id="plan-a",
            plan_version=1,
            objective="An objective.",
            disposition=disposition,
            reason="A reason.",
            completed_step_ids=[],
            actions_completed=0,
            terminal_revision=None,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )

    first_action = ledger.action_outcome("ao-1")
    second_action = ledger.action_outcome("ao-2")
    first_plan = ledger.plan_outcome("po-1")
    second_plan = ledger.plan_outcome("po-2")

    assert first_action is not None and second_action is not None
    assert first_action.assessment is ActionOutcomeAssessment.CHANGED
    assert second_action.assessment is ActionOutcomeAssessment.NOT_EXECUTED
    assert first_plan is not None and second_plan is not None
    assert first_plan.disposition is PlanDisposition.COMPLETED
    assert second_plan.disposition is PlanDisposition.FAILED
    assert ledger.action_outcome("ao-9") is None
    assert ledger.plan_outcome("po-9") is None


def test_each_plan_outcome_keeps_every_field_it_was_given() -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    started = datetime.now(UTC)
    revision = WorldStateRevision(telemetry_sequence=11, frame_sequence=7)

    first = ledger.record_plan_outcome(
        plan_id="plan-a",
        plan_version=3,
        objective="Deliver the canisters.",
        disposition=PlanDisposition.TERMINATED,
        reason="The environment ended the episode.",
        # More than the record may hold: the oldest are dropped, not the newest,
        # and the bound is exactly the bound.
        completed_step_ids=[f"step{index}" for index in range(20)],
        actions_completed=4,
        terminal_revision=revision,
        started_at=started,
        finished_at=started,
    )
    second = ledger.record_plan_outcome(
        plan_id="plan-b",
        plan_version=1,
        objective="Then find work.",
        disposition=PlanDisposition.FAILED,
        reason="No work was on offer.",
        completed_step_ids=[],
        actions_completed=0,
        terminal_revision=None,
        started_at=started,
        finished_at=started,
    )

    assert (first.plan_outcome_id, second.plan_outcome_id) == ("po-1", "po-2")
    assert first.actions_completed == 4
    assert first.terminal_revision == revision
    assert len(first.completed_step_ids) == 16
    assert first.completed_step_ids == [f"step{index}" for index in range(16)]
    assert second.terminal_revision is None
    assert second.actions_completed == 0


def test_advice_is_rendered_as_advice_and_not_as_world_evidence() -> None:
    rendered = render_evidence_reference(
        AdvisorBriefEvidence(brief_id=BRIEF_ID),
        observation=observation(),
        ledger=ledger_with_evidence(),
        store=None,
        advisor_brief_ids={BRIEF_ID},
    )

    assert "advice" in rendered


def test_the_current_observation_reference_carries_its_exact_revision() -> None:
    rendered = render_evidence_reference(
        CurrentObservationEvidence(),
        observation=observation(),
        ledger=ledger_with_evidence(),
        store=None,
        advisor_brief_ids=set(),
    )

    assert "telemetry_sequence=3" in rendered
    assert "frame_sequence=2" in rendered


@pytest.mark.parametrize(
    "reference",
    [
        ActionOutcomeEvidence(outcome_id="ao-404"),
        PlanOutcomeEvidence(plan_outcome_id="po-404"),
        MemoryEvidence(memory_id="mem-0404"),
        AdvisorBriefEvidence(brief_id=OTHER_BRIEF_ID),
    ],
)
def test_an_invented_identity_never_resolves(
    reference: Any,
    tmp_path: Path,
) -> None:
    """Each branch fails independently: one real ID cannot carry four."""

    ledger = ledger_with_evidence()

    with open_store(tmp_path / "memory.sqlite3") as store:
        keep_fact(store, "A remembered fact.", run_id="run-a")
        with pytest.raises(EvidenceResolutionError):
            render_evidence_reference(
                reference,
                observation=observation(),
                ledger=ledger,
                store=store,
                advisor_brief_ids={BRIEF_ID},
            )


def test_a_memory_from_another_namespace_is_not_citable(tmp_path: Path) -> None:
    """Campaigns do not bleed, and neither do their evidence IDs."""

    path = tmp_path / "memory.sqlite3"
    with open_store(path, "other-campaign") as other:
        foreign_id = keep_fact(other, "Another campaign's fact.", run_id="run-z").memory_id

    with open_store(path, "test") as store:
        with pytest.raises(EvidenceResolutionError):
            render_evidence_reference(
                MemoryEvidence(memory_id=foreign_id),
                observation=observation(),
                ledger=ledger_with_evidence(),
                store=store,
                advisor_brief_ids=set(),
            )


def test_a_memory_reference_needs_a_store_to_resolve_against() -> None:
    """Unknown stays unknown: an unavailable store is not a confident yes."""

    with pytest.raises(EvidenceResolutionError):
        render_evidence_reference(
            MemoryEvidence(memory_id="mem-0001"),
            observation=observation(),
            ledger=ledger_with_evidence(),
            store=None,
            advisor_brief_ids=set(),
        )


# --------------------------------------------------------------------------
# Grounding: no future success enters memory
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [MemoryKind.FACT, MemoryKind.EPISODE])
def test_an_ungrounded_fact_or_episode_is_rejected(
    kind: MemoryKind,
    tmp_path: Path,
) -> None:
    """A plan cannot remember the delivery it is only about to attempt."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        receipts = apply_operations(
            engine,
            [keep(kind, "I delivered the cargo.")],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
            plan_id="plan-a",
            plan_version=1,
        )

        assert [receipt.status for receipt in receipts] == [
            ContinuityOperationStatus.REJECTED
        ]
        assert store.recall(limit=8) == []


@pytest.mark.parametrize(
    "reference",
    [
        AdvisorBriefEvidence(brief_id=BRIEF_ID),
        PlanOutcomeEvidence(plan_outcome_id="po-1"),
    ],
)
def test_advice_or_plan_disposition_cannot_be_the_only_ground_for_a_fact(
    reference: Any,
    tmp_path: Path,
) -> None:
    """A recommendation or finished plan is not an observation of the world."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger_with_evidence(), brief_ids={BRIEF_ID})

        receipt = apply_one(
            engine,
            keep(
                MemoryKind.FACT,
                "The cargo reached its destination.",
                references=[reference],
            ),
        )

        assert receipt.status is ContinuityOperationStatus.REJECTED
        assert len(receipt.resolved_evidence) == 1
        assert store.recall(limit=8) == []


def test_a_memory_only_reference_cannot_bootstrap_a_new_world_fact(
    tmp_path: Path,
) -> None:
    """Agent-authored belief cannot promote itself into world evidence."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        prior = store.keep(
            "run-a",
            kind=MemoryKind.HYPOTHESIS,
            content="The trader may have received the cargo.",
            salience=0.5,
            grounding=None,
        )
        engine, _ = authority(store, ledger_with_evidence())

        receipt = apply_one(
            engine,
            keep(
                MemoryKind.FACT,
                "The trader received the cargo.",
                references=[MemoryEvidence(memory_id=prior.memory_id)],
            ),
        )

        assert receipt.status is ContinuityOperationStatus.REJECTED
        assert [record.kind for record in store.recall(limit=8)] == [
            MemoryKind.HYPOTHESIS
        ]


def test_a_stale_observation_cannot_ground_a_fresh_world_fact(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger_with_evidence())

        receipt = apply_operations(
            engine,
            [
                keep(
                    MemoryKind.FACT,
                    "The gate is open.",
                    references=[CurrentObservationEvidence()],
                )
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(stale=True),
            plan_id="plan-a",
            plan_version=1,
        )[0]

        assert receipt.status is ContinuityOperationStatus.REJECTED
        assert store.recall(limit=8) == []


def test_controller_verified_world_effect_can_ground_a_fact(
    tmp_path: Path,
) -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(
        action_outcome().model_copy(
            update={
                "assessment": ActionOutcomeAssessment.CHANGED,
                "causal_revision_advanced": True,
                "controller_verified": True,
                "semantic_status": "transferred",
                "target_id": "resource-copper",
            }
        )
    )
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger)

        receipt = apply_one(
            engine,
            keep(
                MemoryKind.FACT,
                "Copper was transferred from the resource.",
                references=[ActionOutcomeEvidence(outcome_id="ao-1")],
            ),
        )

        assert receipt.status is ContinuityOperationStatus.ACCEPTED
        assert receipt.resolved_evidence[0].authority.value == (
            "verified_world_effect"
        )
        assert receipt.resolved_evidence[0].semantic_status == "transferred"
        assert receipt.resolved_evidence[0].target_id == "resource-copper"


def test_uncausal_observed_change_cannot_ground_a_world_fact(
    tmp_path: Path,
) -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(
        action_outcome().model_copy(
            update={
                "assessment": ActionOutcomeAssessment.CHANGED,
                "causal_revision_advanced": None,
                "controller_verified": False,
            }
        )
    )
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger)

        receipt = apply_one(
            engine,
            keep(
                MemoryKind.FACT,
                "The intended world effect happened.",
                references=[ActionOutcomeEvidence(outcome_id="ao-1")],
            ),
        )

        assert receipt.status is ContinuityOperationStatus.REJECTED
        assert receipt.resolved_evidence[0].authority is (
            EvidenceAuthority.OBSERVED_CHANGE
        )
        assert store.recall(limit=8) == []


@pytest.mark.parametrize(
    "assessment",
    [
        ActionOutcomeAssessment.NO_OP,
        ActionOutcomeAssessment.NOT_EXECUTED,
        ActionOutcomeAssessment.UNKNOWN,
    ],
)
def test_failed_attempt_can_ground_an_episode_without_becoming_success(
    assessment: ActionOutcomeAssessment,
    tmp_path: Path,
) -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(
        action_outcome().model_copy(update={"assessment": assessment})
    )
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger)

        receipt = apply_one(
            engine,
            keep(
                MemoryKind.EPISODE,
                f"The attempt ended as {assessment.value}.",
                references=[ActionOutcomeEvidence(outcome_id="ao-1")],
            ),
        )

        assert receipt.status is ContinuityOperationStatus.ACCEPTED
        assert receipt.resolved_evidence[0].assessment is assessment
        stored = store.get(receipt.memory_id)
        assert stored is not None and stored.latest_provenance is not None
        assert stored.latest_provenance.resolved_evidence[0].assessment is assessment


@pytest.mark.parametrize("kind", [MemoryKind.COMMITMENT, MemoryKind.HYPOTHESIS])
def test_an_intention_or_an_uncertainty_may_be_self_authored(
    kind: MemoryKind,
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        receipts = apply_operations(
            engine,
            [keep(kind, "Next: leave the bar and look for work.")],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
            plan_id="plan-a",
            plan_version=1,
        )

        assert [receipt.status for receipt in receipts] == [
            ContinuityOperationStatus.ACCEPTED
        ]
        assert [record.kind for record in store.recall(limit=8)] == [kind]


def test_a_self_authored_intention_still_needs_its_stated_evidence_to_exist(
    tmp_path: Path,
) -> None:
    """Zero references is permission to be self-authored, not to invent one."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        receipts = apply_operations(
            engine,
            [
                keep(
                    MemoryKind.COMMITMENT,
                    "Leave the bar.",
                    references=[ActionOutcomeEvidence(outcome_id="ao-77")],
                )
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
            plan_id="plan-a",
            plan_version=1,
        )

        assert receipts[0].status is ContinuityOperationStatus.REJECTED
        assert store.recall(limit=8) == []


def test_stored_grounding_is_rendered_by_the_runtime_not_written_by_the_model(
    tmp_path: Path,
) -> None:
    """The free-text claim was the hole: a write could name an outcome it did
    not have. Grounding now comes only from resolved references."""

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(action_outcome("ao-1"))
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger)

        apply_operations(
            engine,
            [
                keep(
                    MemoryKind.EPISODE,
                    "I tried to get work from the barman and nothing changed.",
                    references=[ActionOutcomeEvidence(outcome_id="ao-1")],
                )
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
            plan_id="plan-a",
            plan_version=1,
        )

        record = store.recall(limit=8)[0]

    assert record.grounding is not None
    assert "ao-1" in record.grounding
    assert "no_op" in record.grounding


def test_a_target_id_absent_from_the_fresh_observation_is_rejected(
    tmp_path: Path,
) -> None:
    """A remembered or invented entity ID cannot bind a new memory."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))
        current = observation(target_ids=("entity-present",))

        rejected = apply_operations(
            engine,
            [keep(MemoryKind.COMMITMENT, "Trade here.", target_id="entity-remembered")],
            origin=ContinuityOrigin.PLAN,
            observation=current,
            plan_id="plan-a",
            plan_version=1,
        )
        accepted = apply_operations(
            engine,
            [keep(MemoryKind.COMMITMENT, "Trade here.", target_id="entity-present")],
            origin=ContinuityOrigin.PLAN,
            observation=current,
            plan_id="plan-a",
            plan_version=1,
        )
        bound = store.recall(limit=0, target_ids={"entity-present"}, entity_limit=4)

    assert rejected[0].status is ContinuityOperationStatus.REJECTED
    assert accepted[0].status is ContinuityOperationStatus.ACCEPTED
    assert [record.target_id for record in bound] == ["entity-present"]


def test_stale_telemetry_offers_no_current_target_at_all(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        receipts = apply_operations(
            engine,
            [keep(MemoryKind.COMMITMENT, "Trade here.", target_id="entity-present")],
            origin=ContinuityOrigin.PLAN,
            observation=observation(target_ids=("entity-present",), stale=True),
            plan_id="plan-a",
            plan_version=1,
        )

        assert receipts[0].status is ContinuityOperationStatus.REJECTED
        assert store.recall(limit=8) == []


def test_one_invalid_operation_does_not_take_the_valid_one_with_it(
    tmp_path: Path,
) -> None:
    """A rejected continuity update cannot corrupt the rest of the batch."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, events = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )

        receipts = apply_operations(
            engine,
            [
                keep(MemoryKind.FACT, "Unsupported claim."),
                keep(MemoryKind.COMMITMENT, "Leave the bar."),
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
            plan_id="plan-a",
            plan_version=1,
        )

        assert [receipt.status for receipt in receipts] == [
            ContinuityOperationStatus.REJECTED,
            ContinuityOperationStatus.ACCEPTED,
        ]
        assert [record.content for record in store.recall(limit=8)] == ["Leave the bar."]
    assert [event for event, _ in events].count("continuity_receipt") == 2


def test_every_operation_leaves_a_receipt_naming_its_origin(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        for origin in ContinuityOrigin:
            receipts = apply_operations(
                engine,
                [keep(MemoryKind.COMMITMENT, f"Intent from {origin.value}.")],
                origin=origin,
                observation=observation(),
                plan_id="plan-a",
                plan_version=1,
            )
            assert receipts[0].origin is origin
            assert receipts[0].status is ContinuityOperationStatus.ACCEPTED
            assert receipts[0].memory_id is not None


def test_every_operation_receives_one_unique_runtime_owned_receipt_id(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )
        receipts = apply_operations(
            engine,
            [
                keep(MemoryKind.COMMITMENT, "Deliver the copper."),
                keep(MemoryKind.FACT, "An unsupported world claim."),
                keep(MemoryKind.HYPOTHESIS, "The trader may buy copper."),
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
        )

    receipt_ids = [receipt.receipt_id for receipt in receipts]
    assert len(receipt_ids) == len(set(receipt_ids)) == 3
    assert all(
        receipt_id.startswith("cor-") and len(receipt_id) == 36
        for receipt_id in receipt_ids
    )


def test_receipt_digest_preserves_exact_corrective_and_result_context(
    tmp_path: Path,
) -> None:
    current = observation()
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )
        receipt = apply_operations(
            engine,
            [
                keep(
                    MemoryKind.FACT,
                    "The gate is open.",
                    references=[CurrentObservationEvidence()],
                )
            ],
            origin=ContinuityOrigin.PATCH,
            observation=current,
            plan_id="plan-a",
            plan_version=3,
            step_id="inspect-gate",
        )[0]

    digest = receipt.digest()
    assert digest.receipt_id == receipt.receipt_id
    assert digest.origin is ContinuityOrigin.PATCH
    assert digest.operation == "keep"
    assert digest.status is ContinuityOperationStatus.ACCEPTED
    assert digest.reason == receipt.reason
    assert digest.memory_id == receipt.memory_id
    assert digest.memory_status is MemoryStatus.ACTIVE
    assert digest.authored_context_id == "pc-1"
    assert digest.authored_revision == current.world_revision
    assert digest.commit_revision == current.world_revision
    assert digest.plan_id == "plan-a"
    assert digest.plan_version == 3
    assert digest.step_id == "inspect-gate"
    assert digest.evidence_summary == receipt.evidence
    assert digest.recorded_at == receipt.recorded_at
    assert not digest.writes_degraded


class _FailingProjectionStore(MemoryStore):
    """Inject one unexpected database failure after event append."""

    projection_attempts = 0

    def _insert_projection(self, record: MemoryRecord) -> None:
        self.projection_attempts += 1
        raise sqlite3.OperationalError("injected projection failure")


class _FailingReadStore(MemoryStore):
    """Inject an unexpected database failure at an authority read boundary."""

    def get(self, memory_id: str) -> MemoryRecord | None:
        raise sqlite3.OperationalError("injected continuity read failure")


class _FailingDeliveryStore(MemoryStore):
    """Inject an unexpected failure in the diagnostic delivery write."""

    delivery_attempts = 0

    def record_delivery(self, run_id: str, memory_ids: Sequence[str]) -> None:
        self.delivery_attempts += 1
        raise sqlite3.OperationalError("injected delivery failure")


class _FailingRecallStore(MemoryStore):
    """Inject one read-side failure before automatic recall can return."""

    recall_attempts = 0

    def recall_tiered(self, **kwargs: Any) -> Any:
        self.recall_attempts += 1
        raise sqlite3.DatabaseError("injected recall failure")


class _FailingSearchStore(MemoryStore):
    """Inject one read-side failure in an elective memory search."""

    search_attempts = 0

    def search(self, *, query: str, limit: int) -> Any:
        self.search_attempts += 1
        raise sqlite3.DatabaseError("injected search failure")


def test_evidence_resolution_store_failure_preserves_the_exact_diagnostic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    with _FailingReadStore(
        path,
        CampaignScope(campaign_id="test", origin=CampaignScopeOrigin.CONFIGURED),
        memory_id_factory=lambda: "mem-0001",
    ) as store:
        cited = keep_fact(store, "The earlier route was unsafe.")
        engine, _ = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )
        receipt = apply_operations(
            engine,
            [
                keep(
                    MemoryKind.HYPOTHESIS,
                    "The route may still be unsafe.",
                    references=[MemoryEvidence(memory_id=cited.memory_id)],
                )
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
        )[0]

        expected_reason = (
            "Durable continuity reads and writes are disabled for this run "
            "after "
            "an unexpected store failure "
            "(OperationalError: injected continuity read failure)."
        )
        assert receipt.status is ContinuityOperationStatus.FAILED
        assert receipt.reason == expected_reason
        assert receipt.evidence is None
        assert receipt.resolved_evidence == []
        assert receipt.writes_degraded
        assert engine.reads_degraded_reason == expected_reason
        assert engine.writes_degraded_reason == expected_reason


def test_admissibility_store_failure_retains_already_resolved_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    with open_store(path) as initial:
        commitment = initial.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the copper.",
            salience=0.5,
            grounding=None,
        )

    with _FailingReadStore(
        path,
        CampaignScope(campaign_id="test", origin=CampaignScopeOrigin.CONFIGURED),
    ) as store:
        engine, _ = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )
        receipt = apply_operations(
            engine,
            [
                ResolveMemoryOperation(
                    memory_id=commitment.memory_id,
                    reason="The copper was delivered.",
                    references=[CurrentObservationEvidence()],
                )
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
        )[0]

        expected_reason = (
            "Durable continuity reads and writes are disabled for this run "
            "after "
            "an unexpected store failure "
            "(OperationalError: injected continuity read failure)."
        )
        assert receipt.status is ContinuityOperationStatus.FAILED
        assert receipt.reason == expected_reason
        assert receipt.evidence is None
        assert len(receipt.resolved_evidence) == 1
        assert receipt.resolved_evidence[0].authority is (
            EvidenceAuthority.FRESH_WORLD_OBSERVATION
        )
        assert receipt.writes_degraded
        assert engine.reads_degraded_reason == expected_reason
        assert engine.writes_degraded_reason == expected_reason


def test_unexpected_store_failure_is_failed_rolled_back_and_quarantines_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    identities = iter(f"mem-{index:04d}" for index in range(1, 10))
    with _FailingProjectionStore(
        path,
        CampaignScope(campaign_id="test", origin=CampaignScopeOrigin.CONFIGURED),
        memory_id_factory=lambda: next(identities),
    ) as store:
        engine, events = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )
        receipts = apply_operations(
            engine,
            [
                keep(
                    MemoryKind.FACT,
                    "The gate is open.",
                    references=[CurrentObservationEvidence()],
                ),
                keep(MemoryKind.HYPOTHESIS, "The trader may buy copper."),
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
        )

        assert [receipt.status.value for receipt in receipts] == ["failed", "failed"]
        assert all(receipt.memory_id is None for receipt in receipts)
        assert all(receipt.writes_degraded for receipt in receipts)
        expected_reason = (
            "Durable continuity writes are disabled for this run after "
            "an unexpected store failure "
            "(OperationalError: injected projection failure)."
        )
        assert receipts[0].reason == expected_reason
        assert receipts[1].reason == expected_reason
        assert receipts[0].evidence == (
            "current_observation(telemetry_sequence=3, frame_sequence=2)"
        )
        assert len(receipts[0].resolved_evidence) == 1
        assert receipts[0].resolved_evidence[0].authority is (
            EvidenceAuthority.FRESH_WORLD_OBSERVATION
        )
        assert receipts[1].evidence is None
        assert receipts[1].resolved_evidence == []
        assert engine.writes_degraded_reason == expected_reason
        assert store.projection_attempts == 1
        assert store.event_count() == 0
        logged = [payload["payload"] for event, payload in events if event == "continuity_receipt"]
        assert [item["status"] for item in logged] == ["failed", "failed"]

    with open_store(path) as reopened:
        assert reopened.event_count() == 0
        assert reopened.all_records() == []


def test_store_quarantine_preserves_the_first_failure_per_health_boundary(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=0),
        )

        first_write = engine.quarantine_writes_after_store_failure(
            sqlite3.OperationalError("first write")
        )
        repeated_write = engine.quarantine_writes_after_store_failure(
            sqlite3.DatabaseError("later write")
        )
        first_read = engine.quarantine_reads_after_store_failure(
            sqlite3.DatabaseError("first read")
        )
        repeated_read = engine.quarantine_reads_after_store_failure(
            sqlite3.OperationalError("later read")
        )

    assert first_write == repeated_write
    assert "OperationalError: first write" in first_write
    assert "later write" not in first_write
    assert first_read == repeated_read
    assert "DatabaseError: first read" in first_read
    assert "later read" not in first_read
    assert engine.writes_degraded_reason == first_write
    assert engine.reads_degraded_reason == first_read


@pytest.mark.parametrize(
    ("operation", "expected_status", "has_memory_id", "expected_evidence"),
    [
        (
            keep(MemoryKind.FACT, "Unsupported."),
            ContinuityOperationStatus.REJECTED,
            False,
            None,
        ),
        (
            keep(
                MemoryKind.FACT,
                "Grounded.",
                references=[CurrentObservationEvidence()],
            ),
            ContinuityOperationStatus.ACCEPTED,
            True,
            "current_observation(telemetry_sequence=3, frame_sequence=2)",
        ),
    ],
)
def test_a_receipt_names_the_exact_plan_step_and_grounding_it_came_from(
    operation: KeepMemoryOperation,
    expected_status: ContinuityOperationStatus,
    has_memory_id: bool,
    expected_evidence: str | None,
    tmp_path: Path,
) -> None:
    """A receipt with no provenance cannot be reconciled against anything."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, events = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )

        receipt = apply_operations(
            engine,
            [operation],
            origin=ContinuityOrigin.PATCH,
            observation=observation().model_copy(update={"step_index": 7}),
            plan_id="plan-a",
            plan_version=4,
            step_id="walk",
        )[0]

    assert receipt.status is expected_status
    assert receipt.plan_id == "plan-a"
    assert receipt.plan_version == 4
    assert receipt.step_id == "walk"
    assert receipt.evidence == expected_evidence
    assert (receipt.memory_id is not None) is has_memory_id
    assert events[0][1]["step_index"] == 7


def test_a_no_op_receipt_still_reports_the_grounding_it_resolved() -> None:
    engine, _ = authority(None, ledger_with_evidence())

    receipt = apply_operations(
        engine,
        [
            keep(
                MemoryKind.EPISODE,
                "The route failed.",
                references=[ActionOutcomeEvidence(outcome_id="ao-1")],
            )
        ],
        origin=ContinuityOrigin.PLAN,
        observation=observation(),
        plan_id="plan-a",
        plan_version=2,
        step_id="walk",
    )[0]

    assert receipt.status is ContinuityOperationStatus.NO_OP
    assert receipt.evidence == "action_outcome(ao-1: no_op)"
    assert receipt.plan_id == "plan-a"
    assert receipt.plan_version == 2
    assert receipt.step_id == "walk"
    assert receipt.memory_id is None
    assert [snapshot.authority for snapshot in receipt.resolved_evidence] == [
        EvidenceAuthority.ATTEMPT_NO_OP
    ]


def test_several_references_are_joined_into_one_readable_grounding(
    tmp_path: Path,
) -> None:
    ledger = ledger_with_evidence()
    with open_store(tmp_path / "memory.sqlite3") as store:
        memory_id = keep_fact(store, "An earlier fact.", run_id="run-a").memory_id
        engine, _ = authority(store, ledger, brief_ids={BRIEF_ID})

        receipt = apply_operations(
            engine,
            [
                keep(
                    MemoryKind.EPISODE,
                    "The route failed twice.",
                    references=[
                        ActionOutcomeEvidence(outcome_id="ao-1"),
                        PlanOutcomeEvidence(plan_outcome_id="po-1"),
                        MemoryEvidence(memory_id=memory_id),
                        AdvisorBriefEvidence(brief_id=BRIEF_ID),
                    ],
                )
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
            plan_id="plan-a",
            plan_version=1,
        )[0]
        stored = next(
            record for record in store.recall(limit=8)
            if record.content == "The route failed twice."
        )

    assert receipt.status is ContinuityOperationStatus.ACCEPTED
    assert receipt.evidence == (
        "action_outcome(ao-1: no_op); "
        "plan_outcome(po-1: completed); "
        f"memory {memory_id}; "
        f"advisor_brief({BRIEF_ID}, advice not world evidence)"
    )
    assert stored.grounding == receipt.evidence
    # The store's own bound is the bound; four references cannot approach it.
    assert len(receipt.evidence) < 1000


def test_declared_salience_reaches_the_store_unchanged(tmp_path: Path) -> None:
    """Salience is the only ranking signal the agent controls."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        apply_operations(
            engine,
            [
                keep(MemoryKind.COMMITMENT, "Low priority.", salience=0.1),
                keep(MemoryKind.COMMITMENT, "High priority.", salience=0.9),
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
            plan_id="plan-a",
            plan_version=1,
        )
        records = store.recall(limit=8)

    assert [(record.content, record.salience) for record in records] == [
        ("High priority.", 0.9),
        ("Low priority.", 0.1),
    ]


def test_continuity_is_a_no_op_without_a_store_and_never_pretends_otherwise() -> None:
    engine, _ = authority(None, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

    receipts = apply_operations(
        engine,
        [keep(MemoryKind.COMMITMENT, "Leave the bar.")],
        origin=ContinuityOrigin.PLAN,
        observation=observation(),
        plan_id="plan-a",
        plan_version=1,
    )

    assert receipts[0].status is ContinuityOperationStatus.NO_OP
    assert receipts[0].memory_id is None


def test_no_operations_produce_no_receipts(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, events = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )

        assert (
            apply_operations(
                engine,
                [],
                origin=ContinuityOrigin.PATCH,
                observation=observation(),
                plan_id="plan-a",
                plan_version=1,
            )
            == []
        )

    assert events == []


# --------------------------------------------------------------------------
# Recall is not reinforcement
# --------------------------------------------------------------------------


def test_recall_writes_no_row_at_any_rate(tmp_path: Path) -> None:
    """The observation pump decorates ~10x/second. That cannot be a write."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        keep_fact(store, "A general fact.", run_id="run-a")
        keep_fact(store, "A bound fact.", target_id="entity-a", run_id="run-a")
        before = store._connection.total_changes

        for _ in range(20):
            store.recall(limit=8, target_ids={"entity-a"}, entity_limit=4)

        assert store._connection.total_changes == before


def test_reading_a_memory_never_raises_its_priority(tmp_path: Path) -> None:
    """Only an explicit accepted operation may reinforce."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        older = keep_fact(store, "Older tied fact.", target_id="entity-older")
        newer = keep_fact(store, "Newer tied fact.", target_id="entity-newer")
        assert older.created_at <= newer.created_at

        for _ in range(5):
            store.recall(limit=0, target_ids={"entity-older"}, entity_limit=1)
        store.record_delivery("run-a", [older.memory_id])

        records = store.recall(
            limit=0,
            target_ids={"entity-older", "entity-newer"},
            entity_limit=2,
        )

    assert [record.target_id for record in records] == ["entity-newer", "entity-older"]


def test_delivery_is_recorded_only_when_a_planner_payload_is_assembled(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        memory_id = keep_fact(store, "A fact.", run_id="run-a").memory_id

        assert store.recall(limit=4)[0].last_delivered_at is None

        store.record_delivery("run-a", [memory_id])
        delivered = store.recall(limit=4)[0].last_delivered_at

    assert delivered is not None
    assert delivered.utcoffset() is not None


def test_delivery_time_never_reorders_general_recall(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        first = keep_fact(store, "First fact.", run_id="run-a").memory_id
        keep_fact(store, "Second fact.", run_id="run-a")

        store.record_delivery("run-a", [first])

        assert [record.content for record in store.recall(limit=4)] == [
            "Second fact.",
            "First fact.",
        ]


def test_recording_delivery_of_nothing_touches_nothing(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        keep_fact(store, "A fact.", run_id="run-a")
        before = store._connection.total_changes

        store.record_delivery("run-a", [])

        assert store._connection.total_changes == before


def test_delivery_cannot_reach_another_campaigns_record(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with open_store(path, "other-campaign") as other:
        foreign_id = keep_fact(other, "Another campaign's fact.", run_id="run-z").memory_id

    with open_store(path, "test") as store:
        store.record_delivery("run-a", [foreign_id])

    with open_store(path, "other-campaign") as other:
        assert other.recall(limit=4)[0].last_delivered_at is None


# --------------------------------------------------------------------------
# Runtime timing, end to end
# --------------------------------------------------------------------------


def _single_step_runtime(
    tmp_path: Path,
    planner: Any,
    store: MemoryStore,
) -> tuple[Any, Any]:
    from kenshi_agent.config import MockConfig, SafetyConfig
    from kenshi_agent.env import MockEnvironment
    from kenshi_agent.reflexes import ReflexEngine
    from kenshi_agent.runtime import AgentRuntime
    from kenshi_agent.safety import ActionGuard
    from kenshi_agent.session_log import SessionLogger
    from kenshi_agent.skills import MacroRegistry

    environment = MockEnvironment(
        MockConfig(seed=11, random_events=False),
        tmp_path / "frames",
        "single-step",
    )
    logger = SessionLogger(tmp_path / "events.jsonl", "single-step")
    runtime = AgentRuntime(
        run_id="single-step",
        environment=environment,
        planner=planner,
        guard=ActionGuard(
            SafetyConfig(
                allow_action_kinds=["noop", "stop", "pause", "wait"],
                max_actions_per_minute=500,
            ),
            MacroRegistry({}),
        ),
        reflexes=ReflexEngine(),
        logger=logger,
        memory=store,
        memory_limit=12,
        minimum_memory_salience=0.0,
    )
    return runtime, logger


def test_a_single_step_decision_keeps_only_what_the_receipt_supports(
    tmp_path: Path,
) -> None:
    """One unsupported claim is rejected; the grounded one beside it is kept,
    and the action itself still runs."""

    import asyncio
    import json

    from kenshi_agent.models import PlannerDecision, StopAction
    from kenshi_agent.planners.base import Planner

    class ClaimingPlanner(Planner):
        async def decide(self, current: Observation) -> Any:
            return PlannerDecision(
                intent="Stop, and record two things.",
                rationale="One of these is not supported by any evidence.",
                action=StopAction(reason="done"),
                confidence=1.0,
                continuity_operations=[
                    keep(MemoryKind.FACT, "I already delivered the cargo."),
                    keep(
                        MemoryKind.COMMITMENT,
                        "Next: leave the bar.",
                    ),
                ],
            )

    async def scenario() -> None:
        with open_store(tmp_path / "memory.sqlite3", "single") as store:
            runtime, logger = _single_step_runtime(tmp_path, ClaimingPlanner(), store)
            try:
                summary = await runtime.run(max_steps=1)
            finally:
                logger.close()
            kept = [record.content for record in store.recall(limit=8)]

        assert summary.steps_completed == 1
        assert kept == ["Next: leave the bar."]

        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text().splitlines()
        ]
        receipts = [
            event["payload"]
            for event in events
            if event["event_type"] == "continuity_receipt"
        ]
        assert [receipt["status"] for receipt in receipts] == ["rejected", "accepted"]
        assert all(receipt["origin"] == "decision" for receipt in receipts)
        # The continuity operations are processed after the action receipt, so
        # the outcome that action produced already exists when they are.
        outcome_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "action_outcome"
        )
        receipt_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "continuity_receipt"
        )
        assert outcome_index < receipt_index

    asyncio.run(scenario())


def test_single_step_current_observation_stays_bound_to_the_planners_revision(
    tmp_path: Path,
) -> None:
    """Dispatch may advance the world, but it cannot rewrite authored evidence."""

    import asyncio

    from kenshi_agent.models import PlannerDecision
    from kenshi_agent.planners.base import Planner

    class RevisionClaimingPlanner(Planner):
        seen_revision: WorldStateRevision | None = None

        async def decide(self, current: Observation) -> Any:
            self.seen_revision = current.world_revision
            return PlannerDecision(
                intent="Stop and keep what was visible before stopping.",
                rationale="The fact cites the exact observation used for this decision.",
                action=StopAction(reason="done"),
                confidence=1.0,
                continuity_operations=[
                    keep(
                        MemoryKind.FACT,
                        "The mock character was visible.",
                        references=[CurrentObservationEvidence()],
                    )
                ],
            )

    async def scenario() -> None:
        with open_store(tmp_path / "memory.sqlite3", "single") as store:
            planner = RevisionClaimingPlanner()
            runtime, logger = _single_step_runtime(tmp_path, planner, store)
            try:
                await runtime.run(max_steps=1)
            finally:
                logger.close()
            record = store.recall(limit=8)[0]

        assert planner.seen_revision is not None
        assert record.grounding == (
            "current_observation("
            f"telemetry_sequence={planner.seen_revision.telemetry_sequence}, "
            f"frame_sequence={planner.seen_revision.frame_sequence})"
        )

    asyncio.run(scenario())


def test_runtime_continuity_receipt_feedback_remains_bounded(
    tmp_path: Path,
) -> None:
    import asyncio

    from kenshi_agent.models import PlannerDecision
    from kenshi_agent.planners.base import Planner

    class ManyInvalidOperationsPlanner(Planner):
        async def decide(self, current: Observation) -> Any:
            return PlannerDecision(
                intent="Stop after exercising bounded continuity feedback.",
                rationale="Each unsupported fact should receive its own receipt.",
                action=StopAction(reason="done"),
                confidence=1.0,
                continuity_operations=[
                    keep(MemoryKind.FACT, f"Unsupported fact {index}.")
                    for index in range(6)
                ],
            )

    async def scenario() -> None:
        with open_store(tmp_path / "memory.sqlite3", "single") as store:
            runtime, logger = _single_step_runtime(
                tmp_path,
                ManyInvalidOperationsPlanner(),
                store,
            )
            try:
                await runtime.run(max_steps=1)
            finally:
                logger.close()

            assert len(runtime._continuity_receipts) == 4
            assert all(
                receipt.status is ContinuityOperationStatus.REJECTED
                for receipt in runtime._continuity_receipts
            )
            assert len(
                {receipt.receipt_id for receipt in runtime._continuity_receipts}
            ) == 4

    asyncio.run(scenario())


def test_degraded_writer_does_not_record_later_planner_delivery(
    tmp_path: Path,
) -> None:
    import asyncio

    from kenshi_agent.models import NoopAction, PlannerDecision
    from kenshi_agent.planners.base import Planner

    class TwoTurnPlanner(Planner):
        calls = 0

        async def decide(self, current: Observation) -> Any:
            self.calls += 1
            if self.calls == 1:
                return PlannerDecision(
                    intent="Continue after the continuity failure.",
                    rationale="Gameplay remains valid even if memory writing fails.",
                    action=NoopAction(reason="continue"),
                    confidence=1.0,
                    continuity_operations=[
                        keep(MemoryKind.COMMITMENT, "Deliver the copper.")
                    ],
                )
            return PlannerDecision(
                intent="Stop.",
                rationale="The second planner turn observed degraded continuity.",
                action=StopAction(reason="done"),
                confidence=1.0,
            )

    async def scenario() -> None:
        path = tmp_path / "memory.sqlite3"
        with open_store(path, "single") as initial:
            delivered = keep_fact(initial, "An existing route fact.")

        ids = iter(f"mem-failure-{index}" for index in range(1, 10))
        with _FailingProjectionStore(
            path,
            CampaignScope(
                campaign_id="single",
                origin=CampaignScopeOrigin.CONFIGURED,
            ),
            memory_id_factory=lambda: next(ids),
        ) as store:
            runtime, logger = _single_step_runtime(tmp_path, TwoTurnPlanner(), store)
            try:
                await runtime.run(max_steps=2)
            finally:
                logger.close()

            delivery_events = [
                entry
                for entry in store.history(delivered.memory_id)
                if entry.event is MemoryLifecycleEvent.DELIVER
            ]
            assert len(delivery_events) == 1
            assert runtime._continuity.writes_degraded_reason is not None

    asyncio.run(scenario())


def test_delivery_diagnostic_failure_never_cancels_gameplay_and_reaches_next_planner(
    tmp_path: Path,
) -> None:
    import asyncio
    import json

    from kenshi_agent.models import NoopAction, PlannerDecision
    from kenshi_agent.planners.base import Planner

    class TwoTurnPlanner(Planner):
        seen_degraded_reasons: list[str | None] = []

        async def decide(self, current: Observation) -> Any:
            self.seen_degraded_reasons.append(
                current.continuity_writes_degraded_reason
            )
            if len(self.seen_degraded_reasons) == 1:
                return PlannerDecision(
                    intent="Continue despite a diagnostic write failure.",
                    rationale="World control does not depend on delivery bookkeeping.",
                    action=NoopAction(reason="continue"),
                    confidence=1.0,
                )
            return PlannerDecision(
                intent="Stop after observing the quarantined store.",
                rationale="The degraded state reached the next planner.",
                action=StopAction(reason="done"),
                confidence=1.0,
            )

    async def scenario() -> None:
        path = tmp_path / "memory.sqlite3"
        with open_store(path, "single") as initial:
            keep_fact(initial, "A route fact that will be delivered.")

        with _FailingDeliveryStore(
            path,
            CampaignScope(
                campaign_id="single",
                origin=CampaignScopeOrigin.CONFIGURED,
            ),
        ) as store:
            runtime, logger = _single_step_runtime(tmp_path, TwoTurnPlanner(), store)
            try:
                summary = await runtime.run(max_steps=2)
            finally:
                logger.close()

            expected_reason = (
                "Durable continuity writes are disabled for this run after "
                "an unexpected store failure "
                "(OperationalError: injected delivery failure)."
            )
            assert summary.steps_completed == 2
            assert TwoTurnPlanner.seen_degraded_reasons == [None, expected_reason]
            assert store.delivery_attempts == 1
            assert runtime._continuity.writes_degraded_reason == expected_reason

        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        failures = [
            event for event in events if event["event_type"] == "continuity_store_failed"
        ]
        assert len(failures) == 1
        assert failures[0]["payload"] == {
            "boundary": "record_delivery",
            "reason": expected_reason,
        }

    asyncio.run(scenario())


def test_automatic_recall_failure_quarantines_reads_and_writes_without_stopping_play(
    tmp_path: Path,
) -> None:
    import asyncio
    import json

    from kenshi_agent.models import NoopAction, PlannerDecision
    from kenshi_agent.planners.base import Planner

    class TwoTurnPlanner(Planner):
        seen_health: list[tuple[str | None, str | None]] = []

        async def decide(self, current: Observation) -> Any:
            self.seen_health.append(
                (
                    current.continuity_reads_degraded_reason,
                    current.continuity_writes_degraded_reason,
                )
            )
            if len(self.seen_health) == 1:
                return PlannerDecision(
                    intent="Continue after recall became unavailable.",
                    rationale="Current world control remains independent.",
                    action=NoopAction(reason="continue"),
                    confidence=1.0,
                )
            return PlannerDecision(
                intent="Stop after the health state remained stable.",
                rationale="The store was not retried blindly.",
                action=StopAction(reason="done"),
                confidence=1.0,
            )

    async def scenario() -> None:
        path = tmp_path / "memory.sqlite3"
        with open_store(path, "single") as initial:
            keep_fact(initial, "A route fact that cannot be recalled.")

        with _FailingRecallStore(
            path,
            CampaignScope(
                campaign_id="single",
                origin=CampaignScopeOrigin.CONFIGURED,
            ),
        ) as store:
            runtime, logger = _single_step_runtime(tmp_path, TwoTurnPlanner(), store)
            try:
                summary = await runtime.run(max_steps=2)
            finally:
                logger.close()

            expected_reason = (
                "Durable continuity reads and writes are disabled for this run "
                "after an unexpected store failure "
                "(DatabaseError: injected recall failure)."
            )
            assert summary.steps_completed == 2
            assert TwoTurnPlanner.seen_health == [
                (expected_reason, expected_reason),
                (expected_reason, expected_reason),
            ]
            assert store.recall_attempts == 1
            assert runtime._continuity.reads_degraded_reason == expected_reason
            assert runtime._continuity.writes_degraded_reason == expected_reason

        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        failures = [
            event for event in events if event["event_type"] == "continuity_store_failed"
        ]
        assert len(failures) == 1
        assert failures[0]["payload"] == {
            "boundary": "automatic_recall",
            "reason": expected_reason,
        }

    asyncio.run(scenario())


def test_an_issued_but_undelivered_outcome_cannot_ground_a_fact(
    tmp_path: Path,
) -> None:
    """Existence in the run is weaker than presence in the authored context."""

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(action_outcome("ao-1"))
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger)
        current = observation()

        receipt = engine.apply(
            [
                keep(
                    MemoryKind.FACT,
                    "The action succeeded.",
                    references=[ActionOutcomeEvidence(outcome_id="ao-1")],
                )
            ],
            origin=ContinuityOrigin.DECISION,
            authored_context=planner_context(
                current,
                ledger=ledger,
                store=store,
                brief_ids=set(),
                # Deliberately absent from the input delivered to the author.
                action_outcome_ids=(),
            ),
            commit_observation=current,
        )[0]

        assert receipt.status is ContinuityOperationStatus.REJECTED
        assert store.recall(limit=8) == []


def test_a_planner_context_from_another_run_has_no_continuity_authority(
    tmp_path: Path,
) -> None:
    """Run identity is an authority boundary, not receipt decoration."""

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger)
        foreign = observation().model_copy(update={"run_id": "run-b"})

        receipt = engine.apply(
            [keep(MemoryKind.COMMITMENT, "Continue the foreign run's plan.")],
            origin=ContinuityOrigin.DECISION,
            authored_context=planner_context(
                foreign,
                ledger=ledger,
                store=store,
                brief_ids=set(),
            ),
            commit_observation=observation(),
        )[0]

        assert receipt.status is ContinuityOperationStatus.REJECTED
        assert receipt.reason == "The planner context belongs to another run."
        assert store.recall(limit=8) == []


def test_decorating_observations_at_pump_rate_writes_nothing(tmp_path: Path) -> None:
    """`_with_memories` runs about ten times a second in a live run."""

    from kenshi_agent.config import PlanningConfig
    from kenshi_agent.runtime import AgentRuntime

    with open_store(tmp_path / "memory.sqlite3", "pump") as store:
        keep_fact(store, "A general fact.", run_id="run-a")
        keep_fact(store, "A bound fact.", target_id="entity-a", run_id="run-a")

        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner._recall_budget = RecallBudget(
            commitments=4,
            current_target=4,
            open_hypotheses=2,
            general=8,
        )
        runner.advisor = None
        runner._continuity_receipts = []
        runner._pending_memory_search = None
        runner._ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
        runner._continuity, _ = authority(store, runner._ledger)
        runner.planning_config = PlanningConfig()

        current = observation(target_ids=("entity-a",))
        before = store._connection.total_changes
        for _ in range(30):
            decorated = runner._with_memories(current)

        assert store._connection.total_changes == before
        assert len(decorated.memories) == 2


def test_delivery_marks_every_record_it_was_given(tmp_path: Path) -> None:
    """One placeholder per ID: a single-ID test would never notice the join."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        ids = [
            keep_fact(store, f"Fact {index}.", run_id="run-a").memory_id
            for index in range(4)
        ]

        store.record_delivery("run-a", ids[:3])
        delivered = {
            record.content: record.last_delivered_at is not None
            for record in store.recall(limit=8)
        }

    assert delivered == {
        "Fact 0.": True,
        "Fact 1.": True,
        "Fact 2.": True,
        "Fact 3.": False,
    }


def test_runtime_records_delivery_from_the_final_prepared_input_only(
    tmp_path: Path,
) -> None:
    import asyncio
    import json

    from kenshi_agent.models import PlannerDecision
    from kenshi_agent.planners.base import (
        Planner,
        PreparedPlannerInput,
        prepared_budgeted_input,
    )

    class BudgetingPlanner(Planner):
        included_memory_ids: set[str] = set()

        def prepare_input(
            self,
            current: Observation,
            *,
            context_id: str,
        ) -> PreparedPlannerInput:
            for budget in range(4000, 60001, 1000):
                payload = current.planner_payload(max_chars=budget)
                document = json.loads(payload)
                included = {
                    record["memory_id"] for record in document["memories"]
                }
                if included < {
                    record.memory_id for record in current.memories
                }:
                    self.included_memory_ids = included
                    return prepared_budgeted_input(
                        current,
                        context_id=context_id,
                        payload=payload,
                    )
            raise AssertionError("test setup did not force any memory omission")

        async def decide(self, current: Observation) -> Any:
            return PlannerDecision(
                intent="Stop.",
                rationale="The delivery manifest has been captured.",
                action=StopAction(reason="done"),
                confidence=1.0,
            )

    async def scenario() -> None:
        with open_store(tmp_path / "memory.sqlite3", "delivery") as store:
            for index in range(8):
                keep_fact(store, f"Fact {index}: " + "x" * 1200)
            planner = BudgetingPlanner()
            runtime, logger = _single_step_runtime(tmp_path, planner, store)
            try:
                await runtime.run(max_steps=1)
            finally:
                logger.close()
            delivered = {
                record.memory_id
                for record in store.recall(limit=16)
                if record.last_delivered_at is not None
            }
            all_ids = {record.memory_id for record in store.recall(limit=16)}

        assert delivered == planner.included_memory_ids
        assert delivered != all_ids

    asyncio.run(scenario())


def test_planner_context_is_logged_before_a_provider_failure(tmp_path: Path) -> None:
    import asyncio
    import json

    from kenshi_agent.planners.base import Planner

    class FailingPlanner(Planner):
        async def decide(self, current: Observation) -> Any:
            raise RuntimeError("provider unavailable")

    async def scenario() -> None:
        with open_store(tmp_path / "memory.sqlite3", "failure") as store:
            runtime, logger = _single_step_runtime(tmp_path, FailingPlanner(), store)
            try:
                await runtime.run(max_steps=1)
            finally:
                logger.close()

        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text().splitlines()
        ]
        context_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "planner_context_prepared"
        )
        failure_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "planner_error"
        )
        context = events[context_index]["payload"]
        assert context["context_id"] == "pc-1"
        assert context["input_kind"] == "full_observation"
        assert context_index < failure_index

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# The planner-facing lifecycle
# --------------------------------------------------------------------------


def lifecycle_authority(
    store: MemoryStore,
) -> tuple[ContinuityAuthority, list[tuple[str, dict[str, Any]]]]:
    return authority(store, ledger_with_evidence(), brief_ids={BRIEF_ID})


def apply_one(engine: ContinuityAuthority, operation: Any) -> Any:
    return apply_operations(
        engine,
        [operation],
        origin=ContinuityOrigin.PLAN,
        observation=observation(target_ids=("entity-present",)),
        plan_id="plan-a",
        plan_version=1,
    )[0]


def test_every_lifecycle_transition_reaches_the_store_through_apply(
    tmp_path: Path,
) -> None:
    """`_transition` is the whole planner-facing surface of the lifecycle.

    Testing only `keep` would leave four transitions wired to nothing, which is
    exactly the shape of the dead `PlanPatch` field this design replaced.
    """

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = lifecycle_authority(store)

        kept = apply_one(
            engine,
            keep(
                MemoryKind.COMMITMENT,
                "Deliver six sealed slop canisters.",
                target_id="entity-present",
                salience=0.6,
            ),
        )
        assert kept.status is ContinuityOperationStatus.ACCEPTED
        record = store.get(kept.memory_id)
        assert record is not None
        assert record.content == "Deliver six sealed slop canisters."
        assert record.target_id == "entity-present"
        assert record.salience == 0.6

        reinforced = apply_one(
            engine,
            ReinforceMemoryOperation(
                memory_id=kept.memory_id,
                salience=0.9,
                references=[ActionOutcomeEvidence(outcome_id="ao-1")],
            ),
        )
        assert reinforced.status is ContinuityOperationStatus.ACCEPTED
        assert reinforced.memory_id == kept.memory_id
        after_reinforce = store.get(kept.memory_id)
        assert after_reinforce is not None
        assert after_reinforce.reinforcement_count == 1
        assert after_reinforce.salience == 0.9
        assert after_reinforce.grounding == "action_outcome(ao-1: no_op)"
        assert after_reinforce.latest_provenance is not None
        assert after_reinforce.latest_provenance.operation.operation == "reinforce"

        superseded = apply_one(
            engine,
            SupersedeMemoryOperation(
                memory_id=kept.memory_id,
                kind=MemoryKind.COMMITMENT,
                content="Deliver five canisters; one was lost.",
                salience=0.7,
                target_id="entity-present",
                references=[CurrentObservationEvidence()],
            ),
        )
        assert superseded.status is ContinuityOperationStatus.ACCEPTED
        assert superseded.memory_id != kept.memory_id
        old = store.get(kept.memory_id)
        new = store.get(superseded.memory_id)
        assert old is not None and new is not None
        assert old.superseded_by_id == new.memory_id
        assert new.supersedes_id == kept.memory_id
        assert new.content == "Deliver five canisters; one was lost."
        assert new.salience == 0.7
        assert new.target_id == "entity-present"

        assert new.grounding == (
            "current_observation(telemetry_sequence=3, frame_sequence=2)"
        )
        assert new.latest_provenance is not None
        assert new.latest_provenance.operation.operation == "supersede"

        engine.ledger.record_action_outcome(
            action_outcome("ao-2").model_copy(
                update={
                    "assessment": ActionOutcomeAssessment.CHANGED,
                    "causal_revision_advanced": True,
                    "controller_verified": True,
                    "semantic_status": "cargo_delivered",
                }
            )
        )
        resolved = apply_one(
            engine,
            ResolveMemoryOperation(
                memory_id=new.memory_id,
                reason="All five were handed over.",
                references=[ActionOutcomeEvidence(outcome_id="ao-2")],
            ),
        )
        assert resolved.status is ContinuityOperationStatus.ACCEPTED
        closed = store.get(new.memory_id)
        assert closed is not None
        assert closed.resolution_reason == "All five were handed over."
        # The evidence that closed it replaces the evidence that opened it.
        assert closed.grounding == "action_outcome(ao-2: changed)"
        assert closed.latest_provenance is not None
        assert closed.latest_provenance.operation.operation == "resolve"

        hypothesis = apply_one(
            engine,
            keep(MemoryKind.HYPOTHESIS, "The trader may buy ore."),
        )
        retracted = apply_one(
            engine,
            RetractMemoryOperation(
                memory_id=hypothesis.memory_id,
                reason="Telemetry disproved it.",
            ),
        )
        assert retracted.status is ContinuityOperationStatus.ACCEPTED
        withdrawn = store.get(hypothesis.memory_id)
        assert withdrawn is not None
        assert withdrawn.resolution_reason == "Telemetry disproved it."
        assert withdrawn.latest_provenance is not None
        assert withdrawn.latest_provenance.operation.operation == "retract"
        assert store.recall(limit=8, target_ids={"entity-present"}, entity_limit=4) == []


def test_resolve_requires_evidence_and_only_closes_resolvable_kinds(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = lifecycle_authority(store)
        commitment = apply_one(
            engine,
            keep(MemoryKind.COMMITMENT, "Deliver the cargo."),
        )
        fact = apply_one(
            engine,
            keep(
                MemoryKind.FACT,
                "The gate is open.",
                references=[CurrentObservationEvidence()],
            ),
        )

        no_evidence = apply_one(
            engine,
            ResolveMemoryOperation(
                memory_id=commitment.memory_id,
                reason="Done.",
            ),
        )
        wrong_kind = apply_one(
            engine,
            ResolveMemoryOperation(
                memory_id=fact.memory_id,
                reason="Facts are revised, not resolved.",
                references=[CurrentObservationEvidence()],
            ),
        )

        assert no_evidence.status is ContinuityOperationStatus.REJECTED
        assert wrong_kind.status is ContinuityOperationStatus.REJECTED
        assert store.get(commitment.memory_id).status.value == "active"
        assert store.get(fact.memory_id).status.value == "active"


def test_evidence_capabilities_form_one_exhaustive_lifecycle_matrix(
    tmp_path: Path,
) -> None:
    """Every authority is judged by kind and transition, not by its wording."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = lifecycle_authority(store)
        commitment = store.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the cargo.",
            salience=0.5,
            grounding=None,
        )
        hypothesis = store.keep(
            "run-a",
            kind=MemoryKind.HYPOTHESIS,
            content="The gate may be open.",
            salience=0.5,
            grounding=None,
        )
        fact = keep(
            MemoryKind.FACT,
            "The world has this property.",
            references=[CurrentObservationEvidence()],
        )
        episode = keep(
            MemoryKind.EPISODE,
            "This attempt occurred.",
            references=[CurrentObservationEvidence()],
        )
        close_commitment = ResolveMemoryOperation(
            memory_id=commitment.memory_id,
            reason="The attempt ended.",
            disposition=MemoryResolutionDisposition.COMPLETED,
            references=[CurrentObservationEvidence()],
        )
        confirm_hypothesis = ResolveMemoryOperation(
            memory_id=hypothesis.memory_id,
            reason="The attempt answered it.",
            disposition=MemoryResolutionDisposition.CONFIRMED,
            references=[CurrentObservationEvidence()],
        )
        unknown_hypothesis = confirm_hypothesis.model_copy(
            update={"disposition": MemoryResolutionDisposition.UNKNOWN}
        )

        for evidence_authority in EvidenceAuthority:
            snapshot = ResolvedEvidenceSnapshot(
                source="advisor_brief",
                source_id=f"source-{evidence_authority.value}",
                authority=evidence_authority,
                authored_context_id="pc-1",
                run_id="run-a",
                compact_summary=evidence_authority.value,
            )
            resolved = [snapshot]

            assert (
                engine._admissibility_error(fact, resolved) is None
            ) is (evidence_authority in FACT_AUTHORITIES)
            assert (
                engine._admissibility_error(episode, resolved) is None
            ) is (evidence_authority in EPISODE_AUTHORITIES)
            assert (
                engine._admissibility_error(close_commitment, resolved) is None
            ) is (evidence_authority in COMMITMENT_CLOSURE_AUTHORITIES)
            assert (
                engine._admissibility_error(confirm_hypothesis, resolved) is None
            ) is (evidence_authority in FACT_AUTHORITIES)
            assert (
                engine._admissibility_error(unknown_hypothesis, resolved) is None
            ) is (evidence_authority in EPISODE_AUTHORITIES)

        missing = ResolveMemoryOperation(
            memory_id="mem-missing",
            reason="No such record.",
            references=[CurrentObservationEvidence()],
        )
        assert engine._admissibility_error(
            missing,
            [
                ResolvedEvidenceSnapshot(
                    source="current_observation",
                    source_id="pc-1:current_observation",
                    authority=EvidenceAuthority.FRESH_WORLD_OBSERVATION,
                    authored_context_id="pc-1",
                    run_id="run-a",
                    compact_summary="current observation",
                )
            ],
        ) is None


@pytest.mark.parametrize(
    "assessment",
    [
        ActionOutcomeAssessment.NO_OP,
        ActionOutcomeAssessment.NOT_EXECUTED,
        ActionOutcomeAssessment.UNKNOWN,
    ],
)
def test_non_effect_outcomes_cannot_close_a_world_commitment(
    assessment: ActionOutcomeAssessment,
    tmp_path: Path,
) -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(
        action_outcome().model_copy(update={"assessment": assessment})
    )
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = authority(store, ledger)
        kept = apply_one(
            engine,
            keep(MemoryKind.COMMITMENT, "Deliver the cargo."),
        )

        receipt = apply_one(
            engine,
            ResolveMemoryOperation(
                memory_id=kept.memory_id,
                reason="Delivered.",
                references=[ActionOutcomeEvidence(outcome_id="ao-1")],
            ),
        )

        assert receipt.status is ContinuityOperationStatus.REJECTED
        assert store.get(kept.memory_id).status.value == "active"


def test_hypothesis_resolution_preserves_confirmed_rejected_or_unknown(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = lifecycle_authority(store)
        missing_disposition = apply_one(
            engine,
            keep(MemoryKind.HYPOTHESIS, "The gate may be open."),
        )
        rejected = apply_one(
            engine,
            ResolveMemoryOperation(
                memory_id=missing_disposition.memory_id,
                reason="The gate is visibly closed.",
                references=[CurrentObservationEvidence()],
            ),
        )
        assert rejected.status is ContinuityOperationStatus.REJECTED

        resolved = apply_one(
            engine,
            ResolveMemoryOperation(
                memory_id=missing_disposition.memory_id,
                reason="The gate is visibly closed.",
                disposition=MemoryResolutionDisposition.REJECTED,
                references=[CurrentObservationEvidence()],
            ),
        )

        assert resolved.status is ContinuityOperationStatus.ACCEPTED
        record = store.get(missing_disposition.memory_id)
        assert record is not None
        assert record.resolution_disposition is (
            MemoryResolutionDisposition.REJECTED
        )


def test_accepted_lifecycle_event_persists_structured_canonical_provenance(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = lifecycle_authority(store)

        kept = apply_operations(
            engine,
            [
                keep(
                    MemoryKind.FACT,
                    "The gate is open.",
                    references=[CurrentObservationEvidence()],
                )
            ],
            origin=ContinuityOrigin.PATCH,
            observation=observation(target_ids=("entity-present",)),
            plan_id="plan-a",
            plan_version=3,
            step_id="observe-gate",
        )[0]

        event = store.history(kept.memory_id)[0]
        provenance = event.payload["provenance"]
        assert provenance["schema_version"] == 1
        assert provenance["operation"]["operation"] == "keep"
        assert provenance["operation"]["kind"] == "fact"
        assert provenance["operation"]["content"] == "The gate is open."
        assert provenance["origin"] == "patch"
        assert provenance["run_id"] == "run-a"
        assert provenance["authored_context_id"] == "pc-1"
        assert provenance["authored_revision"]["telemetry_sequence"] == 3
        assert provenance["authored_revision"]["frame_sequence"] == 2
        assert provenance["authored_revision"]["capability_epoch"] == 0
        assert provenance["authored_revision"]["observed_at_monotonic"] > 0.0
        assert provenance["commit_revision"] == provenance["authored_revision"]
        assert provenance["references"] == [{"source": "current_observation"}]
        assert provenance["resolved_evidence"][0]["authority"] == (
            "fresh_world_observation"
        )
        assert provenance["plan_id"] == "plan-a"
        assert provenance["plan_version"] == 3
        assert provenance["step_id"] == "observe-gate"
        assert provenance["rendered_grounding"] == (
            "current_observation(telemetry_sequence=3, frame_sequence=2)"
        )
        assert provenance["transition_result"] == "applied"
        before = store.get(kept.memory_id)
        assert before is not None
        assert before.latest_provenance is not None
        assert before.latest_provenance.plan_id == "plan-a"
        assert before.latest_provenance.plan_version == 3
        assert before.latest_provenance.step_id == "observe-gate"
        store.rebuild_projection()
        assert store.get(kept.memory_id) == before


def test_a_transition_on_a_closed_or_unknown_record_is_a_receipt_not_a_crash(
    tmp_path: Path,
) -> None:
    """Invariant: a rejected continuity update cannot corrupt gameplay."""

    from kenshi_agent.models import (
        ReinforceMemoryOperation,
        ResolveMemoryOperation,
        RetractMemoryOperation,
        SupersedeMemoryOperation,
    )

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = lifecycle_authority(store)
        kept = apply_one(engine, keep(MemoryKind.COMMITMENT, "Deliver the cargo."))
        apply_one(
            engine,
            RetractMemoryOperation(memory_id=kept.memory_id, reason="Abandoned."),
        )

        closed_and_unknown = [
            ReinforceMemoryOperation(memory_id=kept.memory_id),
            ResolveMemoryOperation(memory_id=kept.memory_id, reason="Done."),
            SupersedeMemoryOperation(
                memory_id=kept.memory_id,
                kind=MemoryKind.COMMITMENT,
                content="A replacement.",
            ),
            RetractMemoryOperation(memory_id=kept.memory_id, reason="Again."),
            ReinforceMemoryOperation(memory_id="mem-0404"),
            ResolveMemoryOperation(memory_id="mem-0404", reason="Done."),
            RetractMemoryOperation(memory_id="mem-0404", reason="Gone."),
        ]
        for operation in closed_and_unknown:
            receipt = apply_one(engine, operation)
            assert receipt.status is ContinuityOperationStatus.REJECTED
            assert receipt.memory_id is None
            # The references resolved; it was the transition that was refused,
            # and the receipt has to say which of the two failed.
            assert receipt.evidence is None
            assert receipt.plan_id == "plan-a"

        grounded_but_closed = apply_one(
            engine,
            ReinforceMemoryOperation(
                memory_id=kept.memory_id,
                references=[ActionOutcomeEvidence(outcome_id="ao-1")],
            ),
        )
        assert grounded_but_closed.status is ContinuityOperationStatus.REJECTED
        assert grounded_but_closed.evidence == "action_outcome(ao-1: no_op)"
        assert [
            snapshot.authority for snapshot in grounded_but_closed.resolved_evidence
        ] == [EvidenceAuthority.ATTEMPT_NO_OP]

        assert store.event_count() == 2


def test_active_key_conflict_is_a_rejected_receipt_with_no_partial_transition(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = lifecycle_authority(store)
        original = apply_one(
            engine,
            keep(MemoryKind.COMMITMENT, "Deliver the copper."),
        )
        conflicting = apply_one(
            engine,
            keep(MemoryKind.COMMITMENT, "Sell the copper."),
        )
        before_original = store.get(original.memory_id)
        before_conflicting = store.get(conflicting.memory_id)
        events_before = store.event_count()

        receipt = apply_one(
            engine,
            SupersedeMemoryOperation(
                memory_id=original.memory_id,
                kind=MemoryKind.COMMITMENT,
                content=" SELL   THE COPPER. ",
            ),
        )

        assert receipt.status is ContinuityOperationStatus.REJECTED
        assert receipt.memory_id is None
        assert "active memory" in receipt.reason
        assert conflicting.memory_id in receipt.reason
        assert store.get(original.memory_id) == before_original
        assert store.get(conflicting.memory_id) == before_conflicting
        assert store.event_count() == events_before


def test_a_superseding_replacement_is_held_to_the_same_grounding_rules(
    tmp_path: Path,
) -> None:
    """A replacement fact is still a fact, and still needs evidence."""

    from kenshi_agent.models import SupersedeMemoryOperation

    with open_store(tmp_path / "memory.sqlite3") as store:
        engine, _ = lifecycle_authority(store)
        kept = apply_one(
            engine,
            keep(
                MemoryKind.FACT,
                "The gate is open.",
                references=[CurrentObservationEvidence()],
            ),
        )

        ungrounded = apply_one(
            engine,
            SupersedeMemoryOperation(
                memory_id=kept.memory_id,
                kind=MemoryKind.FACT,
                content="The gate is closed and I already went through it.",
            ),
        )
        remembered_entity = apply_one(
            engine,
            SupersedeMemoryOperation(
                memory_id=kept.memory_id,
                kind=MemoryKind.FACT,
                content="The gate is closed at night.",
                target_id="entity-remembered",
                references=[CurrentObservationEvidence()],
            ),
        )

        assert ungrounded.status is ContinuityOperationStatus.REJECTED
        assert remembered_entity.status is ContinuityOperationStatus.REJECTED
        original = store.get(kept.memory_id)
        assert original is not None
        assert original.status.value == "active"


# --------------------------------------------------------------------------
# The elective read, end to end
# --------------------------------------------------------------------------


def test_a_requested_read_reaches_exactly_the_next_planner_and_touches_no_game(
    tmp_path: Path,
) -> None:
    """Reaching for a memory is deliberation, not an action.

    It must emit no primitive, answer only the call that asked, and then stop
    answering — a stale read lying around reads as a fresh one.
    """

    import asyncio
    import json

    from kenshi_agent.models import (
        NoopAction,
        PlannerDecision,
        RecallMemoryAction,
        StopAction,
    )
    from kenshi_agent.planners.base import Planner

    seen: list[Any] = []

    class ReadingPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> Any:
            self.calls += 1
            seen.append(current.memory_search)
            if self.calls == 1:
                return PlannerDecision(
                    intent="Look up what I know about the gate.",
                    rationale="Automatic recall did not surface it.",
                    action=RecallMemoryAction(query="gate", max_records=2),
                    confidence=1.0,
                )
            if self.calls == 2:
                return PlannerDecision(
                    intent="Use one deliberation turn.",
                    rationale="The next call must not inherit this read.",
                    action=NoopAction(reason="continue"),
                    confidence=1.0,
                )
            return PlannerDecision(
                intent="Stop.",
                rationale="The lookup answered the question.",
                action=StopAction(reason="done"),
                confidence=1.0,
            )

    async def scenario() -> None:
        with open_store(tmp_path / "memory.sqlite3", "reader") as store:
            keep_fact(store, "The gate at Squin closes at night.")
            planner = ReadingPlanner()
            runtime, logger = _single_step_runtime(tmp_path, planner, store)
            runtime.guard.config.allow_action_kinds.append("recall_memory")
            try:
                summary = await runtime.run(max_steps=3)
            finally:
                logger.close()

        assert summary.steps_completed == 3
        assert seen[0] is None, "nothing was requested before the first call"
        assert seen[1] is not None, "the read never reached the planner that asked"
        assert [record.content for record in seen[1].records] == [
            "The gate at Squin closes at night."
        ]
        assert seen[1].receipt_id.startswith("mrr-")
        assert seen[1].source == "durable_memory"
        assert seen[1].status == "completed"
        assert seen[1].campaign_id == "reader"
        assert seen[1].record_ids == [
            record.memory_id for record in seen[1].records
        ]
        assert seen[1].truncated is False
        assert seen[2] is None, "the elective read leaked into a later planner call"
        assert len(seen) == 3

        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text().splitlines()
        ]
        reads = [
            event["payload"] for event in events if event["event_type"] == "memory_read"
        ]
        assert len(reads) == 1
        assert reads[0]["controller_primitives"] == 0
        assert reads[0]["world_command_created"] is False
        receipt_id = reads[0]["result"]["receipt_id"]
        assert receipt_id == seen[1].receipt_id
        manifests = [
            event["payload"]
            for event in events
            if event["event_type"] == "planner_context_prepared"
        ]
        assert [manifest["memory_read_receipt_ids"] for manifest in manifests] == [
            [],
            [receipt_id],
            [],
        ]
        read_receipts = [
            event["payload"]
            for event in events
            if event["event_type"] == "action_receipt"
            and event["payload"]["action"]["kind"] == "recall_memory"
        ]
        assert len(read_receipts) == 1
        assert read_receipts[0]["primitive_actions"] == 0

    asyncio.run(scenario())


def test_a_read_with_memory_disabled_reports_unavailability_not_emptiness(
    tmp_path: Path,
) -> None:
    """Unknown stays unknown: an unavailable read is not "there is nothing"."""

    from kenshi_agent.models import RecallMemoryAction
    from kenshi_agent.runtime import AgentRuntime

    runner = object.__new__(AgentRuntime)
    runner.memory = None
    runner.run_id = "reader"
    runner.control_mode = ControlMode.INTERFACE_ONLY
    runner.logger = SimpleNamespace(write=lambda *a, **k: None)
    runner._pending_memory_search = None

    receipt = AgentRuntime._execute_memory_read(
        runner,
        RecallMemoryAction(query="gate"),
        observation(),
        plan_id="single-step",
        plan_version=1,
        step_id="step-0",
    )

    assert runner._pending_memory_search is not None
    assert runner._pending_memory_search.records == []
    assert runner._pending_memory_search.receipt_id.startswith("mrr-")
    assert runner._pending_memory_search.source == "durable_memory"
    assert runner._pending_memory_search.status is MemoryReadStatus.UNAVAILABLE
    assert runner._pending_memory_search.campaign_id is None
    assert "disabled" in runner._pending_memory_search.reason
    assert receipt.primitive_actions == 0


def test_a_working_outcome_read_returns_exact_runtime_owned_evidence() -> None:
    from kenshi_agent.models import RecallMemoryAction
    from kenshi_agent.runtime import AgentRuntime

    ledger = ledger_with_evidence()
    ledger.record_action_outcome(
        action_outcome("ao-2").model_copy(update={"plan_id": "plan-action"})
    )
    events: list[tuple[str, dict[str, Any]]] = []
    runner = object.__new__(AgentRuntime)
    runner.memory = None
    runner.run_id = "run-a"
    runner.control_mode = ControlMode.INTERFACE_ONLY
    runner._ledger = ledger
    runner.logger = SimpleNamespace(
        write=lambda event_type, **kwargs: events.append((event_type, kwargs))
    )
    runner._pending_memory_search = None

    receipt = AgentRuntime._execute_memory_read(
        runner,
        RecallMemoryAction(
            source="working_outcomes",
            query="plan",
            max_records=8,
        ),
        observation(),
        plan_id="plan-reader",
        plan_version=3,
        step_id="read-outcomes",
    )

    returned = runner._pending_memory_search
    assert returned is not None
    assert returned.source == "working_outcomes"
    assert returned.status is MemoryReadStatus.COMPLETED
    assert returned.campaign_id is None
    assert returned.action_outcome_ids == [
        outcome.outcome_id for outcome in returned.action_outcomes
    ] == ["ao-2"]
    assert returned.plan_outcome_ids == [
        outcome.plan_outcome_id for outcome in returned.plan_outcomes
    ] == ["po-1"]
    assert returned.plan_id == "plan-reader"
    assert returned.plan_version == 3
    assert returned.step_id == "read-outcomes"
    assert receipt.primitive_actions == 0
    read_events = [
        payload["payload"]["result"]
        for event_type, payload in events
        if event_type == "memory_read"
    ]
    assert read_events == [returned.model_dump(mode="json")]


def test_elective_memory_search_failure_is_typed_and_quarantined(
    tmp_path: Path,
) -> None:
    from kenshi_agent.models import RecallMemoryAction
    from kenshi_agent.runtime import AgentRuntime

    path = tmp_path / "memory.sqlite3"
    with _FailingSearchStore(
        path,
        CampaignScope(campaign_id="reader", origin=CampaignScopeOrigin.CONFIGURED),
    ) as store:
        ledger = ContinuityLedger(run_id="reader", action_outcome_limit=0)
        engine, _ = authority(store, ledger)
        events: list[tuple[str, dict[str, Any]]] = []
        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner.run_id = "reader"
        runner.control_mode = ControlMode.INTERFACE_ONLY
        runner._continuity = engine
        runner.logger = SimpleNamespace(
            write=lambda event_type, **kwargs: events.append((event_type, kwargs))
        )
        runner._pending_memory_search = None

        receipt = AgentRuntime._execute_memory_read(
            runner,
            RecallMemoryAction(query="gate"),
            observation(),
            plan_id="single-step",
            plan_version=1,
            step_id="step-0",
        )
        repeated = AgentRuntime._execute_memory_read(
            runner,
            RecallMemoryAction(query="gate"),
            observation(),
            plan_id="single-step",
            plan_version=1,
            step_id="step-1",
        )

        expected_reason = (
            "Durable continuity reads and writes are disabled for this run "
            "after an unexpected store failure "
            "(DatabaseError: injected search failure)."
        )
        assert store.search_attempts == 1
        assert runner._pending_memory_search is not None
        assert runner._pending_memory_search.records == []
        assert runner._pending_memory_search.status is MemoryReadStatus.FAILED
        assert runner._pending_memory_search.campaign_id == "reader"
        assert runner._pending_memory_search.reason == expected_reason
        assert receipt.message == expected_reason
        assert repeated.message == expected_reason
        assert receipt.primitive_actions == 0
        assert engine.reads_degraded_reason == expected_reason
        assert engine.writes_degraded_reason == expected_reason
        failures = [
            payload
            for event_type, payload in events
            if event_type == "continuity_store_failed"
        ]
        assert failures == [
            {
                "step_index": 0,
                "payload": {
                    "boundary": "elective_memory_search",
                    "reason": expected_reason,
                },
            }
        ]
        read_receipts = [
            payload["payload"]["result"]
            for event_type, payload in events
            if event_type == "memory_read"
        ]
        assert [item["status"] for item in read_receipts] == ["failed", "failed"]
        assert len({item["receipt_id"] for item in read_receipts}) == 2


def test_a_rejected_operation_is_shown_to_the_planner_that_would_repeat_it(
    tmp_path: Path,
) -> None:
    from kenshi_agent.config import PlanningConfig
    from kenshi_agent.memory import RecallBudget
    from kenshi_agent.planners.base import planner_context_manifest
    from kenshi_agent.runtime import AgentRuntime

    with open_store(tmp_path / "memory.sqlite3") as store:
        ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
        engine, _ = authority(store, ledger)
        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner.run_id = "run-a"
        runner.advisor = None
        runner.logger = SimpleNamespace(write=lambda *a, **k: None)
        runner._ledger = ledger
        runner._continuity = engine
        runner._pending_memory_search = None
        runner._continuity_receipts = []
        runner.planning_config = PlanningConfig()
        runner._recall_budget = RecallBudget(
            commitments=4,
            current_target=4,
            open_hypotheses=2,
            general=8,
        )
        current = observation()

        runner._apply_decision_continuity(
            SimpleNamespace(  # type: ignore[arg-type]
                continuity_operations=[keep(MemoryKind.FACT, "Unsupported claim.")]
            ),
            current,
            authored_context=planner_context(
                current,
                ledger=ledger,
                store=store,
                brief_ids=set(),
            ),
            plan_id="single-step",
            step_id="step-0",
        )
        decorated = runner._with_memories(observation())

    assert [receipt.status for receipt in decorated.recent_continuity_receipts] == [
        ContinuityOperationStatus.REJECTED
    ]
    rejected = decorated.recent_continuity_receipts[0]
    assert rejected.receipt_id.startswith("cor-")
    assert rejected.operation == "keep"
    assert "must cite" in rejected.reason
    manifest = planner_context_manifest(
        decorated,
        context_id="pc-2",
        input_kind="full_observation",
    )
    assert manifest.continuity_receipt_ids == [rejected.receipt_id]


def test_omitted_general_memories_are_declared_in_the_observation(
    tmp_path: Path,
) -> None:
    from kenshi_agent.config import PlanningConfig
    from kenshi_agent.memory import RecallBudget
    from kenshi_agent.models import RecallTier
    from kenshi_agent.runtime import AgentRuntime

    with open_store(tmp_path / "memory.sqlite3") as store:
        for index in range(5):
            keep_fact(store, f"General fact {index}.")

        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner.advisor = None
        runner._ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=0)
        runner._continuity, _ = authority(store, runner._ledger)
        runner._continuity_receipts = []
        runner._pending_memory_search = None
        runner.planning_config = PlanningConfig()
        runner._recall_budget = RecallBudget(
            commitments=1,
            current_target=1,
            open_hypotheses=1,
            general=2,
        )

        decorated = runner._with_memories(observation())

    assert len(decorated.memories) == 2
    assert decorated.memory_recall.total_omitted == 3
    assert decorated.memory_recall.omitted[RecallTier.GENERAL] == 3
    assert decorated.memory_recall.complete is False


def test_evidence_from_a_superseded_game_session_is_inadmissible() -> None:
    """A load discards the world its outcomes describe.

    Until quickload was wired, only the operator could load, between runs, and
    a new run resets the ledger. The agent can now rotate the session mid-run,
    so a purchase made before the load stays in the ledger as
    `controller_verified` `changed` evidence for a world that no longer exists.
    `run_id` does not change across a load, so it cannot be the currency check.
    """

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    before_load = action_outcome("ao-1").model_copy(
        update={"identity_session_id": "session-AAAA-0000000000000002"}
    )
    ledger.record_action_outcome(before_load)

    after_load = observation().model_copy(
        update={
            "telemetry": TelemetrySnapshot(
                sequence=9,
                capabilities=["identity.stable_handles"],
                identity_session_id="session-BBBB-0000000000000003",
            )
        }
    )
    context = planner_context(
        after_load,
        ledger=ledger,
        store=None,
        brief_ids=set(),
    )

    with pytest.raises(EvidenceResolutionError, match="superseded game session"):
        resolve_evidence_reference(
            ActionOutcomeEvidence(outcome_id="ao-1"),
            authored_context=context,
            ledger=ledger,
            store=None,
            advisor_brief_ids=set(),
        )


def test_evidence_from_the_same_game_session_stays_admissible() -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(
        action_outcome("ao-1").model_copy(
            update={"identity_session_id": "session-AAAA-0000000000000002"}
        )
    )
    same_session = observation().model_copy(
        update={
            "telemetry": TelemetrySnapshot(
                sequence=9,
                capabilities=["identity.stable_handles"],
                identity_session_id="session-AAAA-0000000000000002",
            )
        }
    )
    context = planner_context(
        same_session, ledger=ledger, store=None, brief_ids=set()
    )

    snapshot = resolve_evidence_reference(
        ActionOutcomeEvidence(outcome_id="ao-1"),
        authored_context=context,
        ledger=ledger,
        store=None,
        advisor_brief_ids=set(),
    )

    assert snapshot.source == "action_outcome"


def test_a_run_without_session_identity_keeps_its_evidence() -> None:
    """Refusing needs both sessions known, or mock runs lose their own evidence.

    Mock and interface-only runs carry no `identity_session_id`, so comparing a
    known session against an absent one would reject evidence that never
    crossed a load at all.
    """

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    ledger.record_action_outcome(
        action_outcome("ao-1").model_copy(
            update={"identity_session_id": "session-AAAA-0000000000000002"}
        )
    )
    no_telemetry = observation().model_copy(update={"telemetry": None})
    context = planner_context(
        no_telemetry, ledger=ledger, store=None, brief_ids=set()
    )

    snapshot = resolve_evidence_reference(
        ActionOutcomeEvidence(outcome_id="ao-1"),
        authored_context=context,
        ledger=ledger,
        store=None,
        advisor_brief_ids=set(),
    )

    assert snapshot.source == "action_outcome"
