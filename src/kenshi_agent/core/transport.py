"""Transport domain types."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import (
    Field,
    JsonValue,
    model_validator,
)

from .advisor import AdvisorConsultEvidence
from .authority import AuthorizationCode, InputBoundaryDecision
from .base import StrictModel
from .evidence import SemanticActionReceipt
from .observation import Observation
from .operation import (
    Action,
    ControlMode,
    PointerActionClass,
)
from .planning import ConditionEvaluation
from .telemetry import (
    ContextActionKind,
    NativeCommandAcknowledgement,
    NativeWireCommand,
    require_consistent_wire_shape,
)
from .world import WorldStateRevision


class CalibrationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class CalibrationIdentity(StrictModel):
    """Every fact a profile-calibrated pointer action depends on.

    Each field is nullable and a missing value stays missing. A null is never
    treated as a match, because an unobserved window mode or UI scale is not
    evidence that it is the expected one.
    """

    client_width: int | None = Field(default=None, gt=0)
    client_height: int | None = Field(default=None, gt=0)
    window_mode: str | None = Field(default=None, min_length=1, max_length=32)
    ui_scale: float | None = Field(default=None, gt=0.0, le=8.0)
    dpi_scale: float | None = Field(default=None, gt=0.0, le=8.0)
    keymap_id: str | None = Field(default=None, min_length=1, max_length=64)
    profile_id: str | None = Field(default=None, min_length=1, max_length=80)
    profile_version: int | None = Field(default=None, ge=1)
    macro_set_hash: str | None = Field(default=None, min_length=1, max_length=64)

    def declared_fields(self) -> tuple[str, ...]:
        """Names this identity actually asserts, in stable order."""

        return tuple(
            name for name in self.__class__.model_fields if getattr(self, name) is not None
        )


class CalibrationReport(StrictModel):
    status: CalibrationStatus
    action_class: PointerActionClass
    reason: str = Field(min_length=1, max_length=1000)
    expected: CalibrationIdentity | None = None
    observed: CalibrationIdentity | None = None
    mismatched_fields: list[str] = Field(default_factory=list, max_length=16)
    unobserved_fields: list[str] = Field(default_factory=list, max_length=16)


def new_command_id() -> str:
    return f"cmd-{uuid4().hex}"


class CommandDispatchContext(StrictModel):
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    based_on_revision: WorldStateRevision
    primitive_action_bound: int = Field(default=0, ge=0, le=100)


class NativeCommandRequest(StrictModel):
    schema_version: Literal["1.2"]
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    command: NativeWireCommand
    control_mode: Literal[ControlMode.NATIVE_ASSISTED]
    identity_session_id: str = Field(min_length=1, max_length=200)
    based_on_revision: WorldStateRevision
    selected_character_ids: list[str] = Field(min_length=1, max_length=64)
    # Empty for a directional walk, which references nobody.
    target_id: str = Field(default="", max_length=200)
    # Empty for every command except the generic context-action route.
    context_action: ContextActionKind | Literal[""] = ""
    bearing_degrees: float = Field(default=0.0, ge=0.0, lt=360.0)
    distance_units: float = Field(default=0.0, ge=0.0, le=2000.0)
    minimum_output_quantity: int = Field(default=1, ge=1, le=5)

    @model_validator(mode="after")
    def validate_native_fences(self) -> NativeCommandRequest:
        if self.based_on_revision.telemetry_sequence is None:
            raise ValueError("native command basis requires a telemetry sequence")
        if len(set(self.selected_character_ids)) != len(self.selected_character_ids):
            raise ValueError("native command selection basis contains duplicates")
        # Wire shape is classified once, in telemetry, for both directions of
        # the protocol. Recipient cardinality is not decided at either edge:
        # the operation registry owns recipient scope.
        require_consistent_wire_shape(
            command=self.command,
            subject="command",
            target_id=self.target_id,
            bearing_degrees=self.bearing_degrees,
            distance_units=self.distance_units,
            context_action=str(self.context_action),
            minimum_output_quantity=self.minimum_output_quantity,
        )
        return self


class InputBoundaryReport(StrictModel):
    """Evidence from the final fence that runs after the input lease is acquired.

    Validation performed before a polite input lease can become obsolete while
    the lease is pending, so a sensitive action revalidates its typed plan
    conditions against the latest canonical revision immediately before the
    first primitive is emitted.
    """

    decision: InputBoundaryDecision
    # The typed verdict behind `reason`. Records written before authorization
    # codes existed carry none, so this stays optional rather than defaulting to
    # a value that would reinterpret an older refusal as something it never said.
    code: AuthorizationCode | None = None
    reason: str = Field(min_length=1, max_length=1000)
    lease_wait_seconds: float = Field(default=0.0, ge=0.0)
    plan_id: str | None = Field(default=None, max_length=96)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, max_length=64)
    validated_revision: WorldStateRevision | None = None
    boundary_revision: WorldStateRevision | None = None
    evaluations: list[ConditionEvaluation] = Field(default_factory=list, max_length=24)


class ActionReceipt(StrictModel):
    action: Action
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY
    command_id: str | None = Field(
        default=None,
        pattern=r"^cmd-[0-9a-f]{32}$",
    )
    started_after_revision: WorldStateRevision | None = None
    completed_at_revision: WorldStateRevision | None = None
    causal_revision_advanced: bool | None = None
    native_acknowledgement: NativeCommandAcknowledgement | None = None
    input_boundary: InputBoundaryReport | None = None
    calibration: CalibrationReport | None = None
    semantic: SemanticActionReceipt | None = None
    advisor: AdvisorConsultEvidence | None = None
    accepted: bool
    executed: bool
    dry_run: bool
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    primitive_actions: int = Field(default=0, ge=0)
    message: str = ""
    error_type: str | None = None


class Transition(StrictModel):
    receipt: ActionReceipt
    observation: Observation
    terminated: bool = False
    success: bool | None = None
    events: list[str] = Field(default_factory=list)


class SessionEvent(StrictModel):
    event_type: str
    run_id: str
    step_index: int | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, JsonValue] = Field(default_factory=dict)
