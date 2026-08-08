"""Telemetry domain types."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from math import inf
from typing import Any, ClassVar, Literal

from pydantic import (
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import core_schema

from .base import StrictModel
from .gui_resolution import ResolvedControl, resolve_control

RUNTIME_CONTEXT_MENU_CAPABILITY = "ui.context_menu.orders"
STABLE_HANDLE_CAPABILITY = "identity.stable_handles"


def context_menu_state_is_consistent(
    *,
    context_menu_open: bool | None,
    context_menu_probe: str | None,
    has_context_menu: bool,
) -> bool:
    """Whether open, probe, and payload describe one possible observation."""

    if context_menu_probe is None:
        return not has_context_menu
    if context_menu_probe == "captured":
        return context_menu_open is True and has_context_menu
    if context_menu_probe == "closed":
        return context_menu_open is False and not has_context_menu
    return context_menu_open is True and not has_context_menu


def context_menu_capability_is_consistent(
    *,
    capabilities: Collection[str],
    context_menu_open: bool | None,
    context_menu_probe: str | None,
    has_context_menu: bool,
) -> bool:
    """Whether the advertised capability is backed by a complete envelope."""

    if RUNTIME_CONTEXT_MENU_CAPABILITY not in capabilities:
        return True
    if context_menu_open is None or context_menu_probe is None:
        return False
    return not has_context_menu or STABLE_HANDLE_CAPABILITY in capabilities


def require_consistent_context_menu_state(
    *,
    context_menu_open: bool | None,
    context_menu_probe: str | None,
    context_menu: object | None,
) -> None:
    """Reject a context-menu envelope that cannot be one game observation."""

    if not context_menu_state_is_consistent(
        context_menu_open=context_menu_open,
        context_menu_probe=context_menu_probe,
        has_context_menu=context_menu is not None,
    ):
        raise ValueError("context menu open, probe, and payload are inconsistent")


def require_truthful_context_menu_capability(
    *,
    capabilities: Collection[str],
    context_menu_open: bool | None,
    context_menu_probe: str | None,
    context_menu: object | None,
) -> None:
    """Reject advertised capture authority without its required evidence."""

    if context_menu_capability_is_consistent(
        capabilities=capabilities,
        context_menu_open=context_menu_open,
        context_menu_probe=context_menu_probe,
        has_context_menu=context_menu is not None,
    ):
        return
    if (
        RUNTIME_CONTEXT_MENU_CAPABILITY in capabilities
        and context_menu is not None
        and STABLE_HANDLE_CAPABILITY not in capabilities
    ):
        raise ValueError("runtime context menu targets require identity.stable_handles")
    raise ValueError("ui.context_menu.orders requires context menu open and probe state")


class ScenarioIdentity(StrictModel):
    """One declared game situation, tied to the exact save used to reproduce it."""

    scenario_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    save_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    environment: Literal["indoor", "outdoor"]
    danger: Literal["hostile", "safe"]
    economy: Literal["broke", "funded"]
    party: Literal["solo", "squad"]
    time_of_day: Literal["day", "night"]


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
    # Kenshi prices an item from two sides and shows both: an item listing
    # "Value c.5,165" and "Sell value c.1,291" is bought for the first and sold
    # for the second. Neither is "the" value, so neither is named that.
    item_base_value: int | None = None
    item_sell_value: int | None = None
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


class TaskEntry(StrictModel):
    """One task, in whichever list Kenshi keeps it."""

    # Kenshi's own TaskType value and name. The value is authoritative; the name
    # comes from the generated vocabulary so consumers need no copy of the enum.
    task_value: int | None = None
    task_name: str = Field(default="", max_length=80)
    # What the task acts on, when it names something.
    subject_id: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=300)


class CharacterTaskState(StrictModel):
    """What a character has been told to do, by channel.

    Kenshi keeps ordinary orders, Jobs, permajobs, and the AI's current goal in
    four separate structures with different lifetimes, and none may be inferred
    from another: a mining animation is not evidence of a Job, and an entry in
    the Jobs list is not evidence of an ordinary order.

    Exporting none of this had teeth. An `operate` order was accepted and
    retained by Kenshi while the controller reported it failed and retried it
    eight times, and a character holding a retained mining Job walked out of a
    trade conversation because the Job pulled him back to the node. Both were
    invisible.
    """

    # Whether Kenshi considers this character to be under player orders at all.
    has_player_orders: bool = False
    # The ordinary order queue - what a player fills by right-clicking.
    orders: list[TaskEntry] = Field(default_factory=list, max_length=8)
    orders_count: int = Field(default=0, ge=0)
    # False when the export could not prove it had the whole queue, so a
    # bounded list is never mistaken for a short one. Kenshi exports the head
    # and the tail of the order queue and no size(), so any queue of two or
    # more arrives incomplete: the count means "at least this many, each one
    # proven", not a total.
    orders_complete: bool = True

    # Jobs: repeating assignments, with their own switch. A character can hold
    # Jobs while they are disabled, which is why the flag is separate from the
    # list rather than implied by it being empty.
    jobs_enabled: bool = False
    jobs: list[TaskEntry] = Field(default_factory=list, max_length=8)
    jobs_count: int = Field(default=0, ge=0)
    jobs_complete: bool = True

    # Permajobs: a distinct list with its own slot API and its own clear.
    permajobs: list[TaskEntry] = Field(default_factory=list, max_length=8)
    permajobs_count: int = Field(default=0, ge=0)
    permajobs_complete: bool = True

    # What the AI settled on doing now. Neither an order nor a Job, and not to
    # be read as either - it is the goal the task system currently scores best.
    current_activity: TaskEntry | None = None

    @property
    def has_retained_work(self) -> bool:
        """Whether Kenshi is holding work for this character.

        The question the controller could not previously ask, and the one that
        explains a character leaving a conversation on his own.
        """

        return bool(self.orders_count or self.jobs_count or self.permajobs_count)


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
    # Orders, Jobs, permajobs, and current activity, kept apart. None means the
    # character's task system was unreachable, which is a different fact from
    # holding no work.
    task_state: CharacterTaskState | None = None


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
    # What Kenshi says this person affords the current selection right now.
    # The roles above (`has_dialogue`, `has_vendor_list`, ...) are facts about
    # who someone is; this is the game's own answer about what may be ordered
    # on them, which is why attacking, looting, and first aid need no role flag
    # of their own. Probing is budgeted nearest-first, so `probed` False means
    # "not asked", never "affords nothing" -- an empty list from an unprobed
    # entity is silence, not a denial.
    advertised_tasks: list[AdvertisedTask] = Field(default_factory=list, max_length=64)
    advertised_tasks_probed: bool = False

    def orderable_task_names(self) -> tuple[str, ...]:
        """Lowercased task names this person currently affords, wire-ready.

        The plugin resolves an order by this exact name, so the same string
        that arrives in telemetry is the one that goes back out as a command.
        """

        return tuple(sorted(task.name.lower() for task in self.advertised_tasks))

    def order_evidence(self, order: str) -> frozenset[AdvertisedTaskSource]:
        """Which probes vouched for one order on this person.

        Empty when the order was never advertised. Two probes answer for the
        same target and they disagree in both directions, so an order that
        binds and then fails to take needs its evidence on the receipt rather
        than in a second live run.
        """

        return frozenset(
            task.source for task in self.advertised_tasks if task.name.lower() == order
        )

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

        `has_vendor_list` is the load-bearing term -- no list, no trade window.
        `is_squad_leader` is redundant in practice and kept only as a fence:
        across every recorded run, no talkable vendor-list holder was a
        non-leader (15536 sightings, 0 exceptions), because Kenshi's shopkeepers
        lead their own shop squad. Do not cite it as the reason a vendor was
        rejected without checking dialogue first -- the guards that look like
        non-leader vendors are excluded by `has_dialogue`, not by leadership.
        """

        return (
            self.is_dialogue_target()
            and self.has_vendor_list is True
            and self.is_squad_leader is True
        )


