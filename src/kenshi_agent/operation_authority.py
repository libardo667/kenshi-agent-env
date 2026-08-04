"""One cross-cutting authority for whether an operation may act right now.

The same question is asked twice: once before an operation is scheduled, and
again inside the acquired input lease immediately before the first host or
native primitive. A polite lease can wait an unbounded interval, so the second
answer must come from fresh state - but it must come from the *same* policy, and
it must provably concern the *same* operation.

This owns that. Domain prerequisites stay on the operation definition; mutable
accounting stays with the action-budget ledger. What lives here is the typed verdict both
moments share, and the fingerprint that proves they judged one operation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import JsonValue

from .affordances import OperationBindingAuthority, OperationBindingError
from .authorization import AuthorizationCode
from .models import Observation, PointerActionClass, WorldStateRevision
from .operation_definitions import BoundOperation
from .safety import OperationPolicy, SafetyViolation


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """One typed verdict about one exact operation on one exact revision."""

    allowed: bool
    code: AuthorizationCode
    based_on_revision: WorldStateRevision
    operation_fingerprint: str
    details: Mapping[str, JsonValue] = field(default_factory=dict)
    bound_operation: BoundOperation | None = field(default=None, repr=False, compare=False)

    def concerns_same_operation_as(self, other: AuthorizationDecision) -> bool:
        """Did these two verdicts judge the same operation?

        A revalidation that answers about a different operation than the one
        authorized is not a revalidation, however current its evidence.
        """

        return self.operation_fingerprint == other.operation_fingerprint


class OperationAuthority:
    """Evaluate one operation's cross-cutting authority against current state."""

    def __init__(
        self,
        policy: OperationPolicy,
        binding: OperationBindingAuthority,
    ) -> None:
        self._policy = policy
        self._binding = binding

    def pointer_class_for(self, bound: BoundOperation) -> PointerActionClass:
        return self._policy.pointer_class_for(bound)

    def evaluate(
        self,
        bound: BoundOperation,
        observation: Observation,
    ) -> AuthorizationDecision:
        """Answer whether this operation may act on this observation.

        Deliberately free of side effects: budget is reserved separately, so the
        input boundary can ask the same question again without spending capacity
        the scheduling check already accounted for.
        """

        fingerprint = bound.identity.fingerprint
        try:
            rebound = self._binding.rebind(bound, observation)
        except OperationBindingError as exc:
            return AuthorizationDecision(
                allowed=False,
                code=exc.code,
                based_on_revision=observation.world_revision,
                operation_fingerprint=fingerprint,
                details={"violation": str(exc), "operation_kind": bound.operation.kind},
            )
        if rebound.identity != bound.identity:
            return AuthorizationDecision(
                allowed=False,
                code=AuthorizationCode.STALE_BOUND_IDENTITY,
                based_on_revision=observation.world_revision,
                operation_fingerprint=rebound.identity.fingerprint,
                details={
                    "violation": "Fresh binding resolved to a different operation identity.",
                    "operation_kind": bound.operation.kind,
                    "scheduled_fingerprint": fingerprint,
                },
            )
        try:
            self._policy.revalidate_bound(rebound, observation)
        except SafetyViolation as exc:
            return AuthorizationDecision(
                allowed=False,
                code=exc.code,
                based_on_revision=observation.world_revision,
                operation_fingerprint=fingerprint,
                details={"violation": str(exc), "operation_kind": bound.operation.kind},
            )
        return AuthorizationDecision(
            allowed=True,
            code=AuthorizationCode.ALLOWED,
            based_on_revision=observation.world_revision,
            operation_fingerprint=fingerprint,
            details={
                "operation_kind": bound.operation.kind,
                "binding_revision": observation.world_revision.model_dump(mode="json"),
            },
            bound_operation=rebound,
        )
