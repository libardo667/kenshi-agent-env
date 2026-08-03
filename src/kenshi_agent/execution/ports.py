"""The whole operation vocabulary an environment adapter must be able to perform.

Each family declares the mechanics its own handlers require. This composes those
consumer-defined ports so an adapter can be typed once, without restating a
single signature: the family protocol stays the one authority for its family.
"""

from __future__ import annotations

from typing import Protocol

from .handlers.camera import CameraMechanicsPort
from .handlers.dialogue import DialogueMechanicsPort
from .handlers.inventory import InventoryMechanicsPort
from .handlers.movement import MovementMechanicsPort
from .handlers.resources import ResourceMechanicsPort
from .handlers.runtime import RuntimeMechanicsPort
from .handlers.screens import ScreenMechanicsPort
from .handlers.trade import TradeMechanicsPort


class OperationMechanicsPort(
    RuntimeMechanicsPort,
    MovementMechanicsPort,
    DialogueMechanicsPort,
    TradeMechanicsPort,
    InventoryMechanicsPort,
    ScreenMechanicsPort,
    CameraMechanicsPort,
    ResourceMechanicsPort,
    Protocol,
):
    """One adapter that answers every operation family's required mechanics."""
