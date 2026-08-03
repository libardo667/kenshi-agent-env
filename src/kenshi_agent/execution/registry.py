"""Exact handler lookup for operation definitions."""

from __future__ import annotations

from collections.abc import Mapping

from ..operation_definitions import OPERATION_DEFINITION_LIST, BoundOperation
from .types import OperationHandler


class HandlerRegistry:
    """Immutable-by-convention one-to-one definition/handler registry."""

    def __init__(self, handlers: Mapping[str, OperationHandler]) -> None:
        expected = {definition.handler_key for definition in OPERATION_DEFINITION_LIST}
        actual = set(handlers)
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append(f"missing handler keys: {', '.join(missing)}")
            if unknown:
                details.append(f"unknown handler keys: {', '.join(unknown)}")
            raise ValueError("Invalid operation handler registry; " + "; ".join(details))
        self._handlers = dict(handlers)

    def resolve(self, bound: BoundOperation) -> OperationHandler:
        """Resolve exactly the handler named by the bound definition."""

        return self._handlers[bound.definition.handler_key]

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(self._handlers)
