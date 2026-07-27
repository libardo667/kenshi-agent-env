#!/usr/bin/env python3
"""Emit one revision-bound direction plan for a guarded live smoke test."""

from __future__ import annotations

import argparse
import sys

from kenshi_agent.action_contracts import NATIVE_WALK_DESTINATION_REACHED_RESULT
from kenshi_agent.live_smoke_planner import (
    interrupt_with_pause_handoff_patch,
    preserve_pause_handoff_patch,
    smoke_handoff_steps,
)
from kenshi_agent.models import (
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    IdempotencyPolicy,
    InterruptPolicy,
    MoveInDirectionAction,
    Observation,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bearing", type=float, required=True)
    parser.add_argument("--distance", type=float, required=True)
    parser.add_argument("--interrupt-on-advisory", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    observation = Observation.model_validate_json(sys.stdin.readline())
    if observation.active_plan is not None:
        patch = (
            interrupt_with_pause_handoff_patch(observation)
            if args.interrupt_on_advisory
            else preserve_pause_handoff_patch(observation)
        )
        print(patch.model_dump_json())
        return
    selected = (
        next(
            (character for character in observation.telemetry.squad if character.selected),
            None,
        )
        if observation.telemetry is not None
        else None
    )
    subject = selected.name if selected is not None else "the selected character"

    plan = PlanEnvelope(
        schema_version="1.0",
        plan_id="live-direction-smoke",
        objective=(
            f"Move {subject} {args.distance:g} units on bearing "
            f"{args.bearing:g} degrees."
        ),
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
                step_id="direction-smoke",
                action=MoveInDirectionAction(
                    bearing_degrees=args.bearing,
                    distance_units=args.distance,
                    expected_effect=(
                        f"{subject} advances a short distance toward the observed "
                        "Barman without using a character target."
                    ),
                ),
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
                success_conditions=[
                    Condition(
                        kind=ConditionKind.FIELD,
                        path=ConditionPath.TELEMETRY_NATIVE_CONTROL_LAST_RESULT,
                        operator=ConditionOperator.EQUALS,
                        expected=NATIVE_WALK_DESTINATION_REACHED_RESULT,
                        max_age_seconds=3.0,
                        required_capabilities=["control.move_in_direction"],
                    )
                ],
                timeout_seconds=55.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                interrupt_policy=InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH,
                on_success="pause-after-smoke",
            ),
            *smoke_handoff_steps(),
        ],
        entry_step_id="direction-smoke",
        max_actions=3,
        max_wall_seconds=65.0,
        max_game_seconds=540.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=1,
        ),
    )
    print(plan.model_dump_json())


if __name__ == "__main__":
    main()