class ContextActionKind(str):
    """One runtime-advertised semantic context order.

    The protocol validates a stable semantic identifier but deliberately does
    not enumerate gameplay orders in Python. Source adapters decide whether a
    currently advertised order has a native or UI execution path.
    """

    OPERATE: ClassVar[ContextActionKind]

    def __new__(cls, value: str) -> ContextActionKind:
        return str.__new__(cls, value)

    @property
    def value(self) -> str:
        return str(self)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: object,
        _handler: object,
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(
                min_length=1,
                max_length=80,
                pattern=r"^[a-z][a-z0-9_]*$",
            ),
        )


ContextActionKind.OPERATE = ContextActionKind("operate")


class AdvertisedTaskSource(StrEnum):
    """How Kenshi was asked, because the answers are not equally trustworthy.

    Three separate engine calls were each mistaken for "what applies here"
    before provenance existed to tell them apart, and the cost was a day of
    live diagnosis against a list nobody could attribute.
    """

    MENU = "menu"
    """Kenshi's own context-menu builder produced this order for this target.

    Not a proxy for the answer, the answer: it is exactly what a player sees on
    right-click, obtained by having the game build the menu with its renderer
    muted.
    """

    ODDS = "odds"
    """`getPlayerTaskProbability` returned a success chance above zero.

    Sound when it fires, and silent for every order Kenshi renders no
    percentage beside - measured live it reported KIDNAP_ORDER and
    STEALTH_KNOCKOUT on a cannibal whose menu offered attacking instead. Its
    absence therefore means nothing at all.
    """


