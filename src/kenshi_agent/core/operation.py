"""Operation domain types."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypeAlias

from pydantic import (
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .base import StrictModel
from .telemetry import (
    ContextActionKind,
    NormalizedPointerBounds,
)


class ControlMode(StrEnum):
    INTERFACE_ONLY = "interface_only"
    NATIVE_ASSISTED = "native_assisted"


class PlanningMode(StrEnum):
    SINGLE_STEP = "single_step"
    CONTINUOUS = "continuous"


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


class SelectSquadMemberExactAction(StrictModel):
    """Select one exact squad member through native stable identity."""

    kind: Literal["select_squad_member_exact"] = "select_squad_member_exact"
    target_id: str = Field(min_length=1, max_length=200)


class GameScreen(StrEnum):
    """A screen the agent wants open, named by what it is rather than a key."""

    INVENTORY = "inventory"
    STATS = "stats"
    MAP = "map"
    RESEARCH = "research"
    CRAFTING = "crafting"


# Which management tab index each screen occupies, measured live in
# live-management-tabs-20260729-r3 and r4 rather than assumed: map 0,
# research 2, crafting 3, and -1 whenever the window is closed. Map, research
# and crafting share one window, so the tab index is the only thing that
# distinguishes "research opened" from "some management screen opened".
MANAGEMENT_TAB_INDICES: dict[GameScreen, int] = {
    GameScreen.MAP: 0,
    GameScreen.RESEARCH: 2,
    GameScreen.CRAFTING: 3,
}
MANAGEMENT_TAB_CLOSED = -1


# `use_game_binding` makes the planner name a mechanism when what it has is an
# intent: "I need the inventory" became `toggle_inventory` plus a hand-authored
# causal condition proving it opened. Verifying that pressing I opened the
# inventory is a mechanical fact about Kenshi, not a strategic choice, and the
# controller owns mechanical facts.
#
# This also promises the screen is *open*, which a toggle cannot: pressing the
# binding when the screen is already up closes it, so an agent that wanted the
# inventory and pressed I twice ends with no inventory and a receipt saying
# something changed both times.
class OpenScreenAction(StrictModel):
    """Have a named screen open. Prefer this over pressing its key.

    Idempotent: succeeds if the screen is already open.
    """

    kind: Literal["open_screen"] = "open_screen"
    screen: GameScreen


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


class ThreatResponseStrategy(StrEnum):
    ENGAGE = "engage"
    WITHDRAW = "withdraw"


class RespondToImmediateThreatAction(StrictModel):
    """Choose whether one selected actor engages or withdraws from a threat.

    The runtime owns playback, an observed escape vector for withdrawal,
    continuous threat and squad-health observation, timeout, interruption, and
    the terminal pause. The planner chooses only the actor and gameplay policy.
    """

    kind: Literal["respond_to_immediate_threat"] = "respond_to_immediate_threat"
    actor_id: str = Field(min_length=1, max_length=200)
    strategy: ThreatResponseStrategy


class RegroupWithSquadMemberAction(StrictModel):
    """Bring the selected actor to one exact current squadmate."""

    kind: Literal["regroup_with_squad_member"] = "regroup_with_squad_member"
    actor_id: str = Field(min_length=1, max_length=200)
    target_id: str = Field(min_length=1, max_length=200)


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


# A model docstring becomes the schema's `description`, so every word here is
# static prompt text on a hard budget. Rationale that only a maintainer needs
# goes in comments like this one, which never reach the planner.
#
# `expected_price` is checked against the cell's `item_base_value` before any
# input is sent, rather than discovered from the debit afterwards. It used to be
# compared against the sell value - what the trader pays out - which made every
# spending gate advisory: one run declared 300 for Bread, was charged 549, and
# tripped nothing.
class PurchaseItemAction(StrictModel):
    """Acquire a bounded quantity of one item from exact seller-owned cells.

    `expected_price` is the nonnegative per-unit charge reported as buy_price.
    Zero is a real free acquisition, not missing price evidence. The controller
    rebinds interchangeable stock after each transfer and proves that carried
    gain exactly matches the quoted charge before attempting the next unit.
    """

    kind: Literal["purchase_item"] = "purchase_item"
    cell_label: str = Field(min_length=1, max_length=80)
    item_name: str = Field(min_length=1, max_length=200)
    expected_price: int = Field(ge=0)
    quantity: int = Field(default=1, ge=1, le=5)
    # Caption of the seller's own inventory window. A trade screen shows two
    # inventories whose cell ordinals run across both, so this is what says the
    # item being bought is the shop's rather than ours.
    window: str = Field(min_length=1, max_length=200)
    seller_id: str = Field(min_length=1, max_length=200)


class DismissScreenAction(StrictModel):
    """Close one exact currently open screen toward the world view.

    Exiting is as much a part of using an interface as entering it. Naming the
    screen and, where applicable, the owner window makes the action bind to
    observed state instead of blindly pressing a key and hoping. Named game
    screens close through their own toggle binding; trade windows close through
    their exact close box. It deliberately does not end an active conversation:
    dialogue must choose an exact visible closing reply.
    """

    kind: Literal["dismiss_screen"] = "dismiss_screen"
    expected_screen: GameScreen | Literal["dialogue", "trade"]
    # Caption of the window to close. Inventory and trade windows are closed by
    # their own close box, whose position is derived from the window's observed
    # rect rather than a calibrated screen coordinate. An empty named screen
    # uses its own exact toggle binding; an active dialogue target makes any
    # generic dismissal route fail closed.
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
    """Equip one item from an exact currently selected squad-owned window.

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


