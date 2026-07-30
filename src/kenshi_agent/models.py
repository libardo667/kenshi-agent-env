from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import inf
from pathlib import Path
from time import monotonic
from typing import Any, Literal, TypeAlias
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .observation_budget import budget_observation_payload, irreducible_payload


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MemoryKind(StrEnum):
    FACT = "fact"
    EPISODE = "episode"
    COMMITMENT = "commitment"
    HYPOTHESIS = "hypothesis"


class ControlMode(StrEnum):
    INTERFACE_ONLY = "interface_only"
    NATIVE_ASSISTED = "native_assisted"


class PlanningMode(StrEnum):
    SINGLE_STEP = "single_step"
    CONTINUOUS = "continuous"


class LiveContinuousPolicy(StrEnum):
    DISABLED = "disabled"
    # Generic: validates contracts, references, and budgets rather than an exact
    # scenario recipe. It does not prescribe a step sequence.
    DIALOGUE_INTERACTION_V1 = "dialogue_interaction_v1"


class ScenarioIdentity(StrictModel):
    """One declared game situation, tied to the exact save used to reproduce it."""

    scenario_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    save_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    environment: Literal["indoor", "outdoor"]
    danger: Literal["hostile", "safe"]
    economy: Literal["broke", "funded"]
    party: Literal["solo", "squad"]
    time_of_day: Literal["day", "night"]


class ConditionKind(StrEnum):
    FIELD = "field"
    CAPABILITY = "capability"
    TELEMETRY_FRESH = "telemetry_fresh"


class ConditionOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    CONTAINS = "contains"


