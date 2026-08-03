"""Movement, selection, travel, regroup, exit, and threat handlers."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol, cast

from ... import operation_definitions as operations
from ...config import PlanningConfig
from ...input_boundary import ExecutionToken
from ...models import (
    Action,
    ActionReceipt,
    ClickAction,
    CommandDispatchContext,
    ControlMode,
    ExitCurrentBuildingAction,
    MouseButton,
    MoveInDirectionAction,
    MoveToCharacterAction,
    Observation,
    PauseAction,
    RegroupWithSquadMemberAction,
    RespondToImmediateThreatAction,
    SelectSquadMemberAction,
    SelectSquadMemberExactAction,
    SemanticActionReceipt,
    SetSpeedAction,
    SkillAction,
    SkillArgument,
    ThreatResponseStrategy,
    Transition,
    TravelToMapDestinationAction,
)
from ...operation_definitions import BoundOperation
from ...options import (
    NativeMovementAction,
    OptionLifecycleError,
    OptionStatus,
    StatefulApproachOption,
    StatefulMovementOption,
    StatefulNativeMovementOption,
    StatefulThreatResponseOption,
)
from ...skills import MacroRegistry
from ..monitor_types import (
    MonitoredOperation,
    MonitoredOperationResult,
    MonitorFinalizer,
)
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .kenshi_surface import KenshiControlSurface

MovementOperation = Callable[..., Coroutine[Any, Any, Transition]]


class MovementMechanicsPort(Protocol):
    async def select_squad_member(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def select_squad_member_exact(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def move_to_character(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def move_in_direction(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def travel_to_map_destination(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def regroup_with_squad_member(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def exit_current_building(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def respond_to_immediate_threat(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def skill(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def pause(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...


@dataclass(frozen=True, slots=True)
class AtomicMovementHandler:
    operation: MovementOperation
    verify_native_terminal: bool = False

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        transition = await _execute(self.operation, bound.operation, context)
        if self.verify_native_terminal:
            acknowledgement = transition.receipt.native_acknowledgement
            succeeded = bool(
                acknowledgement is not None
                and acknowledgement.reason in bound.definition.native_terminal_success_reasons
            )
            context.progress(
                "Checked the definition's exact native terminal.",
                transition.observation,
                evidence={
                    "controller_verified": True,
                    "status": (
                        acknowledgement.status.value if acknowledgement is not None else "missing"
                    ),
                    "terminal_reason": (
                        acknowledgement.reason if acknowledgement is not None else "missing"
                    ),
                    "accepted_terminal_reasons": sorted(
                        bound.definition.native_terminal_success_reasons
                    ),
                },
            )
            return _transition_result(
                transition,
                status=(OperationStatus.SUCCEEDED if succeeded else OperationStatus.FAILED),
                reason=(
                    "Native action reached its definition-declared exact terminal."
                    if succeeded
                    else "Native action lacked its definition-declared exact terminal."
                ),
            )
        accepted = transition.receipt.accepted or transition.receipt.executed
        return _transition_result(
            transition,
            status=(OperationStatus.SUCCEEDED if accepted else OperationStatus.REJECTED),
            reason=transition.receipt.message,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


@dataclass(frozen=True, slots=True)
class NativeMovementHandler:
    operation: MovementOperation
    planning_config: PlanningConfig

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for movement.")
        action = cast(NativeMovementAction, bound.operation)
        option = StatefulNativeMovementOption(
            option_id=(
                f"native-movement-{context.scope.plan_id}-"
                f"{context.scope.plan_version}-{context.scope.step_id}"
            ),
            action=action,
            operation=partial(self.operation, action),
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
class ThreatResponseHandler:
    operation: MovementOperation
    withdrawal_operation: MovementOperation
    pause_operation: MovementOperation

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for threat response.")
        action = cast(RespondToImmediateThreatAction, bound.operation)
        option = StatefulThreatResponseOption(
            option_id=(
                f"threat-response-{context.scope.plan_id}-"
                f"{context.scope.plan_version}-{context.scope.step_id}"
            ),
            action=action,
            operation=partial(self.operation, action),
            withdrawal_operation=self.withdrawal_operation,
            pause_operation=partial(self.pause_operation, PauseAction(paused=True)),
        )
        return await run_prepared_option(
            option,
            observation,
            context,
            allow_concurrent=False,
            finalize=option.finish,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


@dataclass(frozen=True, slots=True)
class SkillHandler:
    operation: MovementOperation
    planning_config: PlanningConfig
    macros: MacroRegistry

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        observation = context.world.latest
        if observation is None:
            raise RuntimeError("No current observation is available for skill execution.")
        action = cast(SkillAction, bound.operation)
        if (
            self.planning_config.stateful_movement_options_enabled
            and self.macros.is_stateful_movement(action)
        ):
            option = StatefulMovementOption(
                option_id=(
                    f"option-{context.scope.plan_id}-"
                    f"{context.scope.plan_version}-{context.scope.step_id}"
                ),
                action=action,
                operation=partial(self.operation, action),
                require_paused_start=(self.planning_config.require_paused_between_actions),
            )
            return await run_prepared_option(option, observation, context)
        if (
            self.planning_config.stateful_approach_options_enabled
            and self.macros.is_approach_option(action)
        ):
            params = self.macros.approach_option_params(action)
            if params is None:
                raise RuntimeError("Approach skill has no current option parameters.")
            approach_option = StatefulApproachOption(
                option_id=(
                    f"approach-{context.scope.plan_id}-"
                    f"{context.scope.plan_version}-{context.scope.step_id}"
                ),
                action=action,
                operation=partial(self.operation, action),
                target_id=params.target_id,
                arrival_distance=params.arrival_distance,
                threat_distance=params.threat_distance,
                require_paused_start=(self.planning_config.require_paused_between_actions),
            )
            return await run_prepared_option(approach_option, observation, context)
        transition = await _execute(self.operation, action, context)
        accepted = transition.receipt.accepted or transition.receipt.executed
        return _transition_result(
            transition,
            status=(OperationStatus.SUCCEEDED if accepted else OperationStatus.REJECTED),
            reason=transition.receipt.message,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


async def run_prepared_option(
    option: MonitoredOperation,
    observation: Observation,
    context: OperationContext,
    *,
    allow_concurrent: bool = True,
    finalize: MonitorFinalizer | None = None,
) -> OperationResult:
    try:
        prepared = option.prepare(observation)
    except OptionLifecycleError as exc:
        return OperationResult(
            status=OperationStatus.REJECTED,
            observation=observation,
            reason=f"Monitored operation preparation failed: {exc}",
        )
    context.progress(
        prepared.reason,
        observation,
        event_type="option_prepared",
        evidence={
            "option_id": prepared.option_id,
            "option_status": prepared.status.value,
            "start_revision": prepared.revision.model_dump(mode="json"),
        },
    )
    if context.monitor is None or context.command is None:
        raise RuntimeError("Monitored operation has no lifecycle or command authority.")
    monitored = await context.monitor.run(
        option,
        command=context.command,
        token=context.token,
        allow_concurrent_planning=allow_concurrent,
        finalize=finalize,
        observation=observation,
    )
    return monitored_operation_result(monitored)


def monitored_operation_result(monitored: MonitoredOperationResult) -> OperationResult:
    succeeded = monitored.terminal.status is OptionStatus.SUCCEEDED
    interrupted = monitored.interrupted and monitored.staged_patch is not None
    return _transition_result(
        monitored.transition,
        status=(
            OperationStatus.INTERRUPTED
            if interrupted
            else OperationStatus.SUCCEEDED
            if succeeded
            else OperationStatus.FAILED
        ),
        reason=monitored.terminal.reason,
        staged_patch=monitored.staged_patch,
        pause_before_replan=(
            not succeeded
            and monitored.transition.observation.telemetry is not None
            and monitored.transition.observation.telemetry.game.paused is False
        ),
    )


async def _execute(
    operation: MovementOperation,
    action: Action,
    context: OperationContext,
) -> Transition:
    if context.command is None:
        raise RuntimeError("Movement operation has no command authority.")
    return await operation(action, command=context.command, token=context.token)


def _transition_result(
    transition: Transition,
    *,
    status: OperationStatus,
    reason: str,
    staged_patch: object | None = None,
    pause_before_replan: bool = False,
) -> OperationResult:
    return OperationResult(
        status=status,
        observation=transition.observation,
        reason=reason,
        transition=transition,
        terminated=transition.terminated,
        success=transition.success,
        monitoring_started=True,
        staged_patch=staged_patch,
        pause_before_replan=pause_before_replan,
    )


def _cancelled(
    active: ActiveOperation,
    context: OperationContext,
) -> OperationResult:
    return OperationResult(
        status=OperationStatus.CANCELLED,
        observation=context.world.latest or active.started_observation,
        reason="Movement operation was cancelled.",
    )


def movement_handlers(
    port: MovementMechanicsPort,
    planning_config: PlanningConfig,
    macros: MacroRegistry,
) -> dict[str, OperationHandler]:
    return {
        "movement.select_squad_member": AtomicMovementHandler(port.select_squad_member),
        "movement.select_squad_member_exact": AtomicMovementHandler(
            port.select_squad_member_exact,
            verify_native_terminal=True,
        ),
        "movement.move_to_character": NativeMovementHandler(
            port.move_to_character, planning_config
        ),
        "movement.move_in_direction": NativeMovementHandler(
            port.move_in_direction, planning_config
        ),
        "movement.travel_to_map_destination": NativeMovementHandler(
            port.travel_to_map_destination, planning_config
        ),
        "movement.regroup_with_squad_member": NativeMovementHandler(
            port.regroup_with_squad_member, planning_config
        ),
        "movement.exit_current_building": NativeMovementHandler(
            port.exit_current_building, planning_config
        ),
        "movement.respond_to_immediate_threat": ThreatResponseHandler(
            port.respond_to_immediate_threat,
            port.move_in_direction,
            port.pause,
        ),
        "movement.skill": SkillHandler(port.skill, planning_config, macros),
    }


class KenshiMovementMechanics:
    """Selection, native movement, threat response, and skill mechanics."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def skill(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        typed = cast(SkillAction, action)
        if (
            self._surface.macros.has(typed.name)
            and self._surface.macros.requires_native_assisted(typed.name)
            and self._surface.port.control_mode is not ControlMode.NATIVE_ASSISTED
        ):
            raise RuntimeError(f"Skill {typed.name!r} requires native_assisted control mode.")
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_runtime_skill
        )

    async def respond_to_immediate_threat(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_runtime_threat
        )

    async def select_squad_member(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_select_operation
        )

    async def select_squad_member_exact(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_select_exact_operation
        )

    async def move_in_direction(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_direction_operation
        )

    async def travel_to_map_destination(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_travel_operation
        )

    async def regroup_with_squad_member(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_regroup_operation
        )

    async def exit_current_building(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_exit_operation
        )

    async def move_to_character(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_move_operation
        )

    async def _execute_runtime_skill(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        typed = cast(SkillAction, action)
        pulse_seconds = self._surface.macros.resolve_movement_pulse_seconds(typed)
        if pulse_seconds is None:
            return await self._execute_skill(typed, started)
        if (
            self._surface.macros.requires_native_assisted(typed.name)
            and typed.name == "approach_confirmed_vendor"
        ):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            target_id = typed.argument_map().get("target_id")
            if not isinstance(target_id, str) or not target_id:
                raise RuntimeError("Native vendor approach requires an exact target_id.")
            return await self._surface.run_native_order(
                typed,
                started,
                command,
                target_id=target_id,
                pulse_seconds=pulse_seconds,
                primitive_skill=typed,
                require_vendor_role=True,
            )
        return await self._surface.run_movement_pulse(typed, started, pulse_seconds=pulse_seconds)

    async def _execute_runtime_threat(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        del command
        typed = cast(RespondToImmediateThreatAction, action)
        if typed.strategy is not ThreatResponseStrategy.ENGAGE:
            raise RuntimeError("Withdrawal must be compiled to runtime-owned native movement.")
        playback = await self._surface.apply_playback_speed(SetSpeedAction(speed=1), started)
        return playback.model_copy(
            update={
                "action": typed,
                "message": (
                    "Runtime established normal-speed playback for the chosen "
                    "engagement; threat and squad-health monitoring now own the "
                    "terminal. " + playback.message
                ),
                "semantic": SemanticActionReceipt(
                    action_kind=typed.kind,
                    contract_version="1.0",
                    target_id=typed.actor_id,
                    revalidation=(
                        "The exact selected actor and immediate hostile state were "
                        "revalidated inside the input lease."
                    ),
                ),
            }
        )

    async def _execute_select_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        del command
        return await self._execute_select_squad_member(
            cast(SelectSquadMemberAction, action), started
        )

    async def _execute_select_exact_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_select_squad_member_exact(
            cast(SelectSquadMemberExactAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_direction_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_directional_move(
            cast(MoveInDirectionAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_travel_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_map_travel(
            cast(TravelToMapDestinationAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_regroup_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_squad_regroup(
            cast(RegroupWithSquadMemberAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_exit_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_exit_current_building(
            cast(ExitCurrentBuildingAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_move_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        return await self._execute_semantic_move(
            cast(MoveToCharacterAction, action),
            started,
            await self._surface.require_command(command),
        )

    async def _execute_skill(self, action: SkillAction, started: datetime) -> ActionReceipt:
        primitive_count, messages = await self._surface.run_skill_primitives(action)
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=primitive_count,
            message=f"Executed skill {action.name!r}. " + " ".join(messages),
        )

    async def _execute_select_squad_member(
        self,
        action: SelectSquadMemberAction,
        started: datetime,
    ) -> ActionReceipt:
        """Left-click one exact squad portrait after in-lease revalidation."""

        result = self._surface.telemetry_reader.read()
        if result.stale:
            raise RuntimeError("No input was sent: telemetry became stale inside the input lease.")
        observation = self._surface.port._observation_from_snapshot(result.snapshot)
        binding = operations.require_bound(
            operations.SELECT_SQUAD_MEMBER_DEFINITION.bind(action, observation),
            operations.BoundVisibleTarget,
        )
        bounds = binding.resolved_bounds
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        primitive_receipt = await self._surface.controller.execute(
            ClickAction(
                x=x,
                y=y,
                button=MouseButton.LEFT,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.SELECT_SQUAD_MEMBER_DEFINITION.version,
            target_id=binding.target_id,
            resolved_label=binding.resolved_label,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-resolved the exact squad member and current lower-HUD portrait "
                f"inside the input lease before Mouse1. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Selected current squad member {binding.target_id!r} with "
                    "Mouse1. A later observation must confirm the singular "
                    "selected character."
                ),
            }
        )

    async def _execute_select_squad_member_exact(
        self,
        action: SelectSquadMemberExactAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Select and verify one exact squad identity through native code."""

        skill_name = self._surface.controls_config.native_approach_skill
        if skill_name is None or not self._surface.macros.has(skill_name):
            raise RuntimeError(
                "Exact squad selection requires a configured native transport skill."
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
            contract_version=operations.SELECT_SQUAD_MEMBER_EXACT_DEFINITION.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact current squad target and singular selection "
                "basis; native code owns selection and terminal verification."
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
            wire_command=operations.NATIVE_SQUAD_SELECTION_WIRE_COMMAND,
            require_dialogue_target=False,
            accepted_is_terminal_error=True,
        )

    async def _execute_directional_move(
        self,
        action: MoveInDirectionAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Walk a bearing and distance from wherever the character stands.

        The one movement that names nobody, so the one that still works
        somewhere empty. Everything else about the lifecycle - the bounded
        primitives, the acknowledgement, the monitored walk - is the approach's.
        """

        skill_name = self._surface.controls_config.native_approach_skill
        if skill_name is None or not self._surface.macros.has(skill_name):
            raise RuntimeError(
                "Directional movement requires a configured native approach skill "
                "to supply its bounded primitives."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value="")],
        )
        pulse_seconds = self._surface.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.MOVE_IN_DIRECTION_DEFINITION.version,
            source_revision=command.based_on_revision,
            revalidation=(
                f"Ordered a walk of {action.distance_units:.0f} units on bearing "
                f"{action.bearing_degrees:.0f} from the selected character's own "
                "position."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id="",
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=operations.NATIVE_DIRECTION_WIRE_COMMAND,
            require_dialogue_target=False,
            bearing_degrees=action.bearing_degrees,
            distance_units=action.distance_units,
        )

    async def _execute_map_travel(
        self,
        action: TravelToMapDestinationAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Issue one exact long-distance order to a discovered settlement."""

        skill_name = self._surface.controls_config.native_approach_skill
        if skill_name is None or not self._surface.macros.has(skill_name):
            raise RuntimeError(
                "Map travel requires a configured native approach skill to "
                "supply its bounded transport primitive."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[
                SkillArgument(
                    name="target_id",
                    value=action.destination_id,
                )
            ],
        )
        pulse_seconds = self._surface.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.TRAVEL_TO_MAP_DESTINATION_DEFINITION.version,
            target_id=action.destination_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound one exact currently discovered settlement marker; "
                "native code owns its waypoint, route, camera, and arrival."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id=action.destination_id,
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=operations.NATIVE_MAP_TRAVEL_WIRE_COMMAND,
            require_dialogue_target=False,
            running_speed_gear=3,
        )

    async def _execute_squad_regroup(
        self,
        action: RegroupWithSquadMemberAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Issue one global, exact order from the selected actor to a squadmate."""

        skill_name = self._surface.controls_config.native_approach_skill
        if skill_name is None or not self._surface.macros.has(skill_name):
            raise RuntimeError(
                "Squad regrouping requires a configured native approach skill to "
                "supply its bounded transport primitive."
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
            contract_version=operations.REGROUP_WITH_SQUAD_MEMBER_DEFINITION.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact selected actor and distinct current squadmate; "
                "native code owns global lookup, pathing, playback, and arrival."
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
            wire_command=operations.NATIVE_SQUAD_REGROUP_WIRE_COMMAND,
            require_dialogue_target=False,
            running_speed_gear=3,
            expected_actor_id=action.actor_id,
        )

    async def _execute_exit_current_building(
        self,
        action: ExitCurrentBuildingAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Ask native code to resolve and traverse the current building's door."""

        skill_name = self._surface.controls_config.native_approach_skill
        if skill_name is None or not self._surface.macros.has(skill_name):
            raise RuntimeError(
                "Building exit requires a configured native approach skill to "
                "supply its bounded transport primitive."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value="")],
        )
        pulse_seconds = self._surface.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.EXIT_CURRENT_BUILDING_DEFINITION.version,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-proved one selected character indoors, then delegated door "
                "choice, outdoor destination, and terminal judgment to native code."
            ),
        )
        return await self._surface.run_native_order(
            action,
            started,
            command,
            target_id="",
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=operations.NATIVE_EXIT_BUILDING_WIRE_COMMAND,
            require_dialogue_target=False,
        )

    async def _execute_semantic_move(
        self,
        action: MoveToCharacterAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Walk to one exact observed character without opening dialogue.

        Shares the approach's bounded primitives and pulse timing, which are
        proven, and differs in exactly two ways: the destination need not be
        talkable, and the native order is a move rather than a talk-to, so
        arriving starts no conversation.
        """

        skill_name = self._surface.controls_config.native_approach_skill
        if skill_name is None or not self._surface.macros.has(skill_name):
            raise RuntimeError(
                "Semantic move requires a configured native approach skill to "
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
            contract_version=operations.MOVE_TO_CHARACTER_DEFINITION.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Bound to the exact stable nearby character and issued at most one "
                "native move order for this option lifecycle."
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
            wire_command=operations.NATIVE_MOVE_WIRE_COMMAND,
            require_dialogue_target=False,
        )