class AdvertisedTask(StrictModel):
    """One task Kenshi confirms the current selection may issue to a target.

    The value and name are the game's own; mapping either onto a controller
    semantic is the operation registry's job, not this model's. `source` records
    which engine call vouched for it, so a wrong offer stays attributable to the
    evidence that produced it.
    """

    value: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=80)
    source: AdvertisedTaskSource


class GroundPosition(StrictModel):
    """A position on Kenshi's ground plane.

    Kenshi's world is x/z at ground level with y as altitude, so a
    two-component ground position is x and z. `Vec2` is a screen-space x/y and
    means something different; reusing it here would have quietly relabelled an
    axis.
    """

    x: float
    z: float


class ProspectReading(StrictModel):
    """One line as Kenshi's Prospecting window displays it.

    The label is verbatim. The window builds each line from a resource name and
    a value but exposes only the button, so the exact split is unproven -
    parsing it here would assert a format nobody has confirmed. Whatever
    structure this turns out to have can be modelled once it is observed.
    """

    label: str = Field(min_length=1, max_length=200)
    # The reading beside the name, verbatim and colour-tag stripped. Empty when
    # the line carried no sibling caption, which is a different fact from a
    # reading of zero.
    value: str = Field(default="", max_length=200)


class ProspectSurvey(StrictModel):
    """One completed prospecting survey, tied to the command that asked for it.

    Read from the game's own window rather than the terrain field beneath it,
    so the agent sees what a player would see. Deliberately a pulse: it knows
    what it surveyed and where it stood, not what exists everywhere.

    The readings are area coverage, not deposit counts. A discrete iron node
    occupies a trivial fraction of the surveyed area, so it can read near zero
    while the deposit is plainly there - which is why this is not the channel
    for finding deposits. `world_targets` already carries those by coordinate
    with quality levels; this is the wider picture around them.
    """

    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    center: GroundPosition
    # The surveying character's science skill, which bounds what the window
    # reveals.
    skill: float = Field(ge=0.0)
    surveyed_name: str = Field(default="", max_length=200)
    # Whether Kenshi's prospecting window was actually showing when read.
    # An empty reading list with the window hidden is a different fact from
    # an empty list with it open.
    window_visible: bool = False
    readings: list[ProspectReading] = Field(default_factory=list, max_length=32)


class DiscoveredObject(StrictModel):
    """One nearby object and what Kenshi says it can be ordered to do.

    Reconnaissance, not a routed affordance. `world_targets` is the surface the
    controller has semantic routes for; this is the surface that exists. The
    gap between them is the implementation queue, and it is derived from the
    game rather than from anyone noticing an absence.

    Presence here authorizes nothing. A task appearing means Kenshi answered
    yes to "may this selection order this object to do that", which is a fact
    about the game, not permission for the controller to try it.
    """

    id: str = Field(min_length=1, max_length=500)
    name: str = Field(default="", max_length=500)
    # Kenshi's own object-category name, from its `itemType` enum.
    category: str = Field(min_length=1, max_length=80)
    distance: float = Field(ge=0.0)
    advertised_tasks: list[AdvertisedTask] = Field(default_factory=list, max_length=64)
    advertised_tasks_probed: bool = True


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
    # What Kenshi itself says the current selection may order this target to
    # do, discovered by probing its TaskType vocabulary rather than by the
    # plug-in restating a literal. This is reconnaissance, not authority: a
    # task appearing here means the game answered yes, not that the controller
    # has a semantic route for it. Routing stays with the operation registry.
    advertised_tasks: list[AdvertisedTask] = Field(default_factory=list, max_length=64)
    # False means this target was outside the per-snapshot probe budget, so an
    # empty `advertised_tasks` says nothing about what it affords.
    advertised_tasks_probed: bool = False


class KnownMapDestination(StrictModel):
    """A settlement marker the current player has actually discovered.

    The stable identity and player-visible name authorize a semantic journey.
    The controller owns the exact waypoint; no world coordinate is exposed to
    or authored by the planner.
    """

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    # Native XZ distance from the farthest currently selected squad member.
    # For one selected member this is the ordinary point distance. For a group,
    # using the primary member would hide travel whenever that member was
    # already local while another selected member was still far away.
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
    whole_group_present: bool = True,
) -> bool:
    """Whether travel to this destination would be a no-op.

    `whole_group_present` is a fact about the recipients, resolved by the
    map-travel operation from its own contract. This helper does not count
    selected characters: recipient scope belongs to the operation registry, and
    a cardinality rule living here was a second authority on the same question.
    """

    return (
        location_authoritative
        and whole_group_present
        and current_location_id == destination.id
        and (inside_town_walls is True or destination.has_gates is False)
    )