# The mirror of `purchase_item`: with only a purchase action the agent could
# spend its starting money and never earn any.
#
# It carries no expected price. That was originally because a shop's offer "is
# not exported" - no longer true, since cells now carry `item_sell_value`, the
# same number the in-game tooltip labels "Sell value". Adding a checked price
# here is real work, not a rename: nothing has yet confirmed that the proceeds
# of a sale equal that field the way a purchase's debit was confirmed to equal
# `item_base_value`. Until a live sale demonstrates it, asserting a price here
# would assert something unverified.
class SellItemAction(StrictModel):
    """Sell a bounded quantity from an exact squad-owned inventory window.

    Checked: the window resolves to one selected owner and its cell holds this
    item.
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


QUICKSAVE_COMPLETION_CAPABILITY = "host.quicksave_completion"
"""Controller can attribute F5 to one completed exact quicksave tree."""


class GameBinding(StrEnum):
    """Kenshi's named control intentions under the shipped default keymap.

    The current physical mapping is a hard-coded copy of the shipped
    `controls.cfg`; it is not read from the user's active keymap. Membership is
    the adapter-owned subset recorded by the game-binding parity ledger.
    """

    # Screens. Each is a toggle, so pressing twice returns to where it started.
    TOGGLE_INVENTORY = "toggle_inventory"
    TOGGLE_MAP = "toggle_map"
    TOGGLE_STATS = "toggle_stats"
    TOGGLE_HELP = "toggle_help"
    TOGGLE_CRAFTING = "toggle_crafting"
    TOGGLE_RESEARCH = "toggle_research"
    # Save-state control.
    QUICKSAVE = "quicksave"
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
    TOGGLE_BUILD = "toggle_build"
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
    TOGGLE_FPS_CAMERA = "toggle_fps_camera"
    # Development host controls.
    EDITOR_DELETE = "editor_delete"
    EDITOR_TOGGLE = "editor_toggle"
    REBUILD_NAVMESH = "rebuild_navmesh"
    RELOAD_BIOMES = "reload_biomes"
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
    SELECT_GROUP_0 = "select_0"
    SELECT_GROUP_1 = "select_1"
    SELECT_GROUP_2 = "select_2"
    SELECT_GROUP_3 = "select_3"
    SELECT_GROUP_4 = "select_4"
    SELECT_GROUP_5 = "select_5"
    SELECT_GROUP_6 = "select_6"
    SELECT_GROUP_7 = "select_7"
    SELECT_GROUP_8 = "select_8"
    SELECT_GROUP_9 = "select_9"
    # Orders.
    STOP_MOVEMENT = "stop_movement"
    MEDIC = "medic"
    RESCUE = "rescue"
    TOGGLE_HOLD = "toggle_hold"
    TOGGLE_BLOCK = "toggle_block"
    TOGGLE_BAR = "toggle_bar"
    TOGGLE_PASSIVE = "toggle_passive"
    TOGGLE_RANGED = "toggle_ranged"
    TOGGLE_SNEAK = "toggle_sneak"
    TOGGLE_TAUNT = "toggle_taunt"


GAME_BINDING_KEYS: dict[GameBinding, str | tuple[str, ...]] = {
    GameBinding.TOGGLE_INVENTORY: "i",
    GameBinding.TOGGLE_MAP: "m",
    GameBinding.TOGGLE_STATS: "c",
    GameBinding.TOGGLE_HELP: "f1",
    GameBinding.TOGGLE_CRAFTING: "y",
    GameBinding.TOGGLE_RESEARCH: "t",
    GameBinding.QUICKSAVE: "f5",
    GameBinding.QUICKLOAD: "f9",
    GameBinding.BUILD_APPLY: "space",
    GameBinding.BUILD_MOVE_DOWN: "minus",
    GameBinding.BUILD_MOVE_UP: "equals",
    GameBinding.BUILD_ROTATE_LEFT: "comma",
    GameBinding.BUILD_ROTATE_RIGHT: "period",
    GameBinding.BUILD_TILT_DECREASE: "[",
    GameBinding.BUILD_TILT_INCREASE: "]",
    GameBinding.BUILD_UNDO: "backspace",
    GameBinding.TOGGLE_BUILD: "b",
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
    GameBinding.TOGGLE_FPS_CAMERA: "semicolon",
    GameBinding.EDITOR_DELETE: "delete",
    GameBinding.EDITOR_TOGGLE: ("shift", "f12"),
    GameBinding.REBUILD_NAVMESH: ("ctrl", "shift", "f11"),
    GameBinding.RELOAD_BIOMES: ("ctrl", "f6"),
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
    GameBinding.SELECT_GROUP_0: "1",
    GameBinding.SELECT_GROUP_1: "2",
    GameBinding.SELECT_GROUP_2: "3",
    GameBinding.SELECT_GROUP_3: "4",
    GameBinding.SELECT_GROUP_4: "5",
    GameBinding.SELECT_GROUP_5: "6",
    GameBinding.SELECT_GROUP_6: "7",
    GameBinding.SELECT_GROUP_7: "8",
    GameBinding.SELECT_GROUP_8: "9",
    GameBinding.SELECT_GROUP_9: "0",
    GameBinding.STOP_MOVEMENT: "r",
    GameBinding.MEDIC: "numpad7",
    GameBinding.RESCUE: "numpad8",
    GameBinding.TOGGLE_HOLD: "numpad1",
    GameBinding.TOGGLE_BLOCK: "numpad0",
    GameBinding.TOGGLE_BAR: "f7",
    GameBinding.TOGGLE_PASSIVE: "numpad2",
    GameBinding.TOGGLE_RANGED: "numpad3",
    GameBinding.TOGGLE_SNEAK: "numpad4",
    GameBinding.TOGGLE_TAUNT: "numpad5",
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
        GameBinding.TOGGLE_BUILD,
        GameBinding.TOGGLE_FPS_CAMERA,
        GameBinding.MEDIC,
        GameBinding.RESCUE,
        GameBinding.TOGGLE_HOLD,
        GameBinding.TOGGLE_BLOCK,
        GameBinding.TOGGLE_BAR,
        GameBinding.TOGGLE_PASSIVE,
        GameBinding.TOGGLE_RANGED,
        GameBinding.TOGGLE_SNEAK,
        GameBinding.TOGGLE_TAUNT,
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
"""Low-level time keys reserved for runtime-owned option mechanics."""


# The agent kept trying to reach screens by hunting for a widget to click -
# clicking the time-speed buttons to unpause, clicking around the world hoping
# an inventory would appear - because nothing in the catalog could simply open a
# screen. Kenshi already binds all of it. Naming the *binding* rather than the
# key keeps the intention readable and the default mapping in one place;
# customized keymaps are not yet read. The enum is a semantic vocabulary, not an
# escape hatch to arbitrary keys. Time keys stay controller details behind
# monitored semantic options.
class UseGameBindingAction(StrictModel):
    """Press one named Kenshi control through the shipped-default keymap.

    Prefer `open_screen` for reaching a screen; a binding only toggles.
    """

    kind: Literal["use_game_binding"] = "use_game_binding"
    binding: GameBinding
    # Runtime-authored audit label. The playing model selects the offered
    # binding and never writes this private operation field.
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

These remain the only way input actually reaches Windows, but they are never an
offered gameplay intention: a raw coordinate carries no evidence about what it
would activate. The affordance surface never advertises them.
"""

