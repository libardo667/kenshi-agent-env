"""Dialogue approach and current world-target activation handlers."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, Protocol, cast

from ... import operation_definitions as operations
from ...config import PlanningConfig
from ...input_boundary import ExecutionToken
from ...models import (
    Action,
    ActionReceipt,
    ApproachDialogueTargetAction,
    ClickAction,
    CommandDispatchContext,
    CommandWorldTargetAction,
    MouseButton,
    SemanticActionReceipt,
    SkillAction,
    SkillArgument,
    Transition,
)
from ...operation_definitions import BoundActor, BoundOperation, require_bound
from ...options import StatefulApproachOption
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .input_binding import authorized_input_binding
from .kenshi_surface import KenshiControlSurface
from .movement import run_prepared_option

DialogueOperation = Callable[..., Coroutine[Any, Any, Transition]]


class DialogueMechanicsPort(Protocol):
    async def approach_dialogue_target(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def command_world_target(
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


@dataclass(frozen=True, slots=True)
class WorldTargetHandler:
    operation: DialogueOperation

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        transition = await _execute(self.operation, bound.operation, context)
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
        return _cancelled(active, context)


async def _execute(
    operation: DialogueOperation,
    action: Action,
    context: OperationContext,
) -> Transition:
    if context.command is None:
        raise RuntimeError("Dialogue operation has no command authority.")
    return await operation(action, command=context.command, token=context.token)


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
        "dialogue.command_world_target": WorldTargetHandler(port.command_world_target),
    }


class KenshiDialogueMechanics:
    """Approach and world-target mechanics that reach a dialogue terminal."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def approach_dialogue_target(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_approach_operation
        )

    async def command_world_target(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_world_target_operation(
                current, started, dispatch, token
            ),
        )

    async def _execute_approach_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_semantic_approach(
            cast(ApproachDialogueTargetAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_world_target_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_world_target_command(
            cast(CommandWorldTargetAction, action), started, token
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

        skill_name = self._surface.controls_config.native_approach_skill
        if skill_name is None or not self._surface.macros.has(skill_name):
            raise RuntimeError(
                "Semantic approach requires a configured native approach skill to "
                "supply its bounded primitives."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value=action.target_id)],
        )
        pulse_seconds = self._surface.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
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
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            paused_dialogue_terminal=True,
        )

    async def _execute_world_target_command(
        self,
        action: CommandWorldTargetAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Right-click one exact target at geometry re-read inside the input lease."""

        binding, observation = authorized_input_binding(
            action,
            token,
            operations.BoundPointerTarget,
        )
        bounds = binding.resolved_bounds
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        primitive_receipt = await self._surface.controller.execute(
            ClickAction(
                x=x,
                y=y,
                button=MouseButton.RIGHT,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.COMMAND_WORLD_TARGET_DEFINITION.version,
            target_id=binding.target_id,
            resolved_label=binding.resolved_label,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-resolved the exact world target and its current screen position "
                f"inside the input lease before Mouse2. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Commanded current target {binding.target_id!r} with Mouse2 "
                    f"for {binding.resolved_label!r}. A later observation must "
                    "confirm the resulting world task."
                ),
            }
        )