class ConditionResult(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class PointerActionClass(StrEnum):
    """How an action's coordinates are derived, which decides what must match.

    `coordinate_independent` actions carry no screen position at all.
    `semantic_current` actions resolve their position from live control,
    tooltip, or entity bounds that are re-read inside the input lease, so they
    survive a resolution change. `profile_calibrated` actions replay fixed
    normalized coordinates and are valid only under one exact identity.
    """

    COORDINATE_INDEPENDENT = "coordinate_independent"
    SEMANTIC_CURRENT = "semantic_current"
    PROFILE_CALIBRATED = "profile_calibrated"
    UNSUPPORTED = "unsupported"


class CalibrationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class CalibrationIdentity(StrictModel):
    """Every fact a profile-calibrated pointer action depends on.

    Each field is nullable and a missing value stays missing. A null is never
    treated as a match, because an unobserved window mode or UI scale is not
    evidence that it is the expected one.
    """

    client_width: int | None = Field(default=None, gt=0)
    client_height: int | None = Field(default=None, gt=0)
    window_mode: str | None = Field(default=None, min_length=1, max_length=32)
    ui_scale: float | None = Field(default=None, gt=0.0, le=8.0)
    dpi_scale: float | None = Field(default=None, gt=0.0, le=8.0)
    keymap_id: str | None = Field(default=None, min_length=1, max_length=64)
    profile_id: str | None = Field(default=None, min_length=1, max_length=80)
    profile_version: int | None = Field(default=None, ge=1)
    macro_set_hash: str | None = Field(default=None, min_length=1, max_length=64)

    def declared_fields(self) -> tuple[str, ...]:
        """Names this identity actually asserts, in stable order."""

        return tuple(
            name for name in self.__class__.model_fields if getattr(self, name) is not None
        )


class CalibrationReport(StrictModel):
    status: CalibrationStatus
    action_class: PointerActionClass
    reason: str = Field(min_length=1, max_length=1000)
    expected: CalibrationIdentity | None = None
    observed: CalibrationIdentity | None = None
    mismatched_fields: list[str] = Field(default_factory=list, max_length=16)
    unobserved_fields: list[str] = Field(default_factory=list, max_length=16)


class InputBoundaryDecision(StrEnum):
    """Outcome of the final revalidation performed inside the acquired input lease."""

    NOT_REQUIRED = "not_required"
    REVALIDATED = "revalidated"
    REJECTED = "rejected"


class InterruptPolicy(StrEnum):
    CANCEL_ON_REFLEX = "cancel_on_reflex"
    CANCEL_ON_REFLEX_OR_PLAN_PATCH = "cancel_on_reflex_or_plan_patch"


class ObservationPolicy(StrEnum):
    AFTER_ACTION = "after_action"
    UNTIL_TERMINAL = "until_terminal"


class IdempotencyPolicy(StrEnum):
    AT_MOST_ONCE = "at_most_once"
    SAFE_TO_RETRY = "safe_to_retry"


class MouseButton(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"
    X1 = "x1"
    X2 = "x2"


class CameraRotationDirection(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class CoordinateSpace(StrEnum):
    NORMALIZED = "normalized"
    CLIENT = "client"
    SCREEN = "screen"


class Disposition(StrEnum):
    FRIENDLY = "friendly"
    NEUTRAL = "neutral"
    HOSTILE = "hostile"
    UNKNOWN = "unknown"


class Vec2(StrictModel):
    x: float
    y: float


class Vec3(StrictModel):
    x: float
    y: float
    z: float


class BodyPartState(StrictModel):
    name: str
    current_hp: float | None = None
    max_hp: float | None = None
    cut_damage: float | None = None
    wear_damage: float | None = None
    bleeding_rate: float | None = None
    missing: bool | None = None


class InventoryItem(StrictModel):
    name: str = ""
    quantity: int = Field(default=1, ge=0)
    category: str | None = None
    charges: float | None = None
    stolen: bool | None = None
    # Emitted by the plug-in's own item description, shared with shop cells.
    item_name: str | None = Field(default=None, max_length=200)
    item_value: int | None = None
    item_quantity: int | None = Field(default=None, ge=0)
    item_type: int | None = None
    # Which inventory section holds it, and whether that section is worn or
    # wielded rather than carried. A flat item list reported a character in
    # trousers holding a stick as carrying nothing.
    section: str = Field(default="", max_length=80)
    equipped: bool | None = None

    @model_validator(mode="after")
    def name_falls_back_to_item_name(self) -> InventoryItem:
        if not self.name and self.item_name:
            object.__setattr__(self, "name", self.item_name)
        return self


class CharacterState(StrictModel):
    id: str
    name: str
    selected: bool = False
    alive: bool | None = None
    conscious: bool | None = None
    down: bool | None = None
    crippled: bool | None = None
    getting_eaten: bool | None = None
    imprisoned: bool | None = None
    enslaved: bool | None = None
    in_combat: bool | None = None
    stealth: bool | None = None
    position: Vec3 | None = None
    movement_speed: float | None = None
    # Resolved native building membership. Kenshi can retain a valid-looking
    # indoor handle after its building no longer resolves, so the producer
    # fails that stale case closed. This is deliberately a boolean rather than
    # a model-authored location: it answers the exact controller question "is
    # an exit order applicable?"
    indoors: bool | None = None
    # How *fed* the character is, not how hungry, despite the name: a nutrition
    # reserve on a 0.0-3.0 scale where 3.0 is full and 0.0 is starving. It falls
    # slowly and eating refills it. Kenshi's own UI shows the same number times
    # a hundred, so an on-screen "Hunger: 300" is a character that needs nothing.
    # Read the wrong way round it is a standing instruction to panic: an agent
    # at 2.94, essentially full, spent a run buying food it could not eat.
    hunger: float | None = None
    blood: float | None = None
    bleeding_rate: float | None = None
    # Kenshi's own count, and not to be trusted against `inventory`: measured
    # live it reported 0 while the character carried two Greenfruit and a Water.
    # `inventory` names what is actually held; prefer it, and never conclude
    # from a zero here that there is nothing to eat.
    food_items: int | None = None
    first_aid_kits: int | None = None
    current_goal: str | None = None
    body_parts: list[BodyPartState] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    # False means the bounded native export omitted one or more items. Absence
    # from `inventory` is therefore usable as zero only when this is true.
    inventory_complete: bool | None = None


class NearbyEntity(StrictModel):
    id: str
    name: str
    kind: str = "unknown"
    is_animal: bool | None = None
    trader_squad: bool | None = None
    has_vendor_list: bool | None = None
    is_squad_leader: bool | None = None
    has_dialogue: bool | None = None
    shop_inventory_owner: bool | None = None
    faction: str | None = None
    disposition: Disposition = Disposition.UNKNOWN
    distance: float | None = None
    position: Vec3 | None = None
    camera_bearing_degrees: float | None = Field(default=None, ge=-180.0, le=180.0)
    screen_position: Vec2 | None = None
    visible: bool | None = None
    conscious: bool | None = None

    def is_dialogue_target(self) -> bool:
        """Deterministic "can the agent approach and talk to this person" fence.

        This is the general interaction primitive, not a vendor check: Kenshi's
        native talk order works on any non-hostile character with dialogue,
        vendor or not. Whether someone is talkable is a fact the telemetry
        already carries, not a judgment for the model to re-derive. Every flag
        must be explicitly set; a missing value is never assumed favorable.
        `visible` and `distance` are deliberately excluded: they gate when to
        act, not whether the person is a talk target, and the native approach
        paths to an occluded/indoor target.
        """

        return (
            self.is_animal is False
            and self.has_dialogue is True
            and self.disposition in (Disposition.NEUTRAL, Disposition.FRIENDLY)
        )

    def is_confirmed_vendor(self) -> bool:
        """A dialogue target the agent can also trade with.

        Vendor-ness is a specialization of talkability: a confirmed vendor is a
        talk target that additionally owns a vendor list and leads its shop
        squad. Trade is the downstream sub-task; the approach primitive itself
        is the general `is_dialogue_target`.
        """

        return (
            self.is_dialogue_target()
            and self.has_vendor_list is True
            and self.is_squad_leader is True
        )


class ContextActionKind(StrEnum):
    """A reviewed semantic action an exact world object accepts as an attempt."""

    OPERATE = "operate"


class WorldTarget(StrictModel):
    """A non-character object with exact reviewed contextual affordances.

    This is the semantic equivalent of what a player learns by right-clicking
    an object. A listed action authorizes an exact bounded attempt, not a
    prediction that Kenshi will accept or complete it. The native controller
    proves acceptance causally from the selected character's exact AI goal.
    Absence means unsupported, never permission to improvise a click.
    """

    id: str
    name: str
    kind: str
    position: Vec3
    distance: float = Field(ge=0.0)
    context_actions: list[ContextActionKind] = Field(default_factory=list)
    default_task: str
    mining_resource_level: float | None = None
    screen_position: Vec2 | None = None


class KnownMapDestination(StrictModel):
    """A settlement marker the current player has actually discovered.

    The stable identity and player-visible name authorize a semantic journey.
    The controller owns the exact waypoint; no world coordinate is exposed to
    or authored by the planner.
    """

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    distance: float = Field(ge=0.0)
    has_gates: bool | None = None


# This is intentionally a planner-policy radius, not a duplicate of the native
# walk-arrival tolerance. A settlement marker already within local-interaction
# range is still observed, but ordering another map-scale journey to it cannot
# produce a meaningful new route.
MINIMUM_REMOTE_MAP_TRAVEL_DISTANCE = 50.0


def map_destination_already_reached(
    destination: KnownMapDestination,
    *,
    current_location_id: str | None = None,
    inside_town_walls: bool | None = None,
    location_authoritative: bool = False,
) -> bool:
    return (
        location_authoritative
        and current_location_id == destination.id
        and (inside_town_walls is True or destination.has_gates is False)
    )


def map_destination_travel_available(
    destination: KnownMapDestination,
    *,
    current_location_id: str | None = None,
    inside_town_walls: bool | None = None,
    location_authoritative: bool = False,
) -> bool:
    if map_destination_already_reached(
        destination,
        current_location_id=current_location_id,
        inside_town_walls=inside_town_walls,
        location_authoritative=location_authoritative,
    ):
        return False
    return destination.distance > MINIMUM_REMOTE_MAP_TRAVEL_DISTANCE


def _nearest_first(entities: list[NearbyEntity]) -> list[NearbyEntity]:
    return sorted(
        entities,
        key=lambda entity: entity.distance if entity.distance is not None else inf,
    )


def dialogue_targets(entities: list[NearbyEntity]) -> list[NearbyEntity]:
    """Every non-hostile person the agent could approach and talk to, nearest first.

    The general interaction affordance the planner receives; it approves or picks
    a target rather than re-judging who is talkable.
    """

    return _nearest_first([entity for entity in entities if entity.is_dialogue_target()])


def confirmed_vendor_candidates(
    entities: list[NearbyEntity],
) -> list[NearbyEntity]:
    """Dialogue targets that are also confirmed trade vendors, nearest first."""

    return _nearest_first([entity for entity in entities if entity.is_confirmed_vendor()])


class GameState(StrictModel):
    loaded: bool = False
    paused: bool | None = None
    speed_multiplier: float | None = None
    day: int | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    elapsed_minutes: float | None = Field(default=None, ge=0)
    money: int | None = None
    location_id: str | None = None
    location_name: str | None = None
    inside_town_walls: bool | None = None


class CameraState(StrictModel):
    position: Vec3 | None = None
    center: Vec3 | None = None
    zoom: float | None = None


class NormalizedPointerBounds(StrictModel):
    min_x: float = Field(ge=0.0, le=1.0)
    max_x: float = Field(ge=0.0, le=1.0)
    min_y: float = Field(ge=0.0, le=1.0)
    max_y: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_order(self) -> NormalizedPointerBounds:
        if self.min_x > self.max_x:
            raise ValueError("min_x must not exceed max_x")
        if self.min_y > self.max_y:
            raise ValueError("min_y must not exceed max_y")
        return self

    def contains(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y


# The ceiling on a rendered observation, in characters. This is a property of
# the model, not a preference, so it belongs with the planner config that knows
# which model is being used - `PlannerConfig.max_context_chars` overrides it.
# The default is deliberately far below any current model (the smallest context
# we run against holds roughly a hundred times this) because exceeding it is a
# hard failure, while sitting under it costs nothing: what an observation
# actually spends is governed by `max_observation_chars`.
MAX_PLANNER_CONTEXT_CHARS = 400_000

# Only a backstop against a pathological control list, not the working limit:
# how many controls the planner actually sees is decided by how many fit in the
# payload's character budget. A hand-picked count is wrong on both sides - it
# starves a dense trade screen while leaving room unused on a sparse one - and
# picking a new one just moves the cliff.
MAX_DIGESTED_VISIBLE_CONTROLS = 4096


def group_controls_by_window(
    entries: Sequence[dict[str, Any]],
    owners: Mapping[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Present controls the way Kenshi actually arranges them: per window.

    A flat list repeats the window on all 188 entries and still leaves the
    planner to reconstruct which of two open inventories a cell sits in. That
    question is not cosmetic: in a trade the same right-click buys from the
    shop's window and sells from your own, so "which window" is the whole
    difference between spending and being robbed. A probe that ignored it once
    sold a character's clothes and weapon.

    Windows keep first-appearance order and controls keep document order within
    a window, so positional reasoning still holds. The key is the exact string
    an action's `window` argument takes, empty string included - it is meant to
    be copied, not read.
    """

    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        window = str(entry.get("window", ""))
        groups.setdefault(window, []).append(
            {key: value for key, value in entry.items() if key != "window"}
        )

    resolved = owners or {}
    grouped = []
    for window, controls in groups.items():
        group: dict[str, Any] = {"window": window}
        # Say whose window it is rather than leaving the planner to match the
        # caption against a trader's name. For a vendor this also carries the
        # id `purchase_item` needs as its `seller_id`, so nothing about the
        # trade has to be inferred from a string.
        group.update(resolved.get(normalize_control_label(window), {}))
        group["controls"] = controls
        grouped.append(group)
    return grouped


def budgeted_visible_controls(
    controls: Sequence[VisibleUIControl],
    limit: int = MAX_DIGESTED_VISIBLE_CONTROLS,
) -> list[VisibleUIControl]:
    """Trim the control list to a budget without starving any one role.

    The plug-in emits buttons first, then item cells, then text. Taking a flat
    prefix therefore drops text first and drops it entirely: a trade screen
    exports over two hundred controls, so every text widget fell outside a
    hundred-and-twenty entry window. Text is where Kenshi puts its refusals -
    "you can't afford that" - so the agent could act, be refused, and see a
    screen identical to the one before it acted.

    Round-robin across roles instead, so each role is represented before any
    role takes a second share, then restore document order so positional
    reasoning still holds. Truncation stays fail-closed: an unlisted control is
    one the planner will not author, never one it may author blindly.
    """

    by_role: dict[str, list[VisibleUIControl]] = {}
    for control in controls:
        by_role.setdefault(control.role, []).append(control)
    if not by_role:
        return []

    effective_limit = max(0, limit)
    chosen = [
        bucket[round_index]
        for round_index in range(max(len(bucket) for bucket in by_role.values()))
        for bucket in by_role.values()
        if round_index < len(bucket)
    ][:effective_limit]

    positions = {id(control): order for order, control in enumerate(controls)}
    chosen.sort(key=lambda control: positions[id(control)])
    return chosen


# Where a MyGUI window's close box sits relative to the window's own rect,
# measured live: the title bar's right end. Derived from observed bounds, so it
# follows the window when it moves and survives a resolution change - unlike the
# calibrated screen coordinates this replaces.
WINDOW_CLOSE_INSET_X = 0.012
WINDOW_CLOSE_INSET_Y = 0.011


def window_close_point(bounds: NormalizedPointerBounds) -> tuple[float, float]:
    """The close box of a window occupying these bounds."""

    return (bounds.max_x - WINDOW_CLOSE_INSET_X, bounds.min_y + WINDOW_CLOSE_INSET_Y)


def normalize_control_label(value: str) -> str:
    """Collapse whitespace and case so an exact label survives UI formatting.

    Used for both matching and duplicate detection, so "resolves to exactly one
    control" means the same thing everywhere.
    """

    return " ".join(value.split()).casefold()


class VisibleUIControl(StrictModel):
    label: str = Field(min_length=1, max_length=500)

    @field_validator("label", mode="before")
    @classmethod
    def truncate_long_label(cls, value: object) -> object:
        """Clip an over-long caption rather than rejecting the whole snapshot.

        Telemetry is evidence we receive, not a document we author, so a widget
        Kenshi chose to fill with prose must not be able to invalidate it. One
        long story message - a bar rumour running past 500 characters - made an
        entire live observation unparseable, which blinds the agent completely:
        no cells, no money, no screen, from one caption. Keeping the first 500
        characters preserves what the label was for.
        """

        if isinstance(value, str) and len(value) > 500:
            return value[:497] + "..."
        return value

    # Caption of the MyGUI window this control belongs to, when it has one.
    # Several open windows otherwise arrive as one flat list in which every
    # close button looks identical, so "close the shop" cannot be expressed.
    window: str = Field(default="", max_length=200)
    # For `item` cells: what the cell actually holds. Without these the agent
    # can only learn a cell's contents by hovering it, one model round-trip at
    # a time, while a human simply reads the shop.
    item_name: str | None = Field(default=None, max_length=200)
    item_value: int | None = None
    item_quantity: int | None = Field(default=None, ge=0)
    item_type: int | None = None
    section: str = Field(default="", max_length=80)
    # `item` is an inventory or shop grid cell. It carries no caption of its
    # own, so its label is an ordinal from the deterministic export walk and
    # what it actually holds must be read from the tooltip after hovering it.
    role: Literal["button", "text", "item"]
    bounds: NormalizedPointerBounds

    @property
    def center(self) -> tuple[float, float]:
        return (
            (self.bounds.min_x + self.bounds.max_x) / 2.0,
            (self.bounds.min_y + self.bounds.max_y) / 2.0,
        )


class UIState(StrictModel):
    active_screen: str | None = None
    modal_open: bool | None = None
    dialogue_open: bool | None = None
    dialogue_target_id: str | None = None
    dialogue_options: list[str] | None = None
    tooltip_visible: bool | None = None
    tooltip_text: str | None = None
    tooltip_source_bounds: NormalizedPointerBounds | None = None
    visible_controls: list[VisibleUIControl] | None = Field(
        default=None,
        # Must not be tighter than the plug-in's own export cap, or a rich
        # screen fails validation outright instead of arriving truncated.
        max_length=224,
    )
    # False means either the widget walk or the emitted-control budget bound.
    # A missing item/control is known absent only when this is true.
    visible_controls_complete: bool | None = None
    # Stable identity of the exact building whose contextual inventory is
    # currently open. Window captions are not identities: two resources may
    # share the same name.
    context_inventory_target_id: str | None = Field(default=None, max_length=200)
    context_menu_open: bool | None = None
    # Additional screen signals. `active_screen` collapses everything to
    # dialogue/trade/inventory/world, which cannot express "the stats window is
    # up" or "two inventory windows are open".
    stats_window_open: bool | None = None
    open_inventory_windows: int | None = Field(default=None, ge=0)
    # Map, squad, research and factions are tabs of one management window, not
    # separate screens, so `active_screen` cannot express them.
    management_screen_open: bool | None = None
    management_tab: int | None = Field(default=None, ge=-1)
    selected_character_id: str | None = None
    selected_character_ids: list[str] = Field(default_factory=list)
    client_width: int | None = Field(default=None, gt=0)
    client_height: int | None = Field(default=None, gt=0)


class NativeCommandStatus(StrEnum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class NativeCommandAcknowledgement(StrictModel):
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    command: Literal[
        "approach_confirmed_vendor",
        "move_to_character",
        "move_in_direction",
        "travel_to_map_destination",
        "exit_current_building",
        "operate_natural_resource",
        "produce_resource_output",
        "open_context_inventory",
    ]
    status: NativeCommandStatus
    reason: str = Field(min_length=1, max_length=200)
    # Targeted commands bind to one stable entity. Directional movement binds
    # to its bearing and distance instead and deliberately names no target.
    target_id: str = Field(default="", max_length=200)
    bearing_degrees: float = Field(default=0.0, ge=0.0, lt=360.0)
    distance_units: float = Field(default=0.0, ge=0.0, le=2000.0)
    # Retained in the acknowledgement so an adopted resource-production
    # command cannot silently satisfy a later request for a larger yield.
    minimum_output_quantity: int = Field(default=1, ge=1, le=5)
    selected_character_ids: list[str] = Field(min_length=1, max_length=1)
    based_on_telemetry_sequence: int = Field(ge=0)
    acknowledged_at_telemetry_sequence: int = Field(ge=0)
    accepted_at_telemetry_sequence: int | None = Field(default=None, ge=0)
    terminal_at_telemetry_sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_causal_lifecycle(self) -> NativeCommandAcknowledgement:
        if self.acknowledged_at_telemetry_sequence <= self.based_on_telemetry_sequence:
            raise ValueError(
                "acknowledged_at_telemetry_sequence must be later than the request basis"
            )
        if len(set(self.selected_character_ids)) != 1:
            raise ValueError("native acknowledgement requires exactly one selected character")
        if self.command == "move_in_direction":
            if self.target_id:
                raise ValueError("a directional acknowledgement must not name a target")
            if self.distance_units <= 0.0:
                raise ValueError("a directional acknowledgement requires a distance")
        elif self.command == "exit_current_building":
            if self.target_id:
                raise ValueError("a building-exit acknowledgement must not name a target")
            if self.bearing_degrees != 0.0 or self.distance_units != 0.0:
                raise ValueError("a building-exit acknowledgement must not carry direction fields")
        else:
            if not self.target_id:
                raise ValueError("this native acknowledgement requires a target")
            if self.bearing_degrees != 0.0 or self.distance_units != 0.0:
                raise ValueError("a targeted acknowledgement must not carry direction fields")

        if self.status == NativeCommandStatus.REJECTED:
            if self.accepted_at_telemetry_sequence is not None:
                raise ValueError("rejected acknowledgement must not report acceptance")
            if self.terminal_at_telemetry_sequence is None:
                raise ValueError("rejected acknowledgement requires terminal_at_telemetry_sequence")
        else:
            if self.accepted_at_telemetry_sequence is None:
                raise ValueError(
                    "accepted_at_telemetry_sequence is required after native acceptance"
                )
            if self.accepted_at_telemetry_sequence < self.acknowledged_at_telemetry_sequence:
                raise ValueError("accepted_at_telemetry_sequence cannot predate acknowledgement")

        if self.status in {
            NativeCommandStatus.COMPLETED,
            NativeCommandStatus.CANCELLED,
        }:
            if self.terminal_at_telemetry_sequence is None:
                raise ValueError("terminal_at_telemetry_sequence is required for terminal status")
        elif (
            self.status == NativeCommandStatus.ACCEPTED
            and self.terminal_at_telemetry_sequence is not None
        ):
            raise ValueError("accepted acknowledgement must not report a terminal sequence")

        if (
            self.terminal_at_telemetry_sequence is not None
            and self.terminal_at_telemetry_sequence < self.acknowledged_at_telemetry_sequence
        ):
            raise ValueError("terminal_at_telemetry_sequence cannot predate acknowledgement")
        return self


class NativeControlState(StrictModel):
    available: bool = False
    active_command_id: str | None = Field(
        default=None,
        pattern=r"^cmd-[0-9a-f]{32}$",
    )
    acknowledgements: list[NativeCommandAcknowledgement] = Field(
        default_factory=list,
        max_length=16,
    )
    last_command_sequence: int = Field(default=0, ge=0)
    last_command: str | None = None
    last_result: str | None = None
    last_target: str | None = None
    last_target_id: str | None = None

    @model_validator(mode="after")
    def acknowledgement_ids_are_unique(self) -> NativeControlState:
        command_ids = [ack.command_id for ack in self.acknowledgements]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("native acknowledgement command IDs must be unique")
        if (
            self.active_command_id is not None
            and self.acknowledgement_for(self.active_command_id) is None
        ):
            raise ValueError("active native command must have an acknowledgement")
        return self

    def acknowledgement_for(
        self,
        command_id: str,
    ) -> NativeCommandAcknowledgement | None:
        return next(
            (
                acknowledgement
                for acknowledgement in self.acknowledgements
                if acknowledgement.command_id == command_id
            ),
            None,
        )


class TelemetrySnapshot(StrictModel):
    protocol_version: str = "1.4.0"
    sequence: int = Field(default=0, ge=0)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = "unknown"
    identity_session_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    game: GameState = Field(default_factory=GameState)
    camera: CameraState = Field(default_factory=CameraState)
    ui: UIState = Field(default_factory=UIState)
    native_control: NativeControlState = Field(default_factory=NativeControlState)
    squad: list[CharacterState] = Field(default_factory=list)
    # Despite the historical wire name, this is the number of lifecycle-tracked
    # ShopTrader character objects loaded in the session. It is not the number
    # of open trades and must never confer current UI authority.
    active_shop_trader_count: int | None = Field(default=None, ge=0)
    nearby_entities: list[NearbyEntity] = Field(default_factory=list)
    world_targets: list[WorldTarget] = Field(default_factory=list)
    known_map_destinations: list[KnownMapDestination] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @model_validator(mode="after")
    def stable_identity_must_be_complete_and_consistent(self) -> TelemetrySnapshot:
        location = self.game
        if "game.location.identity" in self.capabilities:
            populated_location_fields = (
                location.location_id is not None,
                location.location_name is not None,
                location.inside_town_walls is not None,
            )
            if any(populated_location_fields) and not all(populated_location_fields):
                raise ValueError(
                    "game.location.identity requires location_id, location_name, "
                    "and inside_town_walls to be populated together"
                )
        if "identity.stable_handles" not in self.capabilities:
            return self
        if not self.identity_session_id:
            raise ValueError("identity.stable_handles requires a non-empty identity_session_id")

        squad_ids = [character.id for character in self.squad]
        nearby_ids = [entity.id for entity in self.nearby_entities]
        world_target_ids = [target.id for target in self.world_targets]
        all_ids = squad_ids + nearby_ids + world_target_ids
        if any(not entity_id for entity_id in all_ids):
            raise ValueError("stable entity IDs must be non-empty")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("stable entity IDs must be unique within a snapshot")

        selected_ids = self.ui.selected_character_ids
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("selected_character_ids must not contain duplicates")
        unknown_selected = set(selected_ids) - set(squad_ids)
        if unknown_selected:
            raise ValueError("selected_character_ids must refer to current squad IDs")
        if (
            self.ui.selected_character_id is not None
            and self.ui.selected_character_id not in selected_ids
        ):
            raise ValueError("selected_character_id must also appear in selected_character_ids")
        flagged_selected = {character.id for character in self.squad if character.selected}
        if flagged_selected != set(selected_ids):
            raise ValueError("squad selected flags must match selected_character_ids exactly")
        for acknowledgement in self.native_control.acknowledgements:
            sequences = [
                acknowledgement.acknowledged_at_telemetry_sequence,
                acknowledgement.accepted_at_telemetry_sequence,
                acknowledgement.terminal_at_telemetry_sequence,
            ]
            if any(sequence is not None and sequence > self.sequence for sequence in sequences):
                raise ValueError("native acknowledgement sequences cannot exceed snapshot sequence")
        return self


class NoopAction(StrictModel):
    kind: Literal["noop"] = "noop"
    reason: str = "No action required."


class StopAction(StrictModel):
    """End the whole run, not the current plan.

    The distinction is not obvious from the name and was undocumented, so a
    planner that had finished what it set out to do used this to say "that
    objective is complete" and ended an eighty-step run on step eleven, with a
    reason that read "resuming free play to pursue next objective". A plan ends
    by its steps completing; this ends the agent.
    """

    kind: Literal["stop"] = "stop"
    reason: str


class PauseAction(StrictModel):
    kind: Literal["pause"] = "pause"
    paused: bool = True


GAME_SPEED_MULTIPLIER_BY_GEAR: dict[int, float] = {
    1: 1.0,
    2: 3.0,
    3: 5.0,
}
"""Observed simulation multiplier selected by each ordinal Kenshi speed gear."""


class SetSpeedAction(StrictModel):
    """Set a running playback state at one of Kenshi's three ordinal gears.

    This is one semantic transition even when Kenshi is paused. The controller
    owns starting at gear 1 before selecting a faster gear, because the faster
    keys do not themselves resume a paused world.
    """

    kind: Literal["set_speed"] = "set_speed"
    speed: Literal[1, 2, 3]


class WaitAction(StrictModel):
    kind: Literal["wait"] = "wait"
    seconds: float = Field(ge=0.0, le=60.0)


class AdvisorFocus(StrEnum):
    NEXT_GOAL = "next_goal"
    SURVIVAL = "survival"
    FOOD = "food"
    ECONOMY = "economy"
    RECRUITMENT = "recruitment"
    TRAVEL = "travel"
    RECOVERY = "recovery"


class ConsultAdvisorAction(StrictModel):
    """Ask a read-only guide-grounded model for strategic advice.

    This is a cognitive action. It emits no controller primitives, never enters
    the environment dispatch path, and cannot directly change Kenshi state.
    """

    kind: Literal["consult_advisor"] = "consult_advisor"
    question: str = Field(min_length=1, max_length=600)
    focus: AdvisorFocus = AdvisorFocus.NEXT_GOAL


class RecallMemoryAction(StrictModel):
    """Deliberately read continuity beyond what automatic context already showed.

    A cognitive action. It emits no controller primitives, never enters the
    environment dispatch path, spends no pointer, purchase, or native risk
    budget, and cannot authorize anything. Durable memory searches active
    records; working-outcome searches resurface compact runtime-owned evidence
    that scrolled out of the rich recent window.
    """

    kind: Literal["recall_memory"] = "recall_memory"
    source: Literal["durable_memory", "working_outcomes"] = "durable_memory"
    query: str = Field(min_length=1, max_length=200)
    max_records: int = Field(default=4, ge=1, le=8)


FIELD_BOOK_PROJECT_ID_PATTERN = r"^fbp-[0-9a-f]{32}$"
FIELD_BOOK_ENTRY_ID_PATTERN = r"^fbe-[0-9a-f]{32}$"


class ReadFieldbookAction(StrictModel):
    """Electively inspect bounded private project context without game input."""

    kind: Literal["read_fieldbook"] = "read_fieldbook"
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )
    query: str | None = Field(default=None, min_length=1, max_length=200)
    max_entries: int = Field(default=4, ge=1, le=8)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("fieldbook query must not be blank")
        return normalized

    @model_validator(mode="after")
    def has_a_read_selector(self) -> ReadFieldbookAction:
        if self.project_id is None and self.query is None:
            raise ValueError("read_fieldbook requires project_id or query")
        return self


class KeyAction(StrictModel):
    kind: Literal["key"] = "key"
    key: str = Field(min_length=1, max_length=32)
    hold_seconds: float = Field(default=0.04, ge=0.0, le=5.0)


class HotkeyAction(StrictModel):
    kind: Literal["hotkey"] = "hotkey"
    keys: list[str] = Field(min_length=2, max_length=5)
    hold_seconds: float = Field(default=0.04, ge=0.0, le=5.0)


class MouseButtonAction(StrictModel):
    kind: Literal["mouse_button"] = "mouse_button"
    button: MouseButton
    hold_seconds: float = Field(default=0.04, ge=0.0, le=5.0)


class MouseDragAction(StrictModel):
    kind: Literal["mouse_drag"] = "mouse_drag"
    button: MouseButton
    delta_x: int = Field(ge=-512, le=512)
    delta_y: int = Field(ge=-512, le=512)
    steps: int = Field(default=8, ge=1, le=32)


class MoveCursorAction(StrictModel):
    kind: Literal["move_cursor"] = "move_cursor"
    x: float
    y: float
    space: CoordinateSpace = CoordinateSpace.NORMALIZED


class ClickAction(StrictModel):
    kind: Literal["click"] = "click"
    x: float
    y: float
    space: CoordinateSpace = CoordinateSpace.NORMALIZED
    button: MouseButton = MouseButton.LEFT
    clicks: int = Field(default=1, ge=1, le=3)
    hold_seconds: float = Field(default=0.0, ge=0.0, le=0.5)
    interval_seconds: float = Field(default=0.08, ge=0.0, le=1.0)


class ScrollAction(StrictModel):
    kind: Literal["scroll"] = "scroll"
    x: float
    y: float
    space: CoordinateSpace = CoordinateSpace.NORMALIZED
    notches: int = Field(ge=-8, le=8)

    @field_validator("notches")
    @classmethod
    def notches_must_move(cls, value: int) -> int:
        if value == 0:
            raise ValueError("notches must not be zero")
        return value


class ApproachDialogueTargetAction(StrictModel):
    """Initiate dialogue with one exact currently observed dialogue target.

    Reusable and target-generic: the target is any current non-hostile person
    the telemetry already reports as talkable, vendor or not. The action names
    only a stable observed identity, never a role, a scenario, or a coordinate.
    The controller issues Kenshi's native talk-to order and owns the whole
    monitored interaction, so there is no planner-visible "continue
    approaching" or prerequisite unpause command.
    """

    kind: Literal["approach_dialogue_target"] = "approach_dialogue_target"
    target_id: str = Field(min_length=1, max_length=200)


class PerformContextAction(StrictModel):
    """Perform one exact contextual task an observed world object advertises.

    The planner copies only a `(target_id, context_action)` pair from current
    `context_targets`. Native code re-resolves the object, rechecks the reviewed
    task, issues Kenshi's own default order, and proves the selected character's
    AI accepted that exact object/task pair.
    """

    kind: Literal["perform_context_action"] = "perform_context_action"
    target_id: str = Field(min_length=1, max_length=200)
    context_action: ContextActionKind


class CommandWorldTargetAction(StrictModel):
    """Right-click one exact current world target at telemetry-owned geometry."""

    kind: Literal["command_world_target"] = "command_world_target"
    target_id: str = Field(min_length=1, max_length=200)
    context_action: ContextActionKind


class SelectSquadMemberAction(StrictModel):
    """Select one exact current squad member through observed world geometry."""

    kind: Literal["select_squad_member"] = "select_squad_member"
    target_id: str = Field(min_length=1, max_length=200)


class ProduceResourceOutputAction(StrictModel):
    """Internal production phase retained until a bounded output yield exists."""

    kind: Literal["produce_resource_output"] = "produce_resource_output"
    target_id: str = Field(min_length=1, max_length=200)
    minimum_output_quantity: int = Field(default=1, ge=1, le=5)


class HarvestResourceAction(StrictModel):
    """Harvest a bounded yield from one exact resource into one exact actor.

    The planner chooses the actor, resource, and useful yield once. The
    controller owns production, inventory opening, exact conserved transfer,
    cleanup, and terminal proof as one interruptible option.
    """

    kind: Literal["harvest_resource"] = "harvest_resource"
    actor_id: str = Field(min_length=1, max_length=200)
    target_id: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=5)


class OpenContextInventoryAction(StrictModel):
    """Open the ordinary inventory UI for one exact observed world target."""

    kind: Literal["open_context_inventory"] = "open_context_inventory"
    target_id: str = Field(min_length=1, max_length=200)


class MoveInDirectionAction(StrictModel):
    """Walk a bearing and a distance from wherever the character is standing.

    The intended destination is a point, not a person, so this action models
    movement even when no nearby character can serve as a destination. Its
    native request and acknowledgement deliberately carry an empty target ID;
    command identity is the keyed command, selected character, bearing, and
    distance.

    `bearing_degrees` is clockwise from north, as read on the map: 0 north,
    90 east, 180 south, 270 west.
    """

    kind: Literal["move_in_direction"] = "move_in_direction"
    bearing_degrees: float = Field(ge=0.0, lt=360.0)
    distance_units: float = Field(gt=0.0, le=2000.0)
    # Said in the plan so a later observation can judge it; the action itself
    # cannot know what is that way.
    expected_effect: str = Field(min_length=1, max_length=200)


class TravelToMapDestinationAction(StrictModel):
    """Travel to one exact settlement marker already known to the player.

    Native code re-resolves the discovered marker, chooses Kenshi's waypoint,
    issues one pathing order, and owns camera follow and arrival. The planner
    never supplies map coordinates or re-authors movement pulses.
    """

    kind: Literal["travel_to_map_destination"] = "travel_to_map_destination"
    destination_id: str = Field(min_length=1, max_length=200)


class ExitCurrentBuildingAction(StrictModel):
    """Leave the selected character's current building through a native door.

    The planner supplies no coordinates, bearing, door identity, or retry
    strategy. The native controller resolves an unlocked door from the exact
    building the selected character currently occupies, issues one pathing
    order to its outdoor point, and owns completion or typed failure.
    """

    kind: Literal["exit_current_building"] = "exit_current_building"


class MoveToCharacterAction(StrictModel):
    """Walk to any exact observed nearby character without talking to them.

    Movement used to be possible only toward someone the agent could hold a
    conversation with, so inside a building holding two people that was the
    whole reachable world: it sold what it could, asked both for work, recorded
    that it was repeating itself, and had no action that could take it
    anywhere. Nearby characters are reported within four hundred units, which
    across a town is most of it, so walking to a person is how the agent gets
    to a place - the destination is a stable observed identity, never a
    coordinate.
    """

    kind: Literal["move_to_character"] = "move_to_character"
    target_id: str = Field(min_length=1, max_length=200)


class PurchaseItemAction(StrictModel):
    """Buy a bounded quantity of one item from exact seller-owned cells.

    Current producers name the cell and item directly. `expected_price` carries
    the best current value estimate for optional spending gates, but the
    exported `item_value` is base worth rather than an authoritative final shop
    charge. The model chooses the useful quantity once. The controller rebinds
    interchangeable stock after each transfer and proves both money loss and
    carried-item gain before attempting the next unit.
    """

    kind: Literal["purchase_item"] = "purchase_item"
    cell_label: str = Field(min_length=1, max_length=80)
    item_name: str = Field(min_length=1, max_length=200)
    expected_price: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=5)
    # Caption of the seller's own inventory window. A trade screen shows two
    # inventories whose cell ordinals run across both, so this is what says the
    # item being bought is the shop's rather than ours.
    window: str = Field(min_length=1, max_length=200)
    seller_id: str = Field(min_length=1, max_length=200)


class DismissScreenAction(StrictModel):
    """Close one bound inventory or trade window toward the world view.

    Exiting is as much a part of using an interface as entering it. Naming the
    screen and, where applicable, the owner window makes the action bind to
    observed state instead of blindly pressing a key and hoping. It deliberately
    does not end an active conversation: Kenshi's Escape opens the ESC menu, so
    dialogue must choose an exact visible closing reply.
    """

    kind: Literal["dismiss_screen"] = "dismiss_screen"
    expected_screen: Literal["dialogue", "trade", "inventory"]
    # Caption of the window to close. Inventory and trade windows are closed by
    # their own close box, whose position is derived from the window's observed
    # rect rather than a calibrated screen coordinate. An empty window uses the
    # configured dismiss key only when the current state can safely bind it; an
    # active dialogue target makes that route fail closed.
    window: str = Field(default="", max_length=200)


class ActivateVisibleControlAction(StrictModel):
    """Activate exactly one control the interface currently advertises.

    The arguments are an exact current label and role, not coordinates: the
    bounds come from telemetry and are re-resolved inside the input lease. The
    action knows nothing about which screen, which conversation, or which option
    index it is activating.
    """

    kind: Literal["activate_visible_control"] = "activate_visible_control"
    exact_label: str = Field(min_length=1, max_length=500)
    role: Literal["button", "text", "item"] = "button"
    # Optional narrowing when several windows advertise the same label.
    window: str = Field(default="", max_length=200)


SkillArgumentValue: TypeAlias = str | int | float | bool | None


class SkillArgument(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    value: SkillArgumentValue


class SkillAction(StrictModel):
    kind: Literal["skill"] = "skill"
    name: str = Field(min_length=1, max_length=80)
    args: list[SkillArgument] = Field(default_factory=list, max_length=20)

    @field_validator("args", mode="before")
    @classmethod
    def accept_argument_mapping(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [{"name": name, "value": argument} for name, argument in value.items()]
        return value

    def argument_map(self) -> dict[str, SkillArgumentValue]:
        return {argument.name: argument.value for argument in self.args}


class EquipItemAction(StrictModel):
    """Equip the item in one cell of the selected character's own inventory.

    Kenshi equips on right-click (`rightClickAutoEquipping`) - the *same*
    gesture that sells an item when a trade is open. That collision is the whole
    hazard: an equip attempted with a shop window up is a sale, and the item is
    gone before any postcondition could notice. So this action refuses outright
    unless no trade is active.
    """

    kind: Literal["equip_item"] = "equip_item"
    cell_label: str = Field(min_length=1, max_length=80)
    item_name: str = Field(min_length=1, max_length=200)
    # Must be the selected character's own inventory window.
    window: str = Field(min_length=1, max_length=200)


class SellItemAction(StrictModel):
    """Sell a bounded quantity from the agent's own inventory.

    The mirror of `purchase_item`, and the reason trading stopped being one-way:
    with only a purchase action the agent could spend its starting money and
    then never earn any.

    Deliberately carries no expected price. A shop pays its own multiplier on an
    item's value rather than the listed value, and that offer is not exported,
    so asserting a price here would be asserting something we cannot check - the
    exact failure mode `purchase_item` was built to avoid. What *is* checked is
    that the cell is in this character's own inventory and holds this item.
    """

    kind: Literal["sell_item"] = "sell_item"
    cell_label: str = Field(min_length=1, max_length=80)
    item_name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=5)
    # Caption of the inventory window the cell sits in; must be the selected
    # character's own window, never the trader's.
    window: str = Field(min_length=1, max_length=200)
    buyer_id: str = Field(min_length=1, max_length=200)


class CollectResourceOutputAction(StrictModel):
    """Transfer one exact resource-output cell into the selected squadmate.

    The exact world-target ID binds the source window to its observed resource.
    `source_quantity` is copied from the cell so the controller can prove that
    source loss equals destination gain after the right-click.
    """

    kind: Literal["collect_resource_output"] = "collect_resource_output"
    target_id: str = Field(min_length=1, max_length=200)
    cell_label: str = Field(min_length=1, max_length=80)
    item_name: str = Field(min_length=1, max_length=200)
    source_quantity: int = Field(gt=0)
    window: str = Field(min_length=1, max_length=200)
    section: Literal["out"] = "out"


class ScrollScreenAction(StrictModel):
    """Scroll inside one open window so more of its contents become visible.

    Shops and inventories hold more than fits on screen, and the export only
    ever describes what is currently rendered. Without this, stock past the
    first screenful is not merely hard to reach - it does not exist as far as
    the agent is concerned, and no amount of replanning reveals it.

    Names a window rather than a coordinate: the scroll lands at the centre of
    that window's own observed bounds, so it follows the window and survives a
    resolution change.
    """

    kind: Literal["scroll_screen"] = "scroll_screen"
    # Caption of the window to scroll, exactly as `visible_controls` reports it.
    window: str = Field(min_length=1, max_length=200)
    # Negative scrolls down (further into the list), positive scrolls up.
    notches: int = Field(ge=-8, le=8)

    @field_validator("notches")
    @classmethod
    def notches_must_move(cls, value: int) -> int:
        if value == 0:
            raise ValueError("notches must not be zero")
        return value


class RecoverCameraViewAction(StrictModel):
    """Ask the controller to restore a usable character-following world view.

    This action intentionally has no camera parameters. The caller identifies
    the problem; the controller owns the bounded follow, floor, zoom, orbit,
    capture, and scoring transaction and reports its terminal outcome.
    """

    kind: Literal["recover_camera_view"] = "recover_camera_view"


class RotateCameraAction(StrictModel):
    """Rotate the world camera one bounded horizontal increment."""

    kind: Literal["rotate_camera"] = "rotate_camera"
    direction: CameraRotationDirection


def camera_rotation_primitive(action: RotateCameraAction) -> MouseDragAction:
    """Map one semantic yaw increment to Kenshi's held-Mouse3 rotation mode."""

    delta_x = 96 if action.direction is CameraRotationDirection.LEFT else -96
    return MouseDragAction(
        button=MouseButton.MIDDLE,
        delta_x=delta_x,
        delta_y=0,
        steps=8,
    )


class GameBinding(StrEnum):
    """Kenshi's named control intentions under the shipped default keymap.

    The current physical mapping is a hard-coded copy of the shipped
    `controls.cfg`; it is not read from the user's active keymap. Membership is
    the planner-visible subset recorded by the game-binding parity ledger.
    """

    # Screens. Each is a toggle, so pressing twice returns to where it started.
    TOGGLE_INVENTORY = "toggle_inventory"
    TOGGLE_MAP = "toggle_map"
    TOGGLE_STATS = "toggle_stats"
    TOGGLE_HELP = "toggle_help"
    TOGGLE_CRAFTING = "toggle_crafting"
    TOGGLE_RESEARCH = "toggle_research"
    # Save-state control.
    QUICKLOAD = "quickload"
    # Construction.
    BUILD_APPLY = "build_apply"
    BUILD_MOVE_DOWN = "build_move_down"
    BUILD_MOVE_UP = "build_move_up"
    BUILD_ROTATE_LEFT = "build_rotate_left"
    BUILD_ROTATE_RIGHT = "build_rotate_right"
    BUILD_TILT_DECREASE = "build_tilt_decrease"
    BUILD_TILT_INCREASE = "build_tilt_increase"
    BUILD_UNDO = "build_undo"
    # Time.
    PAUSE = "pause"
    SPEED_1 = "speed_1"
    SPEED_2 = "speed_2"
    SPEED_3 = "speed_3"
    # Camera.
    CAMERA_FORWARD = "camera_forward"
    CAMERA_BACK = "camera_back"
    CAMERA_LEFT = "camera_left"
    CAMERA_RIGHT = "camera_right"
    CAMERA_ROTATE_LEFT = "camera_rotate_left"
    CAMERA_ROTATE_RIGHT = "camera_rotate_right"
    CAMERA_TILT_UP = "camera_tilt+"
    CAMERA_TILT_DOWN = "camera_tilt-"
    CAMERA_ZOOM_IN = "camera_zoom_in"
    CAMERA_ZOOM_OUT = "camera_zoom_out"
    CYCLE_RUN_SPEED = "cycle_run_speed"
    # Development host controls.
    EDITOR_DELETE = "editor_delete"
    EDITOR_TOGGLE = "editor_toggle"
    FLOOR_DOWN = "floor_down"
    FLOOR_UP = "floor_up"
    GIZMO_MOVE = "gizmo_move"
    GIZMO_ROTATE = "gizmo_rotate"
    GIZMO_SCALE = "gizmo_scale"
    FOCUS_CHAR = "focus_char"
    HIGHLIGHT = "highlight"
    # Selection.
    SELECT_ALL = "select_all"
    CHANGE_SQUAD = "change_squad"
    CHARACTER_NEXT = "character_next"
    CHARACTER_PREV = "character_prev"
    # Orders.
    STOP_MOVEMENT = "stop_movement"
    MEDIC = "medic"


GAME_BINDING_KEYS: dict[GameBinding, str | tuple[str, ...]] = {
    GameBinding.TOGGLE_INVENTORY: "i",
    GameBinding.TOGGLE_MAP: "m",
    GameBinding.TOGGLE_STATS: "c",
    GameBinding.TOGGLE_HELP: "f1",
    GameBinding.TOGGLE_CRAFTING: "y",
    GameBinding.TOGGLE_RESEARCH: "t",
    GameBinding.QUICKLOAD: "f9",
    GameBinding.BUILD_APPLY: "space",
    GameBinding.BUILD_MOVE_DOWN: "minus",
    GameBinding.BUILD_MOVE_UP: "equals",
    GameBinding.BUILD_ROTATE_LEFT: "comma",
    GameBinding.BUILD_ROTATE_RIGHT: "period",
    GameBinding.BUILD_TILT_DECREASE: "[",
    GameBinding.BUILD_TILT_INCREASE: "]",
    GameBinding.BUILD_UNDO: "backspace",
    GameBinding.PAUSE: "space",
    GameBinding.SPEED_1: "f2",
    GameBinding.SPEED_2: "f3",
    GameBinding.SPEED_3: "f4",
    GameBinding.CAMERA_FORWARD: "w",
    GameBinding.CAMERA_BACK: "s",
    GameBinding.CAMERA_LEFT: "a",
    GameBinding.CAMERA_RIGHT: "d",
    GameBinding.CAMERA_ROTATE_LEFT: "q",
    GameBinding.CAMERA_ROTATE_RIGHT: "e",
    GameBinding.CAMERA_TILT_UP: "comma",
    GameBinding.CAMERA_TILT_DOWN: "period",
    GameBinding.CAMERA_ZOOM_IN: "home",
    GameBinding.CAMERA_ZOOM_OUT: "end",
    GameBinding.CYCLE_RUN_SPEED: "numpad6",
    GameBinding.EDITOR_DELETE: "delete",
    GameBinding.EDITOR_TOGGLE: ("shift", "f12"),
    GameBinding.FLOOR_DOWN: "pagedown",
    GameBinding.FLOOR_UP: "pageup",
    GameBinding.GIZMO_MOVE: "h",
    GameBinding.GIZMO_ROTATE: "j",
    GameBinding.GIZMO_SCALE: "k",
    GameBinding.FOCUS_CHAR: "f",
    GameBinding.SELECT_ALL: "grave",
    GameBinding.CHANGE_SQUAD: "tab",
    GameBinding.CHARACTER_NEXT: "]",
    GameBinding.CHARACTER_PREV: "[",
    GameBinding.STOP_MOVEMENT: "r",
    GameBinding.MEDIC: "numpad7",
}
"""Default Kenshi key per binding; hard-coded, not parsed from active controls.cfg."""


GAME_BINDING_MOUSE_BUTTONS: dict[GameBinding, MouseButton] = {
    GameBinding.HIGHLIGHT: MouseButton.X2,
}
"""Default Kenshi mouse button per binding; hard-coded from controls.cfg."""


def game_binding_primitive(
    binding: GameBinding,
) -> KeyAction | HotkeyAction | MouseButtonAction:
    """Resolve one reviewed binding to the primitive that drives its default input."""

    mouse_button = GAME_BINDING_MOUSE_BUTTONS.get(binding)
    if mouse_button is not None:
        return MouseButtonAction(button=mouse_button, hold_seconds=0.25)
    mapped = GAME_BINDING_KEYS[binding]
    if isinstance(mapped, str):
        return KeyAction(key=mapped)
    return HotkeyAction(keys=list(mapped))


# Bindings that flip state rather than setting it, so a "retry" undoes the
# first press instead of repeating it. These may never be retried.
TOGGLE_GAME_BINDINGS: frozenset[GameBinding] = frozenset(
    {
        GameBinding.TOGGLE_INVENTORY,
        GameBinding.TOGGLE_MAP,
        GameBinding.TOGGLE_STATS,
        GameBinding.TOGGLE_HELP,
        GameBinding.TOGGLE_CRAFTING,
        GameBinding.TOGGLE_RESEARCH,
        GameBinding.PAUSE,
        GameBinding.CHANGE_SQUAD,
        GameBinding.CYCLE_RUN_SPEED,
        GameBinding.MEDIC,
    }
)


TIME_GAME_BINDINGS: frozenset[GameBinding] = frozenset(
    {
        GameBinding.PAUSE,
        GameBinding.SPEED_1,
        GameBinding.SPEED_2,
        GameBinding.SPEED_3,
    }
)
"""Low-level time keys represented to planners by pause/set_speed actions."""


class UseGameBindingAction(StrictModel):
    """Press one named Kenshi control through the shipped-default keymap.

    The agent kept trying to reach screens by hunting for a widget to click -
    clicking the time-speed buttons to unpause, clicking around the world hoping
    an inventory would appear - because nothing in the catalog could simply open
    a screen. Kenshi already binds all of this: `I` opens the inventory, `M` the
    map and `C` the stats window under the shipped defaults. Naming
    the *binding* rather than the key keeps the intention readable and the
    current default mapping in one place; customized keymaps are not yet read.
    The binding enum is the semantic vocabulary, rather than an escape hatch to
    arbitrary keys. Time keys remain controller details behind PauseAction and
    SetSpeedAction.
    """

    kind: Literal["use_game_binding"] = "use_game_binding"
    binding: GameBinding
    # Human-readable intent for logs and uncertain bindings. When the binding
    # has a mechanically derivable transition, the runtime owns its typed
    # completion condition instead of asking the planner to duplicate it.
    expected_effect: str = Field(min_length=1, max_length=200)


ControllerPrimitive: TypeAlias = (
    KeyAction
    | HotkeyAction
    | MouseButtonAction
    | MouseDragAction
    | MoveCursorAction
    | ClickAction
    | ScrollAction
)
"""Deterministic executor/controller implementation details.

These remain the only way input actually reaches Windows, but they are not an
intention a planner may author: a raw coordinate carries no evidence about what
it would activate. The generic live planner surface never advertises them.
"""

PlannerControlAction: TypeAlias = (
    NoopAction
    | StopAction
    | PauseAction
    | SetSpeedAction
    | WaitAction
    | ConsultAdvisorAction
    | RecallMemoryAction
    | ReadFieldbookAction
)
"""Planner-layer intentions that touch no game object and bind to no reference."""

PlannerAtomicSemanticAction: TypeAlias = (
    ApproachDialogueTargetAction
    | CommandWorldTargetAction
    | SelectSquadMemberAction
    | RotateCameraAction
    | MoveToCharacterAction
    | MoveInDirectionAction
    | TravelToMapDestinationAction
    | ExitCurrentBuildingAction
    | ActivateVisibleControlAction
    | DismissScreenAction
    | PurchaseItemAction
    | UseGameBindingAction
    | ScrollScreenAction
    | SellItemAction
    | EquipItemAction
    | RecoverCameraViewAction
)
"""Reusable atomic game/UI intentions either planner mode may author."""

PlannerCompositeSemanticAction: TypeAlias = HarvestResourceAction
"""Executor-owned options that require continuous plan supervision."""

PlannerSemanticAction: TypeAlias = (
    PlannerAtomicSemanticAction | PlannerCompositeSemanticAction
)
"""Every game/UI intention a continuous strategic planner may author."""

InternalSemanticAction: TypeAlias = (
    PerformContextAction
    | ProduceResourceOutputAction
    | OpenContextInventoryAction
    | CollectResourceOutputAction
)
"""Controller-owned phases used only inside larger semantic options."""

SemanticAction: TypeAlias = PlannerSemanticAction | InternalSemanticAction
"""Every typed game/UI intention, including controller-owned phases."""

PlannerAction: TypeAlias = PlannerControlAction | PlannerSemanticAction | SkillAction
"""What a continuous planner may author."""

SingleStepPlannerAction: TypeAlias = (
    PlannerControlAction | PlannerAtomicSemanticAction | SkillAction
)
"""Actions that do not require the continuous executor's option ownership."""

Action: TypeAlias = (
    NoopAction
    | StopAction
    | PauseAction
    | SetSpeedAction
    | WaitAction
    | ConsultAdvisorAction
    | RecallMemoryAction
    | ReadFieldbookAction
    | KeyAction
    | HotkeyAction
    | MouseButtonAction
    | MouseDragAction
    | MoveCursorAction
    | ClickAction
    | ScrollAction
    | SkillAction
    | ApproachDialogueTargetAction
    | CommandWorldTargetAction
    | SelectSquadMemberAction
    | RotateCameraAction
    | PerformContextAction
    | ProduceResourceOutputAction
    | OpenContextInventoryAction
    | HarvestResourceAction
    | MoveToCharacterAction
    | MoveInDirectionAction
    | TravelToMapDestinationAction
    | ExitCurrentBuildingAction
    | ActivateVisibleControlAction
    | DismissScreenAction
    | PurchaseItemAction
    | UseGameBindingAction
    | ScrollScreenAction
    | SellItemAction
    | EquipItemAction
    | CollectResourceOutputAction
    | RecoverCameraViewAction
)
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)

SEMANTIC_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "approach_dialogue_target",
        "command_world_target",
        "select_squad_member",
        "rotate_camera",
        "perform_context_action",
        "produce_resource_output",
        "open_context_inventory",
        "harvest_resource",
        "move_to_character",
        "move_in_direction",
        "travel_to_map_destination",
        "exit_current_building",
        "activate_visible_control",
        "dismiss_screen",
        "purchase_item",
        "use_game_binding",
        "scroll_screen",
        "sell_item",
        "equip_item",
        "collect_resource_output",
        "recover_camera_view",
    }
)
CONTROLLER_PRIMITIVE_KINDS: frozenset[str] = frozenset(
    {
        "key",
        "hotkey",
        "mouse_button",
        "mouse_drag",
        "move_cursor",
        "click",
        "scroll",
    }
)


def is_controller_primitive(action: Action) -> bool:
    return action.kind in CONTROLLER_PRIMITIVE_KINDS


def is_semantic_action(action: Action) -> bool:
    return action.kind in SEMANTIC_ACTION_KINDS


PLANNER_CONTROL_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "noop",
        "stop",
        "pause",
        "set_speed",
        "wait",
        "consult_advisor",
        "recall_memory",
        "read_fieldbook",
    }
)


def is_planner_control_action(action: Action) -> bool:
    """Planner-layer control that touches no game object and binds to no reference."""

    return action.kind in PLANNER_CONTROL_ACTION_KINDS


def new_command_id() -> str:
    return f"cmd-{uuid4().hex}"


def new_memory_id() -> str:
    """A runtime-owned durable identity, stable across processes.

    Not the SQLite rowid: a memory ID is cited by planners and quoted in
    receipts, so it must survive a projection rebuild that renumbers rows.
    """

    return f"mem-{uuid4().hex}"


def new_continuity_receipt_id() -> str:
    """A runtime-owned identity for one attempted continuity operation."""

    return f"cor-{uuid4().hex}"


def new_memory_read_receipt_id() -> str:
    """A runtime-owned identity for one elective continuity read."""

    return f"mrr-{uuid4().hex}"


def new_memory_compaction_candidate_id() -> str:
    """A runtime-owned identity for one inspectable compaction proposal."""

    return f"mcc-{uuid4().hex}"


def new_fieldbook_project_id() -> str:
    return f"fbp-{uuid4().hex}"


def new_fieldbook_entry_id() -> str:
    return f"fbe-{uuid4().hex}"


def new_fieldbook_operation_receipt_id() -> str:
    return f"fbor-{uuid4().hex}"


def new_fieldbook_read_receipt_id() -> str:
    return f"fbr-{uuid4().hex}"


def parse_action(value: Any) -> Action:
    return ACTION_ADAPTER.validate_python(value)


class StateChange(StrictModel):
    """One field of the world that moved since the previous observation.

    Deliberately carries the values and not just the path. "money changed" does
    not tell an agent whether its purchase went through; "money 118 -> 96" does,
    and that is the difference between noticing a failed action and repeating it.
    """

    path: str = Field(min_length=1, max_length=200)
    before: str | None = Field(default=None, max_length=200)
    after: str | None = Field(default=None, max_length=200)


class ActionOutcomeAssessment(StrEnum):
    CHANGED = "changed"
    NO_OP = "no_op"
    NOT_EXECUTED = "not_executed"
    UNKNOWN = "unknown"


ACTION_OUTCOME_ID_PATTERN = r"^ao-[1-9][0-9]{0,8}$"
PLAN_OUTCOME_ID_PATTERN = r"^po-[1-9][0-9]{0,8}$"
PLAN_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,95}$"
STEP_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"


