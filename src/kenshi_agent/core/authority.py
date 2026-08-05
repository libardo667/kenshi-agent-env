"""Typed operation and input-boundary authorization verdicts."""

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
    # The operation is still runnable, but for different characters than it
    # was authored for. Distinct from STALE_BOUND_IDENTITY so a post-mortem
    # can tell "the world moved under this plan" from "this order would have
    # been delivered to somebody else".
    STALE_RECIPIENT_BASIS = "stale_recipient_basis"
    PRECONDITION_UNTRUE = "precondition_untrue"
    FAILURE_CONDITION_ACTIVE = "failure_condition_active"