def map_destination_travel_available(
    destination: KnownMapDestination,
    *,
    current_location_id: str | None = None,
    inside_town_walls: bool | None = None,
    location_authoritative: bool = False,
    whole_group_present: bool = True,
) -> bool:
    if map_destination_already_reached(
        destination,
        current_location_id=current_location_id,
        inside_town_walls=inside_town_walls,
        location_authoritative=location_authoritative,
        whole_group_present=whole_group_present,
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


# Only a backstop against a pathological control list, not the working limit:
# how many controls the planner actually sees is decided by how many fit in the
# payload's measured envelope. A hand-picked count is wrong on both sides - it
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


def window_instance_of(widget_name: str) -> str:
    """Which open window a widget belongs to, from its MyGUI name.

    Windows are the one thing in this telemetry with no identity. Characters,
    resources, map destinations and dialogue targets all carry an opaque ID
    derived from a validated Kenshi handle; windows carry a caption and a count.
    That is why "is this the shop's window or the merchant's own?" has been
    answered by comparing caption strings in five places, and why trade
    authority took four commits to settle.

    MyGUI instantiates a layout with a per-load instance prefix on every widget
    name, so the prefix identifies the open window instance without serialising
    a pointer. It is stable while the window is open and differs between two
    windows built from the same layout - observed live as two separate
    `BorderPanel` instances under different prefixes.

    Undecorated and module-level so mutation tooling can see these decisions.
    """

    prefix, separator, _ = widget_name.partition("_")
    if not separator or not prefix:
        return ""
    # MyGUI writes the id as comma-grouped hex. Anything else is a widget whose
    # own name simply contains an underscore, like an `item_3` cell.
    for part in prefix.split(","):
        if not part:
            return ""
        for character in part:
            if character not in "0123456789ABCDEF":
                return ""
    return prefix


def layout_widget_name_of(widget_name: str) -> str:
    """The widget's name inside its layout, joinable to Kenshi's own files."""

    instance = window_instance_of(widget_name)
    if not instance:
        return widget_name
    return widget_name[len(instance) + 1 :]


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
    # The widget's own MyGUI name, always, separate from whatever a human reads
    # off it. `label` is a caption for most widgets and the widget name for a
    # caption-less button, so it cannot be joined against Kenshi's shipped
    # layouts: a caption may be localised, may collide, and 56 of 91 observed
    # labels were rendered content like '16 mph' rather than any control.
    widget_name: str = Field(default="", max_length=200)
    # MyGUI's own type - Button, TextBox, EditBox, ListBox, TabItem, ItemBox.
    # `role` collapses everything to button/text/item, which cannot say whether
    # a control is editable, scrollable, or a tab.
    widget_type: str = Field(default="", max_length=80)

    @property
    def window_instance(self) -> str:
        """Which open window this control belongs to, as an opaque identity."""

        return window_instance_of(self.widget_name)

    @property
    def layout_widget_name(self) -> str:
        """The widget's name inside its layout, joinable to Kenshi's own files."""

        return layout_widget_name_of(self.widget_name)

    @property
    def declaration(self) -> ResolvedControl:
        """This control matched against Kenshi's shipped GUI declaration.

        Turns a rendered caption into a declared identity: which layouts name
        this widget, its declared type, and the caption it was authored with.
        A control matching nothing is `undeclared` - built in code or added by
        a mod - which is a fact worth having rather than an error.
        """

        return resolve_control(self.layout_widget_name)

    # For `item` cells: what the cell actually holds. Without these the agent
    # can only learn a cell's contents by hovering it, one model round-trip at
    # a time, while a human simply reads the shop.
    item_name: str | None = Field(default=None, max_length=200)
    # What buying this cell costs, and what selling it returns. The exporter
    # once shipped only the sell side under the name `item_value`, which reads
    # like an asking price and is not one: a run declared 300 for Bread on that
    # number and was charged 549. Live-confirmed 2026-07-30 - a Greenfruit
    # carrying item_base_value 33 debited exactly 33.
    #
    # Both are relative to where the item currently sits, because Kenshi prices
    # from the owner's side. That same Greenfruit reports 30 once it is in the
    # player's own inventory: the trader's markup is in the shop cell's number
    # and not in ours. So these are not intrinsic item properties and two cells
    # holding "the same" item may legitimately disagree - reconciling them
    # would replace the price you will be charged with an average nobody
    # quotes.
    item_base_value: int | None = None
    item_sell_value: int | None = None
    item_quantity: int | None = Field(default=None, ge=0)
    item_type: int | None = None
    # Kenshi's own `Character::hasRoomForItem` verdict for the current primary
    # selected character and this exact item type. This is stronger than free
    # cell count or visual movement: inventory capacity is rectangular, and
    # ARRANGE may shuffle icons without changing whether the item fits.
    selected_inventory_accepts_item: bool | None = None
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


RUNTIME_OWNED_TIME_WIDGETS = frozenset(
    {
        "TimeSpeedButton1",
        "TimeSpeedButton2",
        "TimeSpeedButton3",
        "TimeSpeedButton4",
    }
)


def is_runtime_owned_visible_control(control: VisibleUIControl) -> bool:
    """Whether a visible widget is mechanics owned by a semantic option.

    Kenshi's time buttons are caption-less, so the exporter advertises their
    opaque MyGUI instance names as labels. They are real visible controls, but
    they are not independent gameplay intentions: monitored options own the
    corresponding pause, playback, observation, and terminal cleanup. Keep the
    widget identity out of the generic action surface even when an older
    snapshot carries it only in ``label`` rather than ``widget_name``.
    """

    return any(
        layout_widget_name_of(name) in RUNTIME_OWNED_TIME_WIDGETS
        for name in (control.widget_name, control.label)
        if name
    )


class ToolTipLine(StrictModel):
    """One tooltip row as the game stores it: a label and its value.

    Kenshi's `ToolTipLine` keeps the two in separate boxes - "Value" / "c.5,165"
    - so a price is a lookup rather than a parse.
    """

    label: str = Field(default="", max_length=200)
    value: str = Field(default="", max_length=200)


class ContextMenuProbe(StrEnum):
    """What the native sampler could prove about Kenshi's context menu."""

    CLOSED = "closed"
    CAPTURED = "captured"
    INVALID_TARGET = "invalid_target"


class RuntimeContextMenu(StrictModel):
    """Exact game-owned menu orders, distinct from reviewed action authority.

    ``task_type_values`` deliberately retains numeric values that the pinned
    TaskType vocabulary does not yet name. Observing a menu item must never
    make it executable; only ``WorldTarget.context_actions`` carries that
    separately reviewed authority.
    """

    target_id: str = Field(min_length=1, max_length=200)
    target_name: str | None = Field(default=None, max_length=200)
    task_type_values: list[int] = Field(default_factory=list, max_length=64)
    # False means the menu had more entries than the bounded native export.
    # Absence from task_type_values is usable as absence only when this is true.
    task_type_values_complete: bool


class InventorySlotItem(StrictModel):
    """One item in one inventory slot, with the coordinates that name it.

    `x`/`y` are not decoration. Kenshi's own transfer takes a section name and a
    slot, so the slot *is* the item's address to the engine - unlike a cell
    label scraped off a MyGUI widget, which addresses a picture of it.
    """

    item_name: str = Field(max_length=200)
    item_sell_value: int
    item_base_value: int
    item_quantity: int = Field(ge=0)
    item_type: int
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=0)
    h: int = Field(ge=0)


