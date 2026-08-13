"""Affordance domain types."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from .base import StrictModel
from .world import WorldStateRevision


class AffordanceSource(StrEnum):
    RUNTIME = "runtime"
    GAME_BINDING = "game_binding"
    VISIBLE_CONTROL = "visible_control"
    CONTEXT_ORDER = "context_order"
    DIALOGUE = "dialogue"
    INVENTORY = "inventory"
    SQUAD = "squad"
    NEARBY_CHARACTER = "nearby_character"
    MAP = "map"
    NATIVE_OPERATION = "native_operation"
    COMPOSITE_OPERATION = "composite_operation"


class AffordanceExecution(StrEnum):
    IMMEDIATE = "immediate"
    MONITORED = "monitored"
    COMPOSITE = "composite"


class AffordanceLifecycleStatus(StrEnum):
    OFFERED = "offered"
    BOUND = "bound"
    EXECUTING = "executing"
    MONITORING = "monitoring"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    INTERRUPTED = "interrupted"


class AffordanceParameter(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    value: JsonValue


class AffordanceTarget(StrictModel):
    target_id: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=1, max_length=80)


class AffordanceSourceCompletenessStatus(StrEnum):
    """How much of one adapter's source denominator was knowable."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"
    UNKNOWN = "unknown"
    NOT_DELIVERED = "not_delivered"


class AffordanceWithheldCategory(StrEnum):
    """Why a category of choices was not in the delivered set.

    These are semantic evidence categories, never operation or native-command
    names.  They explain the limit without publishing an unoffered command
    inventory as if it were a planner surface.
    """

    MISSING_TELEMETRY = "missing_telemetry"
    STALE_TELEMETRY = "stale_telemetry"
    SOURCE_TRUNCATED = "source_truncated"
    SOURCE_UNKNOWN = "source_unknown"
    UNPROBED_TARGETS = "unprobed_targets"
    INVALID_SEMANTIC_VALUE = "invalid_semantic_value"
    NOT_AUTHORABLE = "not_authorable"
    NOT_BINDABLE = "not_bindable"
    INTERFACE_SCOPED = "interface_scoped"
    NOT_DELIVERED = "not_delivered"


class AffordanceSetParameter(StrictModel):
    """One planner-authorable parameter without presentation prose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    kind: Literal["integer", "number", "text", "choice"]
    required: bool
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def contract_is_coherent(self) -> AffordanceSetParameter:
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("affordance-set parameter minimum exceeds maximum")
        if len(self.choices) != len(set(self.choices)):
            raise ValueError("affordance-set parameter choices must be unique")
        if self.kind == "choice":
            if not self.choices:
                raise ValueError("choice parameter must declare at least one choice")
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("choice parameter cannot declare numeric bounds")
        elif self.choices:
            raise ValueError("only choice parameters may declare choices")
        if self.kind == "text" and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("text parameter cannot declare numeric bounds")
        return self


class AffordanceSetTarget(StrictModel):
    """One opaque semantic participant, never a label or mechanical address."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    target_id: str = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=1, max_length=80)


class AffordanceSetOffer(StrictModel):
    """Replay-safe evidence for one choice in the delivered menu."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    affordance_id: str = Field(pattern=r"^aff-[0-9a-f]{20}$")
    operation_kind: str = Field(min_length=1, max_length=80)
    source_adapter: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    semantic: str = Field(min_length=1, max_length=100)
    selection_target_id: str | None = Field(default=None, min_length=1, max_length=500)
    semantic_parameters: tuple[AffordanceSetParameter, ...] = Field(
        default=(),
        max_length=8,
    )
    applicable_targets: tuple[AffordanceSetTarget, ...] = Field(
        default=(),
        max_length=8,
    )
    target_id_required: bool

    @model_validator(mode="after")
    def semantic_fields_are_unambiguous(self) -> AffordanceSetOffer:
        parameter_names = [parameter.name for parameter in self.semantic_parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("affordance-set parameter names must be unique")
        target_roles = [target.role for target in self.applicable_targets]
        if len(target_roles) != len(set(target_roles)):
            raise ValueError("affordance-set target roles must be unique")
        if self.target_id_required != (self.selection_target_id is not None):
            raise ValueError(
                "affordance-set selection target must exactly follow target_id_required"
            )
        return self


class AffordanceSourceCompleteness(StrictModel):
    """Typed completeness and withholding facts for one source adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_adapter: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    status: AffordanceSourceCompletenessStatus
    withheld_categories: tuple[AffordanceWithheldCategory, ...] = Field(
        default=(),
        max_length=16,
    )

    @model_validator(mode="after")
    def categories_are_unique(self) -> AffordanceSourceCompleteness:
        if len(self.withheld_categories) != len(set(self.withheld_categories)):
            raise ValueError("affordance source withheld categories must be unique")
        if list(self.withheld_categories) != sorted(self.withheld_categories):
            raise ValueError("affordance source withheld categories must be sorted")
        categories = set(self.withheld_categories)
        incomplete = {
            AffordanceWithheldCategory.MISSING_TELEMETRY,
            AffordanceWithheldCategory.STALE_TELEMETRY,
            AffordanceWithheldCategory.SOURCE_TRUNCATED,
            AffordanceWithheldCategory.SOURCE_UNKNOWN,
            AffordanceWithheldCategory.UNPROBED_TARGETS,
            AffordanceWithheldCategory.NOT_DELIVERED,
        }
        if self.status is AffordanceSourceCompletenessStatus.COMPLETE:
            if categories & incomplete:
                raise ValueError("complete affordance source carries incomplete evidence")
        elif self.status is AffordanceSourceCompletenessStatus.TRUNCATED:
            if AffordanceWithheldCategory.SOURCE_TRUNCATED not in categories:
                raise ValueError("truncated affordance source lacks truncation evidence")
        elif self.status is AffordanceSourceCompletenessStatus.UNKNOWN:
            unknown_evidence = {
                AffordanceWithheldCategory.MISSING_TELEMETRY,
                AffordanceWithheldCategory.STALE_TELEMETRY,
                AffordanceWithheldCategory.SOURCE_UNKNOWN,
            }
            if not categories & unknown_evidence:
                raise ValueError("unknown affordance source lacks unknown evidence")
        elif categories != {AffordanceWithheldCategory.NOT_DELIVERED}:
            raise ValueError("not-delivered affordance source has contradictory evidence")
        return self