class ActionOutcome(StrictModel):
    """One attempted action and what the world did about it.

    `outcome_id` is runtime-owned. A planner may cite it as evidence for a
    later memory, which is only sound because the planner cannot mint one: an
    outcome exists after its action has already been assessed, so a plan
    physically cannot cite the success of its own future steps.
    """

    outcome_id: str = Field(pattern=ACTION_OUTCOME_ID_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(default=1, ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    command_id: str | None = Field(default=None, max_length=80)
    step_index: int = Field(ge=0)
    intent: str = Field(min_length=1, max_length=1000)
    action: Action
    executed: bool
    receipt_message: str = Field(default="", max_length=2000)
    assessment: ActionOutcomeAssessment
    # These fields survive the rich visible window in `ActionOutcomeDigest`.
    # They distinguish "the screen looked different" from a controller-owned
    # terminal, and preserve the exact target and causal revision basis after
    # the full receipt is evicted.
    causal_revision_advanced: bool | None = None
    controller_verified: bool = False
    semantic_status: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=200)
    feedback: str = Field(min_length=1, max_length=1000)
    started_after_revision: WorldStateRevision | None = None
    completed_at_revision: WorldStateRevision | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    visual_change_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    telemetry_changes: list[str] = Field(default_factory=list, max_length=30)
    selected_character_name: str | None = Field(default=None, max_length=200)
    position_before: Vec3 | None = None
    position_after: Vec3 | None = None


class PlanDisposition(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"
    TERMINATED = "terminated"


class PlanOutcome(StrictModel):
    """Why a plan ended, in terms of what it originally set out to do.

    Without this the next planner reconstructs purpose from "Execute step X",
    which is not a purpose. The objective is copied from the plan that carried
    it, and the reason is the executor's terminal verdict — neither is written
    by a model after the fact.
    """

    plan_outcome_id: str = Field(pattern=PLAN_OUTCOME_ID_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=1000)
    disposition: PlanDisposition
    reason: str = Field(min_length=1, max_length=1000)
    completed_step_ids: list[str] = Field(default_factory=list, max_length=16)
    actions_completed: int = Field(default=0, ge=0)
    terminal_revision: WorldStateRevision | None = None
    started_at: datetime
    finished_at: datetime


class ActionOutcomeDigest(StrictModel):
    """Compact immutable evidence retained for the lifetime of one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_id: str = Field(pattern=ACTION_OUTCOME_ID_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    command_id: str | None = Field(default=None, max_length=80)
    action_kind: str = Field(min_length=1, max_length=80)
    assessment: ActionOutcomeAssessment
    executed: bool
    causal_revision_advanced: bool | None = None
    controller_verified: bool
    semantic_status: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=200)
    started_after_revision: WorldStateRevision | None = None
    completed_at_revision: WorldStateRevision | None = None
    evidence_summary: str = Field(min_length=1, max_length=500)
    recorded_at: datetime


class PlanOutcomeDigest(StrictModel):
    """Compact immutable plan lifecycle retained for the lifetime of one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_outcome_id: str = Field(pattern=PLAN_OUTCOME_ID_PATTERN)
    run_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=1000)
    disposition: PlanDisposition
    reason_digest: str = Field(min_length=1, max_length=1000)
    completed_step_ids: list[str] = Field(default_factory=list, max_length=16)
    actions_completed: int = Field(default=0, ge=0)
    terminal_revision: WorldStateRevision | None = None
    started_at: datetime
    finished_at: datetime


class CurrentObservationEvidence(StrictModel):
    """The exact observation the planner was looking at when it wrote this."""

    source: Literal["current_observation"] = "current_observation"


class ActionOutcomeEvidence(StrictModel):
    source: Literal["action_outcome"] = "action_outcome"
    outcome_id: str = Field(pattern=ACTION_OUTCOME_ID_PATTERN)


class PlanOutcomeEvidence(StrictModel):
    source: Literal["plan_outcome"] = "plan_outcome"
    plan_outcome_id: str = Field(pattern=PLAN_OUTCOME_ID_PATTERN)


MEMORY_ID_PATTERN = r"^mem-[A-Za-z0-9]{1,72}$"


class MemoryEvidence(StrictModel):
    source: Literal["memory"] = "memory"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)