RuntimeControlAction: TypeAlias = (
    NoopAction
    | StopAction
    | PauseAction
    | SetSpeedAction
    | WaitAction
    | ConsultAdvisorAction
    | RecallMemoryAction
    | ReadFieldbookAction
)
"""Runtime intentions that touch no game object and bind to no reference."""

AtomicRuntimeOperation: TypeAlias = (
    ApproachDialogueTargetAction
    | CommandWorldTargetAction
    | SelectSquadMemberAction
    | SelectSquadMemberExactAction
    | RotateCameraAction
    | MoveToCharacterAction
    | MoveInDirectionAction
    | TravelToMapDestinationAction
    | ExitCurrentBuildingAction
    | ActivateVisibleControlAction
    | DismissScreenAction
    | PurchaseItemAction
    | OpenScreenAction
    | UseGameBindingAction
    | ScrollScreenAction
    | SellItemAction
    | EquipItemAction
    | RecoverCameraViewAction
)
"""Reusable atomic game/UI operations materialized from affordances."""

CompositeRuntimeOperation: TypeAlias = (
    HarvestResourceAction | RespondToImmediateThreatAction | RegroupWithSquadMemberAction
)
"""Executor-owned options that require continuous plan supervision."""

RuntimeSemanticOperation: TypeAlias = AtomicRuntimeOperation | CompositeRuntimeOperation
"""Every game/UI operation materialized from a continuous affordance."""

