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
)


class ControlMode(StrEnum):
    INTERFACE_ONLY = "interface_only"
    NATIVE_ASSISTED = "native_assisted"


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


class ProduceResourceOutputAction(StrictModel):
    """Internal production phase retained until a bounded output yield exists."""

    kind: Literal["produce_resource_output"] = "produce_resource_output"
    target_id: str = Field(min_length=1, max_length=200)
    minimum_output_quantity: int = Field(default=1, ge=1, le=5)


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


class OpenTradeWindowAction(StrictModel):
    """Put two inventories side by side, in Kenshi's own terms.

    `showInventory` opens one window - a character's personal gear, the view a
    player gets for stealing - so a transfer opened that way has nothing to move
    between. `ForgottenGUI::showTradeWindow` takes both sides and a type, and
    that type enum is the engine stating that trading and looting are one
    mechanism with a flag rather than two problems.
    """

    kind: Literal["open_trade_window"] = "open_trade_window"
    first_owner_id: str = Field(min_length=1, max_length=200)
    second_owner_id: str = Field(min_length=1, max_length=200)
    # Kenshi's own TradeWindowType, minus TW_OFF which closes rather than opens.
    window_type: Literal["money_trading", "looting", "auto"] = "auto"


class CloseActiveInterfaceAction(StrictModel):
    """Return from the currently blocking interface to the world.

    The action deliberately names no widget, caption, or coordinate. Native
    code closes the interface families the controller can cause or observe and
    acknowledges success only after Kenshi reports no remaining dialogue,
    message box, Prospecting window, inventory pair, or ordinary GUI window.
    """

    kind: Literal["close_active_interface"] = "close_active_interface"


class ConfirmCharacterEditorAction(StrictModel):
    """Accept the character currently held in Kenshi's mandatory editor.

    Recruitment is not complete when dialogue closes: Kenshi opens this editor
    and withholds the new roster until its exact CONFIRM control is activated.
    Native code dispatches that game-owned semantic event without moving a
    pointer or naming a coordinate.
    """

    kind: Literal["confirm_character_editor"] = "confirm_character_editor"


class SelectDialogueOptionAction(StrictModel):
    """Choose one exact reply from the currently open conversation.

    The index is not trusted alone: the dialogue target and exact rendered
    caption travel with it, so a reply list that changes between observation
    and dispatch fails closed instead of selecting whatever moved into the old
    row.
    """

    kind: Literal["select_dialogue_option"] = "select_dialogue_option"
    dialogue_target_id: str = Field(min_length=1, max_length=200)
    option_index: int = Field(ge=0, le=63)
    option_text: str = Field(min_length=1, max_length=500)


class TransferItemAction(StrictModel):
    """Move one item between two open inventories, whatever owns them.

    Looting a body, buying from a shop, handing something to a squadmate and
    emptying a mining crate are one model-level move with different owners.
    The plug-in removes the item from the source `Inventory` and tries to add it
    to the destination. When a shop trade is open, the project applies a
    deliberately simplified price from `Item::getValueSingle`; it does not call
    `InventoryGUI::RClickAutoTrade` or inherit Kenshi's theft, faction-standing,
    stolen-goods, or haggling adjudication.

    The slot is the address. `InventorySection::getItemAt(x, y)` resolves the
    source item, so `(section_name, slot_x, slot_y)` names it to the model,
    unlike a cell label read off a widget, which names a picture of it.
    """

    kind: Literal["transfer_item"] = "transfer_item"
    # The inventory the item leaves. Both owners are named because a transfer is
    # addressed by both ends.
    source_owner_id: str = Field(min_length=1, max_length=200)
    destination_owner_id: str = Field(min_length=1, max_length=200)
    section_name: str = Field(min_length=1, max_length=80)
    slot_x: int = Field(ge=0, le=64)
    slot_y: int = Field(ge=0, le=64)
    # Copied from the observed slot so a moved or swapped item is refused rather
    # than transferred by position alone.
    item_name: str = Field(min_length=1, max_length=200)


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


