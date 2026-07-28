"""The sole planner-authored path into the campaign fieldbook."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from .continuity import (
    EPISODE_AUTHORITIES,
    FACT_AUTHORITIES,
    ContinuityAuthority,
    EvidenceResolutionError,
    render_evidence_snapshot,
    resolve_evidence_reference,
)
from .fieldbook import FieldbookNoOp, FieldbookTransitionError
from .models import (
    AppendFieldbookEntryOperation,
    AuthoredPlannerContext,
    CanonicalFieldbookProvenance,
    ContinuityOperationStatus,
    ContinuityOrigin,
    CreateFieldbookProjectOperation,
    FieldbookEntry,
    FieldbookEntryKind,
    FieldbookOperation,
    FieldbookOperationReceipt,
    FieldbookProject,
    Observation,
    ResolvedEvidenceSnapshot,
    SelectFieldbookProjectOperation,
    SetFieldbookProjectStatusOperation,
    UpdateFieldbookSummaryOperation,
    new_fieldbook_operation_receipt_id,
)

_FACT_ENTRY_KINDS = frozenset(
    {
        FieldbookEntryKind.OBSERVATION,
        FieldbookEntryKind.MANIFEST,
        FieldbookEntryKind.EXPENSE,
    }
)
_EPISODE_ENTRY_KINDS = frozenset(
    {
        FieldbookEntryKind.INCIDENT,
        FieldbookEntryKind.ROUTE_ENTRY,
    }
)


class FieldbookAuthority:
    """Validate fieldbook transitions against exact delivered planner context.

    Store health is intentionally owned by ``ContinuityAuthority``. Memory and
    fieldbook share one SQLite connection, so pretending one durable subsystem
    remained writable after that connection failed would be dishonest.
    """

    __slots__ = ("continuity",)

    def __init__(self, *, continuity: ContinuityAuthority) -> None:
        self.continuity = continuity

    def apply(
        self,
        operations: Sequence[FieldbookOperation],
        *,
        origin: ContinuityOrigin,
        authored_context: AuthoredPlannerContext,
        commit_observation: Observation,
        plan_id: str | None = None,
        plan_version: int | None = None,
        step_id: str | None = None,
    ) -> list[FieldbookOperationReceipt]:
        receipts: list[FieldbookOperationReceipt] = []
        for operation in operations:
            receipt = self._apply_one(
                operation,
                origin=origin,
                authored_context=authored_context,
                commit_observation=commit_observation,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
            )
            receipts.append(receipt)
            self.continuity.logger.write(
                "fieldbook_receipt",
                step_index=commit_observation.step_index,
                payload=receipt.model_dump(mode="json"),
            )
        return receipts

    def _apply_one(
        self,
        operation: FieldbookOperation,
        *,
        origin: ContinuityOrigin,
        authored_context: AuthoredPlannerContext,
        commit_observation: Observation,
        plan_id: str | None,
        plan_version: int | None,
        step_id: str | None,
    ) -> FieldbookOperationReceipt:
        receipt_id = new_fieldbook_operation_receipt_id()

        def receipt(
            status: ContinuityOperationStatus,
            reason: str,
            *,
            project_id: str | None = None,
            entry_id: str | None = None,
            resolved_evidence: Sequence[ResolvedEvidenceSnapshot] = (),
            writes_degraded: bool = False,
        ) -> FieldbookOperationReceipt:
            return FieldbookOperationReceipt(
                receipt_id=receipt_id,
                origin=origin,
                status=status,
                operation=operation,
                reason=reason,
                project_id=project_id,
                entry_id=entry_id,
                resolved_evidence=list(resolved_evidence),
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                authored_context_id=authored_context.manifest.context_id,
                authored_revision=authored_context.manifest.authored_revision,
                commit_revision=commit_observation.world_revision,
                writes_degraded=writes_degraded,
            )

        if authored_context.manifest.run_id != self.continuity.run_id:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                "The planner context belongs to another run.",
            )

        degraded_reason = self.continuity.writes_degraded_reason
        if degraded_reason is not None:
            return receipt(
                ContinuityOperationStatus.FAILED,
                degraded_reason,
                writes_degraded=True,
            )

        project_id = getattr(operation, "project_id", None)
        if (
            project_id is not None
            and project_id
            not in authored_context.manifest.fieldbook_project_ids
        ):
            return receipt(
                ContinuityOperationStatus.REJECTED,
                f"Fieldbook project {project_id!r} was not delivered in "
                f"planner context {authored_context.manifest.context_id}.",
            )

        references = (
            operation.references
            if isinstance(operation, AppendFieldbookEntryOperation)
            else []
        )
        try:
            resolved = [
                resolve_evidence_reference(
                    reference,
                    authored_context=authored_context,
                    ledger=self.continuity.ledger,
                    store=self.continuity.store,
                    advisor_brief_ids=self.continuity.advisor_brief_ids(),
                )
                for reference in references
            ]
        except EvidenceResolutionError as exc:
            return receipt(ContinuityOperationStatus.REJECTED, str(exc))
        except sqlite3.Error as exc:
            reason = self.continuity.quarantine_reads_after_store_failure(exc)
            return receipt(
                ContinuityOperationStatus.FAILED,
                reason,
                writes_degraded=True,
            )

        evidence_error = self._admissibility_error(operation, resolved)
        if evidence_error is not None:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                evidence_error,
                resolved_evidence=resolved,
            )

        if self.continuity.store is None:
            return receipt(
                ContinuityOperationStatus.NO_OP,
                "The durable fieldbook is disabled for this run; nothing was written.",
                resolved_evidence=resolved,
            )

        rendered = "; ".join(
            render_evidence_snapshot(snapshot) for snapshot in resolved
        ) or None
        provenance = CanonicalFieldbookProvenance(
            operation=operation,
            origin=origin,
            run_id=self.continuity.run_id,
            authored_context_id=authored_context.manifest.context_id,
            authored_revision=authored_context.manifest.authored_revision,
            commit_revision=commit_observation.world_revision,
            references=list(references),
            resolved_evidence=resolved,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id,
            rendered_grounding=rendered,
        )
        try:
            project, entry = self._transition(operation, provenance)
        except FieldbookNoOp as exc:
            return receipt(
                ContinuityOperationStatus.NO_OP,
                str(exc),
                project_id=project_id,
                resolved_evidence=resolved,
            )
        except FieldbookTransitionError as exc:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                str(exc),
                project_id=project_id,
                resolved_evidence=resolved,
            )
        except sqlite3.Error as exc:
            reason = self.continuity.quarantine_writes_after_store_failure(exc)
            return receipt(
                ContinuityOperationStatus.FAILED,
                reason,
                project_id=project_id,
                resolved_evidence=resolved,
                writes_degraded=True,
            )
        return receipt(
            ContinuityOperationStatus.ACCEPTED,
            self._accepted_reason(operation, project, entry),
            project_id=None if project is None else project.project_id,
            entry_id=None if entry is None else entry.entry_id,
            resolved_evidence=resolved,
        )

    @staticmethod
    def _admissibility_error(
        operation: FieldbookOperation,
        resolved: Sequence[ResolvedEvidenceSnapshot],
    ) -> str | None:
        if not isinstance(operation, AppendFieldbookEntryOperation):
            return None
        authorities = {snapshot.authority for snapshot in resolved}
        if operation.kind in _FACT_ENTRY_KINDS and not (
            authorities & FACT_AUTHORITIES
        ):
            return (
                f"A fieldbook {operation.kind.value} entry requires fresh or "
                "causally verified world evidence."
            )
        if operation.kind in _EPISODE_ENTRY_KINDS and not (
            authorities & EPISODE_AUTHORITIES
        ):
            return (
                f"A fieldbook {operation.kind.value} entry requires a current "
                "observation, action attempt, or plan lifecycle outcome."
            )
        return None

    def _transition(
        self,
        operation: FieldbookOperation,
        provenance: CanonicalFieldbookProvenance,
    ) -> tuple[FieldbookProject | None, FieldbookEntry | None]:
        store = self.continuity.store
        assert store is not None
        fieldbook = store.fieldbook
        if isinstance(operation, CreateFieldbookProjectOperation):
            return (
                fieldbook.create_project(
                    run_id=self.continuity.run_id,
                    kind=operation.kind,
                    title=operation.title,
                    summary=operation.summary,
                    provenance=provenance,
                ),
                None,
            )
        if isinstance(operation, AppendFieldbookEntryOperation):
            entry = fieldbook.append_entry(
                run_id=self.continuity.run_id,
                project_id=operation.project_id,
                kind=operation.kind,
                content=operation.content,
                provenance=provenance,
            )
            return fieldbook.get_project(operation.project_id), entry
        if isinstance(operation, UpdateFieldbookSummaryOperation):
            return (
                fieldbook.update_summary(
                    run_id=self.continuity.run_id,
                    project_id=operation.project_id,
                    summary=operation.summary,
                    provenance=provenance,
                ),
                None,
            )
        if isinstance(operation, SelectFieldbookProjectOperation):
            return (
                fieldbook.select_project(
                    run_id=self.continuity.run_id,
                    project_id=operation.project_id,
                    provenance=provenance,
                ),
                None,
            )
        assert isinstance(operation, SetFieldbookProjectStatusOperation)
        return (
            fieldbook.set_status(
                run_id=self.continuity.run_id,
                project_id=operation.project_id,
                status=operation.status,
                provenance=provenance,
            ),
            None,
        )

    @staticmethod
    def _accepted_reason(
        operation: FieldbookOperation,
        project: FieldbookProject | None,
        entry: FieldbookEntry | None,
    ) -> str:
        if entry is not None:
            return (
                f"append_entry added fieldbook entry {entry.entry_id} "
                f"to project {entry.project_id}."
            )
        if project is None:
            return "select_project cleared the selected fieldbook project."
        return (
            f"{operation.operation} applied to fieldbook project "
            f"{project.project_id} ({project.status.value})."
        )
