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
from .core.interaction import (
    AuthoredRecipientBasis,
    CompletionMilestone,
    OperationInteractionContract,
    PlaybackRequirement,
    RecipientScope,
    SelectionDependency,
    explicit_recipients_of,
    global_ui,
    ordinary_order,
    runtime_only,
    selection_mutation,
)
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
    OpenTradeWindowAction,
    PauseAction,
    PerformCharacterOrderAction,
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
    ShiftIntoBodyAction,
    StopAction,
    SurveyLocalResourcesAction,
    ThreatResponseStrategy,
    TransferItemAction,
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
    NearbyEntity,
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
NATIVE_CHARACTER_ORDER_CAPABILITY = "control.perform_character_order"
NEARBY_ORDERABLE_TASKS_CAPABILITY = "nearby.orderable_tasks"
NATIVE_SHIFT_BODY_CAPABILITY = "control.shift_into_body"
NATIVE_RESOURCE_SURVEY_CAPABILITY = "control.survey_local_resources"

NATIVE_CONTEXT_TARGETS_CAPABILITY = "world.context_targets"
WORLD_CONTEXT_TARGET_SCREEN_POSITIONS_CAPABILITY = "world.context_target_screen_positions"
NATIVE_PRODUCE_RESOURCE_CAPABILITY = "control.produce_resource_output"
NATIVE_OPEN_CONTEXT_INVENTORY_CAPABILITY = "control.open_context_inventory"
NATIVE_TRANSFER_CAPABILITY = "control.transfer_item"
NATIVE_TRADE_WINDOW_CAPABILITY = "control.open_trade_window"

# The one mapping from a control capability to the native command it authorizes.
# A capability is the permission; the command is the thing performed with it.
# This was restated in three places - an action-class chain in option
# preparation that ended in an unconditional `return "move_in_direction"`, a
# dict in the capability-consistency test, and the native parser's own name
# list - so an operation could be admitted under one name and dispatched under
# another. Names line up with the capability everywhere except the approach,
# which kept its original wire name; that exception is written down here rather
# than lived with.


# One projection per dispatching operation: the fields that make a request that
# command, and equally the fields an acknowledgement must carry to be that
# command's. Keeping them in one function is what stops a request and a matcher
# from disagreeing about what identifies an operation.
def _wire_target(field_name: str = "target_id") -> WireFieldFactory:
    def project(action: Action) -> WireFields:
        return {"target_id": getattr(action, field_name)}

    return project


def _wire_direction(action: Action) -> WireFields:
    return {
        "bearing_degrees": action.bearing_degrees,
        "distance_units": action.distance_units,
    }


def _wire_nothing(action: Action) -> WireFields:
    del action
    return {}


def _wire_context_action(action: Action) -> WireFields:
    return {
        "target_id": action.target_id,
        "context_action": str(action.context_action),
    }


def _wire_character_order(action: Action) -> WireFields:
    # The order is part of the identity, not decoration. One person can afford
    # several orders at once, so a match on target alone would let either
    # satisfy a wait for the other.
    return {"target_id": action.target_id, "context_action": action.order}


def _wire_resource_output(action: Action) -> WireFields:
    return {
        "target_id": action.target_id,
        "minimum_output_quantity": action.minimum_output_quantity,
    }


def _wire_trade_window(action: Action) -> WireFields:
    return {
        "target_id": action.first_owner_id,
        "destination_id": action.second_owner_id,
        "context_action": action.window_type,
    }


def _wire_transfer(action: Action) -> WireFields:
    return {
        "target_id": action.source_owner_id,
        "destination_id": action.destination_owner_id,
        "section_name": action.section_name,
        "slot_x": action.slot_x,
        "slot_y": action.slot_y,
    }


WIRE_FIELD_DEFAULTS: WireFields = {
    "target_id": "",
    "context_action": "",
    "bearing_degrees": 0.0,
    "distance_units": 0.0,
    "minimum_output_quantity": 1,
    "destination_id": "",
    "section_name": "",
    "slot_x": 0,
    "slot_y": 0,
    "paused": False,
    "speed_multiplier": 0.0,
}


def wire_fields_for(action: Action) -> WireFields:
    """Every wire field this action implies, defaults included.

    Raises rather than returning a partial answer. An operation that dispatches
    a native command and declares no projection cannot have a request built for
    it or an acknowledgement attributed to it, and saying so here is what makes
    that loud instead of a silent mismatch.
    """

    definition = definition_for(action)
    if definition is None or definition.project_wire_fields is None:
        raise ValueError(
            f"Operation {action.kind!r} declares no wire projection, so its "
            "request cannot be built and its acknowledgement cannot be matched."
        )
    fields = dict(WIRE_FIELD_DEFAULTS)
    fields.update(definition.project_wire_fields(action))
    return fields


def native_wire_command_for(definition: OperationDefinition) -> str | None:
    """The native command this operation dispatches, declared by the operation.

    This used to scan `sorted(required_capabilities)` for the first capability
    appearing in a lookup table, which made an operation's dispatch route a
    consequence of alphabetical order. For anything requiring one control
    capability that happened to be right; for anything requiring several it was
    arbitrary, and two operations were quietly wrong -- `harvest_resource`
    resolved to `open_context_inventory` and `respond_to_immediate_threat` to
    `move_in_direction`, in both cases because that capability sorted first.

    A route is not derivable from a permission set. It is a fact about the
    operation, so the operation states it.
    """

    return definition.wire_command or None
CONTEXT_INVENTORY_TARGET_CAPABILITY = "ui.context_inventory_target"

VISIBLE_CONTROLS_CAPABILITY = "ui.visible_controls"
CAMERA_RECOVERY_CAPABILITY = "camera.recovery"
SQUAD_REGROUP_ARRIVAL_DISTANCE = 12.0