class InventorySectionView(StrictModel):
    """One named grid within an inventory."""

    name: str = Field(max_length=80)
    # Worn or wielded rather than carried. Kenshi transfers an equipped item by
    # a different path, and calling its transfer on one crashed the game.
    equipped: bool = False
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    items: list[InventorySlotItem] = Field(default_factory=list, max_length=128)


class OpenInventory(StrictModel):
    """One inventory Kenshi currently has open, whatever owns it.

    Read from `ForgottenGUI::inventoryWindowsOpen`, the engine's own map of
    owner handle to window, which holds no opinion about what kind of thing the
    owner is. The field it replaces read one of four typed slots and saw only
    buildings, so a looted body's window had no exported owner at all: the agent
    ordered looting, Kenshi opened the window, and nothing told the agent an
    inventory was there or what was in it.

    A body, a crate, a shop and a squadmate all arrive here the same way, which
    is what lets one transfer serve looting, buying, giving and harvesting
    without knowing which it is looking at.
    """

    owner_id: str = Field(max_length=200)
    owner_name: str = Field(default="", max_length=200)
    owner_kind: Literal["character", "building", "item", "unknown"]
    player_owned: bool
    money: int
    total_weight: float
    sections: list[InventorySectionView] = Field(default_factory=list, max_length=16)
    # Kenshi's own `isWithinRangeToTrade` between this window and the selected
    # character. An open window is not a reachable one: a trade window opened
    # against a shopkeeper across town shows two full inventories and refuses
    # every transfer, and the only signal was `OUT_OF_RANGE` after the attempt.
    # None means the engine could not be asked, which is silence, not a denial.
    within_trade_range: bool | None = None


