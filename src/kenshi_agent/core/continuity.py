"""Continuity domain types."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, TypeAlias
from uuid import uuid4

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .base import StrictModel
from .evidence import (
    MEMORY_ID_PATTERN,
    PLAN_ID_PATTERN,
    STEP_ID_PATTERN,
    ActionOutcomeAssessment,
    ActionOutcomeDigest,
    EvidenceReference,
    PlanDisposition,
    PlanOutcomeDigest,
)
from .operation import (
    FIELD_BOOK_ENTRY_ID_PATTERN,
    FIELD_BOOK_PROJECT_ID_PATTERN,
)
from .world import WorldStateRevision


class MemoryKind(StrEnum):
    FACT = "fact"
    EPISODE = "episode"
    COMMITMENT = "commitment"
    HYPOTHESIS = "hypothesis"


def new_memory_id() -> str:
    """A runtime-owned durable identity, stable across processes.

    Not the SQLite rowid: a memory ID is cited by planners and quoted in
    receipts, so it must survive a projection rebuild that renumbers rows.
    """

    return f"mem-{uuid4().hex}"


def new_continuity_receipt_id() -> str:
    """A runtime-owned identity for one attempted continuity operation."""

    return f"cor-{uuid4().hex}"


def new_memory_read_receipt_id() -> str:
    """A runtime-owned identity for one elective continuity read."""

    return f"mrr-{uuid4().hex}"


def new_memory_compaction_candidate_id() -> str:
    """A runtime-owned identity for one inspectable compaction proposal."""

    return f"mcc-{uuid4().hex}"


def new_fieldbook_project_id() -> str:
    return f"fbp-{uuid4().hex}"


def new_fieldbook_entry_id() -> str:
    return f"fbe-{uuid4().hex}"


def new_fieldbook_operation_receipt_id() -> str:
    return f"fbor-{uuid4().hex}"


def new_fieldbook_read_receipt_id() -> str:
    return f"fbr-{uuid4().hex}"


class MemoryResolutionDisposition(StrEnum):
    """What resolving an intention or uncertainty actually concluded."""

    COMPLETED = "completed"
    ABANDONED = "abandoned"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class KeepMemoryOperation(StrictModel):
    """Create one durable record from something already established.

    `evidence` is absent on purpose. The stored grounding string is rendered by
    the runtime from `references` after each one resolves, so a record can
    never describe proof it does not have.
    """

    operation: Literal["keep"] = "keep"
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=2000)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    # Opaque identity copied exactly from the current observation. Display
    # names are intentionally insufficient: two Barmen may share one, and a
    # later identity session may reuse the same role for another character.
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)


class ReinforceMemoryOperation(StrictModel):
    """Say an existing record still matters, without writing a second copy."""

    operation: Literal["reinforce"] = "reinforce"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    salience: float | None = Field(default=None, ge=0.0, le=1.0)
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)


class ResolveMemoryOperation(StrictModel):
    """Close an open commitment or question with the evidence that closed it."""

    operation: Literal["resolve"] = "resolve"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    reason: str = Field(min_length=1, max_length=1000)
    disposition: MemoryResolutionDisposition | None = None
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)


class SupersedeMemoryOperation(StrictModel):
    """Replace a record and link the old one to its replacement, atomically."""

    operation: Literal["supersede"] = "supersede"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=2000)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)


class RetractMemoryOperation(StrictModel):
    """Withdraw a record from active recall without deleting its history."""

    operation: Literal["retract"] = "retract"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    reason: str = Field(min_length=1, max_length=1000)


ContinuityOperation: TypeAlias = (
    KeepMemoryOperation
    | ReinforceMemoryOperation
    | ResolveMemoryOperation
    | SupersedeMemoryOperation
    | RetractMemoryOperation
)
"""Every explicit transition a planner may ask for, and nothing else.

