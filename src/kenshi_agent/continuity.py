"""Working continuity and the one path into durable kept memory.

Three authorities meet here and must not blur:

- **World evidence** — telemetry, receipts, revisions — is the only thing that
  can establish what the game did.
- **Working continuity** — the action and plan outcomes in `ContinuityLedger` —
  is runtime-owned, bounded, and run-scoped. It says what was attempted and
  what came of it.
- **Durable kept memory** — what the agent deliberately chose to carry — is
  agent-authored and always secondary to the first two.

`ContinuityAuthority` is the only route from a planner's authored operations to
the memory store. Plans, single-step decisions, and applied patches all pass
through it, so grounding, target-identity, and receipt rules are stated once
instead of drifting into three implementations.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from .memory import MemoryStore, MemoryTransitionError
from .models import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionOutcomeDigest,
    ActionOutcomeEvidence,
    AuthoredPlannerContext,
    CanonicalMemoryProvenance,
    ContinuityOperation,
    ContinuityOperationReceipt,
    ContinuityOperationStatus,
    ContinuityOrigin,
    CurrentObservationEvidence,
    EvidenceAuthority,
    EvidenceReference,
    KeepMemoryOperation,
    MemoryEvidence,
    MemoryKind,
    MemoryRecord,
    MemoryResolutionDisposition,
    MemorySearchResult,
    MemoryStatus,
    Observation,
    PlanDisposition,
    PlanOutcome,
    PlanOutcomeDigest,
    PlanOutcomeEvidence,
    ReinforceMemoryOperation,
    ResolvedEvidenceSnapshot,
    ResolveMemoryOperation,
    SupersedeMemoryOperation,
    WorldStateRevision,
    new_continuity_receipt_id,
)

# A fact or an episode reports something that happened; it must point at what
# proved it. A commitment is an intention and a hypothesis is an uncertainty:
# both are the agent's own, so both may stand without external evidence.
GROUNDED_KINDS = frozenset({MemoryKind.FACT, MemoryKind.EPISODE})

MAX_PLAN_OUTCOMES = 6

# Four references, each bounded by its own ID pattern, cannot approach
# `MemoryWrite.evidence`'s thousand-character bound. Truncating here would
# silently shorten grounding rather than fail, so the model's bound is the bound.
EVIDENCE_SEPARATOR = "; "

FACT_AUTHORITIES = frozenset(
    {
        EvidenceAuthority.FRESH_WORLD_OBSERVATION,
        EvidenceAuthority.VERIFIED_WORLD_EFFECT,
        EvidenceAuthority.ATTEMPT_CHANGED,
    }
)
EPISODE_AUTHORITIES = FACT_AUTHORITIES | frozenset(
    {
        EvidenceAuthority.OBSERVED_CHANGE,
        EvidenceAuthority.ATTEMPT_NO_OP,
        EvidenceAuthority.ATTEMPT_NOT_EXECUTED,
        EvidenceAuthority.ATTEMPT_UNKNOWN,
        EvidenceAuthority.PLAN_DISPOSITION,
    }
)
COMMITMENT_CLOSURE_AUTHORITIES = FACT_AUTHORITIES


class EvidenceResolutionError(ValueError):
    """A cited identity is not one this runtime issued, or no longer resolves."""


class ContinuityLogger(Protocol):
    def write(
        self,
        event_type: str,
        *,
        step_index: int | None = None,
        payload: Any = None,
    ) -> None: ...


class ContinuityLedger:
    """Bounded working continuity for one run.

    The bound is on what a planner is *shown*, not on what the runtime is
    willing to vouch for. Evicting an outcome from the visible window must not
    turn an honest citation of it into an invention, so issued identities are
    remembered even after their records are trimmed away.

    Deliberately not a dataclass: mutmut cannot see behavior inside a decorated
    class, and an authority seam that no mutant can reach is not measured.
    """

    __slots__ = (
        "run_id",
        "action_outcome_limit",
        "plan_outcome_limit",
        "_action_outcomes",
        "_plan_outcomes",
        "_action_outcome_digests",
        "_plan_outcome_digests",
        "_action_outcomes_recorded",
        "_plan_outcomes_recorded",
    )

    def __init__(
        self,
        *,
        run_id: str,
        action_outcome_limit: int,
        plan_outcome_limit: int = MAX_PLAN_OUTCOMES,
    ) -> None:
        self.run_id = run_id
        self.action_outcome_limit = action_outcome_limit
        self.plan_outcome_limit = plan_outcome_limit
        self._action_outcomes: list[ActionOutcome] = []
        self._plan_outcomes: list[PlanOutcome] = []
        self._action_outcome_digests: dict[str, ActionOutcomeDigest] = {}
        self._plan_outcome_digests: dict[str, PlanOutcomeDigest] = {}
        self._action_outcomes_recorded = 0
        self._plan_outcomes_recorded = 0

    def reset(self) -> None:
        self._action_outcomes.clear()
        self._plan_outcomes.clear()
        self._action_outcome_digests.clear()
        self._plan_outcome_digests.clear()
        self._action_outcomes_recorded = 0
        self._plan_outcomes_recorded = 0

    def next_action_outcome_id(self) -> str:
        self._action_outcomes_recorded += 1
        return f"ao-{self._action_outcomes_recorded}"

    def record_action_outcome(self, outcome: ActionOutcome) -> None:
        self._action_outcome_digests[outcome.outcome_id] = ActionOutcomeDigest(
            outcome_id=outcome.outcome_id,
            run_id=outcome.run_id,
            plan_id=outcome.plan_id,
            plan_version=outcome.plan_version,
            step_id=outcome.step_id,
            command_id=outcome.command_id,
            action_kind=outcome.action.kind,
            assessment=outcome.assessment,
            executed=outcome.executed,
            causal_revision_advanced=outcome.causal_revision_advanced,
            controller_verified=outcome.controller_verified,
            semantic_status=outcome.semantic_status,
            target_id=outcome.target_id,
            started_after_revision=outcome.started_after_revision,
            completed_at_revision=outcome.completed_at_revision,
            evidence_summary=outcome.feedback[:500],
            recorded_at=outcome.recorded_at,
        )
        self._action_outcomes.append(outcome)
        del self._action_outcomes[: -self.action_outcome_limit or None]

    def record_plan_outcome(
        self,
        *,
        plan_id: str,
        plan_version: int,
        objective: str,
        disposition: PlanDisposition,
        reason: str,
        completed_step_ids: Sequence[str],
        actions_completed: int,
        terminal_revision: WorldStateRevision | None,
        started_at: datetime,
        finished_at: datetime,
    ) -> PlanOutcome:
        self._plan_outcomes_recorded += 1
        outcome = PlanOutcome(
            plan_outcome_id=f"po-{self._plan_outcomes_recorded}",
            run_id=self.run_id,
            plan_id=plan_id,
            plan_version=plan_version,
            objective=objective,
            disposition=disposition,
            reason=reason,
            completed_step_ids=list(completed_step_ids)[:16],
            actions_completed=actions_completed,
            terminal_revision=terminal_revision,
            started_at=started_at,
            finished_at=finished_at,
        )
        self._plan_outcome_digests[outcome.plan_outcome_id] = PlanOutcomeDigest(
            plan_outcome_id=outcome.plan_outcome_id,
            run_id=outcome.run_id,
            plan_id=outcome.plan_id,
            plan_version=outcome.plan_version,
            objective=outcome.objective,
            disposition=outcome.disposition,
            reason_digest=outcome.reason,
            completed_step_ids=outcome.completed_step_ids,
            actions_completed=outcome.actions_completed,
            terminal_revision=outcome.terminal_revision,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
        )
        self._plan_outcomes.append(outcome)
        del self._plan_outcomes[: -self.plan_outcome_limit or None]
        return outcome

    @property
    def recent_action_outcomes(self) -> list[ActionOutcome]:
        if self.action_outcome_limit <= 0:
            return []
        return list(self._action_outcomes)

    @property
    def recent_plan_outcomes(self) -> list[PlanOutcome]:
        if self.plan_outcome_limit <= 0:
            return []
        return list(self._plan_outcomes)

    def has_action_outcome(self, outcome_id: str) -> bool:
        return outcome_id in self._action_outcome_digests

    def has_plan_outcome(self, plan_outcome_id: str) -> bool:
        return plan_outcome_id in self._plan_outcome_digests

    def action_outcome_digest(self, outcome_id: str) -> ActionOutcomeDigest | None:
        return self._action_outcome_digests.get(outcome_id)

    def plan_outcome_digest(self, plan_outcome_id: str) -> PlanOutcomeDigest | None:
        return self._plan_outcome_digests.get(plan_outcome_id)

    def search_outcomes(self, *, query: str, limit: int) -> MemorySearchResult:
        """Resurface bounded compact evidence without restoring rich records."""

        if limit < 1:
            raise ValueError(  # mutation: reason
                "outcome search limit must be at least one"  # mutation: reason
            )
        needle = query.casefold()
        action_matches = [
            digest
            for digest in reversed(tuple(self._action_outcome_digests.values()))
            if needle
            in " ".join(
                part
                for part in (
                    digest.outcome_id,
                    digest.plan_id,
                    digest.step_id,
                    digest.command_id,
                    digest.action_kind,
                    digest.assessment.value,
                    digest.semantic_status,
                    digest.target_id,
                    digest.evidence_summary,
                )
                if part is not None
            ).casefold()
        ]
        plan_matches = [
            digest
            for digest in reversed(tuple(self._plan_outcome_digests.values()))
            if needle
            in " ".join(
                (
                    digest.plan_outcome_id,
                    digest.plan_id,
                    digest.objective,
                    digest.disposition.value,
                    digest.reason_digest,
                    *digest.completed_step_ids,
                )
            ).casefold()
        ]
        combined = len(action_matches) + len(plan_matches)
        shown_actions = action_matches[:limit]
        remaining = limit - len(shown_actions)
        shown_plans = plan_matches[:remaining]
        return MemorySearchResult(
            query=query,
            action_outcomes=shown_actions,
            plan_outcomes=shown_plans,
            matched=combined,
            truncated=combined > limit,
            reason=(  # mutation: reason
                f"{combined} retained working outcomes match {query!r}; "  # mutation: reason
                f"{len(shown_actions) + len(shown_plans)} shown."  # mutation: reason
            ),
        )

    def action_outcome(self, outcome_id: str) -> ActionOutcome | None:
        for outcome in reversed(self._action_outcomes):
            if outcome.outcome_id == outcome_id:
                return outcome
        return None

    def plan_outcome(self, plan_outcome_id: str) -> PlanOutcome | None:
        for outcome in reversed(self._plan_outcomes):
            if outcome.plan_outcome_id == plan_outcome_id:
                return outcome
        return None


def resolve_evidence_reference(
    reference: EvidenceReference,
    *,
    authored_context: AuthoredPlannerContext,
    ledger: ContinuityLedger,
    store: MemoryStore | None,
    advisor_brief_ids: set[str],
) -> ResolvedEvidenceSnapshot:
    """Resolve one identity to immutable typed authority, or refuse it.

    Refusal is the point. Every branch checks the authority that actually owns
    the identity, so a plausible-looking ID from another run, another campaign,
    or nowhere at all cannot become evidence for a durable claim. Rendering is
    a projection of this snapshot and never the validation input.
    """

    manifest = authored_context.manifest
    if isinstance(reference, CurrentObservationEvidence):
        if not manifest.current_observation_delivered:
            raise EvidenceResolutionError(  # mutation: reason
                "The authored planner input did not contain "  # mutation: reason
                "a current observation."  # mutation: reason
            )
        if not manifest.telemetry_was_fresh:
            raise EvidenceResolutionError(  # mutation: reason
                "The current observation in this planner context was stale "  # mutation: reason
                "and cannot establish a fresh world claim."  # mutation: reason
            )
        revision = manifest.authored_revision
        return ResolvedEvidenceSnapshot(
            source="current_observation",
            source_id=f"{manifest.context_id}:current_observation",
            authority=EvidenceAuthority.FRESH_WORLD_OBSERVATION,
            authored_context_id=manifest.context_id,
            run_id=manifest.run_id,
            world_revision=revision,
            compact_summary=(
                "current_observation("
                f"telemetry_sequence={revision.telemetry_sequence}, "
                f"frame_sequence={revision.frame_sequence})"
            ),
        )
    if isinstance(reference, ActionOutcomeEvidence):
        if reference.outcome_id not in manifest.action_outcome_ids:
            raise EvidenceResolutionError(  # mutation: reason
                f"Action outcome {reference.outcome_id!r} was not delivered "  # mutation: reason
                f"in planner context {manifest.context_id}."  # mutation: reason
            )
        action_digest = ledger.action_outcome_digest(reference.outcome_id)
        if action_digest is None or action_digest.run_id != manifest.run_id:
            raise EvidenceResolutionError(  # mutation: reason
                f"No action outcome {reference.outcome_id!r} "  # mutation: reason
                "was recorded in this run."  # mutation: reason
            )
        if not action_digest.executed:
            authority = EvidenceAuthority.ATTEMPT_NOT_EXECUTED
        elif (
            action_digest.controller_verified
            and action_digest.assessment is ActionOutcomeAssessment.CHANGED
        ):
            authority = EvidenceAuthority.VERIFIED_WORLD_EFFECT
        elif action_digest.assessment is ActionOutcomeAssessment.CHANGED:
            authority = (
                EvidenceAuthority.ATTEMPT_CHANGED
                if action_digest.causal_revision_advanced
                else EvidenceAuthority.OBSERVED_CHANGE
            )
        elif action_digest.assessment is ActionOutcomeAssessment.NO_OP:
            authority = EvidenceAuthority.ATTEMPT_NO_OP
        elif action_digest.assessment is ActionOutcomeAssessment.NOT_EXECUTED:
            authority = EvidenceAuthority.ATTEMPT_NOT_EXECUTED
        else:
            authority = EvidenceAuthority.ATTEMPT_UNKNOWN
        return ResolvedEvidenceSnapshot(
            source="action_outcome",
            source_id=action_digest.outcome_id,
            authority=authority,
            authored_context_id=manifest.context_id,
            run_id=action_digest.run_id,
            world_revision=action_digest.completed_at_revision,
            assessment=action_digest.assessment,
            action_kind=action_digest.action_kind,
            executed=action_digest.executed,
            causal_revision_advanced=action_digest.causal_revision_advanced,
            controller_verified=action_digest.controller_verified,
            semantic_status=action_digest.semantic_status,
            target_id=action_digest.target_id,
            compact_summary=(
                f"action_outcome({action_digest.outcome_id}: "
                f"{action_digest.assessment.value})"
            ),
        )
    if isinstance(reference, PlanOutcomeEvidence):
        if reference.plan_outcome_id not in manifest.plan_outcome_ids:
            raise EvidenceResolutionError(  # mutation: reason
                f"Plan outcome {reference.plan_outcome_id!r} was not delivered "  # mutation: reason
                f"in planner context {manifest.context_id}."  # mutation: reason
            )
        plan_digest = ledger.plan_outcome_digest(reference.plan_outcome_id)
        if plan_digest is None or plan_digest.run_id != manifest.run_id:
            raise EvidenceResolutionError(  # mutation: reason
                f"No plan outcome {reference.plan_outcome_id!r} "  # mutation: reason
                "was recorded in this run."  # mutation: reason
            )
        return ResolvedEvidenceSnapshot(
            source="plan_outcome",
            source_id=plan_digest.plan_outcome_id,
            authority=EvidenceAuthority.PLAN_DISPOSITION,
            authored_context_id=manifest.context_id,
            run_id=plan_digest.run_id,
            world_revision=plan_digest.terminal_revision,
            plan_disposition=plan_digest.disposition,
            compact_summary=(
                f"plan_outcome({plan_digest.plan_outcome_id}: "
                f"{plan_digest.disposition.value})"
            ),
        )
    if isinstance(reference, MemoryEvidence):
        if reference.memory_id not in manifest.memory_ids:
            raise EvidenceResolutionError(  # mutation: reason
                f"Memory {reference.memory_id!r} was not delivered "  # mutation: reason
                f"in planner context {manifest.context_id}."  # mutation: reason
            )
        if store is None:
            raise EvidenceResolutionError(  # mutation: reason
                "Durable memory is unavailable, so memory "  # mutation: reason
                f"{reference.memory_id} cannot be cited."  # mutation: reason
            )
        record = store.get(reference.memory_id)
        if record is None or record.status is not MemoryStatus.ACTIVE:
            raise EvidenceResolutionError(  # mutation: reason
                f"No active memory {reference.memory_id} "  # mutation: reason
                "exists in this campaign."  # mutation: reason
            )
        return ResolvedEvidenceSnapshot(
            source="memory",
            source_id=record.memory_id,
            authority=EvidenceAuthority.AGENT_BELIEF,
            authored_context_id=manifest.context_id,
            run_id=manifest.run_id,
            memory_kind=record.kind,
            memory_status=record.status,
            target_id=record.target_id,
            compact_summary=f"memory {record.memory_id}",
        )
    if reference.brief_id not in manifest.advisor_brief_ids:
        raise EvidenceResolutionError(  # mutation: reason
            f"Advisor brief {reference.brief_id!r} was not delivered "  # mutation: reason
            f"in planner context {manifest.context_id}."  # mutation: reason
        )
    if reference.brief_id not in advisor_brief_ids:
        raise EvidenceResolutionError(  # mutation: reason
            f"No advisor brief {reference.brief_id!r} "  # mutation: reason
            "was issued in this run."  # mutation: reason
        )
    # Marked as advice on purpose: a strategic brief is a second opinion about
    # the world, never an observation of it.
    return ResolvedEvidenceSnapshot(
        source="advisor_brief",
        source_id=reference.brief_id,
        authority=EvidenceAuthority.ADVICE,
        authored_context_id=manifest.context_id,
        run_id=manifest.run_id,
        compact_summary=(
            f"advisor_brief({reference.brief_id}, advice not world evidence)"
        ),
    )


def render_evidence_snapshot(snapshot: ResolvedEvidenceSnapshot) -> str:
    """Human-readable projection; capability checks use the typed snapshot."""

    return snapshot.compact_summary


def render_evidence_reference(
    reference: EvidenceReference,
    *,
    authored_context: AuthoredPlannerContext,
    ledger: ContinuityLedger,
    store: MemoryStore | None,
    advisor_brief_ids: set[str],
) -> str:
    """Compatibility projection for callers that only need display text."""

    return render_evidence_snapshot(
        resolve_evidence_reference(
            reference,
            authored_context=authored_context,
            ledger=ledger,
            store=store,
            advisor_brief_ids=advisor_brief_ids,
        )
    )


class ContinuityAuthority:
    """The only route from planner-authored continuity to durable memory.

    Deliberately not a dataclass, for the same reason as `ContinuityLedger`.
    """

    __slots__ = (
        "run_id",
        "store",
        "ledger",
        "logger",
        "advisor_brief_ids",
        "_reads_degraded_reason",
        "_writes_degraded_reason",
    )

    def __init__(
        self,
        *,
        run_id: str,
        store: MemoryStore | None,
        ledger: ContinuityLedger,
        logger: ContinuityLogger,
        advisor_brief_ids: Callable[[], set[str]],
    ) -> None:
        self.run_id = run_id
        self.store = store
        self.ledger = ledger
        self.logger = logger
        self.advisor_brief_ids = advisor_brief_ids
        self._reads_degraded_reason: str | None = None
        self._writes_degraded_reason: str | None = None

    @property
    def reads_degraded_reason(self) -> str | None:
        return self._reads_degraded_reason

    @property
    def writes_degraded_reason(self) -> str | None:
        return self._writes_degraded_reason

    def quarantine_reads_after_store_failure(self, exc: sqlite3.Error) -> str:
        """Disable reads and writes when a read can no longer be trusted."""

        if self._reads_degraded_reason is None:
            self._reads_degraded_reason = (
                "Durable continuity reads and writes are disabled for this run "
                "after an unexpected store failure "
                f"({type(exc).__name__}: {exc})."
            )
        if self._writes_degraded_reason is None:
            self._writes_degraded_reason = self._reads_degraded_reason
        return self._reads_degraded_reason

    def quarantine_writes_after_store_failure(self, exc: sqlite3.Error) -> str:
        """Disable later writes while preserving the first exact store failure."""

        if self._writes_degraded_reason is None:
            self._writes_degraded_reason = (
                "Durable continuity writes are disabled for this run after "
                f"an unexpected store failure ({type(exc).__name__}: {exc})."
            )
        return self._writes_degraded_reason

    def apply(
        self,
        operations: Sequence[ContinuityOperation],
        *,
        origin: ContinuityOrigin,
        authored_context: AuthoredPlannerContext,
        commit_observation: Observation,
        plan_id: str | None = None,
        plan_version: int | None = None,
        step_id: str | None = None,
    ) -> list[ContinuityOperationReceipt]:
        receipts: list[ContinuityOperationReceipt] = []
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
            self.logger.write(
                "continuity_receipt",
                step_index=commit_observation.step_index,
                payload=receipt.model_dump(mode="json"),
            )
        return receipts

    def _apply_one(
        self,
        operation: ContinuityOperation,
        *,
        origin: ContinuityOrigin,
        authored_context: AuthoredPlannerContext,
        commit_observation: Observation,
        plan_id: str | None,
        plan_version: int | None,
        step_id: str | None,
    ) -> ContinuityOperationReceipt:
        receipt_id = new_continuity_receipt_id()

        def receipt(
            status: ContinuityOperationStatus,
            reason: str,
            *,
            memory_id: str | None = None,
            memory_status: MemoryStatus | None = None,
            evidence: str | None = None,
            resolved_evidence: Sequence[ResolvedEvidenceSnapshot] = (),
            writes_degraded: bool = False,
        ) -> ContinuityOperationReceipt:
            return ContinuityOperationReceipt(
                receipt_id=receipt_id,
                origin=origin,
                status=status,
                operation=operation,
                reason=reason,
                memory_id=memory_id,
                memory_status=memory_status,
                evidence=evidence,
                resolved_evidence=list(resolved_evidence),
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                authored_context_id=authored_context.manifest.context_id,
                authored_revision=authored_context.manifest.authored_revision,
                commit_revision=commit_observation.world_revision,
                writes_degraded=writes_degraded,
            )

        def store_failure(
            exc: sqlite3.Error,
            *,
            evidence: str | None = None,
            resolved_evidence: Sequence[ResolvedEvidenceSnapshot] = (),
            read_failed: bool = False,
        ) -> ContinuityOperationReceipt:
            reason = (
                self.quarantine_reads_after_store_failure(exc)
                if read_failed
                else self.quarantine_writes_after_store_failure(exc)
            )
            return receipt(
                ContinuityOperationStatus.FAILED,
                reason,
                evidence=evidence,
                resolved_evidence=resolved_evidence,
                writes_degraded=True,
            )

        if authored_context.manifest.run_id != self.run_id:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                "The planner context belongs to another run.",  # mutation: reason
            )

        if self._writes_degraded_reason is not None:
            return receipt(
                ContinuityOperationStatus.FAILED,
                self._writes_degraded_reason,
                writes_degraded=True,
            )

        if isinstance(operation, (KeepMemoryOperation, SupersedeMemoryOperation)):
            if operation.kind in GROUNDED_KINDS and not operation.references:
                return receipt(
                    ContinuityOperationStatus.REJECTED,
                    f"A {operation.kind.value} must cite at least one "  # mutation: reason
                    "evidence reference. Use commitment for an intention "  # mutation: reason
                    "or hypothesis for an uncertainty.",  # mutation: reason
                )
            if operation.target_id is not None:
                if (
                    operation.target_id
                    not in authored_context.manifest.current_target_ids
                ):
                    return receipt(
                        ContinuityOperationStatus.REJECTED,
                        f"target_id {operation.target_id!r} is not "  # mutation: reason
                        "an entity in the current observation. Copy an "  # mutation: reason
                        "exact ID from fresh telemetry or leave it null.",  # mutation: reason
                    )

        references = getattr(operation, "references", ())
        try:
            resolved = [
                resolve_evidence_reference(
                    reference,
                    authored_context=authored_context,
                    ledger=self.ledger,
                    store=self.store,
                    advisor_brief_ids=self.advisor_brief_ids(),
                )
                for reference in references
            ]
        except EvidenceResolutionError as exc:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                str(exc),  # mutation: reason
            )
        except sqlite3.Error as exc:
            return store_failure(exc, read_failed=True)

        try:
            evidence_error = self._admissibility_error(operation, resolved)
        except sqlite3.Error as exc:
            return store_failure(
                exc,
                resolved_evidence=resolved,
                read_failed=True,
            )
        if evidence_error is not None:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                evidence_error,
                resolved_evidence=resolved,
            )

        rendered = [render_evidence_snapshot(snapshot) for snapshot in resolved]
        evidence = EVIDENCE_SEPARATOR.join(rendered) or None
        if self.store is None:
            return receipt(
                ContinuityOperationStatus.NO_OP,
                "Durable memory is disabled for this run; "  # mutation: reason
                "nothing was kept.",  # mutation: reason
                evidence=evidence,
                resolved_evidence=resolved,
            )

        provenance = CanonicalMemoryProvenance(
            operation=operation,
            origin=origin,
            run_id=self.run_id,
            authored_context_id=authored_context.manifest.context_id,
            authored_revision=authored_context.manifest.authored_revision,
            commit_revision=commit_observation.world_revision,
            references=list(references),
            resolved_evidence=resolved,
            plan_id=plan_id,
            plan_version=plan_version,
            step_id=step_id,
            rendered_grounding=evidence,
        )

        # Every transition below is refused rather than raised through: an
        # invalid continuity update must not take an otherwise valid game plan
        # down with it.
        try:
            record = self._transition(operation, evidence, provenance)
        except MemoryTransitionError as exc:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                str(exc),  # mutation: reason
                evidence=evidence,
                resolved_evidence=resolved,
            )
        except sqlite3.Error as exc:
            return store_failure(
                exc,
                evidence=evidence,
                resolved_evidence=resolved,
            )
        return receipt(
            ContinuityOperationStatus.ACCEPTED,
            f"{operation.operation} applied to "  # mutation: reason
            f"memory {record.memory_id} ({record.status.value}).",  # mutation: reason
            memory_id=record.memory_id,
            memory_status=record.status,
            evidence=evidence,
            resolved_evidence=resolved,
        )

    def _admissibility_error(
        self,
        operation: ContinuityOperation,
        resolved: Sequence[ResolvedEvidenceSnapshot],
    ) -> str | None:
        """Apply the evidence-capability matrix before anything is persisted."""

        authorities = {snapshot.authority for snapshot in resolved}
        replacement_kind = (
            operation.kind
            if isinstance(operation, (KeepMemoryOperation, SupersedeMemoryOperation))
            else None
        )
        if replacement_kind is MemoryKind.FACT and not (
            authorities & FACT_AUTHORITIES
        ):
            return (  # mutation: reason
                "A fact needs fresh world observation, a controller-verified "  # mutation: reason
                "world effect, or a causally observed change. Advice, memory, "  # mutation: reason
                "plan disposition, no-op, not-executed, and unknown outcomes "  # mutation: reason
                "cannot establish it."  # mutation: reason
            )
        if replacement_kind is MemoryKind.EPISODE and not (
            authorities & EPISODE_AUTHORITIES
        ):
            return (  # mutation: reason
                "An episode needs a current observation, action attempt, or "  # mutation: reason
                "plan lifecycle outcome. Advice or remembered belief alone "  # mutation: reason
                "cannot establish that an episode occurred."  # mutation: reason
            )
        if not isinstance(operation, ResolveMemoryOperation):
            return None
        if not operation.references:
            return (  # mutation: reason
                "Resolve requires at least one explicit evidence reference."  # mutation: reason
            )
        if self.store is None:
            return None
        record = self.store.get(operation.memory_id)
        if record is None or record.status is not MemoryStatus.ACTIVE:
            # Let the transition boundary retain its canonical unknown/closed
            # diagnostic after evidence has resolved.
            return None
        if record.kind not in {MemoryKind.COMMITMENT, MemoryKind.HYPOTHESIS}:
            return (  # mutation: reason
                f"A {record.kind.value} cannot be resolved. "  # mutation: reason
                "Facts and episodes must be superseded or retracted "  # mutation: reason
                "so their history remains honest."  # mutation: reason
            )
        if record.kind is MemoryKind.COMMITMENT:
            disposition = (
                operation.disposition or MemoryResolutionDisposition.COMPLETED
            )
            if disposition not in {
                MemoryResolutionDisposition.COMPLETED,
                MemoryResolutionDisposition.ABANDONED,
            }:
                return (  # mutation: reason
                    "A commitment resolves only as completed or abandoned; "  # mutation: reason
                    f"{disposition.value} is a hypothesis disposition."  # mutation: reason
                )
            if not authorities & COMMITMENT_CLOSURE_AUTHORITIES:
                return (  # mutation: reason
                    "Closing a commitment requires fresh or causally verified "  # mutation: reason
                    "world evidence. A no-op, unknown, not-executed "  # mutation: reason
                    "action, plan "  # mutation: reason
                    "disposition, advice, or belief cannot prove delivery."  # mutation: reason
                )
            return None
        if operation.disposition not in {
            MemoryResolutionDisposition.CONFIRMED,
            MemoryResolutionDisposition.REJECTED,
            MemoryResolutionDisposition.UNKNOWN,
        }:
            return (  # mutation: reason
                "Resolving a hypothesis requires disposition confirmed, "  # mutation: reason
                "rejected, or unknown."  # mutation: reason
            )
        if operation.disposition is not MemoryResolutionDisposition.UNKNOWN and not (
            authorities & FACT_AUTHORITIES
        ):
            return (  # mutation: reason
                "Confirming or rejecting a hypothesis requires fresh or "  # mutation: reason
                "causally observed world evidence."  # mutation: reason
            )
        if operation.disposition is MemoryResolutionDisposition.UNKNOWN and not (
            authorities & EPISODE_AUTHORITIES
        ):
            return (  # mutation: reason
                "Closing a hypothesis as unknown requires an observed attempt "  # mutation: reason
                "or current world evidence, not advice or belief alone."  # mutation: reason
            )
        return None

    def _transition(
        self,
        operation: ContinuityOperation,
        evidence: str | None,
        provenance: CanonicalMemoryProvenance,
    ) -> MemoryRecord:
        assert self.store is not None
        if isinstance(operation, KeepMemoryOperation):
            return self.store.keep(
                self.run_id,
                kind=operation.kind,
                content=operation.content,
                salience=operation.salience,
                grounding=evidence,
                target_id=operation.target_id,
                provenance=provenance,
            )
        if isinstance(operation, ReinforceMemoryOperation):
            return self.store.reinforce(
                self.run_id,
                operation.memory_id,
                grounding=evidence,
                salience=operation.salience,
                provenance=provenance,
            )
        if isinstance(operation, ResolveMemoryOperation):
            return self.store.resolve(
                self.run_id,
                operation.memory_id,
                reason=operation.reason,
                grounding=evidence,
                disposition=(
                    operation.disposition
                    or MemoryResolutionDisposition.COMPLETED
                ),
                provenance=provenance,
            )
        if isinstance(operation, SupersedeMemoryOperation):
            return self.store.supersede(
                self.run_id,
                operation.memory_id,
                kind=operation.kind,
                content=operation.content,
                salience=operation.salience,
                grounding=evidence,
                target_id=operation.target_id,
                provenance=provenance,
            )
        return self.store.retract(
            self.run_id,
            operation.memory_id,
            reason=operation.reason,
            provenance=provenance,
        )
