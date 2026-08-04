"""One typed vocabulary for why host input authority is granted or withdrawn.

Plan-time validation and the final check inside the input lease answer the same
question at two moments. Naming each verdict once lets a refusal be counted and
compared across runs, and lets the wording live at the edge that renders it
instead of every check inventing its own sentence for the same condition.

This is deliberately a leaf module: it imports nothing from the package, so the
model vocabulary can depend on it without a cycle.
"""

from __future__ import annotations

from enum import StrEnum


class InputBoundaryDecision(StrEnum):
    """Outcome of the final revalidation performed inside the acquired input lease."""

    NOT_REQUIRED = "not_required"
    REVALIDATED = "revalidated"
    REJECTED = "rejected"


class AuthorizationCode(StrEnum):
    """Why an operation may or may not emit host input right now."""

    ALLOWED = "allowed"
    CALIBRATION_DRIFTED = "calibration_drifted"
    OBSERVATION_UNAVAILABLE = "observation_unavailable"
    TELEMETRY_STALE = "telemetry_stale"
    TELEMETRY_AGE_CEILING_UNKNOWN = "telemetry_age_ceiling_unknown"
    TELEMETRY_AGE_UNKNOWN = "telemetry_age_unknown"
    TELEMETRY_TOO_OLD = "telemetry_too_old"
    REVISION_REGRESSED = "revision_regressed"
    CONTROL_MODE_CHANGED = "control_mode_changed"
    INPUT_AUTHORITY_WITHDRAWN = "input_authority_withdrawn"
    OPERATION_UNAUTHORIZED = "operation_unauthorized"
    OPERATION_IDENTITY_CHANGED = "operation_identity_changed"
    BINDING_ABSENT = "binding_absent"
    BINDING_AMBIGUOUS = "binding_ambiguous"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    SELECTION_INVALID = "selection_invalid"
    POLICY_DISALLOWED = "policy_disallowed"
    TRANSACTION_BUDGET_UNAVAILABLE = "transaction_budget_unavailable"
    STALE_BOUND_IDENTITY = "stale_bound_identity"
    PRECONDITION_UNTRUE = "precondition_untrue"
    FAILURE_CONDITION_ACTIVE = "failure_condition_active"
