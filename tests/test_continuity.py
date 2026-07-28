"""Authority and timing for planner-authored continuity.

Three layers must not blur: world evidence, working continuity, and durable
kept memory. These tests hold that seam — continuity carries runtime-owned
identity, a plan cannot remember work it has not done yet, and merely reading
memory changes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kenshi_agent.continuity import (
    ContinuityAuthority,
    ContinuityLedger,
    EvidenceResolutionError,
    render_evidence_reference,
)
from kenshi_agent.memory import MemoryStore
from kenshi_agent.models import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionOutcomeEvidence,
    AdvisorBriefEvidence,
    ContinuityOperationStatus,
    ContinuityOrigin,
    CurrentObservationEvidence,
    KeepMemoryOperation,
    MemoryEvidence,
    MemoryKind,
    MemoryWrite,
    NearbyEntity,
    Observation,
    PlanDisposition,
    PlanOutcomeEvidence,
    StopAction,
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


def write_of(content: str, *, target_id: str | None = None) -> MemoryWrite:
    return MemoryWrite(
        kind=MemoryKind.FACT,
        content=content,
        salience=0.5,
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


def test_a_zero_length_ledger_shows_nothing_and_still_proves_everything() -> None:
    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=0)
    ledger.record_action_outcome(action_outcome("ao-1"))

    assert ledger.recent_action_outcomes == []
    assert ledger.has_action_outcome("ao-1")


def test_plan_outcomes_carry_the_original_objective_and_terminal_reason() -> None:
    """"Execute step X" is not a purpose. The next plan needs the real one."""

    ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
    started = datetime.now(UTC)

    outcome = ledger.record_plan_outcome(
        plan_id="plan-a",
        plan_version=2,
        objective="Deliver six sealed slop canisters.",
        disposition=PlanDisposition.FAILED,
        reason="The gate was closed.",
        completed_step_ids=["walk", "open"],
        actions_completed=2,
        terminal_revision=WorldStateRevision(telemetry_sequence=9),
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

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        memory_id = store.add("run-a", write_of("A remembered fact."))
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


def test_evidence_that_scrolled_out_of_the_window_renders_as_evicted() -> None:
    """Still citable, but honest that the record itself is no longer held."""

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
        )

    assert render(ActionOutcomeEvidence(outcome_id="ao-1")) == (
        "action_outcome(ao-1: evicted)"
    )
    assert render(ActionOutcomeEvidence(outcome_id="ao-2")) == (
        "action_outcome(ao-2: no_op)"
    )
    assert render(PlanOutcomeEvidence(plan_outcome_id="po-1")) == (
        "plan_outcome(po-1: evicted)"
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
        MemoryEvidence(memory_id=404),
        AdvisorBriefEvidence(brief_id=OTHER_BRIEF_ID),
    ],
)
def test_an_invented_identity_never_resolves(
    reference: Any,
    tmp_path: Path,
) -> None:
    """Each branch fails independently: one real ID cannot carry four."""

    ledger = ledger_with_evidence()

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        store.add("run-a", write_of("A remembered fact."))
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
    with MemoryStore(path, "other-campaign") as other:
        foreign_id = other.add("run-z", write_of("Another campaign's fact."))

    with MemoryStore(path, "test") as store:
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
            MemoryEvidence(memory_id=1),
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

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        receipts = engine.apply(
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


@pytest.mark.parametrize("kind", [MemoryKind.COMMITMENT, MemoryKind.HYPOTHESIS])
def test_an_intention_or_an_uncertainty_may_be_self_authored(
    kind: MemoryKind,
    tmp_path: Path,
) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        receipts = engine.apply(
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

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        receipts = engine.apply(
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
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, _ = authority(store, ledger)

        engine.apply(
            [
                keep(
                    MemoryKind.FACT,
                    "The barman offers no work.",
                    references=[ActionOutcomeEvidence(outcome_id="ao-1")],
                )
            ],
            origin=ContinuityOrigin.PLAN,
            observation=observation(),
            plan_id="plan-a",
            plan_version=1,
        )

        record = store.recall(limit=8)[0]

    assert record.evidence is not None
    assert "ao-1" in record.evidence
    assert "no_op" in record.evidence


def test_a_target_id_absent_from_the_fresh_observation_is_rejected(
    tmp_path: Path,
) -> None:
    """A remembered or invented entity ID cannot bind a new memory."""

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))
        current = observation(target_ids=("entity-present",))

        rejected = engine.apply(
            [keep(MemoryKind.COMMITMENT, "Trade here.", target_id="entity-remembered")],
            origin=ContinuityOrigin.PLAN,
            observation=current,
            plan_id="plan-a",
            plan_version=1,
        )
        accepted = engine.apply(
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
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        receipts = engine.apply(
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

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, events = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )

        receipts = engine.apply(
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
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        for origin in ContinuityOrigin:
            receipts = engine.apply(
                [keep(MemoryKind.COMMITMENT, f"Intent from {origin.value}.")],
                origin=origin,
                observation=observation(),
                plan_id="plan-a",
                plan_version=1,
            )
            assert receipts[0].origin is origin
            assert receipts[0].status is ContinuityOperationStatus.ACCEPTED
            assert receipts[0].memory_id is not None


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

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, events = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )

        receipt = engine.apply(
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

    receipt = engine.apply(
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


def test_several_references_are_joined_into_one_readable_grounding(
    tmp_path: Path,
) -> None:
    ledger = ledger_with_evidence()
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        memory_id = store.add("run-a", write_of("An earlier fact."))
        engine, _ = authority(store, ledger, brief_ids={BRIEF_ID})

        receipt = engine.apply(
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
    assert stored.evidence == receipt.evidence
    # The store's own bound is the bound; four references cannot approach it.
    assert len(receipt.evidence) < 1000


def test_declared_salience_reaches_the_store_unchanged(tmp_path: Path) -> None:
    """Salience is the only ranking signal the agent controls."""

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, _ = authority(store, ContinuityLedger(run_id="run-a", action_outcome_limit=4))

        engine.apply(
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

    receipts = engine.apply(
        [keep(MemoryKind.COMMITMENT, "Leave the bar.")],
        origin=ContinuityOrigin.PLAN,
        observation=observation(),
        plan_id="plan-a",
        plan_version=1,
    )

    assert receipts[0].status is ContinuityOperationStatus.NO_OP
    assert receipts[0].memory_id is None


def test_no_operations_produce_no_receipts(tmp_path: Path) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        engine, events = authority(
            store,
            ContinuityLedger(run_id="run-a", action_outcome_limit=4),
        )

        assert (
            engine.apply(
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

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        store.add("run-a", write_of("A general fact."))
        store.add("run-a", write_of("A bound fact.", target_id="entity-a"))
        before = store._connection.total_changes

        for _ in range(20):
            store.recall(limit=8, target_ids={"entity-a"}, entity_limit=4)

        assert store._connection.total_changes == before


def test_reading_a_memory_never_raises_its_priority(tmp_path: Path) -> None:
    """Only an explicit accepted operation may reinforce."""

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        older = store.add("run-a", write_of("Older tied fact.", target_id="entity-older"))
        newer = store.add("run-a", write_of("Newer tied fact.", target_id="entity-newer"))
        assert older < newer

        for _ in range(5):
            store.recall(limit=0, target_ids={"entity-older"}, entity_limit=1)
        store.record_delivery([older])

        records = store.recall(
            limit=0,
            target_ids={"entity-older", "entity-newer"},
            entity_limit=2,
        )

    assert [record.target_id for record in records] == ["entity-newer", "entity-older"]


def test_delivery_is_recorded_only_when_a_planner_payload_is_assembled(
    tmp_path: Path,
) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        memory_id = store.add("run-a", write_of("A fact."))

        assert store.recall(limit=4)[0].last_delivered_at is None

        store.record_delivery([memory_id])
        delivered = store.recall(limit=4)[0].last_delivered_at

    assert delivered is not None
    assert delivered.utcoffset() is not None


def test_delivery_time_never_reorders_general_recall(tmp_path: Path) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        first = store.add("run-a", write_of("First fact."))
        store.add("run-a", write_of("Second fact."))

        store.record_delivery([first])

        assert [record.content for record in store.recall(limit=4)] == [
            "Second fact.",
            "First fact.",
        ]


def test_recording_delivery_of_nothing_touches_nothing(tmp_path: Path) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        store.add("run-a", write_of("A fact."))
        before = store._connection.total_changes

        store.record_delivery([])

        assert store._connection.total_changes == before


def test_delivery_cannot_reach_another_campaigns_record(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with MemoryStore(path, "other-campaign") as other:
        foreign_id = other.add("run-z", write_of("Another campaign's fact."))

    with MemoryStore(path, "test") as store:
        store.record_delivery([foreign_id])

    with MemoryStore(path, "other-campaign") as other:
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
        with MemoryStore(tmp_path / "memory.sqlite3", "single") as store:
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


def test_decorating_observations_at_pump_rate_writes_nothing(tmp_path: Path) -> None:
    """`_with_memories` runs about ten times a second in a live run."""

    from kenshi_agent.config import PlanningConfig
    from kenshi_agent.runtime import AgentRuntime

    with MemoryStore(tmp_path / "memory.sqlite3", "pump") as store:
        store.add("run-a", write_of("A general fact."))
        store.add("run-a", write_of("A bound fact.", target_id="entity-a"))

        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner.memory_limit = 8
        runner.entity_memory_limit = 4
        runner.minimum_memory_salience = 0.0
        runner.advisor = None
        runner._affordance_requests = []
        runner._ledger = ContinuityLedger(run_id="run-a", action_outcome_limit=4)
        runner.planning_config = PlanningConfig()

        current = observation(target_ids=("entity-a",))
        before = store._connection.total_changes
        for _ in range(30):
            decorated = runner._with_memories(current)

        assert store._connection.total_changes == before
        assert len(decorated.memories) == 2


def test_delivery_marks_every_record_it_was_given(tmp_path: Path) -> None:
    """One placeholder per ID: a single-ID test would never notice the join."""

    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        ids = [store.add("run-a", write_of(f"Fact {index}.")) for index in range(4)]

        store.record_delivery(ids[:3])
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


def test_a_bound_memory_keeps_its_entity_through_the_delivery_migration(
    tmp_path: Path,
) -> None:
    """The shape that had `target_id` but tracked read time, not delivery.

    Rebuilding must carry every exact identity across. Dropping them would
    quietly turn one entity's memory into general knowledge about everyone.
    """

    import sqlite3

    path = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT NOT NULL,
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            salience REAL NOT NULL,
            evidence TEXT,
            created_at TEXT NOT NULL,
            last_accessed_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            target_id TEXT NOT NULL DEFAULT '',
            UNIQUE(namespace, kind, content, target_id)
        );
        INSERT INTO memories (
            namespace, run_id, kind, content, salience, evidence,
            created_at, last_accessed_at, active, target_id
        ) VALUES
        ('test', 'legacy', 'fact', 'This barman offers no work.', 0.5, 'seen',
         '2026-07-26T00:00:00+00:00', '2026-07-27T00:00:00+00:00', 1,
         'entity-barman'),
        ('test', 'legacy', 'fact', 'The Hub has a bar.', 0.5, NULL,
         '2026-07-26T00:00:00+00:00', '2026-07-27T00:00:00+00:00', 1, '');
        """
    )
    connection.commit()
    connection.close()

    with MemoryStore(path, "test") as store:
        general = store.recall(limit=8)
        bound = store.recall(limit=0, target_ids={"entity-barman"}, entity_limit=4)
    # A second open must not migrate again or duplicate anything.
    with MemoryStore(path, "test") as reopened:
        again = reopened.recall(limit=8)

    assert [record.content for record in general] == ["The Hub has a bar."]
    assert [record.target_id for record in bound] == ["entity-barman"]
    assert bound[0].content == "This barman offers no work."
    assert bound[0].evidence == "seen"
    # The old column recorded automatic recall, not delivery to a planner.
    assert bound[0].last_delivered_at is None
    assert general[0].last_delivered_at is None
    assert [record.content for record in again] == ["The Hub has a bar."]
