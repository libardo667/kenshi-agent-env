"""Evidence domain types."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, TypeAlias

from pydantic import (
    ConfigDict,
    Field,
    model_validator,
)

from .base import StrictModel
from .operation import Action
from .telemetry import (
    NormalizedPointerBounds,
    Vec3,
)
from .world import WorldStateRevision


class StateChange(StrictModel):
    """One field of the world that moved since the previous observation.

    Deliberately carries the values and not just the path. "money changed" does
    not tell an agent whether its purchase went through; "money 118 -> 96" does,
    and that is the difference between noticing a failed action and repeating it.
    """

    path: str = Field(min_length=1, max_length=200)
    before: str | None = Field(default=None, max_length=200)
    after: str | None = Field(default=None, max_length=200)


class ActionOutcomeAssessment(StrEnum):
    CHANGED = "changed"
    NO_OP = "no_op"
    NOT_EXECUTED = "not_executed"
    UNKNOWN = "unknown"


ACTION_OUTCOME_ID_PATTERN = r"^ao-[1-9][0-9]{0,8}$"
PLAN_OUTCOME_ID_PATTERN = r"^po-[1-9][0-9]{0,8}$"
PLAN_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,95}$"
STEP_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"


class ActionOutcome(StrictModel):
    """One attempted action and what the world did about it.

    `outcome_id` is runtime-owned. A planner may cite it as evidence for a
    later memory, which is only sound because the planner cannot mint one: an
    outcome exists after its action has already been assessed, so a plan
    physically cannot cite the success of its own future steps.
    """

    outcome_id: str = Field(pattern=ACTION_OUTCOME_ID_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(default=1, ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    command_id: str | None = Field(default=None, max_length=80)
    step_index: int = Field(ge=0)
    intent: str = Field(min_length=1, max_length=1000)
    action: Action
    executed: bool
    receipt_message: str = Field(default="", max_length=2000)
    assessment: ActionOutcomeAssessment
    # These fields survive the rich visible window in `ActionOutcomeDigest`.
    # They distinguish "the screen looked different" from a controller-owned
    # terminal, and preserve the exact target and causal revision basis after
    # the full receipt is evicted.
    causal_revision_advanced: bool | None = None
    controller_verified: bool = False
    semantic_status: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=200)
    feedback: str = Field(min_length=1, max_length=1000)
    started_after_revision: WorldStateRevision | None = None
    completed_at_revision: WorldStateRevision | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    visual_change_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    telemetry_changes: list[str] = Field(default_factory=list, max_length=30)
    selected_character_name: str | None = Field(default=None, max_length=200)
    position_before: Vec3 | None = None
    position_after: Vec3 | None = None
    # Which in-process game session this happened in. A load discards the world
    # an outcome describes while leaving `run_id` untouched, so run identity
    # cannot be the currency check once the agent can load for itself.
    identity_session_id: str | None = Field(default=None, max_length=200)
    # Runtime-owned evidence for admitting an exact retry after a definitive
    # no-op. It deliberately excludes time and generic UI churn: only state
    # known to change the action's result belongs in this fingerprint.
    retry_state_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class PlanDisposition(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"
    TERMINATED = "terminated"


class PlanOutcome(StrictModel):
    """Why a plan ended, in terms of what it originally set out to do.

    Without this the next planner reconstructs purpose from "Execute step X",
    which is not a purpose. The objective is copied from the plan that carried
    it, and the reason is the executor's terminal verdict — neither is written
    by a model after the fact.
    """

    plan_outcome_id: str = Field(pattern=PLAN_OUTCOME_ID_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=1000)
    disposition: PlanDisposition
    reason: str = Field(min_length=1, max_length=1000)
    completed_step_ids: list[str] = Field(default_factory=list, max_length=16)
    actions_completed: int = Field(default=0, ge=0)
    terminal_revision: WorldStateRevision | None = None
    started_at: datetime
    finished_at: datetime


class ActionOutcomeDigest(StrictModel):
    """Compact immutable evidence retained for the lifetime of one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_id: str = Field(pattern=ACTION_OUTCOME_ID_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    command_id: str | None = Field(default=None, max_length=80)
    action_kind: str = Field(min_length=1, max_length=80)
    assessment: ActionOutcomeAssessment
    executed: bool
    causal_revision_advanced: bool | None = None
    controller_verified: bool
    semantic_status: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=200)
    started_after_revision: WorldStateRevision | None = None
    completed_at_revision: WorldStateRevision | None = None
    evidence_summary: str = Field(min_length=1, max_length=500)
    recorded_at: datetime
    identity_session_id: str | None = Field(default=None, max_length=200)


