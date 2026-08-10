"""The sole definition and binding authority for private runtime operations.

An operation definition owns policy, risk, terminal authority, handler identity,
and exact current-state binding. Affordance adapters invoke these definitions
directly; no second contract language reconstructs an operation's meaning.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields
from enum import Enum, StrEnum
from hashlib import sha256
from typing import Literal, TypeAlias, TypeVar, cast

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
    GAME_SPEED_MULTIPLIER_BY_GEAR,
    Action,
    ApproachDialogueTargetAction,
    CloseActiveInterfaceAction,
    ConsultAdvisorAction,
    ControlMode,
    ExitCurrentBuildingAction,
    IdempotencyPolicy,
    MoveInDirectionAction,
    MoveToCharacterAction,
    NoopAction,
    OpenTradeWindowAction,
    PauseAction,
    PerformCharacterOrderAction,
    PerformContextAction,
    PointerActionClass,
    ProduceResourceOutputAction,
    ReadFieldbookAction,
    RecallMemoryAction,
    RegroupWithSquadMemberAction,
    RespondToImmediateThreatAction,
    SelectDialogueOptionAction,
    SelectSquadMemberExactAction,
    SetSpeedAction,
    ShiftIntoBodyAction,
    StopAction,
    SurveyLocalResourcesAction,
    ThreatResponseStrategy,
    TransferItemAction,
    TravelToMapDestinationAction,
    WaitAction,
)
from .core.planning import (
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionPath,
)
from .core.telemetry import (
    TRADE_WINDOW_AUTHORING_DISTANCE,
    ContextActionKind,
    Disposition,
    NearbyEntity,
    WorldTarget,
    dialogue_targets,
    inventory_owner_distance_from_primary,
    inventory_owner_is_within_trade_authoring_distance,
    map_destination_already_reached,
    map_destination_travel_available,
)
from .core.world import WorldStateRevision
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
NATIVE_RESOURCE_OPERATOR_STATE_CAPABILITY = "world.resource_operators"
NATIVE_PRODUCE_RESOURCE_CAPABILITY = "control.produce_resource_output"
NATIVE_TRANSFER_CAPABILITY = "control.transfer_item"
NATIVE_TRADE_WINDOW_CAPABILITY = "control.open_trade_window"
NATIVE_CLOSE_INTERFACE_CAPABILITY = "control.close_active_interface"
NATIVE_DIALOGUE_OPTION_CAPABILITY = "control.select_dialogue_option"

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
    direction = cast(MoveInDirectionAction, action)
    return {
        "bearing_degrees": direction.bearing_degrees,
        "distance_units": direction.distance_units,
    }


def _wire_nothing(action: Action) -> WireFields:
    del action
    return {}


def _wire_context_action(action: Action) -> WireFields:
    context = cast(PerformContextAction, action)
    return {
        "target_id": context.target_id,
        "context_action": str(context.context_action),
    }


def _wire_character_order(action: Action) -> WireFields:
    # The order is part of the identity, not decoration. One person can afford
    # several orders at once, so a match on target alone would let either
    # satisfy a wait for the other.
    order = cast(PerformCharacterOrderAction, action)
    return {"target_id": order.target_id, "context_action": order.order}


def _wire_resource_output(action: Action) -> WireFields:
    output = cast(ProduceResourceOutputAction, action)
    return {
        "target_id": output.target_id,
        "minimum_output_quantity": output.minimum_output_quantity,
    }


def _wire_trade_window(action: Action) -> WireFields:
    window = cast(OpenTradeWindowAction, action)
    return {
        "target_id": window.first_owner_id,
        "destination_id": window.second_owner_id,
        "context_action": window.window_type,
    }


def _wire_transfer(action: Action) -> WireFields:
    transfer = cast(TransferItemAction, action)
    return {
        "target_id": transfer.source_owner_id,
        "destination_id": transfer.destination_owner_id,
        "section_name": transfer.section_name,
        "slot_x": transfer.slot_x,
        "slot_y": transfer.slot_y,
    }


def _wire_dialogue_option(action: Action) -> WireFields:
    option = cast(SelectDialogueOptionAction, action)
    return {
        "target_id": option.dialogue_target_id,
        "dialogue_option_index": option.option_index,
        "dialogue_option_text": option.option_text,
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
    "dialogue_option_index": -1,
    "dialogue_option_text": "",
    "paused": False,
    "speed_multiplier": 0.0,
    "quantity": 0,
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
class BoundNamedOperation:
    reason: str
    resolved_label: str
    source_revision: WorldStateRevision
    bound: Literal[True] = field(default=True, init=False)

OperationBinding: TypeAlias = (
    BindingFailure
    | EmptyBinding
    | BoundActor
    | BoundNamedTarget
    | BoundNamedOperation
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


def active_interface_is_open(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    ui = telemetry.ui
    return bool(
        ui.dialogue_open is True
        or ui.modal_open is True
        or (ui.open_inventory_windows or 0) > 0
        or ui.stats_window_open is True
        or ui.prospecting_window_open is True
        or ui.management_screen_open is True
        or (ui.active_screen is not None and ui.active_screen != "world")
    )


def bind_close_active_interface(
    action: Action,
    observation: Observation,
) -> BoundNamedOperation | BindingFailure:
    if not isinstance(action, CloseActiveInterfaceAction):
        return _unbound("Action is not a close_active_interface action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind interface cleanup.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so interface cleanup cannot be bound.")
    if telemetry.game.loaded is not True:
        return _unbound("No loaded world has an interface to close.")
    if not active_interface_is_open(observation):
        return _unbound("The world interface is already unobstructed.")
    return BoundNamedOperation(
        reason=(
            "Bound to the current blocking interface state; native cleanup must "
            "return dialogue, modal, inventory, and management signals to world."
        ),
        resolved_label=telemetry.ui.active_screen or "blocking_interface",
        source_revision=observation.world_revision,
    )


def dialogue_option_is_currently_authorable(observation: Observation) -> bool:
    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    ui = telemetry.ui
    return bool(
        ui.dialogue_open is True
        and ui.dialogue_target_id
        and ui.dialogue_options is not None
        and ui.dialogue_options
    )


def bind_select_dialogue_option(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    if not isinstance(action, SelectDialogueOptionAction):
        return _unbound("Action is not a select_dialogue_option action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the dialogue reply.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the dialogue reply cannot be bound.")
    ui = telemetry.ui
    if ui.dialogue_open is not True or ui.active_screen != "dialogue":
        return _unbound("No current dialogue interface is open.")
    if ui.dialogue_target_id != action.dialogue_target_id:
        return _unbound("The current dialogue target does not match the offered reply.")
    if ui.dialogue_options is None:
        return _unbound("Current dialogue options are unavailable rather than empty.")
    if action.option_index >= len(ui.dialogue_options):
        return _unbound("The offered dialogue reply index is no longer present.")
    current_text = ui.dialogue_options[action.option_index]
    if current_text != action.option_text:
        return _unbound(
            "The dialogue reply list changed; the exact offered caption no longer "
            "occupies its observed index."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound exact dialogue reply {action.option_index} for target "
            f"{action.dialogue_target_id}: {action.option_text!r}."
        ),
        target_id=action.dialogue_target_id,
        resolved_label=action.option_text,
        source_revision=observation.world_revision,
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
    # The authority travels into the reason. A receipt therefore proves the
    # game's own context menu offered the order rather than merely saying
    # "Kenshi advertised it" without naming the reader.
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
    actor_id = telemetry.primary_character_id
    if not actor_id:
        return _unbound("A survey requires an exported primary character.")
    actor = next(
        (member for member in telemetry.roster if member.id == actor_id),
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
    primary = telemetry.primary_character_id
    return bool(
        telemetry.game.loaded is True
        and primary
        and primary in telemetry.selected_character_ids
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
    selected_ids = telemetry.selected_character_ids
    if not selected_ids or telemetry.primary_character_id not in selected_ids:
        return _unbound(
            "Native squad selection requires one or more exact current squad "
            "selections as its causal basis."
        )
    matches = [character for character in telemetry.roster if character.id == action.target_id]
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
        and bool(telemetry.selected_character_ids)
        and telemetry.primary_character_id in telemetry.selected_character_ids
        and any(
            bind_select_squad_member_exact(
                SelectSquadMemberExactAction(target_id=character.id),
                observation,
            ).bound
            for character in telemetry.roster
        )
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
    if NATIVE_RESOURCE_OPERATOR_STATE_CAPABILITY not in telemetry.capabilities:
        return None, _unbound(
            "Exact resource capacity and accepted operators are not available."
        )
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
    if (
        target.operator_capacity is None
        or target.operator_capacity < 1
        or not target.current_operators_complete
    ):
        return None, _unbound(
            f"Target {target.name!r} does not expose complete engine operator "
            "capacity and identity state."
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
    if not target.output_inventory_complete:
        return _unbound(
            f"Target {target.name!r} does not expose a complete output inventory."
        )
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
            f"Bound retained production to {target.name!r} ({target.id}) with "
            f"engine capacity {target.operator_capacity} and accepted operators "
            f"{target.current_operator_ids!r}; selected recipients and queued work "
            "are not operator acceptance, and output inventory is terminal proof."
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
        and NATIVE_RESOURCE_OPERATOR_STATE_CAPABILITY in telemetry.capabilities
        and any(
            target.kind == "natural_resource"
            and ContextActionKind.OPERATE in target.context_actions
            and target.default_task == "operate_machinery"
            and target.operator_capacity is not None
            and target.operator_capacity > 0
            and target.current_operators_complete
            and target.output_inventory_complete
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
    for member in telemetry.roster:
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




def bind_open_trade_window(
    action: Action,
    observation: Observation,
) -> BoundNamedTarget | BindingFailure:
    """Bind two observed parties whose inventories should be paired.

    Both must be observed and the second owner must be locally interactable
    from the current primary. The native dispatcher rechecks this conservative
    position fence before opening anything; Kenshi's private exact trade-range
    predicate remains the terminal after the windows exist.
    """

    if not isinstance(action, OpenTradeWindowAction):
        return _unbound("Action is not an open_trade_window action.")
    telemetry = observation.telemetry
    if telemetry is None:
        return _unbound("No telemetry is available to bind the trade window.")
    if observation.telemetry_stale:
        return _unbound("Telemetry is stale, so the trade window cannot be bound.")
    if action.first_owner_id != telemetry.primary_character_id:
        return _unbound(
            "The first trade-window owner must be the exact current primary character."
        )
    if action.second_owner_id == action.first_owner_id:
        return _unbound("A trade window requires two distinct inventory owners.")
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
    distance = inventory_owner_distance_from_primary(
        telemetry,
        action.second_owner_id,
    )
    if distance is None:
        return _unbound(
            f"{second[0]!r} has no exact current distance, so mere observation "
            "cannot authorize an interaction window."
        )
    if distance > TRADE_WINDOW_AUTHORING_DISTANCE:
        return _unbound(
            f"{second[0]!r} is {distance:.1f} units away, outside the conservative "
            f"{TRADE_WINDOW_AUTHORING_DISTANCE:g}-unit trade-window authoring fence."
        )
    return BoundNamedTarget(
        reason=(
            f"Bound a {action.window_type} window pairing {first[0]!r} with "
            f"{second[0]!r} at {distance:.1f} units. Native dispatch rechecks "
            "that local fence before drawing anything, then Kenshi's exact "
            "trade-range predicate owns the terminal."
        ),
        target_id=action.first_owner_id,
        resolved_label=f"{first[0]} and {second[0]}",
        source_revision=observation.world_revision,
    )


def trade_window_is_currently_authorable(observation: Observation) -> bool:
    """Whether the primary has any currently local observed inventory owner."""

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return False
    if telemetry.ui.dialogue_open is not False:
        return False
    primary = telemetry.primary_character_id
    if primary is None:
        return False
    owner_ids = (
        [member.id for member in telemetry.roster if member.id != primary]
        + [entity.id for entity in telemetry.nearby_entities]
        + [target.id for target in telemetry.world_targets]
        + [found.id for found in telemetry.discovered_objects]
    )
    return any(
        inventory_owner_is_within_trade_authoring_distance(telemetry, owner_id)
        for owner_id in owner_ids
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
    selected = telemetry.selected_characters()
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
    selected = telemetry.selected_characters()
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
    selected = telemetry.selected_characters()
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
        member
        for member in telemetry.roster
        if member.id == action.actor_id
        and member.id in telemetry.selected_character_ids
    ]
    if (
        len(actor_matches) != 1
        or telemetry.primary_character_id != action.actor_id
        or telemetry.selected_character_ids != [action.actor_id]
    ):
        return _unbound("actor_id must be the one exact currently selected squad member.")
    actor = actor_matches[0]
    if actor.alive is not True or actor.conscious is not True or actor.down is True:
        return _unbound(f"Selected actor {actor.name!r} is not confirmed able to travel.")
    if action.target_id == action.actor_id:
        return _unbound("A squad member cannot regroup with itself.")
    target_matches = [member for member in telemetry.roster if member.id == action.target_id]
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
    selected = telemetry.selected_characters()
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
        for target in telemetry.roster
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
    selected = telemetry.selected_characters()
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
    selected = telemetry.selected_characters()
    if len(selected) != 1:
        return False
    probe = RespondToImmediateThreatAction(
        actor_id=selected[0].id,
        strategy=ThreatResponseStrategy.ENGAGE,
    )
    return threat_response_authority_error(probe, observation) is None


























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
    # Whether successful completion is evidence of progress for scheduler
    # liveness accounting. This is definition-owned so the coordinator never
    # needs to know which semantic operation is observe-only.
    counts_as_progress: bool = True
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
        selected_ids = telemetry.selected_character_ids
        primary_id = telemetry.primary_character_id
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
        return bool(telemetry.roster)


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
    `primary_character_id` is the primary, and the first selected member of
    `telemetry.roster` is merely the first one the exporter happened to walk.
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
        primary=telemetry.primary_character_id,
        selection=telemetry.selected_character_ids,
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














def _selected_squad_member(
    action: Action,
    observation: Observation,
) -> tuple[Condition, ...] | None:
    if not isinstance(action, SelectSquadMemberExactAction):
        return ()
    return (
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_ID,
            operator=ConditionOperator.EQUALS,
            expected=action.target_id,
            max_age_seconds=3.0,
            required_capabilities=["roster.basic"],
        ),
        Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_SELECTED_CHARACTER_COUNT,
            operator=ConditionOperator.EQUALS,
            expected=1,
            max_age_seconds=3.0,
            required_capabilities=["roster.basic"],
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
    counts_as_progress: bool = True,
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
        counts_as_progress=counts_as_progress,
        requires_fresh_telemetry=False,
        controller_verified=True,
    )


NOOP_DEFINITION = _runtime_cognitive_definition(
    kind="noop",
    operation_type=NoopAction,
    summary="Acknowledge that the current state requires no game input.",
    argument_source="The runtime offer supplies the optional reason.",
    handler_key="runtime.noop",
    max_primitive_actions=0,
    counts_as_progress=False,
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
            "roster.basic",
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
        "owns execution until Kenshi reports acceptance at the target. For a natural "
        "resource, acceptance means an exact selected identity entered the engine's "
        "current-operator set; selection or queued work alone never completes it."
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
    native_task_started_reasons=frozenset(
        {"context_task_started", "resource_operator_accepted"}
    ),
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
        "resource output inventory contains stock. Current operators come only from "
        "Kenshi's accepted-operator set; selection and queued work are not acceptance. "
        "Work issued by this option is fully cleared before its terminal; an already "
        "accepted selected operator is adopted and left player-owned."
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
            NATIVE_RESOURCE_OPERATOR_STATE_CAPABILITY,
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
    # Kenshi has accepted the ordinary order, but an order such as
    # PLAYER_TALK_TO still needs world time before its visible outcome can
    # occur. Native dispatch owns the 1x resume and Python waits for this
    # terminal without sending a playback key.
    native_task_started_reasons=frozenset({"context_task_started"}),
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
            "roster.health",
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
        "in, only after the other owner is inside a conservative local "
        "interaction fence. Kenshi's own window types are money_trading, looting and auto; the "
        "single-inventory opener shows a character's personal gear instead, "
        "which is the stealing view and cannot host a transfer. After opening, "
        "Kenshi's exact trade-range predicate is still the terminal authority."
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


CLOSE_ACTIVE_INTERFACE_DEFINITION = OperationDefinition(
    kind="close_active_interface",
    wire_command="close_active_interface",
    project_wire_fields=_wire_nothing,
    version="1.0",
    interaction=global_ui(milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED),
    operation_type=CloseActiveInterfaceAction,
    summary=(
        "Return from the current blocking interface to the world through Kenshi's "
        "own close methods. This covers Prospecting, dialogue, message boxes, "
        "trade and inventory windows, and ordinary GUI windows."
    ),
    argument_source="No arguments; current UI telemetry is the exact binding.",
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset({NATIVE_CLOSE_INTERFACE_CAPABILITY}),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=1,
    reference_fields=(),
    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_interface_close",
    bind=bind_close_active_interface,
    handler_key="resources.close_active_interface",
    controller_verified=True,
    native_terminal_success_reasons=frozenset({"active_interface_closed"}),
    authorable_when=active_interface_is_open,
)


SELECT_DIALOGUE_OPTION_DEFINITION = OperationDefinition(
    kind="select_dialogue_option",
    wire_command="select_dialogue_option",
    project_wire_fields=_wire_dialogue_option,
    version="1.0",
    interaction=global_ui(
        milestone=CompletionMilestone.WORLD_OUTCOME_OBSERVED,
        playback=PlaybackRequirement.PAUSED_TRANSACTION,
    ),
    operation_type=SelectDialogueOptionAction,
    summary=(
        "Choose one exact current dialogue reply through Kenshi's dialogue model. "
        "The conversation target, displayed index, and exact caption are all "
        "revalidated on the game thread before the reply is selected."
    ),
    argument_source=(
        "dialogue_target_id, option_index, and option_text are copied exactly from "
        "the currently open dialogue and its complete ordered option list."
    ),
    allowed_control_modes=frozenset({ControlMode.NATIVE_ASSISTED}),
    required_capabilities=frozenset(
        {NATIVE_DIALOGUE_OPTION_CAPABILITY, "ui.dialogue.options", "ui.dialogue.target"}
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=1,
    reference_fields=("dialogue_target_id", "option_index", "option_text"),
    idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    execution=OperationExecution.ATOMIC_HANDLER,
    receipt_kind="semantic_dialogue_reply",
    bind=bind_select_dialogue_option,
    handler_key="resources.select_dialogue_option",
    controller_verified=True,
    native_terminal_success_reasons=frozenset(
        {"dialogue_closed", "dialogue_target_changed", "dialogue_options_changed"}
    ),
    native_task_started_reasons=frozenset({"dialogue_option_selected"}),
    authorable_when=dialogue_option_is_currently_authorable,
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
        "and emptying a crate are one inventory-model act with different owners. "
        "Open both inventories first, then name the source slot. Native code "
        "removes from the source and tries to add to the destination; when a "
        "shop trade is open, the project charges a simplified buy or sell value "
        "from Item::getValueSingle. This does not reproduce Kenshi's theft, "
        "faction-standing, stolen-goods, or haggling rules. Success requires an "
        "observed move, not merely a returned call."
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
            "roster.basic",
            "roster.health",
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
    required_capabilities=frozenset({NATIVE_DIRECTION_CAPABILITY, "roster.health"}),
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
            "roster.health",
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
            "roster.basic",
            "identity.stable_handles",
            NATIVE_RESOURCE_SURVEY_CAPABILITY,
        }
    ),
    capability_aliases=frozenset(),
    pointer_class=PointerActionClass.COORDINATE_INDEPENDENT,
    native_assisted=True,
    # Reading the resource field changes no durable world state, so a repeated
    # survey is safe. It is nevertheless a monitored option: Kenshi advances a
    # progress bar before rendering the result window, and the native command
    # owns that temporal lifecycle through capture, close, and re-pause.
    risk=OperationRisk(native_assisted_actions=1),
    max_primitive_actions=0,
    reference_fields=(),
    idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
    execution=OperationExecution.MONITORED_OPTION,
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
            "roster.indoors",
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
    CLOSE_ACTIVE_INTERFACE_DEFINITION,
    SELECT_DIALOGUE_OPTION_DEFINITION,
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


def operations_count_as_progress(actions: Iterable[Action]) -> bool:
    """Whether any action's sole definition classifies it as progress."""

    for action in actions:
        definition = definition_for(action)
        # An unknown operation is never safe to classify as observe-only.
        if definition is None or definition.counts_as_progress:
            return True
    return False


def risk_for_operation(action: Action) -> OperationRisk | None:
    """Return one definition-owned risk declaration for plan accounting."""

    definition = definition_for(action)
    return definition.risk_for(action) if definition is not None else None
