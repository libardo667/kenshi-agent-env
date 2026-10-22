"""Single-owner routing for native movement completion."""

from __future__ import annotations

from .core.operation import (
    Action,
    ExitCurrentBuildingAction,
    MoveInDirectionAction,
    MoveToCharacterAction,
    PerformCharacterOrderAction,
    PerformContextAction,
    ProduceResourceOutputAction,
    RegroupWithSquadMemberAction,
    SurveyLocalResourcesAction,
    TravelToMapDestinationAction,
)


def has_keyed_native_movement_terminal(action: Action) -> bool:
    """Return whether native acknowledgement is the action's sole terminal."""

    return (
        isinstance(action, MoveInDirectionAction)
        or isinstance(action, MoveToCharacterAction)
        or isinstance(action, RegroupWithSquadMemberAction)
        or isinstance(action, TravelToMapDestinationAction)
        or isinstance(action, ExitCurrentBuildingAction)
        or isinstance(action, PerformCharacterOrderAction)
        or isinstance(action, PerformContextAction)
        or isinstance(action, ProduceResourceOutputAction)
        or isinstance(action, SurveyLocalResourcesAction)
    )