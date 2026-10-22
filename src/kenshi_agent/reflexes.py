from __future__ import annotations

from .core.observation import Observation
from .core.operation import (
    ControlMode,
    PauseAction,
    StopAction,
)
from .core.planning import PlannerDecision
from .core.telemetry import Disposition


def _can_shift_into_nearby_body(observation: Observation) -> bool:
    """Whether total-party-loss recovery via `shift_into_body` is reachable."""

    telemetry = observation.telemetry
    if (
        telemetry is None
        or observation.telemetry_stale
        or observation.control_mode != ControlMode.NATIVE_ASSISTED
    ):
        return False
    required = {
        "control.shift_into_body",
        "identity.stable_handles",
        "nearby.characters",
    }
    if not required.issubset(telemetry.capabilities):
        return False
    return any(
        bool(entity.id and entity.name)
        and entity.is_animal is not True
        and entity.conscious is True
        and entity.disposition != Disposition.HOSTILE
        for entity in telemetry.nearby_entities
    )


class ReflexEngine:
    """Small, auditable emergency layer. It does not pursue ordinary goals."""

    def decide(self, observation: Observation) -> PlannerDecision | None:
        telemetry = observation.telemetry
        if observation.mode == "live" and telemetry is None:
            return PlannerDecision(
                intent="Prevent blind control without telemetry.",
                rationale="No valid live telemetry snapshot is available.",
                action=StopAction(reason="Live telemetry unavailable."),
                confidence=1.0,
            )
        if observation.mode == "live" and observation.telemetry_stale:
            if telemetry is not None and telemetry.game.paused is False:
                return PlannerDecision(
                    intent="Freeze the world while observations are stale.",
                    rationale="The last telemetry snapshot is stale and the game reports unpaused.",
                    action=PauseAction(paused=True),
                    confidence=0.99,
                )
            return PlannerDecision(
                intent="Do not act on stale state.",
                rationale=(
                    "Telemetry is stale; continuing could turn interface error into game loss."
                ),
                action=StopAction(reason="Telemetry remained stale."),
                confidence=0.99,
            )
        if telemetry is None:
            return None
        squad = telemetry.roster
        if (
            squad
            and all(member.alive is False for member in squad)
            and not _can_shift_into_nearby_body(observation)
        ):
            return PlannerDecision(
                intent="End the episode.",
                rationale="Every observed squad member is dead.",
                action=StopAction(reason="No living squad members remain."),
                confidence=1.0,
            )
        catastrophic_body_state = any(member.getting_eaten is True for member in squad)
        if catastrophic_body_state and telemetry.game.paused is False:
            return PlannerDecision(
                intent="Pause for emergency reassessment.",
                rationale="A squad member is being eaten.",
                action=PauseAction(paused=True),
                confidence=0.98,
            )
        return None
