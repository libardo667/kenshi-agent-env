"""Failure-isolated continuity operations used by the runtime coordinator.

The game loop owns sequencing and logging. This module owns the smaller
contract at the SQLite boundary: one unexpected store failure quarantines the
affected capability, returns typed planner-visible state, and never turns a
valid gameplay decision into an exception.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from .memory import RecallBudget, TieredRecall
from .models import (
    ContinuityOperationReceipt,
    ContinuityReceiptDigest,
    MemoryRecord,
    MemorySearchResult,
    RecallSummary,
)


class ContinuityStore(Protocol):
    def recall_tiered(
        self,
        *,
        budget: RecallBudget,
        target_ids: Collection[str] = (),
    ) -> TieredRecall: ...

    def search(self, *, query: str, limit: int) -> MemorySearchResult: ...

    def record_delivery(self, run_id: str, memory_ids: Sequence[str]) -> None: ...


class ContinuityStoreHealth(Protocol):
    @property
    def reads_degraded_reason(self) -> str | None: ...

    @property
    def writes_degraded_reason(self) -> str | None: ...

    def quarantine_reads_after_store_failure(self, exc: sqlite3.Error) -> str: ...

    def quarantine_writes_after_store_failure(self, exc: sqlite3.Error) -> str: ...


@dataclass(frozen=True, slots=True)
class StoreBoundaryFailure:
    boundary: Literal[
        "automatic_recall",
        "elective_memory_search",
        "record_delivery",
    ]
    reason: str


@dataclass(frozen=True, slots=True)
class ObservationRecall:
    records: list[MemoryRecord]
    summary: RecallSummary
    reads_degraded_reason: str | None
    writes_degraded_reason: str | None
    failure: StoreBoundaryFailure | None = None


@dataclass(frozen=True, slots=True)
class DurableMemorySearch:
    result: MemorySearchResult
    failure: StoreBoundaryFailure | None = None


def _empty_recall(
    health: ContinuityStoreHealth,
    *,
    failure: StoreBoundaryFailure | None = None,
) -> ObservationRecall:
    return ObservationRecall(
        records=[],
        summary=RecallSummary(),
        reads_degraded_reason=health.reads_degraded_reason,
        writes_degraded_reason=health.writes_degraded_reason,
        failure=failure,
    )


def recall_for_observation(
    store: ContinuityStore,
    health: ContinuityStoreHealth,
    *,
    budget: RecallBudget,
    target_ids: Collection[str],
) -> ObservationRecall:
    """Recall once, or quarantine future reads and writes after SQLite failure."""

    if health.reads_degraded_reason is not None:
        return _empty_recall(health)
    try:
        recalled = store.recall_tiered(
            budget=budget,
            target_ids=sorted(target_ids),
        )
    except sqlite3.Error as exc:
        reason = health.quarantine_reads_after_store_failure(exc)
        return _empty_recall(
            health,
            failure=StoreBoundaryFailure(
                boundary="automatic_recall",
                reason=reason,
            ),
        )
    return ObservationRecall(
        records=recalled.records,
        summary=RecallSummary(
            omitted={
                tier: count
                for tier, count in recalled.omitted.items()
                if count
            },
            total_omitted=recalled.total_omitted,
        ),
        # The guarded read succeeded, so this is necessarily healthy.
        reads_degraded_reason=None,
        writes_degraded_reason=health.writes_degraded_reason,
    )


def record_planner_delivery(
    store: ContinuityStore,
    health: ContinuityStoreHealth,
    *,
    run_id: str,
    memory_ids: Sequence[str],
) -> StoreBoundaryFailure | None:
    """Record one exact delivered ID set unless writes are already quarantined."""

    if not memory_ids or health.writes_degraded_reason is not None:
        return None
    try:
        store.record_delivery(run_id, memory_ids)
    except sqlite3.Error as exc:
        reason = health.quarantine_writes_after_store_failure(exc)
        return StoreBoundaryFailure(
            boundary="record_delivery",
            reason=reason,
        )
    return None


def search_durable_memory(
    store: ContinuityStore,
    health: ContinuityStoreHealth,
    *,
    query: str,
    limit: int,
) -> DurableMemorySearch:
    """Search once, preserving unavailability as distinct from no matches."""

    if health.reads_degraded_reason is not None:
        return DurableMemorySearch(
            result=MemorySearchResult(
                query=query,
                reason=health.reads_degraded_reason,
            )
        )
    try:
        result = store.search(query=query, limit=limit)
    except sqlite3.Error as exc:
        reason = health.quarantine_reads_after_store_failure(exc)
        return DurableMemorySearch(
            result=MemorySearchResult(query=query, reason=reason),
            failure=StoreBoundaryFailure(
                boundary="elective_memory_search",
                reason=reason,
            ),
        )
    return DurableMemorySearch(result=result)


def continuity_receipt_digests(
    receipts: Sequence[ContinuityOperationReceipt],
) -> list[ContinuityReceiptDigest]:
    """Project full receipts to their bounded planner-visible form."""

    return [receipt.digest() for receipt in receipts]
