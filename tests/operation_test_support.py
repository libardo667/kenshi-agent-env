"""Test-only adapter from scripted transition environments to fixed operations."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from kenshi_agent.input_boundary import ExecutionToken
from kenshi_agent.models import Action, CommandDispatchContext, Transition
from kenshi_agent.options import TransitionOperation

_EXACT_METHODS = frozenset(
    {
        "activate_visible_control",
        "approach_dialogue_target",
        "collect_resource_output",
        "command_world_target",
        "dismiss_screen",
        "equip_item",
        "exit_current_building",
        "move_in_direction",
        "move_to_character",
        "open_context_inventory",
        "open_screen",
        "pause",
        "perform_context_action",
        "produce_resource_output",
        "purchase_item",
        "recover_camera_view",
        "regroup_with_squad_member",
        "respond_to_immediate_threat",
        "rotate_camera",
        "scroll_screen",
        "select_squad_member",
        "select_squad_member_exact",
        "sell_item",
        "set_speed",
        "skill",
        "travel_to_map_destination",
        "use_game_binding",
        "wait",
    }
)


def operation_for(
    environment: Any,
    action: Action,
) -> TransitionOperation:
    async def execute(
        *,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> Transition:
        mechanics = getattr(environment, "operation_mechanics", None)
        operation = getattr(mechanics, action.kind, None)
        if operation is not None:
            authority = command or await _command_for(environment)
            return await operation(action, command=authority, token=token)
        return await _scripted_transition(
            environment,
            action,
            command=command,
            token=token,
        )

    return execute


def operation_family(environment: Any) -> TransitionOperation:
    async def execute(
        action: Action,
        *,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> Transition:
        mechanics = getattr(environment, "operation_mechanics", None)
        operation = getattr(mechanics, action.kind, None)
        if operation is not None:
            authority = command or await _command_for(environment)
            return await operation(action, command=authority, token=token)
        return await _scripted_transition(
            environment,
            action,
            command=command,
            token=token,
        )

    return execute


async def execute_operation(
    environment: Any,
    action: Action,
    *,
    command: CommandDispatchContext | None = None,
    token: ExecutionToken | None = None,
) -> Transition:
    """Exercise a concrete adapter through the same exact port as the kernel."""

    return await operation_for(environment, action)(command=command, token=token)


async def _command_for(environment: Any) -> CommandDispatchContext:
    observation = getattr(environment, "_last_observation", None)
    if observation is None:
        observation = environment.input_boundary_observation()
    if observation is None:
        observation = await environment.observe()
    return CommandDispatchContext(
        command_id=f"cmd-{uuid4().hex}",
        based_on_revision=observation.world_revision,
    )


async def _scripted_transition(
    environment: Any,
    action: Action,
    *,
    command: CommandDispatchContext | None,
    token: ExecutionToken | None,
) -> Transition:
    dispatch = getattr(environment, "dispatch", None)
    if command is not None and dispatch is not None:
        return await dispatch(action, command=command, token=token)
    transition = await environment.step(action)
    if command is None:
        return transition
    receipt = transition.receipt.model_copy(
        update={
            "command_id": command.command_id,
            "started_after_revision": command.based_on_revision,
            "completed_at_revision": transition.observation.world_revision,
            "causal_revision_advanced": transition.observation.world_revision.is_later_than(
                command.based_on_revision
            ),
        }
    )
    return transition.model_copy(update={"receipt": receipt})


class ScriptedOperationPort:
    """Test-only exact-method facade over a scripted transition environment."""

    def __init__(self, environment: Any) -> None:
        self._operation = operation_family(environment)

    def __getattr__(self, name: str) -> TransitionOperation:
        if name not in _EXACT_METHODS:
            raise AttributeError(name)
        return self._operation


def operation_port(environment: Any) -> ScriptedOperationPort:
    return ScriptedOperationPort(environment)