There is no edit and no delete. A belief that turns out to be wrong is
superseded or retracted, both of which leave the original readable.
"""


class ContinuityOrigin(StrEnum):
    DECISION = "decision"
    PLAN = "plan"
    PATCH = "patch"


class ContinuityOperationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NO_OP = "no_op"
    FAILED = "failed"


class FieldbookProjectKind(StrEnum):
    DELIVERY_DOCKET = "delivery_docket"
    ROUTE_ATLAS = "route_atlas"
    INCIDENT_LOG = "incident_log"
    VENDOR_LEDGER = "vendor_ledger"
    EQUIPMENT_PLAN = "equipment_plan"
    JOURNAL = "journal"
    GENERIC = "generic"


class FieldbookProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class FieldbookEntryKind(StrEnum):
    NOTE = "note"
    DECISION = "decision"
    OBSERVATION = "observation"
    INCIDENT = "incident"
    MANIFEST = "manifest"
    ROUTE_ENTRY = "route_entry"
    EXPENSE = "expense"
    QUESTION = "question"


class CreateFieldbookProjectOperation(StrictModel):
    operation: Literal["create_project"] = "create_project"
    kind: FieldbookProjectKind
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("title", "summary")
    @classmethod
    def normalize_nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fieldbook text must not be blank")
        return normalized


class AppendFieldbookEntryOperation(StrictModel):
    operation: Literal["append_entry"] = "append_entry"
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    kind: FieldbookEntryKind
    content: str = Field(min_length=1, max_length=2000)
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)

    @field_validator("content")
    @classmethod
    def normalize_nonblank_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fieldbook entry content must not be blank")
        return normalized


class UpdateFieldbookSummaryOperation(StrictModel):
    operation: Literal["update_summary"] = "update_summary"
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("summary")
    @classmethod
    def normalize_nonblank_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fieldbook summary must not be blank")
        return normalized


class SelectFieldbookProjectOperation(StrictModel):
    operation: Literal["select_project"] = "select_project"
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )


class SetFieldbookProjectStatusOperation(StrictModel):
    operation: Literal["set_project_status"] = "set_project_status"
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    status: FieldbookProjectStatus


FieldbookOperation: TypeAlias = (
    CreateFieldbookProjectOperation
    | AppendFieldbookEntryOperation
    | UpdateFieldbookSummaryOperation
    | SelectFieldbookProjectOperation
    | SetFieldbookProjectStatusOperation
)


class MemoryStatus(StrEnum):
    """Where a record sits in its lifecycle. Only `active` reaches recall."""

    ACTIVE = "active"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class EvidenceAuthority(StrEnum):
    """What one resolved identity is actually capable of establishing."""

    FRESH_WORLD_OBSERVATION = "fresh_world_observation"
    VERIFIED_WORLD_EFFECT = "verified_world_effect"
    OBSERVED_CHANGE = "observed_change"
    ATTEMPT_CHANGED = "attempt_changed"
    ATTEMPT_NO_OP = "attempt_no_op"
    ATTEMPT_NOT_EXECUTED = "attempt_not_executed"
    ATTEMPT_UNKNOWN = "attempt_unknown"
    PLAN_DISPOSITION = "plan_disposition"
    AGENT_BELIEF = "agent_belief"
    ADVICE = "advice"
    SCENARIO_ATTESTATION = "scenario_attestation"


class ResolvedEvidenceSnapshot(StrictModel):
    """Typed immutable truth retained after a reference leaves planner context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal[
        "current_observation",
        "action_outcome",
        "plan_outcome",
        "memory",
        "advisor_brief",
    ]
    source_id: str = Field(min_length=1, max_length=200)
    authority: EvidenceAuthority
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    run_id: str = Field(min_length=1, max_length=200)
    world_revision: WorldStateRevision | None = None
    assessment: ActionOutcomeAssessment | None = None
    action_kind: str | None = Field(default=None, max_length=80)
    executed: bool | None = None
    causal_revision_advanced: bool | None = None
    controller_verified: bool | None = None
    semantic_status: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=200)
    plan_disposition: PlanDisposition | None = None
    memory_kind: MemoryKind | None = None
    memory_status: MemoryStatus | None = None
    compact_summary: str = Field(min_length=1, max_length=500)