class AdvisorBriefEvidence(StrictModel):
    """Advice, not world evidence. Rendered as such wherever it is stored."""

    source: Literal["advisor_brief"] = "advisor_brief"
    brief_id: str = Field(pattern=r"^advisor-[0-9a-f]{32}$")


EvidenceReference: TypeAlias = (
    CurrentObservationEvidence
    | ActionOutcomeEvidence
    | PlanOutcomeEvidence
    | MemoryEvidence
    | AdvisorBriefEvidence
)
"""Every identity a continuity operation may cite, and nothing else.

Each branch names an authority that already exists at the moment the operation
is processed. There is deliberately no free-text branch: a sentence claiming an
outcome is not the outcome.
"""


class MemoryResolutionDisposition(StrEnum):
    """What resolving an intention or uncertainty actually concluded."""

    COMPLETED = "completed"
    ABANDONED = "abandoned"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class KeepMemoryOperation(StrictModel):
    """Create one durable record from something already established.

    `evidence` is absent on purpose. The stored grounding string is rendered by
    the runtime from `references` after each one resolves, so a record can
    never describe proof it does not have.
    """

    operation: Literal["keep"] = "keep"
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=2000)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    # Opaque identity copied exactly from the current observation. Display
    # names are intentionally insufficient: two Barmen may share one, and a
    # later identity session may reuse the same role for another character.
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)


class ReinforceMemoryOperation(StrictModel):
    """Say an existing record still matters, without writing a second copy."""

    operation: Literal["reinforce"] = "reinforce"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    salience: float | None = Field(default=None, ge=0.0, le=1.0)
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)


class ResolveMemoryOperation(StrictModel):
    """Close an open commitment or question with the evidence that closed it."""

    operation: Literal["resolve"] = "resolve"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    reason: str = Field(min_length=1, max_length=1000)
    disposition: MemoryResolutionDisposition | None = None
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)


class SupersedeMemoryOperation(StrictModel):
    """Replace a record and link the old one to its replacement, atomically."""

    operation: Literal["supersede"] = "supersede"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=2000)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)


class RetractMemoryOperation(StrictModel):
    """Withdraw a record from active recall without deleting its history."""

    operation: Literal["retract"] = "retract"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    reason: str = Field(min_length=1, max_length=1000)


ContinuityOperation: TypeAlias = (
    KeepMemoryOperation
    | ReinforceMemoryOperation
    | ResolveMemoryOperation
    | SupersedeMemoryOperation
    | RetractMemoryOperation
)
"""Every explicit transition a planner may ask for, and nothing else.

There is no edit and no delete. A belief that turns out to be wrong is
superseded or retracted, both of which leave the original readable.
"""


class ContinuityOrigin(StrEnum):
    DECISION = "decision"
    PLAN = "plan"
    PATCH = "patch"


class ContinuityOperationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NO_OP = "no_op"
    FAILED = "failed"


class FieldbookProjectKind(StrEnum):
    DELIVERY_DOCKET = "delivery_docket"
    ROUTE_ATLAS = "route_atlas"
    INCIDENT_LOG = "incident_log"
    VENDOR_LEDGER = "vendor_ledger"
    EQUIPMENT_PLAN = "equipment_plan"
    JOURNAL = "journal"
    GENERIC = "generic"


class FieldbookProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class FieldbookEntryKind(StrEnum):
    NOTE = "note"
    DECISION = "decision"
    OBSERVATION = "observation"
    INCIDENT = "incident"
    MANIFEST = "manifest"
    ROUTE_ENTRY = "route_entry"
    EXPENSE = "expense"
    QUESTION = "question"


class CreateFieldbookProjectOperation(StrictModel):
    operation: Literal["create_project"] = "create_project"
    kind: FieldbookProjectKind
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("title", "summary")
    @classmethod
    def normalize_nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fieldbook text must not be blank")
        return normalized


class AppendFieldbookEntryOperation(StrictModel):
    operation: Literal["append_entry"] = "append_entry"
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    kind: FieldbookEntryKind
    content: str = Field(min_length=1, max_length=2000)
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)

    @field_validator("content")
    @classmethod
    def normalize_nonblank_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fieldbook entry content must not be blank")
        return normalized


class UpdateFieldbookSummaryOperation(StrictModel):
    operation: Literal["update_summary"] = "update_summary"
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("summary")
    @classmethod
    def normalize_nonblank_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fieldbook summary must not be blank")
        return normalized


class SelectFieldbookProjectOperation(StrictModel):
    operation: Literal["select_project"] = "select_project"
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )


class SetFieldbookProjectStatusOperation(StrictModel):
    operation: Literal["set_project_status"] = "set_project_status"
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    status: FieldbookProjectStatus


FieldbookOperation: TypeAlias = (
    CreateFieldbookProjectOperation
    | AppendFieldbookEntryOperation
    | UpdateFieldbookSummaryOperation
    | SelectFieldbookProjectOperation
    | SetFieldbookProjectStatusOperation
)


class MemoryStatus(StrEnum):
    """Where a record sits in its lifecycle. Only `active` reaches recall."""

    ACTIVE = "active"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class EvidenceAuthority(StrEnum):
    """What one resolved identity is actually capable of establishing."""

    FRESH_WORLD_OBSERVATION = "fresh_world_observation"
    VERIFIED_WORLD_EFFECT = "verified_world_effect"
    OBSERVED_CHANGE = "observed_change"
    ATTEMPT_CHANGED = "attempt_changed"
    ATTEMPT_NO_OP = "attempt_no_op"
    ATTEMPT_NOT_EXECUTED = "attempt_not_executed"
    ATTEMPT_UNKNOWN = "attempt_unknown"
    PLAN_DISPOSITION = "plan_disposition"
    AGENT_BELIEF = "agent_belief"
    ADVICE = "advice"
    SCENARIO_ATTESTATION = "scenario_attestation"


class ResolvedEvidenceSnapshot(StrictModel):
    """Typed immutable truth retained after a reference leaves planner context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal[
        "current_observation",
        "action_outcome",
        "plan_outcome",
        "memory",
        "advisor_brief",
    ]
    source_id: str = Field(min_length=1, max_length=200)
    authority: EvidenceAuthority
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    run_id: str = Field(min_length=1, max_length=200)
    world_revision: WorldStateRevision | None = None
    assessment: ActionOutcomeAssessment | None = None
    action_kind: str | None = Field(default=None, max_length=80)
    executed: bool | None = None
    causal_revision_advanced: bool | None = None
    controller_verified: bool | None = None
    semantic_status: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=200)
    plan_disposition: PlanDisposition | None = None
    memory_kind: MemoryKind | None = None
    memory_status: MemoryStatus | None = None
    compact_summary: str = Field(min_length=1, max_length=500)


class CanonicalMemoryProvenance(StrictModel):
    """The exact accepted lifecycle operation and the authority behind it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation: ContinuityOperation
    origin: ContinuityOrigin
    run_id: str = Field(min_length=1, max_length=200)
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)
    resolved_evidence: list[ResolvedEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=4,
    )
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    rendered_grounding: str | None = Field(default=None, max_length=1000)
    transition_result: Literal["applied"] = "applied"


