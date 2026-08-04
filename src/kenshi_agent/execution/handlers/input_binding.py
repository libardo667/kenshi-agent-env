"""Consume the operation authority's one fresh input-boundary binding."""

from __future__ import annotations

from typing import TypeVar

from ...core.observation import Observation
from ...core.operation import Action
from ...input_boundary import ExecutionToken
from ...operation_definitions import require_bound

BindingT = TypeVar("BindingT")


def authorized_input_binding(
    action: Action,
    token: ExecutionToken | None,
    binding_type: type[BindingT],
) -> tuple[BindingT, Observation]:
    """Return the fresh binding already authorized inside the input lease."""

    if token is None:
        raise RuntimeError("No input was sent: the operation has no input-boundary authority.")
    bound = token.authorized_bound
    observation = token.authorized_observation
    if bound is None or observation is None:
        raise RuntimeError("No input was sent: input-boundary authority produced no fresh binding.")
    if bound.operation != action:
        raise RuntimeError(
            "No input was sent: input-boundary authority concerns a different operation."
        )
    return require_bound(bound.binding, binding_type), observation
