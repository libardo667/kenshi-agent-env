from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Any, Literal, TypeAlias
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
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


def _nearest_first(entities: list[NearbyEntity]) -> list[NearbyEntity]:
    return sorted(
        entities,
        key=lambda entity: entity.distance if entity.distance is not None else float("inf"),
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
    location_name: str | None = None


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

    if limit <= 0:
        return []
    if len(controls) <= limit:
        return list(controls)

    by_role: dict[str, list[VisibleUIControl]] = {}
    for control in controls:
        by_role.setdefault(control.role, []).append(control)

    chosen: list[VisibleUIControl] = []
    cursors = dict.fromkeys(by_role, 0)
    while len(chosen) < limit:
        progressed = False
        for role, bucket in by_role.items():
            if len(chosen) >= limit:
                break
            index = cursors[role]
            if index < len(bucket):
                chosen.append(bucket[index])
                cursors[role] = index + 1
                progressed = True
        if not progressed:
            break

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
        "exit_current_building",
        "operate_natural_resource",
    ]
    status: NativeCommandStatus
    reason: str = Field(min_length=1, max_length=200)
    # Targeted commands bind to one stable entity. Directional movement binds
    # to its bearing and distance instead and deliberately names no target.
    target_id: str = Field(default="", max_length=200)
    bearing_degrees: float = Field(default=0.0, ge=0.0, lt=360.0)
    distance_units: float = Field(default=0.0, ge=0.0, le=2000.0)
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
    protocol_version: str = "1.0.0"
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
    active_shop_trader_count: int | None = Field(default=None, ge=0)
    nearby_entities: list[NearbyEntity] = Field(default_factory=list)
    world_targets: list[WorldTarget] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @model_validator(mode="after")
    def stable_identity_must_be_complete_and_consistent(self) -> TelemetrySnapshot:
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


class SetSpeedAction(StrictModel):
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


class AffordanceUrgency(StrEnum):
    SURVIVAL_CRITICAL = "survival_critical"
    BLOCKS_CURRENT_GOAL = "blocks_current_goal"
    IMPROVES_FIDELITY = "improves_fidelity"


class AffordanceIntentClass(StrEnum):
    """Small game-neutral classes for grouping missing player intentions."""

    OBSERVE = "observe"
    MOVE = "move"
    INTERACT = "interact"
    COMMUNICATE = "communicate"
    MANAGE = "manage"


class RequestAffordanceAction(StrictModel):
    """Retain a concrete capability gap without emitting game input.

    This is a cognitive action, like consulting the advisor. It tells the
    engineering loop which intention the current action surface cannot express;
    it does not grant that capability or authorize an improvised substitute.
    """

    kind: Literal["request_affordance"] = "request_affordance"
    game: Literal["kenshi"] = "kenshi"
    intent_class: AffordanceIntentClass
    capability_slug: str = Field(
        min_length=3,
        max_length=80,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$",
    )
    capability_description: str = Field(min_length=1, max_length=300)
    blocked_goal: str = Field(min_length=1, max_length=300)
    why_needed: str = Field(min_length=1, max_length=600)
    evidence: str = Field(min_length=1, max_length=600)
    available_workaround: str | None = Field(default=None, max_length=400)
    urgency: AffordanceUrgency = AffordanceUrgency.BLOCKS_CURRENT_GOAL


class KeyAction(StrictModel):
    kind: Literal["key"] = "key"
    key: str = Field(min_length=1, max_length=32)
    hold_seconds: float = Field(default=0.04, ge=0.0, le=5.0)


