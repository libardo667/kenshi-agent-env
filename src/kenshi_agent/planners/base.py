from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Observation, PlannerOutput


class Planner(ABC):
    @abstractmethod
    async def decide(self, observation: Observation) -> PlannerOutput:
        raise NotImplementedError
