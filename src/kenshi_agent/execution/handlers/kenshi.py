"""Every operation family's Kenshi mechanics over one external control surface."""

from __future__ import annotations

from .dialogue import KenshiDialogueMechanics
from .movement import KenshiMovementMechanics
from .resources import KenshiResourceMechanics
from .runtime import KenshiRuntimeMechanics


class KenshiOperationMechanics(
    KenshiRuntimeMechanics,
    KenshiMovementMechanics,
    KenshiDialogueMechanics,
    KenshiResourceMechanics,
):
    """The exact operations a live Kenshi adapter can perform.

    Each family owns its own mechanics in its own module; this composes them so
    one adapter object answers the whole operation vocabulary. It adds no
    behavior of its own, which is what keeps the families the only authority.
    """