class CanonicalMemoryProvenance(StrictModel):
    """The exact accepted lifecycle operation and the authority behind it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation: ContinuityOperation
    origin: ContinuityOrigin
    run_id: str = Field(min_length=1, max_length=200)
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)
    resolved_evidence: list[ResolvedEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=4,
    )
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    rendered_grounding: str | None = Field(default=None, max_length=1000)
    transition_result: Literal["applied"] = "applied"


class CanonicalFieldbookProvenance(StrictModel):
    """Exact planner context and resolved sources behind a fieldbook change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation: FieldbookOperation
    origin: ContinuityOrigin
    run_id: str = Field(min_length=1, max_length=200)
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)
    resolved_evidence: list[ResolvedEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=4,
    )
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    rendered_grounding: str | None = Field(default=None, max_length=1000)
    transition_result: Literal["applied"] = "applied"


class FieldbookLifecycleEvent(StrEnum):
    CREATE_PROJECT = "create_project"
    APPEND_ENTRY = "append_entry"
    UPDATE_SUMMARY = "update_summary"
    SELECT_PROJECT = "select_project"
    CLEAR_SELECTION = "clear_selection"
    SET_PROJECT_STATUS = "set_project_status"


class FieldbookProject(StrictModel):
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    campaign_id: str = Field(min_length=1, max_length=80)
    kind: FieldbookProjectKind
    status: FieldbookProjectStatus
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1000)
    selected: bool = False
    entry_count: int = Field(default=0, ge=0)
    created_run_id: str = Field(min_length=1, max_length=200)
    created_at: datetime
    updated_at: datetime
    latest_provenance: CanonicalFieldbookProvenance | None = None


class FieldbookProjectIndex(StrictModel):
    """Bounded metadata automatically shown without full project entries."""

    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    kind: FieldbookProjectKind
    status: FieldbookProjectStatus
    short_summary: str = Field(min_length=1, max_length=160)
    entry_count: int = Field(ge=0)
    updated_at: datetime
    selected: bool


class ActiveFieldbookProject(StrictModel):
    """The one explicitly selected project allowed a fuller automatic summary."""

    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    kind: FieldbookProjectKind
    status: Literal[FieldbookProjectStatus.ACTIVE]
    summary: str = Field(min_length=1, max_length=1000)
    entry_count: int = Field(ge=0)
    updated_at: datetime


class FieldbookEntry(StrictModel):
    entry_id: str = Field(pattern=FIELD_BOOK_ENTRY_ID_PATTERN)
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    campaign_id: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=1)
    kind: FieldbookEntryKind
    content: str = Field(min_length=1, max_length=2000)
    created_run_id: str = Field(min_length=1, max_length=200)
    created_at: datetime
    provenance: CanonicalFieldbookProvenance | None = None


class FieldbookHistoryEntry(StrictModel):
    event_id: int = Field(ge=1)
    campaign_id: str
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    entry_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_ENTRY_ID_PATTERN,
    )
    event: FieldbookLifecycleEvent
    run_id: str
    recorded_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class FieldbookReadResult(StrictModel):
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )
    query: str | None = Field(default=None, min_length=1, max_length=200)
    project: FieldbookProject | None = None
    entries: list[FieldbookEntry] = Field(default_factory=list, max_length=8)
    matched: int = Field(default=0, ge=0)
    truncated: bool = False
    reason: str = Field(default="", max_length=600)


class FieldbookReadStatus(StrEnum):
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class FieldbookReadReceipt(FieldbookReadResult):
    receipt_id: str = Field(pattern=r"^fbr-[0-9a-f]{32}$")
    status: FieldbookReadStatus
    campaign_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,79}$",
    )
    project_ids: list[str] = Field(default_factory=list, max_length=8)
    entry_ids: list[str] = Field(default_factory=list, max_length=8)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def result_ids_and_status_match(self) -> FieldbookReadReceipt:
        expected_projects = sorted(
            {
                *([self.project.project_id] if self.project is not None else []),
                *(entry.project_id for entry in self.entries),
            }
        )
        if self.project_ids != expected_projects:
            raise ValueError("project_ids must exactly match returned fieldbook data")
        expected_entries = [entry.entry_id for entry in self.entries]
        if self.entry_ids != expected_entries:
            raise ValueError("entry_ids must exactly match returned fieldbook entries")
        unavailable_is_valid = (
            self.status is FieldbookReadStatus.UNAVAILABLE and self.campaign_id is None
        )
        available_is_valid = (
            self.status in {FieldbookReadStatus.COMPLETED, FieldbookReadStatus.FAILED}
            and self.campaign_id is not None
        )
        if not (unavailable_is_valid or available_is_valid):
            raise ValueError("status and campaign_id describe an impossible fieldbook read")
        return self


