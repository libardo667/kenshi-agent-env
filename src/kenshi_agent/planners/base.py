from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Observation, PlannerDecision


class Planner(ABC):
    @abstractmethod
    async def decide(self, observation: Observation) -> PlannerDecision:
        raise NotImplementedError
