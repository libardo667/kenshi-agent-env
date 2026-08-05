"""One runtime-generated contract for every playing-model possibility.

The playing model selects an offer from the current observation.  It does not
name an executor class, restate a UI binding, or author mechanical policy.  A
source adapter owns discovery and later materializes the selected offer into a
private executor operation after re-enumerating the same source.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from .core.affordance import (
    AffordanceExecution,
    AffordanceLifecycleEvent,
    AffordanceLifecycleStatus,
    AffordanceParameter,
    AffordanceReceipt,
    AffordanceSource,
    AffordanceTarget,
    BoundAffordance,
)
from .core.authority import AuthorizationCode
from .core.observation import Observation
from .core.operation import (
    TIME_GAME_BINDINGS,
    Action,
    CameraRotationDirection,
    ControlMode,
    GameBinding,
    GameScreen,
    PlanningMode,
    SingleStepRuntimeAction,
    ThreatResponseStrategy,
)
from .core.planning import screen_is_open
from .core.telemetry import (
    CharacterState,
    ContextActionKind,
    is_runtime_owned_visible_control,
    map_destination_travel_available,
    normalize_control_label,
)
from .non_progress import unchanged_definitive_no_op_reason
from .operation_definitions import (
    OPERATION_DEFINITIONS,
    SQUAD_REGROUP_ARRIVAL_DISTANCE,
    BindingFailure,
    BoundOperation,
    OperationDefinition,
    OperationExecution,
    TerminalOwner,
    definition_for,
    operation_identity,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class OperationBindingError(ValueError):
    """One typed failure from the sole fresh-binding authority."""

    def __init__(self, message: str, *, code: AuthorizationCode) -> None:
        super().__init__(message)
        self.code = code


def _operation_binding_error(reason: str) -> OperationBindingError:
    code = (
        AuthorizationCode.BINDING_AMBIGUOUS
        if "ambiguous" in reason.lower()
        else AuthorizationCode.BINDING_ABSENT
    )
    return OperationBindingError(reason, code=code)


class AffordanceParameterKind(StrEnum):
    INTEGER = "integer"
    NUMBER = "number"
    TEXT = "text"
    CHOICE = "choice"


SEMANTICALLY_ADAPTED_GAME_BINDINGS: frozenset[GameBinding] = frozenset(
    {
        GameBinding.TOGGLE_INVENTORY,
        GameBinding.TOGGLE_STATS,
        GameBinding.TOGGLE_MAP,
        GameBinding.TOGGLE_RESEARCH,
        GameBinding.TOGGLE_CRAFTING,
        GameBinding.CAMERA_ROTATE_LEFT,
        GameBinding.CAMERA_ROTATE_RIGHT,
        GameBinding.SELECT_ALL,
    }
)

# These controls address a changing portrait slot rather than a witnessed
# character identity. Keep them as actuator coverage, but do not offer them to
# the playing model when the native exact-identity route is available. The
# semantic route may itself be temporarily blocked by a modal; that is a reason
# to close the modal, not to resurrect an opaque policy bypass.
OPAQUE_CHARACTER_SELECTION_GAME_BINDINGS: frozenset[GameBinding] = frozenset(
    {
        GameBinding.CHARACTER_NEXT,
        GameBinding.CHARACTER_PREV,
        *(GameBinding[f"SELECT_GROUP_{index}"] for index in range(10)),
    }
)

# When an interface owns input, the playing model should see only choices that
# operate on that interface or on the run itself. World movement, camera, raw
# bindings, selection, and opening another screen are contradictory until the
# current modal reaches an explicit close terminal.
INTERFACE_SCOPED_OPERATION_KINDS: frozenset[str] = frozenset(
    {
        "activate_visible_control",
        "consult_advisor",
        "dismiss_screen",
        "equip_item",
        "noop",
        "purchase_item",
        "read_fieldbook",
        "recall_memory",
        "scroll_screen",
        "sell_item",
        "stop",
    }
)


class AffordanceParameterSpec(_StrictModel):
    name: str = Field(min_length=1, max_length=80)
    kind: AffordanceParameterKind
    description: str = Field(min_length=1, max_length=300)
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()


class AffordanceSelection(_StrictModel):
    """The entire game-action language exposed to the playing model."""

    affordance_id: str = Field(pattern=r"^aff-[0-9a-f]{20}$")
    target_id: str | None = Field(default=None, min_length=1, max_length=500)
    parameters: list[AffordanceParameter] = Field(default_factory=list, max_length=8)

    def parameter_map(self) -> dict[str, JsonValue]:
        return {parameter.name: parameter.value for parameter in self.parameters}


class AffordanceOffer(_StrictModel):
    affordance_id: str = Field(pattern=r"^aff-[0-9a-f]{20}$")
    source: AffordanceSource
    semantic: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    target: AffordanceTarget | None = None
    parameters: tuple[AffordanceParameterSpec, ...] = ()
    operation_kind: str = Field(min_length=1, max_length=80)
    operation_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    offered_at_telemetry_sequence: int = Field(ge=0)

    def planner_digest(self) -> dict[str, JsonValue]:
        """Project only semantic choice, exact target, and gameplay parameters."""

        return {
            "affordance_id": self.affordance_id,
            "semantic": self.semantic,
            "source": self.source.value,
            "description": self.description,
            "target": self.target.model_dump(mode="json") if self.target else None,
            "parameters": [
                parameter.model_dump(mode="json", exclude_none=True)
                for parameter in self.parameters
            ],
        }


def _offer_id(
    *,
    sequence: int,
    source: AffordanceSource,
    semantic: str,
    target_id: str | None,
    operation_kind: str,
    operation_arguments: dict[str, JsonValue],
) -> str:
    identity = json.dumps(
        {
            "sequence": sequence,
            "source": source.value,
            "semantic": semantic,
            "target_id": target_id,
            "operation_kind": operation_kind,
            "operation_arguments": operation_arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"aff-{sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _offer(
    observation: Observation,
    *,
    source: AffordanceSource,
    semantic: str,
    description: str,
    operation_kind: str,
    target: AffordanceTarget | None = None,
    parameters: tuple[AffordanceParameterSpec, ...] = (),
    arguments: dict[str, JsonValue] | None = None,
) -> AffordanceOffer:
    telemetry = observation.telemetry
    if telemetry is None:
        raise ValueError("cannot offer a game affordance without telemetry")
    operation_arguments = arguments or {}
    return AffordanceOffer(
        affordance_id=_offer_id(
            sequence=telemetry.sequence,
            source=source,
            semantic=semantic,
            target_id=target.target_id if target else None,
            operation_kind=operation_kind,
            operation_arguments=operation_arguments,
        ),
        source=source,
        semantic=semantic,
        description=description,
        target=target,
        parameters=parameters,
        operation_kind=operation_kind,
        operation_arguments=operation_arguments,
        offered_at_telemetry_sequence=telemetry.sequence,
    )


def _quantity_parameter(maximum: int = 5) -> AffordanceParameterSpec:
    return AffordanceParameterSpec(
        name="quantity",
        kind=AffordanceParameterKind.INTEGER,
        description="Gameplay quantity to attempt.",
        minimum=1,
        maximum=maximum,
    )


def _runtime_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    yield _offer(
        observation,
        source=AffordanceSource.RUNTIME,
        semantic="observe",
        description="Take no game input and re-evaluate current evidence.",
        operation_kind="noop",
        arguments={"reason": "Re-evaluate current evidence."},
    )
    yield _offer(
        observation,
        source=AffordanceSource.RUNTIME,
        semantic="stop_run",
        description="End the whole run at an explicit terminal boundary.",
        operation_kind="stop",
        arguments={"reason": "The selected objective is terminal."},
    )
    if observation.advisor is not None and observation.advisor.may_request:
        yield _offer(
            observation,
            source=AffordanceSource.RUNTIME,
            semantic="consult_advisor",
            description="Request a read-only strategic second opinion.",
            operation_kind="consult_advisor",
            parameters=(
                AffordanceParameterSpec(
                    name="question",
                    kind=AffordanceParameterKind.TEXT,
                    description="Strategic question whose answer could change the goal.",
                ),
                AffordanceParameterSpec(
                    name="focus",
                    kind=AffordanceParameterKind.CHOICE,
                    description="Gameplay topic for the advice.",
                    choices=(
                        "next_goal",
                        "survival",
                        "food",
                        "economy",
                        "recruitment",
                        "travel",
                        "recovery",
                    ),
                ),
            ),
        )
    if observation.memories or observation.recent_action_outcomes:
        yield _offer(
            observation,
            source=AffordanceSource.RUNTIME,
            semantic="recall_memory",
            description="Search older durable gameplay continuity.",
            operation_kind="recall_memory",
            parameters=(
                AffordanceParameterSpec(
                    name="query",
                    kind=AffordanceParameterKind.TEXT,
                    description="Specific older gameplay fact that could change the decision.",
                ),
            ),
            arguments={"source": "durable_memory", "max_records": 4},
        )
    if observation.fieldbook_projects:
        yield _offer(
            observation,
            source=AffordanceSource.RUNTIME,
            semantic="read_fieldbook",
            description="Read bounded private project context.",
            operation_kind="read_fieldbook",
            parameters=(
                AffordanceParameterSpec(
                    name="query",
                    kind=AffordanceParameterKind.TEXT,
                    description="Specific private-project context to retrieve.",
                ),
            ),
            arguments={"max_entries": 4},
        )


def _game_binding_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    exact_identity_selection = bool(
        observation.control_mode is ControlMode.NATIVE_ASSISTED
        and "control.select_squad_member" in telemetry.capabilities
        and "identity.stable_handles" in telemetry.capabilities
        and telemetry.ui.selected_character_id is not None
        and telemetry.ui.selected_character_id in telemetry.ui.selected_character_ids
    )
    for binding in GameBinding:
        if (
            binding in TIME_GAME_BINDINGS
            or binding in SEMANTICALLY_ADAPTED_GAME_BINDINGS
            or (exact_identity_selection and binding in OPAQUE_CHARACTER_SELECTION_GAME_BINDINGS)
        ):
            continue
        yield _offer(
            observation,
            source=AffordanceSource.GAME_BINDING,
            semantic=binding.value,
            description=f"Use Kenshi's named {binding.value} binding.",
            operation_kind="use_game_binding",
            arguments={"binding": binding.value, "expected_effect": binding.value},
        )


def _screen_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    interface_clear = bool(
        telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
    )
    for screen in GameScreen:
        open_state = screen_is_open(screen, telemetry)
        if open_state is not False or not interface_clear:
            continue
        yield _offer(
            observation,
            source=AffordanceSource.GAME_BINDING,
            semantic=f"open_{screen.value}",
            description=f"Have the {screen.value!r} screen open.",
            operation_kind="open_screen",
            arguments={"screen": screen.value},
        )

    if telemetry.ui.dialogue_open:
        return

    owners = observation.window_owners()
    captions = [
        window
        for window in observation.open_window_captions()
        if normalize_control_label(window) in owners
    ]
    inventory_open = screen_is_open(GameScreen.INVENTORY, telemetry)
    if inventory_open is True:
        if captions:
            for window in captions:
                yield _offer(
                    observation,
                    source=AffordanceSource.VISIBLE_CONTROL,
                    semantic="close_inventory_window",
                    description=f"Close the exact open inventory window {window!r}.",
                    operation_kind="dismiss_screen",
                    target=AffordanceTarget(
                        target_id=window,
                        label=window,
                        kind="window",
                    ),
                    arguments={
                        "expected_screen": GameScreen.INVENTORY.value,
                        "window": window,
                    },
                )
        else:
            yield _offer(
                observation,
                source=AffordanceSource.GAME_BINDING,
                semantic="close_inventory",
                description="Close the currently open inventory screen.",
                operation_kind="dismiss_screen",
                arguments={"expected_screen": GameScreen.INVENTORY.value},
            )

    for screen in (GameScreen.STATS, GameScreen.MAP, GameScreen.RESEARCH, GameScreen.CRAFTING):
        if screen_is_open(screen, telemetry) is not True:
            continue
        yield _offer(
            observation,
            source=AffordanceSource.GAME_BINDING,
            semantic=f"close_{screen.value}",
            description=f"Close the currently open {screen.value!r} screen.",
            operation_kind="dismiss_screen",
            arguments={"expected_screen": screen.value},
        )

    if telemetry.ui.active_screen == "trade":
        for window in captions:
            yield _offer(
                observation,
                source=AffordanceSource.VISIBLE_CONTROL,
                semantic="close_trade_window",
                description=f"Close the exact open trade window {window!r}.",
                operation_kind="dismiss_screen",
                target=AffordanceTarget(
                    target_id=window,
                    label=window,
                    kind="window",
                ),
                arguments={"expected_screen": "trade", "window": window},
            )


def _visible_control_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if (
        telemetry is None
        or telemetry.ui.visible_controls is None
        or "ui.visible_controls" not in telemetry.capabilities
    ):
        return
    dialogue_labels = {
        normalize_control_label(label) for label in (telemetry.ui.dialogue_options or [])
    }
    for control in telemetry.ui.visible_controls:
        if is_runtime_owned_visible_control(control) or control.role == "item":
            continue
        if control.role == "text" and not (
            telemetry.ui.dialogue_open and normalize_control_label(control.label) in dialogue_labels
        ):
            continue
        source = (
            AffordanceSource.DIALOGUE
            if telemetry.ui.dialogue_open
            else AffordanceSource.VISIBLE_CONTROL
        )
        target_id = f"{control.window}\x1f{control.role}\x1f{control.label}"
        yield _offer(
            observation,
            source=source,
            semantic="choose_dialogue" if source is AffordanceSource.DIALOGUE else "activate",
            description=f"Activate {control.label!r} in {control.window or 'the current UI'}.",
            operation_kind="activate_visible_control",
            target=AffordanceTarget(
                target_id=target_id,
                label=control.label,
                kind=control.role,
            ),
            arguments={
                "exact_label": control.label,
                "role": control.role,
                "window": control.window,
            },
        )


def _context_order_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    native = "control.perform_context_action" in telemetry.capabilities
    for target in telemetry.world_targets:
        for order in target.context_actions:
            native_order = bool(
                native
                and (
                    order == ContextActionKind.OPERATE
                    and target.kind == "natural_resource"
                    or order == ContextActionKind("first_aid")
                    and target.kind == "squad_character"
                )
            )
            if not native_order and target.screen_position is None:
                continue
            operation_kind = "perform_context_action" if native_order else "command_world_target"
            yield _offer(
                observation,
                source=AffordanceSource.CONTEXT_ORDER,
                semantic=order.value,
                description=f"Issue {order.value!r} to {target.name!r}.",
                operation_kind=operation_kind,
                target=AffordanceTarget(
                    target_id=target.id,
                    label=target.name,
                    kind=target.kind,
                ),
                arguments={"target_id": target.id, "context_action": order.value},
            )


def _dialogue_target_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None or observation.control_mode is not ControlMode.NATIVE_ASSISTED:
        return
    capabilities = set(telemetry.capabilities)
    required = {
        "identity.stable_handles",
        "nearby.characters",
        "nearby.roles",
    }
    if not required <= capabilities or not (
        {"control.approach_dialogue_target", "control.approach_vendor"} & capabilities
    ):
        return
    for target in observation.dialogue_target_digest():
        yield _offer(
            observation,
            source=AffordanceSource.DIALOGUE,
            semantic="approach_for_dialogue",
            description=f"Approach {target['name']!r} to begin dialogue.",
            operation_kind="approach_dialogue_target",
            target=AffordanceTarget(
                target_id=str(target["id"]),
                label=str(target["name"]),
                kind="character",
            ),
            arguments={"target_id": str(target["id"])},
        )


def _inventory_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if (
        telemetry is None
        or telemetry.ui.visible_controls is None
        or "ui.visible_controls" not in telemetry.capabilities
    ):
        return
    owners = observation.window_owners()
    open_vendor_ids = {
        str(owner["seller_id"])
        for caption in observation.open_window_captions()
        if (owner := owners.get(normalize_control_label(caption), {})).get("belongs_to") == "vendor"
        and owner.get("seller_id")
    }
    paired_vendor_id = next(iter(open_vendor_ids)) if len(open_vendor_ids) == 1 else None
    for control in telemetry.ui.visible_controls:
        if control.role != "item" or not control.item_name:
            continue
        owner = owners.get(normalize_control_label(control.window), {})
        target_id = f"{control.window}\x1f{control.label}\x1f{control.item_name}"
        target = AffordanceTarget(
            target_id=target_id,
            label=control.item_name,
            kind="inventory_cell",
        )
        base: dict[str, JsonValue] = {
            "cell_label": control.label,
            "item_name": control.item_name,
            "window": control.window,
        }
        cell_quantity = (
            str(control.item_quantity) if control.item_quantity is not None else "unknown"
        )
        cell_quantity_max = (
            min(5, control.item_quantity) if control.item_quantity is not None else 5
        )
        purchase_quantity_max = cell_quantity_max
        if telemetry.game.money is not None and control.item_base_value:
            purchase_quantity_max = min(
                purchase_quantity_max,
                telemetry.game.money // control.item_base_value,
            )
        if (
            owner.get("belongs_to") == "vendor"
            and owner.get("seller_id")
            and control.item_base_value is not None
            and control.item_base_value >= 0
            and purchase_quantity_max >= 1
        ):
            seller_id = str(owner["seller_id"])
            quoted_charge = (
                "for free"
                if control.item_base_value == 0
                else f"for {control.item_base_value} cats per unit"
            )
            yield _offer(
                observation,
                source=AffordanceSource.INVENTORY,
                semantic="buy",
                description=(
                    f"Acquire {control.item_name!r} from {control.window!r} "
                    f"{quoted_charge}; current cell "
                    f"quantity is {cell_quantity}."
                ),
                operation_kind="purchase_item",
                target=target,
                parameters=(_quantity_parameter(purchase_quantity_max),),
                arguments={
                    **base,
                    "expected_price": control.item_base_value,
                    "seller_id": seller_id,
                },
            )
        if owner.get("belongs_to") == "you":
            if paired_vendor_id and cell_quantity_max >= 1:
                yield _offer(
                    observation,
                    source=AffordanceSource.INVENTORY,
                    semantic="sell",
                    description=(
                        f"Sell {control.item_name!r} from {control.window!r}; "
                        f"current cell quantity is {cell_quantity}."
                    ),
                    operation_kind="sell_item",
                    target=target,
                    parameters=(_quantity_parameter(cell_quantity_max),),
                    arguments={**base, "buyer_id": paired_vendor_id},
                )
            elif not observation.trade_screen_open():
                yield _offer(
                    observation,
                    source=AffordanceSource.INVENTORY,
                    semantic="equip",
                    description=f"Equip {control.item_name!r} from {control.window!r}.",
                    operation_kind="equip_item",
                    target=target,
                    arguments=base,
                )


def _character_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    capabilities = set(telemetry.capabilities)
    selected = next((member for member in telemetry.squad if member.selected), None)
    exact_selection = bool(
        observation.control_mode is ControlMode.NATIVE_ASSISTED
        and "control.select_squad_member" in capabilities
        and "identity.stable_handles" in capabilities
        and telemetry.ui.selected_character_id is not None
        and telemetry.ui.selected_character_id in telemetry.ui.selected_character_ids
    )
    for member in telemetry.squad:
        target = AffordanceTarget(
            target_id=member.id,
            label=member.name,
            kind="squad_member",
        )
        if telemetry.ui.selected_character_ids != [member.id]:
            yield _offer(
                observation,
                source=AffordanceSource.SQUAD,
                semantic="select_only",
                description=(
                    f"Replace the current selection with only {member.name!r}, "
                    "deselecting every other party member."
                ),
                operation_kind=(
                    "select_squad_member_exact" if exact_selection else "select_squad_member"
                ),
                target=target,
                arguments={"target_id": member.id},
            )
    all_member_ids = {member.id for member in telemetry.squad}
    if (
        all_member_ids
        and set(telemetry.ui.selected_character_ids) != all_member_ids
        and telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
        and "squad.basic" in capabilities
        and "identity.stable_handles" in capabilities
    ):
        names = ", ".join(member.name for member in telemetry.squad)
        if len(names) > 240:
            names = f"{len(telemetry.squad)} current members"
        yield _offer(
            observation,
            source=AffordanceSource.SQUAD,
            semantic="select_whole_party",
            description=(
                f"Select the complete current party together: {names}. This "
                "replaces the current selection with all current members."
            ),
            operation_kind="use_game_binding",
            arguments={
                "binding": GameBinding.SELECT_ALL.value,
                "expected_effect": (f"select all {len(telemetry.squad)} current party members"),
            },
        )
    if (
        selected is not None
        and selected.position is not None
        and "control.regroup_with_squad_member" in capabilities
    ):
        candidates = [
            member
            for member in telemetry.squad
            if member.id != selected.id
            and member.alive is True
            and member.position is not None
            and (
                (member.position.x - selected.position.x) ** 2
                + (member.position.z - selected.position.z) ** 2
                > SQUAD_REGROUP_ARRIVAL_DISTANCE**2
            )
        ]
        if candidates:

            def reunion_distance_key(member: CharacterState) -> tuple[float, str]:
                assert selected.position is not None
                assert member.position is not None
                return (
                    (member.position.x - selected.position.x) ** 2
                    + (member.position.z - selected.position.z) ** 2,
                    member.id,
                )

            target_member = min(
                candidates,
                key=reunion_distance_key,
            )
            yield _offer(
                observation,
                source=AffordanceSource.COMPOSITE_OPERATION,
                semantic="reunite_squad",
                description=(
                    f"Reunite {selected.name!r} with the nearest separated "
                    f"squadmate, currently {target_member.name!r}."
                ),
                operation_kind="regroup_with_squad_member",
                target=AffordanceTarget(
                    target_id=target_member.id,
                    label=target_member.name,
                    kind="squad_member",
                ),
                arguments={
                    "actor_id": selected.id,
                    "target_id": target_member.id,
                },
            )
    selected_count = len([member for member in telemetry.squad if member.selected])
    if selected_count == 0:
        return
    for character in telemetry.nearby_entities:
        if character.is_animal or character.disposition.value == "hostile":
            continue
        yield _offer(
            observation,
            source=AffordanceSource.NEARBY_CHARACTER,
            semantic="move_to" if selected_count == 1 else "move_squad_to",
            description=(
                f"Move to nearby character {character.name!r}."
                if selected_count == 1
                else f"Move {selected_count} selected squad members together "
                f"to nearby character {character.name!r}."
            ),
            operation_kind="move_to_character",
            target=AffordanceTarget(
                target_id=character.id,
                label=character.name,
                kind="character",
            ),
            arguments={"target_id": character.id},
        )
    if selected is not None and selected.in_combat and telemetry.game.paused:
        yield _offer(
            observation,
            source=AffordanceSource.COMPOSITE_OPERATION,
            semantic="respond_to_immediate_threat",
            description=f"Choose a combat response for {selected.name!r}.",
            operation_kind="respond_to_immediate_threat",
            target=AffordanceTarget(
                target_id=selected.id,
                label=selected.name,
                kind="squad_member",
            ),
            parameters=(
                AffordanceParameterSpec(
                    name="strategy",
                    kind=AffordanceParameterKind.CHOICE,
                    description="Gameplay strategy for the immediate threat.",
                    choices=tuple(strategy.value for strategy in ThreatResponseStrategy),
                ),
            ),
            arguments={"actor_id": selected.id},
        )


def _map_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    capabilities = set(telemetry.capabilities)
    if (
        not {
            "control.travel_to_map_destination",
            "world.known_map_destinations",
        }
        <= capabilities
    ):
        return
    selected = [member for member in telemetry.squad if member.selected]
    if not selected:
        return
    selected_count = len(selected)
    for destination in telemetry.known_map_destinations:
        if not map_destination_travel_available(
            destination,
            current_location_id=telemetry.game.location_id,
            inside_town_walls=telemetry.game.inside_town_walls,
            location_authoritative="game.location.identity" in capabilities,
            whole_group_present=selected_count == 1,
        ):
            continue
        yield _offer(
            observation,
            source=AffordanceSource.MAP,
            semantic="travel" if selected_count == 1 else "travel_squad",
            description=(
                f"Travel to known map destination {destination.name!r}."
                if selected_count == 1
                else f"Travel {selected_count} selected squad members together "
                f"to known map destination {destination.name!r}."
            ),
            operation_kind="travel_to_map_destination",
            target=AffordanceTarget(
                target_id=destination.id,
                label=destination.name,
                kind="map_destination",
            ),
            arguments={"destination_id": destination.id},
        )


def _native_and_composite_offers(
    observation: Observation,
) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    selected = next((member for member in telemetry.squad if member.selected), None)

    for direction in CameraRotationDirection:
        yield _offer(
            observation,
            source=AffordanceSource.NATIVE_OPERATION,
            semantic=f"rotate_camera_{direction.value}",
            description=f"Rotate the camera {direction.value} one bounded increment.",
            operation_kind="rotate_camera",
            arguments={"direction": direction.value},
        )

    yield _offer(
        observation,
        source=AffordanceSource.NATIVE_OPERATION,
        semantic="move_in_direction",
        description="Move a gameplay-selected bearing and distance.",
        operation_kind="move_in_direction",
        parameters=(
            AffordanceParameterSpec(
                name="bearing_degrees",
                kind=AffordanceParameterKind.NUMBER,
                description="Clockwise gameplay bearing from north.",
                minimum=0,
                maximum=359.999,
            ),
            AffordanceParameterSpec(
                name="distance_units",
                kind=AffordanceParameterKind.NUMBER,
                description="Gameplay travel distance.",
                minimum=1,
                maximum=2000,
            ),
        ),
    )

    if selected is not None and selected.indoors is True:
        yield _offer(
            observation,
            source=AffordanceSource.NATIVE_OPERATION,
            semantic="exit_current_building",
            description=f"Move {selected.name!r} out of the current building.",
            operation_kind="exit_current_building",
            target=AffordanceTarget(
                target_id=selected.id,
                label=selected.name,
                kind="squad_member",
            ),
        )

    yield _offer(
        observation,
        source=AffordanceSource.COMPOSITE_OPERATION,
        semantic="recover_camera_view",
        description="Restore a readable, character-following camera view.",
        operation_kind="recover_camera_view",
    )

    if telemetry.ui.visible_controls is not None:
        for window in observation.open_window_captions():
            yield _offer(
                observation,
                source=AffordanceSource.VISIBLE_CONTROL,
                semantic="scroll_down",
                description=f"Reveal later content in open window {window!r}.",
                operation_kind="scroll_screen",
                target=AffordanceTarget(
                    target_id=window,
                    label=window,
                    kind="window",
                ),
                arguments={"window": window, "notches": -3},
            )
            yield _offer(
                observation,
                source=AffordanceSource.VISIBLE_CONTROL,
                semantic="scroll_up",
                description=f"Reveal earlier content in open window {window!r}.",
                operation_kind="scroll_screen",
                target=AffordanceTarget(
                    target_id=window,
                    label=window,
                    kind="window",
                ),
                arguments={"window": window, "notches": 3},
            )

    if selected is not None:
        for target in telemetry.world_targets:
            if (
                target.kind != "natural_resource"
                or ContextActionKind.OPERATE not in target.context_actions
            ):
                continue
            yield _offer(
                observation,
                source=AffordanceSource.COMPOSITE_OPERATION,
                semantic="harvest",
                description=f"Harvest a bounded yield from {target.name!r}.",
                operation_kind="harvest_resource",
                target=AffordanceTarget(
                    target_id=target.id,
                    label=target.name,
                    kind=target.kind,
                ),
                parameters=(_quantity_parameter(),),
                arguments={"actor_id": selected.id, "target_id": target.id},
            )


@dataclass(frozen=True, slots=True)
class AffordanceAdapter:
    """One source denominator and the operations that can realize its offers."""

    name: str
    sources: frozenset[AffordanceSource]
    operation_kinds: frozenset[str]
    denominator: str
    completeness_boundary: str
    enumerate: Callable[[Observation], Iterable[AffordanceOffer]]

    def offers(self, observation: Observation) -> Iterable[AffordanceOffer]:
        """Every offer this adapter makes that the registry would also accept.

        Enumeration used to answer "what could be offered" while
        `OperationDefinition.is_currently_authorable` answered "what could be
        run", and nothing reconciled them. A live two-character start offered
        `harvest_resource` on an iron deposit that could not be harvested,
        because the adapter never asked. Filtering here makes the two agree by
        construction rather than by coincidence, so a future adapter cannot
        quietly reintroduce the disagreement.

        Enumerate through this, not through `enumerate`, everywhere an offer is
        shown to or rebound for the planner.
        """

        for offer in self.enumerate(observation):
            definition = OPERATION_DEFINITIONS.get(offer.operation_kind)
            if definition is not None and not definition.is_currently_authorable(observation):
                continue
            yield offer

    def bind(
        self,
        selection: AffordanceSelection,
        observation: Observation,
    ) -> BoundOperation:
        """Re-enumerate this adapter and bind one exact current offer."""

        return _bind_adapter_selection(self, selection, observation)


AFFORDANCE_ADAPTERS: tuple[AffordanceAdapter, ...] = (
    AffordanceAdapter(
        name="runtime",
        sources=frozenset({AffordanceSource.RUNTIME}),
        operation_kinds=frozenset(
            {"noop", "stop", "consult_advisor", "recall_memory", "read_fieldbook"}
        ),
        denominator="Runtime control, advisor, memory, and fieldbook state.",
        completeness_boundary="Only choices applicable to the current run state.",
        enumerate=_runtime_offers,
    ),
    AffordanceAdapter(
        name="game_bindings",
        sources=frozenset({AffordanceSource.GAME_BINDING}),
        operation_kinds=frozenset({"use_game_binding"}),
        denominator="Every captured-default-keymap binding not owned by another adapter.",
        completeness_boundary=(
            "Witnessed bindings use effect terminals; unwitnessed bindings stop at "
            "accepted delivery plus a later observation. Playback, stateful screens, "
            "and camera rotation route through semantic adapters."
        ),
        enumerate=_game_binding_offers,
    ),
    AffordanceAdapter(
        name="screens",
        sources=frozenset({AffordanceSource.GAME_BINDING, AffordanceSource.VISIBLE_CONTROL}),
        operation_kinds=frozenset({"open_screen", "dismiss_screen"}),
        denominator="Observable named-screen states and currently open window captions.",
        completeness_boundary=(
            "Opening requires an observable exact terminal. Named-window dismissal "
            "uses a count terminal; uncaptained dismissal stops at delivery. Dialogue "
            "is excluded."
        ),
        enumerate=_screen_offers,
    ),
    AffordanceAdapter(
        name="visible_controls",
        sources=frozenset({AffordanceSource.VISIBLE_CONTROL, AffordanceSource.DIALOGUE}),
        operation_kinds=frozenset({"activate_visible_control"}),
        denominator="Every current non-item, non-runtime-owned visible control.",
        completeness_boundary=(
            "Ambiguous or stale controls fail exact rebinding. Activation proves exact "
            "delivery and a later observation, not the gameplay meaning of the result."
        ),
        enumerate=_visible_control_offers,
    ),
    AffordanceAdapter(
        name="context_orders",
        sources=frozenset({AffordanceSource.CONTEXT_ORDER}),
        operation_kinds=frozenset({"perform_context_action", "command_world_target"}),
        denominator="Every exact world-target/order pair advertised by current telemetry.",
        completeness_boundary=(
            "Native execution proves the reviewed natural-resource operate and "
            "squad-character first_aid semantics; other orders require current screen "
            "geometry and stop at the generic UI delivery boundary."
        ),
        enumerate=_context_order_offers,
    ),
    AffordanceAdapter(
        name="dialogue_targets",
        sources=frozenset({AffordanceSource.DIALOGUE}),
        operation_kinds=frozenset({"approach_dialogue_target"}),
        denominator="Every exact current non-hostile character confirmed talkable.",
        completeness_boundary="Native-assisted stable identity and dialogue-role evidence.",
        enumerate=_dialogue_target_offers,
    ),
    AffordanceAdapter(
        name="inventory",
        sources=frozenset({AffordanceSource.INVENTORY}),
        operation_kinds=frozenset({"purchase_item", "sell_item", "equip_item"}),
        denominator="Every current item cell with exact window ownership and item facts.",
        completeness_boundary=(
            "Transactions require unambiguous counterpart identity and conservation "
            "evidence. Equip currently proves exact delivery, not final equipment state."
        ),
        enumerate=_inventory_offers,
    ),
    AffordanceAdapter(
        name="characters",
        sources=frozenset(
            {
                AffordanceSource.SQUAD,
                AffordanceSource.NEARBY_CHARACTER,
                AffordanceSource.COMPOSITE_OPERATION,
            }
        ),
        operation_kinds=frozenset(
            {
                "select_squad_member",
                "select_squad_member_exact",
                "use_game_binding",
                "regroup_with_squad_member",
                "move_to_character",
                "respond_to_immediate_threat",
            }
        ),
        denominator="Every exact current squad member and eligible nearby character.",
        completeness_boundary=(
            "Offers require the source-specific selection, identity, geometry, and safety facts."
        ),
        enumerate=_character_offers,
    ),
    AffordanceAdapter(
        name="map",
        sources=frozenset({AffordanceSource.MAP}),
        operation_kinds=frozenset({"travel_to_map_destination"}),
        denominator="Every currently known exact map destination.",
        completeness_boundary="Only destinations with authoritative current travel applicability.",
        enumerate=_map_offers,
    ),
    AffordanceAdapter(
        name="native_and_composite",
        sources=frozenset(
            {
                AffordanceSource.NATIVE_OPERATION,
                AffordanceSource.COMPOSITE_OPERATION,
                AffordanceSource.VISIBLE_CONTROL,
            }
        ),
        operation_kinds=frozenset(
            {
                "rotate_camera",
                "move_in_direction",
                "exit_current_building",
                "recover_camera_view",
                "scroll_screen",
                "harvest_resource",
            }
        ),
        denominator="Current state for native movement, camera, scrolling, and harvesting.",
        completeness_boundary=(
            "Only operations with a current binder and declared runtime completion "
            "boundary. Scrolling proves exact delivery, not newly revealed content."
        ),
        enumerate=_native_and_composite_offers,
    ),
)


def affordance_operation_kinds() -> frozenset[str]:
    return frozenset(kind for adapter in AFFORDANCE_ADAPTERS for kind in adapter.operation_kinds)


def _sample_parameters(offer: AffordanceOffer) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {}
    for spec in offer.parameters:
        if spec.choices:
            values[spec.name] = spec.choices[0]
        elif spec.minimum is not None:
            value: float | int = spec.minimum
            if spec.kind is AffordanceParameterKind.INTEGER:
                value = int(value)
            values[spec.name] = value
        elif spec.kind is AffordanceParameterKind.TEXT:
            values[spec.name] = "selected strategy"
        else:
            values[spec.name] = 1
    return values


def _operation_for(
    offer: AffordanceOffer,
    parameters: dict[str, JsonValue],
) -> Action:
    arguments: dict[str, Any] = {**offer.operation_arguments, **parameters}
    if offer.operation_kind == "move_in_direction":
        arguments.setdefault("expected_effect", "Move along the selected bearing.")
    operation: Action = TypeAdapter(Action).validate_python(
        {"kind": offer.operation_kind, **arguments}
    )
    return operation


def _offer_binds_now(offer: AffordanceOffer, observation: Observation) -> bool:
    operation = _operation_for(offer, _sample_parameters(offer))
    if unchanged_definitive_no_op_reason(operation, observation) is not None:
        return False
    if observation.planning_mode is PlanningMode.SINGLE_STEP:
        try:
            TypeAdapter(SingleStepRuntimeAction).validate_python(operation)
        except ValidationError:
            return False
    definition = definition_for(operation)
    if definition is None:
        raise RuntimeError(f"adapter emitted {operation.kind!r} without an operation definition")
    telemetry = observation.telemetry
    capabilities = set(telemetry.capabilities if telemetry is not None else [])
    if (
        not definition.allows_control_mode(observation.control_mode)
        or definition.missing_capabilities(capabilities)
        or not definition.is_currently_authorable(observation)
    ):
        return False
    binding = definition.bind(operation, observation)
    if isinstance(binding, BindingFailure):
        return False
    completion = definition.resolve_terminal(
        operation,
        observation,
        selected_affordance=True,
    )
    if completion.owner is TerminalOwner.STEP_CONDITIONS:
        raise RuntimeError("an offered affordance delegated completion to its caller")
    return not (completion.owner is TerminalOwner.RUNTIME_CONDITIONS and not completion.conditions)


def offered_affordances(observation: Observation) -> tuple[AffordanceOffer, ...]:
    """Enumerate one immutable, fail-closed offer set for this observation."""

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return ()
    interface_clear = bool(
        telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
    )
    enumerated = tuple(
        (adapter, offer)
        for adapter in AFFORDANCE_ADAPTERS
        for offer in adapter.offers(observation)
        if interface_clear or offer.operation_kind in INTERFACE_SCOPED_OPERATION_KINDS
    )
    offers_by_id: dict[str, AffordanceOffer] = {}
    for adapter, offer in enumerated:
        if offer.source not in adapter.sources:
            raise RuntimeError(
                f"adapter {adapter.name!r} emitted undeclared source {offer.source.value!r}"
            )
        if offer.operation_kind not in adapter.operation_kinds:
            raise RuntimeError(
                f"adapter {adapter.name!r} emitted undeclared operation {offer.operation_kind!r}"
            )
        if not _offer_binds_now(offer, observation):
            continue
        existing = offers_by_id.get(offer.affordance_id)
        if existing is None:
            offers_by_id[offer.affordance_id] = offer
            continue
        if existing != offer:
            raise RuntimeError("source adapters generated a colliding affordance ID")
    offers = tuple(offers_by_id.values())
    if any(
        len(spec.choices) != len(set(spec.choices)) for offer in offers for spec in offer.parameters
    ):
        raise RuntimeError("source adapter generated duplicate parameter choices")
    return tuple(sorted(offers, key=lambda offer: offer.affordance_id))


def _validated_parameters(
    selection: AffordanceSelection,
    offer: AffordanceOffer,
) -> dict[str, JsonValue]:
    supplied = selection.parameter_map()
    if len(supplied) != len(selection.parameters):
        raise ValueError("affordance parameter names must be unique")
    specs = {spec.name: spec for spec in offer.parameters}
    unknown = supplied.keys() - specs.keys()
    missing = {name for name, spec in specs.items() if spec.required} - supplied.keys()
    if unknown:
        raise ValueError("unknown affordance parameters: " + ", ".join(sorted(unknown)))
    if missing:
        raise ValueError("missing affordance parameters: " + ", ".join(sorted(missing)))
    for name, value in supplied.items():
        spec = specs[name]
        if spec.kind is AffordanceParameterKind.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"parameter {name!r} must be an integer")
        elif spec.kind is AffordanceParameterKind.NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"parameter {name!r} must be numeric")
        elif spec.kind is AffordanceParameterKind.TEXT:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"parameter {name!r} must be non-empty text")
        elif not isinstance(value, str) or value not in spec.choices:
            raise ValueError(f"parameter {name!r} must be one of {', '.join(spec.choices)}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if spec.minimum is not None and value < spec.minimum:
                raise ValueError(f"parameter {name!r} is below its offered minimum")
            if spec.maximum is not None and value > spec.maximum:
                raise ValueError(f"parameter {name!r} exceeds its offered maximum")
    return supplied


def _execution_for(definition: OperationDefinition) -> AffordanceExecution:
    if definition.execution is OperationExecution.COMPOSITE_OPTION:
        return AffordanceExecution.COMPOSITE
    if (
        definition.execution is OperationExecution.MONITORED_OPTION
        or definition.derive_completion_conditions is not None
    ):
        return AffordanceExecution.MONITORED
    return AffordanceExecution.IMMEDIATE


def _bound_affordance(
    offer: AffordanceOffer,
    selection: AffordanceSelection,
    definition: OperationDefinition,
) -> BoundAffordance:
    return BoundAffordance(
        affordance_id=offer.affordance_id,
        source=offer.source,
        semantic=offer.semantic,
        target=offer.target,
        parameters=selection.parameters,
        execution=_execution_for(definition),
        operation_kind=offer.operation_kind,
        offered_at_telemetry_sequence=offer.offered_at_telemetry_sequence,
    )


def _bind_adapter_selection(
    adapter: AffordanceAdapter,
    selection: AffordanceSelection,
    observation: Observation,
) -> BoundOperation:
    telemetry = observation.telemetry
    interface_clear = bool(
        telemetry is not None
        and telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
    )
    matches = [
        offer
        for offer in adapter.offers(observation)
        if offer.affordance_id == selection.affordance_id
        and (interface_clear or offer.operation_kind in INTERFACE_SCOPED_OPERATION_KINDS)
        and _offer_binds_now(offer, observation)
    ]
    if len(matches) != 1:
        raise ValueError("affordance is absent from the adapter's current source")
    offer = matches[0]
    if offer.source not in adapter.sources or offer.operation_kind not in adapter.operation_kinds:
        raise RuntimeError(f"adapter {adapter.name!r} emitted an undeclared offer")
    expected_target_id = offer.target.target_id if offer.target else None
    if selection.target_id != expected_target_id:
        raise ValueError("selection target does not match the exact offered target")
    parameters = _validated_parameters(selection, offer)
    operation = _operation_for(offer, parameters)
    definition = definition_for(operation)
    if definition is None:
        raise RuntimeError(f"operation {operation.kind!r} has no definition")
    binding = definition.bind(operation, observation)
    if isinstance(binding, BindingFailure):
        raise ValueError(f"affordance no longer binds: {binding.reason}")
    affordance = _bound_affordance(offer, selection, definition)
    return BoundOperation(
        definition=definition,
        operation=operation,
        binding=binding,
        affordance=affordance,
        based_on_revision=observation.world_revision,
        identity=operation_identity(definition, operation, binding, affordance),
    )


def bind_affordance(
    selection: AffordanceSelection,
    observation: Observation,
) -> BoundOperation:
    """Route a selection back to its issuing adapter for exact current binding."""

    matches = [
        adapter
        for adapter in AFFORDANCE_ADAPTERS
        for offer in adapter.offers(observation)
        if offer.affordance_id == selection.affordance_id
    ]
    if len(matches) != 1:
        raise ValueError("affordance is absent from the current observation")
    return matches[0].bind(selection, observation)


def bound_affordance(bound: BoundOperation) -> BoundAffordance:
    """Return the planner record retained by an already-bound operation."""

    if bound.affordance is None:
        raise ValueError("Runtime-internal operation has no planner affordance.")
    return bound.affordance


def _rebind_affordance_operation(
    operation: Action,
    affordance: BoundAffordance,
    observation: Observation,
) -> BoundOperation:
    """Re-enumerate the issuing adapter and bind the exact planned operation.

    The retained affordance is provenance, not durable authority. Rebuilding
    its original selection forces execution back through the source adapter's
    current denominator and rejects any operation drift before a handler runs.
    """

    adapters = [
        adapter
        for adapter in AFFORDANCE_ADAPTERS
        if affordance.source in adapter.sources
        and affordance.operation_kind in adapter.operation_kinds
    ]
    if len(adapters) != 1:
        raise RuntimeError("affordance provenance does not identify one source adapter")
    adapter = adapters[0]
    target_id = affordance.target.target_id if affordance.target else None
    rebounds: list[BoundOperation] = []
    for offer in adapter.offers(observation):
        current_target_id = offer.target.target_id if offer.target else None
        if (
            offer.source is not affordance.source
            or offer.semantic != affordance.semantic
            or offer.operation_kind != affordance.operation_kind
            or current_target_id != target_id
            or not _offer_binds_now(offer, observation)
        ):
            continue
        selection = AffordanceSelection(
            affordance_id=offer.affordance_id,
            target_id=current_target_id,
            parameters=affordance.parameters,
        )
        try:
            candidate = adapter.bind(selection, observation)
        except ValueError:
            continue
        if candidate.operation == operation:
            rebounds.append(candidate)
    if not rebounds:
        raise OperationBindingError(
            "Affordance is absent from the current observation.",
            code=AuthorizationCode.BINDING_ABSENT,
        )
    if len(rebounds) > 1:
        raise OperationBindingError(
            "Affordance is ambiguous in the current observation.",
            code=AuthorizationCode.BINDING_AMBIGUOUS,
        )
    rebound = rebounds[0]
    return BoundOperation(
        definition=rebound.definition,
        operation=rebound.operation,
        binding=rebound.binding,
        affordance=affordance,
        based_on_revision=rebound.based_on_revision,
        identity=operation_identity(
            rebound.definition,
            rebound.operation,
            rebound.binding,
            affordance,
        ),
    )


@dataclass(frozen=True, slots=True)
class OperationBindingAuthority:
    """The sole fresh-binding implementation for executable operations."""

    def bind(
        self,
        operation: Action,
        observation: Observation,
        *,
        affordance: BoundAffordance | None,
    ) -> BoundOperation:
        """Bind planner provenance or explicit runtime authority to current state."""

        if affordance is not None:
            return _rebind_affordance_operation(operation, affordance, observation)
        non_progress_reason = unchanged_definitive_no_op_reason(operation, observation)
        if non_progress_reason is not None:
            raise OperationBindingError(
                f"Runtime operation is not currently eligible: {non_progress_reason}.",
                code=AuthorizationCode.POLICY_DISALLOWED,
            )
        definition = definition_for(operation)
        if definition is None:
            raise OperationBindingError(
                f"Operation {operation.kind!r} has no definition.",
                code=AuthorizationCode.BINDING_ABSENT,
            )
        binding = definition.bind(operation, observation)
        if isinstance(binding, BindingFailure):
            raise _operation_binding_error(
                f"Runtime operation no longer binds: {binding.reason}"
            )
        return BoundOperation(
            definition=definition,
            operation=operation,
            binding=binding,
            affordance=None,
            based_on_revision=observation.world_revision,
            identity=operation_identity(definition, operation, binding, None),
        )

    def rebind(
        self,
        bound: BoundOperation,
        observation: Observation,
    ) -> BoundOperation:
        """Resolve one already-selected operation against a fresh observation."""

        return self.bind(
            bound.operation,
            observation,
            affordance=bound.affordance,
        )


OPERATION_BINDING_AUTHORITY = OperationBindingAuthority()


def bind_runtime_operation(
    operation: Action,
    observation: Observation,
    *,
    affordance: BoundAffordance | None,
) -> BoundOperation:
    """Bind through the process-wide operation binding authority."""

    return OPERATION_BINDING_AUTHORITY.bind(
        operation,
        observation,
        affordance=affordance,
    )


def terminal_affordance_receipt(
    affordance: BoundAffordance,
    *,
    status: AffordanceLifecycleStatus,
    message: str,
    telemetry_sequence: int | None,
    execution_started: bool,
    monitoring_started: bool,
) -> AffordanceReceipt:
    """Close one bound affordance without inventing lifecycle phases."""

    if monitoring_started and not execution_started:
        raise ValueError("affordance monitoring cannot start before execution")
    if monitoring_started and affordance.execution is AffordanceExecution.IMMEDIATE:
        raise ValueError("an immediate affordance cannot enter monitoring")

    lifecycle = [
        AffordanceLifecycleEvent(
            status=AffordanceLifecycleStatus.OFFERED,
            telemetry_sequence=affordance.offered_at_telemetry_sequence,
            detail="The source adapter offered this exact semantic choice.",
        ),
        AffordanceLifecycleEvent(
            status=AffordanceLifecycleStatus.BOUND,
            telemetry_sequence=affordance.offered_at_telemetry_sequence,
            detail="Runtime rebound the selection to its current exact source target.",
        ),
    ]
    if execution_started:
        lifecycle.append(
            AffordanceLifecycleEvent(
                status=AffordanceLifecycleStatus.EXECUTING,
                telemetry_sequence=telemetry_sequence,
                detail="Runtime began executing the selected affordance.",
            )
        )
    if monitoring_started:
        lifecycle.append(
            AffordanceLifecycleEvent(
                status=AffordanceLifecycleStatus.MONITORING,
                telemetry_sequence=telemetry_sequence,
                detail="Runtime monitored source-specific completion and cleanup.",
            )
        )
    lifecycle.append(
        AffordanceLifecycleEvent(
            status=status,
            telemetry_sequence=telemetry_sequence,
            detail=message,
        )
    )
    return AffordanceReceipt(
        affordance=affordance,
        status=status,
        lifecycle=lifecycle,
        message=message,
    )


def selection_for(
    offer: AffordanceOffer,
    **parameters: JsonValue,
) -> AffordanceSelection:
    """Construct an exact selection for deterministic planners and tests."""

    return AffordanceSelection(
        affordance_id=offer.affordance_id,
        target_id=offer.target.target_id if offer.target else None,
        parameters=[
            AffordanceParameter(name=name, value=value) for name, value in parameters.items()
        ],
    )