class OperationExecution(StrEnum):
    """How the executor must run an action, not what the action means."""

    ATOMIC_HANDLER = "atomic_handler"
    MONITORED_OPTION = "monitored_option"
    COMPOSITE_OPTION = "composite_option"


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
InteractionContractFactory = Callable[
    [Action, Observation | None],
    OperationInteractionContract,
]


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


# The wire fields one action projects onto a native request. Every key is a
# `NativeCommandRequest` field name; anything omitted keeps its default, and the
# same defaults are what an acknowledgement is checked against.
WireFields = dict[str, object]
WireFieldFactory = Callable[[Action], WireFields]

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


def shift_into_body_is_currently_authorable(observation: Observation) -> bool:
    """Whether any body is currently observable to shift into."""

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    if telemetry.ui.active_screen != "world":
        return False
    return any(_body_is_shiftable(entity) for entity in telemetry.nearby_entities)


def _body_is_shiftable(entity: NearbyEntity) -> bool:
    """Whether one observed character could be entered right now.

    Refuses hostiles deliberately. A hostile body is the case most likely to
    have consequences nobody has measured - the faction it is being taken from
    is already fighting - and the plug-in refuses it too, so offering it would
    only manufacture a rejected plan.
    """

    return bool(
        entity.kind == "character"
        and entity.is_animal is False
        and entity.conscious is True
        and entity.disposition is not Disposition.HOSTILE
        and entity.id
    )