class FieldbookReceiptDigest(StrictModel):
    receipt_id: str = Field(pattern=r"^fbor-[0-9a-f]{32}$")
    origin: ContinuityOrigin
    operation: Literal[
        "create_project",
        "append_entry",
        "update_summary",
        "select_project",
        "set_project_status",
    ]
    status: ContinuityOperationStatus
    reason: str = Field(min_length=1, max_length=1000)
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )
    entry_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_ENTRY_ID_PATTERN,
    )
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    writes_degraded: bool = False
    recorded_at: datetime


class FieldbookOperationReceipt(StrictModel):
    receipt_id: str = Field(pattern=r"^fbor-[0-9a-f]{32}$")
    origin: ContinuityOrigin
    status: ContinuityOperationStatus
    operation: FieldbookOperation
    reason: str = Field(min_length=1, max_length=1000)
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )
    entry_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_ENTRY_ID_PATTERN,
    )
    resolved_evidence: list[ResolvedEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=4,
    )
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    writes_degraded: bool = False
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def digest(self) -> FieldbookReceiptDigest:
        return FieldbookReceiptDigest(
            receipt_id=self.receipt_id,
            origin=self.origin,
            operation=self.operation.operation,
            status=self.status,
            reason=self.reason,
            project_id=self.project_id,
            entry_id=self.entry_id,
            authored_context_id=self.authored_context_id,
            authored_revision=self.authored_revision,
            commit_revision=self.commit_revision,
            plan_id=self.plan_id,
            plan_version=self.plan_version,
            step_id=self.step_id,
            writes_degraded=self.writes_degraded,
            recorded_at=self.recorded_at,
        )


class ContinuityReceiptDigest(StrictModel):
    """Bounded planner feedback for one full continuity operation receipt."""

    receipt_id: str = Field(pattern=r"^cor-[0-9a-f]{32}$")
    origin: ContinuityOrigin
    operation: Literal["keep", "reinforce", "resolve", "supersede", "retract"]
    status: ContinuityOperationStatus
    reason: str = Field(min_length=1, max_length=1000)
    memory_id: str | None = Field(default=None, pattern=MEMORY_ID_PATTERN)
    memory_status: MemoryStatus | None = None
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    evidence_summary: str | None = Field(default=None, max_length=500)
    writes_degraded: bool = False
    recorded_at: datetime


class ContinuityOperationReceipt(StrictModel):
    receipt_id: str = Field(pattern=r"^cor-[0-9a-f]{32}$")
    origin: ContinuityOrigin
    status: ContinuityOperationStatus
    operation: ContinuityOperation
    reason: str = Field(min_length=1, max_length=1000)
    memory_id: str | None = Field(default=None, pattern=MEMORY_ID_PATTERN)
    memory_status: MemoryStatus | None = None
    evidence: str | None = Field(default=None, max_length=1000)
    resolved_evidence: list[ResolvedEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=4,
    )
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    writes_degraded: bool = False
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def digest(self) -> ContinuityReceiptDigest:
        return ContinuityReceiptDigest(
            receipt_id=self.receipt_id,
            origin=self.origin,
            operation=self.operation.operation,
            status=self.status,
            reason=self.reason,
            memory_id=self.memory_id,
            memory_status=self.memory_status,
            authored_context_id=self.authored_context_id,
            authored_revision=self.authored_revision,
            commit_revision=self.commit_revision,
            plan_id=self.plan_id,
            plan_version=self.plan_version,
            step_id=self.step_id,
            evidence_summary=(None if self.evidence is None else self.evidence[:500]),
            writes_degraded=self.writes_degraded,
            recorded_at=self.recorded_at,
        )


