"""Deterministic one-action planner for guarded native-assisted live smokes."""

from __future__ import annotations

from typing import Literal

from kenshi_agent.live_smoke_planner import (
    preserve_pause_handoff_patch,
    smoke_handoff_steps,
)
from kenshi_agent.models import (
    CharacterState,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    ContextActionKind,
    ExitCurrentBuildingAction,
    HarvestResourceAction,
    IdempotencyPolicy,
    InterruptPolicy,
    MoveToCharacterAction,
    Observation,
    PlanEnvelope,
    PlannerAction,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanStep,
    RespondToImmediateThreatAction,
    RiskBudget,
)

SmokeActionKind = Literal[
    "exit_current_building",
    "harvest_resource",
    "move_to_character",
]


def _selected_character(observation: Observation) -> CharacterState:
    telemetry = observation.telemetry
    if telemetry is None:
        raise ValueError("live native smoke requires telemetry")
    selected = [character for character in telemetry.squad if character.selected]
    if len(selected) != 1:
        raise ValueError("live native smoke requires one exact selected character")
    return selected[0]


def _smoke_action(
    observation: Observation,
    *,
    action_kind: SmokeActionKind,
    target_id: str | None,
) -> tuple[PlannerAction, str]:
    telemetry = observation.telemetry
    if telemetry is None:
        raise ValueError("live native smoke requires telemetry")
    selected = _selected_character(observation)

    if action_kind == "exit_current_building":
        if selected.indoors is not True:
            raise ValueError(
                "building-exit smoke requires the selected character confirmed indoors"
            )
        return (
            ExitCurrentBuildingAction(),
            f"Exit {selected.name}'s current building through its native door.",
        )

    if not target_id:
        raise ValueError("targeted native smoke requires --target-id")
    if action_kind == "move_to_character":
        characters = [
            entity
            for entity in telemetry.nearby_entities
            if entity.id == target_id
            and entity.kind == "character"
            and entity.is_animal is False
        ]
        if len(characters) != 1:
            raise ValueError(
                f"character target {target_id!r} is not currently nearby"
            )
        character = characters[0]
        return (
            MoveToCharacterAction(target_id=character.id),
            f"Walk to the exact currently nearby character {character.name}.",
        )

    targets = [
        target
        for target in telemetry.world_targets
        if target.id == target_id
        and ContextActionKind.OPERATE in target.context_actions
    ]
    if len(targets) != 1:
        raise ValueError(
            f"context target {target_id!r} is not currently actionable as operate"
        )
    target = targets[0]
    return (
        HarvestResourceAction(
            actor_id=selected.id,
            target_id=target.id,
            quantity=1,
        ),
        f"Harvest one output from the exact currently advertised {target.name}.",
    )


def build_plan(
    observation: Observation,
    *,
    action_kind: SmokeActionKind,
    target_id: str | None = None,
) -> PlanEnvelope:
    """Build one revision-bound plan or refuse an inapplicable live action."""

    if observation.telemetry_stale:
        raise ValueError("live native smoke requires fresh telemetry")
    telemetry = observation.telemetry
    if telemetry is None or not telemetry.game.loaded:
        raise ValueError("live native smoke requires a loaded game")
    if telemetry.game.paused is not True:
        raise ValueError("live native smoke requires a confirmed paused game")

    action, objective = _smoke_action(
        observation,
        action_kind=action_kind,
        target_id=target_id,
    )
    return PlanEnvelope(
        schema_version="1.0",
        plan_id=f"live-{action_kind.replace('_', '-')}-smoke",
        objective=objective,
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
        steps=[
            PlanStep(
                step_id="native-smoke",
                action=action,
                preconditions=[
                    Condition(
                        kind=ConditionKind.FIELD,
                        path=ConditionPath.TELEMETRY_GAME_PAUSED,
                        operator=ConditionOperator.EQUALS,
                        expected=True,
                        max_age_seconds=3.0,
                        required_capabilities=["game.pause"],
                    )
                ],
                success_conditions=[],
                timeout_seconds=(
                    300.0
                    if isinstance(action, HarvestResourceAction)
                    else (
                        60.0 if isinstance(action, MoveToCharacterAction) else 30.0
                    )
                ),
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                interrupt_policy=InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH,
                on_success="pause-after-smoke",
            ),
            *smoke_handoff_steps(),
        ],
        entry_step_id="native-smoke",
        max_actions=3,
        max_wall_seconds=360.0,
        max_game_seconds=540.0,
        risk_budget=RiskBudget(
            max_pointer_actions=(
                4 if isinstance(action, HarvestResourceAction) else 0
            ),
            max_purchase_actions=0,
            max_native_assisted_actions=(
                2 if isinstance(action, HarvestResourceAction) else 1
            ),
        ),
    )


def build_decision(
    observation: Observation,
    *,
    action_kind: SmokeActionKind,
    target_id: str | None = None,
) -> PlannerDecision:
    """Build the guarded action for this deterministic single-step probe."""

    if observation.telemetry_stale:
        raise ValueError("live native smoke requires fresh telemetry")
    telemetry = observation.telemetry
    if telemetry is None or not telemetry.game.loaded:
        raise ValueError("live native smoke requires a loaded game")
    if telemetry.game.paused is not True:
        raise ValueError("live native smoke requires a confirmed paused game")

    action, objective = _smoke_action(
        observation,
        action_kind=action_kind,
        target_id=target_id,
    )
    if isinstance(
        action,
        (HarvestResourceAction, RespondToImmediateThreatAction),
    ):
        raise ValueError(
            f"{action.kind} requires continuous option ownership"
        )
    return PlannerDecision(
        intent=objective,
        rationale=(
            "This smoke planner emits only the requested semantic action after "
            "checking its current native eligibility."
        ),
        action=action,
        confidence=1.0,
    )


def build_output(
    observation: Observation,
    *,
    action_kind: SmokeActionKind,
    target_id: str | None = None,
) -> PlannerOutput:
    """Follow the continuous planner state machine without reissuing the action."""

    if observation.planning_mode == PlanningMode.CONTINUOUS:
        return (
            preserve_pause_handoff_patch(observation)
            if observation.active_plan is not None
            else build_plan(
                observation,
                action_kind=action_kind,
                target_id=target_id,
            )
        )
    return build_decision(
        observation,
        action_kind=action_kind,
        target_id=target_id,
    )
