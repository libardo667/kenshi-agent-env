from __future__ import annotations

import json
from pathlib import Path

from ..models import Observation, PlannerDecision, StopAction
from .base import Planner


class ScriptedPlanner(Planner):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._decisions = self._load(path)
        self._index = 0

    @staticmethod
    def _load(path: Path) -> list[PlannerDecision]:
        decisions: list[PlannerDecision] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                try:
                    decisions.append(PlannerDecision.model_validate(json.loads(stripped)))
                except Exception as exc:
                    raise ValueError(
                        f"Invalid scripted decision at {path}:{line_number}: {exc}"
                    ) from exc
        return decisions

    async def decide(self, observation: Observation) -> PlannerDecision:
        if self._index >= len(self._decisions):
            return PlannerDecision(
                intent="End the scripted episode.",
                rationale="The scripted decision file is exhausted.",
                action=StopAction(reason="Script exhausted."),
                confidence=1.0,
            )
        decision = self._decisions[self._index]
        self._index += 1
        return decision
