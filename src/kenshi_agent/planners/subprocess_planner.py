from __future__ import annotations

import asyncio
import json
import shlex

from ..models import Observation, PlanEnvelope, PlannerDecision, PlannerOutput, PlanningMode
from .base import Planner


class SubprocessPlanner(Planner):
    """JSON-lines bridge for any external coding-agent or model harness."""

    def __init__(self, command: str | list[str], *, timeout_seconds: float = 90.0) -> None:
        self.command = shlex.split(command) if isinstance(command, str) else list(command)
        if not self.command:
            raise ValueError("Subprocess planner command may not be empty.")
        self.timeout_seconds = timeout_seconds

    async def decide(self, observation: Observation) -> PlannerOutput:
        process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        request = observation.model_dump_json() + "\n"
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(request.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError("Subprocess planner timed out.") from exc
        if process.returncode != 0:
            raise RuntimeError(
                f"Subprocess planner exited {process.returncode}: "
                f"{stderr.decode('utf-8', errors='replace').strip()}"
            )
        text = stdout.decode("utf-8").strip()
        if not text:
            raise RuntimeError("Subprocess planner returned no JSON decision.")
        try:
            payload = json.loads(text.splitlines()[-1])
            if observation.planning_mode == PlanningMode.CONTINUOUS:
                return PlanEnvelope.model_validate(payload)
            return PlannerDecision.model_validate(payload)
        except Exception as exc:
            raise RuntimeError(f"Subprocess planner returned invalid decision JSON: {exc}") from exc
