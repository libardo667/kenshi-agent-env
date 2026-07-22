from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..models import Action, ActionReceipt, Observation, StopAction, Transition
from .base import AgentEnvironment


class ReplayEnvironment(AgentEnvironment):
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self._observations = self._load_observations(log_path)
        self._index = 0

    @staticmethod
    def _load_observations(path: Path) -> list[Observation]:
        observations: list[Observation] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                if record.get("event_type") == "observation":
                    observations.append(Observation.model_validate(record["payload"]))
        if not observations:
            raise ValueError(f"No observation events found in {path}.")
        return observations

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        self._index = 0
        return self._observations[0].model_copy(update={"mode": "replay"})

    async def observe(self) -> Observation:
        return self._observations[self._index].model_copy(update={"mode": "replay"})

    async def step(self, action: Action) -> Transition:
        started = datetime.now(UTC)
        if self._index + 1 < len(self._observations):
            self._index += 1
        terminated = self._index + 1 >= len(self._observations) or isinstance(action, StopAction)
        observation = await self.observe()
        return Transition(
            receipt=ActionReceipt(
                action=action,
                accepted=True,
                executed=False,
                dry_run=True,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=0,
                message="Replay environment does not execute actions.",
            ),
            observation=observation,
            terminated=terminated,
            success=None,
        )

    async def close(self) -> None:
        return None
