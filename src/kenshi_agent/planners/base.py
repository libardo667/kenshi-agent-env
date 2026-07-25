from __future__ import annotations

import re
from abc import ABC, abstractmethod

from ..config import PlannerConfig
from ..models import (
    LiveContinuousPolicy,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanPatch,
)

PlannerOutputModel = type[PlannerDecision] | type[PlanEnvelope] | type[PlanPatch]

_POLICY_SECTION = re.compile(
    r"<!-- policy:(?P<policy>[a-z0-9_,]+) -->\n(?P<body>.*?)<!-- /policy -->\n",
    re.DOTALL,
)


def instructions_for_policy(instructions: str, policy: LiveContinuousPolicy) -> str:
    """Keep only the prompt sections that apply to the active live policy.

    Every policy's rules used to be sent on every call, so a generic run also
    received the Barman recipe - wasted tokens, and a standing invitation to
    anchor on a scenario the run is not in. Sections are marked in the prompt
    file rather than split across files so the whole document stays readable.
    """

    def keep(match: re.Match[str]) -> str:
        wanted = {name.strip() for name in match.group("policy").split(",")}
        return match.group("body") if policy.value in wanted else ""

    return _POLICY_SECTION.sub(keep, instructions).replace("\n\n\n", "\n\n")


def structured_output_model(observation: Observation) -> PlannerOutputModel:
    if observation.planning_mode != PlanningMode.CONTINUOUS:
        return PlannerDecision
    if observation.active_plan is not None:
        return PlanPatch
    return PlanEnvelope


def output_token_budget(
    config: PlannerConfig,
    observation: Observation,
    *,
    max_plan_steps: int,
) -> int:
    expected_steps = 0
    if observation.planning_mode == PlanningMode.CONTINUOUS:
        if observation.active_plan is not None:
            expected_steps = max(1, observation.active_plan.remaining_actions)
        elif (
            observation.live_execution_policy
            == LiveContinuousPolicy.FOOD_PROCUREMENT_V1
            and observation.telemetry is not None
        ):
            phase_steps: dict[str | None, int] = {
                None: 1,
                "world": 3,
                "dialogue": 2,
                "trade": 1,
            }
            expected_steps = phase_steps.get(
                observation.telemetry.ui.active_screen,
                1,
            )
        else:
            expected_steps = max_plan_steps
        expected_steps = min(expected_steps, max_plan_steps)
    return min(
        config.max_output_tokens_ceiling,
        config.max_output_tokens_base
        + config.max_output_tokens_per_plan_step * expected_steps,
    )


class Planner(ABC):
    @abstractmethod
    async def decide(self, observation: Observation) -> PlannerOutput:
        raise NotImplementedError