class AffordanceSetEvent(StrictModel):
    """The exact semantic choices delivered for one authored planner context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    identity_session_id: str | None = Field(default=None, max_length=200)
    offers: tuple[AffordanceSetOffer, ...] = Field(default=(), max_length=256)
    source_completeness: tuple[AffordanceSourceCompleteness, ...] = Field(
        min_length=1,
        max_length=32,
    )
    withheld_categories: tuple[AffordanceWithheldCategory, ...] = Field(
        default=(),
        max_length=16,
    )

    @model_validator(mode="after")
    def evidence_is_unique_and_canonical(self) -> AffordanceSetEvent:
        offer_ids = [offer.affordance_id for offer in self.offers]
        if len(offer_ids) != len(set(offer_ids)):
            raise ValueError("affordance-set offer identities must be unique")
        if offer_ids != sorted(offer_ids):
            raise ValueError("affordance-set offers must be sorted by identity")
        selectors = [
            (offer.semantic, offer.selection_target_id) for offer in self.offers
        ]
        if len(selectors) != len(set(selectors)):
            raise ValueError("affordance-set planner selectors must be unique")
        adapters = [source.source_adapter for source in self.source_completeness]
        if len(adapters) != len(set(adapters)):
            raise ValueError("affordance-set source adapters must be unique")
        if adapters != sorted(adapters):
            raise ValueError("affordance-set source adapters must be sorted")
        if len(self.withheld_categories) != len(set(self.withheld_categories)):
            raise ValueError("affordance-set withheld categories must be unique")
        if list(self.withheld_categories) != sorted(self.withheld_categories):
            raise ValueError("affordance-set withheld categories must be sorted")
        source_categories = {
            category
            for source in self.source_completeness
            for category in source.withheld_categories
        }
        if set(self.withheld_categories) != source_categories:
            raise ValueError(
                "affordance-set withheld categories must equal source evidence"
            )
        unavailable = {
            AffordanceWithheldCategory.MISSING_TELEMETRY,
            AffordanceWithheldCategory.STALE_TELEMETRY,
            AffordanceWithheldCategory.NOT_DELIVERED,
        }
        if set(self.withheld_categories) & unavailable and self.offers:
            raise ValueError("unavailable affordance set cannot contain offers")
        return self


class BoundAffordance(StrictModel):
    """Runtime provenance retained after an exact affordance selection is compiled."""

    affordance_id: str = Field(pattern=r"^aff-[0-9a-f]{20}$")
    source: AffordanceSource
    semantic: str = Field(min_length=1, max_length=100)
    target: AffordanceTarget | None = None
    parameters: list[AffordanceParameter] = Field(default_factory=list, max_length=8)
    execution: AffordanceExecution
    operation_kind: str = Field(min_length=1, max_length=80)
    offered_at_telemetry_sequence: int = Field(ge=0)


class AffordanceLifecycleEvent(StrictModel):
    status: AffordanceLifecycleStatus
    telemetry_sequence: int | None = Field(default=None, ge=0)
    detail: str = Field(min_length=1, max_length=1000)


class AffordanceReceipt(StrictModel):
    """Common terminal evidence emitted for every selected affordance."""

    affordance: BoundAffordance
    status: AffordanceLifecycleStatus
    lifecycle: list[AffordanceLifecycleEvent] = Field(min_length=3, max_length=5)
    message: str = Field(min_length=1, max_length=2000)

    def model_post_init(self, __context: Any) -> None:
        terminal = {
            AffordanceLifecycleStatus.SUCCEEDED,
            AffordanceLifecycleStatus.FAILED,
            AffordanceLifecycleStatus.REJECTED,
            AffordanceLifecycleStatus.INTERRUPTED,
        }
        if self.status not in terminal:
            raise ValueError("AffordanceReceipt status must be terminal")
        if self.lifecycle[-1].status is not self.status:
            raise ValueError("AffordanceReceipt lifecycle must end at its terminal status")
        prefixes = {
            (
                AffordanceLifecycleStatus.OFFERED,
                AffordanceLifecycleStatus.BOUND,
            ),
            (
                AffordanceLifecycleStatus.OFFERED,
                AffordanceLifecycleStatus.BOUND,
                AffordanceLifecycleStatus.EXECUTING,
            ),
            (
                AffordanceLifecycleStatus.OFFERED,
                AffordanceLifecycleStatus.BOUND,
                AffordanceLifecycleStatus.EXECUTING,
                AffordanceLifecycleStatus.MONITORING,
            ),
        }
        if tuple(event.status for event in self.lifecycle[:-1]) not in prefixes:
            raise ValueError("AffordanceReceipt lifecycle contains a phase that did not occur")
