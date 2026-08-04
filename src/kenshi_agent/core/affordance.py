"""Affordance domain types."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import (
    Field,
    JsonValue,
)

from .base import StrictModel


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
