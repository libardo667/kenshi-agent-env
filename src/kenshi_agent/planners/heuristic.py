from __future__ import annotations

from ..models import (
    Condition,
    ConditionKind,
    ConditionOperator,
    IdempotencyPolicy,
    MemoryKind,
    MemoryWrite,
    Observation,
    PauseAction,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanStep,
    RiskBudget,
    SetSpeedAction,
    SkillAction,
    StopAction,
    WaitAction,
)
from .base import Planner


class HeuristicPlanner(Planner):
    """Auditable baseline policy used for smoke tests and benchmark control."""

    async def decide(self, observation: Observation) -> PlannerOutput:
        if observation.planning_mode == PlanningMode.CONTINUOUS:
            continuous = self._continuous_setup_plan(observation)
            if continuous is not None:
                return continuous
            return PlannerDecision(
                intent="Stop after the bounded continuous setup proof.",
                rationale=(
                    "The heuristic continuous baseline has no further typed plan "
                    "whose postconditions it can prove."
                ),
                action=StopAction(reason="Continuous heuristic setup complete."),
                confidence=1.0,
            )

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
        selected = next(
            (member for member in telemetry.squad if member.selected), telemetry.squad[0]
        )
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
                expected_observation=(
                    "The hostile should disappear and location should become The Hub."
                ),
                memory_writes=[
                    MemoryWrite(
                        kind=MemoryKind.EPISODE,
                        content=(
                            f"Encountered {hostile.name} near "
                            f"{telemetry.game.location_name or 'an unknown place'}."
                        ),
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
                rationale=(
                    "The situation is stable and the baseline benchmark benefits from faster time."
                ),
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

    @classmethod
    def _continuous_setup_plan(
        cls,
        observation: Observation,
    ) -> PlanEnvelope | PlannerDecision | None:
        """Return a small causal plan for the deterministic mock setup seam."""

        telemetry = observation.telemetry
        if telemetry is None:
            return PlannerDecision(
                intent="Stop rather than create a plan from absent telemetry.",
                rationale="Continuous plans require an observable causal state.",
                action=StopAction(reason="No telemetry available."),
                confidence=1.0,
            )
        if telemetry.game.elapsed_minutes is None:
            return PlannerDecision(
                intent="Stop because game-time budget cannot be enforced.",
                rationale="Continuous plan budgets require observed elapsed game time.",
                action=StopAction(reason="Game-time telemetry unavailable."),
                confidence=1.0,
            )

        steps: list[PlanStep] = []
        if telemetry.game.paused is True:
            steps.append(
                PlanStep(
                    step_id="resume",
                    action=PauseAction(paused=False),
                    preconditions=[
                        cls._field_condition(
                            "telemetry.game.paused",
                            True,
                            "game.pause",
                        )
                    ],
                    success_conditions=[
                        cls._field_condition(
                            "telemetry.game.paused",
                            False,
                            "game.pause",
                        )
                    ],
                    timeout_seconds=2.0,
                    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                    on_success=(
                        "accelerate"
                        if telemetry.game.speed_multiplier is not None
                        and telemetry.game.speed_multiplier < 3
                        else None
                    ),
                )
            )
        if telemetry.game.speed_multiplier is not None and telemetry.game.speed_multiplier < 3:
            steps.append(
                PlanStep(
                    step_id="accelerate",
                    action=SetSpeedAction(speed=3),
                    preconditions=[
                        cls._field_condition(
                            "telemetry.game.paused",
                            False,
                            "game.pause",
                        )
                    ],
                    success_conditions=[
                        cls._field_condition(
                            "telemetry.game.speed_multiplier",
                            3.0,
                            "game.speed",
                        )
                    ],
                    timeout_seconds=2.0,
                    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                )
            )
        if not steps:
            return None

        return PlanEnvelope(
            schema_version="1.0",
            plan_id=f"heuristic_setup_{observation.step_index}",
            plan_version=1,
            objective="Resume and accelerate a causally observed stable world.",
            control_mode=observation.control_mode,
            based_on_revision=observation.world_revision,
            assumptions=[
                Condition(
                    kind=ConditionKind.TELEMETRY_FRESH,
                    operator=ConditionOperator.EQUALS,
                    expected=True,
                    max_age_seconds=3.0,
                )
            ],
            steps=steps,
            entry_step_id=steps[0].step_id,
            max_actions=len(steps),
            max_wall_seconds=8.0,
            max_game_seconds=10.0,
            risk_budget=RiskBudget(
                max_pointer_actions=0,
                max_purchase_actions=0,
                max_native_assisted_actions=0,
            ),
        )

    @staticmethod
    def _field_condition(
        path: str,
        expected: str | int | float | bool,
        capability: str,
    ) -> Condition:
        return Condition(
            kind=ConditionKind.FIELD,
            path=path,
            operator=ConditionOperator.EQUALS,
            expected=expected,
            max_age_seconds=3.0,
            required_capabilities=[capability],
        )