class PlanOutcomeDigest(StrictModel):
    """Compact immutable plan lifecycle retained for the lifetime of one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_outcome_id: str = Field(pattern=PLAN_OUTCOME_ID_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=1000)
    disposition: PlanDisposition
    reason_digest: str = Field(min_length=1, max_length=1000)
    completed_step_ids: list[str] = Field(default_factory=list, max_length=16)
    actions_completed: int = Field(default=0, ge=0)
    terminal_revision: WorldStateRevision | None = None
    started_at: datetime
    finished_at: datetime


class CurrentObservationEvidence(StrictModel):
    """The exact observation the planner was looking at when it wrote this."""

    source: Literal["current_observation"] = "current_observation"


class ActionOutcomeEvidence(StrictModel):
    source: Literal["action_outcome"] = "action_outcome"
    outcome_id: str = Field(pattern=ACTION_OUTCOME_ID_PATTERN)


class PlanOutcomeEvidence(StrictModel):
    source: Literal["plan_outcome"] = "plan_outcome"
    plan_outcome_id: str = Field(pattern=PLAN_OUTCOME_ID_PATTERN)


MEMORY_ID_PATTERN = r"^mem-[A-Za-z0-9]{1,72}$"


class MemoryEvidence(StrictModel):
    source: Literal["memory"] = "memory"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)


class AdvisorBriefEvidence(StrictModel):
    """Advice, not world evidence. Rendered as such wherever it is stored."""

    source: Literal["advisor_brief"] = "advisor_brief"
    brief_id: str = Field(pattern=r"^advisor-[0-9a-f]{32}$")


EvidenceReference: TypeAlias = (
    CurrentObservationEvidence
    | ActionOutcomeEvidence
    | PlanOutcomeEvidence
    | MemoryEvidence
    | AdvisorBriefEvidence
)
"""Every identity a continuity operation may cite, and nothing else.

Each branch names an authority that already exists at the moment the operation
is processed. There is deliberately no free-text branch: a sentence claiming an
outcome is not the outcome.
"""




























class QuicksaveStatus(StrEnum):
    SAVED = "saved"
    NOT_OBSERVED = "not_observed"


def _validate_quicksave_completion(
    status: QuicksaveStatus,
    changed_files: int,
    quick_save_size_bytes: int | None,
) -> None:
    """Keep a terminal save verdict coupled to observable filesystem proof."""

    if status is QuicksaveStatus.SAVED and (changed_files < 1 or quick_save_size_bytes is None):
        raise ValueError("saved quicksave evidence requires a changed tree and nonempty quick.save")


class QuicksaveEvidence(StrictModel):
    """Controller-owned proof that F5 replaced the exact quicksave tree."""

    status: QuicksaveStatus
    slot: Literal["quicksave"] = "quicksave"
    changed_files: int = Field(ge=0)
    quick_save_size_bytes: int | None = Field(default=None, gt=0)
    quiescent_seconds: float = Field(ge=0.0)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def saved_requires_a_changed_nonempty_quick_save(self) -> QuicksaveEvidence:
        _validate_quicksave_completion(
            self.status,
            self.changed_files,
            self.quick_save_size_bytes,
        )
        return self


class SemanticActionReceipt(StrictModel):
    """Causal evidence for one reusable semantic action.

    Records what the action's arguments actually resolved to against observed
    state, so a receipt proves which real reference was acted on rather than
    only which arguments were requested.
    """

    action_kind: str = Field(min_length=1, max_length=80)
    contract_version: str = Field(min_length=1, max_length=32)
    target_id: str | None = Field(default=None, max_length=200)
    resolved_label: str | None = Field(default=None, max_length=500)
    resolved_role: str | None = Field(default=None, max_length=32)
    resolved_bounds: NormalizedPointerBounds | None = None
    source_revision: WorldStateRevision | None = None
    option_id: str | None = Field(default=None, max_length=128)
    revalidation: str = Field(min_length=1, max_length=1000)
    quicksave: QuicksaveEvidence | None = None
