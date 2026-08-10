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
    Action,
    ControlMode,
    GameBinding,
    ThreatResponseStrategy,
)
from .core.telemetry import (
    TRADE_WINDOW_AUTHORING_DISTANCE,
    CharacterState,
    ContextActionKind,
    NearbyEntity,
    WorldTarget,
    inventory_owner_is_within_trade_authoring_distance,
    map_destination_travel_available,
)
from .operation_definitions import (
    NATIVE_CHARACTER_ORDER_CAPABILITY,
    NATIVE_CLOSE_INTERFACE_CAPABILITY,
    NATIVE_DIALOGUE_OPTION_CAPABILITY,
    NATIVE_PRODUCE_RESOURCE_CAPABILITY,
    NATIVE_RESOURCE_OPERATOR_STATE_CAPABILITY,
    NATIVE_SHIFT_BODY_CAPABILITY,
    NATIVE_TRADE_WINDOW_CAPABILITY,
    NATIVE_TRANSFER_CAPABILITY,
    NEARBY_ORDERABLE_TASKS_CAPABILITY,
    OPERATION_DEFINITIONS,
    SQUAD_REGROUP_ARRIVAL_DISTANCE,
    BindingFailure,
    BoundOperation,
    OperationDefinition,
    OperationExecution,
    TerminalOwner,
    _body_is_shiftable,
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
        "consult_advisor",
        "noop",
        # A transfer exists only while two inventories are open, so gating it
        # behind a clear interface withheld the one operation that requires the
        # opposite. It was the last mirror of the retired clicking surface: this
        # list still named six operations that no longer exist and none of the
        # ones that replaced them.
        "transfer_item",
        # Pairing a second inventory while one is already open is how a trade
        # or a looting window is reached at all.
        "open_trade_window",
        "close_active_interface",
        "select_dialogue_option",
        # Playback is declared global_ui: it suspends the whole world and is
        # orthogonal to what is on screen, exactly as it is for a player, who
        # can pause with the inventory open. Gating it behind a clear interface
        # would let a modal strand an agent in a world whose clock it cannot
        # start. `wait` is deliberately not here - it is only meaningful while
        # the world both runs and is being watched.
        "pause",
        "set_speed",
        "read_fieldbook",
        "recall_memory",
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
    """The entire game-action language exposed to the playing model.

    A choice is named by what it means, not by an opaque handle. The handle
    remains for runtime provenance and is accepted when a caller has one, but a
    playing model is never required to reproduce it.

    That requirement was a real failure mode rather than a theoretical one: a
    live run stopped at step zero three times over because the model emitted
    `aff-9f556b8eaba80dbfd68c`, a plausible twenty-hex-character id that had
    never existed in any observation. Every other field it emits is meaningful
    and it gets those right; a hash is the one thing it cannot check itself
    against, and an invented one is indistinguishable from a remembered one.
    """

    semantic: str = Field(min_length=1, max_length=100)
    target_id: str | None = Field(default=None, min_length=1, max_length=500)
    parameters: list[AffordanceParameter] = Field(default_factory=list, max_length=8)

    def parameter_map(self) -> dict[str, JsonValue]:
        return {parameter.name: parameter.value for parameter in self.parameters}

    def describe(self) -> str:
        """How this choice reads back in a refusal."""

        return (
            f"{self.semantic!r} on {self.target_id!r}"
            if self.target_id
            else repr(self.semantic)
        )


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
    """Identity of a choice: what it is, not when it was seen.

    `sequence` was part of this hash, so "operate on that iron deposit" had a
    different identity on every telemetry publication. A planner authored the
    choice, the input lease waited, telemetry advanced, and the boundary looked
    for an identity that no longer existed anywhere - reporting "Affordance is
    absent from the current observation" while the deposit sat there and the
    same choice was being offered under a new name.

    Any operation whose lease wait spanned a publication was unauthorizable,
    which is why mining kept failing while paused-world UI actions succeeded.

    When the offer was made is provenance, and `offered_at_telemetry_sequence`
    already records it; freshness is settled by the world revision, telemetry
    age, binding, and recipient basis - none of which need identity to rot.
    """

    del sequence
    identity = json.dumps(
        {
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


def _quantity_parameter(
    maximum: int = 5,
    *,
    available: str = "",
) -> AffordanceParameterSpec:
    """How many, and how many there are.

    The bound used to live only in the schema, described as "gameplay quantity
    to attempt", so the number the planner had to choose arrived with no sense
    of what it was choosing between. Saying how many exist is the difference
    between picking a quantity and guessing one.
    """

    return AffordanceParameterSpec(
        name="quantity",
        kind=AffordanceParameterKind.INTEGER,
        description=(
            f"How many, 1 to {maximum}"
            + (f" ({available})" if available else "")
            + "."
        ),
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
    telemetry = observation.telemetry
    if telemetry is not None and telemetry.game.loaded:
        # Playback control. Kenshi loads paused, and every operation whose
        # milestone is a world outcome needs game time to advance before it can
        # reach one. Without these offered, an agent handed a paused save has no
        # move that can ever succeed: a live run spent all three of its plans on
        # world commands, reported "no causal transition" each time, and aborted
        # with elapsed_minutes frozen at its starting value. Sixty-four
        # affordances were on the menu and not one of them could start the clock.
        if telemetry.game.paused:
            yield _offer(
                observation,
                source=AffordanceSource.RUNTIME,
                semantic="resume_game",
                description=(
                    "Resume play. The world is paused, so nothing that depends "
                    "on the world changing can complete until it runs."
                ),
                operation_kind="pause",
                arguments={"paused": False},
            )
        else:
            yield _offer(
                observation,
                source=AffordanceSource.RUNTIME,
                semantic="pause_game",
                description="Pause play to decide without the world moving on.",
                operation_kind="pause",
                arguments={"paused": True},
            )
            # Gears only mean anything while the world is running, and waiting
            # through a paused world burns real seconds for no game time.
            yield _offer(
                observation,
                source=AffordanceSource.RUNTIME,
                semantic="set_game_speed",
                description="Choose an exact Kenshi playback gear.",
                operation_kind="set_speed",
                parameters=(
                    AffordanceParameterSpec(
                        name="speed",
                        kind=AffordanceParameterKind.INTEGER,
                        description="Playback gear: 1 normal, 2 fast, 3 fastest.",
                        minimum=1,
                        maximum=3,
                    ),
                ),
            )
            yield _offer(
                observation,
                source=AffordanceSource.RUNTIME,
                semantic="wait",
                description=(
                    "Let the running world advance for a bounded interval "
                    "without sending input."
                ),
                operation_kind="wait",
                parameters=(
                    AffordanceParameterSpec(
                        name="seconds",
                        kind=AffordanceParameterKind.NUMBER,
                        description=(
                            "Seconds of real time to observe. Productive work owns "
                            "its longer monitored interval instead of using wait."
                        ),
                        minimum=0,
                        maximum=8,
                    ),
                ),
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


def _context_order_description(
    order: ContextActionKind,
    target: WorldTarget,
) -> str:
    """Say what the order actually does, not merely that it exists.

    "Issue 'operate' to 'Iron Resource'" reads like "mine this for money", and
    it is not: it assigns a standing job whose output piles up inside the
    resource and never reaches anyone's pack. Natural resources use the
    monitored output operation instead, so this description now applies only
    to other reviewed context orders.
    """

    if order == ContextActionKind.OPERATE and target.kind == "natural_resource":
        occupied_slots = len(target.current_operator_ids)
        return (
            f"Assign the selection to operate {target.name!r} indefinitely "
            f"({occupied_slots}/{target.operator_capacity} operator slots occupied). "
            "Selection and queued work do not prove acceptance. Output stays in the "
            "resource; use produce_resource_output, then pair inventories and transfer "
            "the output slot to collect it."
        )
    return f"Issue {order.value!r} to {target.name!r}."


def _context_order_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    native = "control.perform_context_action" in telemetry.capabilities
    for target in telemetry.world_targets:
        for order in target.context_actions:
            # A natural-resource `operate` is an indefinite job assignment,
            # while the planner's mining primitive owns work through actual
            # output and releases it. Offering both caused the soak planner to
            # choose the indefinite one, then spend its next plans on invalid
            # generic waits. Keep the lower-level command routable for retained
            # jobs, but make productive mining the only authored resource verb.
            if order == ContextActionKind.OPERATE and target.kind == "natural_resource":
                continue
            # Native or not at all. `command_world_target` clicked the object's
            # screen position for semantics the native route refuses, which is a
            # real gap - but a mouse fallback is how the planner kept choosing
            # the clicking path, so the gap is left visible instead of covered.
            if not (
                native
                and (
                    order == ContextActionKind("first_aid")
                    and target.kind == "squad_character"
                )
            ):
                continue
            operation_kind = "perform_context_action"
            yield _offer(
                observation,
                source=AffordanceSource.CONTEXT_ORDER,
                semantic=order.value,
                description=_context_order_description(order, target),
                operation_kind=operation_kind,
                target=AffordanceTarget(
                    target_id=target.id,
                    label=target.name,
                    kind=target.kind,
                ),
                arguments={"target_id": target.id, "context_action": order.value},
            )


def _body_shift_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    """Offer every body the agent could currently become.

    Enumerated rather than left to the planner to name, for the same reason
    every other target is: an operation proposed against an entity the
    observation does not currently offer is a plan that cannot bind, and the
    planner learns nothing useful from the refusal.
    """

    telemetry = observation.telemetry
    if telemetry is None or observation.control_mode is not ControlMode.NATIVE_ASSISTED:
        return
    capabilities = set(telemetry.capabilities)
    required = {
        NATIVE_SHIFT_BODY_CAPABILITY,
        "identity.stable_handles",
        "nearby.characters",
    }
    if not required <= capabilities:
        return
    if telemetry.ui.active_screen != "world":
        return
    for entity in telemetry.nearby_entities:
        if not _body_is_shiftable(entity):
            continue
        faction = entity.faction or "no faction"
        yield _offer(
            observation,
            source=AffordanceSource.NEARBY_CHARACTER,
            semantic="shift_into_body",
            description=(
                f"Become {entity.name!r} of {faction}, leaving the current body behind."
            ),
            operation_kind="shift_into_body",
            target=AffordanceTarget(
                target_id=entity.id,
                label=entity.name,
                kind="character",
            ),
            arguments={"target_id": entity.id},
        )


# One person affording a dozen distinct orders is already an unusual scene;
# Uncapped. Twelve orders per person, chosen silently, with nothing saying a
# thirteenth was dropped, would overrule the menu authority we asked Kenshi to
# provide.
MAX_ORDERS_OFFERED_PER_PERSON = None


def _character_order_description(order: str, entity: NearbyEntity) -> str:
    """Say what the order is, on whom, and in what state they are.

    The order keeps Kenshi's own name rather than a prettier synonym. A run
    bundle a year from now should let someone match the choice to the engine
    task it issued, and a translation layer would break that for the sake of
    reading slightly better.
    """

    standing = "unconscious" if entity.conscious is False else "conscious"
    distance = f"{entity.distance:g} away" if entity.distance is not None else "distance unknown"
    return (
        f"Order the current selection to {order.replace('_', ' ')} "
        f"{entity.name!r} ({entity.faction or 'no faction'}, {standing}, {distance}). "
        f"Kenshi currently advertises this order on them as {order.upper()}."
    )


def _character_order_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    """Offer every order Kenshi currently advertises on every nearby person.

    Deliberately uncurated. The engine already answered, per target, which of
    its own tasks apply, so this enumerates that answer instead of re-deriving
    it. Attacking, looting, and aiding are not special cases here; they are
    whatever Kenshi said yes to on someone standing nearby.

    Unprobed people are skipped rather than offered empty: probing is budgeted,
    so silence about what someone affords is not a claim that they afford
    nothing.
    """

    telemetry = observation.telemetry
    if telemetry is None or observation.control_mode is not ControlMode.NATIVE_ASSISTED:
        return
    capabilities = set(telemetry.capabilities)
    required = {
        NATIVE_CHARACTER_ORDER_CAPABILITY,
        NEARBY_ORDERABLE_TASKS_CAPABILITY,
        "nearby.characters",
    }
    if not required <= capabilities:
        return
    if telemetry.ui.active_screen != "world":
        return
    for entity in telemetry.nearby_entities:
        if not entity.advertised_tasks_probed:
            continue
        for order in entity.orderable_task_names():
            yield _offer(
                observation,
                source=AffordanceSource.NEARBY_CHARACTER,
                semantic=order,
                description=_character_order_description(order, entity),
                operation_kind="perform_character_order",
                target=AffordanceTarget(
                    target_id=entity.id,
                    label=entity.name,
                    kind="character",
                ),
                arguments={"target_id": entity.id, "order": order},
            )


def _semantic_slug(value: str) -> str:
    """A stable lower-snake fragment for naming one choice among many."""

    cleaned = "".join(
        character.lower() if character.isalnum() else "_" for character in value
    ).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "unnamed"


MAX_TRADE_WINDOWS_OFFERED = 12


def _trade_window_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    """Offer pairing the selected character's inventory with someone else's.

    This is the state a transfer acts in, and the single-inventory opener
    cannot produce it: `showInventory` shows a character's personal gear, which
    is the view for stealing. Kenshi's own window types come along unchanged -
    looting and money trading are one mechanism with a flag, not two problems.
    """

    telemetry = observation.telemetry
    if telemetry is None or observation.control_mode is not ControlMode.NATIVE_ASSISTED:
        return
    if NATIVE_TRADE_WINDOW_CAPABILITY not in set(telemetry.capabilities):
        return
    if telemetry.ui.dialogue_open is not False:
        return
    actor = telemetry.primary_character_id
    if actor is None:
        return
    held = {inventory.owner_id for inventory in telemetry.ui.open_inventories}

    # Squad and reviewed resources first, then nearby owners by stable id.
    #
    # This listed nearby entities first and truncated at the cap, so with a
    # crowd around the squad pairings fell off the end - and *which* nearby ones
    # survived shifted as distances changed. A planner copied a real
    # `affordance_id`, the next observation no longer carried it, and the choice
    # was refused as absent. `_offer_id` already carries a note about this
    # failure from the last time an offer's identity was unstable; the identity
    # was fine here and the membership was not.
    others: list[tuple[str, str, str]] = [
        (member.id, member.name, "squad_character")
        for member in sorted(telemetry.roster, key=lambda member: member.id)
        if member.id != actor
    ] + [
        (target.id, target.name, target.kind)
        for target in sorted(telemetry.world_targets, key=lambda target: target.id)
        if target.kind == "natural_resource"
        and ContextActionKind.OPERATE in target.context_actions
        and target.default_task == "operate_machinery"
    ] + [
        (entity.id, entity.name, entity.kind)
        for entity in sorted(telemetry.nearby_entities, key=lambda entity: entity.id)
    ]
    offered = 0
    for owner_id, label, kind in others:
        if (
            owner_id in held
            or not inventory_owner_is_within_trade_authoring_distance(
                telemetry,
                owner_id,
            )
            or offered >= MAX_TRADE_WINDOWS_OFFERED
        ):
            continue
        offered += 1
        yield _offer(
            observation,
            source=AffordanceSource.INVENTORY,
            semantic="open_trade_window",
            description=(
                f"Open your inventory alongside {kind} {label!r} so items can "
                "move between them. This owner is inside the conservative local "
                f"interaction fence ({TRADE_WINDOW_AUTHORING_DISTANCE:g} units); "
                "Kenshi's exact trade-range predicate is still required before "
                "the window can complete."
            ),
            operation_kind="open_trade_window",
            target=AffordanceTarget(target_id=owner_id, label=label, kind=kind),
            arguments={
                "first_owner_id": actor,
                "second_owner_id": owner_id,
                "window_type": "auto",
            },
        )


def _interface_exit_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    """Offer one native escape whenever an observed interface blocks the world."""

    telemetry = observation.telemetry
    if telemetry is None or observation.control_mode is not ControlMode.NATIVE_ASSISTED:
        return
    if NATIVE_CLOSE_INTERFACE_CAPABILITY not in set(telemetry.capabilities):
        return
    ui = telemetry.ui
    blocked = bool(
        ui.dialogue_open is True
        or ui.modal_open is True
        or (ui.open_inventory_windows or 0) > 0
        or ui.stats_window_open is True
        or ui.prospecting_window_open is True
        or ui.management_screen_open is True
        or (ui.active_screen is not None and ui.active_screen != "world")
    )
    if not blocked:
        return
    yield _offer(
        observation,
        source=AffordanceSource.NATIVE_OPERATION,
        semantic="return_to_world",
        description=(
            "Close the currently blocking interface through Kenshi's native UI "
            "lifecycle and return to the world."
        ),
        operation_kind="close_active_interface",
        arguments={},
    )


def _dialogue_option_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    """Offer every exact reply in the current ordered conversation surface."""

    telemetry = observation.telemetry
    if telemetry is None or observation.control_mode is not ControlMode.NATIVE_ASSISTED:
        return
    if NATIVE_DIALOGUE_OPTION_CAPABILITY not in set(telemetry.capabilities):
        return
    ui = telemetry.ui
    if (
        ui.dialogue_open is not True
        or not ui.dialogue_target_id
        or ui.dialogue_options is None
    ):
        return
    for option_index, option_text in enumerate(ui.dialogue_options):
        # Exact caption is part of the action and request. Withhold a caption
        # the strict address cannot carry rather than truncating it into a
        # different reply.
        if not option_text or len(option_text) > 500:
            continue
        yield _offer(
            observation,
            source=AffordanceSource.DIALOGUE,
            semantic=f"reply_{option_index + 1}",
            description="Choose this exact current dialogue reply.",
            operation_kind="select_dialogue_option",
            target=AffordanceTarget(
                target_id=ui.dialogue_target_id,
                label=option_text,
                kind="dialogue_option",
            ),
            arguments={
                "dialogue_target_id": ui.dialogue_target_id,
                "option_index": option_index,
                "option_text": option_text,
            },
        )


# No cap. A shop's contents are bounded and knowable, and a silent limit on
# them is not a smaller world -- it is a world the agent cannot tell apart from
# a smaller one. Truncation manufactures exactly the confusion the tri-state
# exists to prevent: it produces "not asked" wearing the face of "there is
# nothing there". Commerce is the case that makes it obvious, because an agent
# shown an arbitrary slice of a shop cannot compare prices, cannot decide what
# to sell, and cannot see that the thing it wants is even present.
#
# Measured live: the Barman's ~30 items filled a 24-offer cap in the *first*
# direction, so every offer was buying and not one sale was ever offered. The
# agent was told, in effect, that selling did not exist.
#
# The structural fix is to stop enumerating the cartesian product of items and
# destinations and make the item a *parameter* of one move -- enumerate verbs,
# parameterize nouns -- so the offer set stops scaling with world size. Until
# that lands, completeness beats compactness: an offer set that is merely large
# is a cost, while one that is quietly partial is a lie.


def _item_transfer_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    """Offer moving each item in each open inventory to each other open one.

    Uncurated on purpose, in the same way orders are. Looting, buying, selling
    and giving are not four kinds of offer here; they are this offer with
    different owners. Native code owns model capacity and simplified shop
    pricing. It deliberately does not claim Kenshi's richer theft, faction,
    stolen-goods, or haggling adjudication.

    Reach is the one exception, and it is not an exception to that principle:
    `within_trade_range` is Kenshi's `isWithinRangeToTrade`, not a distance rule
    invented here. An out-of-reach window refuses every move in it, so offering
    its contents is offering choices that cannot be dispatched - measured live,
    a trade window opened against a shopkeeper across town advertised three
    carried items and refused all three. Unknown reach still offers: None means
    the engine was not asked, which is silence rather than a denial.
    """

    telemetry = observation.telemetry
    if telemetry is None or observation.control_mode is not ControlMode.NATIVE_ASSISTED:
        return
    if NATIVE_TRANSFER_CAPABILITY not in set(telemetry.capabilities):
        return
    inventories = telemetry.ui.open_inventories
    if len(inventories) < 2:
        return

    for source in inventories:
        if source.within_trade_range is False:
            continue
        for destination in inventories:
            if destination.owner_id == source.owner_id:
                continue
            if destination.within_trade_range is False:
                continue
            for section in source.sections:
                # Worn gear is offered. It is most of what a body has, and the
                # refusal that hid it was inherited from a crash that turned out
                # to be a calling convention, not equipment.
                for item in section.items:
                    # The item and destination are part of the semantic, not
                    # decoration. The target is the *source* inventory, so two
                    # items in it collapsed to one indistinguishable choice and
                    # the planner's selection was refused for matching two.
                    # `_character_order_offers` learned this first: one person
                    # affording several orders needs the order in the semantic.
                    #
                    # The slot is in the name because the item's name is not its
                    # identity: two characters carry identically named gear, and
                    # one inventory can hold the same item twice. Section and
                    # coordinates are what the engine transfers by, so they are
                    # what distinguishes one choice from another.
                    yield _offer(
                        observation,
                        source=AffordanceSource.INVENTORY,
                        semantic=(
                            f"transfer_{_semantic_slug(item.item_name)}"
                            f"_{_semantic_slug(section.name)}_{item.x}_{item.y}"
                            f"_to_{_semantic_slug(destination.owner_name)}"
                        ),
                        description=(
                            f"Move {item.item_name!r} from {source.owner_name!r} "
                            f"to {destination.owner_name!r} through the native "
                            "inventory model and declared shop-pricing rule."
                        ),
                        operation_kind="transfer_item",
                        target=AffordanceTarget(
                            target_id=source.owner_id,
                            label=f"{item.item_name} to {destination.owner_name}",
                            kind="inventory_item",
                        ),
                        arguments={
                            "source_owner_id": source.owner_id,
                            "destination_owner_id": destination.owner_id,
                            "section_name": section.name,
                            "slot_x": item.x,
                            "slot_y": item.y,
                            "item_name": item.item_name,
                        },
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



def _character_offers(observation: Observation) -> Iterable[AffordanceOffer]:
    telemetry = observation.telemetry
    if telemetry is None:
        return
    capabilities = set(telemetry.capabilities)
    selected = telemetry.primary_character()
    # Selection is native or it does not happen. There used to be a pointer
    # fallback offered whenever this was false, which meant two operations for
    # one act: one that asks Kenshi to select a character and one that clicks
    # the portrait. Coexistence like that is what the desktop subsystem exists
    # to serve, so the fallback is gone rather than kept for a mode nothing
    # live uses.
    exact_selection = bool(
        observation.control_mode is ControlMode.NATIVE_ASSISTED
        and "control.select_squad_member" in capabilities
        and "identity.stable_handles" in capabilities
        and telemetry.primary_character_id is not None
        and telemetry.primary_character_id in telemetry.selected_character_ids
    )
    for member in telemetry.roster if exact_selection else []:
        target = AffordanceTarget(
            target_id=member.id,
            label=member.name,
            kind="squad_member",
        )
        if telemetry.selected_character_ids != [member.id]:
            yield _offer(
                observation,
                source=AffordanceSource.SQUAD,
                semantic="select_only",
                description=(
                    f"Replace the current selection with only {member.name!r}, "
                    "deselecting every other party member."
                ),
                operation_kind="select_squad_member_exact",
                target=target,
                arguments={"target_id": member.id},
            )
    # Selecting the whole party was a keystroke through `use_game_binding`.
    # Kenshi has no native "select all" this bridge can reach yet, so the offer
    # is gone rather than kept as the one remaining reason to send input; the
    # agent selects members individually through the native path.
    if (
        selected is not None
        and selected.position is not None
        and "control.regroup_with_squad_member" in capabilities
    ):
        candidates = [
            member
            for member in telemetry.roster
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
    selected_count = len(telemetry.selected_character_ids)
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
    selected = telemetry.selected_characters()
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
    # Kenshi's exported primary, not the first selected member the exporter
    # happened to walk - the harvest binding requires the primary, so offering
    # it against anyone else manufactures a choice that cannot bind.
    selected = next(
        (
            member
            for member in telemetry.roster
            if member.id == telemetry.primary_character_id
        ),
        None,
    )

    primary_id = telemetry.primary_character_id
    if primary_id and primary_id in telemetry.selected_character_ids:
        primary = next(
            (member for member in telemetry.roster if member.id == primary_id),
            None,
        )
        if primary is not None:
            yield _offer(
                observation,
                source=AffordanceSource.NATIVE_OPERATION,
                semantic="survey_local_resources",
                description=(
                    f"Survey mineral and water resources around {primary.name!r}'s "
                    "current position. Returns a grid with each resource's peak "
                    "location, so a resource present as a discrete deposit is "
                    "located rather than averaged away to zero."
                ),
                operation_kind="survey_local_resources",
                arguments={},
            )

    if {
        NATIVE_PRODUCE_RESOURCE_CAPABILITY,
        NATIVE_RESOURCE_OPERATOR_STATE_CAPABILITY,
    }.issubset(telemetry.capabilities):
        for target in telemetry.world_targets:
            if not (
                target.kind == "natural_resource"
                and ContextActionKind.OPERATE in target.context_actions
                and target.default_task == "operate_machinery"
                and target.operator_capacity is not None
                and target.operator_capacity > 0
                and target.current_operators_complete
                and target.output_inventory_complete
            ):
                continue
            occupied_slots = len(target.current_operator_ids)
            yield _offer(
                observation,
                source=AffordanceSource.NATIVE_OPERATION,
                semantic="produce_resource_output",
                description=(
                    f"Work {target.name!r} at fastest playback only until output stock "
                    "exists, then release "
                    f"controller-owned work ({occupied_slots}/{target.operator_capacity} "
                    "operator slots occupied). Selection and queued work do not prove "
                    "acceptance. Output stays in the resource; pair inventories and "
                    "transfer the output slot to collect it."
                ),
                operation_kind="produce_resource_output",
                target=AffordanceTarget(
                    target_id=target.id,
                    label=target.name,
                    kind=target.kind,
                ),
                arguments={"target_id": target.id},
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
        a resource operation on an iron deposit that could not bind,
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
        *,
        offer: AffordanceOffer | None = None,
    ) -> BoundOperation:
        """Re-enumerate this adapter and bind one exact current offer."""

        return _bind_adapter_selection(self, selection, observation, offer=offer)


AFFORDANCE_ADAPTERS: tuple[AffordanceAdapter, ...] = (
    AffordanceAdapter(
        name="dialogue_options",
        sources=frozenset({AffordanceSource.DIALOGUE}),
        operation_kinds=frozenset({"select_dialogue_option"}),
        denominator="Every exact current reply in the open dialogue's ordered option list.",
        completeness_boundary=(
            "The producer exports the complete rendered reply list. Empty captions "
            "and captions beyond the 500-character exact-address contract are withheld."
        ),
        enumerate=_dialogue_option_offers,
    ),
    AffordanceAdapter(
        name="interface_exit",
        sources=frozenset({AffordanceSource.NATIVE_OPERATION}),
        operation_kinds=frozenset({"close_active_interface"}),
        denominator="The current observed blocking interface, when one exists.",
        completeness_boundary=(
            "Native cleanup covers Prospecting, dialogue, message boxes, trade and "
            "inventory windows, and ordinary registered GUI windows."
        ),
        enumerate=_interface_exit_offers,
    ),
    AffordanceAdapter(
        name="runtime",
        sources=frozenset({AffordanceSource.RUNTIME}),
        operation_kinds=frozenset(
            {
                "noop",
                "stop",
                "consult_advisor",
                "recall_memory",
                "read_fieldbook",
                # Playback. Offered here rather than through a game binding
                # because pausing is a run-state decision, not a keystroke: the
                # agent needs to be able to start the clock it is being asked
                # to act inside.
                "pause",
                "set_speed",
                "wait",
            }
        ),
        denominator="Runtime control, playback, advisor, memory, and fieldbook state.",
        completeness_boundary="Only choices applicable to the current run state.",
        enumerate=_runtime_offers,
    ),
    AffordanceAdapter(
        name="context_orders",
        sources=frozenset({AffordanceSource.CONTEXT_ORDER}),
        operation_kinds=frozenset({"perform_context_action"}),
        denominator="Every exact reviewed world-target/order pair authorable now.",
        completeness_boundary=(
            "Only squad-character first_aid is emitted here. Natural-resource "
            "operate is an indefinite standing job and is withheld in favor of the "
            "monitored produce_resource_output operation. Every other telemetry "
            "context action is withheld rather than routed through the retired "
            "pointer path."
        ),
        enumerate=_context_order_offers,
    ),
    AffordanceAdapter(
        name="body_shift",
        sources=frozenset({AffordanceSource.NEARBY_CHARACTER}),
        operation_kinds=frozenset({"shift_into_body"}),
        denominator=(
            "Every exact current conscious, non-animal, non-hostile nearby character."
        ),
        completeness_boundary=(
            "Native-assisted stable identity and nearby-character evidence; a body "
            "outside the reported radius cannot be offered and a hostile one is "
            "deliberately withheld."
        ),
        enumerate=_body_shift_offers,
    ),
    AffordanceAdapter(
        name="character_orders",
        sources=frozenset({AffordanceSource.NEARBY_CHARACTER}),
        operation_kinds=frozenset({"perform_character_order"}),
        denominator=(
            "Every order Kenshi currently advertises on every probed nearby person."
        ),
        completeness_boundary=(
            "Bounded by the native probe budget: the nearest few people are asked what "
            "they afford, and the rest report that they were not asked."
        ),
        enumerate=_character_order_offers,
    ),
    AffordanceAdapter(
        name="item_transfers",
        sources=frozenset({AffordanceSource.INVENTORY}),
        operation_kinds=frozenset({"transfer_item"}),
        denominator=(
            "Every item in every open inventory, offered into every other open "
            "inventory."
        ),
        completeness_boundary=(
            "Uncapped across the currently exported open inventories. Native model "
            "capacity and simplified shop pricing are enforced at dispatch; Kenshi's "
            "richer trade and theft adjudication is not claimed."
        ),
        enumerate=_item_transfer_offers,
    ),
    AffordanceAdapter(
        name="trade_windows",
        sources=frozenset({AffordanceSource.INVENTORY}),
        operation_kinds=frozenset({"open_trade_window"}),
        denominator=(
            "Every observed squadmate, reviewed natural resource, or nearby person "
            "whose exact current distance from the primary is inside the local "
            "trade-window authoring fence."
        ),
        completeness_boundary=(
            "Unknown or greater-than-30-unit distance is withheld before rendering. "
            "After a local open, Kenshi's exact trade-range predicate remains the "
            "terminal authority; nearby people remain subject to the stable owner cap."
        ),
        enumerate=_trade_window_offers,
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
                "select_squad_member_exact",
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
                "survey_local_resources",
                "produce_resource_output",
                "move_in_direction",
                "exit_current_building",
            }
        ),
        denominator="Current state for native movement and bounded resource production.",
        completeness_boundary=(
            "Only operations with a current binder and declared runtime completion "
            "boundary. Resource production stops at observed output; transferring "
            "that output remains a separate inventory operation."
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
    *,
    offer: AffordanceOffer | None = None,
) -> BoundOperation:
    telemetry = observation.telemetry
    interface_clear = bool(
        telemetry is not None
        and telemetry.ui.active_screen == "world"
        and telemetry.ui.modal_open is False
        and telemetry.ui.dialogue_open is False
    )
    wanted = offer.affordance_id if offer is not None else None
    matches = [
        offer
        for offer in adapter.offers(observation)
        if (
            offer.affordance_id == wanted
            if wanted is not None
            else (
                offer.semantic == selection.semantic
                and (offer.target.target_id if offer.target else None)
                == selection.target_id
            )
        )
        and (interface_clear or offer.operation_kind in INTERFACE_SCOPED_OPERATION_KINDS)
        and _offer_binds_now(offer, observation)
    ]
    distinct = {offer.affordance_id: offer for offer in matches}
    if len(distinct) != 1:
        raise ValueError(
            f"{len(distinct)} distinct offers match inside {adapter.name!r}; "
            "exactly one must"
        )
    offer = next(iter(distinct.values()))
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
        identity=operation_identity(definition, operation, binding, affordance, observation),
    )


class AffordanceChoiceError(ValueError):
    """A named choice does not pick out exactly one current offer.

    A type rather than a message, because the planner path has to recognise this
    exact failure to retry usefully, and recognising it by substring is one
    reworded error away from silently not recognising it at all.
    """


def resolve_selection(
    selection: AffordanceSelection,
    observation: Observation,
) -> AffordanceOffer:
    """Find the one current offer a named choice refers to.

    Names, not handles. A refusal here says what was asked for, what is
    available under that name, and which field would disambiguate - because the
    caller that gets this wrong is usually a model that cannot see why, and
    "absent" on its own taught it nothing.
    """

    offers = offered_affordances(observation)
    candidates = [offer for offer in offers if offer.semantic == selection.semantic]
    if not candidates:
        available = sorted({offer.semantic for offer in offers})
        raise AffordanceChoiceError(
            f"no current choice is named {selection.semantic!r}; "
            f"{len(offers)} are offered"
            + (f", named: {', '.join(available[:12])}" if available else "")
        )

    if selection.target_id is not None:
        narrowed = [
            offer
            for offer in candidates
            if offer.target is not None and offer.target.target_id == selection.target_id
        ]
        if not narrowed:
            targets = sorted(
                offer.target.target_id for offer in candidates if offer.target is not None
            )
            raise AffordanceChoiceError(
                f"{selection.semantic!r} is offered, but not on {selection.target_id!r}"
                + (f"; current targets: {', '.join(targets[:6])}" if targets else "")
            )
        candidates = narrowed

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise AffordanceChoiceError(
            f"no current choice matches {selection.describe()}"
        )
    kinds = sorted({offer.operation_kind for offer in candidates})
    # Two offers sharing a name and a target is an affordance-naming problem,
    # not something a caller can disambiguate: there is no third field to reach
    # for now that the handle is gone. Say so plainly rather than inventing one.
    raise AffordanceChoiceError(
        f"{selection.describe()} matches {len(candidates)} current choices "
        f"({', '.join(kinds)}); these need distinguishable names"
    )


def bind_affordance(
    selection: AffordanceSelection,
    observation: Observation,
) -> BoundOperation:
    """Route a selection back to its issuing adapter for exact current binding."""

    resolved = resolve_selection(selection, observation)
    # Deduplicate before counting. An id is derived from what a choice *is*, so
    # a repeated identical offer is the same choice seen twice, not ambiguity: a
    # trade window holding ten identically-labelled cells emits ten identical
    # identical offers. `offered_affordances` already collapses them, so the
    # planner sees one and picks it correctly; this raw walk must agree.
    matches = {
        adapter.name: adapter
        for adapter in AFFORDANCE_ADAPTERS
        for offer in adapter.offers(observation)
        if offer.affordance_id == resolved.affordance_id
    }
    if len(matches) != 1:
        # `resolve_selection` already refused anything it could not place, and it
        # resolves against the same adapters, so reaching here means two adapters
        # claim one offer id. That is a registry inconsistency rather than a bad
        # choice, and saying so keeps it from being read as the caller's fault.
        raise RuntimeError(
            f"{len(matches)} adapters claim affordance {resolved.affordance_id!r} "
            f"({', '.join(sorted(matches))}); exactly one must issue it"
        )
    return next(iter(matches.values())).bind(selection, observation, offer=resolved)


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
    # Keyed, not appended. This is the third walk over raw adapter output to
    # count what it finds, and the third to mistake a repeated identical offer
    # for two different ones - a shop window with ten identically labelled cells
    # emits one choice ten times, and counting them called it ambiguous.
    rebounds: dict[str, BoundOperation] = {}
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
            semantic=offer.semantic,
            target_id=current_target_id,
            parameters=affordance.parameters,
        )
        try:
            candidate = adapter.bind(selection, observation)
        except ValueError:
            continue
        if candidate.operation == operation:
            rebounds[offer.affordance_id] = candidate
    if not rebounds:
        raise OperationBindingError(
            "Affordance is absent from the current observation.",
            code=AuthorizationCode.BINDING_ABSENT,
        )
    if len(rebounds) > 1:
        raise OperationBindingError(
            f"Affordance is ambiguous in the current observation: "
            f"{len(rebounds)} distinct offers match "
            f"({', '.join(sorted(rebounds))}).",
            code=AuthorizationCode.BINDING_AMBIGUOUS,
        )
    rebound = next(iter(rebounds.values()))
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
            observation,
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
            identity=operation_identity(definition, operation, binding, None, observation),
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
        semantic=offer.semantic,
        target_id=offer.target.target_id if offer.target else None,
        parameters=[
            AffordanceParameter(name=name, value=value) for name, value in parameters.items()
        ],
    )
