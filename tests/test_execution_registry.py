from __future__ import annotations

from dataclasses import dataclass

import pytest

from kenshi_agent.execution.registry import HandlerRegistry
from kenshi_agent.operation_definitions import OPERATION_DEFINITION_LIST


@dataclass
class StubHandler:
    async def execute(self, bound: object, context: object) -> object:
        raise AssertionError("not executed")

    async def cancel(self, active: object, context: object) -> object:
        raise AssertionError("not cancelled")


def complete_handlers() -> dict[str, StubHandler]:
    return {
        definition.handler_key: StubHandler()
        for definition in OPERATION_DEFINITION_LIST
    }


def test_registry_requires_exactly_the_definition_handler_keys() -> None:
    registry = HandlerRegistry(complete_handlers())  # type: ignore[arg-type]

    assert registry.keys == frozenset(
        definition.handler_key for definition in OPERATION_DEFINITION_LIST
    )


def test_registry_rejects_missing_and_unknown_handlers() -> None:
    handlers = complete_handlers()
    missing = next(iter(handlers))
    del handlers[missing]
    handlers["obsolete.switch"] = StubHandler()

    with pytest.raises(ValueError, match="missing handler keys") as error:
        HandlerRegistry(handlers)  # type: ignore[arg-type]

    assert missing in str(error.value)
    assert "unknown handler keys: obsolete.switch" in str(error.value)
