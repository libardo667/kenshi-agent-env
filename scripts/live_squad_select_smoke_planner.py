#!/usr/bin/env python3
"""Select one exact visible squad member in a guarded live smoke test."""

from __future__ import annotations

import sys

from kenshi_agent.action_contracts import SELECT_SQUAD_MEMBER_CONTRACT
from kenshi_agent.live_smoke_planner import preserve_pause_handoff_patch
from kenshi_agent.models import (
    Condition,
    ConditionKind,
    ConditionOperator,
    IdempotencyPolicy,
    Observation,
    PlanEnvelope,
    PlanStep,
    RiskBudget,
    SelectSquadMemberAction,
)


def main() -> None:
    observation = Observation.model_validate_json(sys.stdin.readline())
    if observation.active_plan is not None:
        print(preserve_pause_handoff_patch(observation).model_dump_json())
        return
    telemetry = observation.telemetry
    if telemetry is None:
        raise RuntimeError("Squad-selection smoke requires current telemetry.")
    bindable = [
        (character, SelectSquadMemberAction(target_id=character.id))
        for character in telemetry.squad
        if SELECT_SQUAD_MEMBER_CONTRACT.bind(
            SelectSquadMemberAction(target_id=character.id),
            observation,
        ).bound
    ]
    if not bindable:
        raise RuntimeError(
            "Squad-selection smoke requires one squad member with current "
            "unambiguous lower-HUD portrait geometry."
        )
    target, action = next(
        (candidate for candidate in bindable if not candidate[0].selected),
        bindable[0],
    )

    fresh = Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )
    plan = PlanEnvelope(
        schema_version="1.0",
        plan_id="live-squad-select-smoke",
        objective=(
            f"Select exact visible squad member {target.name} ({target.id}) "
            "through the current lower-HUD portrait."
        ),
        control_mode=observation.control_mode,
        based_on_revision=observation.world_revision,
        assumptions=[fresh],
        steps=[
            PlanStep(
                step_id="select-visible-squad-member",
                action=action,
                preconditions=[fresh],
                timeout_seconds=10.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            )
        ],
        entry_step_id="select-visible-squad-member",
        max_actions=1,
        max_wall_seconds=20.0,
        max_game_seconds=30.0,
        risk_budget=RiskBudget(
            max_pointer_actions=1,
            max_purchase_actions=0,
            max_native_assisted_actions=1,
        ),
    )
    print(plan.model_dump_json())


if __name__ == "__main__":
    main()
