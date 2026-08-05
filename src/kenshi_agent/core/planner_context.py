"""Planner Context domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import (
    ConfigDict,
    Field,
)

from .base import StrictModel
from .observation import Observation
from .planning import PlannerOutput
from .world import WorldStateRevision


class PlannerContextManifest(StrictModel):
    """Exact runtime-owned identities present in one final planner input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    run_id: str = Field(min_length=1, max_length=200)
    authored_revision: WorldStateRevision
    current_observation_delivered: bool
    telemetry_was_fresh: bool
    input_kind: Literal["full_observation", "budgeted_json", "scripted"]
    current_target_ids: list[str] = Field(default_factory=list, max_length=512)
    action_outcome_ids: list[str] = Field(default_factory=list, max_length=100)
    plan_outcome_ids: list[str] = Field(default_factory=list, max_length=8)
    memory_ids: list[str] = Field(default_factory=list, max_length=128)
    continuity_receipt_ids: list[str] = Field(default_factory=list, max_length=8)
    memory_read_receipt_ids: list[str] = Field(default_factory=list, max_length=8)
    fieldbook_project_ids: list[str] = Field(default_factory=list, max_length=32)
    fieldbook_entry_ids: list[str] = Field(default_factory=list, max_length=8)
    fieldbook_receipt_ids: list[str] = Field(default_factory=list, max_length=8)
    fieldbook_read_receipt_ids: list[str] = Field(default_factory=list, max_length=8)
    advisor_brief_ids: list[str] = Field(default_factory=list, max_length=8)
    # The menu the model actually chose from, and what was kept off it.
    #
    # A manifest that records only the chosen affordance cannot distinguish
    # "the model ignored a good option" from "the option was never offered" -
    # different problems with different fixes, and guessed wrong more than once
    # in a single session. Recorded as `semantic:operation_kind` rather than
    # affordance ids, because ids are per-sequence and say nothing months later.
    offered: list[str] = Field(default_factory=list, max_length=256)
    offered_count: int = Field(default=0, ge=0)
    # Operation kinds the registry currently declares unauthorable. This does
    # not explain every absence - an enumerator that returns early leaves no
    # trace - but it separates "refused by its own definition" from "never
    # enumerated", which was previously indistinguishable.
    withheld_unauthorable: list[str] = Field(default_factory=list, max_length=64)
    candidate_memory_count: int = Field(default=0, ge=0)
    payload_characters: int | None = Field(default=None, ge=0)
    context_capacity_source: str | None = None
    context_window_tokens: int | None = Field(default=None, ge=1)
    compaction_target_tokens: int | None = Field(default=None, ge=1)
    hard_observation_tokens: int | None = Field(default=None, ge=1)
    context_token_estimator: str | None = None
    reserved_output_tokens: int | None = Field(default=None, ge=0)
    reserved_static_tokens: int | None = Field(default=None, ge=0)
    reserved_image_tokens: int | None = Field(default=None, ge=0)
    proactive_headroom_tokens: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class AuthoredPlannerContext:
    """The immutable observation and manifest paired with one planner call."""

    manifest: PlannerContextManifest
    observation: Observation


@dataclass(frozen=True, slots=True)
class AuthoredPlannerOutput:
    """A parsed planner output inseparable from the context that authored it."""

    output: PlannerOutput
    context: AuthoredPlannerContext
