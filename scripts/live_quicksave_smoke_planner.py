#!/usr/bin/env python3
"""Write one controller-verified quicksave in a guarded live smoke test."""

from __future__ import annotations

import sys

from kenshi_agent.action_contracts import USE_GAME_BINDING_CONTRACT
from kenshi_agent.live_smoke_planner import preserve_pause_handoff_patch
from kenshi_agent.models import (
    Condition,
    ConditionKind,
    ConditionOperator,
    GameBinding,
    IdempotencyPolicy,
    Observation,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
    UseGameBindingAction,
)


def main() -> None:
    observation = Observation.model_validate_json(sys.stdin.readline())
    if observation.active_plan is not None:
        print(preserve_pause_handoff_patch(observation).model_dump_json())
        return
    action = UseGameBindingAction(
        binding=GameBinding.QUICKSAVE,
        expected_effect="write the current game to the exact quicksave slot",
    )
    binding = USE_GAME_BINDING_CONTRACT.bind(action, observation)
    if not binding.bound:
        raise RuntimeError(f"Quicksave smoke cannot bind: {binding.reason}")
    fresh = Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )
    plan = PlanEnvelope(
        schema_version="1.0",
        plan_id="live-quicksave-smoke",
        objective="Write and causally verify one exact quicksave.",
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh],
        steps=[
            PlanStep(
                step_id="write-quicksave",
                action=action,
                preconditions=[fresh],
                timeout_seconds=15.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        entry_step_id="write-quicksave",
        max_actions=1,
        max_wall_seconds=20.0,
        max_game_seconds=30.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )
    print(plan.model_dump_json())


if __name__ == "__main__":
    main()