class UIState(StrictModel):
    active_screen: str | None = None
    modal_open: bool | None = None
    # Kenshi's own `InventoryGUI::getNPCTrader()`: who the player is trading
    # with for money, or None. This is the switch a transfer uses to decide
    # between the engine's priced adjudicator and a plain inventory move, and
    # it is exported because a switch that silently reads None would move a
    # shopkeeper's goods for free.
    shop_trader_name: str | None = Field(default=None, max_length=200)
    dialogue_open: bool | None = None
    dialogue_target_id: str | None = None
    dialogue_options: list[str] | None = None
    tooltip_visible: bool | None = None
    # Why `tooltip_visible` is what it is. A bare false folded "nothing to look
    # at", "the pointer held nothing", "reading it faulted" and "hidden" into
    # one value, and a tooltip sensor aimed at the wrong object read false for
    # a thousand observations with nothing able to say so.
    tooltip_probe: str | None = Field(default=None, max_length=20)
    tooltip_text: str | None = None
    # The game builds a tooltip row from two captions, so the pairs survive
    # instead of being flattened into prose a consumer has to regex.
    tooltip_lines: list[ToolTipLine] | None = Field(default=None, max_length=64)
    tooltip_source_bounds: NormalizedPointerBounds | None = None
    # What this trader charges over an item's base value. Observed at 1.0 on
    # the live Barman, where the charge equalled `item_base_value` exactly, so
    # nothing yet distinguishes "the multiplier is applied" from "it is always
    # one" - the field is carried rather than trusted.
    trader_price_multiplier: float | None = Field(default=None, ge=0.0, le=100.0)
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
    # Every open inventory, by owner. `context_inventory_target_id` above reads
    # only Kenshi's Building slot, so it stays for the resource route it already
    # serves and this is what anything general asks.
    open_inventories: list[OpenInventory] = Field(default_factory=list, max_length=8)
    # A bounded export is not an empty world. False means the count stopped, not
    # that nothing else was open.
    open_inventories_complete: bool = True
    context_menu_open: bool | None = None
    # A visible menu can outlive its target or briefly overlap a delayed old
    # menu. Keep that distinction instead of flattening every failed read to
    # an empty order list, which would falsely mean "this target has no menu."
    context_menu_probe: ContextMenuProbe | None = None
    context_menu: RuntimeContextMenu | None = None
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
    # Selected entities Kenshi will deliver an order to but that cannot be named
    # to the planner: no stable id, not a player character, or absent from the
    # current squad. Empty is the normal case. A non-empty value means the
    # command validator is comparing against more members than were authorized,
    # which it refuses - correctly, since the extra one is a real recipient.
    selected_character_ids_withheld: list[str] = Field(default_factory=list, max_length=64)
    # Diagnostic: each selection entry's stored handle beside the same
    # character's current handle. A `hand` carries its container, so a character
    # who changes platoon gets a new one and any selection captured before the
    # move keeps the old coordinate. Free-form because it is for reading, not
    # for deciding: nothing branches on it.
    selection_handle_audit: list[str] = Field(default_factory=list, max_length=64)
    client_width: int | None = Field(default=None, gt=0)
    client_height: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def runtime_context_menu_is_internally_consistent(self) -> UIState:
        require_consistent_context_menu_state(
            context_menu_open=self.context_menu_open,
            context_menu_probe=self.context_menu_probe,
            context_menu=self.context_menu,
        )
        return self