class HotkeyAction(StrictModel):
    kind: Literal["hotkey"] = "hotkey"
    keys: list[str] = Field(min_length=2, max_length=5)
    hold_seconds: float = Field(default=0.04, ge=0.0, le=5.0)


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
    """Buy the item in one exact seller-owned cell.

    Current producers name the cell and item directly. `expected_price` carries
    the best current value estimate for optional spending gates, but the
    exported `item_value` is base worth rather than an authoritative final shop
    charge. The action carries no coordinates; exact item, seller, and owner
    binding prevents the wrong cell from being selected, and a later money
    change proves the effect.
    """

    kind: Literal["purchase_item"] = "purchase_item"
    cell_label: str = Field(min_length=1, max_length=80)
    item_name: str = Field(min_length=1, max_length=200)
    expected_price: int = Field(gt=0)
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
    """Sell the item in one exact cell of the agent's own inventory.

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
    # Caption of the inventory window the cell sits in; must be the selected
    # character's own window, never the trader's.
    window: str = Field(min_length=1, max_length=200)
    buyer_id: str = Field(min_length=1, max_length=200)


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


class GameBinding(StrEnum):
    """Kenshi's named control intentions under the shipped default keymap.

    The current physical mapping is a hard-coded copy of the shipped
    `controls.cfg`; it is not read from the user's active keymap. Only the
    reversible ones are here: `quicksave`, `quickload`, `editor_toggle`,
    `rebuild_navmesh` and `reload_biomes` all exist in that file and are all
    deliberately absent from this enum, because an agent running unattended on a
    stream must not be one keystroke away from overwriting a save.
    """

    # Screens. Each is a toggle, so pressing twice returns to where it started.
    TOGGLE_INVENTORY = "toggle_inventory"
    TOGGLE_MAP = "toggle_map"
    TOGGLE_STATS = "toggle_stats"
    TOGGLE_HELP = "toggle_help"
    TOGGLE_CRAFTING = "toggle_crafting"
    TOGGLE_RESEARCH = "toggle_research"
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
    CAMERA_ZOOM_IN = "camera_zoom_in"
    CAMERA_ZOOM_OUT = "camera_zoom_out"
    FOCUS_CHAR = "focus_char"
    # Selection.
    SELECT_ALL = "select_all"
    CHANGE_SQUAD = "change_squad"
    CHARACTER_NEXT = "character_next"
    CHARACTER_PREV = "character_prev"
    # Orders.
    STOP_MOVEMENT = "stop_movement"


GAME_BINDING_KEYS: dict[GameBinding, str] = {
    GameBinding.TOGGLE_INVENTORY: "i",
    GameBinding.TOGGLE_MAP: "m",
    GameBinding.TOGGLE_STATS: "c",
    GameBinding.TOGGLE_HELP: "f1",
    GameBinding.TOGGLE_CRAFTING: "y",
    GameBinding.TOGGLE_RESEARCH: "t",
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
    GameBinding.CAMERA_ZOOM_IN: "home",
    GameBinding.CAMERA_ZOOM_OUT: "end",
    GameBinding.FOCUS_CHAR: "f",
    GameBinding.SELECT_ALL: "grave",
    GameBinding.CHANGE_SQUAD: "tab",
    GameBinding.CHARACTER_NEXT: "]",
    GameBinding.CHARACTER_PREV: "[",
    GameBinding.STOP_MOVEMENT: "r",
}
"""Default Kenshi key per binding; hard-coded, not parsed from active controls.cfg."""

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
    }
)


class UseGameBindingAction(StrictModel):
    """Press one named Kenshi control through the shipped-default keymap.

    The agent kept trying to reach screens by hunting for a widget to click -
    clicking the time-speed buttons to unpause, clicking around the world hoping
    an inventory would appear - because nothing in the catalog could simply open
    a screen. Kenshi already binds all of this: `I` opens the inventory, `M` the
    map, `C` the stats window, `Space` pauses under the shipped defaults. Naming
    the *binding* rather than the key keeps the intention readable and the
    current default mapping in one place; customized keymaps are not yet read.
    """

    kind: Literal["use_game_binding"] = "use_game_binding"
    binding: GameBinding
    # What the planner expects this to change, so the step can be verified
    # rather than assumed. Free text: the typed check lives in the step's
    # success conditions.
    expected_effect: str = Field(min_length=1, max_length=200)


ControllerPrimitive: TypeAlias = (
    KeyAction | HotkeyAction | MoveCursorAction | ClickAction | ScrollAction
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
    | RequestAffordanceAction
)
"""Planner-layer intentions that touch no game object and bind to no reference."""

SemanticAction: TypeAlias = (
    ApproachDialogueTargetAction
    | PerformContextAction
    | MoveToCharacterAction
    | MoveInDirectionAction
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
"""Reusable typed game/UI intentions bound to currently observed references."""

PlannerAction: TypeAlias = PlannerControlAction | SemanticAction | SkillAction
"""What a planner may author. `SkillAction` is temporary legacy compatibility."""

Action: TypeAlias = (
    NoopAction
    | StopAction
    | PauseAction
    | SetSpeedAction
    | WaitAction
    | ConsultAdvisorAction
    | RequestAffordanceAction
    | KeyAction
    | HotkeyAction
    | MoveCursorAction
    | ClickAction
    | ScrollAction
    | SkillAction
    | ApproachDialogueTargetAction
    | PerformContextAction
    | MoveToCharacterAction
    | MoveInDirectionAction
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
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)

SEMANTIC_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "approach_dialogue_target",
        "perform_context_action",
        "move_to_character",
        "move_in_direction",
        "exit_current_building",
        "activate_visible_control",
        "dismiss_screen",
        "purchase_item",
        "use_game_binding",
        "scroll_screen",
        "sell_item",
        "equip_item",
        "recover_camera_view",
    }
)
CONTROLLER_PRIMITIVE_KINDS: frozenset[str] = frozenset(
    {"key", "hotkey", "move_cursor", "click", "scroll"}
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
        "request_affordance",
    }
)


def is_planner_control_action(action: Action) -> bool:
    """Planner-layer control that touches no game object and binds to no reference."""

    return action.kind in PLANNER_CONTROL_ACTION_KINDS


def new_command_id() -> str:
    return f"cmd-{uuid4().hex}"


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


class ActionOutcome(StrictModel):
    step_index: int = Field(ge=0)
    intent: str = Field(min_length=1, max_length=1000)
    action: Action
    executed: bool
    receipt_message: str = Field(default="", max_length=2000)
    assessment: ActionOutcomeAssessment
    feedback: str = Field(min_length=1, max_length=1000)
    visual_change_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    telemetry_changes: list[str] = Field(default_factory=list, max_length=30)
    selected_character_name: str | None = Field(default=None, max_length=200)
    position_before: Vec3 | None = None
    position_after: Vec3 | None = None


class MemoryWrite(StrictModel):
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=2000)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: str | None = Field(default=None, max_length=1000)
    # Opaque identity copied exactly from the current observation. Display
    # names are intentionally insufficient: two Barmen may share one, and a
    # later identity session may reuse the same role for another character.
    target_id: str | None = Field(default=None, min_length=1, max_length=200)


class MemoryRecord(StrictModel):
    id: int
    namespace: str
    run_id: str
    kind: MemoryKind
    content: str
    salience: float
    evidence: str | None = None
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    created_at: datetime
    last_accessed_at: datetime


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


class AdvisorConsultStatus(StrEnum):
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


class AffordanceRequestStatus(StrEnum):
    RETAINED = "retained"
    DUPLICATE = "duplicate"


class AffordanceRequestEvidence(StrictModel):
    status: AffordanceRequestStatus
    reason: str = Field(min_length=1, max_length=1000)
    request_number: int = Field(ge=1)
    aggregation_key: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^kenshi:(?:observe|move|interact|communicate|manage):"
        r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+$",
    )


def affordance_aggregation_key(action: RequestAffordanceAction) -> str:
    """Return the one stable cross-run identity for a grounded capability gap.

    A second copy of this rule is a second answer to "is this a duplicate?", so
    retained records, receipts, and offline aggregation all derive from here.
    """

    return f"{action.game}:{action.intent_class.value}:{action.capability_slug}"


class AffordanceRequestRecord(StrictModel):
    request_number: int = Field(ge=1)
    action: RequestAffordanceAction
    based_on_revision: WorldStateRevision
    # Stored beside the record so duplicate detection reads the same list the
    # planner sees. A parallel index outlived this list once and made an evicted
    # gap permanently unreportable.
    aggregation_key: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def key_matches_action(self) -> AffordanceRequestRecord:
        expected = affordance_aggregation_key(self.action)
        if self.aggregation_key != expected:
            raise ValueError(
                "Affordance request aggregation_key must match its typed action."
            )
        return self


class CommandDispatchContext(StrictModel):
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    based_on_revision: WorldStateRevision


class NativeCommandRequest(StrictModel):
    schema_version: Literal["1.0"]
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    command: Literal[
        "approach_confirmed_vendor",
        "move_to_character",
        "move_in_direction",
        "exit_current_building",
        "operate_natural_resource",
    ]
    control_mode: Literal[ControlMode.NATIVE_ASSISTED]
    identity_session_id: str = Field(min_length=1, max_length=200)
    based_on_revision: WorldStateRevision
    selected_character_ids: list[str] = Field(min_length=1, max_length=1)
    # Empty for a directional walk, which references nobody.
    target_id: str = Field(default="", max_length=200)
    bearing_degrees: float = Field(default=0.0, ge=0.0, lt=360.0)
    distance_units: float = Field(default=0.0, ge=0.0, le=2000.0)

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
        return self


ConditionScalar: TypeAlias = str | int | float | bool | None


class ConditionPath(StrEnum):
    CONTROL_MODE = "control_mode"
    TELEMETRY_STALE = "telemetry_stale"
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
    GAME_PAUSE_CAPABILITY = "game.pause"
    GAME_SPEED_CAPABILITY = "game.speed"
    GAME_TIME_CAPABILITY = "game.time"
    GAME_MONEY_CAPABILITY = "game.money"
    GAME_LOCATION_CAPABILITY = "game.location"
    CAMERA_POSITION_CAPABILITY = "camera.position"
    SQUAD_BASIC_CAPABILITY = "squad.basic"
    SQUAD_HUNGER_CAPABILITY = "squad.hunger"
    SQUAD_HEALTH_CAPABILITY = "squad.health"
    SQUAD_INVENTORY_CAPABILITY = "squad.inventory"
    SQUAD_CURRENT_GOAL_CAPABILITY = "squad.current_goal"
    UI_MODAL_CAPABILITY = "ui.modal"
    UI_INVENTORY_CAPABILITY = "ui.inventory"
    UI_DIALOGUE_CAPABILITY = "ui.dialogue"
    UI_DIALOGUE_TARGET_CAPABILITY = "ui.dialogue.target"
    UI_DIALOGUE_OPTIONS_CAPABILITY = "ui.dialogue.options"
    UI_VISIBLE_CONTROLS_CAPABILITY = "ui.visible_controls"
    UI_TOOLTIP_CAPABILITY = "ui.tooltip"
    NEARBY_CHARACTERS_CAPABILITY = "nearby.characters"
    NEARBY_VISIBLE_ENTITIES_CAPABILITY = "nearby.visible_entities"
    NEARBY_ROLES_CAPABILITY = "nearby.roles"
    NEARBY_SHOP_OWNERS_CAPABILITY = "nearby.shop_owners"
    # The authorization fact is "this is a valid current dialogue target", not
    # "this is a vendor". The legacy name remains the wire capability the
    # installed plug-in emits; the generic name is the contract vocabulary.
    CONTROL_APPROACH_VENDOR_CAPABILITY = "control.approach_vendor"
    CONTROL_APPROACH_DIALOGUE_TARGET_CAPABILITY = "control.approach_dialogue_target"
    IDENTITY_STABLE_HANDLES_CAPABILITY = "identity.stable_handles"


_ALLOWED_CONDITION_PATHS = {
    path.value
    for path in ConditionPath
    if path.value.startswith(("telemetry.", "selected.", "target."))
    or path in {ConditionPath.CONTROL_MODE, ConditionPath.TELEMETRY_STALE}
}
_ALLOWED_CAPABILITY_PATHS = {
    path.value for path in ConditionPath if path.value not in _ALLOWED_CONDITION_PATHS
}
# Capability names a condition may require. `required_capabilities` used to be
# free-form, so a planner could put a *field path* like "ui.active_screen" there
# and only find out much later, as an opaque "capabilities are unavailable".
# Checking here turns that into an immediate, nameable mistake.
KNOWN_CAPABILITIES: frozenset[str] = frozenset(_ALLOWED_CAPABILITY_PATHS)


class Condition(StrictModel):
    kind: ConditionKind
    path: ConditionPath | None = None
    operator: ConditionOperator
    expected: ConditionScalar = None
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    max_age_seconds: float = Field(gt=0.0, le=300.0)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_shape(self) -> Condition:
        if self.kind == ConditionKind.FIELD and self.path in _ALLOWED_CAPABILITY_PATHS:
            # `ConditionPath` is one flat enum of 80 values, 24 of which are only
            # ever legal as capability names and 56 only as field paths, with
            # nothing in the schema saying which is which. A model reading it
            # picks `squad.inventory` as a field path - entirely reasonable - and
            # is refused. The intent is unambiguous, so read it as the capability
            # condition it can only have meant.
            object.__setattr__(self, "kind", ConditionKind.CAPABILITY)

        if self.kind == ConditionKind.FIELD:
            if self.path not in _ALLOWED_CONDITION_PATHS:
                raise ValueError(f"Unsupported condition path: {self.path!r}")
            if self.path is not None and self.path.startswith("target.") and not self.target_id:
                raise ValueError("target.* conditions require target_id")
            if (
                self.path is not None
                and not self.path.startswith("target.")
                and self.target_id is not None
            ):
                # A redundant entity annotation cannot narrow a global scalar.
                # Normalize it away so policy matching and evaluation share one
                # canonical condition shape.
                object.__setattr__(self, "target_id", None)
        elif self.kind == ConditionKind.CAPABILITY:
            if self.path is None:
                # The commonest single failure in the model benchmark, across
                # both models and intermittently rather than always: a capability
                # condition whose subject is stated in `required_capabilities`
                # instead of `path`. When exactly one capability is named there
                # the intent is unambiguous, so read it rather than refuse. More
                # than one is genuinely ambiguous and still fails.
                candidates = [
                    name for name in self.required_capabilities if name in _ALLOWED_CAPABILITY_PATHS
                ]
                if candidates:
                    # Every name here is enforced at evaluation regardless of
                    # which one `path` names - a missing one withholds the
                    # verdict - so taking the first loses nothing and asserts
                    # exactly what was meant.
                    object.__setattr__(self, "path", candidates[0])
            if self.path is None:
                raise ValueError(
                    "Capability conditions require path: name the capability in "
                    "`path`, not only in `required_capabilities`."
                )
            if self.path not in _ALLOWED_CAPABILITY_PATHS:
                raise ValueError(f"Unsupported capability path: {self.path!r}")
            if self.target_id is not None:
                # Inert here for the same reason as on a global field: evaluation
                # never reads it. Normalize rather than refuse.
                object.__setattr__(self, "target_id", None)
        else:
            # `telemetry_fresh` asks one question - is telemetry current - and
            # evaluation consults neither path nor target_id when answering it.
            # Rejecting a plan over fields that cannot change its meaning threw
            # away every plan six different models produced, each of which
            # annotated the condition with the field it was about. Normalize to
            # the canonical shape, as the field branch above already does.
            if self.path is not None:
                object.__setattr__(self, "path", None)
            if self.target_id is not None:
                object.__setattr__(self, "target_id", None)
        # `required_capabilities` is a belt over braces: evaluation independently
        # looks up the capability a condition's own field path depends on and
        # withholds a verdict when Kenshi is not reporting it. So an entry here
        # can only ever *add* strictness, and a wrong one cannot let an unsafe
        # condition through - it can only destroy an otherwise sound plan, which
        # is what it did to three of five models in one benchmark, each naming a
        # field path where a capability name goes despite the prompt saying not
        # to. When five independent models make one mistake the vocabulary is at
        # fault, so drop what we do not recognise and keep the plan.
        recognised = [name for name in self.required_capabilities if name in KNOWN_CAPABILITIES]
        if len(recognised) != len(self.required_capabilities):
            object.__setattr__(self, "required_capabilities", recognised)
        if self.expected is None:
            raise ValueError(
                f"an '{self.operator.value}' condition has no `expected` value, so "
                f"there is nothing to compare the observation against. Set `expected` "
                f"to the value this condition should hold"
            )
        if self.operator == ConditionOperator.CONTAINS and not isinstance(self.expected, str):
            raise ValueError("contains conditions require a string expected value")
        return self


class ConditionEvaluation(StrictModel):
    condition: Condition
    result: ConditionResult
    actual: ConditionScalar = None
    reason: str = Field(min_length=1, max_length=1000)


class RiskBudget(StrictModel):
    max_pointer_actions: int = Field(ge=0, le=32)
    max_purchase_actions: int = Field(ge=0, le=8)
    max_native_assisted_actions: int = Field(ge=0, le=8)


class PlanStep(StrictModel):
    step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    # `PlannerAction`, not `Action`: a plan is authored, so it must not offer
    # the controller primitives. Advertising them put five raw-input actions in
    # the response schema the planner is never allowed to choose.
    action: PlannerAction
    preconditions: list[Condition] = Field(min_length=1, max_length=12)
    # Controller-verified actions return their own typed terminal verdict and
    # therefore need no model-authored condition. Every other action still
    # requires at least one condition; the validator below enforces that
    # conditional rule.
    success_conditions: list[Condition] = Field(default_factory=list, max_length=12)
    failure_conditions: list[Condition] = Field(default_factory=list, max_length=12)
    timeout_seconds: float = Field(gt=0.0, le=60.0)
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

    @model_validator(mode="after")
    def retry_requires_idempotency(self) -> PlanStep:
        if self.retry_budget and self.idempotency != IdempotencyPolicy.SAFE_TO_RETRY:
            raise ValueError("retry_budget requires idempotency=safe_to_retry")
        if not self.success_conditions and not isinstance(
            self.action,
            (
                RecoverCameraViewAction,
                ConsultAdvisorAction,
                RequestAffordanceAction,
                ExitCurrentBuildingAction,
                PerformContextAction,
            ),
        ):
            raise ValueError(
                "success_conditions may be empty only for recover_camera_view, "
                "consult_advisor, request_affordance, exit_current_building, or "
                "perform_context_action, whose owning subsystem returns a typed "
                "terminal outcome"
            )
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
    max_wall_seconds: float = Field(gt=0.0, le=120.0)
    max_game_seconds: float = Field(gt=0.0, le=3600.0)
    risk_budget: RiskBudget
    # A continuous planner had nowhere to write anything down: `memory_writes`
    # existed only on `PlannerDecision`, which single-step runs use, so the
    # memory store was recalled into every observation and could never be
    # filled. An intention therefore died with the plan that held it, and the
    # next plan re-derived a goal from whatever was on screen - which in a bar
    # is the barman, every time.
    memory_writes: list[MemoryWrite] = Field(default_factory=list, max_length=6)

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
    memory_writes: list[MemoryWrite] = Field(default_factory=list, max_length=6)


class ActivePlanContext(StrictModel):
    plan_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,95}$")
    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=1000)
    active_step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    active_step_interrupt_policy: InterruptPolicy = InterruptPolicy.CANCEL_ON_REFLEX
    completed_step_ids: list[str] = Field(default_factory=list, max_length=16)
    remaining_actions: int = Field(ge=0, le=16)


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
    advisor: AdvisorAvailability = Field(default_factory=AdvisorAvailability)
    affordance_requests: list[AffordanceRequestRecord] = Field(
        default_factory=list,
        max_length=32,
    )

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
        return [
            {
                "kind": contract.kind,
                "argument_source": contract.argument_source,
            }
            for contract in planner_visible_contracts(
                control_mode=self.control_mode,
                capabilities=capabilities,
                observation=self,
            )
        ]

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
            "world_revision": self.world_revision.model_dump(mode="json"),
            "telemetry_stale": self.telemetry_stale,
            "telemetry_age_seconds": self.telemetry_age_seconds,
            "events": list(self.events),
            "objective": self.objective,
            "advisor": self.advisor.model_dump(mode="json"),
            "affordance_requests": [
                request.model_dump(mode="json") for request in self.affordance_requests
            ],
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
            },
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
            "native_control": telemetry.native_control.model_dump(mode="json"),
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
                    # Kept so a post-mortem can tell a healthy run from one
                    # where the character was quietly being beaten.
                    "blood": selected.blood,
                    "bleeding_rate": selected.bleeding_rate,
                    "position": (
                        selected.position.model_dump(mode="json")
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

        def fits(limit: int) -> list[dict[str, Any]] | None:
            candidate = self.visible_control_digest(limit)
            floor["visible_controls"] = group_controls_by_window(candidate, owners)
            if len(json.dumps(floor, indent=2, ensure_ascii=False)) > max_chars:
                return None
            return candidate

        everything = fits(MAX_DIGESTED_VISIBLE_CONTROLS)
        if everything is not None:
            return everything

        # Serialized size grows with the limit, so bisect for the largest
        # role-balanced selection that still fits rather than walking up to it -
        # this runs on every observation, at telemetry cadence.
        fitted: list[dict[str, Any]] = []
        low, high = 0, MAX_DIGESTED_VISIBLE_CONTROLS
        while low < high:
            middle = (low + high + 1) // 2
            candidate = fits(middle)
            if candidate is None:
                high = middle - 1
            else:
                fitted = candidate
                low = middle
        return fitted

    def planner_payload(
        self,
        *,
        max_chars: int = 24000,
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

        payload = self.model_dump(mode="json", exclude={"screenshot_path"})
        # Surface the deterministic talk-target list the planner must trust
        # rather than re-derive. A top-level non-collection key is preserved
        # through budgeting.
        payload["dialogue_targets"] = self.dialogue_target_digest()
        payload["travel_destinations"] = self.travel_destination_digest()
        payload["context_targets"] = self.context_target_digest()
        payload["semantic_actions"] = self.semantic_action_digest()

        controls = self.visible_control_digest()
        floor = irreducible_payload(payload)
        floor["visible_controls"] = []
        # A budget too small for the safety envelope is still a hard
        # configuration error. Current-target memories, like controls, may push
        # past the spending preference because silently dropping them changes
        # the planner's effective state.
        safety_floor = irreducible_payload(
            payload,
            preserve_current_target_memories=False,
        )
        safety_floor["visible_controls"] = []
        safety_required = len(json.dumps(safety_floor, indent=2, ensure_ascii=False))
        floor["visible_controls"] = group_controls_by_window(controls, self.window_owners())
        required = len(json.dumps(floor, indent=2, ensure_ascii=False))
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
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        return budget_observation_payload(
            payload,
            full_text=text,
            max_chars=min(max(max_chars, required), max_context_chars),
        )


class PlannerDecision(StrictModel):
    intent: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1500)
    action: PlannerAction
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_observation: str | None = Field(default=None, max_length=1000)
    memory_writes: list[MemoryWrite] = Field(default_factory=list, max_length=6)


PlannerOutput: TypeAlias = PlannerDecision | PlanEnvelope | PlanPatch


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
    affordance_request: AffordanceRequestEvidence | None = None
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