class SurveyLocalResourcesAction(StrictModel):
    """Survey Kenshi's resource field where the selected character stands.

    The prospecting reading the game's own window is built from, returned as a
    grid rather than the single averaged number that window displays. That
    average is why a zone can report `Iron: 0` while two iron deposits sit in
    it: a discrete node covers a trivial fraction of the surveyed area. A grid
    keeps direction and distance.

    The planner supplies nothing. Where the character stands is the survey,
    which is what makes this an action with a cost rather than free perception.
    """

    kind: Literal["survey_local_resources"] = "survey_local_resources"


class ShiftIntoBodyAction(StrictModel):
    """Become one exact currently observed character.

    Control in Kenshi follows selection rather than roster membership - a
    character released from the active platoon keeps taking orders while absent
    from the squad menu - so entering a body means belonging to the player
    faction and being the selected primary. The body is placed in its own squad:
    inhabiting someone is not gaining a follower, and the bodies left behind
    stay their own unit rather than accumulating into a retinue of former hosts.

    This is the operation that makes a total party loss survivable. It is not
    restricted to that case, because an agent that can only change bodies while
    dying cannot practise the thing it will need to do while dying.
    """

    kind: Literal["shift_into_body"] = "shift_into_body"
    target_id: str = Field(min_length=1, max_length=200)


class PerformCharacterOrderAction(StrictModel):
    """Issue one order Kenshi already says this person affords.

    Deliberately one operation rather than a verb per gameplay action. The
    plug-in asks Kenshi to build the exact context menu it would show for the
    target while muting only the renderer. Which orders apply to a bandit, a
    corpse, or a downed squadmate is therefore the engine's judgment, arriving
    in telemetry as `advertised_tasks`. Attacking, looting, and first aid are
    then the same operation with a different name on it.

    The alternative -- one typed action per verb, each with its own eligibility
    fence -- means re-deriving in Python what the game already computed, and
    getting a new disposition gate wrong once per verb.

    `order` is the advertised task name, lowercased. The exact string that
    arrived in telemetry is the one that goes back out, so a command stays
    legible in a run bundle without a lookup table.
    """

    kind: Literal["perform_character_order"] = "perform_character_order"
    target_id: str = Field(min_length=1, max_length=200)
    order: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")


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
    SelectSquadMemberExactAction
    | ShiftIntoBodyAction
    | SurveyLocalResourcesAction
    | OpenTradeWindowAction
    | CloseActiveInterfaceAction
    | ConfirmCharacterEditorAction
    | SelectDialogueOptionAction
    | TransferItemAction
)
"""Reusable atomic game/UI operations materialized from affordances."""

CompositeRuntimeOperation: TypeAlias = (
    ApproachDialogueTargetAction
    | PerformContextAction
    | ProduceResourceOutputAction
    | PerformCharacterOrderAction
    | RespondToImmediateThreatAction
    | RegroupWithSquadMemberAction
    | MoveToCharacterAction
    | MoveInDirectionAction
    | TravelToMapDestinationAction
    | ExitCurrentBuildingAction
)
"""Executor-owned options that require continuous plan supervision."""

RuntimeSemanticOperation: TypeAlias = AtomicRuntimeOperation | CompositeRuntimeOperation
"""Every game/UI operation materialized from a continuous affordance."""

SemanticAction: TypeAlias = RuntimeSemanticOperation
"""Every typed game/UI intention in the operation registry."""

RuntimeAction: TypeAlias = RuntimeControlAction | RuntimeSemanticOperation
"""Executor operations that can appear in a continuously supervised plan."""

UnmonitoredRuntimeAction: TypeAlias = RuntimeControlAction | AtomicRuntimeOperation
"""Executor operations that do not require continuous option ownership."""

Action: TypeAlias = ControllerPrimitive | RuntimeAction
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)

SEMANTIC_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "approach_dialogue_target",
        "select_squad_member_exact",
        "perform_character_order",
        "perform_context_action",
        "produce_resource_output",
        "open_trade_window",
        "close_active_interface",
        "confirm_character_editor",
        "select_dialogue_option",
        "transfer_item",
        "respond_to_immediate_threat",
        "regroup_with_squad_member",
        "move_to_character",
        "move_in_direction",
        "travel_to_map_destination",
        "exit_current_building",
        "survey_local_resources",
        "shift_into_body",
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