class NativeCommandStatus(StrEnum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


# Wire shape, classified once for both directions of the protocol.
#
# The request schema and the acknowledgement schema each carried a full copy of
# this - which command names a target, which carries a direction, which is
# parameterless - so a new command had to be taught to both. survey_local_
# resources was taught to one, and the acknowledgement copy then refused to read
# back a command the plug-in had already executed. Because that is the readback
# path, one unexpected acknowledgement invalidated the whole telemetry snapshot.
#
# These answer shape only. Recipient scope belongs to the operation registry and
# is never inferred from a command name here.
NATIVE_COMMANDS_CARRYING_DIRECTION: frozenset[str] = frozenset({"move_in_direction"})

NATIVE_COMMANDS_NAMING_THEIR_OWN_RECIPIENT: frozenset[str] = frozenset(
    {"shift_into_body"}
)
"""Commands whose recipient is the target they name, not the current selection.

Everything else broadcasts to whoever is selected, so an empty selection means
an order with nobody to receive it and is refused. A body shift is the opposite:
it names the character it acts on, and the case it exists for is precisely the
one where nothing is selected because every character is dead. A blanket
non-empty floor would make the recovery unreachable at exactly the moment it is
the whole point.
"""

# Commands whose meaning is incomplete without naming which action to take.
# `perform_context_action` names a reviewed semantic ("operate", "first_aid");
# `perform_character_order` names one of Kenshi's own task names, which is why
# the field is shared rather than duplicated: both answer "which of this
# target's advertised actions".
NATIVE_TRANSFER_WIRE_COMMAND = "transfer_item"
NATIVE_TRADE_WINDOW_WIRE_COMMAND = "open_trade_window"

# Commands addressed by two parties rather than one.
NATIVE_COMMANDS_NAMING_TWO_PARTIES: frozenset[str] = frozenset(
    {NATIVE_TRANSFER_WIRE_COMMAND, NATIVE_TRADE_WINDOW_WIRE_COMMAND}
)

NATIVE_COMMANDS_NAMING_AN_ACTION: frozenset[str] = frozenset(
    {
        "perform_context_action",
        "perform_character_order",
        # Kenshi's own TradeWindowType - money_trading, looting, auto - rides in
        # the action field, so a pairing always says which kind it is.
        "open_trade_window",
    }
)

NATIVE_COMMANDS_NAMING_A_TARGET: frozenset[str] = frozenset(
    {
        "approach_confirmed_vendor",
        "move_to_character",
        "select_squad_member",
        "regroup_with_squad_member",
        "travel_to_map_destination",
        "perform_context_action",
        # Names the exact person the order is issued against.
        "perform_character_order",
        # Kenshi's TradeWindowType rides in the action field.
        "open_trade_window",
        "produce_resource_output",
        # Names the source inventory's owner. The destination and the slot ride
        # in their own fields, because a transfer is addressed by both ends.
        "transfer_item",
        # Diagnostic probe: names the exact body to move between platoons.
        "shift_body_platoon",
        # Names the exact body to become.
        "shift_into_body",
    }
)


def require_consistent_wire_shape(
    *,
    command: str,
    subject: str,
    selected_character_ids: list[str],
    target_id: str,
    bearing_degrees: float,
    distance_units: float,
    context_action: str,
    minimum_output_quantity: int,
    destination_id: str = "",
    section_name: str = "",
) -> None:
    """Reject a native request or acknowledgement whose fields contradict it."""

    if command not in NATIVE_COMMANDS_NAMING_THEIR_OWN_RECIPIENT and not selected_character_ids:
        raise ValueError(
            f"a {command} {subject} must name at least one selected recipient"
        )
    if command in NATIVE_COMMANDS_CARRYING_DIRECTION:
        if target_id:
            raise ValueError(f"a directional {subject} must not name a target")
        if distance_units <= 0.0:
            raise ValueError(f"a directional {subject} requires a distance")
    elif command in NATIVE_COMMANDS_NAMING_A_TARGET:
        if not target_id:
            raise ValueError(f"this native {subject} requires a target")
        if bearing_degrees != 0.0 or distance_units != 0.0:
            raise ValueError(f"a targeted {subject} must not carry direction fields")
    else:
        # Parameterless: it names neither a target nor a direction.
        if target_id:
            raise ValueError(f"a {command} {subject} must not name a target")
        if bearing_degrees != 0.0 or distance_units != 0.0:
            raise ValueError(f"a {command} {subject} must not carry direction fields")
    if command in NATIVE_COMMANDS_NAMING_AN_ACTION:
        if not context_action:
            raise ValueError(f"a {command} {subject} requires its named action")
    elif context_action:
        raise ValueError(f"only a {subject} that names an action may carry one")
    # A transfer is addressed by both ends. Naming only the source would let one
    # request stand for "move this item somewhere", which is not a thing Kenshi
    # can be asked and not a thing an acknowledgement could be matched to.
    if command in NATIVE_COMMANDS_NAMING_TWO_PARTIES:
        if not destination_id:
            raise ValueError(f"a {command} {subject} requires a destination")
        if destination_id == target_id:
            raise ValueError(
                f"a {command} {subject} must name two different inventories"
            )
        if command == NATIVE_TRANSFER_WIRE_COMMAND and not section_name:
            raise ValueError(f"a {command} {subject} requires a source section")
    elif destination_id or section_name:
        raise ValueError(
            f"only a transfer {subject} may name a destination or a section"
        )
    if command != "produce_resource_output" and minimum_output_quantity != 1:
        raise ValueError("only resource production may request a larger output quantity")


# Every native command the plug-in accepts, defined once.
#
# This vocabulary was written out five times - the request schema, the
# acknowledgement schema, and three signatures in the Kenshi surface - so
# adding a command meant editing five lists and any miss failed somewhere far
# from the edit. Adding survey_local_resources to the request and not the
# acknowledgement meant the plug-in accepted and executed a command Python
# could not read back, and the readback failure invalidated the whole
# telemetry snapshot rather than one field.
NativeWireCommand = Literal[
    "approach_confirmed_vendor",
    "move_to_character",
    "select_squad_member",
    # Diagnostic probe only; no operation definition maps to it. See
    # docs/KENSHI_BODY_SHIFT_PLAN.md.
    "shift_body_platoon",
    "shift_into_body",
    "regroup_with_squad_member",
    "move_in_direction",
    "travel_to_map_destination",
    "exit_current_building",
    "perform_context_action",
    "perform_character_order",
    "produce_resource_output",
    # One transfer between two open inventories, whatever owns them. Kenshi
    # adjudicates it and says why not, so looting, buying, selling, giving and
    # harvesting are one command rather than five that each drive a mouse.
    "transfer_item",
    # Both inventories at once, typed by Kenshi's own TradeWindowType. The
    # single-window opener beside it shows a character's personal gear, which
    # is the stealing view and not a state a transfer can act in.
    "open_trade_window",
    # The clock. Kenshi owns it through `GameWorld::userPause` and
    # `GameWorld::setGameSpeed`, so these stopped being keystrokes -- and with
    # them the last gameplay use of the keyboard. They name no target, no
    # direction and no action; a pause carries a state and a speed carries a
    # multiplier. The safety pause keeps its key deliberately: a stop that
    # depends on the plug-in answering is not a stop.
    "pause",
    "set_speed",
    "survey_local_resources",
]


class NativeCommandAcknowledgement(StrictModel):
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    command: NativeWireCommand
    status: NativeCommandStatus
    reason: str = Field(min_length=1, max_length=200)
    # Targeted commands bind to one stable entity. Directional movement binds
    # to its bearing and distance instead and deliberately names no target.
    target_id: str = Field(default="", max_length=200)
    context_action: ContextActionKind | Literal[""] = ""
    bearing_degrees: float = Field(default=0.0, ge=0.0, lt=360.0)
    distance_units: float = Field(default=0.0, ge=0.0, le=2000.0)
    # Retained in the acknowledgement so an adopted resource-production
    # command cannot silently satisfy a later request for a larger yield.
    minimum_output_quantity: int = Field(default=1, ge=1, le=5)
    # A transfer names two inventories and one slot. `target_id` is the source
    # owner; these are the rest of the address. Kenshi's own `RClickAutoTrade`
    # takes a section name and an x/y, so a slot is how an item is named to the
    # engine - unlike a cell label scraped off a widget, which names a picture
    # of it.
    destination_id: str = Field(default="", max_length=200)
    section_name: str = Field(default="", max_length=80)
    slot_x: int = Field(default=0, ge=0)
    slot_y: int = Field(default=0, ge=0)
    selected_character_ids: list[str] = Field(min_length=1, max_length=64)
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
        if len(set(self.selected_character_ids)) != len(self.selected_character_ids):
            raise ValueError("native acknowledgement selection basis contains duplicates")
        require_consistent_wire_shape(
            command=self.command,
            subject="acknowledgement",
            selected_character_ids=self.selected_character_ids,
            target_id=self.target_id,
            bearing_degrees=self.bearing_degrees,
            distance_units=self.distance_units,
            context_action=str(self.context_action),
            minimum_output_quantity=self.minimum_output_quantity,
            destination_id=self.destination_id,
            section_name=self.section_name,
        )

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
    protocol_version: str = "1.16.0"
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
    # Whether `nearby_entities` is everyone, or where the scan stopped. The
    # sphere is genuinely unbounded, so a cap is honest; reporting a capped
    # result as the whole world is not. An agent cannot act on "nobody else is
    # near" and "we stopped counting" the same way.
    nearby_entities_complete: bool = True
    # `selection_orderable_tasks` was published here and has been removed. It
    # split the order question into "may this selection issue this order at
    # all" and "does the order apply to that target", because a wrong combined
    # answer was unattributable. One live snapshot settled it:
    # `isOrderValidForSelection` returned true for all 291 vocabulary entries,
    # so the selection half discriminates nothing and nothing consults it now.
    world_targets: list[WorldTarget] = Field(default_factory=list)
    # Every nearby object the discovery scan reached this snapshot, with what
    # Kenshi says it affords. Bounded and rotating, so this is a moving window
    # over the world rather than a complete inventory of it.
    discovered_objects: list[DiscoveredObject] = Field(
        default_factory=list,
        max_length=64,
    )
    # Whether discovery saw the categories out, or filled its budget. Each
    # category scan is bounded, and one that hit its limit has stopped looking
    # rather than finished looking.
    discovered_objects_complete: bool = True
    # The most recent survey, or none if this run has not run one. It persists
    # until another replaces it, so a later observation can still read what the
    # last survey found.
    prospect_survey: ProspectSurvey | None = None
    known_map_destinations: list[KnownMapDestination] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @model_validator(mode="after")
    def runtime_context_menu_capability_is_truthful(self) -> TelemetrySnapshot:
        require_truthful_context_menu_capability(
            capabilities=self.capabilities,
            context_menu_open=self.ui.context_menu_open,
            context_menu_probe=self.ui.context_menu_probe,
            context_menu=self.ui.context_menu,
        )
        return self

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
        if (
            len(squad_ids) != len(set(squad_ids))
            or len(nearby_ids) != len(set(nearby_ids))
            or len(world_target_ids) != len(set(world_target_ids))
            or set(squad_ids) & set(nearby_ids)
        ):
            raise ValueError("stable entity IDs must be unique within a snapshot")
        squad_id_set = set(squad_ids)
        nearby_id_set = set(nearby_ids)
        for target in self.world_targets:
            if target.kind == "squad_character":
                if target.id not in squad_id_set:
                    raise ValueError(
                        "squad_character world targets must refer to current squad IDs"
                    )
            elif target.id in squad_id_set or target.id in nearby_id_set:
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
