from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Action, CommandDispatchContext, Observation, Transition


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

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
    ) -> Transition:
        """Dispatch through the legacy step seam while preserving caller causality."""

        transition = await self.step(action)
        if transition.receipt.command_id not in {None, command.command_id}:
            raise RuntimeError(
                "Environment receipt command ID does not match the dispatched command."
            )
        receipt = transition.receipt.model_copy(
            update={
                "command_id": command.command_id,
                "started_after_revision": command.based_on_revision,
                "completed_at_revision": transition.observation.world_revision,
                "causal_revision_advanced": (
                    transition.observation.world_revision.is_later_than(command.based_on_revision)
                ),
            }
        )
        return transition.model_copy(update={"receipt": receipt})

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
