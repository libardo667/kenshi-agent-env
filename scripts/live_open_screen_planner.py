#!/usr/bin/env python3
"""Ask for screens by name, to test the loop that stranded an earlier run.

A window the agent had asked for opened, telemetry did not show it, and the
agent looped because nothing could tell it that it had already succeeded. This
plan asks for the same screen twice: the second request must send no input and
report the screen already open, rather than pressing the toggle closed.
"""

from __future__ import annotations

import argparse
import sys

from kenshi_agent.models import (
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    GameScreen,
    IdempotencyPolicy,
    Observation,
    OpenScreenAction,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen", action="append", required=True)
    return parser.parse_args()


def fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def main() -> None:
    args = parse_args()
    observation = Observation.model_validate_json(sys.stdin.readline())
    if observation.active_plan is not None:
        return

    steps: list[PlanStep] = []
    for name in args.screen:
        screen = GameScreen(name)
        for attempt in ("first", "again"):
            steps.append(
                PlanStep(
                    step_id=f"{screen.value}-{attempt}",
                    action=OpenScreenAction(screen=screen),
                    preconditions=[fresh()],
                    success_conditions=[fresh()],
                    failure_conditions=[
                        Condition(
                            kind=ConditionKind.FIELD,
                            path=ConditionPath.TELEMETRY_STALE,
                            operator=ConditionOperator.EQUALS,
                            expected=True,
                            max_age_seconds=3.0,
                        )
                    ],
                    timeout_seconds=12.0,
                    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                )
            )
    for index, step in enumerate(steps[:-1]):
        step.on_success = steps[index + 1].step_id

    plan = PlanEnvelope(
        schema_version="1.0",
        plan_id="live-open-screen",
        objective="Ask for each screen by name, twice, and observe it arrive.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh()],
        steps=steps,
        entry_step_id=steps[0].step_id,
        max_actions=len(steps),
        max_wall_seconds=240.0,
        max_game_seconds=240.0,
        risk_budget=RiskBudget(
            max_pointer_actions=len(steps),
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )
    print(plan.model_dump_json())


if __name__ == "__main__":
    main()
