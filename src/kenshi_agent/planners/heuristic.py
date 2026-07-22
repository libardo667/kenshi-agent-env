from __future__ import annotations

from ..models import (
    MemoryKind,
    MemoryWrite,
    Observation,
    PauseAction,
    PlannerDecision,
    SetSpeedAction,
    SkillAction,
    StopAction,
    WaitAction,
)
from .base import Planner


class HeuristicPlanner(Planner):
    """Auditable baseline policy used for smoke tests and benchmark control."""

    async def decide(self, observation: Observation) -> PlannerDecision:
        telemetry = observation.telemetry
        if telemetry is None:
            return PlannerDecision(
                intent="Stop rather than operate blindly.",
                rationale="The observation contains no telemetry.",
                action=StopAction(reason="No telemetry available."),
                confidence=1.0,
            )
        if telemetry.game.elapsed_minutes is not None and telemetry.game.elapsed_minutes >= 1440:
            return PlannerDecision(
                intent="Finish the survival episode.",
                rationale="At least one in-game day has elapsed.",
                action=StopAction(reason="One-day survival objective reached."),
                confidence=1.0,
            )
        if not telemetry.squad:
            return PlannerDecision(
                intent="Stop because no controlled character is visible.",
                rationale="The telemetry squad list is empty.",
                action=StopAction(reason="No squad members observed."),
                confidence=0.95,
            )
        selected = next((member for member in telemetry.squad if member.selected), telemetry.squad[0])
        hostile = next(
            (
                entity
                for entity in telemetry.nearby_entities
                if entity.disposition.value == "hostile" and entity.visible
            ),
            None,
        )
        if hostile is not None and "seek_safety" in observation.available_skills:
            return PlannerDecision(
                intent="Break contact and return to safety.",
                rationale=f"{hostile.name} is visibly hostile and nearby.",
                action=SkillAction(name="seek_safety"),
                confidence=0.9,
                expected_observation="The hostile should disappear and location should become The Hub.",
                memory_writes=[
                    MemoryWrite(
                        kind=MemoryKind.EPISODE,
                        content=f"Encountered {hostile.name} near {telemetry.game.location_name or 'an unknown place'}.",
                        salience=0.55,
                        evidence="Visible hostile in telemetry.",
                    )
                ],
            )
        if (
            selected.bleeding_rate is not None
            and selected.bleeding_rate > 0
            and (selected.first_aid_kits or 0) > 0
            and "first_aid" in observation.available_skills
        ):
            return PlannerDecision(
                intent="Stabilize the injury.",
                rationale="The selected character is bleeding and has a first-aid kit.",
                action=SkillAction(name="first_aid"),
                confidence=0.97,
            )
        if (
            selected.hunger is not None
            and selected.hunger < 155
            and (selected.food_items or 0) > 0
            and "eat_food" in observation.available_skills
        ):
            return PlannerDecision(
                intent="Eat before hunger becomes dangerous.",
                rationale="Hunger is low and food is already available.",
                action=SkillAction(name="eat_food"),
                confidence=0.95,
            )
        if (
            selected.hunger is not None
            and selected.hunger < 185
            and (selected.food_items or 0) == 0
            and (telemetry.game.money or 0) >= 50
            and "buy_food" in observation.available_skills
        ):
            return PlannerDecision(
                intent="Buy a food reserve.",
                rationale="Hunger is declining, no food remains, and enough money is available.",
                action=SkillAction(name="buy_food"),
                confidence=0.9,
            )
        if telemetry.game.paused is True:
            return PlannerDecision(
                intent="Resume controlled time progression.",
                rationale="No urgent condition requires the mock world to remain paused.",
                action=PauseAction(paused=False),
                confidence=0.9,
            )
        if telemetry.game.speed_multiplier is not None and telemetry.game.speed_multiplier < 3:
            return PlannerDecision(
                intent="Advance a low-risk routine efficiently.",
                rationale="The situation is stable and the baseline benchmark benefits from faster time.",
                action=SetSpeedAction(speed=3),
                confidence=0.8,
            )
        if "work_for_cats" in observation.available_skills:
            return PlannerDecision(
                intent="Earn money while remaining near safety.",
                rationale="No urgent threat or resource crisis is visible.",
                action=SkillAction(name="work_for_cats"),
                confidence=0.75,
            )
        return PlannerDecision(
            intent="Observe a small amount of world progression.",
            rationale="No higher-priority action is supported by current evidence.",
            action=WaitAction(seconds=2.0),
            confidence=0.6,
        )
