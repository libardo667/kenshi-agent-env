"""The mock port must carry every operation the handlers reach for.

`survey_local_resources` was added to MovementMechanicsPort and to the live
mechanics, and the mock's alias block was not updated. Nothing noticed, because
the handler dict is built eagerly and only at run time: every mock run died at
construction with `'MockOperationPort' object has no attribute
'survey_local_resources'`, and the entire off-game test path was dead until a
mock run was attempted by hand.

The instance is one missing alias. The class is that the mock's claim to be the
"exact operation surface" was a comment rather than a checked invariant, so any
future operation can silently break every mock run the same way.
"""

from __future__ import annotations

import inspect
from typing import Protocol, get_type_hints

import pytest

from kenshi_agent.env.mock import MockOperationPort
from kenshi_agent.execution.handlers.camera import CameraMechanicsPort
from kenshi_agent.execution.handlers.dialogue import DialogueMechanicsPort
from kenshi_agent.execution.handlers.inventory import InventoryMechanicsPort
from kenshi_agent.execution.handlers.movement import MovementMechanicsPort
from kenshi_agent.execution.handlers.resources import ResourceMechanicsPort
from kenshi_agent.execution.handlers.runtime import RuntimeMechanicsPort
from kenshi_agent.execution.handlers.screens import ScreenMechanicsPort
from kenshi_agent.execution.handlers.trade import TradeMechanicsPort

MECHANICS_PORTS: tuple[type[Protocol], ...] = (  # type: ignore[valid-type]
    CameraMechanicsPort,
    DialogueMechanicsPort,
    InventoryMechanicsPort,
    MovementMechanicsPort,
    ResourceMechanicsPort,
    RuntimeMechanicsPort,
    ScreenMechanicsPort,
    TradeMechanicsPort,
)


def _operations(port: type) -> list[str]:
    """Every operation the port declares, ignoring protocol machinery."""

    return sorted(
        name
        for name, member in vars(port).items()
        if not name.startswith("_") and inspect.isfunction(member)
    )


@pytest.mark.parametrize("port", MECHANICS_PORTS, ids=lambda port: port.__name__)
def test_the_mock_implements_every_declared_operation(port: type) -> None:
    declared = _operations(port)
    assert declared, f"{port.__name__} declares no operations; the probe is wrong"

    missing = [name for name in declared if not hasattr(MockOperationPort, name)]

    assert not missing, (
        f"MockOperationPort is missing {missing} from {port.__name__}. "
        "Every mock run dies at handler construction until these exist."
    )


def test_the_handler_dictionary_builds_against_the_mock() -> None:
    """The failure was at construction, so the invariant is checked there.

    Attribute-by-attribute checking would pass if a factory reached for
    something no protocol declares; building the real dictionary would not.
    """

    from kenshi_agent.config import PlanningConfig
    from kenshi_agent.execution.handlers.camera import camera_handlers
    from kenshi_agent.execution.handlers.dialogue import dialogue_handlers
    from kenshi_agent.execution.handlers.inventory import inventory_handlers
    from kenshi_agent.execution.handlers.movement import movement_handlers
    from kenshi_agent.execution.handlers.runtime import runtime_handlers
    from kenshi_agent.execution.handlers.screens import screen_handlers
    from kenshi_agent.execution.handlers.trade import trade_handlers

    port = MockOperationPort.__new__(MockOperationPort)
    config = PlanningConfig()

    handlers = {
        **runtime_handlers(port),
        **screen_handlers(port),
        **trade_handlers(port),
        **inventory_handlers(port),
        **camera_handlers(port),
        **dialogue_handlers(port, config),
        **movement_handlers(port, config),
    }

    assert "movement.survey_local_resources" in handlers


def test_the_probe_reads_real_signatures() -> None:
    """Guards the parametrized test against silently inspecting nothing."""

    assert "survey_local_resources" in _operations(MovementMechanicsPort)
    assert get_type_hints(MovementMechanicsPort.survey_local_resources)
