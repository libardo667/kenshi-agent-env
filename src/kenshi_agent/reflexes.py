from __future__ import annotations

from .models import Observation, PauseAction, PlannerDecision, StopAction


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
        squad = telemetry.squad
        if squad and all(member.alive is False for member in squad):
            return PlannerDecision(
                intent="End the episode.",
                rationale="Every observed squad member is dead.",
                action=StopAction(reason="No living squad members remain."),
                confidence=1.0,
            )
        immediate_threat = any(
            entity.disposition.value == "hostile"
            and entity.visible
            and entity.distance is not None
            and entity.distance <= 35.0
            for entity in telemetry.nearby_entities
        )
        catastrophic_body_state = any(member.getting_eaten is True for member in squad)
        if (immediate_threat or catastrophic_body_state) and telemetry.game.paused is False:
            reason = (
                "A visible hostile is close."
                if immediate_threat
                else "A squad member is being eaten."
            )
            return PlannerDecision(
                intent="Pause for emergency reassessment.",
                rationale=reason,
                action=PauseAction(paused=True),
                confidence=0.98,
            )
        return None
