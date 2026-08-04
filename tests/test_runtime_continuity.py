from __future__ import annotations

import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from kenshi_agent.core.continuity import (
    ContinuityOperationReceipt,
    ContinuityOperationStatus,
    ContinuityOrigin,
    KeepMemoryOperation,
    MemoryKind,
    MemoryReadStatus,
    MemoryRecord,
    MemorySearchResult,
    MemoryStatus,
    RecallTier,
)
from kenshi_agent.core.evidence import (
    ActionOutcomeAssessment,
    ActionOutcomeDigest,
    PlanDisposition,
    PlanOutcomeDigest,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.memory import RecallBudget, TieredRecall
from kenshi_agent.runtime_continuity import (
    StoreBoundaryFailure,
    build_memory_read_receipt,
    continuity_receipt_digests,
    recall_for_observation,
    record_planner_delivery,
    search_durable_memory,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


@dataclass
class _Health:
    reads_degraded_reason: str | None = None
    writes_degraded_reason: str | None = None
    read_failures: int = 0
    write_failures: int = 0

    def quarantine_reads_after_store_failure(self, exc: sqlite3.Error) -> str:
        self.read_failures += 1
        if self.reads_degraded_reason is None:
            self.reads_degraded_reason = f"read:{type(exc).__name__}:{exc}"
        if self.writes_degraded_reason is None:
            self.writes_degraded_reason = self.reads_degraded_reason
        return self.reads_degraded_reason

    def quarantine_writes_after_store_failure(self, exc: sqlite3.Error) -> str:
        self.write_failures += 1
        if self.writes_degraded_reason is None:
            self.writes_degraded_reason = f"write:{type(exc).__name__}:{exc}"
        return self.writes_degraded_reason


@dataclass
class _Store:
    recall_result: TieredRecall
    search_result: MemorySearchResult
    recall_error: Exception | None = None
    search_error: Exception | None = None
    delivery_error: Exception | None = None
    recall_calls: list[tuple[RecallBudget, tuple[str, ...]]] = field(default_factory=list)
    search_calls: list[tuple[str, int]] = field(default_factory=list)
    delivery_calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def recall_tiered(
        self,
        *,
        budget: RecallBudget,
        target_ids: Collection[str] = (),
    ) -> TieredRecall:
        self.recall_calls.append((budget, tuple(target_ids)))
        if self.recall_error is not None:
            raise self.recall_error
        return self.recall_result

    def search(self, *, query: str, limit: int) -> MemorySearchResult:
        self.search_calls.append((query, limit))
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    def record_delivery(self, run_id: str, memory_ids: Sequence[str]) -> None:
        self.delivery_calls.append((run_id, tuple(memory_ids)))
        if self.delivery_error is not None:
            raise self.delivery_error


def _memory(memory_id: str = "mem-a") -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        campaign_id="campaign-a",
        kind=MemoryKind.FACT,
        status=MemoryStatus.ACTIVE,
        content=f"content:{memory_id}",
        salience=0.7,
        created_run_id="run-a",
        created_at=NOW,
    )


def _store(**updates: object) -> _Store:
    memory = _memory()
    values: dict[str, object] = {
        "recall_result": TieredRecall(
            records=[memory],
            tiers={memory.memory_id: RecallTier.GENERAL},
            omitted={
                RecallTier.COMMITMENT: 0,
                RecallTier.CURRENT_TARGET: 2,
                RecallTier.OPEN_HYPOTHESIS: 0,
                RecallTier.GENERAL: 3,
            },
        ),
        "search_result": MemorySearchResult(
            query="gate",
            records=[memory],
            matched=2,
            truncated=True,
            reason="two matched",
        ),
    }
    values.update(updates)
    return _Store(**values)  # type: ignore[arg-type]


def _budget() -> RecallBudget:
    return RecallBudget(
        commitments=4,
        current_target=3,
        open_hypotheses=2,
        general=1,
        minimum_salience=0.25,
    )


def test_successful_recall_conserves_records_and_reports_only_real_omissions() -> None:
    store = _store()
    health = _Health()
    budget = _budget()

    result = recall_for_observation(
        store,
        health,
        budget=budget,
        target_ids={"entity-b", "entity-a"},
    )

    assert result.records == store.recall_result.records
    assert result.summary.omitted == {
        RecallTier.CURRENT_TARGET: 2,
        RecallTier.GENERAL: 3,
    }
    assert result.summary.total_omitted == 5
    assert result.failure is None
    assert result.reads_degraded_reason is None
    assert result.writes_degraded_reason is None
    assert store.recall_calls == [(budget, ("entity-a", "entity-b"))]


def test_recall_failure_quarantines_reads_and_writes_once_without_retrying() -> None:
    store = _store(recall_error=sqlite3.DatabaseError("broken recall"))
    health = _Health()

    first = recall_for_observation(
        store,
        health,
        budget=_budget(),
        target_ids={"entity-a"},
    )
    repeated = recall_for_observation(
        store,
        health,
        budget=_budget(),
        target_ids={"entity-a"},
    )

    assert first.records == []
    assert first.summary.total_omitted == 0
    assert first.failure == StoreBoundaryFailure(
        boundary="automatic_recall",
        reason="read:DatabaseError:broken recall",
    )
    assert first.reads_degraded_reason == "read:DatabaseError:broken recall"
    assert first.writes_degraded_reason == "read:DatabaseError:broken recall"
    assert repeated.failure is None
    assert repeated.reads_degraded_reason == first.reads_degraded_reason
    assert repeated.writes_degraded_reason == first.writes_degraded_reason
    assert len(store.recall_calls) == 1
    assert health.read_failures == 1
    assert health.write_failures == 0


def test_recall_does_not_hide_non_sqlite_programming_errors() -> None:
    store = _store(recall_error=RuntimeError("bad adapter"))

    with pytest.raises(RuntimeError, match="bad adapter"):
        recall_for_observation(
            store,
            _Health(),
            budget=_budget(),
            target_ids=set(),
        )


def test_delivery_is_exact_and_stops_after_the_first_sqlite_failure() -> None:
    store = _store(delivery_error=sqlite3.OperationalError("disk full"))
    health = _Health()

    first = record_planner_delivery(
        store,
        health,
        run_id="run-a",
        memory_ids=["mem-a", "mem-b"],
    )
    repeated = record_planner_delivery(
        store,
        health,
        run_id="run-a",
        memory_ids=["mem-c"],
    )

    assert first == StoreBoundaryFailure(
        boundary="record_delivery",
        reason="write:OperationalError:disk full",
    )
    assert repeated is None
    assert store.delivery_calls == [("run-a", ("mem-a", "mem-b"))]
    assert health.reads_degraded_reason is None
    assert health.writes_degraded_reason == "write:OperationalError:disk full"
    assert health.write_failures == 1


def test_empty_delivery_is_a_no_op_and_non_sqlite_failure_escapes() -> None:
    store = _store(delivery_error=RuntimeError("bad adapter"))
    health = _Health()

    assert (
        record_planner_delivery(
            store,
            health,
            run_id="run-a",
            memory_ids=[],
        )
        is None
    )
    assert store.delivery_calls == []

    with pytest.raises(RuntimeError, match="bad adapter"):
        record_planner_delivery(
            store,
            health,
            run_id="run-a",
            memory_ids=["mem-a"],
        )


def test_search_returns_exact_store_answer_and_never_retries_a_failed_read() -> None:
    store = _store(search_error=sqlite3.DatabaseError("broken search"))
    health = _Health()

    first = search_durable_memory(
        store,
        health,
        query="gate",
        limit=7,
    )
    repeated = search_durable_memory(
        store,
        health,
        query="other",
        limit=4,
    )

    assert first.result == MemorySearchResult(
        query="gate",
        reason="read:DatabaseError:broken search",
    )
    assert first.failure == StoreBoundaryFailure(
        boundary="elective_memory_search",
        reason="read:DatabaseError:broken search",
    )
    assert repeated.result == MemorySearchResult(
        query="other",
        reason="read:DatabaseError:broken search",
    )
    assert repeated.failure is None
    assert store.search_calls == [("gate", 7)]
    assert health.read_failures == 1


def test_successful_search_conserves_result_and_non_sqlite_failure_escapes() -> None:
    store = _store()

    result = search_durable_memory(
        store,
        _Health(),
        query="gate",
        limit=7,
    )

    assert result.result is store.search_result
    assert result.failure is None
    assert store.search_calls == [("gate", 7)]

    store.search_error = RuntimeError("bad adapter")
    with pytest.raises(RuntimeError, match="bad adapter"):
        search_durable_memory(
            store,
            _Health(),
            query="gate",
            limit=7,
        )


def test_memory_read_receipts_conserve_exact_results_provenance_and_identity() -> None:
    memory = _memory()
    action = ActionOutcomeDigest(
        outcome_id="ao-1",
        run_id="run-a",
        plan_id="plan-a",
        plan_version=2,
        step_id="walk",
        action_kind="stop",
        assessment=ActionOutcomeAssessment.NO_OP,
        executed=True,
        controller_verified=False,
        evidence_summary="Nothing changed.",
        recorded_at=NOW,
    )
    plan = PlanOutcomeDigest(
        plan_outcome_id="po-1",
        run_id="run-a",
        plan_id="plan-a",
        plan_version=2,
        objective="Reach the gate.",
        disposition=PlanDisposition.FAILED,
        reason_digest="The gate was closed.",
        actions_completed=1,
        started_at=NOW,
        finished_at=NOW,
    )

    durable = build_memory_read_receipt(
        MemorySearchResult(query="gate", records=[memory]),
        source="durable_memory",
        status=MemoryReadStatus.COMPLETED,
        campaign_id="campaign-a",
        plan_id="plan-a",
        plan_version=2,
        step_id="read-memory",
    )
    working = build_memory_read_receipt(
        MemorySearchResult(
            query="gate",
            action_outcomes=[action],
            plan_outcomes=[plan],
        ),
        source="working_outcomes",
        status=MemoryReadStatus.COMPLETED,
        campaign_id=None,
        plan_id="plan-a",
        plan_version=2,
        step_id="read-outcomes",
    )

    assert durable.record_ids == ["mem-a"]
    assert durable.action_outcome_ids == []
    assert durable.plan_outcome_ids == []
    assert durable.campaign_id == "campaign-a"
    assert working.record_ids == []
    assert working.action_outcome_ids == ["ao-1"]
    assert working.plan_outcome_ids == ["po-1"]
    assert working.campaign_id is None
    assert durable.receipt_id.startswith("mrr-")
    assert working.receipt_id.startswith("mrr-")
    assert durable.receipt_id != working.receipt_id
    assert durable.plan_id == working.plan_id == "plan-a"
    assert durable.plan_version == working.plan_version == 2
    assert durable.step_id == "read-memory"
    assert working.step_id == "read-outcomes"


def test_receipt_digest_projection_conserves_order_and_runtime_identity() -> None:
    revision = WorldStateRevision(
        telemetry_sequence=1,
        frame_sequence=2,
        capability_epoch=3,
        observed_at_monotonic=4.0,
    )
    receipts = [
        ContinuityOperationReceipt(
            receipt_id=f"cor-{digit * 32}",
            origin=ContinuityOrigin.DECISION,
            status=status,
            operation=KeepMemoryOperation(
                kind=MemoryKind.FACT,
                content=f"fact {digit}",
            ),
            reason=f"reason {digit}",
            authored_context_id="pc-1",
            authored_revision=revision,
            commit_revision=revision,
            recorded_at=NOW,
        )
        for digit, status in (
            ("1", ContinuityOperationStatus.REJECTED),
            ("2", ContinuityOperationStatus.FAILED),
        )
    ]

    digests = continuity_receipt_digests(receipts)

    assert [digest.receipt_id for digest in digests] == [
        "cor-" + "1" * 32,
        "cor-" + "2" * 32,
    ]
    assert [digest.status for digest in digests] == [
        ContinuityOperationStatus.REJECTED,
        ContinuityOperationStatus.FAILED,
    ]
