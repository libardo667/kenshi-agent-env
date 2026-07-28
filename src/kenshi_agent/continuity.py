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

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from .memory import MemoryStore, MemoryTransitionError
from .models import (
    ActionOutcome,
    ActionOutcomeEvidence,
    AuthoredPlannerContext,
    ContinuityOperation,
    ContinuityOperationReceipt,
    ContinuityOperationStatus,
    ContinuityOrigin,
    CurrentObservationEvidence,
    EvidenceReference,
    KeepMemoryOperation,
    MemoryEvidence,
    MemoryKind,
    MemoryRecord,
    Observation,
    PlanDisposition,
    PlanOutcome,
    PlanOutcomeEvidence,
    ReinforceMemoryOperation,
    ResolveMemoryOperation,
    SupersedeMemoryOperation,
    WorldStateRevision,
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
        "_issued_action_outcome_ids",
        "_issued_plan_outcome_ids",
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
        self._issued_action_outcome_ids: set[str] = set()
        self._issued_plan_outcome_ids: set[str] = set()
        self._action_outcomes_recorded = 0
        self._plan_outcomes_recorded = 0

    def reset(self) -> None:
        self._action_outcomes.clear()
        self._plan_outcomes.clear()
        self._issued_action_outcome_ids.clear()
        self._issued_plan_outcome_ids.clear()
        self._action_outcomes_recorded = 0
        self._plan_outcomes_recorded = 0

    def next_action_outcome_id(self) -> str:
        self._action_outcomes_recorded += 1
        return f"ao-{self._action_outcomes_recorded}"

    def record_action_outcome(self, outcome: ActionOutcome) -> None:
        self._issued_action_outcome_ids.add(outcome.outcome_id)
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
        self._issued_plan_outcome_ids.add(outcome.plan_outcome_id)
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
        return outcome_id in self._issued_action_outcome_ids

    def has_plan_outcome(self, plan_outcome_id: str) -> bool:
        return plan_outcome_id in self._issued_plan_outcome_ids

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


def render_evidence_reference(
    reference: EvidenceReference,
    *,
    authored_context: AuthoredPlannerContext,
    ledger: ContinuityLedger,
    store: MemoryStore | None,
    advisor_brief_ids: set[str],
) -> str:
    """Return the grounding text for one reference, or refuse to.

    Refusal is the point. Every branch checks the authority that actually owns
    the identity, so a plausible-looking ID from another run, another campaign,
    or nowhere at all cannot become grounding for a durable claim.
    """

    manifest = authored_context.manifest
    if isinstance(reference, CurrentObservationEvidence):
        if not manifest.current_observation_delivered:
            raise EvidenceResolutionError(  # mutation: reason
                "The authored planner input did not contain "  # mutation: reason
                "a current observation."  # mutation: reason
            )
        revision = manifest.authored_revision
        return (
            "current_observation("
            f"telemetry_sequence={revision.telemetry_sequence}, "
            f"frame_sequence={revision.frame_sequence})"
        )
    if isinstance(reference, ActionOutcomeEvidence):
        if reference.outcome_id not in manifest.action_outcome_ids:
            raise EvidenceResolutionError(  # mutation: reason
                f"Action outcome {reference.outcome_id!r} was not delivered "  # mutation: reason
                f"in planner context {manifest.context_id}."  # mutation: reason
            )
        if not ledger.has_action_outcome(reference.outcome_id):
            raise EvidenceResolutionError(  # mutation: reason
                f"No action outcome {reference.outcome_id!r} "  # mutation: reason
                "was recorded in this run."  # mutation: reason
            )
        outcome = ledger.action_outcome(reference.outcome_id)
        assessment = "evicted" if outcome is None else outcome.assessment.value
        return f"action_outcome({reference.outcome_id}: {assessment})"
    if isinstance(reference, PlanOutcomeEvidence):
        if reference.plan_outcome_id not in manifest.plan_outcome_ids:
            raise EvidenceResolutionError(  # mutation: reason
                f"Plan outcome {reference.plan_outcome_id!r} was not delivered "  # mutation: reason
                f"in planner context {manifest.context_id}."  # mutation: reason
            )
        if not ledger.has_plan_outcome(reference.plan_outcome_id):
            raise EvidenceResolutionError(  # mutation: reason
                f"No plan outcome {reference.plan_outcome_id!r} "  # mutation: reason
                "was recorded in this run."  # mutation: reason
            )
        plan_outcome = ledger.plan_outcome(reference.plan_outcome_id)
        disposition = (
            "evicted" if plan_outcome is None else plan_outcome.disposition.value
        )
        return f"plan_outcome({reference.plan_outcome_id}: {disposition})"
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
        if not store.exists(reference.memory_id):
            raise EvidenceResolutionError(  # mutation: reason
                f"No active memory {reference.memory_id} "  # mutation: reason
                "exists in this campaign."  # mutation: reason
            )
        return f"memory {reference.memory_id}"
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
    return f"advisor_brief({reference.brief_id}, advice not world evidence)"


class ContinuityAuthority:
    """The only route from planner-authored continuity to durable memory.

    Deliberately not a dataclass, for the same reason as `ContinuityLedger`.
    """

    __slots__ = ("run_id", "store", "ledger", "logger", "advisor_brief_ids")

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
        def receipt(
            status: ContinuityOperationStatus,
            reason: str,
            *,
            memory_id: str | None = None,
            evidence: str | None = None,
        ) -> ContinuityOperationReceipt:
            return ContinuityOperationReceipt(
                origin=origin,
                status=status,
                operation=operation,
                reason=reason,
                memory_id=memory_id,
                evidence=evidence,
                plan_id=plan_id,
                plan_version=plan_version,
                step_id=step_id,
                authored_context_id=authored_context.manifest.context_id,
                authored_revision=authored_context.manifest.authored_revision,
                commit_revision=commit_observation.world_revision,
            )

        if authored_context.manifest.run_id != self.run_id:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                "The planner context belongs to another run.",  # mutation: reason
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
            rendered = [
                render_evidence_reference(
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

        evidence = EVIDENCE_SEPARATOR.join(rendered) or None
        if self.store is None:
            return receipt(
                ContinuityOperationStatus.NO_OP,
                "Durable memory is disabled for this run; "  # mutation: reason
                "nothing was kept.",  # mutation: reason
                evidence=evidence,
            )

        # Every transition below is refused rather than raised through: an
        # invalid continuity update must not take an otherwise valid game plan
        # down with it.
        try:
            record = self._transition(operation, evidence)
        except MemoryTransitionError as exc:
            return receipt(
                ContinuityOperationStatus.REJECTED,
                str(exc),  # mutation: reason
                evidence=evidence,
            )
        return receipt(
            ContinuityOperationStatus.ACCEPTED,
            f"{operation.operation} applied to "  # mutation: reason
            f"memory {record.memory_id} ({record.status.value}).",  # mutation: reason
            memory_id=record.memory_id,
            evidence=evidence,
        )

    def _transition(
        self,
        operation: ContinuityOperation,
        evidence: str | None,
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
            )
        if isinstance(operation, ReinforceMemoryOperation):
            return self.store.reinforce(
                self.run_id,
                operation.memory_id,
                grounding=evidence,
                salience=operation.salience,
            )
        if isinstance(operation, ResolveMemoryOperation):
            return self.store.resolve(
                self.run_id,
                operation.memory_id,
                reason=operation.reason,
                grounding=evidence,
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
            )
        return self.store.retract(
            self.run_id,
            operation.memory_id,
            reason=operation.reason,
        )
