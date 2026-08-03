from __future__ import annotations

from abc import ABC, abstractmethod

from ..final_safe_state import FinalSafeStateOutcome
from ..models import Observation


class AgentEnvironment(ABC):
    @abstractmethod
    async def reset(self, *, seed: int | None = None) -> Observation:
        raise NotImplementedError

    @abstractmethod
    async def observe(self) -> Observation:
        raise NotImplementedError

    async def observe_without_capture(self) -> Observation:
        """Read current state without requesting a new visual frame when supported."""

        return await self.observe()

    def input_boundary_observation(self) -> Observation | None:
        """Return current synchronous authority for a real input lease, if any."""

        return None

    def input_boundary_max_telemetry_age_seconds(self) -> float | None:
        """Return the freshness ceiling enforced by a real input lease, if any."""

        return None

    @abstractmethod
    async def close(self) -> FinalSafeStateOutcome | None:
        raise NotImplementedError