class MemoryAuthorship(StrEnum):
    """Who stands behind a record, and how much that is worth.

    `legacy_unverified` marks rows written before continuity had grounding at
    all. They are kept because they are real user data, not because anything
    checked them.
    """

    AGENT_AUTHORED = "agent_authored"
    LEGACY_UNVERIFIED = "legacy_unverified"


class CompactionMethod(StrEnum):
    """Implemented compaction treatments.

    Semantic rewriting is deliberately absent until it can satisfy the same
    source-conservation and atomic-application contract as the lossless path.
    """

    LOSSLESS = "lossless"


class MemoryCompactionGenerator(StrictModel):
    """How one candidate was produced, including honest non-use of a prompt."""

    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=120)
    prompt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=16)


def validate_compaction_source_identity(
    source_memory_ids: list[str],
    source_fingerprints: dict[str, str],
) -> None:
    """Reject a candidate whose bounded source identity is not exact."""

    if source_memory_ids != sorted(set(source_memory_ids)):
        raise ValueError("compaction source_memory_ids must be unique and sorted")
    if set(source_fingerprints) != set(source_memory_ids):
        raise ValueError("compaction fingerprints must exactly match source IDs")
    if any(
        len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
        for fingerprint in source_fingerprints.values()
    ):
        raise ValueError("compaction fingerprints must be lowercase SHA-256")


class MemoryCompactionCandidate(StrictModel):
    """A bounded proposal that has no authority until atomically applied."""

    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=r"^mcc-[0-9a-f]{32}$")
    method: CompactionMethod
    campaign_id: str = Field(min_length=1, max_length=80)
    source_memory_ids: list[str] = Field(min_length=2, max_length=8)
    source_fingerprints: dict[str, str] = Field(min_length=2, max_length=8)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=2000)
    salience: float = Field(ge=0.0, le=1.0)
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    authorship: MemoryAuthorship
    generator: MemoryCompactionGenerator
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def model_post_init(self, __context: object) -> None:
        validate_compaction_source_identity(
            self.source_memory_ids,
            self.source_fingerprints,
        )


