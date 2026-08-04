"""One authority for durable memory and fieldbook operations during a run.

Memory and fieldbook stay distinct semantic systems with distinct stores; this
owns what a run does *with* them - validating the operations a plan authored,
committing them at the moment the thing they describe actually happened,
answering deliberate reads, and keeping the bounded receipt windows the next
planner context is allowed to see.

Reads emit nothing into the game. Unavailability is reported as unavailability:
a read that could not happen must never render as "there is nothing there".
"""

from __future__ import annotations

import sqlite3
from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from .continuity import ContinuityAuthority, ContinuityLedger
from .fieldbook import FieldbookTransitionError
from .fieldbook_authority import FieldbookAuthority
from .memory import MemoryStore
from .models import (
    ActionReceipt,
    AuthoredPlannerContext,
    ContinuityOperation,
    ContinuityOrigin,
    ContinuityReceiptDigest,
    ControlMode,
    FieldbookOperation,
    FieldbookReadReceipt,
    FieldbookReadResult,
    FieldbookReadStatus,
    FieldbookReceiptDigest,
    MemoryReadReceipt,
    MemoryReadStatus,
    MemorySearchResult,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    ReadFieldbookAction,
    RecallMemoryAction,
)
from .runtime_continuity import (
    build_fieldbook_read_receipt,
    build_memory_read_receipt,
    continuity_receipt_digests,
    record_planner_delivery,
    search_durable_memory,
)
from .session_log import SessionLogger

# Bounded and short: these exist so a deterministic invalid update is not
# repeated, not as a second history.
MAX_SURFACED_CONTINUITY_RECEIPTS = 4
MAX_SURFACED_FIELDBOOK_RECEIPTS = 4