InternalRuntimeOperation: TypeAlias = (
    PerformContextAction
    | ProduceResourceOutputAction
    | OpenContextInventoryAction
    | CollectResourceOutputAction
)
"""Controller-owned phases used only inside larger semantic options."""

SemanticAction: TypeAlias = RuntimeSemanticOperation | InternalRuntimeOperation
"""Every typed game/UI intention, including controller-owned phases."""

RuntimeAction: TypeAlias = RuntimeControlAction | RuntimeSemanticOperation | SkillAction
"""Executor operations that can appear in a continuously supervised plan."""

SingleStepRuntimeAction: TypeAlias = RuntimeControlAction | AtomicRuntimeOperation | SkillAction
"""Executor operations that do not require continuous option ownership."""

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
    | SelectSquadMemberExactAction
    | RotateCameraAction
    | PerformContextAction
    | ProduceResourceOutputAction
    | OpenContextInventoryAction
    | HarvestResourceAction
    | RespondToImmediateThreatAction
    | RegroupWithSquadMemberAction
    | MoveToCharacterAction
    | MoveInDirectionAction
    | TravelToMapDestinationAction
    | ExitCurrentBuildingAction
    | ActivateVisibleControlAction
    | DismissScreenAction
    | PurchaseItemAction
    | OpenScreenAction
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
        "select_squad_member_exact",
        "rotate_camera",
        "perform_context_action",
        "produce_resource_output",
        "open_context_inventory",
        "harvest_resource",
        "respond_to_immediate_threat",
        "regroup_with_squad_member",
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


RUNTIME_CONTROL_ACTION_KINDS: frozenset[str] = frozenset(
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


def is_runtime_control_action(action: Action) -> bool:
    """Runtime control that touches no game object and binds to no reference."""

    return action.kind in RUNTIME_CONTROL_ACTION_KINDS


def parse_action(value: Any) -> Action:
    return ACTION_ADAPTER.validate_python(value)


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
