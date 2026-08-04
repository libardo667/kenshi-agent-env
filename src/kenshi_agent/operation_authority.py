"""One cross-cutting authority for whether an operation may act right now.

The same question is asked twice: once before an operation is scheduled, and
again inside the acquired input lease immediately before the first host or
native primitive. A polite lease can wait an unbounded interval, so the second
answer must come from fresh state - but it must come from the *same* policy, and
it must provably concern the *same* operation.

This owns that. Domain prerequisites stay on the operation definition; budget
accounting stays with the guard. What lives here is the typed verdict both
moments share, and the fingerprint that proves they judged one operation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256

from pydantic import JsonValue

from .authorization import AuthorizationCode
from .models import Action, Observation, WorldStateRevision
from .safety import ActionGuard, SafetyViolation


def operation_fingerprint(action: Action) -> str:
    """Identify one exact operation and its arguments, stably across moments.

    Plan-time and input-time decisions are only comparable if they name the same
    operation, so this hashes the typed action rather than its object identity:
    the same request rebuilt from the same evidence fingerprints the same, and a
    changed argument does not.
    """

    payload = json.dumps(
        action.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"op-{sha256(payload.encode('utf-8')).hexdigest()[:20]}"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """One typed verdict about one exact operation on one exact revision."""

    allowed: bool
    code: AuthorizationCode
    based_on_revision: WorldStateRevision
    operation_fingerprint: str
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def concerns_same_operation_as(self, other: AuthorizationDecision) -> bool:
        """Did these two verdicts judge the same operation?

        A revalidation that answers about a different operation than the one
        authorized is not a revalidation, however current its evidence.
        """

        return self.operation_fingerprint == other.operation_fingerprint


class OperationAuthority:
    """Evaluate one operation's cross-cutting authority against current state."""

    def __init__(self, guard: ActionGuard) -> None:
        self._guard = guard

    def evaluate(self, action: Action, observation: Observation) -> AuthorizationDecision:
        """Answer whether this operation may act on this observation.

        Deliberately free of side effects: budget is reserved separately, so the
        input boundary can ask the same question again without spending capacity
        the scheduling check already accounted for.
        """

        fingerprint = operation_fingerprint(action)
        try:
            self._guard.revalidate(action, observation)
        except SafetyViolation as exc:
            return AuthorizationDecision(
                allowed=False,
                code=AuthorizationCode.OPERATION_UNAUTHORIZED,
                based_on_revision=observation.world_revision,
                operation_fingerprint=fingerprint,
                details={"violation": str(exc), "operation_kind": action.kind},
            )
        return AuthorizationDecision(
            allowed=True,
            code=AuthorizationCode.ALLOWED,
            based_on_revision=observation.world_revision,
            operation_fingerprint=fingerprint,
            details={"operation_kind": action.kind},
        )
