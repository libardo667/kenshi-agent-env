"""The sole definition and binding authority for private runtime operations.

An operation definition owns policy, risk, terminal authority, handler identity,
and exact current-state binding. Affordance adapters invoke these definitions
directly; no second contract language reconstructs an operation's meaning.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from enum import Enum, StrEnum
from hashlib import sha256
from typing import Literal, TypeAlias, TypeVar

from pydantic import BaseModel

from .core.affordance import BoundAffordance
from .core.observation import Observation
from .core.operation import (
    GAME_BINDING_KEYS,
    GAME_BINDING_MOUSE_BUTTONS,
    GAME_SPEED_MULTIPLIER_BY_GEAR,
    QUICKSAVE_COMPLETION_CAPABILITY,
    TIME_GAME_BINDINGS,
    Action,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    CollectResourceOutputAction,
    CommandWorldTargetAction,
    ConsultAdvisorAction,
    ControlMode,
    DismissScreenAction,
    EquipItemAction,
    ExitCurrentBuildingAction,
    GameBinding,
    GameScreen,
    HarvestResourceAction,
    IdempotencyPolicy,
    MoveInDirectionAction,
    MoveToCharacterAction,
    NoopAction,
    OpenContextInventoryAction,
    OpenScreenAction,
    PauseAction,
    PerformContextAction,
    PointerActionClass,
    ProduceResourceOutputAction,
    PurchaseItemAction,
    ReadFieldbookAction,
    RecallMemoryAction,
    RecoverCameraViewAction,
    RegroupWithSquadMemberAction,
    RespondToImmediateThreatAction,
    RotateCameraAction,
    ScrollScreenAction,
    SelectSquadMemberAction,
    SelectSquadMemberExactAction,
    SellItemAction,
    SetSpeedAction,
    StopAction,
    ThreatResponseStrategy,
    TravelToMapDestinationAction,
    UseGameBindingAction,
    WaitAction,
)
from .core.planning import (
    GAME_BINDING_TERMINALS,
    SCREEN_BINDINGS,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
    close_screen_success_condition,
    game_binding_success_condition,
    open_screen_success_condition,
    screen_is_open,
)
from .core.telemetry import (
    CharacterState,
    ContextActionKind,
    Disposition,
    NormalizedPointerBounds,
    WorldTarget,
    dialogue_targets,
    is_runtime_owned_visible_control,
    map_destination_already_reached,
    map_destination_travel_available,
    normalize_control_label,
)
from .core.world import WorldStateRevision
from .resource_transfer import resource_transfer_layout_error
from .threat_response import threat_response_authority_error

# The installed plug-in still names this capability and wire command after the
# vendor specialization it was first built for, but the fact it authorizes is
# "the caller may issue a pathing order to a valid dialogue target". The generic
# names are the contract vocabulary; the legacy names remain accepted aliases so
# the proven DLL keeps working without a rebuild.
NATIVE_APPROACH_CAPABILITY = "control.approach_dialogue_target"
LEGACY_NATIVE_APPROACH_CAPABILITY = "control.approach_vendor"
NATIVE_APPROACH_CAPABILITY_ALIASES: frozenset[str] = frozenset(
    {NATIVE_APPROACH_CAPABILITY, LEGACY_NATIVE_APPROACH_CAPABILITY}
)
NATIVE_MOVE_CAPABILITY = "control.move_to_character"
NATIVE_SQUAD_SELECTION_CAPABILITY = "control.select_squad_member"
NATIVE_SQUAD_REGROUP_CAPABILITY = "control.regroup_with_squad_member"
NATIVE_DIRECTION_CAPABILITY = "control.move_in_direction"
NATIVE_MAP_TRAVEL_CAPABILITY = "control.travel_to_map_destination"
NATIVE_MAP_DESTINATIONS_CAPABILITY = "world.known_map_destinations"
NATIVE_EXIT_BUILDING_CAPABILITY = "control.exit_current_building"
NATIVE_WALK_DESTINATION_REACHED_RESULT = "walk_destination_reached"
NATIVE_RESOURCE_OUTPUT_READY_RESULT = "resource_output_ready"
NATIVE_RESOURCE_TASK_RELEASED_RESULT = "resource_output_ready_task_released"
NATIVE_CONTEXT_ACTION_CAPABILITY = "control.perform_context_action"
NATIVE_CONTEXT_TARGETS_CAPABILITY = "world.context_targets"
WORLD_CONTEXT_TARGET_SCREEN_POSITIONS_CAPABILITY = "world.context_target_screen_positions"
NATIVE_PRODUCE_RESOURCE_CAPABILITY = "control.produce_resource_output"
NATIVE_OPEN_CONTEXT_INVENTORY_CAPABILITY = "control.open_context_inventory"
CONTEXT_INVENTORY_TARGET_CAPABILITY = "ui.context_inventory_target"

VISIBLE_CONTROLS_CAPABILITY = "ui.visible_controls"
CAMERA_RECOVERY_CAPABILITY = "camera.recovery"
SQUAD_REGROUP_ARRIVAL_DISTANCE = 12.0


class OperationExecution(StrEnum):
    """How the executor must run an action, not what the action means."""

    ATOMIC_HANDLER = "atomic_handler"
    MONITORED_OPTION = "monitored_option"
    COMPOSITE_OPTION = "composite_option"


class SelectionRequirement(StrEnum):
    """How much current squad selection an action may safely own."""

    NONE = "none"
    EXACTLY_ONE = "exactly_one"
    ONE_OR_MORE = "one_or_more"


class TerminalOwner(StrEnum):
    """Who turns one dispatched intention into a terminal result."""

    STEP_CONDITIONS = "step_conditions"
    RUNTIME_CONDITIONS = "runtime_conditions"
    CONTROLLER_TERMINAL = "controller_terminal"
    AFFORDANCE_DELIVERY = "affordance_delivery"


@dataclass(frozen=True, slots=True)
class OperationTerminal:
    """Completion authority resolved for one action at one observation."""

    owner: TerminalOwner
    conditions: tuple[Condition, ...] = ()


CompletionConditionFactory = Callable[
    [Action, Observation],
    tuple[Condition, ...] | None,
]
TerminalFactory = Callable[[Action, Observation, bool], OperationTerminal | None]


@dataclass(frozen=True, slots=True)
class OperationRisk:
    """What one attempt of this action spends from a plan's risk budgets."""

    pointer_actions: int = 0
    purchase_actions: int = 0
    native_assisted_actions: int = 0

    def as_tuple(self) -> tuple[int, int, int]:
        return (
            self.pointer_actions,
            self.purchase_actions,
            self.native_assisted_actions,
        )


RiskFactory = Callable[[Action], OperationRisk]
PrimitiveActionBoundFactory = Callable[[Action], int]


@dataclass(frozen=True, slots=True, kw_only=True)
class BindingFailure:
    """A fail-closed attempt to bind one operation to current state."""

    reason: str
    bound: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class EmptyBinding:
    """A current revision is sufficient; the operation owns no domain reference."""

    reason: str
    source_revision: WorldStateRevision
    bound: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundActor:
    reason: str
    target_id: str
    source_revision: WorldStateRevision
    bound: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundNamedTarget(BoundActor):
    resolved_label: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundPointerTarget(BoundNamedTarget):
    resolved_bounds: NormalizedPointerBounds


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundNamedOperation:
    reason: str
    resolved_label: str
    source_revision: WorldStateRevision
    bound: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundVisibleControl:
    reason: str
    resolved_label: str
    resolved_role: str
    resolved_bounds: NormalizedPointerBounds
    source_revision: WorldStateRevision
    bound: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundVisibleTarget(BoundVisibleControl):
    target_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundItemCell(BoundVisibleControl):
    item_name: str | None
    item_base_value: int | None = None
    item_sell_value: int | None = None
    item_quantity: int | None
    section: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundPurchaseCell(BoundItemCell):
    target_id: str
    inventory_owner_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundSaleCell(BoundVisibleControl):
    target_id: str
    inventory_owner_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundEquipmentCell(BoundVisibleControl):
    inventory_owner_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundResourceOutputCell(BoundVisibleControl):
    target_id: str
    item_name: str
    item_quantity: int
    section: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundScreenDismissal:
    reason: str
    resolved_label: str
    resolved_bounds: NormalizedPointerBounds | None
    source_revision: WorldStateRevision
    bound: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundCameraRecovery(BoundVisibleControl):
    target_id: str
    selected_character_name: str
    floor: int
    floor_up_bounds: NormalizedPointerBounds | None = None
    floor_down_bounds: NormalizedPointerBounds | None = None


OperationBinding: TypeAlias = (
    BindingFailure
    | EmptyBinding
    | BoundActor
    | BoundNamedTarget
    | BoundPointerTarget
    | BoundNamedOperation
    | BoundVisibleControl
    | BoundVisibleTarget
    | BoundItemCell
    | BoundPurchaseCell
    | BoundSaleCell
    | BoundEquipmentCell
    | BoundResourceOutputCell
    | BoundScreenDismissal
    | BoundCameraRecovery
)


def _unbound(reason: str) -> BindingFailure:
    return BindingFailure(reason=reason)


BindingT = TypeVar("BindingT")


def require_bound(
    binding: OperationBinding,
    binding_type: type[BindingT],
    *,
    context: str = "No input was sent",
) -> BindingT:
    """Narrow one definition result or fail before mechanics receive it."""

    if isinstance(binding, BindingFailure):
        raise RuntimeError(f"{context}: {binding.reason}")
    if not isinstance(binding, binding_type):
        raise RuntimeError(f"{context}: the operation definition returned the wrong binding type.")
    return binding


def unresolved_terminal(*, selected_affordance: bool = False) -> OperationTerminal:
    """Return the only valid terminal when no effect witness is defined."""

    return OperationTerminal(
        owner=(
            TerminalOwner.AFFORDANCE_DELIVERY
            if selected_affordance
            else TerminalOwner.STEP_CONDITIONS
        )
    )


def runtime_control_terminal(
    action: Action,
) -> OperationTerminal | None:
    """Resolve terminals for unadapted run-control mechanics only."""

    if isinstance(action, PauseAction):
        return OperationTerminal(
            owner=TerminalOwner.RUNTIME_CONDITIONS,
            conditions=(
                Condition(
                    kind=ConditionKind.FIELD,
                    path=ConditionPath.TELEMETRY_GAME_PAUSED,
                    operator=ConditionOperator.EQUALS,
                    expected=action.paused,
                    max_age_seconds=3.0,
                ),
            ),
        )
    if isinstance(action, SetSpeedAction):
        return OperationTerminal(
            owner=TerminalOwner.RUNTIME_CONDITIONS,
            conditions=(
                Condition(
                    kind=ConditionKind.FIELD,
                    path=ConditionPath.TELEMETRY_GAME_PAUSED,
                    operator=ConditionOperator.EQUALS,
                    expected=False,
                    max_age_seconds=3.0,
                ),
                Condition(
                    kind=ConditionKind.FIELD,
                    path=ConditionPath.TELEMETRY_GAME_SPEED_MULTIPLIER,
                    operator=ConditionOperator.EQUALS,
                    expected=GAME_SPEED_MULTIPLIER_BY_GEAR[action.speed],
                    max_age_seconds=3.0,
                ),
            ),
        )
    if isinstance(action, WaitAction):
        return OperationTerminal(owner=TerminalOwner.CONTROLLER_TERMINAL)
    return None


def unadapted_terminal(
    action: Action,
    *,
    selected_affordance: bool = False,
) -> OperationTerminal:
    """Resolve only run controls and legacy mechanics outside the registry."""

    return runtime_control_terminal(action) or unresolved_terminal(
        selected_affordance=selected_affordance
    )


def _selected_player_window_owner(
    observation: Observation,
    window: str,
) -> tuple[CharacterState | None, str | None]:
    """Resolve one player window to its exact currently selected squad owner."""

    telemetry = observation.telemetry
    if telemetry is None:
        return None, "No telemetry is available to establish window ownership."
    owner = observation.window_owners().get(normalize_control_label(window), {})
    if owner.get("belongs_to") != "you" or not owner.get("owner_id"):
        return (
            None,
            f"Window {window!r} does not resolve to one exact squad inventory owner.",
        )
    owner_id = str(owner["owner_id"])
    character = next(
        (candidate for candidate in telemetry.squad if candidate.id == owner_id),
        None,
    )
    if (
        character is None
        or character.selected is not True
        or owner_id not in telemetry.ui.selected_character_ids
    ):
        return (
            None,
            f"The exact owner of window {window!r} is not in the current selection.",
        )
    return character, None


def _single_selected_player_inventory_owner(
    observation: Observation,
) -> tuple[CharacterState | None, str | None]:
    """Resolve the one player-owned inventory paired with an open trade."""

    owners = observation.window_owners()
    player_windows = [
        caption
        for caption in observation.open_window_captions()
        if owners.get(normalize_control_label(caption), {}).get("belongs_to") == "you"
    ]
    if len(player_windows) != 1:
        return (
            None,
            "Trade delivery requires one exact player-owned inventory window; "
            f"observed {len(player_windows)}.",
        )
    return _selected_player_window_owner(observation, player_windows[0])


def _world_interface_error(observation: Observation) -> str | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return "No telemetry is available to confirm the world interface."
    if (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.modal_open is not False
        or telemetry.ui.dialogue_open is not False
    ):
        return (
            "The unobstructed world interface is not confirmed current; close "
            "the active modal or dialogue before issuing a world action."
        )
    return None


def _capability_condition(path: ConditionPath, *, max_age_seconds: float) -> Condition:
    return Condition(
        kind=ConditionKind.CAPABILITY,
        path=path,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=max_age_seconds,
    )


def bind_approach_dialogue_target(
    action: Action,
    observation: Observation,
) -> BoundActor | BindingFailure:
    """Bind an approach to one exact current dialogue target.

    Deliberately target-generic: the only question asked is whether the exact
    stable id is, right now, one of the people telemetry already says the agent
    could talk to. Vendor status is not consulted, so a shopkeeper and a
    wandering civilian bind identically.
    """

    if not isinstance(action, ApproachDialogueTargetAction):
        return _unbound("Action is not an approach_dialogue_target action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the approach target.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the target cannot be bound.")
    ui = telemetry.ui
    if ui.dialogue_open:
        if ui.dialogue_target_id == action.target_id:
            return _unbound(
                "Dialogue with the requested target is already open; the approach "
                "is already satisfied and must not be dispatched again."
            )
        return _unbound(
            "A dialogue with a different target is open and blocks a new approach; "
            "finish or close that dialogue first."
        )
    if ui.modal_open:
        return _unbound("A modal interface is open and blocks a new approach; close it first.")
    matches = [
        target
        for target in dialogue_targets(telemetry.nearby_entities)
        if target.id == action.target_id
    ]
    if not matches:
        return _unbound(f"Target {action.target_id!r} is not a current valid dialogue target.")
    if len(matches) > 1:
        return _unbound(
            f"Target {action.target_id!r} matches {len(matches)} current entities; "
            "an ambiguous reference fails closed."
        )
    target = matches[0]
    return BoundActor(
        reason=(
            f"Bound to current dialogue target {target.name!r} ({target.id}) at "
            f"distance {target.distance if target.distance is not None else 'unknown'}."
        ),
        target_id=target.id,
        source_revision=observation.world_revision,
    )