class CanonicalFieldbookProvenance(StrictModel):
    """Exact planner context and resolved sources behind a fieldbook change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    operation: FieldbookOperation
    origin: ContinuityOrigin
    run_id: str = Field(min_length=1, max_length=200)
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    references: list[EvidenceReference] = Field(default_factory=list, max_length=4)
    resolved_evidence: list[ResolvedEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=4,
    )
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    rendered_grounding: str | None = Field(default=None, max_length=1000)
    transition_result: Literal["applied"] = "applied"


class FieldbookLifecycleEvent(StrEnum):
    CREATE_PROJECT = "create_project"
    APPEND_ENTRY = "append_entry"
    UPDATE_SUMMARY = "update_summary"
    SELECT_PROJECT = "select_project"
    CLEAR_SELECTION = "clear_selection"
    SET_PROJECT_STATUS = "set_project_status"


class FieldbookProject(StrictModel):
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    campaign_id: str = Field(min_length=1, max_length=80)
    kind: FieldbookProjectKind
    status: FieldbookProjectStatus
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1000)
    selected: bool = False
    entry_count: int = Field(default=0, ge=0)
    created_run_id: str = Field(min_length=1, max_length=200)
    created_at: datetime
    updated_at: datetime
    latest_provenance: CanonicalFieldbookProvenance | None = None


class FieldbookProjectIndex(StrictModel):
    """Bounded metadata automatically shown without full project entries."""

    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    kind: FieldbookProjectKind
    status: FieldbookProjectStatus
    short_summary: str = Field(min_length=1, max_length=160)
    entry_count: int = Field(ge=0)
    updated_at: datetime
    selected: bool


class ActiveFieldbookProject(StrictModel):
    """The one explicitly selected project allowed a fuller automatic summary."""

    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    kind: FieldbookProjectKind
    status: Literal[FieldbookProjectStatus.ACTIVE]
    summary: str = Field(min_length=1, max_length=1000)
    entry_count: int = Field(ge=0)
    updated_at: datetime


class FieldbookEntry(StrictModel):
    entry_id: str = Field(pattern=FIELD_BOOK_ENTRY_ID_PATTERN)
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    campaign_id: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=1)
    kind: FieldbookEntryKind
    content: str = Field(min_length=1, max_length=2000)
    created_run_id: str = Field(min_length=1, max_length=200)
    created_at: datetime
    provenance: CanonicalFieldbookProvenance | None = None


class FieldbookHistoryEntry(StrictModel):
    event_id: int = Field(ge=1)
    campaign_id: str
    project_id: str = Field(pattern=FIELD_BOOK_PROJECT_ID_PATTERN)
    entry_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_ENTRY_ID_PATTERN,
    )
    event: FieldbookLifecycleEvent
    run_id: str
    recorded_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class FieldbookReadResult(StrictModel):
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )
    query: str | None = Field(default=None, min_length=1, max_length=200)
    project: FieldbookProject | None = None
    entries: list[FieldbookEntry] = Field(default_factory=list, max_length=8)
    matched: int = Field(default=0, ge=0)
    truncated: bool = False
    reason: str = Field(default="", max_length=600)


class FieldbookReadStatus(StrEnum):
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class FieldbookReadReceipt(FieldbookReadResult):
    receipt_id: str = Field(pattern=r"^fbr-[0-9a-f]{32}$")
    status: FieldbookReadStatus
    campaign_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,79}$",
    )
    project_ids: list[str] = Field(default_factory=list, max_length=8)
    entry_ids: list[str] = Field(default_factory=list, max_length=8)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def result_ids_and_status_match(self) -> FieldbookReadReceipt:
        expected_projects = sorted(
            {
                *(
                    [self.project.project_id]
                    if self.project is not None
                    else []
                ),
                *(entry.project_id for entry in self.entries),
            }
        )
        if self.project_ids != expected_projects:
            raise ValueError("project_ids must exactly match returned fieldbook data")
        expected_entries = [entry.entry_id for entry in self.entries]
        if self.entry_ids != expected_entries:
            raise ValueError("entry_ids must exactly match returned fieldbook entries")
        unavailable_is_valid = (
            self.status is FieldbookReadStatus.UNAVAILABLE
            and self.campaign_id is None
        )
        available_is_valid = (
            self.status
            in {FieldbookReadStatus.COMPLETED, FieldbookReadStatus.FAILED}
            and self.campaign_id is not None
        )
        if not (unavailable_is_valid or available_is_valid):
            raise ValueError("status and campaign_id describe an impossible fieldbook read")
        return self


class FieldbookReceiptDigest(StrictModel):
    receipt_id: str = Field(pattern=r"^fbor-[0-9a-f]{32}$")
    origin: ContinuityOrigin
    operation: Literal[
        "create_project",
        "append_entry",
        "update_summary",
        "select_project",
        "set_project_status",
    ]
    status: ContinuityOperationStatus
    reason: str = Field(min_length=1, max_length=1000)
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )
    entry_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_ENTRY_ID_PATTERN,
    )
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    writes_degraded: bool = False
    recorded_at: datetime


class FieldbookOperationReceipt(StrictModel):
    receipt_id: str = Field(pattern=r"^fbor-[0-9a-f]{32}$")
    origin: ContinuityOrigin
    status: ContinuityOperationStatus
    operation: FieldbookOperation
    reason: str = Field(min_length=1, max_length=1000)
    project_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_PROJECT_ID_PATTERN,
    )
    entry_id: str | None = Field(
        default=None,
        pattern=FIELD_BOOK_ENTRY_ID_PATTERN,
    )
    resolved_evidence: list[ResolvedEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=4,
    )
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    writes_degraded: bool = False
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def digest(self) -> FieldbookReceiptDigest:
        return FieldbookReceiptDigest(
            receipt_id=self.receipt_id,
            origin=self.origin,
            operation=self.operation.operation,
            status=self.status,
            reason=self.reason,
            project_id=self.project_id,
            entry_id=self.entry_id,
            authored_context_id=self.authored_context_id,
            authored_revision=self.authored_revision,
            commit_revision=self.commit_revision,
            plan_id=self.plan_id,
            plan_version=self.plan_version,
            step_id=self.step_id,
            writes_degraded=self.writes_degraded,
            recorded_at=self.recorded_at,
        )


class ContinuityReceiptDigest(StrictModel):
    """Bounded planner feedback for one full continuity operation receipt."""

    receipt_id: str = Field(pattern=r"^cor-[0-9a-f]{32}$")
    origin: ContinuityOrigin
    operation: Literal["keep", "reinforce", "resolve", "supersede", "retract"]
    status: ContinuityOperationStatus
    reason: str = Field(min_length=1, max_length=1000)
    memory_id: str | None = Field(default=None, pattern=MEMORY_ID_PATTERN)
    memory_status: MemoryStatus | None = None
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    evidence_summary: str | None = Field(default=None, max_length=500)
    writes_degraded: bool = False
    recorded_at: datetime


class ContinuityOperationReceipt(StrictModel):
    receipt_id: str = Field(pattern=r"^cor-[0-9a-f]{32}$")
    origin: ContinuityOrigin
    status: ContinuityOperationStatus
    operation: ContinuityOperation
    reason: str = Field(min_length=1, max_length=1000)
    memory_id: str | None = Field(default=None, pattern=MEMORY_ID_PATTERN)
    memory_status: MemoryStatus | None = None
    evidence: str | None = Field(default=None, max_length=1000)
    resolved_evidence: list[ResolvedEvidenceSnapshot] = Field(
        default_factory=list,
        max_length=4,
    )
    plan_id: str | None = Field(default=None, pattern=PLAN_ID_PATTERN)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    authored_context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    authored_revision: WorldStateRevision
    commit_revision: WorldStateRevision
    writes_degraded: bool = False
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def digest(self) -> ContinuityReceiptDigest:
        return ContinuityReceiptDigest(
            receipt_id=self.receipt_id,
            origin=self.origin,
            operation=self.operation.operation,
            status=self.status,
            reason=self.reason,
            memory_id=self.memory_id,
            memory_status=self.memory_status,
            authored_context_id=self.authored_context_id,
            authored_revision=self.authored_revision,
            commit_revision=self.commit_revision,
            plan_id=self.plan_id,
            plan_version=self.plan_version,
            step_id=self.step_id,
            evidence_summary=(
                None if self.evidence is None else self.evidence[:500]
            ),
            writes_degraded=self.writes_degraded,
            recorded_at=self.recorded_at,
        )


class MemoryAuthorship(StrEnum):
    """Who stands behind a record, and how much that is worth.

    `legacy_unverified` marks rows written before continuity had grounding at
    all. They are kept because they are real user data, not because anything
    checked them.
    """

    AGENT_AUTHORED = "agent_authored"
    LEGACY_UNVERIFIED = "legacy_unverified"


class CompactionMethod(StrEnum):
    """Implemented compaction treatments.

    Semantic rewriting is deliberately absent until it can satisfy the same
    source-conservation and atomic-application contract as the lossless path.
    """

    LOSSLESS = "lossless"


class MemoryCompactionGenerator(StrictModel):
    """How one candidate was produced, including honest non-use of a prompt."""

    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=120)
    prompt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=16)


class MemoryCompactionCandidate(StrictModel):
    """A bounded proposal that has no authority until atomically applied."""

    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=r"^mcc-[0-9a-f]{32}$")
    method: CompactionMethod
    campaign_id: str = Field(min_length=1, max_length=80)
    source_memory_ids: list[str] = Field(min_length=2, max_length=8)
    source_fingerprints: dict[str, str] = Field(min_length=2, max_length=8)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=2000)
    salience: float = Field(ge=0.0, le=1.0)
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    authorship: MemoryAuthorship
    generator: MemoryCompactionGenerator
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def model_post_init(self, __context: object) -> None:
        # Local import avoids a model/compaction module cycle. The executable
        # authority rule lives in the undecorated, mutation-tested compaction
        # seam; this data model merely invokes it after parsing.
        from .memory_compaction import validate_compaction_source_identity

        validate_compaction_source_identity(
            self.source_memory_ids,
            self.source_fingerprints,
        )


class CanonicalCompactionProvenance(StrictModel):
    """Exact immutable candidate and application identity behind a replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    provenance_kind: Literal["compaction"] = "compaction"
    candidate: MemoryCompactionCandidate
    applied_run_id: str = Field(min_length=1, max_length=200)
    replacement_memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    applied_at: datetime
    transition_result: Literal["applied"] = "applied"


MemoryProvenance: TypeAlias = (
    CanonicalMemoryProvenance | CanonicalCompactionProvenance
)


class MemoryLifecycleEvent(StrEnum):
    KEEP = "keep"
    REINFORCE = "reinforce"
    RESOLVE = "resolve"
    SUPERSEDE = "supersede"
    RETRACT = "retract"
    DELIVER = "deliver"


class MemoryRecord(StrictModel):
    """One durable record, projected from its lifecycle history."""

    memory_id: str = Field(min_length=1, max_length=80)
    campaign_id: str = Field(min_length=1, max_length=80)
    kind: MemoryKind
    status: MemoryStatus
    content: str
    salience: float
    # Runtime-rendered from the references that resolved. Never model-authored.
    grounding: str | None = None
    # Exact accepted operation and typed source snapshots behind the latest
    # grounding-bearing transition. Older provenance remains in event history.
    latest_provenance: MemoryProvenance | None = None
    authorship: MemoryAuthorship = MemoryAuthorship.AGENT_AUTHORED
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    created_run_id: str
    created_at: datetime
    # Four separate concepts, deliberately not one "touched at". Being read is
    # not being reinforced, and being reinforced is not being resolved.
    reinforced_at: datetime | None = None
    resolved_at: datetime | None = None
    superseded_at: datetime | None = None
    last_delivered_at: datetime | None = None
    reinforcement_count: int = Field(default=0, ge=0)
    supersedes_id: str | None = Field(default=None, min_length=1, max_length=80)
    superseded_by_id: str | None = Field(default=None, min_length=1, max_length=80)
    resolution_reason: str | None = Field(default=None, max_length=1000)
    resolution_disposition: MemoryResolutionDisposition | None = None


class RecallTier(StrEnum):
    """Why a record was chosen, in the order the tiers are spent.

    The order is the policy: a plan cannot safely proceed without its open
    commitments or what it knows about the entity in front of it, so those are
    not allowed to compete with general knowledge for the same slots.
    """

    COMMITMENT = "commitment"
    CURRENT_TARGET = "current_target"
    OPEN_HYPOTHESIS = "open_hypothesis"
    GENERAL = "general"


class MemoryRetrievalPolicy(StrEnum):
    """Canonical-memory retrieval treatments implemented by this build."""

    DETERMINISTIC = "deterministic"


class RecallSummary(StrictModel):
    """What automatic recall left out, stated rather than implied.

    A planner that cannot tell "nothing else exists" from "more exists, not
    shown" will conclude the first and stop looking.
    """

    omitted: dict[RecallTier, int] = Field(default_factory=dict)
    total_omitted: int = Field(default=0, ge=0)

    @property
    def complete(self) -> bool:
        return self.total_omitted == 0


class MemorySearchResult(StrictModel):
    """The typed answer to one deliberate, bounded continuity read."""

    query: str = Field(min_length=1, max_length=200)
    records: list[MemoryRecord] = Field(default_factory=list, max_length=16)
    action_outcomes: list[ActionOutcomeDigest] = Field(
        default_factory=list,
        max_length=8,
    )
    plan_outcomes: list[PlanOutcomeDigest] = Field(
        default_factory=list,
        max_length=8,
    )
    matched: int = Field(default=0, ge=0)
    truncated: bool = False
    reason: str = Field(default="", max_length=600)


