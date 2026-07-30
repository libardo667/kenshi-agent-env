#!/usr/bin/env python3
"""Open each screen once so its widgets can be compared with Kenshi's layouts.

The widget denominator is captured from `data/gui/layout/*.layout`, but only the
world screen had ever been observed live. A screen that builds its buttons in
code rather than loading them from a layout would be missing from that
denominator in exactly the way `31/31 covered` was missing.
"""

from __future__ import annotations

import argparse
import sys

from kenshi_agent.models import (
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    GameBinding,
    IdempotencyPolicy,
    Observation,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
    UseGameBindingAction,
)


# Each is a toggle, so pressing twice returns to where it started; the sweep
# opens and closes one screen at a time rather than stacking them. A plan is
# capped at eight steps, so at most four screens sweep per run.
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
    screens = tuple(GameBinding(name) for name in args.screen)
    observation = Observation.model_validate_json(sys.stdin.readline())
    if observation.active_plan is not None:
        return

    steps: list[PlanStep] = []
    for binding in screens:
        for phase in ("open", "close"):
            steps.append(
                PlanStep(
                    step_id=f"{phase}-{binding.value.replace('_', '-')}",
                    action=UseGameBindingAction(
                        binding=binding,
                        expected_effect=(
                            f"{phase.capitalize()} the {binding.value} screen so its "
                            "widgets can be observed."
                        ),
                    ),
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
        plan_id="live-screen-sweep",
        objective="Open and close each screen once to observe its widgets.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh()],
        steps=steps,
        entry_step_id=steps[0].step_id,
        max_actions=len(steps),
        max_wall_seconds=240.0,
        max_game_seconds=240.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )
    print(plan.model_dump_json())


if __name__ == "__main__":
    main()
