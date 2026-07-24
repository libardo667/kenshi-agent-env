from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from .env import AgentEnvironment
from .input_boundary import ExecutionToken
from .models import (
    CommandDispatchContext,
    Observation,
    SkillAction,
    Transition,
    WorldStateRevision,
)
from .world_state import StoreUpdate


class OptionLifecycleError(RuntimeError):
    pass


class OptionStatus(StrEnum):
    CREATED = "created"
    PREPARED = "prepared"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class OptionPoll:
    option_id: str
    status: OptionStatus
    reason: str
    revision: WorldStateRevision


class StatefulMovementOption:
    """Lifecycle adapter for one existing bounded movement-pulse skill."""

    def __init__(
        self,
        *,
        option_id: str,
        action: SkillAction,
        environment: AgentEnvironment,
    ) -> None:
        self.option_id = option_id
        self.action = action.model_copy(deep=True)
        self.environment = environment
        self.status = OptionStatus.CREATED
        self.start_observation: Observation | None = None
        self.latest_observation: Observation | None = None
        self.task: asyncio.Task[Transition] | None = None
        self.transition: Transition | None = None
        self.reason = "Option has not been prepared."

    def prepare(self, observation: Observation) -> OptionPoll:
        if self.status is not OptionStatus.CREATED:
            raise OptionLifecycleError("Movement option can only be prepared once.")
        telemetry = observation.telemetry
        if (
            telemetry is None
            or "game.pause" not in telemetry.capabilities
            or telemetry.game.paused is not True
        ):
            raise OptionLifecycleError(
                "Movement option requires a capable, confirmed paused start state."
            )
        self.start_observation = observation.model_copy(deep=True)
        self.latest_observation = observation.model_copy(deep=True)
        self.status = OptionStatus.PREPARED
        self.reason = "Movement start state is capable and confirmed paused."
        return self._poll_result()

    def start(
        self,
        command: CommandDispatchContext | None = None,
        *,
        token: ExecutionToken | None = None,
    ) -> asyncio.Task[Transition]:
        if self.status is not OptionStatus.PREPARED:
            raise OptionLifecycleError("Movement option must be prepared before start.")
        self.status = OptionStatus.RUNNING
        self.reason = "Movement action is running through the environment."
        work = (
            self.environment.dispatch(self.action, command=command, token=token)
            if command is not None
            else self.environment.step(self.action)
        )
        self.task = asyncio.create_task(work, name=f"kenshi-agent-{self.option_id}")
        return self.task

    def poll(self, update: StoreUpdate | None = None) -> OptionPoll:
        if update is not None:
            self.latest_observation = update.observation.model_copy(deep=True)
        task = self.task
        if self.status is OptionStatus.RUNNING and task is not None and task.done():
            if task.cancelled():
                self.status = OptionStatus.CANCELLED
                self.reason = "Movement option task was cancelled."
            else:
                error = task.exception()
                if error is not None:
                    self.status = OptionStatus.FAILED
                    self.reason = f"Movement option failed: {type(error).__name__}: {error}"
                else:
                    self.transition = task.result()
                    self.latest_observation = self.transition.observation.model_copy(deep=True)
                    self.status = OptionStatus.SUCCEEDED
                    self.reason = "Movement environment transition completed."
        return self._poll_result()

    async def cancel(self, reason: str) -> OptionPoll:
        if self.status in {
            OptionStatus.SUCCEEDED,
            OptionStatus.FAILED,
            OptionStatus.CANCELLED,
        }:
            return self._poll_result()
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.status = OptionStatus.FAILED
                self.reason = (
                    f"Movement option cancellation cleanup failed: {type(exc).__name__}: {exc}"
                )
                return self._poll_result()
        self.status = OptionStatus.CANCELLED
        self.reason = reason
        return self._poll_result()

    def result(self) -> Transition:
        self.poll()
        if self.status is OptionStatus.FAILED and self.task is not None:
            error = self.task.exception()
            if error is not None:
                raise error
        if self.status is not OptionStatus.SUCCEEDED or self.transition is None:
            raise OptionLifecycleError(
                f"Movement option has no successful transition in state {self.status.value!r}."
            )
        return self.transition.model_copy(deep=True)

    def _poll_result(self) -> OptionPoll:
        observation = self.latest_observation or self.start_observation
        if observation is None:
            revision = WorldStateRevision()
        else:
            revision = observation.world_revision.model_copy(deep=True)
        return OptionPoll(
            option_id=self.option_id,
            status=self.status,
            reason=self.reason,
            revision=revision,
        )