class MemoryReadStatus(StrEnum):
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class MemoryReadReceipt(MemorySearchResult):
    """Runtime identity and provenance for one planner-requested memory read."""

    receipt_id: str = Field(pattern=r"^mrr-[0-9a-f]{32}$")
    source: Literal["durable_memory", "working_outcomes"]
    status: MemoryReadStatus
    campaign_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,79}$",
    )
    record_ids: list[str] = Field(default_factory=list, max_length=8)
    action_outcome_ids: list[str] = Field(default_factory=list, max_length=8)
    plan_outcome_ids: list[str] = Field(default_factory=list, max_length=8)
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    plan_version: int = Field(ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def result_ids_match_returned_records(self) -> MemoryReadReceipt:
        expected_record_ids = [record.memory_id for record in self.records]
        if self.record_ids != expected_record_ids:
            raise ValueError("record_ids must exactly match returned records")
        expected_action_ids = [
            outcome.outcome_id for outcome in self.action_outcomes
        ]
        if self.action_outcome_ids != expected_action_ids:
            raise ValueError(
                "action_outcome_ids must exactly match returned action outcomes"
            )
        expected_plan_ids = [
            outcome.plan_outcome_id for outcome in self.plan_outcomes
        ]
        if self.plan_outcome_ids != expected_plan_ids:
            raise ValueError(
                "plan_outcome_ids must exactly match returned plan outcomes"
            )
        working_scope_is_valid = (
            self.source == "working_outcomes"
            and self.status is MemoryReadStatus.COMPLETED
            and self.campaign_id is None
        )
        durable_scope_is_valid = (
            self.source == "durable_memory"
            and (
                (
                    self.status is MemoryReadStatus.UNAVAILABLE
                    and self.campaign_id is None
                )
                or (
                    self.status
                    in {MemoryReadStatus.COMPLETED, MemoryReadStatus.FAILED}
                    and self.campaign_id is not None
                )
            )
        )
        if not (working_scope_is_valid or durable_scope_is_valid):
            raise ValueError(
                "source, status, and campaign_id describe an impossible read"
            )
        return self


class MemoryHistoryEntry(StrictModel):
    """One append-only lifecycle event. Never rewritten, never deleted."""

    event_id: int
    campaign_id: str
    memory_id: str
    event: MemoryLifecycleEvent
    run_id: str
    recorded_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class SkillSpec(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    arguments: dict[str, str] = Field(default_factory=dict)
    visual_precondition: str | None = Field(default=None, max_length=1000)
    normalized_pointer_bounds: NormalizedPointerBounds | None = None
    movement_pulse_seconds: float | None = Field(default=None, gt=0.0, le=10.0)
    movement_pulse_min_seconds: float | None = Field(default=None, gt=0.0, le=10.0)
    movement_pulse_max_seconds: float | None = Field(default=None, gt=0.0, le=10.0)
    requires_native_assisted: bool = False


class WorldStateRevision(StrictModel):
    telemetry_sequence: int | None = Field(default=None, ge=0)
    frame_sequence: int | None = Field(default=None, ge=0)
    capability_epoch: int = Field(default=0, ge=0)
    observed_at_monotonic: float = Field(default_factory=monotonic, ge=0.0)

    def same_snapshot_as(self, other: WorldStateRevision) -> bool:
        return (
            self.telemetry_sequence == other.telemetry_sequence
            and self.frame_sequence == other.frame_sequence
            and self.capability_epoch == other.capability_epoch
        )

    def same_telemetry_snapshot_as(self, other: WorldStateRevision) -> bool:
        """Compare the exact native-control basis without requiring a capture."""

        return bool(
            self.telemetry_sequence is not None
            and self.telemetry_sequence == other.telemetry_sequence
            and self.capability_epoch == other.capability_epoch
        )

    def is_later_than(self, other: WorldStateRevision) -> bool:
        telemetry_regressed = (
            self.telemetry_sequence is not None
            and other.telemetry_sequence is not None
            and self.telemetry_sequence < other.telemetry_sequence
        )
        frame_regressed = (
            self.frame_sequence is not None
            and other.frame_sequence is not None
            and self.frame_sequence < other.frame_sequence
        )
        capability_regressed = self.capability_epoch < other.capability_epoch
        telemetry_advanced = (
            self.telemetry_sequence is not None
            and other.telemetry_sequence is not None
            and self.telemetry_sequence > other.telemetry_sequence
        )
        frame_advanced = (
            self.frame_sequence is not None
            and other.frame_sequence is not None
            and self.frame_sequence > other.frame_sequence
        )
        capability_advanced = self.capability_epoch > other.capability_epoch
        return bool(
            not telemetry_regressed
            and not frame_regressed
            and not capability_regressed
            and (telemetry_advanced or frame_advanced or capability_advanced)
            and self.observed_at_monotonic >= other.observed_at_monotonic
        )


class PlannerContextManifest(StrictModel):
    """Exact runtime-owned identities present in one final planner input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_id: str = Field(pattern=r"^pc-[1-9][0-9]{0,8}$")
    run_id: str = Field(min_length=1, max_length=200)
    authored_revision: WorldStateRevision
    current_observation_delivered: bool
    telemetry_was_fresh: bool
    input_kind: Literal["full_observation", "budgeted_json", "scripted"]
    current_target_ids: list[str] = Field(default_factory=list, max_length=512)
    action_outcome_ids: list[str] = Field(default_factory=list, max_length=100)
    plan_outcome_ids: list[str] = Field(default_factory=list, max_length=8)
    memory_ids: list[str] = Field(default_factory=list, max_length=128)
    continuity_receipt_ids: list[str] = Field(default_factory=list, max_length=8)
    memory_read_receipt_ids: list[str] = Field(default_factory=list, max_length=8)
    fieldbook_project_ids: list[str] = Field(default_factory=list, max_length=32)
    fieldbook_entry_ids: list[str] = Field(default_factory=list, max_length=8)
    fieldbook_receipt_ids: list[str] = Field(default_factory=list, max_length=8)
    fieldbook_read_receipt_ids: list[str] = Field(default_factory=list, max_length=8)
    advisor_brief_ids: list[str] = Field(default_factory=list, max_length=8)
    candidate_memory_count: int = Field(default=0, ge=0)
    payload_characters: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class AuthoredPlannerContext:
    """The immutable observation and manifest paired with one planner call."""

    manifest: PlannerContextManifest
    observation: Observation


class AdvisorConsultStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    DISABLED = "disabled"
    COOLDOWN = "cooldown"
    UNCHANGED_STATE = "unchanged_state"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class AdvisorAttribution(StrictModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    title: str = Field(min_length=1, max_length=300)
    creator: str | None = Field(default=None, max_length=200)
    url: str = Field(min_length=1, max_length=1000)


class AdvisorRecommendation(StrictModel):
    rank: int = Field(ge=1, le=5)
    goal: str = Field(min_length=1, max_length=500)
    why_now: str = Field(min_length=1, max_length=800)
    prerequisites: list[str] = Field(default_factory=list, max_length=6)
    cautions: list[str] = Field(default_factory=list, max_length=6)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class AdvisorBrief(StrictModel):
    brief_id: str = Field(pattern=r"^advisor-[0-9a-f]{32}$")
    question: str = Field(min_length=1, max_length=600)
    focus: AdvisorFocus
    based_on_revision: WorldStateRevision
    summary: str = Field(min_length=1, max_length=1200)
    recommendations: list[AdvisorRecommendation] = Field(min_length=1, max_length=4)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
    sources: list[AdvisorAttribution] = Field(min_length=1, max_length=12)
    corpus_version: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=200)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AdvisorAvailability(StrictModel):
    enabled: bool = False
    may_request: bool = False
    suggested: bool = False
    request_pending: bool = False
    reason: str = Field(default="The strategic advisor is disabled.", max_length=600)
    calls_used: int = Field(default=0, ge=0)
    max_calls: int = Field(default=0, ge=0)
    cooldown_steps_remaining: int = Field(default=0, ge=0)
    corpus_version: str | None = Field(default=None, max_length=80)
    latest_brief: AdvisorBrief | None = None


class AdvisorConsultEvidence(StrictModel):
    status: AdvisorConsultStatus
    reason: str = Field(min_length=1, max_length=1000)
    calls_used: int = Field(ge=0)
    max_calls: int = Field(ge=0)
    state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief: AdvisorBrief | None = None


class CommandDispatchContext(StrictModel):
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    based_on_revision: WorldStateRevision


class NativeCommandRequest(StrictModel):
    schema_version: Literal["1.1"]
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    command: Literal[
        "approach_confirmed_vendor",
        "move_to_character",
        "move_in_direction",
        "travel_to_map_destination",
        "exit_current_building",
        "operate_natural_resource",
        "produce_resource_output",
        "open_context_inventory",
    ]
    control_mode: Literal[ControlMode.NATIVE_ASSISTED]
    identity_session_id: str = Field(min_length=1, max_length=200)
    based_on_revision: WorldStateRevision
    selected_character_ids: list[str] = Field(min_length=1, max_length=1)
    # Empty for a directional walk, which references nobody.
    target_id: str = Field(default="", max_length=200)
    bearing_degrees: float = Field(default=0.0, ge=0.0, lt=360.0)
    distance_units: float = Field(default=0.0, ge=0.0, le=2000.0)
    minimum_output_quantity: int = Field(default=1, ge=1, le=5)

    @model_validator(mode="after")
    def validate_native_fences(self) -> NativeCommandRequest:
        if self.based_on_revision.telemetry_sequence is None:
            raise ValueError("native command basis requires a telemetry sequence")
        if len(set(self.selected_character_ids)) != 1:
            raise ValueError("native command requires exactly one selected character")
        if self.command == "move_in_direction":
            if self.target_id:
                raise ValueError("a directional walk must not name a target")
            if self.distance_units <= 0.0:
                raise ValueError("a directional walk requires a distance to walk")
        elif self.command == "exit_current_building":
            if self.target_id:
                raise ValueError("a building-exit command must not name a target")
            if self.bearing_degrees != 0.0 or self.distance_units != 0.0:
                raise ValueError("a building-exit command must not carry direction fields")
        else:
            if not self.target_id:
                raise ValueError("this native command requires a target")
            if self.bearing_degrees != 0.0 or self.distance_units != 0.0:
                raise ValueError("a targeted native command must not carry direction fields")
        if self.command != "produce_resource_output" and self.minimum_output_quantity != 1:
            raise ValueError(
                "only resource production may request a larger output quantity"
            )
        return self


ExpectedConditionScalar: TypeAlias = str | int | float | bool
ConditionScalar: TypeAlias = ExpectedConditionScalar | None


class FieldConditionPath(StrEnum):
    CONTROL_MODE = "control_mode"
    TELEMETRY_STALE = "telemetry_stale"
    TELEMETRY_IDENTITY_SESSION_ID = "telemetry.identity_session_id"
    TELEMETRY_GAME_LOADED = "telemetry.game.loaded"
    TELEMETRY_GAME_PAUSED = "telemetry.game.paused"
    TELEMETRY_GAME_SPEED_MULTIPLIER = "telemetry.game.speed_multiplier"
    TELEMETRY_GAME_ELAPSED_MINUTES = "telemetry.game.elapsed_minutes"
    TELEMETRY_GAME_MONEY = "telemetry.game.money"
    SELECTED_INDOORS = "selected.indoors"
    TELEMETRY_GAME_LOCATION_NAME = "telemetry.game.location_name"
    TELEMETRY_GAME_DAY = "telemetry.game.day"
    TELEMETRY_GAME_HOUR = "telemetry.game.hour"
    TELEMETRY_GAME_MINUTE = "telemetry.game.minute"
    TELEMETRY_UI_ACTIVE_SCREEN = "telemetry.ui.active_screen"
    TELEMETRY_UI_MODAL_OPEN = "telemetry.ui.modal_open"
    TELEMETRY_UI_DIALOGUE_OPEN = "telemetry.ui.dialogue_open"
    TELEMETRY_UI_DIALOGUE_TARGET_ID = "telemetry.ui.dialogue_target_id"
    TELEMETRY_UI_DIALOGUE_OPTION_COUNT = "telemetry.ui.dialogue_option_count"
    TELEMETRY_UI_DIALOGUE_OPTION_0 = "telemetry.ui.dialogue_option_0"
    TELEMETRY_UI_VISIBLE_CONTROL_COUNT = "telemetry.ui.visible_control_count"
    TELEMETRY_UI_STATS_WINDOW_OPEN = "telemetry.ui.stats_window_open"
    TELEMETRY_UI_OPEN_INVENTORY_WINDOWS = "telemetry.ui.open_inventory_windows"
    TELEMETRY_UI_MANAGEMENT_SCREEN_OPEN = "telemetry.ui.management_screen_open"
    TELEMETRY_UI_MANAGEMENT_TAB = "telemetry.ui.management_tab"
    TELEMETRY_UI_TOOLTIP_VISIBLE = "telemetry.ui.tooltip_visible"
    TELEMETRY_UI_TOOLTIP_TEXT = "telemetry.ui.tooltip_text"
    TELEMETRY_UI_CONTEXT_MENU_OPEN = "telemetry.ui.context_menu_open"
    TELEMETRY_UI_SELECTED_CHARACTER_ID = "telemetry.ui.selected_character_id"
    TELEMETRY_UI_SELECTED_CHARACTER_COUNT = "telemetry.ui.selected_character_count"
    TELEMETRY_ACTIVE_SHOP_TRADER_COUNT = "telemetry.active_shop_trader_count"
    TELEMETRY_NATIVE_CONTROL_AVAILABLE = "telemetry.native_control.available"
    TELEMETRY_NATIVE_CONTROL_COMMAND_ACTIVE = "telemetry.native_control.command_active"
    TELEMETRY_NATIVE_CONTROL_LAST_COMMAND_SEQUENCE = (
        "telemetry.native_control.last_command_sequence"
    )
    TELEMETRY_NATIVE_CONTROL_LAST_COMMAND = "telemetry.native_control.last_command"
    TELEMETRY_NATIVE_CONTROL_LAST_RESULT = "telemetry.native_control.last_result"
    TELEMETRY_NATIVE_CONTROL_LAST_TARGET = "telemetry.native_control.last_target"
    TELEMETRY_NATIVE_CONTROL_LAST_TARGET_ID = "telemetry.native_control.last_target_id"
    SELECTED_ALIVE = "selected.alive"
    SELECTED_CONSCIOUS = "selected.conscious"
    SELECTED_DOWN = "selected.down"
    SELECTED_IN_COMBAT = "selected.in_combat"
    SELECTED_POSITION_X = "selected.position.x"
    SELECTED_POSITION_Y = "selected.position.y"
    SELECTED_POSITION_Z = "selected.position.z"
    SELECTED_MOVEMENT_SPEED = "selected.movement_speed"
    SELECTED_HUNGER = "selected.hunger"
    SELECTED_BLEEDING_RATE = "selected.bleeding_rate"
    SELECTED_FOOD_ITEMS = "selected.food_items"
    SELECTED_FIRST_AID_KITS = "selected.first_aid_kits"
    SELECTED_CURRENT_GOAL = "selected.current_goal"
    TARGET_DISPOSITION = "target.disposition"
    TARGET_DISTANCE = "target.distance"
    TARGET_VISIBLE = "target.visible"
    TARGET_CONSCIOUS = "target.conscious"
    TARGET_HAS_VENDOR_LIST = "target.has_vendor_list"
    TARGET_IS_SQUAD_LEADER = "target.is_squad_leader"
    TARGET_HAS_DIALOGUE = "target.has_dialogue"
    TARGET_SHOP_INVENTORY_OWNER = "target.shop_inventory_owner"


# Kept as the Python import name used by existing deterministic planners. The
# hosted schema names the narrower vocabulary honestly as FieldConditionPath.
ConditionPath = FieldConditionPath
_ALLOWED_CONDITION_PATHS = frozenset(path.value for path in FieldConditionPath)

GAME_BINDING_VERIFICATION_PATHS: dict[GameBinding, FieldConditionPath] = {
    GameBinding.QUICKLOAD: FieldConditionPath.TELEMETRY_IDENTITY_SESSION_ID,
    GameBinding.TOGGLE_INVENTORY: FieldConditionPath.TELEMETRY_UI_OPEN_INVENTORY_WINDOWS,
    GameBinding.TOGGLE_MAP: FieldConditionPath.TELEMETRY_UI_MANAGEMENT_SCREEN_OPEN,
    GameBinding.TOGGLE_STATS: FieldConditionPath.TELEMETRY_UI_STATS_WINDOW_OPEN,
}


def _is_field_condition_path(value: object) -> bool:
    return isinstance(value, str) and value in _ALLOWED_CONDITION_PATHS


class _ConditionBase(StrictModel):
    operator: ConditionOperator
    expected: ExpectedConditionScalar
    max_age_seconds: float = Field(gt=0.0, le=300.0)
    required_capabilities: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Additional capability names copied exactly from the current observation. "
            "Field paths do not belong here."
        ),
    )

    @model_validator(mode="after")
    def validate_common_shape(self) -> _ConditionBase:
        if any(_is_field_condition_path(name) for name in self.required_capabilities):
            raise ValueError(
                "required_capabilities accepts capability names, not field paths"
            )
        if self.operator == ConditionOperator.CONTAINS and not isinstance(self.expected, str):
            raise ValueError("contains conditions require a string expected value")
        return self


class FieldCondition(_ConditionBase):
    kind: Literal[ConditionKind.FIELD] = ConditionKind.FIELD
    path: FieldConditionPath
    target_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_target_shape(self) -> FieldCondition:
        if self.path.startswith("target.") and not self.target_id:
            raise ValueError("target.* conditions require target_id")
        if not self.path.startswith("target.") and self.target_id is not None:
            object.__setattr__(self, "target_id", None)
        return self


class CapabilityCondition(_ConditionBase):
    kind: Literal[ConditionKind.CAPABILITY] = ConditionKind.CAPABILITY
    path: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "One capability name copied exactly from the current observation's "
            "telemetry.capabilities list; never a telemetry, selected, or target field path."
        ),
    )
    operator: Literal[ConditionOperator.EQUALS] = ConditionOperator.EQUALS
    expected: Literal[True] = True

    @model_validator(mode="after")
    def validate_capability_path(self) -> CapabilityCondition:
        if _is_field_condition_path(self.path):
            raise ValueError(
                f"Capability conditions require a capability name, not field path {self.path!r}"
            )
        return self


class TelemetryFreshCondition(StrictModel):
    kind: Literal[ConditionKind.TELEMETRY_FRESH] = ConditionKind.TELEMETRY_FRESH
    operator: Literal[ConditionOperator.EQUALS] = ConditionOperator.EQUALS
    expected: Literal[True] = True
    max_age_seconds: float = Field(gt=0.0, le=300.0)


ConditionValue: TypeAlias = FieldCondition | CapabilityCondition | TelemetryFreshCondition


class Condition(RootModel[ConditionValue]):
    """One schema branch whose fields can only express that condition's meaning."""

    def __init__(self, **data: Any) -> None:
        payload: Any = data
        super().__init__(root=payload)

    @model_validator(mode="before")
    @classmethod
    def normalize_unambiguous_model_noise(cls, value: Any) -> Any:
        if isinstance(value, Condition):
            return value.root
        if isinstance(value, (FieldCondition, CapabilityCondition, TelemetryFreshCondition)):
            return value
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if set(data) == {"root"}:
            nested = data["root"]
            if not isinstance(nested, Mapping):
                return nested
            data = dict(nested)

        kind = data.get("kind")
        required = [
            item
            for item in data.get("required_capabilities", [])
            if isinstance(item, str) and not _is_field_condition_path(item)
        ]

        if kind == ConditionKind.TELEMETRY_FRESH:
            return {
                "kind": ConditionKind.TELEMETRY_FRESH,
                "operator": ConditionOperator.EQUALS,
                "expected": True,
                "max_age_seconds": data.get("max_age_seconds"),
            }

        path = data.get("path")
        if kind == ConditionKind.CAPABILITY:
            if path is None or _is_field_condition_path(path):
                if required:
                    path = required[0]
            return {
                "kind": ConditionKind.CAPABILITY,
                "path": path,
                "operator": ConditionOperator.EQUALS,
                "expected": True,
                "max_age_seconds": data.get("max_age_seconds"),
                "required_capabilities": required,
            }

        if kind == ConditionKind.FIELD and not _is_field_condition_path(path):
            return {
                "kind": ConditionKind.CAPABILITY,
                "path": path,
                "operator": ConditionOperator.EQUALS,
                "expected": True,
                "max_age_seconds": data.get("max_age_seconds"),
                "required_capabilities": required,
            }

        if kind == ConditionKind.FIELD:
            data["required_capabilities"] = required
        return data

    @property
    def kind(self) -> ConditionKind:
        return self.root.kind

    @property
    def path(self) -> FieldConditionPath | str | None:
        return getattr(self.root, "path", None)

    @property
    def operator(self) -> ConditionOperator:
        return self.root.operator

    @property
    def expected(self) -> ExpectedConditionScalar:
        return self.root.expected

    @property
    def target_id(self) -> str | None:
        return getattr(self.root, "target_id", None)

    @property
    def max_age_seconds(self) -> float:
        return self.root.max_age_seconds

    @property
    def required_capabilities(self) -> list[str]:
        return getattr(self.root, "required_capabilities", [])


def game_binding_success_condition(
    binding: GameBinding,
    telemetry: TelemetrySnapshot | None,
) -> Condition | None:
    """Describe the exact observable state one reversible binding must change."""

    if telemetry is None:
        return None
    if binding is GameBinding.QUICKLOAD:
        current_session_id = telemetry.identity_session_id
        if (
            current_session_id is None
            or "identity.stable_handles" not in telemetry.capabilities
        ):
            return None
        return Condition(
            kind=ConditionKind.FIELD,
            path=GAME_BINDING_VERIFICATION_PATHS[binding],
            operator=ConditionOperator.NOT_EQUALS,
            expected=current_session_id,
            max_age_seconds=3.0,
            required_capabilities=["identity.stable_handles"],
        )
    if binding is GameBinding.TOGGLE_INVENTORY:
        current = telemetry.ui.open_inventory_windows
        if current is None:
            return None
        return Condition(
            kind=ConditionKind.FIELD,
            path=GAME_BINDING_VERIFICATION_PATHS[binding],
            operator=ConditionOperator.NOT_EQUALS,
            expected=current,
            max_age_seconds=3.0,
        )
    if binding is GameBinding.TOGGLE_MAP:
        current = telemetry.ui.management_screen_open
    elif binding is GameBinding.TOGGLE_STATS:
        current = telemetry.ui.stats_window_open
    else:
        return None
    if current is None:
        return None
    return Condition(
        kind=ConditionKind.FIELD,
        path=GAME_BINDING_VERIFICATION_PATHS[binding],
        operator=ConditionOperator.EQUALS,
        expected=not current,
        max_age_seconds=3.0,
    )


class ConditionEvaluation(StrictModel):
    condition: Condition
    result: ConditionResult
    actual: ConditionScalar = None
    reason: str = Field(min_length=1, max_length=1000)


class RiskBudget(StrictModel):
    max_pointer_actions: int = Field(ge=0, le=32)
    max_purchase_actions: int = Field(ge=0, le=8)
    max_native_assisted_actions: int = Field(ge=0, le=8)


def _unique_conditions(conditions: list[Condition]) -> list[Condition]:
    """Return one copy of each logical predicate, preserving authored order."""

    unique: list[Condition] = []
    for condition in conditions:
        if condition not in unique:
            unique.append(condition)
    return unique


class PlanStep(StrictModel):
    step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    # `PlannerAction`, not `Action`: a plan is authored, so it must not offer
    # the controller primitives. Advertising them put five raw-input actions in
    # the response schema the planner is never allowed to choose.
    action: PlannerAction
    preconditions: list[Condition] = Field(min_length=1, max_length=12)
    # The action-completion catalog decides whether the controller, runtime, or
    # planner owns verification. Keeping that rule out of this generic schema
    # prevents a hard-coded action list from drifting behind the catalog.
    success_conditions: list[Condition] = Field(default_factory=list, max_length=12)
    failure_conditions: list[Condition] = Field(default_factory=list, max_length=12)
    timeout_seconds: float = Field(gt=0.0, le=300.0)
    retry_budget: int = Field(default=0, ge=0, le=2)
    idempotency: IdempotencyPolicy = IdempotencyPolicy.AT_MOST_ONCE
    on_success: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )
    on_failure: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )
    interrupt_policy: InterruptPolicy = InterruptPolicy.CANCEL_ON_REFLEX
    observation_policy: ObservationPolicy = ObservationPolicy.UNTIL_TERMINAL

    @field_validator(
        "preconditions",
        "success_conditions",
        "failure_conditions",
        mode="after",
    )
    @classmethod
    def normalize_duplicate_conditions(
        cls,
        conditions: list[Condition],
    ) -> list[Condition]:
        return _unique_conditions(conditions)

    @model_validator(mode="after")
    def retry_requires_idempotency(self) -> PlanStep:
        if self.retry_budget and self.idempotency != IdempotencyPolicy.SAFE_TO_RETRY:
            raise ValueError("retry_budget requires idempotency=safe_to_retry")
        return self


class PlanEnvelope(StrictModel):
    schema_version: Literal["1.0"]
    plan_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,95}$")
    plan_version: int = Field(default=1, ge=1)
    objective: str = Field(min_length=1, max_length=1000)
    control_mode: ControlMode
    based_on_revision: WorldStateRevision
    assumptions: list[Condition] = Field(min_length=1, max_length=12)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    entry_step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    max_actions: int = Field(ge=1, le=16)
    max_wall_seconds: float = Field(gt=0.0, le=600.0)
    max_game_seconds: float = Field(gt=0.0, le=3600.0)
    risk_budget: RiskBudget
    # A continuous planner had nowhere to write anything down: continuity
    # existed only on `PlannerDecision`, which single-step runs use, so the
    # memory store was recalled into every observation and could never be
    # filled. An intention therefore died with the plan that held it, and the
    # next plan re-derived a goal from whatever was on screen - which in a bar
    # is the barman, every time. Processed only after this plan passes every
    # validation gate; a rejected plan contributes nothing.
    continuity_operations: list[ContinuityOperation] = Field(
        default_factory=list,
        max_length=6,
    )
    fieldbook_operations: list[FieldbookOperation] = Field(
        default_factory=list,
        max_length=4,
    )

    @model_validator(mode="after")
    def validate_graph_and_action_bound(self) -> PlanEnvelope:
        by_id = {step.step_id: step for step in self.steps}
        if len(by_id) != len(self.steps):
            raise ValueError("Plan step_id values must be unique")
        if self.entry_step_id not in by_id:
            raise ValueError("entry_step_id does not identify a plan step")
        for step in self.steps:
            for branch in (step.on_success, step.on_failure):
                if branch is not None and branch not in by_id:
                    raise ValueError(f"Step {step.step_id!r} references unknown branch {branch!r}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("Plan graph must be acyclic")
            if step_id in visited:
                return
            visiting.add(step_id)
            step = by_id[step_id]
            for branch in (step.on_success, step.on_failure):
                if branch is not None:
                    visit(branch)
            visiting.remove(step_id)
            visited.add(step_id)

        visit(self.entry_step_id)
        unreachable = set(by_id) - visited
        if unreachable:
            raise ValueError(f"Plan contains unreachable steps: {sorted(unreachable)}")

        worst_case_actions = sum(1 + step.retry_budget for step in self.steps)
        if worst_case_actions > self.max_actions:
            raise ValueError(
                f"Plan can attempt {worst_case_actions} actions but max_actions is "
                f"{self.max_actions}"
            )
        return self


class PlanPatch(StrictModel):
    schema_version: Literal["1.0"]
    plan_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,95}$")
    based_on_plan_version: int = Field(ge=1)
    based_on_revision: WorldStateRevision
    # Null preserves the active step. Naming it exactly requests a guarded
    # interruption; the executor still owns cancellation and pause handoff.
    interrupt_active_step_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )
    replace_future_steps: list[PlanStep] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=1000)
    # Committed at the exact moment this patch is revalidated and becomes the
    # active plan. A staged patch that is rejected, superseded, or discarded
    # contributes nothing.
    continuity_operations: list[ContinuityOperation] = Field(
        default_factory=list,
        max_length=6,
    )
    fieldbook_operations: list[FieldbookOperation] = Field(
        default_factory=list,
        max_length=4,
    )