class CanonicalCompactionProvenance(StrictModel):
    """Exact immutable candidate and application identity behind a replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    provenance_kind: Literal["compaction"] = "compaction"
    candidate: MemoryCompactionCandidate
    applied_run_id: str = Field(min_length=1, max_length=200)
    replacement_memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    applied_at: datetime
    transition_result: Literal["applied"] = "applied"


MemoryProvenance: TypeAlias = CanonicalMemoryProvenance | CanonicalCompactionProvenance


class MemoryLifecycleEvent(StrEnum):
    KEEP = "keep"
    REINFORCE = "reinforce"
    RESOLVE = "resolve"
    SUPERSEDE = "supersede"
    RETRACT = "retract"
    DELIVER = "deliver"


class MemoryRecord(StrictModel):
    """One durable record, projected from its lifecycle history."""

    memory_id: str = Field(min_length=1, max_length=80)
    campaign_id: str = Field(min_length=1, max_length=80)
    kind: MemoryKind
    status: MemoryStatus
    content: str
    salience: float
    # Runtime-rendered from the references that resolved. Never model-authored.
    grounding: str | None = None
    # Exact accepted operation and typed source snapshots behind the latest
    # grounding-bearing transition. Older provenance remains in event history.
    latest_provenance: MemoryProvenance | None = None
    authorship: MemoryAuthorship = MemoryAuthorship.AGENT_AUTHORED
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    created_run_id: str
    created_at: datetime
    # Four separate concepts, deliberately not one "touched at". Being read is
    # not being reinforced, and being reinforced is not being resolved.
    reinforced_at: datetime | None = None
    resolved_at: datetime | None = None
    superseded_at: datetime | None = None
    last_delivered_at: datetime | None = None
    reinforcement_count: int = Field(default=0, ge=0)
    supersedes_id: str | None = Field(default=None, min_length=1, max_length=80)
    superseded_by_id: str | None = Field(default=None, min_length=1, max_length=80)
    resolution_reason: str | None = Field(default=None, max_length=1000)
    resolution_disposition: MemoryResolutionDisposition | None = None


class RecallTier(StrEnum):
    """Why a record was chosen, in the order the tiers are spent.

    The order is the policy: a plan cannot safely proceed without its open
    commitments or what it knows about the entity in front of it, so those are
    not allowed to compete with general knowledge for the same slots.
    """

    COMMITMENT = "commitment"
    CURRENT_TARGET = "current_target"
    OPEN_HYPOTHESIS = "open_hypothesis"
    GENERAL = "general"


class MemoryRetrievalPolicy(StrEnum):
    """Canonical-memory retrieval treatments implemented by this build."""

    DETERMINISTIC = "deterministic"


class RecallSummary(StrictModel):
    """What automatic recall left out, stated rather than implied.

    A planner that cannot tell "nothing else exists" from "more exists, not
    shown" will conclude the first and stop looking.
    """

    omitted: dict[RecallTier, int] = Field(default_factory=dict)
    total_omitted: int = Field(default=0, ge=0)

    @property
    def complete(self) -> bool:
        return self.total_omitted == 0


class MemorySearchResult(StrictModel):
    """The typed answer to one deliberate, bounded continuity read."""

    query: str = Field(min_length=1, max_length=200)
    records: list[MemoryRecord] = Field(default_factory=list, max_length=16)
    action_outcomes: list[ActionOutcomeDigest] = Field(
        default_factory=list,
        max_length=8,
    )
    plan_outcomes: list[PlanOutcomeDigest] = Field(
        default_factory=list,
        max_length=8,
    )
    matched: int = Field(default=0, ge=0)
    truncated: bool = False
    reason: str = Field(default="", max_length=600)


class MemoryReadStatus(StrEnum):
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class MemoryReadReceipt(MemorySearchResult):
    """Runtime identity and provenance for one planner-requested memory read."""

    receipt_id: str = Field(pattern=r"^mrr-[0-9a-f]{32}$")
    source: Literal["durable_memory", "working_outcomes"]
    status: MemoryReadStatus
    campaign_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,79}$",
    )
    record_ids: list[str] = Field(default_factory=list, max_length=8)
    action_outcome_ids: list[str] = Field(default_factory=list, max_length=8)
    plan_outcome_ids: list[str] = Field(default_factory=list, max_length=8)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def result_ids_match_returned_records(self) -> MemoryReadReceipt:
        expected_record_ids = [record.memory_id for record in self.records]
        if self.record_ids != expected_record_ids:
            raise ValueError("record_ids must exactly match returned records")
        expected_action_ids = [outcome.outcome_id for outcome in self.action_outcomes]
        if self.action_outcome_ids != expected_action_ids:
            raise ValueError("action_outcome_ids must exactly match returned action outcomes")
        expected_plan_ids = [outcome.plan_outcome_id for outcome in self.plan_outcomes]
        if self.plan_outcome_ids != expected_plan_ids:
            raise ValueError("plan_outcome_ids must exactly match returned plan outcomes")
        working_scope_is_valid = (
            self.source == "working_outcomes"
            and self.status is MemoryReadStatus.COMPLETED
            and self.campaign_id is None
        )
        durable_scope_is_valid = self.source == "durable_memory" and (
            (self.status is MemoryReadStatus.UNAVAILABLE and self.campaign_id is None)
            or (
                self.status in {MemoryReadStatus.COMPLETED, MemoryReadStatus.FAILED}
                and self.campaign_id is not None
            )
        )
        if not (working_scope_is_valid or durable_scope_is_valid):
            raise ValueError("source, status, and campaign_id describe an impossible read")
        return self


class MemoryHistoryEntry(StrictModel):
    """One append-only lifecycle event. Never rewritten, never deleted."""

    event_id: int
    campaign_id: str
    memory_id: str
    event: MemoryLifecycleEvent
    run_id: str
    recorded_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