def bind_move_to_character(
    action: Action,
    observation: Observation,
) -> BoundActor | BindingFailure:
    """Bind a walk to one exact currently observed nearby character.

    Deliberately looser than the approach binding in one respect and no other:
    the destination need not be talkable, because going somewhere is not the
    same as talking to someone, and requiring talkability is what confined the
    agent to whichever room it started in. Everything else holds - the id must
    match exactly one current entity, and an ambiguous or absent one fails
    closed.
    """

    if not isinstance(action, MoveToCharacterAction):
        return _unbound("Action is not a move_to_character action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the destination.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the destination cannot be bound.")
    if failure := _world_interface_error(observation):
        return _unbound(failure)
    matches = [entity for entity in telemetry.nearby_entities if entity.id == action.target_id]
    if not matches:
        return _unbound(
            f"Destination {action.target_id!r} is not a currently observed nearby character."
        )
    if len(matches) > 1:
        return _unbound(
            f"Destination {action.target_id!r} matches {len(matches)} current "
            "entities; an ambiguous reference fails closed."
        )
    target = matches[0]
    return BoundActor(
        reason=(
            f"Bound to current nearby character {target.name!r} ({target.id}) at "
            f"distance {target.distance if target.distance is not None else 'unknown'}."
        ),
        target_id=target.id,
        source_revision=observation.world_revision,
    )


