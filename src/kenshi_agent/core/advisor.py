"""Advisor domain types."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import (
    Field,
)

from .base import StrictModel
from .operation import AdvisorFocus
from .world import WorldStateRevision


class AdvisorConsultStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    DISABLED = "disabled"
    COOLDOWN = "cooldown"
    UNCHANGED_STATE = "unchanged_state"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class AdvisorAttribution(StrictModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    title: str = Field(min_length=1, max_length=300)
    creator: str | None = Field(default=None, max_length=200)
    url: str = Field(min_length=1, max_length=1000)


class AdvisorRecommendation(StrictModel):
    rank: int = Field(ge=1, le=5)
    goal: str = Field(min_length=1, max_length=500)
    why_now: str = Field(min_length=1, max_length=800)
    prerequisites: list[str] = Field(default_factory=list, max_length=6)
    cautions: list[str] = Field(default_factory=list, max_length=6)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class AdvisorBrief(StrictModel):
    brief_id: str = Field(pattern=r"^advisor-[0-9a-f]{32}$")
    question: str = Field(min_length=1, max_length=600)
    focus: AdvisorFocus
    based_on_revision: WorldStateRevision
    summary: str = Field(min_length=1, max_length=1200)
    recommendations: list[AdvisorRecommendation] = Field(min_length=1, max_length=4)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
    sources: list[AdvisorAttribution] = Field(min_length=1, max_length=12)
    corpus_version: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=200)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AdvisorAvailability(StrictModel):
    enabled: bool = False
    may_request: bool = False
    suggested: bool = False
    request_pending: bool = False
    reason: str = Field(default="The strategic advisor is disabled.", max_length=600)
    calls_used: int = Field(default=0, ge=0)
    max_calls: int = Field(default=0, ge=0)
    cooldown_steps_remaining: int = Field(default=0, ge=0)
    corpus_version: str | None = Field(default=None, max_length=80)
    latest_brief: AdvisorBrief | None = None


class AdvisorConsultEvidence(StrictModel):
    status: AdvisorConsultStatus
    reason: str = Field(min_length=1, max_length=1000)
    calls_used: int = Field(ge=0)
    max_calls: int = Field(ge=0)
    state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief: AdvisorBrief | None = None
