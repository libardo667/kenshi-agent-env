"""Zero-input runtime operation handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from ...input_boundary import ExecutionToken
from ...models import (
    Action,
    ActionReceipt,
    CommandDispatchContext,
    NoopAction,
    Observation,
    PauseAction,
    SetSpeedAction,
    StopAction,
    Transition,
    WaitAction,
)
from ...operation_definitions import BoundOperation
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .kenshi_surface import KenshiControlSurface


class RuntimeMechanicsPort(Protocol):
    async def pause(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def set_speed(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def wait(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...


@dataclass(frozen=True, slots=True)
class NoopHandler:
    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(NoopAction, bound.operation)
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for noop.")
        return OperationResult(
            status=OperationStatus.SUCCEEDED,
            observation=observation,
            reason=action.reason,
            transition=_transition(
                action,
                observation,
                executed=True,
                terminated=False,
            ),
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context, "Noop was cancelled before completion.")


@dataclass(frozen=True, slots=True)
class StopHandler:
    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(StopAction, bound.operation)
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for stop.")
        return OperationResult(
            status=OperationStatus.SUCCEEDED,
            observation=observation,
            reason=action.reason,
            transition=_transition(
                action,
                observation,
                executed=False,
                terminated=True,
            ),
            terminated=True,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context, "Stop was cancelled before completion.")


@dataclass(frozen=True, slots=True)
class RuntimeControlHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(PauseAction | SetSpeedAction | WaitAction, bound.operation)
        if context.command is None:
            raise RuntimeError("Runtime control has no command authority.")
        transition = await self.operation(
            action,
            command=context.command,
            token=context.token,
        )
        accepted = transition.receipt.accepted or transition.receipt.executed
        return OperationResult(
            status=(OperationStatus.SUCCEEDED if accepted else OperationStatus.REJECTED),
            observation=transition.observation,
            reason=transition.receipt.message,
            transition=transition,
            terminated=transition.terminated,
            success=transition.success,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context, "Runtime control was cancelled.")


def _cancelled(
    active: ActiveOperation,
    context: OperationContext,
    reason: str,
) -> OperationResult:
    observation = context.world.latest or active.started_observation
    return OperationResult(
        status=OperationStatus.CANCELLED,
        observation=observation,
        reason=reason,
    )


def _transition(
    action: NoopAction | StopAction,
    observation: Observation,
    *,
    executed: bool,
    terminated: bool,
) -> Transition:
    now = datetime.now(UTC)
    return Transition(
        receipt=ActionReceipt(
            action=action,
            control_mode=observation.control_mode,
            accepted=True,
            executed=executed,
            dry_run=False,
            started_at=now,
            finished_at=now,
            primitive_actions=0,
            message=action.reason,
        ),
        observation=observation,
        terminated=terminated,
        success=None,
        events=observation.events,
    )


def runtime_handlers(port: RuntimeMechanicsPort) -> dict[str, OperationHandler]:
    return {
        "runtime.noop": NoopHandler(),
        "runtime.stop": StopHandler(),
        "runtime.pause": RuntimeControlHandler(port.pause),
        "runtime.set_speed": RuntimeControlHandler(port.set_speed),
        "runtime.wait": RuntimeControlHandler(port.wait),
    }


class KenshiRuntimeMechanics:
    """Pause, speed, and wait mechanics against a live Kenshi process."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def pause(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_runtime_pause
        )

    async def set_speed(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_runtime_speed
        )

    async def wait(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        del token
        return await self._surface.run_without_input(
            action, command=command, receipt=self._execute_runtime_wait
        )

    async def _execute_runtime_wait(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        del command
        typed = cast(WaitAction, action)
        await asyncio.sleep(typed.seconds)
        return ActionReceipt(
            action=typed,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=0,
            message=f"Observed without input for {typed.seconds:.2f} seconds.",
        )

    async def _execute_runtime_pause(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        del command
        typed = cast(PauseAction, action)
        paused = (
            self._surface.last_observation.telemetry.game.paused
            if self._surface.last_observation is not None
            and self._surface.last_observation.telemetry is not None
            else None
        )
        if paused is typed.paused:
            return ActionReceipt(
                action=typed,
                accepted=True,
                executed=True,
                dry_run=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=0,
                message=f"Kenshi already reports paused={typed.paused}.",
            )
        if paused is None:
            raise RuntimeError(
                "Refusing to change Kenshi pause because the current pause state is unknown."
            )
        primitive_count, pause_control = await self._surface.apply_pause_request(typed.paused)
        return ActionReceipt(
            action=typed,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=primitive_count,
            message=(
                f"Used {pause_control} to request paused={typed.paused}. "
                "A later observation must confirm the state."
            ),
        )

    async def _execute_runtime_speed(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        del command
        return await self._surface.apply_playback_speed(cast(SetSpeedAction, action), started)