class ActivePlanContext(StrictModel):
    plan_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,95}$")
    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=1000)
    active_step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    active_step_interrupt_policy: InterruptPolicy = InterruptPolicy.CANCEL_ON_REFLEX
    completed_step_ids: list[str] = Field(default_factory=list, max_length=16)
    remaining_actions: int = Field(ge=0, le=16)


def _resolved_planner_payload_chars(max_chars: int | None) -> int:
    """Resolve the public default inside mutation-visible behavior."""

    return 24000 if max_chars is None else max_chars


def _planner_json(value: Any) -> str:
    """Render the canonical human-readable planner document."""

    # pragma: no mutate start
    return json.dumps(
        value,
        indent=2,
        # `json.dumps` treats None exactly like False for this flag.
        ensure_ascii=False,
    )
    # pragma: no mutate end


def _json_model(value: BaseModel) -> dict[str, Any]:
    """Project a model through its canonical JSON representation."""

    result: dict[str, Any] = json.loads(value.model_dump_json())
    return result


class Observation(StrictModel):
    run_id: str
    step_index: int = Field(ge=0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mode: Literal["mock", "live", "replay"]
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY
    planning_mode: PlanningMode = PlanningMode.SINGLE_STEP
    live_execution_policy: LiveContinuousPolicy = LiveContinuousPolicy.DISABLED
    world_revision: WorldStateRevision = Field(default_factory=WorldStateRevision)
    telemetry: TelemetrySnapshot | None = None
    telemetry_stale: bool = False
    telemetry_age_seconds: float | None = None
    screenshot_path: Path | None = None
    screenshot_sha256: str | None = None
    events: list[str] = Field(default_factory=list)
    objective: str | None = Field(default=None, max_length=1000)
    active_plan: ActivePlanContext | None = None
    recent_action_outcomes: list[ActionOutcome] = Field(default_factory=list, max_length=100)
    # Why previous plans ended, in terms of what they set out to do. Working
    # history, not durable memory: it is runtime-owned and dies with the run.
    recent_plan_outcomes: list[PlanOutcome] = Field(default_factory=list, max_length=8)
    # Why the last continuity operations were accepted or refused. Without this
    # a planner that makes one deterministic mistake remakes it every plan.
    recent_continuity_receipts: list[ContinuityReceiptDigest] = Field(
        default_factory=list,
        max_length=8,
    )
    # A diagnostic store write can fail without being a planner-authored
    # operation, so it has no operation receipt. Keep the quarantined state
    # explicit until the run ends instead of making absence look healthy.
    continuity_writes_degraded_reason: str | None = Field(
        default=None,
        max_length=1000,
    )
    continuity_reads_degraded_reason: str | None = Field(
        default=None,
        max_length=1000,
    )
    # Why the previous planner response could not be used. Without it a planner
    # that makes one deterministic mistake remakes it on every retry: a live run
    # ended after 21 identical validation failures, each replanned from an
    # observation that said nothing about the previous twenty.
    planner_feedback: str | None = Field(default=None, max_length=1200)
    # What actually moved since the previous observation, as path/before/after.
    # The agent's hardest question is whether the thing it just did had any
    # effect, and a full snapshot answers it only by comparison against a
    # snapshot it no longer has. Both stalled live runs failed here: steps that
    # "completed" while changing nothing, replanned from observations that
    # looked identical to the ones before them.
    recent_changes: list[StateChange] = Field(default_factory=list, max_length=40)
    available_skills: list[str] = Field(default_factory=list)
    skill_specs: list[SkillSpec] = Field(default_factory=list)
    memories: list[MemoryRecord] = Field(default_factory=list)
    # What automatic recall left out, so "nothing else" and "more, not shown"
    # are distinguishable.
    memory_recall: RecallSummary = Field(default_factory=RecallSummary)
    # The typed result of an elective `recall_memory`, carried to exactly the
    # next planner call that asked for it.
    memory_search: MemoryReadReceipt | None = None
    # Automatic context carries only a bounded project index and the selected
    # active summary. Full entries arrive through one bounded elective read.
    fieldbook_projects: list[FieldbookProjectIndex] = Field(
        default_factory=list,
        max_length=8,
    )
    active_fieldbook_project: ActiveFieldbookProject | None = None
    recent_fieldbook_receipts: list[FieldbookReceiptDigest] = Field(
        default_factory=list,
        max_length=8,
    )
    fieldbook_read: FieldbookReadReceipt | None = None
    advisor: AdvisorAvailability = Field(default_factory=AdvisorAvailability)

    def current_memory_target_ids(self) -> set[str]:
        """Return exact identities safe to use for entity-scoped recall.

        A stale snapshot is not evidence that an entity is still current. The
        lookup deliberately ignores display names and historical native target
        fields: only an entity in this snapshot, or the dialogue currently
        open in this snapshot, can reactivate a bound memory.
        """

        if self.telemetry is None or self.telemetry_stale:
            return set()
        telemetry = self.telemetry
        target_ids = {
            item.id
            for collection in (
                telemetry.squad,
                telemetry.nearby_entities,
                telemetry.world_targets,
                telemetry.known_map_destinations,
            )
            for item in collection
            if item.id
        }
        if telemetry.ui.dialogue_target_id:
            target_ids.add(telemetry.ui.dialogue_target_id)
        return target_ids

    def travel_destination_digest(self) -> list[dict[str, Any]]:
        """Somewhere to walk that is not already somewhere to talk.

        `dialogue_targets` already carries the talkable people and survives
        budgeting whole. Movement destinations lived only in
        `telemetry.nearby_entities`, a budgeted collection trimmed before
        anything else: eighteen nearby characters became one in the payload, and
        that one was in the room the agent was already standing in. It was given
        a way to leave and shown nowhere to go.

        Only the characters absent from `dialogue_targets` are listed, because
        the rest would be a second copy, and furthest first, because the near
        ones are the ones already covered. Kept short for the same reason: this
        is preserved through budgeting, so every entry is charged against the
        envelope that must always fit.
        """

        if self.telemetry is None:
            return []
        talkable = {target["id"] for target in self.dialogue_target_digest()}
        elsewhere = [
            entity
            for entity in self.telemetry.nearby_entities
            if entity.id and entity.name and entity.id not in talkable
        ]
        elsewhere.sort(
            key=lambda entity: entity.distance if entity.distance is not None else 0.0,
            reverse=True,
        )
        return [
            {
                "id": entity.id,
                "name": entity.name,
                "distance": entity.distance,
            }
            for entity in elsewhere[:8]
        ]

    def known_map_destination_digest(self) -> list[dict[str, Any]]:
        """Discovered settlement markers available for semantic long travel."""

        if self.telemetry is None or self.telemetry_stale:
            return []
        return [
            destination.model_dump(mode="json", exclude_none=True)
            | {
                "travel_available": map_destination_travel_available(
                    destination,
                    current_location_id=self.telemetry.game.location_id,
                    inside_town_walls=self.telemetry.game.inside_town_walls,
                    location_authoritative=(
                        "game.location.identity" in self.telemetry.capabilities
                    ),
                ),
            }
            for destination in sorted(
                self.telemetry.known_map_destinations,
                key=lambda destination: (destination.distance, destination.id),
            )
        ]

    def dialogue_target_digest(self) -> list[dict[str, Any]]:
        """Deterministic, authoritative interaction affordances for the planner.

        The planner must not re-derive who is talkable from raw entity flags —
        that judgment flaked live. This is the pre-validated answer: every
        non-hostile person the agent could approach and talk to, nearest first,
        with the vendor subset marked. `is_vendor` distinguishes a talk target
        the agent can also trade with.
        """

        if self.telemetry is None:
            return []
        return [
            {
                "id": target.id,
                "name": target.name,
                "distance": target.distance,
                "visible": target.visible,
                "camera_bearing_degrees": target.camera_bearing_degrees,
                "is_vendor": target.is_confirmed_vendor(),
            }
            for target in dialogue_targets(self.telemetry.nearby_entities)
        ]

    def context_target_digest(self) -> list[dict[str, Any]]:
        """Exact reviewed object/task pairs that can be dispatched right now."""

        if self.telemetry is None:
            return []
        targets = sorted(
            (
                target
                for target in self.telemetry.world_targets
                if target.context_actions
            ),
            key=lambda target: (target.distance, target.name, target.id),
        )
        return [
            {
                "id": target.id,
                "name": target.name,
                "kind": target.kind,
                "distance": target.distance,
                "context_actions": [action.value for action in target.context_actions],
                "mining_resource_level": target.mining_resource_level,
                **(
                    {
                        "screen_position": target.screen_position.model_dump(
                            mode="json"
                        )
                    }
                    if target.screen_position is not None
                    else {}
                ),
            }
            for target in targets[:16]
        ]

    def open_window_captions(self) -> list[str]:
        """Captions of the windows currently advertising controls, in order."""

        telemetry = self.telemetry
        if telemetry is None or telemetry.ui.visible_controls is None:
            return []
        seen: list[str] = []
        for control in telemetry.ui.visible_controls:
            if control.window and control.window not in seen:
                seen.append(control.window)
        return seen

    def window_owners(self) -> dict[str, dict[str, Any]]:
        """Whose inventory each open window is, keyed by normalized caption.

        Kenshi captions an inventory window with its owner's name, upper-cased,
        so the only thing standing between the planner and "is this the shop's
        stock or mine" is a case-insensitive name match. Doing it here means the
        answer arrives as a fact rather than as a string comparison the planner
        has to think to make - and gets wrong in the direction that sells your
        own coat.
        """

        telemetry = self.telemetry
        if telemetry is None:
            return {}
        owners: dict[str, dict[str, Any]] = {}
        for entity in telemetry.nearby_entities:
            if entity.shop_inventory_owner is True and entity.name:
                owners[normalize_control_label(entity.name)] = {
                    "belongs_to": "vendor",
                    "seller_id": entity.id,
                }
        # Squad last: a window naming one of your own characters is yours, even
        # if something nearby shares the name.
        for character in telemetry.squad:
            if character.name:
                owners[normalize_control_label(character.name)] = {
                    "belongs_to": "you",
                }
        return owners

    def vendor_inventory_windows(self) -> list[str]:
        """Open windows that are a registered shop owner's own inventory."""

        owners = self.window_owners()
        return [
            caption
            for caption in self.open_window_captions()
            if owners.get(normalize_control_label(caption), {}).get("belongs_to") == "vendor"
        ]

    def trade_screen_open(self) -> bool:
        """Whether a shop's stock is open beside ours.

        Kenshi runs a trade as two inventory windows - the player's and the
        trader's - and every buy or sell is a right-click inside one of them.
        `ui.active_screen` collapses that to one label and reports 'inventory'
        whenever it cannot resolve the trader behind the window, so gating on
        the label alone refuses real trades: live run
        live-shop-ownership-regression-20260729-r2 lost four planner calls to
        "the trade screen is not open" while the operator was looking at it.

        Ownership decides it, exactly as it already decides which cell a
        purchase may bind to. Neither the label nor a count of item cells is
        evidence, and `active_shop_trader_count` counts traders loaded in the
        world rather than windows open on screen.
        """

        telemetry = self.telemetry
        if telemetry is None:
            return False
        if telemetry.ui.active_screen == "trade":
            return True
        open_inventories = telemetry.ui.open_inventory_windows
        if open_inventories is None or open_inventories < 2:
            return False
        return bool(self.vendor_inventory_windows())

    def visible_control_digest(
        self,
        limit: int = MAX_DIGESTED_VISIBLE_CONTROLS,
    ) -> list[dict[str, Any]]:
        """Exact controls the interface currently advertises, unambiguous only.

        The bounded argument source for `activate_visible_control`. A label that
        currently appears more than once is marked ambiguous rather than
        silently resolved, because a duplicate reference must fail closed rather
        than pick one. Bounds stay in telemetry; the planner names a label and
        role, never a coordinate.

        `limit` is normally derived from the room left in the payload rather
        than passed, so a screen with few controls surfaces all of them and a
        crowded one surfaces as many as actually fit.
        """

        telemetry = self.telemetry
        if telemetry is None or telemetry.ui.visible_controls is None:
            return []
        if "ui.visible_controls" not in telemetry.capabilities:
            return []
        controls = telemetry.ui.visible_controls
        # Ambiguity is judged the way the binder judges it, or the advice is
        # stricter than the rule it describes. The binder resolves duplicate item
        # cells that are interchangeable - same window, same item, same price -
        # and only fails closed when they differ. Counting bare labels instead
        # flagged two identical Greenfruit as ambiguous, and since the prompt
        # forbids authoring an ambiguous entry, a stack of anything became
        # unsellable: the agent refused its own duplicate stock, correctly, on
        # our own advice.
        variants: dict[tuple[str, str, str], set[tuple[Any, ...]]] = {}
        for control in controls:
            key = (normalize_control_label(control.label), control.role, control.window)
            # Two buttons sharing a caption are two different buttons and stay
            # ambiguous; only stock has a notion of being interchangeable.
            distinguishing: tuple[Any, ...] = (id(control),)
            if control.role == "item":
                # A cell with no name cannot be shown interchangeable with
                # anything, so each stays its own variant and fails closed.
                distinguishing = (
                    (control.item_name, control.item_value)
                    if control.item_name is not None
                    else (id(control),)
                )
            variants.setdefault(key, set()).add(distinguishing)
        counts = {key: len(seen) for key, seen in variants.items()}
        # Bounded so this digest cannot overflow the irreducible planner
        # envelope. Truncation is fail-closed: an unlisted control is one the
        # planner will not author, never one it may author blindly.
        digest = []
        for control in budgeted_visible_controls(controls, limit):
            entry = {
                "exact_label": control.label,
                "role": control.role,
                "window": control.window,
                "ambiguous": (
                    counts[(normalize_control_label(control.label), control.role, control.window)]
                    > 1
                ),
            }
            # An item cell's label is a bare ordinal from the export walk, so
            # without these the planner sees "cell 37" and has to hover to learn
            # what it is - a round trip per cell, to recover facts the telemetry
            # already carries. The plugin walks the inventory structure
            # precisely so nobody has to hover; dropping them here threw that
            # away and left the agent unable to read a price it was looking at.
            if control.role == "item":
                entry["item_name"] = control.item_name
                entry["item_value"] = control.item_value
                entry["item_quantity"] = control.item_quantity
                entry["section"] = control.section
            digest.append(entry)
        return digest

    def semantic_action_digest(self) -> list[dict[str, Any]]:
        """Exactly the reusable actions that are authorable right now.

        Availability is computed from contracts against this observation's
        control mode and capabilities, so the planner is never shown an action
        the runtime would refuse. Each entry names where its arguments must come
        from, which is what makes composition possible without a recipe.
        """

        # Imported here because the contract catalog is defined in terms of
        # these models; the dependency only exists at call time.
        from .action_contracts import planner_visible_contracts

        capabilities = set(self.telemetry.capabilities if self.telemetry is not None else [])
        # Deliberately terse: this rides in the irreducible planner envelope, so
        # it must not grow without bound as actions are added. What the planner
        # cannot get anywhere else is *which* actions are authorable now and
        # where their arguments come from; the prose description of each lives
        # in the system prompt, and the contract enforces the rest regardless.
        digest: list[dict[str, Any]] = []
        for contract in planner_visible_contracts(
            control_mode=self.control_mode,
            capabilities=capabilities,
            observation=self,
        ):
            entry: dict[str, Any] = {
                "kind": contract.kind,
                "argument_source": contract.argument_source,
            }
            if contract.kind == "use_game_binding":
                entry["runtime_completion_conditions"] = {
                    binding.value: _json_model(condition)
                    for binding in GameBinding
                    if binding not in TIME_GAME_BINDINGS
                    and (
                        condition := game_binding_success_condition(
                            binding,
                            self.telemetry,
                        )
                    )
                    is not None
                }
            digest.append(entry)
        return digest

    def log_digest(self) -> dict[str, Any]:
        """A compact record of this observation for the session log.

        Writing the whole observation every pump tick produced a 112 MB log in
        ten minutes - unworkable for an agent meant to run continuously. Only
        the replay environment ever needed the full payload; the evaluator reads
        a handful of fields, and a human reading the log wants orientation, not
        two hundred control bounds. This keeps every field either consumer uses
        plus enough state to diagnose a run, at roughly a hundredth of the size.
        """

        telemetry = self.telemetry
        digest: dict[str, Any] = {
            "run_id": self.run_id,
            "step_index": self.step_index,
            "mode": self.mode,
            "control_mode": self.control_mode.value,
            "planning_mode": self.planning_mode.value,
            "live_execution_policy": self.live_execution_policy.value,
            "world_revision": _json_model(self.world_revision),
            "telemetry_stale": self.telemetry_stale,
            "telemetry_age_seconds": self.telemetry_age_seconds,
            "events": list(self.events),
            "objective": self.objective,
            "advisor": _json_model(self.advisor),
            "digest": True,
        }
        if telemetry is None:
            digest["telemetry"] = None
            return digest

        selected = next(
            (character for character in telemetry.squad if character.selected),
            None,
        )
        digest["telemetry"] = {
            "sequence": telemetry.sequence,
            "source": telemetry.source,
            "identity_session_id": telemetry.identity_session_id,
            "capabilities": list(telemetry.capabilities),
            "game": {
                "loaded": telemetry.game.loaded,
                "paused": telemetry.game.paused,
                "money": telemetry.game.money,
                "elapsed_minutes": telemetry.game.elapsed_minutes,
                "location_name": telemetry.game.location_name,
            }
            | (
                {
                    "location_id": telemetry.game.location_id,
                    "inside_town_walls": telemetry.game.inside_town_walls,
                }
                if "game.location.identity" in telemetry.capabilities
                else {}
            ),
            "ui": {
                "active_screen": telemetry.ui.active_screen,
                "modal_open": telemetry.ui.modal_open,
                "dialogue_open": telemetry.ui.dialogue_open,
                "dialogue_target_id": telemetry.ui.dialogue_target_id,
                "tooltip_visible": telemetry.ui.tooltip_visible,
                "open_inventory_windows": telemetry.ui.open_inventory_windows,
                "management_screen_open": telemetry.ui.management_screen_open,
                "management_tab": telemetry.ui.management_tab,
                "selected_character_id": telemetry.ui.selected_character_id,
                # These are not bulk UI detail: together they are the authority
                # boundary for binding an output cell to one exact resource.
                "context_inventory_target_id": telemetry.ui.context_inventory_target_id,
                "visible_controls_complete": telemetry.ui.visible_controls_complete,
                "visible_control_count": (
                    len(telemetry.ui.visible_controls)
                    if telemetry.ui.visible_controls is not None
                    else None
                ),
                # Buttons and captions are the bulk of the controls and are not
                # worth keeping, but the item cells are the shelf: without them
                # a post-mortem cannot say what was for sale, at what price, or
                # whether the thing the agent kept reaching for was ever there.
                # They are the minority of controls, so this stays cheap.
                "item_cells": [
                    {
                        "label": control.label,
                        "window": control.window,
                        "section": control.section,
                        "item_name": control.item_name,
                        "item_value": control.item_value,
                        "item_quantity": control.item_quantity,
                    }
                    for control in (telemetry.ui.visible_controls or [])
                    if control.role == "item"
                ][:60],
                "open_windows": self.open_window_captions(),
            },
            # The evaluator reconstructs native command causality from these, so
            # they are kept whole rather than counted.
            "native_control": _json_model(telemetry.native_control),
            "active_shop_trader_count": telemetry.active_shop_trader_count,
            "nearby_entity_count": len(telemetry.nearby_entities),
            "dialogue_target_count": len(dialogue_targets(telemetry.nearby_entities)),
            "world_target_count": len(telemetry.world_targets),
            "context_targets": self.context_target_digest(),
            "selected": (
                {
                    "id": selected.id,
                    "name": selected.name,
                    "hunger": selected.hunger,
                    "food_items": selected.food_items,
                    "in_combat": selected.in_combat,
                    "indoors": selected.indoors,
                    "inventory_complete": selected.inventory_complete,
                    # Kept so a post-mortem can tell a healthy run from one
                    # where the character was quietly being beaten.
                    "blood": selected.blood,
                    "bleeding_rate": selected.bleeding_rate,
                    "position": (
                        _json_model(selected.position)
                        if selected.position is not None
                        else None
                    ),
                }
                if selected is not None
                else None
            ),
        }
        return digest

    def _fitted_visible_controls(
        self,
        payload: dict[str, Any],
        max_chars: int,
    ) -> list[dict[str, Any]]:
        """As many controls as the payload has room for, role-balanced.

        The control digest is preserved whole through payload budgeting - the
        planner may only act on a control it was shown, so a half-listed action
        surface is worse than a smaller observation elsewhere. That made it the
        one collection nothing bounded, which is why it carried a hand-picked
        cap of 120 for so long. Measuring the room that is actually left keeps
        the fail-closed guarantee without guessing the number: a dialogue with
        nine controls surfaces all nine, a trade screen surfaces what fits, and
        raising the payload budget widens both without another edit here.
        """

        # Measured against the irreducible payload, not the full one. Budgeting
        # has not run yet, so the payload still carries whole telemetry and is
        # normally larger than the budget on its own; comparing against it would
        # conclude there is never room for a single control. What the digest
        # actually competes with is the content that budgeting can never drop.
        floor = irreducible_payload(payload)
        owners = self.window_owners()

        def rendered_size(candidate: list[dict[str, Any]]) -> int:
            # This scratch document is measured and discarded. Any same-length
            # spelling has identical behavior, so keep the canonical key out of
            # the mutation signal while testing its consumer at planner_payload.
            # pragma: no mutate start
            floor["visible_controls"] = group_controls_by_window(
                candidate,
                owners,
            )
            # pragma: no mutate end
            return len(_planner_json(floor))

        controls = (
            self.telemetry.ui.visible_controls
            if self.telemetry is not None
            and self.telemetry.ui.visible_controls is not None
            else []
        )
        candidates = [self.visible_control_digest(0)]
        for limit, _control in enumerate(
            controls[:MAX_DIGESTED_VISIBLE_CONTROLS],
            start=1,
        ):
            candidates.append(self.visible_control_digest(limit))

        fitted_index = max(
            0,
            bisect_right(
                [rendered_size(candidate) for candidate in candidates],
                max_chars,
            )
            - 1,
        )
        return candidates[fitted_index]

    def planner_payload(
        self,
        *,
        max_chars: int | None = None,
        max_context_chars: int = MAX_PLANNER_CONTEXT_CHARS,
    ) -> str:
        """Render this observation for the planner within a character budget.

        `max_chars` bounds what the observation *costs*, which is a spending
        decision, not a limit of the model - the configured 30k is under one
        percent of a current context window. It therefore governs the optional
        content only. The control list and exact current-target memories are not
        optional: the planner may act only on a control it was shown, and a
        learned entity constraint must not vanish merely because later general
        facts filled the recall list. When either decision-critical surface
        costs more than the budget, the spending budget gives way.

        `max_context_chars` is the genuine ceiling, being a property of the
        model rather than a preference. Crossing it is a real failure and says
        so, rather than quietly dropping controls.
        """

        max_chars = _resolved_planner_payload_chars(max_chars)
        payload = self.model_dump(mode="json", exclude={"screenshot_path"})
        # Surface the deterministic talk-target list the planner must trust
        # rather than re-derive. A top-level non-collection key is preserved
        # through budgeting.
        payload["dialogue_targets"] = self.dialogue_target_digest()
        payload["travel_destinations"] = self.travel_destination_digest()
        payload["known_map_destinations"] = self.known_map_destination_digest()
        payload["context_targets"] = self.context_target_digest()
        payload["semantic_actions"] = self.semantic_action_digest()

        controls = self.visible_control_digest()
        floor = irreducible_payload(payload)
        # A budget too small for the safety envelope is still a hard
        # configuration error. Current-target memories, like controls, may push
        # past the spending preference because silently dropping them changes
        # the planner's effective state.
        # pragma: no mutate start
        safety_floor = irreducible_payload(
            payload,
            # None is deliberately equivalent to False at this bool boundary.
            preserve_current_target_memories=False,
        )
        # pragma: no mutate end
        # These two scratch documents are measured and discarded. The canonical
        # key itself is asserted on the final planner payload below.
        # pragma: no mutate start
        safety_floor["visible_controls"] = []
        # pragma: no mutate end
        safety_required = len(_planner_json(safety_floor))
        # pragma: no mutate start
        floor["visible_controls"] = group_controls_by_window(
            controls,
            self.window_owners(),
        )
        # pragma: no mutate end
        required = len(_planner_json(floor))
        if max_chars < safety_required:
            required = max_chars
        if required > max_context_chars:
            # Only here is dropping a control the lesser evil. Say so in the
            # payload: an agent that knows its view is incomplete can ask for a
            # simpler screen, where one that is silently blinded cannot.
            shown = self._fitted_visible_controls(payload, max_context_chars)
            payload["visible_controls_truncated"] = {
                "shown": len(shown),
                "total": len(controls),
                "consequence": (
                    "The controls not listed cannot be acted on. Close a window "
                    "to reduce the screen before relying on this list."
                ),
            }
            controls = shown

        payload["visible_controls"] = group_controls_by_window(controls, self.window_owners())
        text = _planner_json(payload)
        return budget_observation_payload(
            payload,
            full_text=text,
            max_chars=min(max(max_chars, required), max_context_chars),
        )


class PlannerDecision(StrictModel):
    intent: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1500)
    action: SingleStepPlannerAction
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_observation: str | None = Field(default=None, max_length=1000)
    # Committed after this action's receipt, never before it.
    continuity_operations: list[ContinuityOperation] = Field(
        default_factory=list,
        max_length=6,
    )
    fieldbook_operations: list[FieldbookOperation] = Field(
        default_factory=list,
        max_length=4,
    )


