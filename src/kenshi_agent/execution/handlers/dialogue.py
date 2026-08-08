"""Native dialogue-approach operation."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, Protocol, cast

from ... import operation_definitions as operations
from ...config import PlanningConfig
from ...core.evidence import SemanticActionReceipt
from ...core.operation import (
    Action,
    ApproachDialogueTargetAction,
)
from ...core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    Transition,
)
from ...input_boundary import ExecutionToken
from ...operation_definitions import BoundActor, BoundOperation, require_bound
from ...options import StatefulApproachOption
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .kenshi_surface import KenshiControlSurface
from .movement import run_prepared_option

DialogueOperation = Callable[..., Coroutine[Any, Any, Transition]]


class DialogueMechanicsPort(Protocol):
    async def approach_dialogue_target(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

@dataclass(frozen=True, slots=True)
class ApproachHandler:
    operation: DialogueOperation
    planning_config: PlanningConfig

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for dialogue approach.")
        action = cast(ApproachDialogueTargetAction, bound.operation)
        binding = require_bound(bound.binding, BoundActor)
        option = StatefulApproachOption(
            option_id=(
                f"approach-{context.scope.plan_id}-"
                f"{context.scope.plan_version}-{context.scope.step_id}"
            ),
            action=action,
            operation=partial(self.operation, action),
            target_id=binding.target_id,
            arrival_distance=self.planning_config.semantic_approach_arrival_distance,
            threat_distance=self.planning_config.semantic_approach_threat_distance,
            require_paused_start=self.planning_config.require_paused_between_actions,
        )
        return await run_prepared_option(option, observation, context)

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


def _cancelled(
    active: ActiveOperation,
    context: OperationContext,
) -> OperationResult:
    return OperationResult(
        status=OperationStatus.CANCELLED,
        observation=context.world.latest or active.started_observation,
        reason="Dialogue operation was cancelled.",
    )


def dialogue_handlers(
    port: DialogueMechanicsPort,
    planning_config: PlanningConfig,
) -> dict[str, OperationHandler]:
    return {
        "dialogue.approach_dialogue_target": ApproachHandler(
            port.approach_dialogue_target,
            planning_config,
        ),
    }


class KenshiDialogueMechanics:
    """Approach mechanics that reach a dialogue terminal."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def approach_dialogue_target(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_approach_operation
        )

    async def _execute_approach_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_semantic_approach(
            cast(ApproachDialogueTargetAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_semantic_approach(
        self,
        action: ApproachDialogueTargetAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Issue one exact native talk-to order through the Kenshi bridge.

        The action never converts a projected world position into a pointer
        click. Kenshi receives `PLAYER_TALK_TO` for the stable entity handle,
        which can open nearby dialogue while paused and paths when movement is
        actually necessary.
        """

        pulse_seconds = self._surface.controls_config.native_movement_pulse_seconds
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.APPROACH_DIALOGUE_TARGET_DEFINITION.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Bound to the exact stable dialogue target and issued at most one "
                "native PLAYER_TALK_TO order for this option lifecycle."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id=action.target_id,
            pulse_seconds=pulse_seconds,
            require_vendor_role=False,
            wire_fields=operations.wire_fields_for(action),
            semantic=semantic,
            continue_until_terminal=True,
            paused_dialogue_terminal=True,
        )
