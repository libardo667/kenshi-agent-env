from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from .approach import ApproachMonitor, ApproachStatus
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


class StatefulApproachOption:
    """A long approach toward a deterministic target, monitored by world state.

    Unlike a movement pulse, the approach dispatch (a native move-then-talk
    order) is acknowledged quickly while the character keeps walking for tens of
    seconds. So the dispatch task completing is not success: the option issues
    the action, then drives an `ApproachMonitor` from world-state updates and
    reaches SUCCEEDED only when dialogue opens with the exact target (or it
    closes inside the arrival radius), and FAILED when the monitor says the
    target vanished or a hostile entered threat range. This is the long,
    interruptible window in which strategic planning can overlap execution.
    """

    def __init__(
        self,
        *,
        option_id: str,
        action: SkillAction,
        environment: AgentEnvironment,
        target_id: str,
        arrival_distance: float = 5.0,
        threat_distance: float = 15.0,
    ) -> None:
        self.option_id = option_id
        self.action = action.model_copy(deep=True)
        self.environment = environment
        self.monitor = ApproachMonitor(
            target_id=target_id,
            arrival_distance=arrival_distance,
            threat_distance=threat_distance,
        )
        self.status = OptionStatus.CREATED
        self.start_observation: Observation | None = None
        self.latest_observation: Observation | None = None
        self.latest_status: ApproachStatus | None = None
        self.task: asyncio.Task[Transition] | None = None
        self.transition: Transition | None = None
        self.reason = "Approach option has not been prepared."

    def prepare(self, observation: Observation) -> OptionPoll:
        if self.status is not OptionStatus.CREATED:
            raise OptionLifecycleError("Approach option can only be prepared once.")
        telemetry = observation.telemetry
        if (
            telemetry is None
            or "game.pause" not in telemetry.capabilities
            or telemetry.game.paused is not True
        ):
            raise OptionLifecycleError(
                "Approach option requires a capable, confirmed paused start state."
            )
        begin = self.monitor.begin(observation)
        if not begin.target_present:
            raise OptionLifecycleError(
                "Approach option requires the target to be present at the start."
            )
        self.start_observation = observation.model_copy(deep=True)
        self.latest_observation = observation.model_copy(deep=True)
        self.latest_status = begin
        self.status = OptionStatus.PREPARED
        self.reason = "Approach start state is capable, paused, and the target is present."
        return self._poll_result()

    def start(
        self,
        command: CommandDispatchContext | None = None,
        *,
        token: ExecutionToken | None = None,
    ) -> asyncio.Task[Transition]:
        if self.status is not OptionStatus.PREPARED:
            raise OptionLifecycleError("Approach option must be prepared before start.")
        self.status = OptionStatus.RUNNING
        self.reason = "Approach order dispatched; walking toward the target."
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
        if self.status is not OptionStatus.RUNNING:
            return self._poll_result()

        # A definitively rejected or failed dispatch means the approach never
        # started; it is not a case of "still walking."
        task = self.task
        if task is not None and task.done():
            if task.cancelled():
                self.status = OptionStatus.CANCELLED
                self.reason = "Approach option task was cancelled."
                return self._poll_result()
            error = task.exception()
            if error is not None:
                self.status = OptionStatus.FAILED
                self.reason = f"Approach dispatch failed: {type(error).__name__}: {error}"
                return self._poll_result()
            if self.transition is None:
                self.transition = task.result()
                if (
                    not self.transition.receipt.accepted
                    and not self.transition.receipt.executed
                ):
                    self.status = OptionStatus.FAILED
                    self.reason = (
                        "Approach order was rejected without execution: "
                        f"{self.transition.receipt.message}"
                    )
                    return self._poll_result()

        # Drive the deterministic monitor from the latest world state.
        if update is not None:
            status = self.monitor.assess(update.observation)
            self.latest_status = status
            if status.arrived:
                # Success requires the order to have been accepted, so we do not
                # claim arrival from a dispatch that never issued.
                if self.transition is not None:
                    self.status = OptionStatus.SUCCEEDED
                    self.reason = status.reason
                else:
                    self.reason = (
                        "Target reached; awaiting dispatch acknowledgement. "
                        f"{status.reason}"
                    )
            elif status.should_abort:
                self.status = OptionStatus.FAILED
                self.reason = status.reason
            else:
                self.reason = status.reason
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
                    f"Approach option cancellation cleanup failed: {type(exc).__name__}: {exc}"
                )
                return self._poll_result()
        self.status = OptionStatus.CANCELLED
        self.reason = reason
        return self._poll_result()

    def result(self) -> Transition:
        if self.status is OptionStatus.FAILED and self.task is not None:
            done_cleanly = self.task.done() and not self.task.cancelled()
            error = self.task.exception() if done_cleanly else None
            if error is not None:
                raise error
        if self.status is not OptionStatus.SUCCEEDED or self.transition is None:
            raise OptionLifecycleError(
                f"Approach option has no successful transition in state {self.status.value!r}."
            )
        return self.transition.model_copy(deep=True)

    def _poll_result(self) -> OptionPoll:
        observation = self.latest_observation or self.start_observation
        revision = (
            observation.world_revision.model_copy(deep=True)
            if observation is not None
            else WorldStateRevision()
        )
        return OptionPoll(
            option_id=self.option_id,
            status=self.status,
            reason=self.reason,
            revision=revision,
        )
