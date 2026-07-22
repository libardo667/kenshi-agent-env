"""Minimal one-shot subprocess planner used to test the JSON-lines bridge."""

from __future__ import annotations

import json
import sys

from kenshi_agent.models import Observation, PauseAction, PlannerDecision, StopAction, WaitAction


def main() -> int:
    line = sys.stdin.readline()
    if not line:
        print("No observation received.", file=sys.stderr)
        return 2
    observation = Observation.model_validate_json(line)
    telemetry = observation.telemetry
    if telemetry is None:
        decision = PlannerDecision(
            intent="Stop without valid telemetry.",
            rationale="The request contained no telemetry snapshot.",
            action=StopAction(reason="No telemetry."),
            confidence=1.0,
        )
    elif telemetry.game.paused is True:
        decision = PlannerDecision(
            intent="Resume a bounded observation interval.",
            rationale="The game reports paused and no emergency policy was triggered.",
            action=PauseAction(paused=False),
            confidence=0.8,
        )
    else:
        decision = PlannerDecision(
            intent="Allow a small amount of time to pass.",
            rationale="This example planner only demonstrates the subprocess contract.",
            action=WaitAction(seconds=1.0),
            confidence=0.6,
        )
    print(json.dumps(decision.model_dump(mode="json"), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
