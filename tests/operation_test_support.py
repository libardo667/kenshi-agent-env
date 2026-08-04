"""Test-only adapter from scripted transition environments to fixed operations."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from kenshi_agent.action_budget import ActionBudgetLedger
from kenshi_agent.affordances import OPERATION_BINDING_AUTHORITY, OperationBindingError
from kenshi_agent.authorization import AuthorizationCode
from kenshi_agent.config import PlanningConfig
from kenshi_agent.continuous_executor import ContinuousPlanExecutor
from kenshi_agent.input_boundary import ExecutionToken
from kenshi_agent.models import (
    Action,
    ClickAction,
    CommandDispatchContext,
    MoveCursorAction,
    Observation,
    PauseAction,
    PointerActionClass,
    ScrollAction,
    SkillAction,
    Transition,
)
from kenshi_agent.operation_authority import AuthorizationDecision, OperationAuthority
from kenshi_agent.operation_definitions import BoundOperation
from kenshi_agent.operation_execution import OperationExecutionFactory
from kenshi_agent.options import TransitionOperation
from kenshi_agent.plan_events import PlanEventRecorder
from kenshi_agent.planning import PlanningClock
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.safety import OperationPolicy
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.world_state import WorldStateStore

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
            execution_token = token or await _token_for(environment, action, authority)
            return await operation(action, command=authority, token=execution_token)
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
            execution_token = token or await _token_for(environment, action, authority)
            return await operation(action, command=authority, token=execution_token)
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


async def _current_observation(environment: Any) -> Observation:
    observation = getattr(environment, "_last_observation", None)
    if observation is None:
        observation = environment.input_boundary_observation()
    if observation is None:
        observation = await environment.observe()
    return observation


async def _token_for(
    environment: Any,
    action: Action,
    command: CommandDispatchContext,
) -> ExecutionToken:
    """Carry a real fresh binding through direct adapter characterization tests."""

    observation = await _current_observation(environment)
    scheduled = OPERATION_BINDING_AUTHORITY.bind(action, observation, affordance=None)
    max_age_seconds = environment.input_boundary_max_telemetry_age_seconds()
    pointer_class = scheduled.definition.pointer_class
    controls = getattr(environment, "controls_config", None)
    macros = getattr(environment, "macros", None)
    if isinstance(action, SkillAction) and controls is not None and macros is not None:
        if action.name in controls.semantic_pointer_skills:
            pointer_class = PointerActionClass.SEMANTIC_CURRENT
        elif not macros.has(action.name):
            pointer_class = PointerActionClass.UNSUPPORTED
        elif not any(
            isinstance(primitive, (ClickAction, MoveCursorAction, ScrollAction))
            for primitive in macros.expand(action)
        ):
            pointer_class = PointerActionClass.COORDINATE_INDEPENDENT

    def authorize(current: Observation) -> AuthorizationDecision:
        try:
            rebound: BoundOperation | None = OPERATION_BINDING_AUTHORITY.rebind(scheduled, current)
        except OperationBindingError as exc:
            return AuthorizationDecision(
                allowed=False,
                code=exc.code,
                based_on_revision=current.world_revision,
                operation_fingerprint=scheduled.identity.fingerprint,
                details={"violation": str(exc)},
            )
        if rebound.identity != scheduled.identity:
            return AuthorizationDecision(
                allowed=False,
                code=AuthorizationCode.STALE_BOUND_IDENTITY,
                based_on_revision=current.world_revision,
                operation_fingerprint=rebound.identity.fingerprint,
            )
        return AuthorizationDecision(
            allowed=True,
            code=AuthorizationCode.ALLOWED,
            based_on_revision=current.world_revision,
            operation_fingerprint=scheduled.identity.fingerprint,
            bound_operation=rebound,
        )

    return ExecutionToken(
        plan_id="adapter-characterization",
        plan_version=1,
        step_id=action.kind,
        command_id=command.command_id,
        control_mode=observation.control_mode,
        validated_revision=observation.world_revision,
        latest_observation=environment.input_boundary_observation,
        max_telemetry_age_seconds=max_age_seconds,
        pointer_class=pointer_class,
        authority_validator=authorize,
        authorized_fingerprint=scheduled.identity.fingerprint,
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

    async def control_pause(
        self,
        action: PauseAction,
        *,
        command: CommandDispatchContext,
    ) -> Transition:
        return await self._operation(action, command=command, token=None)


def operation_port(environment: Any) -> ScriptedOperationPort:
    return ScriptedOperationPort(environment)


def plan_executor(
    *,
    environment: Any,
    operation_port: Any,
    policy: OperationPolicy,
    reflexes: ReflexEngine,
    logger: SessionLogger,
    clock: PlanningClock,
    state_store: WorldStateStore,
    observe_transition: Any,
    planning_config: PlanningConfig,
) -> ContinuousPlanExecutor:
    """Compose the production plan boundary for focused operation tests."""

    events = PlanEventRecorder(logger)
    factory = OperationExecutionFactory(
        environment=environment,
        operation_port=operation_port,
        macros=policy.macros,
        action_budget=ActionBudgetLedger(policy.config, policy.macros),
        authority=OperationAuthority(policy, OPERATION_BINDING_AUTHORITY),
        logger=logger,
        clock=clock,
        observe_transition=observe_transition,
    )
    operations = factory.create(
        state_store=state_store,
        planning_config=planning_config,
        event=events,
        concurrent_planning=False,
    )
    return ContinuousPlanExecutor(
        operations=operations,
        reflexes=reflexes,
        logger=logger,
        clock=clock,
        state_store=state_store,
        planning_config=planning_config,
        event=events,
    )
