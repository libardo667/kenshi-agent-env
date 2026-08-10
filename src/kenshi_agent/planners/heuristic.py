from __future__ import annotations

from ..core.observation import Observation
from ..core.operation import (
    IdempotencyPolicy,
    PauseAction,
    SetSpeedAction,
    StopAction,
)
from ..core.planning import (
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanStep,
    RiskBudget,
)
from .base import Planner


class HeuristicPlanner(Planner):
    """Auditable baseline policy used for smoke tests and benchmark control."""

    async def decide(self, observation: Observation) -> PlannerOutput:
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
                            ConditionPath.TELEMETRY_GAME_PAUSED,
                            True,
                            "game.pause",
                        )
                    ],
                    success_conditions=[
                        cls._field_condition(
                            ConditionPath.TELEMETRY_GAME_PAUSED,
                            False,
                            "game.pause",
                        )
                    ],
                    timeout_seconds=2.0,
                    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                    on_success=(
                        "normalize_speed"
                        if telemetry.game.speed_multiplier is not None
                        and telemetry.game.speed_multiplier != 1.0
                        else None
                    ),
                )
            )
        if (
            telemetry.game.speed_multiplier is not None
            and telemetry.game.speed_multiplier != 1.0
        ):
            steps.append(
                PlanStep(
                    step_id="normalize_speed",
                    action=SetSpeedAction(speed=1),
                    preconditions=[
                        cls._field_condition(
                            ConditionPath.TELEMETRY_GAME_PAUSED,
                            False,
                            "game.pause",
                        )
                    ],
                    success_conditions=[
                        cls._field_condition(
                            ConditionPath.TELEMETRY_GAME_SPEED_MULTIPLIER,
                            1.0,
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
            objective="Resume a causally observed stable world at normal playback.",
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
        path: ConditionPath,
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