class ContinuityService:
    """Validate, commit, and read the run's durable memory and fieldbook."""

    def __init__(
        self,
        *,
        run_id: str,
        store: MemoryStore | None,
        ledger: ContinuityLedger,
        logger: SessionLogger,
        control_mode: ControlMode,
        advisor_brief_ids: Callable[[], set[str]],
        authority: ContinuityAuthority | None = None,
    ) -> None:
        self._run_id = run_id
        self._store = store
        self._ledger = ledger
        self._logger = logger
        self._control_mode = control_mode
        self._authority = authority or ContinuityAuthority(
            run_id=run_id,
            store=store,
            ledger=ledger,
            logger=logger,
            advisor_brief_ids=advisor_brief_ids,
        )
        self._fieldbook = FieldbookAuthority(continuity=self._authority)
        self._receipts: deque[ContinuityReceiptDigest] = deque(
            maxlen=MAX_SURFACED_CONTINUITY_RECEIPTS
        )
        self._fieldbook_receipts: deque[FieldbookReceiptDigest] = deque(
            maxlen=MAX_SURFACED_FIELDBOOK_RECEIPTS
        )
        self._pending_memory_search: MemoryReadReceipt | None = None
        self._pending_fieldbook_read: FieldbookReadReceipt | None = None

    # --- state the planner context is allowed to see -----------------------

    @property
    def authority(self) -> ContinuityAuthority:
        """The commit authority, for collaborators that resolve evidence."""

        return self._authority

    @property
    def recent_receipts(self) -> list[ContinuityReceiptDigest]:
        return list(self._receipts)

    @property
    def recent_fieldbook_receipts(self) -> list[FieldbookReceiptDigest]:
        return list(self._fieldbook_receipts)

    @property
    def pending_memory_search(self) -> MemoryReadReceipt | None:
        return self._pending_memory_search

    @property
    def pending_fieldbook_read(self) -> FieldbookReadReceipt | None:
        return self._pending_fieldbook_read

    def reset(self) -> None:
        """Begin one run with no carried receipts from any previous one."""

        self._receipts.clear()
        self._fieldbook_receipts.clear()
        self._pending_memory_search = None
        self._pending_fieldbook_read = None

    def clear_pending_reads(self) -> None:
        """Drop read results once the context that would show them is built."""

        self._pending_memory_search = None
        self._pending_fieldbook_read = None

    def _surface(self, receipts: Sequence[object]) -> None:
        """Keep the newest receipts where the next planner will see them."""

        self._receipts.extend(continuity_receipt_digests(receipts))  # type: ignore[arg-type]

    def _surface_fieldbook(self, receipts: Sequence[object]) -> None:
        self._fieldbook_receipts.extend(
            receipt.digest() for receipt in receipts  # type: ignore[attr-defined]
        )

    def record_delivery(
        self,
        *,
        memory_ids: Sequence[str],
        observation: Observation,
    ) -> None:
        """Note that these memories actually reached a planner.

        Purely diagnostic: nothing orders or ranks on it, so a memory cannot
        become important merely by having been read.
        """

        if self._store is None:
            return
        failure = record_planner_delivery(
            self._store,
            self._authority,
            run_id=self._run_id,
            memory_ids=list(memory_ids),
        )
        if failure is not None:
            self._logger.write(
                "continuity_store_failed",
                step_index=observation.step_index,
                payload={"boundary": failure.boundary, "reason": failure.reason},
            )

    # --- commits -----------------------------------------------------------

    def apply_decision(
        self,
        decision: PlannerDecision,
        observation: Observation,
        *,
        authored_context: AuthoredPlannerContext | None,
        plan_id: str,
        step_id: str,
    ) -> None:
        if not decision.continuity_operations and not decision.fieldbook_operations:
            return
        if authored_context is None:
            raise RuntimeError("Planner-authored continuity has no authored planner context.")
        if decision.continuity_operations:
            self._surface(
                self._authority.apply(
                    decision.continuity_operations,
                    origin=ContinuityOrigin.DECISION,
                    authored_context=authored_context,
                    commit_observation=observation,
                    plan_id=plan_id,
                    plan_version=1,
                    step_id=step_id,
                )
            )
        if decision.fieldbook_operations:
            self._surface_fieldbook(
                self._fieldbook.apply(
                    decision.fieldbook_operations,
                    origin=ContinuityOrigin.DECISION,
                    authored_context=authored_context,
                    commit_observation=observation,
                    plan_id=plan_id,
                    plan_version=1,
                    step_id=step_id,
                )
            )

    def apply_plan(
        self,
        plan: PlanEnvelope,
        observation: Observation,
        *,
        authored_context: AuthoredPlannerContext,
    ) -> None:
        """Commit an accepted plan's continuity, and nothing about its future.

        This used to also write an automatic "Set out to: <objective>" episode,
        on the theory that continuity is too important to leave to the model.
        But that was a durable claim about work not yet done, filed under the
        kind reserved for events that happened. Plan purpose is working history
        now: the outcome recorder files the original objective once the plan has
        actually ended, with the reason it ended.
        """

        self._surface(
            self._authority.apply(
                plan.continuity_operations,
                origin=ContinuityOrigin.PLAN,
                authored_context=authored_context,
                commit_observation=observation,
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
            )
        )
        if plan.fieldbook_operations:
            self._surface_fieldbook(
                self._fieldbook.apply(
                    plan.fieldbook_operations,
                    origin=ContinuityOrigin.PLAN,
                    authored_context=authored_context,
                    commit_observation=observation,
                    plan_id=plan.plan_id,
                    plan_version=plan.plan_version,
                )
            )

    def apply_patch(
        self,
        operations: Sequence[ContinuityOperation],
        fieldbook_operations: Sequence[FieldbookOperation],
        observation: Observation,
        *,
        authored_context: AuthoredPlannerContext,
        plan_id: str,
        plan_version: int,
        step_id: str | None,
    ) -> None:
        """Commit a patch's continuity only where the patch itself took effect.

        Called at the exact point a revalidated patch becomes the active plan. A
        staged patch that is later rejected, superseded, or discarded never
        reaches here, so it leaves no trace in durable memory.
        """

        self._surface(
            self._authority.apply(
                operations,
                origin=ContinuityOrigin.PATCH,
                authored_context=authored_context,
                commit_observation=observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
            )
        )
        if fieldbook_operations:
            self._surface_fieldbook(
                self._fieldbook.apply(
                    fieldbook_operations,
                    origin=ContinuityOrigin.PATCH,
                    authored_context=authored_context,
                    commit_observation=observation,
                    plan_id=plan_id,
                    plan_version=plan_version,
                    step_id=step_id,
                )
            )

    # --- deliberate reads --------------------------------------------------

    def read_memory(
        self,
        action: RecallMemoryAction,
        observation: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> ActionReceipt:
        """Answer one deliberate read. Emits nothing into the game."""

        started_at = datetime.now(UTC)
        read_status = MemoryReadStatus.COMPLETED
        campaign_id: str | None = None
        if action.source == "working_outcomes":
            result = self._ledger.search_outcomes(
                query=action.query,
                limit=action.max_records,
            )
        elif self._store is None:
            read_status = MemoryReadStatus.UNAVAILABLE
            result = MemorySearchResult(
                query=action.query,
                reason="Durable memory is disabled for this run; nothing was read.",
            )
        else:
            campaign_id = self._store.campaign_id
            search = search_durable_memory(
                self._store,
                self._authority,
                query=action.query,
                limit=action.max_records,
            )
            result = search.result
            if self._authority.reads_degraded_reason is not None:
                read_status = MemoryReadStatus.FAILED
            if search.failure is not None:
                self._logger.write(
                    "continuity_store_failed",
                    step_index=observation.step_index,
                    payload={
                        "boundary": search.failure.boundary,
                        "reason": search.failure.reason,
                    },
                )
        read_receipt = build_memory_read_receipt(
            result,
            source=action.source,
            status=read_status,
            campaign_id=campaign_id,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id,
        )
        self._pending_memory_search = read_receipt
        receipt = self._read_receipt(action, started_at, read_receipt.reason)
        self._log_read(
            "memory_read", observation, read_receipt, receipt, plan_id, plan_version, step_id
        )
        return receipt

    def read_fieldbook(
        self,
        action: ReadFieldbookAction,
        observation: Observation,
        *,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> ActionReceipt:
        """Answer one bounded project read without creating a world command."""

        started_at = datetime.now(UTC)
        campaign_id: str | None = None
        status = FieldbookReadStatus.COMPLETED
        if self._store is None:
            status = FieldbookReadStatus.UNAVAILABLE
            result = FieldbookReadResult(
                project_id=action.project_id,
                query=action.query,
                reason="The durable fieldbook is disabled; nothing was read.",
            )
        elif self._authority.reads_degraded_reason is not None:
            campaign_id = self._store.campaign_id
            status = FieldbookReadStatus.FAILED
            result = FieldbookReadResult(
                project_id=action.project_id,
                query=action.query,
                reason=self._authority.reads_degraded_reason,
            )
        else:
            campaign_id = self._store.campaign_id
            try:
                result = self._store.fieldbook.read(
                    project_id=action.project_id,
                    query=action.query,
                    limit=action.max_entries,
                )
            except FieldbookTransitionError as exc:
                status = FieldbookReadStatus.FAILED
                result = FieldbookReadResult(
                    project_id=action.project_id,
                    query=action.query,
                    reason=str(exc),
                )
            except sqlite3.Error as exc:
                status = FieldbookReadStatus.FAILED
                reason = self._authority.quarantine_reads_after_store_failure(exc)
                result = FieldbookReadResult(
                    project_id=action.project_id,
                    query=action.query,
                    reason=reason,
                )
                self._logger.write(
                    "continuity_store_failed",
                    step_index=observation.step_index,
                    payload={
                        "boundary": "elective_fieldbook_read",
                        "reason": reason,
                    },
                )
        read_receipt = build_fieldbook_read_receipt(
            result,
            status=status,
            campaign_id=campaign_id,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id,
        )
        self._pending_fieldbook_read = read_receipt
        receipt = self._read_receipt(action, started_at, read_receipt.reason)
        self._log_read(
            "fieldbook_read", observation, read_receipt, receipt, plan_id, plan_version, step_id
        )
        return receipt

    def _read_receipt(
        self,
        action: RecallMemoryAction | ReadFieldbookAction,
        started_at: datetime,
        message: str,
    ) -> ActionReceipt:
        return ActionReceipt(
            action=action,
            control_mode=self._control_mode,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            primitive_actions=0,
            message=message,
        )

    def _log_read(
        self,
        event_type: str,
        observation: Observation,
        read_receipt: MemoryReadReceipt | FieldbookReadReceipt,
        receipt: ActionReceipt,
        plan_id: str,
        plan_version: int,
        step_id: str,
    ) -> None:
        self._logger.write(
            event_type,
            step_index=observation.step_index,
            payload={
                "plan_id": plan_id,
                "plan_version": plan_version,
                "step_id": step_id,
                "controller_primitives": 0,
                "world_command_created": False,
                "result": read_receipt.model_dump(mode="json"),
            },
        )
        self._logger.write(
            "action_receipt",
            step_index=observation.step_index,
            payload=receipt,
        )