def bind_shift_into_body(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind the shift to one exact currently observed body.

    The same exactness every other target binding holds to: the id must match
    one current entity, and an absent or ambiguous one fails closed. Becoming
    the wrong person is not a recoverable mistake - there is no undo that
    restores a faction you were pulled out of.
    """

    if not isinstance(action, ShiftIntoBodyAction):
        return _unbound("Action is not a shift_into_body action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the body.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the body cannot be bound.")
    if failure := _world_interface_error(observation):
        return _unbound(failure)
    matches = [entity for entity in telemetry.nearby_entities if entity.id == action.target_id]
    if not matches:
        return _unbound(
            f"Body {action.target_id!r} is not a currently observed nearby character."
        )
    if len(matches) > 1:
        return _unbound(
            f"Body {action.target_id!r} matches {len(matches)} current entities; "
            "an ambiguous reference fails closed."
        )
    target = matches[0]
    if not _body_is_shiftable(target):
        return _unbound(
            f"Body {target.name!r} ({target.id}) is not currently enterable: it must be "
            "a conscious, non-animal, non-hostile character."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound a body shift to current {target.faction or 'unaffiliated'} character "
            f"{target.name!r} ({target.id}) at distance "
            f"{target.distance if target.distance is not None else 'unknown'}."
        ),
        target_id=target.id,
        resolved_label=target.name,
        source_revision=observation.world_revision,
    )


def bind_perform_character_order(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind one order to one exact person Kenshi currently offers it on.

    Eligibility is not re-derived here. Kenshi already answered it per target -
    chiefly by building the context menu it would build for a right-click, with
    its renderer muted - and the answer travelled in `advertised_tasks`; this
    binding only proves the named order is among them.

    An unprobed entity fails closed. Probing is budgeted, so an empty list from
    an unprobed target is silence rather than a denial, and reading silence as
    permission is exactly how a refused order becomes an unexplained failure
    mid-run.
    """

    if not isinstance(action, PerformCharacterOrderAction):
        return _unbound("Action is not a perform_character_order action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the order.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the order cannot be bound.")
    if failure := _world_interface_error(observation):
        return _unbound(failure)
    matches = [entity for entity in telemetry.nearby_entities if entity.id == action.target_id]
    if not matches:
        return _unbound(
            f"Person {action.target_id!r} is not a currently observed nearby character."
        )
    if len(matches) > 1:
        return _unbound(
            f"Person {action.target_id!r} matches {len(matches)} current entities; "
            "an ambiguous reference fails closed."
        )
    target = matches[0]
    if not target.advertised_tasks_probed:
        return _unbound(
            f"{target.name!r} ({target.id}) was not probed this observation, so what it "
            "affords is unknown rather than empty."
        )
    offered = target.orderable_task_names()
    if action.order not in offered:
        return _unbound(
            f"Kenshi does not currently offer {action.order!r} on {target.name!r} "
            f"({target.id}). Offered: {', '.join(offered) if offered else 'nothing'}."
        )
    # Which probe vouched for it travels into the reason. When an order binds
    # and then fails to take, the receipt already says whether the game's own
    # menu offered it or only the odds getter did, so the disagreement is
    # attributable without a second live run.
    sources = ", ".join(sorted(target.order_evidence(action.order)))
    return BoundNamedTarget(
        reason=(
            f"Bound the order {action.order!r} to current "
            f"{target.faction or 'unaffiliated'} character {target.name!r} ({target.id}) "
            f"at distance {target.distance if target.distance is not None else 'unknown'}, "
            f"which Kenshi currently advertises it on (evidence: {sources})."
        ),
        target_id=target.id,
        resolved_label=f"{action.order} on {target.name}",
        source_revision=observation.world_revision,
    )


def perform_character_order_is_currently_authorable(observation: Observation) -> bool:
    """Whether any nearby person currently affords any order at all.

    Asked of the world rather than of a role: if Kenshi advertises nothing on
    anyone nearby, there is no order to offer, and that is the whole condition.
    """

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    return any(
        entity.advertised_tasks_probed and entity.advertised_tasks
        for entity in telemetry.nearby_entities
    )


def resolve_context_action_interaction(
    action: Action,
    observation: Observation | None = None,
) -> OperationInteractionContract:
    """Resolve the contract for one exact context semantic.

    `perform_context_action` cannot have a single contract. Every case is an
    ordinary order broadcast to the current selection, but what counts as
    success differs by semantic: `operate` succeeds when Kenshi is running the
    machine, while an unclassified order can only honestly claim that Kenshi
    accepted it. Collapsing those into one milestone would let acceptance be
    reported as achievement.

    New semantics stay at `ORDER_ACCEPTED` until evidence proves a stronger
    milestone for them specifically.
    """

    semantic = getattr(action, "context_action", None)
    milestone = (
        CompletionMilestone.ACTIVITY_RUNNING
        if semantic == ContextActionKind.OPERATE
        else CompletionMilestone.ORDER_ACCEPTED
    )
    return ordinary_order(
        recipients=RecipientScope.CURRENT_SELECTION,
        milestone=milestone,
    )


def bind_survey_local_resources(
    action: Action,
    observation: Observation,
) -> BoundActor | BindingFailure:
    """Bind the survey to the exact character whose position it will read."""

    if not isinstance(action, SurveyLocalResourcesAction):
        return _unbound("Action is not a local resource survey.")
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return _unbound("A survey requires fresh telemetry.")
    actor_id = telemetry.ui.selected_character_id
    if not actor_id:
        return _unbound("A survey requires an exported primary character.")
    actor = next(
        (member for member in telemetry.squad if member.id == actor_id),
        None,
    )
    if actor is None:
        return _unbound(f"Primary character {actor_id!r} is absent from the roster.")
    return BoundActor(
        reason=(
            f"Bound a resource survey to {actor.name!r} ({actor_id}) at its "
            "current position."
        ),
        target_id=actor_id,
        source_revision=observation.world_revision,
    )


def survey_local_resources_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    primary = telemetry.ui.selected_character_id
    return bool(
        telemetry.game.loaded is True
        and primary
        and primary in telemetry.ui.selected_character_ids
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
    # The actor must be Kenshi's exported primary, because the collection phase
    # opens that character's own inventory and the goods have to land somewhere
    # unambiguous. It need not be the *only* selection: requiring that made an
    # ordinary two-character party unable to harvest at all, which left
    # `perform_context_action('operate')` as the only mining affordance on offer
    # - the one that fills the resource's output box and nobody's pack.
    #
    # `down` is not a fence either. Only unconsciousness stops a character in
    # Kenshi; legs past the knockout point crawl until bandaged.
    if (
        len(selected) != 1
        or telemetry.ui.selected_character_id != action.actor_id
        or action.actor_id not in telemetry.ui.selected_character_ids
        or selected[0].alive is not True
        or selected[0].conscious is not True
        or selected[0].in_combat is not False
        or selected[0].inventory_complete is not True
    ):
        return _unbound(
            "Harvesting requires its actor to be the current primary, selected, "
            "alive, conscious, out of combat, and backed by a complete inventory "
            "export."
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
    # `down is False` was here and was wrong: in Kenshi only unconsciousness
    # stops a character acting. Legs damaged past the knockout point make one
    # crawl until bandaged, which is slow rather than incapable, so a crawling
    # miner was refused the only operation that ends with ore in their pack.
    selected = [
        character
        for character in telemetry.squad
        if character.selected
        and character.alive is True
        and character.conscious is True
        and character.in_combat is False
        and character.inventory_complete is True
    ]
    # A singleton fence stood here too, and it is why every mining run reached
    # for `perform_context_action('operate')`: an ordinary two-character party
    # made the complete harvest unauthorable, leaving only the operation that
    # starts a job and fills nobody's inventory. The actor is named by the
    # action, so party size was never the harvest's business.
    primary = telemetry.ui.selected_character_id
    return bool(
        telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
        and selected
        and primary
        and any(character.id == primary for character in selected)
        and any(
            target.kind == "natural_resource"
            and ContextActionKind.OPERATE in target.context_actions
            and target.default_task == "operate_machinery"
            for target in telemetry.world_targets
        )
    )


def _observed_inventory_owner(
    target_id: str,
    observation: Observation,
) -> tuple[str, str] | None:
    """One observed thing that could own an inventory, as (label, kind).

    Deliberately unfenced by kind. Kenshi keys its open windows by handle and
    opens them with `showInventory(hand, ...)`; neither has ever cared whether
    the handle names a body, a crate, a shopkeeper or a squadmate. The narrow
    lookup this replaces asked `world_targets` for a `natural_resource`, which
    is why looting could not be reached through it at all.
    """

    telemetry = observation.telemetry
    if telemetry is None:
        return None
    for member in telemetry.squad:
        if member.id == target_id:
            return member.name, "squad_character"
    for entity in telemetry.nearby_entities:
        if entity.id == target_id:
            return entity.name, entity.kind
    for target in telemetry.world_targets:
        if target.id == target_id:
            return target.name, target.kind
    for discovered in telemetry.discovered_objects:
        if discovered.id == target_id:
            return discovered.name, discovered.category
    return None


def bind_open_context_inventory(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind inventory opening to one exact observed owner, of any kind.

    No fence on what may own an inventory, and no refusal for other open
    windows. Both were mining artefacts: the old binding demanded a
    `natural_resource` world target, and refused whenever anything else was
    open. A transfer needs two inventories open at once, so "one window is the
    whole interaction" is the assumption that made looting, buying and giving
    look like three separate problems.
    """

    if not isinstance(action, OpenContextInventoryAction):
        return _unbound("Action is not an open_context_inventory action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the inventory owner.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the inventory owner cannot be bound.")
    owner = _observed_inventory_owner(action.target_id, observation)
    if owner is None:
        return _unbound(
            f"{action.target_id!r} is not a currently observed character, world "
            "target, or discovered object, so its inventory cannot be opened."
        )
    label, kind = owner
    already_open = any(
        held.owner_id == action.target_id for held in telemetry.ui.open_inventories
    )
    if telemetry.ui.dialogue_open is not False:
        return _unbound("A dialogue is open; close it before opening an inventory.")
    # `modal_open` is `dialogue_open or inventory_open`, so it cannot by itself
    # tell a blocking message box from an inventory that is simply already
    # showing. `open_inventories` can: a modal with no dialogue and no open
    # inventory behind it is something else, and that is what must be refused.
    # The old fence refused on `modal_open` alone, which meant a second window
    # could never be opened - and two windows is what a transfer is.
    if (
        telemetry.ui.modal_open is True
        and not telemetry.ui.open_inventories
        and telemetry.ui.open_inventories_complete
    ):
        return _unbound(
            "A modal that is neither a dialogue nor an inventory is open; close "
            "it before opening an inventory."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound the inventory of {kind} {label!r} ({action.target_id})"
            + ("; it is already open." if already_open else ".")
        ),
        target_id=action.target_id,
        resolved_label=label,
        source_revision=observation.world_revision,
    )


def bind_open_trade_window(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind two observed parties whose inventories should be paired.

    Both must be observed; neither needs an inventory open yet, because opening
    them is what this does. Whether Kenshi will pair these two is Kenshi's
    answer at dispatch.
    """

    if not isinstance(action, OpenTradeWindowAction):
        return _unbound("Action is not an open_trade_window action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the trade window.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the trade window cannot be bound.")
    first = _observed_inventory_owner(action.first_owner_id, observation)
    second = _observed_inventory_owner(action.second_owner_id, observation)
    if first is None:
        return _unbound(
            f"{action.first_owner_id!r} is not currently observed, so its "
            "inventory cannot be paired."
        )
    if second is None:
        return _unbound(
            f"{action.second_owner_id!r} is not currently observed, so its "
            "inventory cannot be paired."
        )
    if telemetry.ui.dialogue_open is not False:
        return _unbound("A dialogue is open; close it before pairing inventories.")
    return BoundNamedTarget(
        reason=(
            f"Bound a {action.window_type} window pairing {first[0]!r} with "
            f"{second[0]!r}. Whether Kenshi pairs them is its answer at dispatch."
        ),
        target_id=action.first_owner_id,
        resolved_label=f"{first[0]} and {second[0]}",
        source_revision=observation.world_revision,
    )


def trade_window_is_currently_authorable(observation: Observation) -> bool:
    """Whether there are two observed parties to pair at all."""

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    if telemetry.ui.dialogue_open is not False:
        return False
    return bool(telemetry.squad) and bool(
        telemetry.nearby_entities or telemetry.world_targets or len(telemetry.squad) > 1
    )


def bind_transfer_item(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind one item in one open inventory slot to one open destination.

    Both inventories must currently be open and both must be reported, because
    the slot is only meaningful inside the inventory that reported it. What is
    deliberately *not* decided here is whether the transfer is allowed: Kenshi
    answers that, and answers it in detail - no room, cannot afford, that is
    mine, a thief was spotted, the container is not empty. Re-deriving a coarser
    version of that judgment in Python is how a fenced operation ends up
    refusing things the game would have permitted.
    """

    if not isinstance(action, TransferItemAction):
        return _unbound("Action is not a transfer_item action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the transfer.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the transfer cannot be bound.")
    if not telemetry.ui.open_inventories_complete:
        return _unbound(
            "The open-inventory export is incomplete, so the source and "
            "destination cannot both be proved open."
        )
    held = {inventory.owner_id: inventory for inventory in telemetry.ui.open_inventories}
    source = held.get(action.source_owner_id)
    destination = held.get(action.destination_owner_id)
    if source is None:
        return _unbound(
            f"The inventory of {action.source_owner_id!r} is not currently open; "
            "open it before transferring out of it."
        )
    if destination is None:
        return _unbound(
            f"The inventory of {action.destination_owner_id!r} is not currently "
            "open; open it before transferring into it."
        )
    sections = {section.name: section for section in source.sections}
    section = sections.get(action.section_name)
    if section is None:
        return _unbound(
            f"{source.owner_name!r} has no section {action.section_name!r}. "
            f"Sections: {', '.join(sorted(sections)) or 'none reported'}."
        )
    slot = next(
        (
            item
            for item in section.items
            if item.x == action.slot_x and item.y == action.slot_y
        ),
        None,
    )
    if slot is None:
        return _unbound(
            f"No item sits at ({action.slot_x}, {action.slot_y}) in "
            f"{action.section_name!r} of {source.owner_name!r}."
        )
    if slot.item_name != action.item_name:
        # Position alone is not identity. An inventory that shifted between the
        # offer and the dispatch would otherwise transfer whatever moved into
        # that slot.
        return _unbound(
            f"({action.slot_x}, {action.slot_y}) in {action.section_name!r} now "
            f"holds {slot.item_name!r}, not {action.item_name!r}."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound {slot.item_name!r} at ({action.slot_x}, {action.slot_y}) in "
            f"{action.section_name!r} of {source.owner_name!r} for transfer to "
            f"{destination.owner_name!r}. Whether Kenshi permits it is Kenshi's "
            "answer at dispatch."
        ),
        target_id=action.source_owner_id,
        resolved_label=f"{slot.item_name} to {destination.owner_name}",
        source_revision=observation.world_revision,
    )


def transfer_item_is_currently_authorable(observation: Observation) -> bool:
    """Whether two inventories are open with something in one of them."""

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    inventories = telemetry.ui.open_inventories
    if len(inventories) < 2:
        return False
    return any(section.items for inventory in inventories for section in inventory.sections)


def context_inventory_is_currently_authorable(observation: Observation) -> bool:
    """Whether anything observed could have its inventory opened at all."""

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    if telemetry.ui.dialogue_open is not False:
        return False
    return bool(
        telemetry.squad
        or telemetry.nearby_entities
        or telemetry.world_targets
        or telemetry.discovered_objects
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
        whole_group_present=len(selected) == 1,
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
        whole_group_present=len(selected) == 1,
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
    selected = [character for character in telemetry.squad if character.selected]
    location_authoritative = "game.location.identity" in telemetry.capabilities
    return bool(
        not observation.telemetry_stale
        and telemetry.game.loaded is True
        and bool(selected)
        and any(
            map_destination_travel_available(
                destination,
                current_location_id=telemetry.game.location_id,
                inside_town_walls=telemetry.game.inside_town_walls,
                location_authoritative=location_authoritative,
                whole_group_present=len(selected) == 1,
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
    # The native command this operation dispatches, or empty when it sends none.
    # Declared rather than derived: see `native_wire_command_for`.
    wire_command: str = ""
    # How this action becomes wire fields. One mapping, used in both directions:
    # forward it is the request, backward an acknowledgement matches iff it
    # carries the same fields. They used to be separate hand-written chains, and
    # `perform_character_order` was missing from both - the request dropped its
    # order name and the matcher returned a silent False about an order the game
    # had already carried out. One function cannot be half-added.
    project_wire_fields: WireFieldFactory | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    handler_key: str = ""
    emits_world_command: bool = True
    requires_fresh_telemetry: bool = True
    # How this operation addresses Kenshi. Exactly one of these is populated:
    # a static contract, or a resolver for operations whose scope depends on
    # the exact action - `perform_context_action` needs different contracts for
    # different context semantics, so one contract per operation kind would be
    # a lie. The resolver receives an optional observation because authorability
    # is asked before any action exists.
    interaction: OperationInteractionContract | None = None
    resolve_interaction: InteractionContractFactory | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # A resolver's invariant recipient scope, declared once so authorability can
    # be answered before an action exists. Only meaningful with a resolver.
    _dynamic_recipient_scope: RecipientScope = RecipientScope.NONE
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
        if (self.interaction is None) == (self.resolve_interaction is None):
            raise ValueError(
                f"Operation {self.kind!r} must declare exactly one of a static "
                "interaction contract or a contract resolver."
            )

    def interaction_for(
        self,
        action: Action | None = None,
        observation: Observation | None = None,
    ) -> OperationInteractionContract:
        """Resolve this operation's sole interaction contract.

        The registry is the only place recipient scope is decided. Transport
        validation and native parsing consume the resolved contract; they never
        keep their own command-name exception lists.
        """

        if self.interaction is not None:
            return self.interaction
        if self.resolve_interaction is None:  # pragma: no cover - __post_init__ forbids
            raise RuntimeError(f"Operation {self.kind!r} resolves no interaction contract.")
        if action is None:
            raise ValueError(
                f"Operation {self.kind!r} resolves its interaction contract from the "
                "exact action, which was not supplied."
            )
        return self.resolve_interaction(action, observation)

    def recipient_scope_for(
        self,
        action: Action | None = None,
        observation: Observation | None = None,
    ) -> RecipientScope:
        """The scope this operation addresses, for authorability and dispatch."""

        if self.interaction is not None:
            return self.interaction.recipient_scope
        if action is None:
            # A dynamic contract cannot be resolved without its action. Every
            # current resolver keeps recipient scope fixed across its subcases
            # and varies only the milestone, so the shared scope is exact rather
            # than a guess; a resolver that varies scope must declare it here.
            return self._dynamic_recipient_scope
        return self.interaction_for(action, observation).recipient_scope

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
        """Whether this operation could be dispatched against this observation.

        Affordance enumeration consults this, so the planner is never offered a
        choice its own definition would refuse. Before Slice 1 these were two
        authorities that disagreed: a live two-character start offered
        `harvest_resource` on an iron deposit that could not be harvested,
        because enumeration never asked.
        """

        if observation is None:
            return True
        if not self.satisfies_recipient_scope(observation):
            return False
        return self.authorable_when is None or self.authorable_when(observation)

    def satisfies_recipient_scope(
        self,
        observation: Observation,
        action: Action | None = None,
    ) -> bool:
        """Whether the current selection can supply this contract's recipients.

        `CURRENT_SELECTION` needs at least one selected character and a primary
        within that set. `PRIMARY` needs an exported primary. Crucially, neither
        demands a singleton: an order that broadcasts to the selection is not
        made invalid by a second character being selected.
        """

        scope = self.recipient_scope_for(action, observation)
        if scope is RecipientScope.NONE:
            return True
        telemetry = observation.telemetry
        if telemetry is None or observation.telemetry_stale:
            return False
        selected_ids = telemetry.ui.selected_character_ids
        primary_id = telemetry.ui.selected_character_id
        if scope is RecipientScope.PRIMARY:
            return bool(primary_id) and primary_id in selected_ids
        if scope is RecipientScope.CURRENT_SELECTION:
            return bool(selected_ids) and primary_id in selected_ids
        if scope is RecipientScope.NAMED_BODY:
            # Deliberately no roster and no selection requirement. This scope
            # exists for the moment both are gone: every character dead, the
            # squad empty, nothing selected. Demanding recipients here would
            # make the only recovering operation unreachable exactly when it is
            # the whole point.
            return True
        # EXPLICIT_RECIPIENTS names its own characters through the typed action
        # or binding, so it needs a roster rather than a particular selection.
        return bool(telemetry.squad)


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
    # Who this operation was authored to command. Identity previously covered
    # the definition, the typed action, the affordance, and the binding - none
    # of which mention who acts - so an order authored while A and B were
    # selected could be delivered to C after a lease wait without changing any
    # fingerprint.
    #
    # Deliberately outside structural equality and the fingerprint. A recipient
    # change has its own typed refusal, which names who the order would have
    # gone to instead; folding it into the fingerprint as well reported it as a
    # generic identity change and, worse, made an operation bound without an
    # observation differ from its own rebind purely because one could capture a
    # basis and the other could not.
    recipient_basis: AuthoredRecipientBasis | None = field(default=None, compare=False)


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


def capture_recipient_basis(
    definition: OperationDefinition,
    action: Action,
    observation: Observation | None,
) -> AuthoredRecipientBasis | None:
    """Record who this operation was authored to command.

    Read from Kenshi's exported primary and selection, not from roster order:
    `ui.selected_character_id` is the primary, and the first selected member of
    `telemetry.squad` is merely the first one the exporter happened to walk.
    """

    scope = definition.recipient_scope_for(action, observation)
    if scope is RecipientScope.NAMED_BODY:
        # The body named by the action is the recipient - it is what the order
        # acts on and what the agent becomes. `target_id` is deliberately not a
        # general recipient field: for every other operation the target is the
        # object acted upon rather than the character commanded, and conflating
        # those would make an iron deposit a recipient.
        named = getattr(action, "target_id", None)
        return AuthoredRecipientBasis.capture(
            scope,
            primary=None,
            selection=(),
            explicit_recipients=(named,) if isinstance(named, str) and named else (),
        )
    if scope is RecipientScope.EXPLICIT_RECIPIENTS:
        return AuthoredRecipientBasis.capture(
            scope,
            primary=None,
            selection=(),
            explicit_recipients=explicit_recipients_of(action),
        )
    if scope is RecipientScope.NONE:
        return AuthoredRecipientBasis.capture(scope, primary=None, selection=())
    telemetry = None if observation is None else observation.telemetry
    if telemetry is None:
        # No evidence to author against. Recording an empty basis would claim
        # the operation was authored for nobody, which a later populated basis
        # would then contradict; absent is the honest answer.
        return None
    return AuthoredRecipientBasis.capture(
        scope,
        primary=telemetry.ui.selected_character_id,
        selection=telemetry.ui.selected_character_ids,
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
    observation: Observation | None = None,
) -> OperationIdentity:
    """Build the one immutable identity used at scheduling and dispatch.

    `observation` supplies the evidence the recipient basis is captured from.
    Without it the basis is absent rather than empty: an empty basis would
    claim the operation was authored to command nobody, and a later populated
    basis would then read as a change that never happened.
    """

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
    recipient_basis = capture_recipient_basis(definition, operation, observation)
    identity_payload = {
        "definition": {
            "kind": definition.kind,
            "version": definition.version,
            "handler_key": definition.handler_key,
        },
        "operation": operation_hash,
        "affordance": affordance_hash,
        "binding": binding_hash,
        # The resolved contract itself, so an operation whose interaction shape
        # changed between authoring and dispatch is a different operation.
        "interaction": definition.interaction_for(operation, observation).fingerprint(),
    }
    return OperationIdentity(
        fingerprint=_fingerprint("operation", identity_payload),
        operation_kind=operation.kind,
        definition_version=definition.version,
        handler_key=definition.handler_key,
        operation_fingerprint=operation_hash,
        affordance_fingerprint=affordance_hash,
        binding_fingerprint=binding_hash,
        recipient_basis=recipient_basis,
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
        # Cognition and run control reach no part of Kenshi and command nobody.
        interaction=runtime_only(),
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
    interaction: OperationInteractionContract,
    execution: OperationExecution = OperationExecution.ATOMIC_HANDLER,
    pointer_class: PointerActionClass = PointerActionClass.COORDINATE_INDEPENDENT,
) -> OperationDefinition:
    return OperationDefinition(
        kind=kind,
        version="1.0",
        interaction=interaction,
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
    # Playback is game-wide: it suspends every character and every retained
    # order at once. Its terminal is the observed world state, not delivery.
    interaction=global_ui(milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED),
)
SET_SPEED_DEFINITION = _runtime_control_definition(
    kind="set_speed",
    operation_type=SetSpeedAction,
    summary="Request one exact Kenshi playback gear.",
    handler_key="runtime.set_speed",
    interaction=global_ui(milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED),
)
WAIT_DEFINITION = _runtime_control_definition(
    kind="wait",
    operation_type=WaitAction,
    summary="Observe for one bounded interval without sending input.",
    handler_key="runtime.wait",
    # Waiting sends nothing, but it is only meaningful while the world runs.
    interaction=runtime_only(playback=PlaybackRequirement.RUNNING_FOR_PROGRESS),
)
APPROACH_DIALOGUE_TARGET_DEFINITION = OperationDefinition(
    kind="approach_dialogue_target",
    wire_command="approach_confirmed_vendor",
    project_wire_fields=_wire_target(),
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.CURRENT_SELECTION,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
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





SELECT_SQUAD_MEMBER_EXACT_DEFINITION = OperationDefinition(
    kind="select_squad_member_exact",
    wire_command="select_squad_member",
    project_wire_fields=_wire_target(),
    version="1.0",
    interaction=selection_mutation(),
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




PERFORM_CONTEXT_ACTION_DEFINITION = OperationDefinition(
    kind="perform_context_action",
    wire_command="perform_context_action",
    project_wire_fields=_wire_context_action,
    version="1.0",
    resolve_interaction=resolve_context_action_interaction,
    _dynamic_recipient_scope=RecipientScope.CURRENT_SELECTION,
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
    wire_command="produce_resource_output",
    project_wire_fields=_wire_resource_output,
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.CURRENT_SELECTION,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
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



PERFORM_CHARACTER_ORDER_DEFINITION = OperationDefinition(
    kind="perform_character_order",
    wire_command="perform_character_order",
    project_wire_fields=_wire_character_order,
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.CURRENT_SELECTION,
        milestone=CompletionMilestone.ORDER_ACCEPTED,
    ),
    operation_type=PerformCharacterOrderAction,
    summary=(
        "Issue one order Kenshi already advertises on one exact nearby person. "
        "Which orders a person affords is the game's own answer, published per "
        "target, so attacking, looting, and aiding are this one operation under "
        "different names rather than separate verbs with separate fences."
    ),
    argument_source=(
        "target_id must be an exact currently observed nearby character whose "
        "advertised tasks were probed this observation; order must be one of that "
        "person's advertised task names, lowercased, exactly as telemetry reported it."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            "nearby.characters",
            NEARBY_ORDERABLE_TASKS_CAPABILITY,
            NATIVE_CHARACTER_ORDER_CAPABILITY,
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=1,
    reference_fields=("target_id", "order"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.MONITORED_OPTION,
    receipt_kind="semantic_character_order",
    bind=bind_perform_character_order,
    handler_key="movement.perform_character_order",
    controller_verified=True,
    authorable_when=perform_character_order_is_currently_authorable,
)

RESPOND_TO_IMMEDIATE_THREAT_DEFINITION = OperationDefinition(
    kind="respond_to_immediate_threat",
    wire_command="move_in_direction",
    project_wire_fields=_wire_direction,
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.EXPLICIT_RECIPIENTS,
        milestone=CompletionMilestone.ORDER_ACCEPTED,
    ),
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



OPEN_TRADE_WINDOW_DEFINITION = OperationDefinition(
    kind="open_trade_window",
    wire_command="open_trade_window",
    project_wire_fields=_wire_trade_window,
    version="1.0",
    interaction=global_ui(
        recipients=RecipientScope.NONE,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
        selection=SelectionDependency.NONE,
        playback=PlaybackRequirement.PAUSED_TRANSACTION,
    ),
    operation_type=OpenTradeWindowAction,
    summary=(
        "Open two inventories side by side, which is the state a transfer acts "
        "in. Kenshi's own window types are money_trading, looting and auto; the "
        "single-inventory opener shows a character's personal gear instead, "
        "which is the stealing view and cannot host a transfer."
    ),
    argument_source=(
        "first_owner_id and second_owner_id are copied from any observed squad "
        "member, nearby character, or world target."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {NATIVE_TRADE_WINDOW_CAPABILITY, "identity.stable_handles"}
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=("first_owner_id", "second_owner_id", "window_type"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_context_inventory",
    bind=bind_open_trade_window,
    handler_key="resources.open_trade_window",
    controller_verified=True,
    native_terminal_success_reasons=frozenset({"trade_window_open"}),
    # Kenshi records the pairing and opens both windows on a later GUI update,
    # so acceptance is not the outcome: the terminal is two windows observed.
    native_task_started_reasons=frozenset({"trade_window_requested"}),
    authorable_when=trade_window_is_currently_authorable,
)


TRANSFER_ITEM_DEFINITION = OperationDefinition(
    kind="transfer_item",
    wire_command="transfer_item",
    project_wire_fields=_wire_transfer,
    version="1.0",
    interaction=global_ui(
        recipients=RecipientScope.NONE,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
        selection=SelectionDependency.NONE,
        playback=PlaybackRequirement.PAUSED_TRANSACTION,
    ),
    operation_type=TransferItemAction,
    summary=(
        "Move one item between two open inventories, whatever owns them. "
        "Looting a body, buying from a shop, handing something to a squadmate "
        "and emptying a crate are the same act with different owners, and "
        "Kenshi performs all four through one call. Open both inventories "
        "first, then name the source slot. Whether the transfer is allowed is "
        "Kenshi's answer, reported verbatim: no_room, cant_afford, thats_mine, "
        "thief_detected, locked, container_not_empty and the rest. Success "
        "requires an observed move, not merely a permitted one."
    ),
    argument_source=(
        "Copy source_owner_id and destination_owner_id from open_inventories, "
        "and section_name, slot_x, slot_y, item_name from one item in the "
        "source's own sections."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_TRANSFER_CAPABILITY,
            "ui.inventory",
            "identity.stable_handles",
        }
    ),
    capability_aliases=frozenset(),
    # No pointer at all. The five operations this replaces spent twenty-three
    # pointer actions between them, twelve of those in `harvest_resource`.
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=(
        "source_owner_id",
        "destination_owner_id",
        "section_name",
        "slot_x",
        "slot_y",
        "item_name",
    ),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_item_transfer",
    bind=bind_transfer_item,
    handler_key="resources.transfer_item",
    controller_verified=True,
    native_terminal_success_reasons=frozenset({"item_transferred"}),
    authorable_when=transfer_item_is_currently_authorable,
)


REGROUP_WITH_SQUAD_MEMBER_DEFINITION = OperationDefinition(
    kind="regroup_with_squad_member",
    wire_command="regroup_with_squad_member",
    project_wire_fields=_wire_target(),
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.EXPLICIT_RECIPIENTS,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
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
    wire_command="move_in_direction",
    project_wire_fields=_wire_direction,
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.CURRENT_SELECTION,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
    operation_type=MoveInDirectionAction,
    summary=(
        "Walk a bearing and distance from where the character stands, ordering "
        "a walk to a bare point rather than toward anyone. One monitored option "
        "owns the targetless native order through its exact command vector. "
        "Native completion is reported as walk_destination_reached."
        "A character who is down but conscious is crawling, not immobilised: in Kenshi only "
        "unconsciousness stops movement, and legs damaged past the knockout point make a "
        "character crawl until bandaged rather than stop. Crawling is slow, and it is still "
        "movement - waiting to heal before moving is a choice, not a requirement. "
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
    wire_command="travel_to_map_destination",
    project_wire_fields=_wire_target("destination_id"),
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.CURRENT_SELECTION,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
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

SURVEY_LOCAL_RESOURCES_DEFINITION = OperationDefinition(
    kind="survey_local_resources",
    wire_command="survey_local_resources",
    project_wire_fields=_wire_nothing,
    version="1.0",
    # A survey reads the world; it commands nobody, so it has no recipients.
    #
    # It was declared PRIMARY, which makes the authority demand a singleton
    # selection - and a live run rejected every survey the pair attempted with
    # "requires one exact primary selected character". Reading the resource
    # field where a character stands needs an exported primary to locate the
    # centre, which the binder proves, and needs nothing at all about how many
    # others happen to be selected. Demanding at bind time what no phase uses
    # is exactly the defect section 19.4 recorded against harvest.
    interaction=global_ui(
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
    operation_type=SurveyLocalResourcesAction,
    summary=(
        "Survey Kenshi's resource field around the primary character and return "
        "it as a grid. This is the reading the game's Prospecting window is "
        "built from, without that window's averaging: a single area-wide number "
        "reports 0 for a discrete deposit, while a grid keeps which direction "
        "the deposit lies in. Surveying is an action with a position, so the "
        "agent learns what it surveyed rather than what exists everywhere."
    ),
    argument_source=(
        "No arguments. The primary character's current position is the survey "
        "centre."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    # The survey dispatches a native command, so it must declare the control
    # capability that command needs. It previously declared none, and option
    # preparation's private capability map silently fell back to the direction
    # capability - so a survey was admitted by a capability it never used, and
    # would have kept being admitted had the plug-in stopped implementing it.
    required_capabilities=frozenset(
        {
            "squad.basic",
            "identity.stable_handles",
            NATIVE_RESOURCE_SURVEY_CAPABILITY,
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    # Reading the resource field mutates nothing, so a repeated survey is safe
    # and costs only the reading.
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=0,
    reference_fields=(),
    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="resource_survey",
    bind=bind_survey_local_resources,
    handler_key="movement.survey_local_resources",
    controller_verified=True,
    native_terminal_success_reasons=frozenset({"resource_survey_published"}),
    authorable_when=survey_local_resources_is_currently_authorable,
)

EXIT_CURRENT_BUILDING_DEFINITION = OperationDefinition(
    kind="exit_current_building",
    wire_command="exit_current_building",
    project_wire_fields=_wire_nothing,
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.CURRENT_SELECTION,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
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

SHIFT_INTO_BODY_DEFINITION = OperationDefinition(
    kind="shift_into_body",
    wire_command="shift_into_body",
    project_wire_fields=_wire_target(),
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.NAMED_BODY,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
    operation_type=ShiftIntoBodyAction,
    summary=(
        "Become one exact currently observed conscious, non-hostile character. "
        "Control follows selection rather than roster membership, so entering a "
        "body means joining the player faction and becoming the selected "
        "primary; the body is placed in its own squad, so the bodies left "
        "behind stay their own unit instead of accumulating as followers. This "
        "is what makes losing every character survivable rather than terminal."
    ),
    argument_source=(
        "target_id must be an exact id from the observation's telemetry.nearby_entities, "
        "naming a conscious non-animal character who is not hostile."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {
            NATIVE_SHIFT_BODY_CAPABILITY,
            "identity.stable_handles",
            "nearby.characters",
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=4,
    reference_fields=("target_id",),
    # Entering a body is not repeatable against the same evidence: once it
    # succeeds the observation that authorized it describes a world the agent is
    # no longer standing in.
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_body_shift",
    # What Kenshi says when a body has actually been entered. Declaring these is
    # what lets the handler tell a completed shift from a dispatched one: the
    # first live run switched bodies three times and recorded three failures,
    # because the terminal check had nothing to match and correctly refused to
    # call an unnamed outcome a success.
    native_terminal_success_reasons=frozenset(
        {
            "shift_body_recruited",
            "shift_body_recruited_forced",
            "shift_body_already_held",
        }
    ),
    bind=bind_shift_into_body,
    handler_key="movement.shift_into_body",
    controller_verified=True,
    authorable_when=shift_into_body_is_currently_authorable,
)

MOVE_TO_CHARACTER_DEFINITION = OperationDefinition(
    kind="move_to_character",
    wire_command="move_to_character",
    project_wire_fields=_wire_target(),
    version="1.0",
    interaction=ordinary_order(
        recipients=RecipientScope.CURRENT_SELECTION,
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
    ),
    operation_type=MoveToCharacterAction,
    summary=(
        "Walk the complete current selection to one exact currently observed "
        "nearby character without talking to them. This is how the agent goes "
        "somewhere: nearby characters are reported within four hundred units, "
        "so someone standing where you want to be is a destination. One "
        "monitored option owns the whole group walk."
        "A character who is down but conscious is crawling, not immobilised: in Kenshi only "
        "unconsciousness stops movement, and legs damaged past the knockout point make a "
        "character crawl until bandaged rather than stop. Crawling is slow, and it is still "
        "movement - waiting to heal before moving is a choice, not a requirement. "
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
    SELECT_SQUAD_MEMBER_EXACT_DEFINITION,
    PERFORM_CONTEXT_ACTION_DEFINITION,
    PRODUCE_RESOURCE_OUTPUT_DEFINITION,
    PERFORM_CHARACTER_ORDER_DEFINITION,
    RESPOND_TO_IMMEDIATE_THREAT_DEFINITION,
    OPEN_TRADE_WINDOW_DEFINITION,
    TRANSFER_ITEM_DEFINITION,
    REGROUP_WITH_SQUAD_MEMBER_DEFINITION,
    MOVE_TO_CHARACTER_DEFINITION,
    SHIFT_INTO_BODY_DEFINITION,
    MOVE_IN_DIRECTION_DEFINITION,
    TRAVEL_TO_MAP_DESTINATION_DEFINITION,
    EXIT_CURRENT_BUILDING_DEFINITION,
    SURVEY_LOCAL_RESOURCES_DEFINITION,
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