def bind_perform_context_action(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind one exact object/action pair from current world-target telemetry."""

    if not isinstance(action, PerformContextAction):
        return _unbound("Action is not a perform_context_action action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the context target.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the context target cannot be bound.")
    if (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.dialogue_open is not False
        or telemetry.ui.modal_open is not False
    ):
        return _unbound(
            "The modal and dialogue state is not confirmed clear, so a new world "
            "context action cannot bind; finish or close the interface first."
        )
    matches = [target for target in telemetry.world_targets if target.id == action.target_id]
    if not matches:
        return _unbound(f"Target {action.target_id!r} is not a current actionable world target.")
    if len(matches) > 1:
        return _unbound(
            f"Target {action.target_id!r} matches {len(matches)} world targets; "
            "an ambiguous reference fails closed."
        )
    target = matches[0]
    if action.context_action not in target.context_actions:
        return _unbound(
            f"Target {target.name!r} does not currently advertise context action "
            f"{action.context_action.value!r}."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound {action.context_action.value!r} to current {target.kind} "
            f"{target.name!r} ({target.id}) at distance {target.distance}."
        ),
        target_id=target.id,
        resolved_label=action.context_action.value,
        source_revision=observation.world_revision,
    )


def context_action_is_currently_authorable(observation: Observation) -> bool:
    """Whether at least one exact observed context-action pair can be authored."""

    telemetry = observation.telemetry
    return bool(
        telemetry is not None
        and not observation.telemetry_stale
        and telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
        and any(target.context_actions for target in telemetry.world_targets)
    )


def bind_command_world_target(
    action: Action,
    observation: Observation,
) -> BoundPointerTarget | BindingFailure:
    """Bind a right-click to one exact reviewed target and current screen point."""

    if not isinstance(action, CommandWorldTargetAction):
        return _unbound("Action is not a command_world_target action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the world target.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the world target cannot be bound.")
    if (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.dialogue_open is not False
        or telemetry.ui.modal_open is not False
    ):
        return _unbound(
            "The modal and dialogue state is not confirmed clear, so a world "
            "target command cannot bind; finish or close the interface first."
        )
    matches = [target for target in telemetry.world_targets if target.id == action.target_id]
    if not matches:
        return _unbound(f"Target {action.target_id!r} is not a current actionable world target.")
    if len(matches) > 1:
        return _unbound(
            f"Target {action.target_id!r} matches {len(matches)} world targets; "
            "an ambiguous reference fails closed."
        )
    target = matches[0]
    if action.context_action not in target.context_actions:
        return _unbound(
            f"Target {target.name!r} does not currently advertise context action "
            f"{action.context_action.value!r}."
        )
    point = target.screen_position
    if point is None:
        return _unbound(f"Target {target.name!r} has no current on-screen command geometry.")
    if not (0.0 <= point.x <= 1.0 and 0.0 <= point.y <= 1.0):
        return _unbound(f"Target {target.name!r} has out-of-range command geometry.")
    bounds = NormalizedPointerBounds(
        min_x=point.x,
        max_x=point.x,
        min_y=point.y,
        max_y=point.y,
    )
    return BoundPointerTarget(
        reason=(
            f"Bound {action.context_action.value!r} to current {target.kind} "
            f"{target.name!r} ({target.id}) at its observed screen position."
        ),
        target_id=target.id,
        resolved_label=action.context_action.value,
        resolved_bounds=bounds,
        source_revision=observation.world_revision,
    )


def world_target_command_is_currently_authorable(observation: Observation) -> bool:
    """Whether an exact reviewed target currently has click geometry."""

    telemetry = observation.telemetry
    return bool(
        telemetry is not None
        and not observation.telemetry_stale
        and telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
        and any(
            target.context_actions and target.screen_position is not None
            for target in telemetry.world_targets
        )
    )


def bind_select_squad_member(
    action: Action,
    observation: Observation,
) -> BoundVisibleTarget | BindingFailure:
    """Bind Mouse1 to one exact squad member's current lower-HUD portrait."""

    if not isinstance(action, SelectSquadMemberAction):
        return _unbound("Action is not a select_squad_member action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the squad member.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the squad member cannot be bound.")
    if (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.dialogue_open is not False
        or telemetry.ui.modal_open is not False
    ):
        return _unbound(
            "The modal and dialogue state is not confirmed clear, so a squad "
            "member selection cannot bind; finish or close the interface first."
        )
    matches = [character for character in telemetry.squad if character.id == action.target_id]
    if not matches:
        return _unbound(f"Target {action.target_id!r} is not a current squad member.")
    if len(matches) > 1:
        return _unbound(
            f"Target {action.target_id!r} matches {len(matches)} squad members; "
            "an ambiguous reference fails closed."
        )
    target = matches[0]
    same_name = [
        character
        for character in telemetry.squad
        if normalize_control_label(character.name) == normalize_control_label(target.name)
    ]
    if len(same_name) != 1:
        return _unbound(
            f"Squad member name {target.name!r} identifies {len(same_name)} "
            "current members; portrait identity is ambiguous."
        )
    portrait_matches = [
        control
        for control in (telemetry.ui.visible_controls or [])
        if control.role == "text"
        and normalize_control_label(control.label) == normalize_control_label(target.name)
        and control.bounds.min_y >= 0.75
    ]
    if len(portrait_matches) != 1:
        return _unbound(
            f"Squad member {target.name!r} has {len(portrait_matches)} "
            "unambiguous lower-HUD portrait labels; exactly one is required."
        )
    portrait = portrait_matches[0]
    return BoundVisibleTarget(
        reason=(
            f"Bound Mouse1 selection to current squad member {target.name!r} "
            f"({target.id}) through its exact current lower-HUD portrait."
        ),
        target_id=target.id,
        resolved_label=portrait.label,
        resolved_role=portrait.role,
        resolved_bounds=portrait.bounds.model_copy(deep=True),
        source_revision=observation.world_revision,
    )


def squad_member_selection_is_currently_authorable(
    observation: Observation,
) -> bool:
    """Whether any exact current squad member has one unambiguous portrait."""

    telemetry = observation.telemetry
    return bool(
        telemetry is not None
        and not observation.telemetry_stale
        and telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
        and any(
            bind_select_squad_member(
                SelectSquadMemberAction(target_id=character.id),
                observation,
            ).bound
            for character in telemetry.squad
        )
    )


def bind_select_squad_member_exact(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind one native selection request to an exact stable squad identity."""

    if not isinstance(action, SelectSquadMemberExactAction):
        return _unbound("Action is not a select_squad_member_exact action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the squad member.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the squad member cannot be bound.")
    if (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.dialogue_open is not False
        or telemetry.ui.modal_open is not False
    ):
        return _unbound(
            "The modal and dialogue state is not confirmed clear, so a squad "
            "member selection cannot bind; finish or close the interface first."
        )
    selected_ids = telemetry.ui.selected_character_ids
    if not selected_ids or telemetry.ui.selected_character_id not in selected_ids:
        return _unbound(
            "Native squad selection requires one or more exact current squad "
            "selections as its causal basis."
        )
    matches = [character for character in telemetry.squad if character.id == action.target_id]
    if len(matches) != 1:
        return _unbound(
            f"Target {action.target_id!r} identifies {len(matches)} current squad "
            "members; exact native selection fails closed."
        )
    target = matches[0]
    return BoundNamedTarget(
        reason=(
            f"Bound exact native squad selection to {target.name!r} ({target.id}); "
            f"all {len(selected_ids)} current selected identities are carried "
            "as the causal basis."
        ),
        target_id=target.id,
        resolved_label=target.name,
        source_revision=observation.world_revision,
    )


def exact_squad_member_selection_is_currently_authorable(
    observation: Observation,
) -> bool:
    telemetry = observation.telemetry
    return bool(
        telemetry is not None
        and not observation.telemetry_stale
        and bool(telemetry.ui.selected_character_ids)
        and telemetry.ui.selected_character_id in telemetry.ui.selected_character_ids
        and any(
            bind_select_squad_member_exact(
                SelectSquadMemberExactAction(target_id=character.id),
                observation,
            ).bound
            for character in telemetry.squad
        )
    )


def bind_rotate_camera(
    action: Action,
    observation: Observation,
) -> BoundNamedOperation | BindingFailure:
    """Bind one bounded camera yaw only while the unobstructed world is current."""

    if not isinstance(action, RotateCameraAction):
        return _unbound("Action is not a rotate_camera action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind camera rotation.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so camera rotation cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("Kenshi has no loaded world to rotate.")
    if (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.dialogue_open is not False
        or telemetry.ui.modal_open is not False
    ):
        return _unbound(
            "The unobstructed world screen is not confirmed current, so camera "
            "rotation cannot bind."
        )
    return BoundNamedOperation(
        reason=(
            f"Bound one bounded camera rotation {action.direction.value!r} "
            "against the current world screen."
        ),
        resolved_label=action.direction.value,
        source_revision=observation.world_revision,
    )


def camera_rotation_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    return bool(
        telemetry is not None
        and not observation.telemetry_stale
        and telemetry.game.loaded is True
        and telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
    )


def _bind_exact_natural_resource(
    target_id: str,
    observation: Observation,
) -> tuple[WorldTarget | None, BindingFailure | None]:
    telemetry = observation.telemetry
    if telemetry is None:
        return None, _unbound("No telemetry is available to bind the resource.")
    if observation.telemetry_stale:
        return None, _unbound("Telemetry is stale, so the resource cannot be bound.")
    matches = [target for target in telemetry.world_targets if target.id == target_id]
    if not matches:
        return None, _unbound(f"Target {target_id!r} is not a current natural-resource target.")
    if len(matches) > 1:
        return None, _unbound(
            f"Target {target_id!r} matches {len(matches)} world targets; an "
            "ambiguous reference fails closed."
        )
    target = matches[0]
    if (
        target.kind != "natural_resource"
        or ContextActionKind.OPERATE not in target.context_actions
        or target.default_task != "operate_machinery"
    ):
        return None, _unbound(
            f"Target {target.name!r} does not currently advertise the reviewed "
            "natural-resource operation."
        )
    return target, None


def bind_produce_resource_output(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind retained production to one exact reviewed natural resource."""

    if not isinstance(action, ProduceResourceOutputAction):
        return _unbound("Action is not a produce_resource_output action.")
    telemetry = observation.telemetry
    target, failure = _bind_exact_natural_resource(action.target_id, observation)
    if failure is not None:
        return failure
    assert telemetry is not None and target is not None
    if (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.modal_open is not False
        or telemetry.ui.dialogue_open is not False
    ):
        return _unbound(
            "The world interface is not confirmed clear, so resource production cannot bind."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound retained production to {target.name!r} ({target.id}); task "
            "acceptance is progress and output inventory is terminal proof."
        ),
        target_id=target.id,
        resolved_label="produce_output",
        source_revision=observation.world_revision,
    )


def resource_production_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    return bool(
        telemetry is not None
        and not observation.telemetry_stale
        and telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
        and any(
            target.kind == "natural_resource"
            and ContextActionKind.OPERATE in target.context_actions
            and target.default_task == "operate_machinery"
            for target in telemetry.world_targets
        )
    )


def bind_harvest_resource(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind one bounded production/transfer option to an exact actor and source."""

    if not isinstance(action, HarvestResourceAction):
        return _unbound("Action is not a harvest_resource action.")
    telemetry = observation.telemetry
    target, failure = _bind_exact_natural_resource(action.target_id, observation)
    if failure is not None:
        return failure
    assert telemetry is not None and target is not None
    if (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.modal_open is not False
        or telemetry.ui.dialogue_open is not False
    ):
        return _unbound(
            "The world interface is not confirmed clear, so a harvest option cannot begin."
        )
    selected = [
        character
        for character in telemetry.squad
        if character.selected and character.id == action.actor_id
    ]
    if (
        len(selected) != 1
        or telemetry.ui.selected_character_id != action.actor_id
        or telemetry.ui.selected_character_ids != [action.actor_id]
        or selected[0].alive is not True
        or selected[0].conscious is not True
        or selected[0].down is not False
        or selected[0].in_combat is not False
        or selected[0].inventory_complete is not True
    ):
        return _unbound(
            "Harvesting requires the exact selected actor to be alive, conscious, "
            "standing, out of combat, and backed by a complete inventory export."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound a yield of {action.quantity} from {target.name!r} ({target.id}) "
            f"into exact selected actor {selected[0].name!r} ({action.actor_id})."
        ),
        target_id=target.id,
        resolved_label=target.name,
        source_revision=observation.world_revision,
    )


def harvest_resource_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    selected = [
        character
        for character in telemetry.squad
        if character.selected
        and character.alive is True
        and character.conscious is True
        and character.down is False
        and character.in_combat is False
        and character.inventory_complete is True
    ]
    return bool(
        telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
        and len(selected) == 1
        and telemetry.ui.selected_character_id == selected[0].id
        and telemetry.ui.selected_character_ids == [selected[0].id]
        and any(
            target.kind == "natural_resource"
            and ContextActionKind.OPERATE in target.context_actions
            and target.default_task == "operate_machinery"
            for target in telemetry.world_targets
        )
    )


def bind_open_context_inventory(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind native UI opening to one exact resource handle."""

    if not isinstance(action, OpenContextInventoryAction):
        return _unbound("Action is not an open_context_inventory action.")
    telemetry = observation.telemetry
    target, failure = _bind_exact_natural_resource(action.target_id, observation)
    if failure is not None:
        return failure
    assert telemetry is not None and target is not None
    already_open = (
        telemetry.ui.active_screen == "inventory"
        and telemetry.ui.context_inventory_target_id == action.target_id
        and telemetry.ui.dialogue_open is False
    )
    if not already_open and (
        telemetry.ui.active_screen != "world"
        or telemetry.ui.modal_open is not False
        or telemetry.ui.dialogue_open is not False
    ):
        return _unbound(
            "A different modal, dialogue, or inventory is open; close it before "
            "opening this exact resource inventory."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound the contextual inventory to {target.name!r} ({target.id})"
            + ("; it is already open." if already_open else ".")
        ),
        target_id=target.id,
        resolved_label=target.name,
        source_revision=observation.world_revision,
    )


def context_inventory_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    if telemetry.ui.dialogue_open is not False:
        return False
    clear_world = telemetry.ui.active_screen == "world" and telemetry.ui.modal_open is False
    exact_inventory_target = telemetry.ui.context_inventory_target_id
    exact_inventory = telemetry.ui.active_screen == "inventory" and any(
        target.id == exact_inventory_target
        and target.kind == "natural_resource"
        and ContextActionKind.OPERATE in target.context_actions
        and target.default_task == "operate_machinery"
        for target in telemetry.world_targets
    )
    return bool(
        (clear_world or exact_inventory)
        and any(
            target.kind == "natural_resource"
            and ContextActionKind.OPERATE in target.context_actions
            for target in telemetry.world_targets
        )
    )


def bind_move_in_direction(
    action: Action,
    observation: Observation,
) -> BoundNamedOperation | BindingFailure:
    """Bind a directional walk to the character actually doing the walking.

    There is no external reference to resolve - the intended destination is
    derived from where the character already is - so this binder proves only
    that the game is running and somebody is selected to receive the order.
    The monitored option then owns the walk through the exact keyed native
    acknowledgement rather than inventing an external target.
    """

    if not isinstance(action, MoveInDirectionAction):
        return _unbound("Action is not a move_in_direction action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the walk.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the walk cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("The game is not loaded, so no order can be given.")
    if failure := _world_interface_error(observation):
        return _unbound(failure)
    selected = [character for character in telemetry.squad if character.selected]
    if len(selected) != 1:
        return _unbound(
            f"{len(selected)} characters are selected; exactly one must be, so the "
            "order has an unambiguous walker."
        )
    walker = selected[0]
    return BoundNamedOperation(
        reason=(
            f"Bound to selected character {walker.name!r} walking "
            f"{action.distance_units:.0f} units on bearing "
            f"{action.bearing_degrees:.0f}."
        ),
        resolved_label=walker.name,
        source_revision=observation.world_revision,
    )


def bind_travel_to_map_destination(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind long travel to one exact currently known settlement marker."""

    if not isinstance(action, TravelToMapDestinationAction):
        return _unbound("Action is not a travel_to_map_destination action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind map travel.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so map travel cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("The game is not loaded, so map travel cannot begin.")
    if failure := _world_interface_error(observation):
        return _unbound(failure)
    selected = [character for character in telemetry.squad if character.selected]
    if not selected:
        return _unbound("No squad members are selected to receive the travel order.")
    matches = [
        destination
        for destination in telemetry.known_map_destinations
        if destination.id == action.destination_id
    ]
    if not matches:
        return _unbound(
            f"Destination {action.destination_id!r} is not a currently known map destination."
        )
    if len(matches) > 1:
        return _unbound(
            f"Destination {action.destination_id!r} matches {len(matches)} known "
            "markers; an ambiguous reference fails closed."
        )
    destination = matches[0]
    location_authoritative = "game.location.identity" in telemetry.capabilities
    if map_destination_already_reached(
        destination,
        current_location_id=telemetry.game.location_id,
        inside_town_walls=telemetry.game.inside_town_walls,
        location_authoritative=location_authoritative,
    ):
        boundary = (
            "already inside" if telemetry.game.inside_town_walls is True else "already within"
        )
        return _unbound(
            f"Destination {destination.name!r} ({destination.id}) is {boundary} "
            "the exact current town; another map-scale order would repeat a "
            "reached destination rather than make progress."
        )
    if not map_destination_travel_available(
        destination,
        current_location_id=telemetry.game.location_id,
        inside_town_walls=telemetry.game.inside_town_walls,
        location_authoritative=location_authoritative,
    ):
        return _unbound(
            f"Destination {destination.name!r} ({destination.id}) is already local "
            f"at map distance {destination.distance:.0f}; another map-scale order "
            "would repeat a reached destination rather than make progress."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound {len(selected)} selected squad member(s) to long travel to "
            f"known map destination {destination.name!r} ({destination.id}) at "
            f"map distance {destination.distance:.0f}."
        ),
        target_id=destination.id,
        resolved_label=destination.name,
        source_revision=observation.world_revision,
    )


def map_travel_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None:
        return False
    location_authoritative = "game.location.identity" in telemetry.capabilities
    return bool(
        not observation.telemetry_stale
        and telemetry.game.loaded is True
        and bool([character for character in telemetry.squad if character.selected])
        and any(
            map_destination_travel_available(
                destination,
                current_location_id=telemetry.game.location_id,
                inside_town_walls=telemetry.game.inside_town_walls,
                location_authoritative=location_authoritative,
            )
            for destination in telemetry.known_map_destinations
        )
    )


def bind_regroup_with_squad_member(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind one selected actor to one distinct, current squadmate."""

    if not isinstance(action, RegroupWithSquadMemberAction):
        return _unbound("Action is not a regroup_with_squad_member action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind squad regrouping.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so squad regrouping cannot bind.")
    if telemetry.game.loaded is not True:
        return _unbound("The game is not loaded, so squad regrouping cannot begin.")
    if failure := _world_interface_error(observation):
        return _unbound(failure)
    if telemetry.game.paused is not True:
        return _unbound(
            "Squad regrouping begins from a confirmed pause so its monitored "
            "option owns the complete playback boundary."
        )
    actor_matches = [
        member for member in telemetry.squad if member.id == action.actor_id and member.selected
    ]
    if (
        len(actor_matches) != 1
        or telemetry.ui.selected_character_id != action.actor_id
        or telemetry.ui.selected_character_ids != [action.actor_id]
    ):
        return _unbound("actor_id must be the one exact currently selected squad member.")
    actor = actor_matches[0]
    if actor.alive is not True or actor.conscious is not True or actor.down is True:
        return _unbound(f"Selected actor {actor.name!r} is not confirmed able to travel.")
    if action.target_id == action.actor_id:
        return _unbound("A squad member cannot regroup with itself.")
    target_matches = [member for member in telemetry.squad if member.id == action.target_id]
    if len(target_matches) != 1:
        return _unbound(
            f"target_id must identify one exact current squad member; found "
            f"{len(target_matches)} matches."
        )
    target = target_matches[0]
    if target.alive is not True:
        return _unbound(f"Target squad member {target.name!r} is not confirmed alive.")
    if actor.position is None or target.position is None:
        return _unbound("Both squad members need current world positions for regrouping.")
    dx = actor.position.x - target.position.x
    dz = actor.position.z - target.position.z
    if dx * dx + dz * dz <= SQUAD_REGROUP_ARRIVAL_DISTANCE**2:
        return _unbound(
            f"{actor.name!r} is already within the native arrival boundary of {target.name!r}."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound selected actor {actor.name!r} ({actor.id}) to regroup with "
            f"current squadmate {target.name!r} ({target.id}); native code owns "
            "global lookup, pathing, playback, and arrival."
        ),
        target_id=target.id,
        resolved_label=target.name,
        source_revision=observation.world_revision,
    )


def squad_regroup_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None:
        return False
    selected = [member for member in telemetry.squad if member.selected]
    if len(selected) != 1:
        return False
    actor = selected[0]
    return any(
        bind_regroup_with_squad_member(
            RegroupWithSquadMemberAction(
                actor_id=actor.id,
                target_id=target.id,
            ),
            observation,
        ).bound
        for target in telemetry.squad
        if target.id != actor.id
    )


def bind_exit_current_building(
    action: Action,
    observation: Observation,
) -> BoundNamedOperation | BindingFailure:
    """Bind a parameter-free exit request to one selected indoor character."""

    if not isinstance(action, ExitCurrentBuildingAction):
        return _unbound("Action is not an exit_current_building action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the building exit.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the building exit cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("The game is not loaded, so no exit order can be given.")
    if failure := _world_interface_error(observation):
        return _unbound(failure)
    selected = [character for character in telemetry.squad if character.selected]
    if len(selected) != 1:
        return _unbound(
            f"{len(selected)} characters are selected; exactly one must be, so the "
            "building occupant is unambiguous."
        )
    character = selected[0]
    if character.indoors is not True:
        return _unbound(
            f"Selected character {character.name!r} is not confirmed inside a building."
        )
    return BoundNamedOperation(
        reason=(
            f"Bound a controller-owned exit from the selected character "
            f"{character.name!r}'s current building."
        ),
        resolved_label=character.name,
        source_revision=observation.world_revision,
    )


def bind_visible_control(
    action: Action,
    observation: Observation,
) -> BoundVisibleControl | BindingFailure:
    """Bind a control activation to exactly one currently advertised control.

    Bounds are read from telemetry, never authored. Any duplicate of the same
    label and role fails closed rather than picking the first, because "the
    button that says X" is not a reference when two of them say X.
    """

    if not isinstance(action, ActivateVisibleControlAction):
        return _unbound("Action is not an activate_visible_control action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the visible control.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the control cannot be bound.")
    if VISIBLE_CONTROLS_CAPABILITY not in telemetry.capabilities:
        return _unbound(
            f"Capability {VISIBLE_CONTROLS_CAPABILITY!r} is unavailable, so visible "
            "controls are unknown rather than absent."
        )
    controls = telemetry.ui.visible_controls
    if controls is None:
        return _unbound("The interface reports no current visible-control set.")
    wanted = normalize_control_label(action.exact_label)
    matches = [
        control
        for control in controls
        if normalize_control_label(control.label) == wanted
        and control.role == action.role
        # An empty `window` means "do not narrow"; naming one disambiguates a
        # label that several open windows share, such as a close button.
        and (not action.window or control.window == action.window)
    ]
    if any(is_runtime_owned_visible_control(control) for control in matches):
        return _unbound(
            "The matching control is a runtime-owned time widget; author a "
            "semantic gameplay intent and let its monitored option own playback."
        )
    if not matches:
        return _unbound(f"No current {action.role} control matches label {action.exact_label!r}.")
    if len(matches) > 1:
        windows = sorted({control.window or "<no window>" for control in matches})
        return _unbound(
            f"{len(matches)} current {action.role} controls match label "
            f"{action.exact_label!r} (in {windows}); an ambiguous reference fails "
            "closed. Name the window to narrow it."
        )
    control = matches[0]
    return BoundVisibleControl(
        reason=(
            f"Bound to exactly one current {control.role} control "
            f"{control.label!r} at its observed bounds."
        ),
        resolved_label=control.label,
        resolved_role=control.role,
        resolved_bounds=control.bounds.model_copy(deep=True),
        source_revision=observation.world_revision,
    )


def bind_respond_to_immediate_threat(
    action: Action,
    observation: Observation,
) -> BoundActor | BindingFailure:
    if not isinstance(action, RespondToImmediateThreatAction):
        return _unbound("Action is not a respond_to_immediate_threat action.")
    if reason := threat_response_authority_error(action, observation):
        return _unbound(reason)
    return BoundActor(
        reason=(
            f"Bound selected actor {action.actor_id!r} and strategy "
            f"{action.strategy.value!r} to one runtime-owned immediate-threat response."
        ),
        target_id=action.actor_id,
        source_revision=observation.world_revision,
    )


def threat_response_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None:
        return False
    selected = [member for member in telemetry.squad if member.selected]
    if len(selected) != 1:
        return False
    probe = RespondToImmediateThreatAction(
        actor_id=selected[0].id,
        strategy=ThreatResponseStrategy.ENGAGE,
    )
    return threat_response_authority_error(probe, observation) is None


ITEM_ROLE = "item"


def _window_belongs_to(window: str, owner_name: str | None) -> bool:
    """Whether an inventory window caption names this character.

    Kenshi captions inventory windows in upper case ("HEP") while the character
    is named "Hep", so this has to be case-insensitive; comparing exactly would
    reject every real window.
    """

    if not owner_name:
        return False
    return normalize_control_label(window) == normalize_control_label(owner_name)


def _bind_item_cell(
    cell_label: str,
    observation: Observation,
    *,
    window: str | None = None,
    item_base_value: int | None = None,
    item_name: str | None = None,
    item_quantity: int | None = None,
    section: str | None = None,
    require_selected_inventory_accepts_item: bool = False,
) -> BoundItemCell | BindingFailure:
    """Resolve one exact inventory or shop cell from current telemetry.

    `window` narrows the search to one open inventory. A trade screen shows two
    side by side and the cell ordinals run across both, so on that screen the
    label alone is not a reference to anything in particular.

    `item_base_value` narrows further, because a label is not unique either: the
    live Barman stocks five cells all labelled "Tooth Pick", two priced 809 and
    three priced 390 - different weapon grades wearing the same name. The price
    separates them.
    """

    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the item cell.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the item cell cannot be bound.")
    if VISIBLE_CONTROLS_CAPABILITY not in telemetry.capabilities:
        return _unbound(
            f"Capability {VISIBLE_CONTROLS_CAPABILITY!r} is unavailable, so item "
            "cells are unknown rather than absent."
        )
    wanted = normalize_control_label(cell_label)
    matches = [
        control
        for control in (telemetry.ui.visible_controls or [])
        if control.role == ITEM_ROLE
        and normalize_control_label(control.label) == wanted
        and (window is None or control.window == window)
        and (item_name is None or control.item_name == item_name)
        and (item_quantity is None or control.item_quantity == item_quantity)
        and (section is None or control.section == section)
    ]
    if not matches:
        where = f" in window {window!r}" if window is not None else ""
        return _unbound(f"No current item cell matches {cell_label!r}{where}.")

    if require_selected_inventory_accepts_item and any(
        control.selected_inventory_accepts_item is not True for control in matches
    ):
        return _unbound(
            f"The open player inventory does not explicitly accept "
            f"{item_name or cell_label!r}; the transfer cannot be authorized."
        )

    if len(matches) > 1 and item_base_value is not None:
        # A tie-breaker between cells that share a name - the Barman stocks five
        # "Tooth Pick" at two grades. Narrowing stays permissive here even
        # though the price is now exact, because the caller already rejects a
        # mismatched price with the real one named; refusing here as well would
        # report the cell as missing rather than mispriced, which is the less
        # actionable of the two failures.
        narrowed = [control for control in matches if control.item_base_value == item_base_value]
        if narrowed:
            matches = narrowed

    if len(matches) > 1:
        # Ambiguity only matters when the candidates differ in a way that could
        # change the outcome. A shop holding five identical Tooth Picks at the
        # same base value in the same window offers five interchangeable cells, and
        # refusing all of them makes stacked stock unbuyable - which is what the
        # live Barman's shelf actually did. Distinguishable duplicates still
        # fail closed.
        distinct = {
            (control.window, control.item_name, control.item_base_value) for control in matches
        }
        if len(distinct) > 1 or matches[0].item_name is None:
            return _unbound(
                f"{len(matches)} current item cells match {cell_label!r} and they "
                "are not interchangeable; an ambiguous reference fails closed."
            )
    cell = matches[0]
    return BoundItemCell(
        reason=f"Bound to current item cell {cell.label!r} at its observed bounds.",
        resolved_label=cell.label,
        resolved_role=cell.role,
        resolved_bounds=cell.bounds.model_copy(deep=True),
        source_revision=observation.world_revision,
        item_name=cell.item_name,
        item_base_value=cell.item_base_value,
        item_sell_value=cell.item_sell_value,
        item_quantity=cell.item_quantity,
        section=cell.section,
    )


def bind_purchase_item(
    action: Action,
    observation: Observation,
) -> BoundPurchaseCell | BindingFailure:
    """Bind a purchase to one exact named seller-owned cell.

    Current producers export the cell's item facts directly. Older producers
    fall back to a tooltip bound to the same cell. `item_base_value` is the
    charge itself, so the declared price is checked against it before any input
    is sent. Deliberately says nothing about
    *what kind* of item is worth buying: that is task intent, not purchase
    safety.
    """

    if not isinstance(action, PurchaseItemAction):
        return _unbound("Action is not a purchase_item action.")
    cell = _bind_item_cell(
        action.cell_label,
        observation,
        window=action.window,
        item_base_value=action.expected_price,
    )
    if isinstance(cell, BindingFailure):
        return cell
    telemetry = observation.telemetry
    assert telemetry is not None

    # The cell itself now carries the game's own name and base value, which is
    # stronger evidence than text scraped from a tooltip - and requiring a
    # tooltip forced a hover, a replan, and a second model call before every
    # purchase. Prefer the cell's facts; fall back to the tooltip only when a
    # plug-in too old to export them is installed.
    cell_name = cell.item_name
    cell_price = cell.item_base_value
    if cell_name is not None and cell_price is not None:
        if action.item_name != cell_name:
            return _unbound(f"The cell holds {cell_name!r}, not {action.item_name!r}.")
        # This check used to be skipped, on the grounds that the asking price
        # "is never exported" and so a disagreeing `expected_price` proved
        # nothing. That was true of the old export, which shipped the sell
        # value - what the trader pays *out* - under a neutral name. The cell
        # now carries the charge itself: live-confirmed 2026-07-30, a cell
        # priced 33 debited exactly 33. So the price is checkable, and a
        # declared price that disagrees is a plan reasoning about money the
        # game never quoted it.
        #
        # The rejection names the accepted value, because a plan told only that
        # it is wrong can do nothing but guess again.
        if action.expected_price != cell_price:
            return _unbound(
                f"{cell_name!r} costs {cell_price}, not {action.expected_price}; "
                f"declare expected_price {cell_price}."
            )
    else:
        tooltip_text = telemetry.ui.tooltip_text
        tooltip_bounds = telemetry.ui.tooltip_source_bounds
        if telemetry.ui.tooltip_visible is not True or not tooltip_text or tooltip_bounds is None:
            return _unbound(
                "This plug-in does not name item cells, so a purchase needs a "
                "visible tooltip; hover the cell first."
            )
        assert cell.resolved_bounds is not None
        centre_x = (cell.resolved_bounds.min_x + cell.resolved_bounds.max_x) / 2.0
        centre_y = (cell.resolved_bounds.min_y + cell.resolved_bounds.max_y) / 2.0
        if not tooltip_bounds.contains(centre_x, centre_y):
            return _unbound(
                f"The visible tooltip does not belong to cell {action.cell_label!r}; "
                "it describes a different widget."
            )
        if action.item_name not in tooltip_text:
            return _unbound(
                f"The tooltip does not name {action.item_name!r}, so the item being "
                "bought is not the item described."
            )
        price_pattern = rf"(?<![A-Za-z0-9])c\.{action.expected_price}(?![0-9])"
        if re.search(price_pattern, tooltip_text) is None:
            return _unbound(
                f"The tooltip does not show price c.{action.expected_price}; the "
                "expected price disagrees with the interface."
            )

    seller = next(
        (entity for entity in telemetry.nearby_entities if entity.id == action.seller_id),
        None,
    )
    if (
        seller is None
        or seller.shop_inventory_owner is not True
        or seller.disposition not in (Disposition.NEUTRAL, Disposition.FRIENDLY)
    ):
        return _unbound("The seller is not a verified non-hostile shop owner.")
    # Ownership is proved by the cell sitting in the seller's own inventory
    # window, not by a count of shop traders in the world. `active_shop_trader_count`
    # is that registry - it read 5 in a bar with no trade open at all - so gating
    # on it being exactly 1 made this action unbindable everywhere.
    if not _window_belongs_to(action.window, seller.name):
        return _unbound(
            f"Window {action.window!r} is not the seller's own inventory "
            f"({seller.name!r}); the cell is not the shop's stock."
        )
    recipient, recipient_error = _single_selected_player_inventory_owner(observation)
    if recipient is None:
        assert recipient_error is not None
        return _unbound(recipient_error)

    return BoundPurchaseCell(
        reason=(
            f"Bound {action.item_name!r} to seller-owned cell "
            f"{cell.resolved_label!r} for seller {action.seller_id} at a "
            f"checked price of c.{action.expected_price}, delivered to the "
            f"exact open inventory owned by {recipient.name!r}."
        ),
        target_id=action.seller_id,
        inventory_owner_id=recipient.id,
        resolved_label=cell.resolved_label,
        resolved_role=cell.resolved_role,
        resolved_bounds=cell.resolved_bounds,
        source_revision=observation.world_revision,
        # Carry the cell's own facts through. Rebuilding the binding from
        # label, role and bounds alone dropped them, so the executor knew where
        # to click and nothing about what it was clicking - which is why an
        # unaffordable purchase could only be discovered by attempting it and
        # watching for a delta that never came.
        item_name=cell.item_name,
        item_base_value=cell.item_base_value,
        item_sell_value=cell.item_sell_value,
        item_quantity=cell.item_quantity,
        section=cell.section,
    )


def bind_sell_item(
    action: Action,
    observation: Observation,
) -> BoundSaleCell | BindingFailure:
    """Bind a sale to a cell in one exact selected squad-owned inventory.

    The one thing that must not be got wrong here is whose item is being sold.
    A trade screen shows two inventories side by side, and the cell ordinals run
    across both, so "cell 12" alone is not a reference. The window caption owns
    actor identity; primary selection and squad ordering cannot replace it.
    """

    if not isinstance(action, SellItemAction):
        return _unbound("Action is not a sell_item action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the sale.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the sale cannot be bound.")

    owner, owner_error = _selected_player_window_owner(observation, action.window)
    if owner is None:
        assert owner_error is not None
        return _unbound(owner_error)

    cell = _bind_item_cell(action.cell_label, observation, window=action.window)
    if isinstance(cell, BindingFailure):
        return cell
    if cell.item_name is not None and action.item_name != cell.item_name:
        return _unbound(f"The cell holds {cell.item_name!r}, not {action.item_name!r}.")

    buyer = next(
        (entity for entity in telemetry.nearby_entities if entity.id == action.buyer_id),
        None,
    )
    if (
        buyer is None
        or buyer.shop_inventory_owner is not True
        or buyer.disposition not in (Disposition.NEUTRAL, Disposition.FRIENDLY)
    ):
        return _unbound("The buyer is not a verified non-hostile shop owner.")
    # A trade is open with this buyer exactly when their own inventory is on
    # screen beside ours. Counting shop traders in the world says nothing about
    # that - the same misreading that made `purchase_item` unbindable.
    if not any(
        control.role == ITEM_ROLE and _window_belongs_to(control.window, buyer.name)
        for control in (telemetry.ui.visible_controls or [])
    ):
        return _unbound(
            f"No inventory window belonging to {buyer.name!r} is open, so there is "
            "no trade to sell into."
        )

    return BoundSaleCell(
        reason=(
            f"Bound to cell {cell.resolved_label!r} in {owner.name!r}'s own "
            f"inventory, holding {action.item_name!r}, sold to {action.buyer_id}."
        ),
        target_id=action.buyer_id,
        inventory_owner_id=owner.id,
        resolved_label=cell.resolved_label,
        resolved_role=cell.resolved_role,
        resolved_bounds=cell.resolved_bounds,
        source_revision=observation.world_revision,
    )


def bind_equip_item(
    action: Action,
    observation: Observation,
) -> BoundEquipmentCell | BindingFailure:
    """Bind an equip to our own cell, and only while no trade is open.

    Right-click means "equip this" in an inventory and "sell this" in a trade,
    and Kenshi decides which by whether a trade partner is registered - not by
    anything in the gesture. An equip issued with a shop window up is therefore
    a sale that no postcondition can undo. Refusing whenever a trader is active
    is the only safe reading of an ambiguous gesture.
    """

    if not isinstance(action, EquipItemAction):
        return _unbound("Action is not an equip_item action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the equip.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the equip cannot be bound.")

    # `active_shop_trader_count` counts shop traders loaded in the world - it
    # reads 5 in a bar with nothing open - so it says nothing about whether a
    # trade is in progress. What does: a trade shows the shop's inventory
    # alongside ours, so exactly one open inventory window means the only one
    # open is our own, and this right-click equips rather than sells.
    if telemetry.ui.open_inventory_windows != 1:
        return _unbound(
            f"{telemetry.ui.open_inventory_windows} inventory windows are open; "
            "equipping requires exactly one, our own. With a shop's inventory "
            "also open this same right-click sells the item instead."
        )

    owner, owner_error = _selected_player_window_owner(observation, action.window)
    if owner is None:
        assert owner_error is not None
        return _unbound(owner_error)

    cell = _bind_item_cell(action.cell_label, observation, window=action.window)
    if isinstance(cell, BindingFailure):
        return cell
    if cell.item_name is not None and action.item_name != cell.item_name:
        return _unbound(f"The cell holds {cell.item_name!r}, not {action.item_name!r}.")

    return BoundEquipmentCell(
        reason=(
            f"Bound to cell {cell.resolved_label!r} holding {action.item_name!r} in "
            f"{owner.name!r}'s own inventory, with no trade open."
        ),
        inventory_owner_id=owner.id,
        resolved_label=cell.resolved_label,
        resolved_role=cell.resolved_role,
        resolved_bounds=cell.resolved_bounds,
        source_revision=observation.world_revision,
    )


def bind_collect_resource_output(
    action: Action,
    observation: Observation,
) -> BoundResourceOutputCell | BindingFailure:
    """Bind one exact output cell to the exact open resource inventory."""

    if not isinstance(action, CollectResourceOutputAction):
        return _unbound("Action is not a collect_resource_output action.")
    telemetry = observation.telemetry
    target, failure = _bind_exact_natural_resource(action.target_id, observation)
    if failure is not None:
        return failure
    assert telemetry is not None and target is not None
    layout_error = resource_transfer_layout_error(action, observation)
    if layout_error is not None:
        return _unbound(layout_error)
    if telemetry.ui.context_inventory_target_id != action.target_id:
        return _unbound(
            "The open contextual inventory does not belong to the exact requested resource target."
        )
    if telemetry.ui.visible_controls_complete is not True:
        return _unbound(
            "The visible-control export is incomplete, so source absence or "
            "quantity cannot be proved."
        )
    if not _window_belongs_to(action.window, target.name):
        return _unbound(f"Window {action.window!r} does not name target {target.name!r}.")
    selected = [character for character in telemetry.squad if character.selected]
    if (
        len(selected) != 1
        or telemetry.ui.selected_character_ids != [selected[0].id]
        or telemetry.ui.selected_character_id != selected[0].id
        or selected[0].inventory_complete is not True
    ):
        return _unbound(
            "One exact selected character with a complete destination inventory is required."
        )
    cell = _bind_item_cell(
        action.cell_label,
        observation,
        window=action.window,
        item_name=action.item_name,
        item_quantity=action.source_quantity,
        section=action.section,
        require_selected_inventory_accepts_item=True,
    )
    if isinstance(cell, BindingFailure):
        return cell
    return BoundResourceOutputCell(
        reason=(
            f"Bound {action.source_quantity} {action.item_name!r} in exact "
            f"{action.section!r} output cell {cell.resolved_label!r} for "
            f"{target.id}; destination is selected character {selected[0].id}."
        ),
        target_id=target.id,
        resolved_label=cell.resolved_label,
        resolved_role=cell.resolved_role,
        resolved_bounds=cell.resolved_bounds,
        source_revision=observation.world_revision,
        item_name=action.item_name,
        item_quantity=action.source_quantity,
        section=action.section,
    )


def resource_output_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    target_id = telemetry.ui.context_inventory_target_id
    targets = [
        target
        for target in telemetry.world_targets
        if target.id == target_id
        and target.kind == "natural_resource"
        and ContextActionKind.OPERATE in target.context_actions
        and target.default_task == "operate_machinery"
    ]
    selected = [character for character in telemetry.squad if character.selected]
    output_controls = [
        control
        for control in (telemetry.ui.visible_controls or [])
        if control.role == ITEM_ROLE
        and control.section == "out"
        and control.item_name is not None
        and control.item_quantity is not None
        and control.item_quantity > 0
        and len(targets) == 1
        and _window_belongs_to(control.window, targets[0].name)
    ]
    return bool(
        len(targets) == 1
        and len(selected) == 1
        and telemetry.ui.selected_character_ids == [selected[0].id]
        and telemetry.ui.selected_character_id == selected[0].id
        and selected[0].inventory_complete is True
        and len(output_controls) >= 1
        and resource_transfer_layout_error(
            CollectResourceOutputAction(
                target_id=targets[0].id,
                cell_label=output_controls[0].label,
                item_name=output_controls[0].item_name or "",
                source_quantity=output_controls[0].item_quantity or 0,
                window=output_controls[0].window,
                section="out",
            ),
            observation,
        )
        is None
    )


def bind_dismiss_screen(
    action: Action,
    observation: Observation,
) -> BoundScreenDismissal | BindingFailure:
    """Bind a dismissal to the screen that is actually open right now.

    The reference is the current screen. Refusing when the planner's belief
    disagrees with observation is what stops a stray Escape from closing
    something the planner never looked at.
    """

    if not isinstance(action, DismissScreenAction):
        return _unbound("Action is not a dismiss_screen action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the current screen.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the current screen cannot be bound.")
    named_screen = (
        action.expected_screen if isinstance(action.expected_screen, GameScreen) else None
    )
    if named_screen is not None:
        open_state = screen_is_open(named_screen, telemetry)
        if open_state is None:
            return _unbound(f"Nothing observable reports whether {named_screen.value} is open.")
        if not open_state:
            return _unbound(
                f"The {named_screen.value} screen is already closed, so no "
                "dismissal input may be sent."
            )
        if not action.window:
            binding = SCREEN_BINDINGS[named_screen]
            return BoundScreenDismissal(
                reason=(
                    f"Bound the currently open {named_screen.value!r} screen to "
                    f"its exact closing toggle {binding.value!r}."
                ),
                resolved_label=named_screen.value,
                resolved_bounds=None,
                source_revision=observation.world_revision,
            )

    current = telemetry.ui.active_screen
    if current is None:
        return _unbound("The current screen is unknown, so nothing may be dismissed.")
    if current != action.expected_screen:
        return _unbound(
            f"Expected screen {action.expected_screen!r} but the interface reports "
            f"{current!r}; dismissing the wrong screen is not permitted."
        )
    if not action.window:
        if telemetry.ui.dialogue_target_id is not None:
            # Escape does not back out of a Kenshi conversation. With a dialogue
            # open it opens the ESC menu, which costs a step to undo and leaves
            # the conversation exactly where it was. A conversation ends by
            # choosing the option that ends it.
            return _unbound(
                "A Kenshi conversation is not dismissed with a key: Escape opens "
                "the ESC menu and leaves the dialogue open. End it by choosing the "
                "closing dialogue option with activate_visible_control."
            )
        # A keyed screen with no window of its own is dismissed with the key.
        return BoundScreenDismissal(
            reason=f"Bound to the currently open {current!r} screen.",
            resolved_label=current,
            resolved_bounds=None,
            source_revision=observation.world_revision,
        )

    # A named window is closed by its own close box, positioned from the rect
    # the window itself reports.
    owned = [
        control
        for control in (telemetry.ui.visible_controls or [])
        if control.window == action.window
    ]
    if not owned:
        return _unbound(
            f"No window captioned {action.window!r} is currently open, so it cannot be closed."
        )
    rect = max(
        (control.bounds for control in owned),
        key=lambda b: (b.max_x - b.min_x) * (b.max_y - b.min_y),
    )
    return BoundScreenDismissal(
        reason=(
            f"Bound to the {action.window!r} window on the {current!r} screen; its "
            "close box follows the window's own observed rect."
        ),
        resolved_label=action.window,
        resolved_bounds=rect.model_copy(deep=True),
        source_revision=observation.world_revision,
    )


def bind_use_game_binding(
    action: Action,
    observation: Observation,
) -> BoundNamedOperation | BindingFailure:
    """Bind a keypress to the game actually being in a state to receive it.

    There is no widget to resolve here - the reference is the game itself. What
    still has to be proved is that Kenshi is loaded and listening, because a
    keystroke sent at a loading screen or a dead telemetry stream vanishes with
    no evidence either way, which is exactly the silent failure this action
    exists to replace.
    """

    if not isinstance(action, UseGameBindingAction):
        return _unbound("Action is not a use_game_binding action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available, so the game cannot be bound.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the game cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("Kenshi has no loaded game to receive a binding.")
    if (
        action.binding is GameBinding.QUICKSAVE
        and QUICKSAVE_COMPLETION_CAPABILITY not in telemetry.capabilities
    ):
        return _unbound(
            "Quicksave requires controller-owned completion evidence for the exact quicksave slot."
        )
    if action.binding is GameBinding.QUICKLOAD and (
        telemetry.identity_session_id is None
        or "identity.stable_handles" not in telemetry.capabilities
    ):
        return _unbound(
            "Quickload requires a current stable identity session so completion "
            "can be attributed to a new loaded session."
        )
    if action.binding in TIME_GAME_BINDINGS:
        return _unbound(
            "Raw time bindings are runtime-owned mechanics; author a semantic "
            "gameplay intent whose monitored option owns playback."
        )
    mapped_input = GAME_BINDING_KEYS.get(action.binding)
    if mapped_input is None:
        mapped_input = GAME_BINDING_MOUSE_BUTTONS.get(action.binding)
    if mapped_input is None:
        return _unbound(f"No input is mapped for binding {action.binding.value!r}.")
    return BoundNamedOperation(
        reason=(
            f"Bound {action.binding.value!r} to the current hard-coded default "
            f"Kenshi input {mapped_input!r} on a loaded game."
        ),
        resolved_label=action.binding.value,
        source_revision=observation.world_revision,
    )


def bind_recover_camera_view(
    action: Action,
    observation: Observation,
) -> BoundCameraRecovery | BindingFailure:
    """Bind recovery to one selected character and the current world HUD.

    The model names no coordinates. The controller resolves the selected
    character's portrait, current floor, and both floor arrows from fresh
    visible-control telemetry. Any ambiguity fails closed before input.
    """

    if not isinstance(action, RecoverCameraViewAction):
        return _unbound("Action is not a recover_camera_view action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind camera recovery.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so camera recovery cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("Kenshi has no loaded game whose camera can be recovered.")
    if telemetry.ui.active_screen != "world":
        return _unbound(
            "Camera recovery is allowed only on the world screen; current screen is "
            f"{telemetry.ui.active_screen!r}."
        )
    if telemetry.ui.modal_open is not False:
        return _unbound("Camera recovery requires a confirmed closed modal.")
    if telemetry.ui.dialogue_open is not False:
        return _unbound("Camera recovery requires dialogue to be closed.")
    if VISIBLE_CONTROLS_CAPABILITY not in telemetry.capabilities:
        return _unbound("Visible-control telemetry is unavailable.")
    if CAMERA_RECOVERY_CAPABILITY not in telemetry.capabilities:
        return _unbound("Window capture/scoring is unavailable.")

    selected = [character for character in telemetry.squad if character.selected]
    if len(selected) != 1:
        return _unbound(
            f"{len(selected)} characters are selected; camera recovery requires "
            "exactly one unambiguous follow target."
        )
    character = selected[0]
    controls = telemetry.ui.visible_controls or []
    portrait_matches = [
        control
        for control in controls
        if control.role == "text"
        and normalize_control_label(control.label) == normalize_control_label(character.name)
        and control.bounds.min_y >= 0.75
    ]
    if len(portrait_matches) != 1:
        return _unbound(
            f"Selected character {character.name!r} has {len(portrait_matches)} "
            "unambiguous lower-HUD portrait labels; exactly one is required."
        )

    floor_matches: list[tuple[int, NormalizedPointerBounds]] = []
    for control in controls:
        if control.role != "text":
            continue
        match = re.fullmatch(r"\s*Floor\s+(-?\d+)\s*", control.label, re.IGNORECASE)
        if match is not None:
            floor_matches.append((int(match.group(1)), control.bounds))
    if len(floor_matches) != 1:
        return _unbound(
            f"The HUD exposes {len(floor_matches)} current floor labels; exactly one is required."
        )

    up_matches = [
        control
        for control in controls
        if control.role == "button" and control.label.casefold().endswith("_floorarrowup")
    ]
    down_matches = [
        control
        for control in controls
        if control.role == "button" and control.label.casefold().endswith("_floorarrowdown")
    ]
    if len(up_matches) != 1 or len(down_matches) != 1:
        return _unbound(
            "The HUD must expose exactly one floor-up and one floor-down button; "
            f"found {len(up_matches)} up and {len(down_matches)} down."
        )

    portrait = portrait_matches[0]
    floor = floor_matches[0][0]
    return BoundCameraRecovery(
        reason=(
            f"Bound camera recovery to selected character {character.name!r} "
            f"({character.id}), portrait {portrait.label!r}, and floor {floor}."
        ),
        target_id=character.id,
        resolved_label=portrait.label,
        resolved_role=portrait.role,
        resolved_bounds=portrait.bounds.model_copy(deep=True),
        source_revision=observation.world_revision,
        selected_character_name=character.name,
        floor=floor,
        floor_up_bounds=up_matches[0].bounds.model_copy(deep=True),
        floor_down_bounds=down_matches[0].bounds.model_copy(deep=True),
    )


def bind_scroll_screen(
    action: Action,
    observation: Observation,
) -> BoundVisibleControl | BindingFailure:
    """Bind a scroll to the observed bounds of one currently open window.

    The reference is the window, not a coordinate: the scroll lands at the
    centre of the rectangle its own controls occupy. A window with nothing
    exported in it fails closed rather than scrolling the world behind it,
    which is what a bare coordinate would have done.
    """

    if not isinstance(action, ScrollScreenAction):
        return _unbound("Action is not a scroll_screen action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the window.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the window cannot be bound.")
    if VISIBLE_CONTROLS_CAPABILITY not in telemetry.capabilities:
        return _unbound(
            f"Capability {VISIBLE_CONTROLS_CAPABILITY!r} is unavailable, so open "
            "windows are unknown rather than absent."
        )
    controls = telemetry.ui.visible_controls
    if not controls:
        return _unbound("The interface reports no current visible-control set.")
    members = [control for control in controls if control.window == action.window]
    if not members:
        return _unbound(
            f"No control currently belongs to a window named {action.window!r}, "
            "so there is nothing to scroll."
        )
    bounds = NormalizedPointerBounds(
        min_x=min(control.bounds.min_x for control in members),
        min_y=min(control.bounds.min_y for control in members),
        max_x=max(control.bounds.max_x for control in members),
        max_y=max(control.bounds.max_y for control in members),
    )
    return BoundVisibleControl(
        reason=(
            f"Bound to window {action.window!r}, whose {len(members)} exported "
            "controls span the region to scroll."
        ),
        resolved_label=action.window,
        resolved_role="window",
        resolved_bounds=bounds,
        source_revision=observation.world_revision,
    )


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """Everything the runtime must know to route one typed action safely."""

    kind: str
    version: str
    operation_type: type[BaseModel]
    summary: str
    argument_source: str
    allowed_control_modes: frozenset[ControlMode]
    required_capabilities: frozenset[str]
    capability_aliases: frozenset[str]
    pointer_class: PointerActionClass
    native_assisted: bool
    risk: OperationRisk
    max_primitive_actions: int
    reference_fields: tuple[str, ...]
    idempotency: IdempotencyPolicy
    execution: OperationExecution
    receipt_kind: str
    bind: Callable[[Action, Observation], OperationBinding]
    handler_key: str = ""
    emits_world_command: bool = True
    requires_fresh_telemetry: bool = True
    # Selection cardinality is action-specific. Most character operations own
    # one exact actor, while selection collapse and ordinary group travel bind
    # the complete current selected set.
    selection_requirement: SelectionRequirement = SelectionRequirement.NONE
    derive_risk: RiskFactory | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    derive_primitive_action_bound: PrimitiveActionBoundFactory | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # The handler itself returns a typed terminal verdict based on evidence it
    # owns, so a caller must not invent a redundant postcondition.
    controller_verified: bool = False
    # Atomic native actions declare their accepted terminal reasons here. The
    # executor consumes this generically, so adding a new exact native command
    # cannot succeed in the controller and then fall through as untyped.
    native_terminal_success_reasons: frozenset[str] = frozenset()
    # Reasons that prove Kenshi adopted the order without carrying it out. The
    # character holds an AI goal it can only act on while the world runs, so a
    # terminal here is acceptance and the runtime still owes it a running world.
    native_task_started_reasons: frozenset[str] = frozenset()
    # A deterministic effect derived from the action and its immediate
    # pre-dispatch observation. `None` means this operation has no effect-level
    # condition; an empty tuple means the runtime owns one but the required
    # baseline is unavailable, which fails closed before dispatch.
    derive_completion_conditions: CompletionConditionFactory | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    derive_terminal: TerminalFactory | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Optional observation-specific visibility. Capabilities answer whether an
    # action kind exists; this answers whether its declared argument source
    # contains at least one currently bindable choice.
    authorable_when: Callable[[Observation], bool] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.handler_key:
            raise ValueError(f"Operation {self.kind!r} must declare one handler key.")

    def risk_for(self, action: Action) -> OperationRisk:
        """Resolve risk from this exact action without weakening the ceiling."""

        risk = self.derive_risk(action) if self.derive_risk is not None else self.risk
        if min(risk.as_tuple()) < 0:
            raise RuntimeError(f"Operation {self.kind!r} derived negative risk.")
        return risk

    def primitive_action_bound_for(self, action: Action) -> int:
        """Resolve the exact transaction bound under the declared maximum."""

        bound = (
            self.derive_primitive_action_bound(action)
            if self.derive_primitive_action_bound is not None
            else self.max_primitive_actions
        )
        if not 0 <= bound <= self.max_primitive_actions:
            raise RuntimeError(
                f"Operation {self.kind!r} derived {bound} primitives "
                f"outside its declared 0-{self.max_primitive_actions} bound."
            )
        return bound

    def missing_capabilities(self, capabilities: set[str] | frozenset[str]) -> list[str]:
        """Required capabilities absent from an observation, alias-aware.

        A capability with accepted aliases is satisfied by any one of them, so a
        plug-in that still emits the legacy name is not treated as incapable.
        """

        missing: list[str] = []
        for required in sorted(self.required_capabilities):
            if required in capabilities:
                continue
            if required in self.capability_aliases and (
                self.capability_aliases & set(capabilities)
            ):
                continue
            missing.append(required)
        return missing

    def allows_control_mode(self, control_mode: ControlMode) -> bool:
        return control_mode in self.allowed_control_modes

    def resolve_terminal(
        self,
        action: Action,
        observation: Observation,
        *,
        selected_affordance: bool = False,
    ) -> OperationTerminal:
        """Resolve this definition's sole terminal authority before dispatch."""

        if self.derive_terminal is not None:
            terminal = self.derive_terminal(action, observation, selected_affordance)
            if terminal is not None:
                return terminal
        if self.controller_verified:
            return OperationTerminal(owner=TerminalOwner.CONTROLLER_TERMINAL)
        if self.derive_completion_conditions is None:
            return unresolved_terminal(selected_affordance=selected_affordance)
        conditions = self.derive_completion_conditions(action, observation)
        if conditions is None:
            return unresolved_terminal(selected_affordance=selected_affordance)
        return OperationTerminal(
            owner=TerminalOwner.RUNTIME_CONDITIONS,
            conditions=conditions,
        )

    def is_currently_authorable(self, observation: Observation | None) -> bool:
        if observation is None:
            return True
        telemetry = observation.telemetry
        if self.selection_requirement is not SelectionRequirement.NONE:
            if telemetry is None or observation.telemetry_stale:
                return False
            selected_ids = telemetry.ui.selected_character_ids
            primary_id = telemetry.ui.selected_character_id
            if self.selection_requirement is SelectionRequirement.EXACTLY_ONE:
                if len(selected_ids) != 1 or primary_id != selected_ids[0]:
                    return False
            elif not selected_ids or primary_id not in selected_ids:
                return False
        return self.authorable_when is None or self.authorable_when(observation)


@dataclass(frozen=True, slots=True)
class BoundOperation:
    """One current, typed operation ready for its definition's handler."""

    definition: OperationDefinition
    operation: Action
    binding: OperationBinding
    # Deterministic runtime/reflex operations have no planner offer to retain.
    # Keeping that absence explicit is more honest than forging provenance.
    affordance: BoundAffordance | None
    based_on_revision: WorldStateRevision
    identity: OperationIdentity


@dataclass(frozen=True, slots=True)
class OperationIdentity:
    """Stable semantic identity retained across fresh current-state binding.

    Rebinding is expected to advance the source revision and may move witnessed
    pointer geometry. Neither changes what was selected. Definition identity,
    authored arguments, affordance provenance, and stable domain references do.
    """

    fingerprint: str
    operation_kind: str
    definition_version: str
    handler_key: str
    operation_fingerprint: str
    affordance_fingerprint: str | None
    binding_fingerprint: str


_VOLATILE_BINDING_IDENTITY_FIELDS = frozenset(
    {
        "bound",
        "reason",
        "resolved_bounds",
        "floor_up_bounds",
        "floor_down_bounds",
        "source_revision",
    }
)


def _identity_json(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_identity_json(item) for item in value]
    if isinstance(value, list):
        return [_identity_json(item) for item in value]
    return value


def _fingerprint(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{prefix}-{sha256(encoded.encode('utf-8')).hexdigest()[:20]}"


def operation_identity(
    definition: OperationDefinition,
    operation: Action,
    binding: OperationBinding,
    affordance: BoundAffordance | None,
) -> OperationIdentity:
    """Build the one immutable identity used at scheduling and dispatch."""

    if isinstance(binding, BindingFailure):
        raise ValueError("An unbound operation cannot have execution identity.")
    operation_payload = operation.model_dump(mode="json")
    operation_hash = _fingerprint("request", operation_payload)
    binding_payload = {
        "type": type(binding).__name__,
        **{
            item.name: _identity_json(getattr(binding, item.name))
            for item in fields(binding)
            if item.name not in _VOLATILE_BINDING_IDENTITY_FIELDS
        },
    }
    binding_hash = _fingerprint("binding", binding_payload)
    affordance_payload = (
        {
            "source": affordance.source.value,
            "semantic": affordance.semantic,
            "target": (
                affordance.target.model_dump(mode="json") if affordance.target is not None else None
            ),
            "parameters": [
                parameter.model_dump(mode="json") for parameter in affordance.parameters
            ],
            "execution": affordance.execution.value,
            "operation_kind": affordance.operation_kind,
        }
        if affordance is not None
        else None
    )
    affordance_hash = (
        _fingerprint("affordance", affordance_payload) if affordance_payload is not None else None
    )
    identity_payload = {
        "definition": {
            "kind": definition.kind,
            "version": definition.version,
            "handler_key": definition.handler_key,
        },
        "operation": operation_hash,
        "affordance": affordance_hash,
        "binding": binding_hash,
    }
    return OperationIdentity(
        fingerprint=_fingerprint("operation", identity_payload),
        operation_kind=operation.kind,
        definition_version=definition.version,
        handler_key=definition.handler_key,
        operation_fingerprint=operation_hash,
        affordance_fingerprint=affordance_hash,
        binding_fingerprint=binding_hash,
    )


def _bounded_trade_quantity(action: Action) -> int:
    if not isinstance(action, (PurchaseItemAction, SellItemAction)):
        raise TypeError("bounded trade cost requires a purchase or sale action")
    return action.quantity


def _bounded_trade_risk(action: Action) -> OperationRisk:
    quantity = _bounded_trade_quantity(action)
    return OperationRisk(
        pointer_actions=quantity,
        purchase_actions=quantity,
    )


def _bounded_trade_primitive_action_bound(action: Action) -> int:
    quantity = _bounded_trade_quantity(action)
    # One current-cell cursor move and one right-click per requested unit.
    return quantity * 2


def _dismissed_screen_closed(
    action: Action,
    observation: Observation,
) -> tuple[Condition, ...] | None:
    if not isinstance(action, DismissScreenAction):
        return None
    telemetry = observation.telemetry
    if action.window:
        if telemetry is None or telemetry.ui.open_inventory_windows is None:
            return ()
        return (
            Condition(
                kind=ConditionKind.FIELD,
                path=ConditionPath.TELEMETRY_UI_OPEN_INVENTORY_WINDOWS,
                operator=ConditionOperator.LESS_THAN,
                expected=telemetry.ui.open_inventory_windows,
                max_age_seconds=3.0,
            ),
        )
    if not isinstance(action.expected_screen, GameScreen):
        return None
    condition = close_screen_success_condition(action.expected_screen, telemetry)
    return (condition,) if condition is not None else ()


def _binding_transition(
    action: Action,
    observation: Observation,
) -> tuple[Condition, ...] | None:
    if not isinstance(action, UseGameBindingAction):
        return ()
    # A second literal copy of this set lived here and silently shadowed the
    # terminal table: map gained a condition while research and crafting, whose
    # table entries were identical, were still rejected at plan validation.
    # There is one source of which bindings are witnessed.
    if action.binding not in GAME_BINDING_TERMINALS:
        return None
    condition = game_binding_success_condition(action.binding, observation.telemetry)
    return (condition,) if condition is not None else ()


def _game_binding_terminal(
    action: Action,
    observation: Observation,
    selected_affordance: bool,
) -> OperationTerminal | None:
    del observation, selected_affordance
    if isinstance(action, UseGameBindingAction) and action.binding is GameBinding.QUICKSAVE:
        return OperationTerminal(owner=TerminalOwner.CONTROLLER_TERMINAL)
    return None


def _selected_squad_member(
    action: Action,
    observation: Observation,
) -> tuple[Condition, ...] | None:
    if not isinstance(action, (SelectSquadMemberAction, SelectSquadMemberExactAction)):
        return ()
    return (
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_ID,
            operator=ConditionOperator.EQUALS,
            expected=action.target_id,
            max_age_seconds=3.0,
            required_capabilities=["squad.basic"],
        ),
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_COUNT,
            operator=ConditionOperator.EQUALS,
            expected=1,
            max_age_seconds=3.0,
            required_capabilities=["squad.basic"],
        ),
    )


_RUNTIME_COGNITIVE_ACTION_TYPES = (
    NoopAction,
    StopAction,
    ConsultAdvisorAction,
    RecallMemoryAction,
    ReadFieldbookAction,
)

_RUNTIME_CONTROL_ACTION_TYPES = (
    PauseAction,
    SetSpeedAction,
    WaitAction,
)


def bind_runtime_cognitive(
    action: Action,
    observation: Observation,
) -> EmptyBinding | BindingFailure:
    """Bind zero-input runtime work to the exact observation revision."""

    if not isinstance(action, _RUNTIME_COGNITIVE_ACTION_TYPES):
        return _unbound("Action is not a runtime or cognitive operation.")
    return EmptyBinding(
        reason=f"Bound {action.kind!r} to the current runtime revision.",
        source_revision=observation.world_revision,
    )


def bind_runtime_control(
    action: Action,
    observation: Observation,
) -> EmptyBinding | BindingFailure:
    """Bind runtime playback or waiting to the current observation revision."""

    if not isinstance(action, _RUNTIME_CONTROL_ACTION_TYPES):
        return _unbound("Action is not a runtime-control operation.")
    return EmptyBinding(
        reason=f"Bound {action.kind!r} to the current runtime revision.",
        source_revision=observation.world_revision,
    )


def _runtime_cognitive_definition(
    *,
    kind: str,
    operation_type: type[BaseModel],
    summary: str,
    argument_source: str,
    handler_key: str,
    idempotency: IdempotencyPolicy = IdempotencyPolicy.SAFE_TO_RETRY,
    max_primitive_actions: int = 0,
) -> OperationDefinition:
    return OperationDefinition(
        kind=kind,
        version="1.0",
        operation_type=operation_type,
        summary=summary,
        argument_source=argument_source,
        allowed_control_modes=frozenset(ControlMode),
        required_capabilities=frozenset(),
        capability_aliases=frozenset(),
        pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
        native_assisted=False,
        risk=OperationRisk(),
        max_primitive_actions=max_primitive_actions,
        reference_fields=(),
        idempotency=idempotency,
        execution=OperationExecution.ATOMIC_HANDLER,
        receipt_kind="runtime_control",
        bind=bind_runtime_cognitive,
        handler_key=handler_key,
        emits_world_command=False,
        requires_fresh_telemetry=False,
        controller_verified=True,
    )


NOOP_DEFINITION = _runtime_cognitive_definition(
    kind="noop",
    operation_type=NoopAction,
    summary="Acknowledge that the current state requires no game input.",
    argument_source="The runtime offer supplies the optional reason.",
    handler_key="runtime.noop",
    max_primitive_actions=1,
)
STOP_DEFINITION = _runtime_cognitive_definition(
    kind="stop",
    operation_type=StopAction,
    summary="End the agent run without sending game input.",
    argument_source="The runtime offer supplies the stop reason.",
    handler_key="runtime.stop",
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    max_primitive_actions=1,
)
CONSULT_ADVISOR_DEFINITION = _runtime_cognitive_definition(
    kind="consult_advisor",
    operation_type=ConsultAdvisorAction,
    summary="Request bounded read-only strategic advice.",
    argument_source="The selected offer supplies the question and focus.",
    handler_key="cognition.advisor",
)
RECALL_MEMORY_DEFINITION = _runtime_cognitive_definition(
    kind="recall_memory",
    operation_type=RecallMemoryAction,
    summary="Read bounded continuity records without game input.",
    argument_source="The selected offer supplies the source and query.",
    handler_key="cognition.memory",
)
READ_FIELDBOOK_DEFINITION = _runtime_cognitive_definition(
    kind="read_fieldbook",
    operation_type=ReadFieldbookAction,
    summary="Read bounded fieldbook context without game input.",
    argument_source="The selected offer supplies the project or entry reference.",
    handler_key="cognition.fieldbook",
)


def _runtime_control_definition(
    *,
    kind: str,
    operation_type: type[BaseModel],
    summary: str,
    handler_key: str,
    execution: OperationExecution = OperationExecution.ATOMIC_HANDLER,
    pointer_class: PointerActionClass = PointerActionClass.COORDINATE_INDEPENDENT,
) -> OperationDefinition:
    return OperationDefinition(
        kind=kind,
        version="1.0",
        operation_type=operation_type,
        summary=summary,
        argument_source="Runtime-internal control authority.",
        allowed_control_modes=frozenset(ControlMode),
        required_capabilities=frozenset(),
        capability_aliases=frozenset(),
        pointer_class=pointer_class,
        native_assisted=False,
        risk=OperationRisk(),
        max_primitive_actions=1,
        reference_fields=(),
        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
        execution=execution,
        receipt_kind="runtime_control",
        bind=bind_runtime_control,
        handler_key=handler_key,
        derive_terminal=lambda action, observation, selected: runtime_control_terminal(action),
        requires_fresh_telemetry=False,
    )


PAUSE_DEFINITION = _runtime_control_definition(
    kind="pause",
    operation_type=PauseAction,
    summary="Request one exact paused or running playback state.",
    handler_key="runtime.pause",
)
SET_SPEED_DEFINITION = _runtime_control_definition(
    kind="set_speed",
    operation_type=SetSpeedAction,
    summary="Request one exact Kenshi playback gear.",
    handler_key="runtime.set_speed",
)
WAIT_DEFINITION = _runtime_control_definition(
    kind="wait",
    operation_type=WaitAction,
    summary="Observe for one bounded interval without sending input.",
    handler_key="runtime.wait",
)
APPROACH_DIALOGUE_TARGET_DEFINITION = OperationDefinition(
    kind="approach_dialogue_target",
    version="1.0",
    operation_type=ApproachDialogueTargetAction,
    summary=(
        "Issue Kenshi's native talk-to order for the complete current selection "
        "and one exact current target. The primary selected character remains "
        "the exact monitored speaker. The native order may open nearby dialogue "
        "while paused and otherwise owns the pathing lifecycle. Do not add a "
        "separate unpause step."
    ),
    argument_source="target_id must be an exact id from the observation's dialogue_targets.",
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_APPROACH_CAPABILITY,
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        }
    ),
    capability_aliases=NATIVE_APPROACH_CAPABILITY_ALIASES,
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.ONE_OR_MORE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=("target_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_approach",
    bind=bind_approach_dialogue_target,
    handler_key="dialogue.approach_dialogue_target",
    controller_verified=True,
)

COMMAND_WORLD_TARGET_DEFINITION = OperationDefinition(
    kind="command_world_target",
    version="1.0",
    operation_type=CommandWorldTargetAction,
    summary=(
        "Issue Kenshi's Mouse2 command to one exact current world target at a "
        "screen position exported by current telemetry and re-resolved inside "
        "the input lease."
    ),
    argument_source=(
        "target_id and context_action must be copied as an exact pair from a "
        "context_targets entry that also has screen_position."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_CONTEXT_TARGETS_CAPABILITY,
            WORLD_CONTEXT_TARGET_SCREEN_POSITIONS_CAPABILITY,
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(pointer_actions=1),
    max_primitive_actions=1,
    reference_fields=("target_id", "context_action"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_world_command",
    bind=bind_command_world_target,
    handler_key="dialogue.command_world_target",
    authorable_when=world_target_command_is_currently_authorable,
)


SELECT_SQUAD_MEMBER_DEFINITION = OperationDefinition(
    kind="select_squad_member",
    version="1.0",
    operation_type=SelectSquadMemberAction,
    summary=(
        "Select one exact current squad member with Kenshi's Mouse1 binding at "
        "that member's unique current lower-HUD portrait, re-resolved inside "
        "the input lease."
    ),
    argument_source=(
        "target_id must be copied from a current squad entry whose unique name "
        "matches exactly one current lower-HUD portrait label."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            "squad.basic",
            VISIBLE_CONTROLS_CAPABILITY,
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=OperationRisk(pointer_actions=1),
    max_primitive_actions=1,
    reference_fields=("target_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_squad_selection",
    bind=bind_select_squad_member,
    handler_key="movement.select_squad_member",
    derive_completion_conditions=_selected_squad_member,
    authorable_when=squad_member_selection_is_currently_authorable,
)


SELECT_SQUAD_MEMBER_EXACT_DEFINITION = OperationDefinition(
    kind="select_squad_member_exact",
    version="1.0",
    operation_type=SelectSquadMemberExactAction,
    summary=(
        "Select one exact current squad member by stable native identity and "
        "verify the singular resulting selection before acknowledging completion."
    ),
    argument_source="target_id is copied from one exact current squad entry.",
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_SQUAD_SELECTION_CAPABILITY,
            "identity.stable_handles",
            "squad.basic",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.ONE_OR_MORE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=1,
    reference_fields=("target_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_squad_selection",
    bind=bind_select_squad_member_exact,
    handler_key="movement.select_squad_member_exact",
    derive_completion_conditions=_selected_squad_member,
    controller_verified=True,
    native_terminal_success_reasons=frozenset({"exact_squad_member_selected"}),
    authorable_when=exact_squad_member_selection_is_currently_authorable,
)


ROTATE_CAMERA_DEFINITION = OperationDefinition(
    kind="rotate_camera",
    version="1.0",
    operation_type=RotateCameraAction,
    summary=(
        "Rotate the current world camera one bounded horizontal increment through "
        "Kenshi's held-Mouse3 rotation mode."
    ),
    argument_source="direction is left or right.",
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=False,
    risk=OperationRisk(pointer_actions=1),
    max_primitive_actions=1,
    reference_fields=(),
    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_camera_rotation",
    bind=bind_rotate_camera,
    handler_key="camera.rotate_camera",
    authorable_when=camera_rotation_is_currently_authorable,
)


PERFORM_CONTEXT_ACTION_DEFINITION = OperationDefinition(
    kind="perform_context_action",
    version="1.0",
    operation_type=PerformContextAction,
    summary=(
        "Attempt one exact contextual action advertised by a current world object. "
        "The native controller rechecks the target and reviewed semantic task, then "
        "owns execution until the selected character's AI reports that exact task "
        "and subject."
    ),
    argument_source=(
        "target_id and context_action must be copied as an exact pair from the "
        "observation's context_targets."
    ),
    # Kept as a compatibility-level "issue the task" primitive. Planning uses
    # produce_resource_output, whose terminal is actual output rather than the
    # first observed AI goal.
    # Kenshi reports this the moment the character adopts the goal, which is why
    # it is a started reason rather than a success one.
    native_task_started_reasons=frozenset({"context_task_started"}),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_CONTEXT_ACTION_CAPABILITY,
            NATIVE_CONTEXT_TARGETS_CAPABILITY,
            "game.pause",
            "identity.stable_handles",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=("target_id", "context_action"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_context_action",
    bind=bind_perform_context_action,
    handler_key="resources.perform_context_action",
    controller_verified=True,
    authorable_when=context_action_is_currently_authorable,
)

PRODUCE_RESOURCE_OUTPUT_DEFINITION = OperationDefinition(
    kind="produce_resource_output",
    version="1.0",
    operation_type=ProduceResourceOutputAction,
    summary=(
        "Keep one exact natural-resource order under option ownership until the "
        "resource output inventory contains stock. An Operating machine goal is "
        "progress, not success. Work issued by this option is fully cleared before its "
        "terminal; unchanged active work is adopted and left player-owned."
    ),
    argument_source=(
        "target_id must be copied from one natural_resource entry in "
        "context_targets that advertises operate."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_PRODUCE_RESOURCE_CAPABILITY,
            NATIVE_CONTEXT_TARGETS_CAPABILITY,
            "game.pause",
            "identity.stable_handles",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=7,
    reference_fields=("target_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_resource_production",
    bind=bind_produce_resource_output,
    handler_key="resources.produce_resource_output",
    controller_verified=True,
    # Adopting the job is progress; only stock in the output inventory proves
    # the operation itself finished.
    native_terminal_success_reasons=frozenset(
        {
            NATIVE_RESOURCE_OUTPUT_READY_RESULT,
            NATIVE_RESOURCE_TASK_RELEASED_RESULT,
        }
    ),
    authorable_when=resource_production_is_currently_authorable,
)

HARVEST_RESOURCE_DEFINITION = OperationDefinition(
    kind="harvest_resource",
    version="1.0",
    operation_type=HarvestResourceAction,
    summary=(
        "Run one exact natural-resource job at Kenshi's observed 5x speed until "
        "the requested bounded yield exists, restore normal speed, transfer it "
        "conservatively into one exact selected actor, and close the two owned "
        "inventory windows. Its controller-issued operating order is cleared, while "
        "pre-existing work is preserved. Production, transfer, and cleanup are one "
        "interruptible controller option."
    ),
    argument_source=(
        "actor_id is selected.id; target_id is one natural_resource entry in "
        "context_targets advertising operate; quantity is the useful yield, 1-5."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_PRODUCE_RESOURCE_CAPABILITY,
            NATIVE_OPEN_CONTEXT_INVENTORY_CAPABILITY,
            NATIVE_CONTEXT_TARGETS_CAPABILITY,
            CONTEXT_INVENTORY_TARGET_CAPABILITY,
            VISIBLE_CONTROLS_CAPABILITY,
            "game.pause",
            "game.speed",
            "squad.basic",
            "squad.health",
            "squad.inventory",
            "ui.inventory",
            "identity.stable_handles",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(pointer_actions=12, native_assisted_actions=2),
    max_primitive_actions=45,
    reference_fields=("actor_id", "target_id"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.COMPOSITE_OPTION,
    receipt_kind="semantic_resource_harvest",
    bind=bind_harvest_resource,
    handler_key="resources.harvest_resource",
    controller_verified=True,
    authorable_when=harvest_resource_is_currently_authorable,
)


RESPOND_TO_IMMEDIATE_THREAT_DEFINITION = OperationDefinition(
    kind="respond_to_immediate_threat",
    version="1.0",
    operation_type=RespondToImmediateThreatAction,
    summary=(
        "Choose whether the exact selected actor engages or withdraws from an "
        "immediate threat. The runtime owns normal-speed playback, withdrawal "
        "geometry and pathing, threat and health monitoring, timeout, interruption, "
        "and a confirmed terminal pause."
    ),
    argument_source=(
        "actor_id must be the exact currently selected squad member; strategy is "
        "'engage' or 'withdraw'. This action appears only from a fresh paused "
        "immediate-threat state with grounded positions and safe squad health."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            "game.pause",
            "game.speed",
            "nearby.visible_entities",
            "squad.health",
            NATIVE_DIRECTION_CAPABILITY,
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(native_assisted_actions=1),
    # Withdrawal may spend the complete four-primitive native movement budget;
    # the wrapper then owns one additional terminal pause.
    max_primitive_actions=5,
    reference_fields=("actor_id", "strategy"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_threat_response",
    bind=bind_respond_to_immediate_threat,
    handler_key="movement.respond_to_immediate_threat",
    controller_verified=True,
    authorable_when=threat_response_is_currently_authorable,
)

OPEN_CONTEXT_INVENTORY_DEFINITION = OperationDefinition(
    kind="open_context_inventory",
    version="1.0",
    operation_type=OpenContextInventoryAction,
    summary=(
        "Open the ordinary inventory UI for one exact current natural-resource "
        "handle. Native code re-resolves the target and terminally proves that "
        "this exact building inventory is open. This opens the source window; "
        "use toggle_inventory afterward to open the selected character's "
        "destination before collecting output."
    ),
    argument_source=(
        "target_id must be copied from one natural_resource entry in context_targets."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_OPEN_CONTEXT_INVENTORY_CAPABILITY,
            NATIVE_CONTEXT_TARGETS_CAPABILITY,
            "identity.stable_handles",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=6,
    reference_fields=("target_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_context_inventory",
    bind=bind_open_context_inventory,
    handler_key="resources.open_context_inventory",
    controller_verified=True,
    native_terminal_success_reasons=frozenset({"exact_context_inventory_open"}),
    authorable_when=context_inventory_is_currently_authorable,
)


REGROUP_WITH_SQUAD_MEMBER_DEFINITION = OperationDefinition(
    kind="regroup_with_squad_member",
    version="1.0",
    operation_type=RegroupWithSquadMemberAction,
    summary=(
        "Bring one exact selected actor to one distinct current squadmate. The "
        "character adapter binds the current actor and exact target; native code owns global squad "
        "lookup, container-stable identity, pathing, 5x playback, moving-target "
        "tracking, arrival, and a confirmed terminal pause."
    ),
    argument_source=(
        "actor_id must be the exact selected squad member and target_id must be "
        "a distinct current squad entry. The target may be down or unconscious."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_SQUAD_REGROUP_CAPABILITY,
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "squad.basic",
            "squad.health",
        }
    ),
    capability_aliases=frozenset({NATIVE_SQUAD_REGROUP_CAPABILITY}),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=5,
    reference_fields=("actor_id", "target_id"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_squad_regroup",
    bind=bind_regroup_with_squad_member,
    handler_key="movement.regroup_with_squad_member",
    controller_verified=True,
    # Only exact arrival proves the regroup; any other terminal reason is a
    # completion claim without arrival evidence.
    native_terminal_success_reasons=frozenset({"squad_member_reached"}),
    authorable_when=squad_regroup_is_currently_authorable,
)


MOVE_IN_DIRECTION_DEFINITION = OperationDefinition(
    kind="move_in_direction",
    version="1.0",
    operation_type=MoveInDirectionAction,
    summary=(
        "Walk a bearing and distance from where the character stands, ordering "
        "a walk to a bare point rather than toward anyone. One monitored option "
        "owns the targetless native order through its exact command vector. "
        "Native completion is reported as walk_destination_reached."
    ),
    argument_source=(
        "bearing_degrees is clockwise from north (0 N, 90 E, 180 S, 270 W); "
        "distance_units is how far to walk. Neither is read from the "
        "observation - they are chosen."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset({NATIVE_DIRECTION_CAPABILITY, "squad.health"}),
    capability_aliases=frozenset({NATIVE_DIRECTION_CAPABILITY}),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=(),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_move",
    bind=bind_move_in_direction,
    handler_key="movement.move_in_direction",
    controller_verified=True,
)

TRAVEL_TO_MAP_DESTINATION_DEFINITION = OperationDefinition(
    kind="travel_to_map_destination",
    version="1.0",
    operation_type=TravelToMapDestinationAction,
    summary=(
        "Travel to one exact settlement marker the player has already "
        "discovered. Native code re-resolves the marker, selects Kenshi's "
        "direction-dependent gate waypoint, continues through a gated entrance, "
        "aligns the follow camera behind the route, and owns arrival until exact "
        "current-town evidence is usable. The controller runs long travel at 5x "
        "and pauses at the terminal boundary."
    ),
    argument_source=(
        "destination_id must be copied exactly from a known_map_destinations "
        "entry whose travel_available is true. Coordinates are neither exposed "
        "nor accepted."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_MAP_TRAVEL_CAPABILITY,
            NATIVE_MAP_DESTINATIONS_CAPABILITY,
            "game.pause",
            "game.speed",
            "identity.stable_handles",
            "squad.health",
        }
    ),
    capability_aliases=frozenset({NATIVE_MAP_TRAVEL_CAPABILITY}),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.ONE_OR_MORE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=5,
    reference_fields=("destination_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_map_travel",
    bind=bind_travel_to_map_destination,
    handler_key="movement.travel_to_map_destination",
    controller_verified=True,
    authorable_when=map_travel_is_currently_authorable,
)

EXIT_CURRENT_BUILDING_DEFINITION = OperationDefinition(
    kind="exit_current_building",
    version="1.0",
    operation_type=ExitCurrentBuildingAction,
    summary=(
        "Leave the selected character's current building. The planner supplies "
        "no direction or coordinates; native code resolves an unlocked door, "
        "issues one order to its outdoor point, and owns completion or bounded "
        "failure. Completion accepts either stable outdoor membership or tightly "
        "reaching that resolved outside-door destination because Kenshi can "
        "retain a stale indoor handle after visible traversal."
    ),
    argument_source=(
        "No arguments. Availability requires one exact selected character with "
        "selected.indoors=true in current telemetry."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_EXIT_BUILDING_CAPABILITY,
            "game.pause",
            "identity.stable_handles",
            "squad.indoors",
        }
    ),
    capability_aliases=frozenset({NATIVE_EXIT_BUILDING_CAPABILITY}),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.EXACTLY_ONE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=(),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_move",
    bind=bind_exit_current_building,
    handler_key="movement.exit_current_building",
    controller_verified=True,
)

MOVE_TO_CHARACTER_DEFINITION = OperationDefinition(
    kind="move_to_character",
    version="1.0",
    operation_type=MoveToCharacterAction,
    summary=(
        "Walk the complete current selection to one exact currently observed "
        "nearby character without talking to them. This is how the agent goes "
        "somewhere: nearby characters are reported within four hundred units, "
        "so someone standing where you want to be is a destination. One "
        "monitored option owns the whole group walk."
    ),
    argument_source=(
        "target_id must be an exact id from the observation's telemetry.nearby_entities."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_MOVE_CAPABILITY,
            "identity.stable_handles",
            "nearby.characters",
        }
    ),
    capability_aliases=frozenset({NATIVE_MOVE_CAPABILITY}),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    selection_requirement=SelectionRequirement.ONE_OR_MORE,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=("target_id",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_move",
    bind=bind_move_to_character,
    handler_key="movement.move_to_character",
    controller_verified=True,
)

ACTIVATE_VISIBLE_CONTROL_DEFINITION = OperationDefinition(
    kind="activate_visible_control",
    version="1.0",
    operation_type=ActivateVisibleControlAction,
    summary=(
        "Activate exactly one control the interface currently advertises, using "
        "its observed bounds re-resolved inside the input lease."
    ),
    argument_source=(
        "exact_label and role must match exactly one non-ambiguous, non-runtime-owned "
        "entry of the observation's visible_controls."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset({VISIBLE_CONTROLS_CAPABILITY}),
    capability_aliases=frozenset(),
    # Bounds come from current telemetry and are re-read inside the lease, so
    # this action survives a resolution change and needs no calibrated profile.
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=OperationRisk(pointer_actions=1),
    max_primitive_actions=1,
    reference_fields=("exact_label", "role"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_control",
    bind=bind_visible_control,
    handler_key="screens.activate_visible_control",
)

DISMISS_SCREEN_DEFINITION = OperationDefinition(
    kind="dismiss_screen",
    version="1.0",
    operation_type=DismissScreenAction,
    summary=(
        "Close one currently bound named screen or exact trade/inventory window "
        "toward the world view. Active dialogue instead ends through an exact "
        "visible reply."
    ),
    argument_source=(
        "expected_screen must be observably open; inventory/trade may also name "
        "the exact current owner window. Do not use for active dialogue."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(),
    capability_aliases=frozenset(),
    # Inventory and trade windows close through their current close box; other
    # screens use a configured key. The pointer path is resolved from current
    # telemetry inside the input lease.
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=OperationRisk(pointer_actions=1),
    max_primitive_actions=3,
    reference_fields=("expected_screen",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_dismiss",
    bind=bind_dismiss_screen,
    handler_key="screens.dismiss_screen",
    derive_completion_conditions=_dismissed_screen_closed,
)

PURCHASE_ITEM_DEFINITION = OperationDefinition(
    kind="purchase_item",
    version="2.2",
    operation_type=PurchaseItemAction,
    summary=(
        "Acquire a bounded quantity of one item from exact seller-owned cells. "
        "The controller binds the exact open player-window owner, rebinds each "
        "unit, and proves that owner's carried gain against the exact quoted "
        "charge, including a zero-cost transfer."
    ),
    argument_source=(
        "cell_label, item_name, expected_price and window come from one "
        "visible_controls item entry; seller_id is the exact stable id of that "
        "vendor group; quantity is the useful bounded amount, 1-5. "
        "expected_price must equal that entry's nonnegative buy_price exactly; "
        "zero means free. sell_price is what a trader pays you and is rejected "
        "here."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            VISIBLE_CONTROLS_CAPABILITY,
            "ui.tooltip",
            "ui.inventory",
            "game.money",
            "game.pause",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.shop_owners",
            "squad.basic",
            "squad.inventory",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=OperationRisk(pointer_actions=1, purchase_actions=1),
    max_primitive_actions=10,
    reference_fields=(
        "cell_label",
        "item_name",
        "expected_price",
        "quantity",
        "seller_id",
    ),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.COMPOSITE_OPTION,
    receipt_kind="semantic_purchase",
    bind=bind_purchase_item,
    handler_key="trade.purchase_item",
    derive_risk=_bounded_trade_risk,
    derive_primitive_action_bound=_bounded_trade_primitive_action_bound,
    controller_verified=True,
)


def bind_open_screen(
    action: Action,
    observation: Observation,
) -> EmptyBinding | BindingFailure:
    """Resolve the screen to its binding and to whether it is already up.

    Already-satisfied aware on purpose. The underlying controls are toggles, so
    pressing to "open" a screen that is open closes it; this action promises the
    screen IS open, which is the whole reason it exists rather than
    `use_game_binding`.
    """

    if not isinstance(action, OpenScreenAction):
        return _unbound("Action is not an open_screen action.")
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return _unbound("No fresh telemetry, so the screen state cannot be read.")
    already = screen_is_open(action.screen, telemetry)
    if already is None:
        return _unbound(
            f"Nothing observable reports whether {action.screen.value} is open, "
            "so opening it could not be proven."
        )
    binding = SCREEN_BINDINGS[action.screen]
    if already:
        return EmptyBinding(
            reason=(
                f"The {action.screen.value} screen is already open; pressing "
                f"{binding.value} would close it, so no input is sent."
            ),
            source_revision=observation.world_revision,
        )
    return EmptyBinding(
        reason=(f"The {action.screen.value} screen is closed and {binding.value} opens it."),
        source_revision=observation.world_revision,
    )


def _open_screen_terminal(
    action: Action,
    observation: Observation,
) -> tuple[Condition, ...] | None:
    if not isinstance(action, OpenScreenAction):
        return ()
    condition = open_screen_success_condition(action.screen, observation.telemetry)
    return (condition,) if condition is not None else ()


OPEN_SCREEN_DEFINITION = OperationDefinition(
    kind="open_screen",
    version="1.0",
    operation_type=OpenScreenAction,
    summary=(
        "Have a named screen open. The controller presses whichever binding "
        "opens it and proves the exact screen arrived, so the affordance expresses "
        "state intent rather than a key."
    ),
    argument_source=("The screen adapter supplies one current GameScreen state intention."),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=False,
    risk=OperationRisk(),
    max_primitive_actions=1,
    reference_fields=("screen",),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_screen",
    bind=bind_open_screen,
    handler_key="screens.open_screen",
    derive_completion_conditions=_open_screen_terminal,
)


USE_GAME_BINDING_DEFINITION = OperationDefinition(
    kind="use_game_binding",
    version="1.0",
    operation_type=UseGameBindingAction,
    summary=(
        "Press one named Kenshi control through the hard-coded shipped-default "
        "keymap. The binding catalog is the reviewed semantic vocabulary; it "
        "does not permit arbitrary keys. Use a named control instead of hunting "
        "for a widget when one exists. Customized keymaps are not currently read."
    ),
    argument_source=(
        "The game-binding adapter supplies one exact current GameBinding. Live "
        "time controls remain runtime-owned. expected_effect is a runtime audit "
        "label; completion is either a derived terminal or the adapter's explicit "
        "delivery boundary."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    # A keypress needs the game loaded and nothing else; requiring more would
    # withhold the one action that recovers from a screen we cannot identify.
    required_capabilities=frozenset(),
    capability_aliases=frozenset(),
    # A key carries no screen position at all.
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=False,
    risk=OperationRisk(),
    max_primitive_actions=1,
    reference_fields=("binding",),
    # Set at construction below: toggles may not be retried, because a retry
    # undoes the first press instead of repeating it.
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_binding",
    bind=bind_use_game_binding,
    handler_key="screens.use_game_binding",
    derive_completion_conditions=_binding_transition,
    derive_terminal=_game_binding_terminal,
)

RECOVER_CAMERA_VIEW_DEFINITION = OperationDefinition(
    kind="recover_camera_view",
    version="1.0",
    operation_type=RecoverCameraViewAction,
    summary=(
        "Restore a usable selected-character-following world view through one "
        "bounded controller-owned transaction. The caller supplies no camera "
        "parameters and receives already_clear, recovered, or "
        "failed_after_bounded_attempts."
    ),
    argument_source=(
        "No arguments. The controller resolves the one selected character, its "
        "lower-HUD portrait, the current floor, and floor arrows from fresh "
        "telemetry, then scores retained frames."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            CAMERA_RECOVERY_CAPABILITY,
            "camera.position",
            "game.pause",
            "squad.basic",
            VISIBLE_CONTROLS_CAPABILITY,
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=OperationRisk(pointer_actions=1),
    max_primitive_actions=15,
    reference_fields=(),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_camera_recovery",
    bind=bind_recover_camera_view,
    handler_key="camera.recover_camera_view",
    controller_verified=True,
)


SCROLL_SCREEN_DEFINITION = OperationDefinition(
    kind="scroll_screen",
    version="1.0",
    operation_type=ScrollScreenAction,
    summary=(
        "Scroll inside one open window to reveal contents past the first "
        "screenful. Shop stock and inventory that are not currently rendered "
        "are not exported at all, so scrolling is the only way to find them."
    ),
    argument_source=(
        "window must exactly match the `window` of at least one current "
        "visible_controls entry; notches is negative to scroll further down "
        "the list and positive to scroll back up."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset({VISIBLE_CONTROLS_CAPABILITY}),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    # A scroll commits nothing: it changes what is rendered, not the world.
    risk=OperationRisk(),
    max_primitive_actions=1,
    reference_fields=("window",),
    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_scroll",
    bind=bind_scroll_screen,
    handler_key="screens.scroll_screen",
)


SELL_ITEM_DEFINITION = OperationDefinition(
    kind="sell_item",
    version="2.1",
    operation_type=SellItemAction,
    summary=(
        "Sell a bounded quantity from the exact observed player-window owner. "
        "The controller rebinds every unit and proves that owner's carried loss "
        "plus purse gain."
    ),
    argument_source=(
        "cell_label from a visible_controls entry with role 'item'; window must "
        "resolve to one selected squad member's own name; item_name copied from "
        "that cell's own entry; buyer_id the exact stable id of the one active shop "
        "owner; quantity is the useful bounded amount, 1-5. No price is given: "
        "the shop's offer is not exported."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            VISIBLE_CONTROLS_CAPABILITY,
            "ui.inventory",
            "squad.inventory",
            "game.money",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.shop_owners",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    # Counted against the purchase budget: a sale is as irreversible as a buy.
    risk=OperationRisk(pointer_actions=1, purchase_actions=1),
    max_primitive_actions=10,
    reference_fields=("cell_label", "item_name", "quantity", "window", "buyer_id"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.COMPOSITE_OPTION,
    receipt_kind="semantic_sell",
    bind=bind_sell_item,
    handler_key="trade.sell_item",
    derive_risk=_bounded_trade_risk,
    derive_primitive_action_bound=_bounded_trade_primitive_action_bound,
    controller_verified=True,
)


EQUIP_ITEM_DEFINITION = OperationDefinition(
    kind="equip_item",
    version="1.1",
    operation_type=EquipItemAction,
    summary=(
        "Equip the item in one exact selected squad-owned inventory window. "
        "Refused while any trade is open, because there the same right-click "
        "sells the item instead."
    ),
    argument_source=(
        "cell_label from a visible_controls entry with role 'item'; window must "
        "resolve to one selected squad member's own name; item_name copied from "
        "that cell's own entry."
    ),
    allowed_control_modes=frozenset({ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {VISIBLE_CONTROLS_CAPABILITY, "ui.inventory", "squad.inventory"}
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=OperationRisk(pointer_actions=1),
    max_primitive_actions=1,
    reference_fields=("cell_label", "window"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_equip",
    bind=bind_equip_item,
    handler_key="inventory.equip_item",
)

COLLECT_RESOURCE_OUTPUT_DEFINITION = OperationDefinition(
    kind="collect_resource_output",
    version="1.2",
    operation_type=CollectResourceOutputAction,
    summary=(
        "Right-click one exact observed output cell into the selected character. "
        "The exact resource inventory and selected character's own inventory "
        "must be the only two open inventory owners; after open_context_inventory, "
        "use toggle_inventory to open the destination. Loaded shop-owner characters "
        "do not define the current UI layout. "
        "Success requires a causally later equal source loss and destination gain "
        "from complete inventories; a click receipt is never enough."
    ),
    argument_source=(
        "target_id is the open resource group's exact target_id; copy cell_label, "
        "item_name, item_quantity as source_quantity, window, and section='out' "
        "from one item in that same group."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            VISIBLE_CONTROLS_CAPABILITY,
            CONTEXT_INVENTORY_TARGET_CAPABILITY,
            NATIVE_CONTEXT_TARGETS_CAPABILITY,
            "ui.inventory",
            "squad.inventory",
            "identity.stable_handles",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.SEMANTIC_CURRENT,
    native_assisted=False,
    risk=OperationRisk(pointer_actions=2),
    max_primitive_actions=4,
    reference_fields=(
        "target_id",
        "cell_label",
        "item_name",
        "source_quantity",
        "window",
        "section",
    ),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_resource_transfer",
    bind=bind_collect_resource_output,
    handler_key="resources.collect_resource_output",
    controller_verified=True,
    authorable_when=resource_output_is_currently_authorable,
)

OPERATION_DEFINITION_LIST: tuple[OperationDefinition, ...] = (
    NOOP_DEFINITION,
    STOP_DEFINITION,
    CONSULT_ADVISOR_DEFINITION,
    RECALL_MEMORY_DEFINITION,
    READ_FIELDBOOK_DEFINITION,
    PAUSE_DEFINITION,
    SET_SPEED_DEFINITION,
    WAIT_DEFINITION,
    APPROACH_DIALOGUE_TARGET_DEFINITION,
    OPEN_SCREEN_DEFINITION,
    COMMAND_WORLD_TARGET_DEFINITION,
    SELECT_SQUAD_MEMBER_DEFINITION,
    SELECT_SQUAD_MEMBER_EXACT_DEFINITION,
    ROTATE_CAMERA_DEFINITION,
    PERFORM_CONTEXT_ACTION_DEFINITION,
    PRODUCE_RESOURCE_OUTPUT_DEFINITION,
    HARVEST_RESOURCE_DEFINITION,
    RESPOND_TO_IMMEDIATE_THREAT_DEFINITION,
    OPEN_CONTEXT_INVENTORY_DEFINITION,
    REGROUP_WITH_SQUAD_MEMBER_DEFINITION,
    MOVE_TO_CHARACTER_DEFINITION,
    MOVE_IN_DIRECTION_DEFINITION,
    TRAVEL_TO_MAP_DESTINATION_DEFINITION,
    EXIT_CURRENT_BUILDING_DEFINITION,
    ACTIVATE_VISIBLE_CONTROL_DEFINITION,
    DISMISS_SCREEN_DEFINITION,
    PURCHASE_ITEM_DEFINITION,
    USE_GAME_BINDING_DEFINITION,
    RECOVER_CAMERA_VIEW_DEFINITION,
    SCROLL_SCREEN_DEFINITION,
    SELL_ITEM_DEFINITION,
    EQUIP_ITEM_DEFINITION,
    COLLECT_RESOURCE_OUTPUT_DEFINITION,
)


def _build_definition_registry(
    definitions: tuple[OperationDefinition, ...],
) -> dict[str, OperationDefinition]:
    registry: dict[str, OperationDefinition] = {}
    for definition in definitions:
        if definition.kind in registry:
            raise RuntimeError(f"Operation {definition.kind!r} is multiply defined.")
        if not definition.handler_key:
            raise RuntimeError(f"Operation {definition.kind!r} has no handler key.")
        registry[definition.kind] = definition
    return registry


OPERATION_DEFINITIONS = _build_definition_registry(OPERATION_DEFINITION_LIST)


def definition_for(action: Action) -> OperationDefinition | None:
    """Return the sole definition for an adapted private operation, if any."""

    return OPERATION_DEFINITIONS.get(action.kind)


def risk_for_operation(action: Action) -> OperationRisk | None:
    """Return one definition-owned risk declaration for plan accounting."""

    definition = definition_for(action)
    return definition.risk_for(action) if definition is not None else None
