from __future__ import annotations

from math import atan2, degrees

from .models import (
    CharacterState,
    MoveInDirectionAction,
    Observation,
    RespondToImmediateThreatAction,
    ThreatResponseStrategy,
)

IMMEDIATE_THREAT_DISTANCE = 35.0
MINIMUM_THREAT_RESPONSE_BLOOD = 50.0
THREAT_WITHDRAW_DISTANCE = 160.0
THREAT_RESPONSE_ACTION_KIND = "respond_to_immediate_threat"


def visible_immediate_hostile(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None:
        return False
    return any(
        entity.disposition.value == "hostile"
        and entity.visible is True
        and entity.distance is not None
        and entity.distance <= IMMEDIATE_THREAT_DISTANCE
        for entity in telemetry.nearby_entities
    )


def threat_response_health_error(observation: Observation) -> str | None:
    """Return the first squad state that makes continued combat unsafe."""

    telemetry = observation.telemetry
    if telemetry is None:
        return "No telemetry is available to monitor squad health."
    if "squad.health" not in telemetry.capabilities:
        return "The squad.health capability is unavailable."
    if not telemetry.squad:
        return "No squad members are available to monitor."
    for member in telemetry.squad:
        if member.alive is not True:
            return f"{member.name} is not confirmed alive."
        if member.conscious is not True or member.down is True:
            return f"{member.name} is no longer confirmed standing and conscious."
        if member.getting_eaten is True:
            return f"{member.name} is being eaten."
        if member.blood is None:
            return f"{member.name} has no current blood observation."
        if member.blood <= MINIMUM_THREAT_RESPONSE_BLOOD:
            return (
                f"{member.name} blood reached {member.blood:g}, at or below the "
                f"runtime safety floor {MINIMUM_THREAT_RESPONSE_BLOOD:g}."
            )
    return None


def _selected_actor(
    action: RespondToImmediateThreatAction,
    observation: Observation,
) -> CharacterState | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return None
    selected = [
        member
        for member in telemetry.squad
        if member.selected and member.id == action.actor_id
    ]
    if len(selected) != 1:
        return None
    if telemetry.ui.selected_character_ids != [action.actor_id]:
        return None
    if telemetry.ui.selected_character_id != action.actor_id:
        return None
    return selected[0]


def threat_response_authority_error(
    action: RespondToImmediateThreatAction,
    observation: Observation,
) -> str | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return "No telemetry is available to authorize threat response."
    if observation.telemetry_stale:
        return "Telemetry is stale, so threat response cannot be authorized."
    if telemetry.game.loaded is not True:
        return "The game is not loaded."
    if telemetry.game.paused is not True:
        return "Threat response must begin from a confirmed pause."
    if telemetry.game.speed_multiplier is None:
        return "Current playback speed is unknown."
    if not visible_immediate_hostile(observation):
        return "No visible hostile is within the immediate-threat boundary."
    actor = _selected_actor(action, observation)
    if actor is None:
        return "actor_id must be the one exact currently selected squad member."
    if actor.position is None:
        return "The selected actor has no current world position."
    positioned_hostiles = [
        entity
        for entity in telemetry.nearby_entities
        if entity.disposition.value == "hostile"
        and entity.visible is True
        and entity.distance is not None
        and entity.distance <= IMMEDIATE_THREAT_DISTANCE
        and entity.position is not None
    ]
    if not positioned_hostiles:
        return "No immediate hostile has a current world position."
    return threat_response_health_error(observation)


def threat_response_terminal_reached(
    action: RespondToImmediateThreatAction,
    observation: Observation,
) -> bool:
    """Whether both the immediate threat and squad combat state have cleared."""

    telemetry = observation.telemetry
    if telemetry is None or not telemetry.squad:
        return False
    actor = next((member for member in telemetry.squad if member.id == action.actor_id), None)
    return bool(
        actor is not None
        and not visible_immediate_hostile(observation)
        and actor.in_combat is False
    )


def withdrawal_move(
    action: RespondToImmediateThreatAction,
    observation: Observation,
) -> MoveInDirectionAction | None:
    if action.strategy is not ThreatResponseStrategy.WITHDRAW:
        return None
    telemetry = observation.telemetry
    actor = _selected_actor(action, observation)
    if telemetry is None or actor is None or actor.position is None:
        return None
    positioned = [
        entity
        for entity in telemetry.nearby_entities
        if entity.disposition.value == "hostile"
        and entity.visible is True
        and entity.distance is not None
        and entity.distance <= IMMEDIATE_THREAT_DISTANCE
        and entity.position is not None
    ]
    if not positioned:
        return None
    hostile = min(positioned, key=lambda entity: entity.distance or 0.0)
    assert hostile.position is not None
    away_x = actor.position.x - hostile.position.x
    away_z = actor.position.z - hostile.position.z
    if away_x == 0.0 and away_z == 0.0:
        return None
    bearing = degrees(atan2(away_x, away_z)) % 360.0
    return MoveInDirectionAction(
        bearing_degrees=bearing,
        distance_units=THREAT_WITHDRAW_DISTANCE,
        expected_effect="Withdraw the selected actor away from the nearest immediate hostile.",
    )
