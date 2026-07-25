"""Time one real planning call per model, and check the plan survives validation.

Reads the live observation but sends no input: this measures the planner, not
the game. Latency is the number that matters - the executor's native fence has
about a half-second life, and a 25s round trip is what made plans stale.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from kenshi_agent.config import load_config
from kenshi_agent.live_dev import _telemetry_read
from kenshi_agent.models import ControlMode, Observation, PlanningMode, WorldStateRevision
from kenshi_agent.planners.openrouter_planner import OpenRouterPlanner


def live_observation(config):
    snapshot = _telemetry_read(config).read().snapshot
    return Observation(
        run_id="bench", step_index=0, mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        planning_mode=PlanningMode.CONTINUOUS,
        live_execution_policy=config.planning.live_execution_policy,
        world_revision=WorldStateRevision(telemetry_sequence=snapshot.sequence),
        telemetry=snapshot, telemetry_stale=False,
        objective="You're playing Kenshi. Decide a goal for yourself and work toward it.",
    )


async def main() -> int:
    config = load_config("config/live.longform.yaml")
    obs = live_observation(config)
    print(f"observation: screen={obs.telemetry.ui.active_screen} "
          f"actions_offered={len(obs.semantic_action_digest())} "
          f"payload={len(obs.planner_payload(max_chars=config.planner.max_observation_chars))} chars\n")

    models = sys.argv[1:] or ["openai/gpt-4.1-mini"]
    print(f"{'model':44s} {'secs':>6s}  verdict")
    for model in models:
        cfg = config.planner.model_copy(update={
            "openrouter_model": model,
            "reasoning_effort": "none",
            "include_screenshot": False,
        })
        planner = OpenRouterPlanner(cfg, Path("prompts/planner_system.md"))
        start = time.monotonic()
        try:
            out = await planner.decide(obs)
            secs = time.monotonic() - start
            steps = len(getattr(out, "steps", []) or [])
            kinds = [s.action.kind for s in getattr(out, "steps", []) or []]
            print(f"{model:44s} {secs:6.1f}  PLAN ok: {steps} steps {kinds}")
            goal = getattr(out, "goal", None)
            if goal:
                print(f"{'':44s}         goal: {goal[:90]}")
        except Exception as exc:
            secs = time.monotonic() - start
            msg = f"{type(exc).__name__}: {exc}"
            print(f"{model:44s} {secs:6.1f}  FAILED {msg[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
