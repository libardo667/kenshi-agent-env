"""One runtime-generated contract for every planner-visible possibility.

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
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from .models import (
    TIME_GAME_BINDINGS,
    Action,
    CameraRotationDirection,
    ContextActionKind,
    ControlMode,
    GameBinding,
    IdempotencyPolicy,
    Observation,
    PlanningMode,
    SingleStepPlannerAction,
    ThreatResponseStrategy,
    game_binding_success_condition,
    is_runtime_owned_visible_control,
    map_destination_travel_available,
    normalize_control_label,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AffordanceSource(StrEnum):
    RUNTIME = "runtime"
    GAME_BINDING = "game_binding"
    VISIBLE_CONTROL = "visible_control"
    CONTEXT_ORDER = "context_order"
    DIALOGUE = "dialogue"
    INVENTORY = "inventory"
    SQUAD = "squad"
    NEARBY_CHARACTER = "nearby_character"
    MAP = "map"
    NATIVE_OPERATION = "native_operation"
    COMPOSITE_OPERATION = "composite_operation"


class AffordanceExecution(StrEnum):
    IMMEDIATE = "immediate"
    MONITORED = "monitored"
    COMPOSITE = "composite"


class AffordanceParameterKind(StrEnum):
    INTEGER = "integer"
    NUMBER = "number"
    TEXT = "text"
    CHOICE = "choice"


class AffordanceLifecycleStatus(StrEnum):
    OFFERED = "offered"
    BOUND = "bound"
    EXECUTING = "executing"
    MONITORING = "monitoring"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    INTERRUPTED = "interrupted"


class AffordanceParameterSpec(_StrictModel):
    name: str = Field(min_length=1, max_length=80)
    kind: AffordanceParameterKind
    description: str = Field(min_length=1, max_length=300)
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()


class AffordanceParameter(_StrictModel):
    name: str = Field(min_length=1, max_length=80)
    value: JsonValue


class AffordanceTarget(_StrictModel):
    target_id: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=1, max_length=80)


class AffordanceSelection(_StrictModel):
    """The entire game-action language exposed to the playing model."""

    affordance_id: str = Field(pattern=r"^aff-[0-9a-f]{20}$")
    target_id: str | None = Field(default=None, min_length=1, max_length=500)
    parameters: list[AffordanceParameter] = Field(default_factory=list, max_length=8)

    def parameter_map(self) -> dict[str, JsonValue]:
        return {parameter.name: parameter.value for parameter in self.parameters}


class AffordancePolicy(_StrictModel):
    """Mechanical policy owned by runtime code and hidden from planner prose."""

    control_modes: frozenset[ControlMode]
    required_capabilities: frozenset[str] = frozenset()
    execution: AffordanceExecution
    idempotency: IdempotencyPolicy
    pointer_actions: int = Field(default=0, ge=0)
    purchase_actions: int = Field(default=0, ge=0)
    native_assisted_actions: int = Field(default=0, ge=0)
    max_primitive_actions: int = Field(default=0, ge=0)
    timeout_seconds: float = Field(gt=0.0, le=300.0)
    controller_verified: bool = False


class AffordanceOffer(_StrictModel):
    affordance_id: str = Field(pattern=r"^aff-[0-9a-f]{20}$")
    source: AffordanceSource
    semantic: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    target: AffordanceTarget | None = None
    parameters: tuple[AffordanceParameterSpec, ...] = ()
    policy: AffordancePolicy
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


class AffordanceLifecycleEvent(_StrictModel):
    status: AffordanceLifecycleStatus
    telemetry_sequence: int | None = Field(default=None, ge=0)
    detail: str = Field(min_length=1, max_length=1000)


class AffordanceReceipt(_StrictModel):
    """Common evidence vocabulary for every source adapter."""

    affordance_id: str = Field(pattern=r"^aff-[0-9a-f]{20}$")
    source: AffordanceSource
    semantic: str = Field(min_length=1, max_length=100)
    target: AffordanceTarget | None = None
    parameters: list[AffordanceParameter] = Field(default_factory=list, max_length=8)
    status: Literal[
        AffordanceLifecycleStatus.SUCCEEDED,
        AffordanceLifecycleStatus.FAILED,
        AffordanceLifecycleStatus.REJECTED,
        AffordanceLifecycleStatus.INTERRUPTED,
    ]
    lifecycle: list[AffordanceLifecycleEvent] = Field(min_length=1, max_length=16)
    operation_kind: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=2000)


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


def _policy(
    *,
    execution: AffordanceExecution = AffordanceExecution.IMMEDIATE,
    idempotency: IdempotencyPolicy = IdempotencyPolicy.AT_MOST_ONCE,
    modes: frozenset[ControlMode] = frozenset(
        {ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}
    ),
    capabilities: Iterable[str] = (),
    pointer_actions: int = 0,
    purchase_actions: int = 0,
    native_actions: int = 0,
    primitives: int = 0,
    timeout_seconds: float = 10.0,
    controller_verified: bool = False,
) -> AffordancePolicy:
    return AffordancePolicy(
        control_modes=modes,
        required_capabilities=frozenset(capabilities),
        execution=execution,
        idempotency=idempotency,
        pointer_actions=pointer_actions,
        purchase_actions=purchase_actions,
        native_assisted_actions=native_actions,
        max_primitive_actions=primitives,
        timeout_seconds=timeout_seconds,
        controller_verified=controller_verified,
    )


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
    policy: AffordancePolicy | None = None,
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
        policy=policy or _policy(),
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
        policy=_policy(
            execution=AffordanceExecution.IMMEDIATE,
            idempotency=IdempotencyPolicy.SAFE_TO_RETRY,
            primitives=0,
            controller_verified=True,
        ),
    )
    yield _offer(
        observation,
        source=AffordanceSource.RUNTIME,
        semantic="stop_run",
        description="End the whole run at an explicit terminal boundary.",
        operation_kind="stop",
        arguments={"reason": "The selected objective is terminal."},
        policy=_policy(controller_verified=True),
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
            policy=_policy(controller_verified=True),
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
            policy=_policy(controller_verified=True),
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
            policy=_policy(controller_verified=True),
        )


def _game_binding_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    for binding in GameBinding:
        if binding in TIME_GAME_BINDINGS:
            continue
        if game_binding_success_condition(binding, telemetry) is None:
            continue
        yield _offer(
            observation,
            source=AffordanceSource.GAME_BINDING,
            semantic=binding.value,
            description=f"Use Kenshi's named {binding.value} binding.",
            operation_kind="use_game_binding",
            arguments={"binding": binding.value, "expected_effect": binding.value},
            policy=_policy(
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                primitives=1,
            ),
        )


def _visible_control_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if (
        telemetry is None
        or telemetry.ui.visible_controls is None
        or "ui.visible_controls" not in telemetry.capabilities
    ):
        return
    for control in telemetry.ui.visible_controls:
        if is_runtime_owned_visible_control(control) or control.role == "item":
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
            policy=_policy(pointer_actions=1, primitives=2),
        )


def _context_order_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    native = "control.perform_context_action" in telemetry.capabilities
    for target in telemetry.world_targets:
        for order in target.context_actions:
            operation_kind = "perform_context_action" if native else "command_world_target"
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
                policy=_policy(
                    execution=(
                        AffordanceExecution.MONITORED
                        if native
                        else AffordanceExecution.IMMEDIATE
                    ),
                    modes=(
                        frozenset({ControlMode.NATIVE_ASSISTED})
                        if native
                        else frozenset(
                            {ControlMode.INTERFACE_ONLY, ControlMode.NATIVE_ASSISTED}
                        )
                    ),
                    native_actions=1 if native else 0,
                    pointer_actions=0 if native else 1,
                    primitives=1,
                    timeout_seconds=300.0 if native else 10.0,
                    controller_verified=native,
                ),
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
        {"control.approach_dialogue_target", "control.approach_vendor"}
        & capabilities
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
            policy=_policy(
                execution=AffordanceExecution.MONITORED,
                modes=frozenset({ControlMode.NATIVE_ASSISTED}),
                capabilities=required,
                native_actions=1,
                primitives=1,
                timeout_seconds=300.0,
                controller_verified=True,
            ),
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
    selected_id = telemetry.ui.selected_character_id
    seller_ids = [
        entity.id for entity in telemetry.nearby_entities if entity.has_vendor_list
    ]
    seller_id = seller_ids[0] if len(seller_ids) == 1 else None
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
            str(control.item_quantity)
            if control.item_quantity is not None
            else "unknown"
        )
        if (
            owner.get("belongs_to") == "vendor"
            and seller_id
            and control.item_base_value is not None
        ):
            yield _offer(
                observation,
                source=AffordanceSource.INVENTORY,
                semantic="buy",
                description=(
                    f"Buy {control.item_name!r} from {control.window!r} for "
                    f"{control.item_base_value} cats per unit; current cell "
                    f"quantity is {cell_quantity}."
                ),
                operation_kind="purchase_item",
                target=target,
                parameters=(_quantity_parameter(),),
                arguments={
                    **base,
                    "expected_price": control.item_base_value,
                    "seller_id": seller_id,
                },
                policy=_policy(
                    execution=AffordanceExecution.COMPOSITE,
                    pointer_actions=5,
                    purchase_actions=5,
                    primitives=10,
                    timeout_seconds=300.0,
                    controller_verified=True,
                ),
            )
        if owner.get("belongs_to") == "you" and selected_id:
            if telemetry.active_shop_trader_count == 1 and seller_id:
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
                    parameters=(_quantity_parameter(),),
                    arguments={**base, "buyer_id": seller_id},
                    policy=_policy(
                        execution=AffordanceExecution.COMPOSITE,
                        pointer_actions=5,
                        purchase_actions=5,
                        primitives=10,
                        timeout_seconds=300.0,
                        controller_verified=True,
                    ),
                )
            elif telemetry.active_shop_trader_count == 0:
                yield _offer(
                    observation,
                    source=AffordanceSource.INVENTORY,
                    semantic="equip",
                    description=f"Equip {control.item_name!r} from {control.window!r}.",
                    operation_kind="equip_item",
                    target=target,
                    arguments=base,
                    policy=_policy(pointer_actions=1, primitives=2),
                )


def _character_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    capabilities = set(telemetry.capabilities)
    selected = next((member for member in telemetry.squad if member.selected), None)
    for member in telemetry.squad:
        target = AffordanceTarget(
            target_id=member.id,
            label=member.name,
            kind="squad_member",
        )
        if member.id != telemetry.ui.selected_character_id:
            yield _offer(
                observation,
                source=AffordanceSource.SQUAD,
                semantic="select",
                description=f"Select {member.name!r} as the exact active squad member.",
                operation_kind="select_squad_member",
                target=target,
                arguments={"target_id": member.id},
                policy=_policy(pointer_actions=1, primitives=2),
            )
        if (
            selected is not None
            and member.id != selected.id
            and "control.regroup_with_squad_member" in capabilities
        ):
            yield _offer(
                observation,
                source=AffordanceSource.COMPOSITE_OPERATION,
                semantic="regroup",
                description=f"Bring {selected.name!r} to squadmate {member.name!r}.",
                operation_kind="regroup_with_squad_member",
                target=target,
                arguments={"actor_id": selected.id, "target_id": member.id},
                policy=_policy(
                    execution=AffordanceExecution.MONITORED,
                    modes=frozenset({ControlMode.NATIVE_ASSISTED}),
                    native_actions=1,
                    primitives=1,
                    timeout_seconds=300.0,
                    controller_verified=True,
                ),
            )
    for character in telemetry.nearby_entities:
        if character.is_animal or character.disposition.value == "hostile":
            continue
        yield _offer(
            observation,
            source=AffordanceSource.NEARBY_CHARACTER,
            semantic="move_to",
            description=f"Move to nearby character {character.name!r}.",
            operation_kind="move_to_character",
            target=AffordanceTarget(
                target_id=character.id,
                label=character.name,
                kind="character",
            ),
            arguments={"target_id": character.id},
            policy=_policy(
                execution=AffordanceExecution.MONITORED,
                modes=frozenset({ControlMode.NATIVE_ASSISTED}),
                native_actions=1,
                primitives=1,
                timeout_seconds=300.0,
                controller_verified=True,
            ),
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
            policy=_policy(
                execution=AffordanceExecution.MONITORED,
                modes=frozenset({ControlMode.NATIVE_ASSISTED}),
                native_actions=1,
                primitives=1,
                timeout_seconds=300.0,
                controller_verified=True,
            ),
        )


def _map_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    capabilities = set(telemetry.capabilities)
    if not {
        "control.travel_to_map_destination",
        "world.known_map_destinations",
    } <= capabilities:
        return
    selected = next((member for member in telemetry.squad if member.selected), None)
    if selected is None:
        return
    for destination in telemetry.known_map_destinations:
        if not map_destination_travel_available(
            destination,
            current_location_id=telemetry.game.location_id,
            inside_town_walls=telemetry.game.inside_town_walls,
            location_authoritative="game.location.identity" in capabilities,
        ):
            continue
        yield _offer(
            observation,
            source=AffordanceSource.MAP,
            semantic="travel",
            description=f"Travel to known map destination {destination.name!r}.",
            operation_kind="travel_to_map_destination",
            target=AffordanceTarget(
                target_id=destination.id,
                label=destination.name,
                kind="map_destination",
            ),
            arguments={"destination_id": destination.id},
            policy=_policy(
                execution=AffordanceExecution.MONITORED,
                modes=frozenset({ControlMode.NATIVE_ASSISTED}),
                native_actions=1,
                primitives=1,
                timeout_seconds=300.0,
                controller_verified=True,
            ),
        )


def _native_and_composite_offers(
    observation: Observation,
) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    capabilities = set(telemetry.capabilities)
    selected = next((member for member in telemetry.squad if member.selected), None)

    for direction in CameraRotationDirection:
        yield _offer(
            observation,
            source=AffordanceSource.NATIVE_OPERATION,
            semantic=f"rotate_camera_{direction.value}",
            description=f"Rotate the camera {direction.value} one bounded increment.",
            operation_kind="rotate_camera",
            arguments={"direction": direction.value},
            policy=_policy(pointer_actions=1, primitives=1),
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
        policy=_policy(
            execution=AffordanceExecution.MONITORED,
            modes=frozenset({ControlMode.NATIVE_ASSISTED}),
            native_actions=1,
            primitives=1,
            timeout_seconds=300.0,
            controller_verified=True,
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
            policy=_policy(
                execution=AffordanceExecution.MONITORED,
                modes=frozenset({ControlMode.NATIVE_ASSISTED}),
                native_actions=1,
                primitives=1,
                timeout_seconds=300.0,
                controller_verified=True,
            ),
        )

    yield _offer(
        observation,
        source=AffordanceSource.COMPOSITE_OPERATION,
        semantic="recover_camera_view",
        description="Restore a readable, character-following camera view.",
        operation_kind="recover_camera_view",
        policy=_policy(
            execution=AffordanceExecution.COMPOSITE,
            pointer_actions=1,
            primitives=20,
            timeout_seconds=30.0,
            controller_verified=True,
        ),
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
                policy=_policy(pointer_actions=1, primitives=2),
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
                policy=_policy(pointer_actions=1, primitives=2),
            )

    if selected is not None and "control.perform_context_action" in capabilities:
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
                policy=_policy(
                    execution=AffordanceExecution.COMPOSITE,
                    modes=frozenset({ControlMode.NATIVE_ASSISTED}),
                    native_actions=5,
                    pointer_actions=5,
                    primitives=45,
                    timeout_seconds=300.0,
                    controller_verified=True,
                ),
            )


_SOURCE_ENUMERATORS: tuple[
    Callable[[Observation], Iterable[AffordanceOffer]], ...
] = (
    _runtime_offers,
    _game_binding_offers,
    _visible_control_offers,
    _context_order_offers,
    _dialogue_target_offers,
    _inventory_offers,
    _character_offers,
    _map_offers,
    _native_and_composite_offers,
)


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
    from .action_contracts import contract_for

    operation = _operation_for(offer, _sample_parameters(offer))
    if observation.planning_mode is PlanningMode.SINGLE_STEP:
        try:
            TypeAdapter(SingleStepPlannerAction).validate_python(operation)
        except ValidationError:
            return False
    contract = contract_for(operation)
    if contract is None:
        return True
    telemetry = observation.telemetry
    capabilities = set(telemetry.capabilities if telemetry is not None else [])
    return (
        contract.allows_control_mode(observation.control_mode)
        and not contract.missing_capabilities(capabilities)
        and contract.is_currently_authorable(observation)
        and contract.bind(operation, observation).bound
    )


def offered_affordances(observation: Observation) -> tuple[AffordanceOffer, ...]:
    """Enumerate one immutable, fail-closed offer set for this observation."""

    telemetry = observation.telemetry
    if telemetry is None or observation.telemetry_stale:
        return ()
    capabilities = set(telemetry.capabilities)
    offers = tuple(
        offer
        for enumerate_source in _SOURCE_ENUMERATORS
        for offer in enumerate_source(observation)
        if observation.control_mode in offer.policy.control_modes
        and offer.policy.required_capabilities <= capabilities
        and _offer_binds_now(offer, observation)
    )
    ids = [offer.affordance_id for offer in offers]
    if len(ids) != len(set(ids)):
        raise RuntimeError("source adapters generated duplicate affordance IDs")
    if any(
        len(spec.choices) != len(set(spec.choices))
        for offer in offers
        for spec in offer.parameters
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
            raise ValueError(
                f"parameter {name!r} must be one of {', '.join(spec.choices)}"
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if spec.minimum is not None and value < spec.minimum:
                raise ValueError(f"parameter {name!r} is below its offered minimum")
            if spec.maximum is not None and value > spec.maximum:
                raise ValueError(f"parameter {name!r} exceeds its offered maximum")
    return supplied


@dataclass(frozen=True, slots=True)
class BoundAffordance:
    offer: AffordanceOffer
    selection: AffordanceSelection
    operation: Action


def bind_affordance(
    selection: AffordanceSelection,
    observation: Observation,
) -> BoundAffordance:
    """Re-enumerate, bind exactly, then materialize one private operation."""

    matches = [
        offer
        for offer in offered_affordances(observation)
        if offer.affordance_id == selection.affordance_id
    ]
    if len(matches) != 1:
        raise ValueError("affordance is absent from the current observation")
    offer = matches[0]
    expected_target_id = offer.target.target_id if offer.target else None
    if selection.target_id != expected_target_id:
        raise ValueError("selection target does not match the exact offered target")
    parameters = _validated_parameters(selection, offer)
    operation = _operation_for(offer, parameters)
    from .action_contracts import contract_for

    contract = contract_for(operation)
    if contract is not None:
        binding = contract.bind(operation, observation)
        if not binding.bound:
            raise ValueError(f"affordance no longer binds: {binding.reason}")
    return BoundAffordance(offer=offer, selection=selection, operation=operation)


def selection_for(
    offer: AffordanceOffer,
    **parameters: JsonValue,
) -> AffordanceSelection:
    """Construct an exact selection for deterministic planners and tests."""

    return AffordanceSelection(
        affordance_id=offer.affordance_id,
        target_id=offer.target.target_id if offer.target else None,
        parameters=[
            AffordanceParameter(name=name, value=value)
            for name, value in parameters.items()
        ],
    )