PlannerOutput: TypeAlias = PlannerDecision | PlanEnvelope | PlanPatch


@dataclass(frozen=True, slots=True)
class AuthoredPlannerOutput:
    """A parsed planner output inseparable from the context that authored it."""

    output: PlannerOutput
    context: AuthoredPlannerContext


class InputBoundaryReport(StrictModel):
    """Evidence from the final fence that runs after the input lease is acquired.

    Validation performed before a polite input lease can become obsolete while
    the lease is pending, so a sensitive action revalidates its typed plan
    conditions against the latest canonical revision immediately before the
    first primitive is emitted.
    """

    decision: InputBoundaryDecision
    reason: str = Field(min_length=1, max_length=1000)
    lease_wait_seconds: float = Field(default=0.0, ge=0.0)
    plan_id: str | None = Field(default=None, max_length=96)
    plan_version: int | None = Field(default=None, ge=1)
    step_id: str | None = Field(default=None, max_length=64)
    validated_revision: WorldStateRevision | None = None
    boundary_revision: WorldStateRevision | None = None
    evaluations: list[ConditionEvaluation] = Field(default_factory=list, max_length=24)


class CameraRecoveryStatus(StrEnum):
    ALREADY_CLEAR = "already_clear"
    RECOVERED = "recovered"
    FAILED_AFTER_BOUNDED_ATTEMPTS = "failed_after_bounded_attempts"


class CameraFrameScore(StrictModel):
    """One retained frame and the deterministic signals used to rank it."""

    candidate: str = Field(min_length=1, max_length=80)
    screenshot_path: Path
    screenshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    telemetry_sequence: int = Field(ge=0)
    frame_sequence: int = Field(ge=0)
    floor: int
    score: float = Field(ge=0.0, le=1.0)
    edge_density: float = Field(ge=0.0, le=1.0)
    contrast: float = Field(ge=0.0, le=1.0)
    color_diversity: float = Field(ge=0.0, le=1.0)
    nonflat_fraction: float = Field(ge=0.0, le=1.0)
    inverse_dominant_color: float = Field(ge=0.0, le=1.0)
    selected_world_label_visible: bool
    anchor_distance: float | None = Field(default=None, ge=0.0)
    clear: bool


class CameraRecoveryEvidence(StrictModel):
    """Controller-owned proof for a complete bounded recovery transaction."""

    status: CameraRecoveryStatus
    selected_character_id: str = Field(min_length=1, max_length=200)
    selected_character_name: str = Field(min_length=1, max_length=200)
    initial_floor: int
    final_floor: int
    clear_score_threshold: float = Field(ge=0.0, le=1.0)
    anchor_max_distance: float = Field(gt=0.0)
    paused_for_recovery: bool
    primitive_actions: int = Field(ge=0, le=100)
    follow_method: Literal["already_anchored", "portrait_double_click"]
    chosen_candidate: str = Field(min_length=1, max_length=80)
    candidates: list[CameraFrameScore] = Field(min_length=1, max_length=16)


class ResourceTransferStatus(StrEnum):
    TRANSFERRED = "transferred"
    NOT_TRANSFERRED = "not_transferred"
    UNVERIFIED = "unverified"


class ResourceTransferEvidence(StrictModel):
    """Controller-owned conservation proof for one resource-output transfer."""

    status: ResourceTransferStatus
    target_id: str = Field(min_length=1, max_length=200)
    selected_character_id: str | None = Field(default=None, min_length=1, max_length=200)
    item_name: str = Field(min_length=1, max_length=200)
    source_quantity_before: int | None = Field(default=None, ge=0)
    source_quantity_after: int | None = Field(default=None, ge=0)
    destination_quantity_before: int | None = Field(default=None, ge=0)
    destination_quantity_after: int | None = Field(default=None, ge=0)
    observed_after_sequence: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class ResourceHarvestStatus(StrEnum):
    HARVESTED = "harvested"
    NOT_HARVESTED = "not_harvested"
    CLEANUP_FAILED = "cleanup_failed"


class ResourceHarvestEvidence(StrictModel):
    """Terminal proof for one controller-owned production and transfer bundle."""

    status: ResourceHarvestStatus
    target_id: str = Field(min_length=1, max_length=200)
    selected_character_id: str = Field(min_length=1, max_length=200)
    requested_quantity: int = Field(ge=1, le=5)
    item_name: str | None = Field(default=None, min_length=1, max_length=200)
    transferred_quantity: int = Field(default=0, ge=0)
    production_command_id: str | None = Field(
        default=None,
        pattern=r"^cmd-[0-9a-f]{32}$",
    )
    inventory_command_id: str | None = Field(
        default=None,
        pattern=r"^cmd-[0-9a-f]{32}$",
    )
    transfer: ResourceTransferEvidence | None = None
    cleanup_confirmed: bool
    reason: str = Field(min_length=1, max_length=1000)


class PurchaseStatus(StrEnum):
    PURCHASED = "purchased"
    PARTIALLY_PURCHASED = "partially_purchased"
    NOT_PURCHASED = "not_purchased"
    OUTCOME_UNKNOWN = "outcome_unknown"


def _validate_purchase_status_quantity(
    status: PurchaseStatus,
    requested_quantity: int,
    purchased_quantity: int,
) -> None:
    """Keep terminal status consistent with the controller-proven quantity."""

    if purchased_quantity > requested_quantity:
        raise ValueError("purchased_quantity cannot exceed requested_quantity")
    if (
        status is PurchaseStatus.PURCHASED
        and purchased_quantity != requested_quantity
    ):
        raise ValueError("purchased status requires the full requested quantity")
    if status is PurchaseStatus.PARTIALLY_PURCHASED and not (
        0 < purchased_quantity < requested_quantity
    ):
        raise ValueError(
            "partially_purchased status requires a strict partial quantity"
        )
    if status is PurchaseStatus.NOT_PURCHASED and purchased_quantity != 0:
        raise ValueError("not_purchased status requires zero purchased quantity")
    if (
        status is PurchaseStatus.OUTCOME_UNKNOWN
        and purchased_quantity >= requested_quantity
    ):
        raise ValueError("outcome_unknown requires an unresolved remaining quantity")


class PurchaseEvidence(StrictModel):
    """Terminal conservation proof for one bounded purchasing transaction."""

    status: PurchaseStatus
    seller_id: str = Field(min_length=1, max_length=200)
    selected_character_id: str = Field(min_length=1, max_length=200)
    item_name: str = Field(min_length=1, max_length=200)
    requested_quantity: int = Field(ge=1, le=5)
    purchased_quantity: int = Field(ge=0, le=5)
    money_before: int = Field(ge=0)
    money_after: int | None = Field(default=None, ge=0)
    inventory_quantity_before: int = Field(ge=0)
    inventory_quantity_after: int | None = Field(default=None, ge=0)
    observed_after_sequence: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def status_matches_conserved_quantity(self) -> PurchaseEvidence:
        _validate_purchase_status_quantity(
            self.status,
            self.requested_quantity,
            self.purchased_quantity,
        )
        return self


class SaleStatus(StrEnum):
    SOLD = "sold"
    PARTIALLY_SOLD = "partially_sold"
    NOT_SOLD = "not_sold"
    OUTCOME_UNKNOWN = "outcome_unknown"


def _validate_sale_status_quantity(
    status: SaleStatus,
    requested_quantity: int,
    sold_quantity: int,
) -> None:
    """Keep terminal status consistent with the controller-proven quantity."""

    if sold_quantity > requested_quantity:
        raise ValueError("sold_quantity cannot exceed requested_quantity")
    if status is SaleStatus.SOLD and sold_quantity != requested_quantity:
        raise ValueError("sold status requires the full requested quantity")
    if status is SaleStatus.PARTIALLY_SOLD and not (
        0 < sold_quantity < requested_quantity
    ):
        raise ValueError("partially_sold status requires a strict partial quantity")
    if status is SaleStatus.NOT_SOLD and sold_quantity != 0:
        raise ValueError("not_sold status requires zero sold quantity")
    if status is SaleStatus.OUTCOME_UNKNOWN and sold_quantity >= requested_quantity:
        raise ValueError("outcome_unknown requires an unresolved remaining quantity")


class SaleEvidence(StrictModel):
    """Terminal conservation proof for one bounded selling transaction."""

    status: SaleStatus
    buyer_id: str = Field(min_length=1, max_length=200)
    selected_character_id: str = Field(min_length=1, max_length=200)
    item_name: str = Field(min_length=1, max_length=200)
    requested_quantity: int = Field(ge=1, le=5)
    sold_quantity: int = Field(ge=0, le=5)
    money_before: int = Field(ge=0)
    money_after: int | None = Field(default=None, ge=0)
    inventory_quantity_before: int = Field(ge=0)
    inventory_quantity_after: int | None = Field(default=None, ge=0)
    observed_after_sequence: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def status_matches_conserved_quantity(self) -> SaleEvidence:
        _validate_sale_status_quantity(
            self.status,
            self.requested_quantity,
            self.sold_quantity,
        )
        return self


class SemanticActionReceipt(StrictModel):
    """Causal evidence for one reusable semantic action.

    Records what the action's arguments actually resolved to against observed
    state, so a receipt proves which real reference was acted on rather than
    only which arguments were requested.
    """

    action_kind: str = Field(min_length=1, max_length=80)
    contract_version: str = Field(min_length=1, max_length=32)
    target_id: str | None = Field(default=None, max_length=200)
    resolved_label: str | None = Field(default=None, max_length=500)
    resolved_role: str | None = Field(default=None, max_length=32)
    resolved_bounds: NormalizedPointerBounds | None = None
    source_revision: WorldStateRevision | None = None
    option_id: str | None = Field(default=None, max_length=128)
    revalidation: str = Field(min_length=1, max_length=1000)
    legacy_compatibility: bool = False
    camera_recovery: CameraRecoveryEvidence | None = None
    purchase: PurchaseEvidence | None = None
    sale: SaleEvidence | None = None
    resource_transfer: ResourceTransferEvidence | None = None
    resource_harvest: ResourceHarvestEvidence | None = None


class ActionReceipt(StrictModel):
    action: Action
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY
    command_id: str | None = Field(
        default=None,
        pattern=r"^cmd-[0-9a-f]{32}$",
    )
    started_after_revision: WorldStateRevision | None = None
    completed_at_revision: WorldStateRevision | None = None
    causal_revision_advanced: bool | None = None
    native_acknowledgement: NativeCommandAcknowledgement | None = None
    input_boundary: InputBoundaryReport | None = None
    calibration: CalibrationReport | None = None
    semantic: SemanticActionReceipt | None = None
    advisor: AdvisorConsultEvidence | None = None
    accepted: bool
    executed: bool
    dry_run: bool
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    primitive_actions: int = Field(default=0, ge=0)
    message: str = ""
    error_type: str | None = None


class Transition(StrictModel):
    receipt: ActionReceipt
    observation: Observation
    terminated: bool = False
    success: bool | None = None
    events: list[str] = Field(default_factory=list)


class SessionEvent(StrictModel):
    event_type: str
    run_id: str
    step_index: int | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, JsonValue] = Field(default_factory=dict)
