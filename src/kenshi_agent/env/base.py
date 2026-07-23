from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Action, Observation, Transition


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

    @abstractmethod
    async def step(self, action: Action) -> Transition:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
