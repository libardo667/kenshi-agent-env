"""Planning domain types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, TypeAlias

from pydantic import (
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from .affordance import BoundAffordance
from .base import StrictModel
from .continuity import (
    ContinuityOperation,
    FieldbookOperation,
)
from .operation import (
    MANAGEMENT_TAB_INDICES,
    Action,
    ControlMode,
    GameBinding,
    GameScreen,
    IdempotencyPolicy,
    InterruptPolicy,
    ObservationPolicy,
    UnmonitoredRuntimeAction,
)
from .telemetry import TelemetrySnapshot
from .world import WorldStateRevision


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
    SELECTED_NUTRITION_RESERVE = "selected.nutrition_reserve"
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


class BindingWitness(StrEnum):
    """How a later observation proves one binding actually landed."""

    # Any change in the field proves it: a tab index that moved, a session that
    # rotated, a speed that is no longer what it was.
    CHANGED = "changed"
    # A boolean that must end up as the opposite of what it was.
    TOGGLED = "toggled"


@dataclass(frozen=True, slots=True)
class BindingTerminal:
    path: FieldConditionPath
    witness: BindingWitness
    required_capabilities: tuple[str, ...] = ()


# Which observation proves each binding landed. Declarative rather than a chain
# of hand-written branches, because only four of sixty-eight bindings had one:
# every other binding was wired, counted as parity coverage, and then rejected
# at plan validation with "has no causal success condition". A binding with no
# entry here and no entry below fails a test rather than silently arriving
# unusable.
GAME_BINDING_TERMINALS: dict[GameBinding, BindingTerminal] = {
    GameBinding.QUICKLOAD: BindingTerminal(
        FieldConditionPath.TELEMETRY_IDENTITY_SESSION_ID,
        BindingWitness.CHANGED,
        ("identity.stable_handles",),
    ),
    GameBinding.TOGGLE_INVENTORY: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_OPEN_INVENTORY_WINDOWS,
        BindingWitness.CHANGED,
    ),
    GameBinding.TOGGLE_STATS: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_STATS_WINDOW_OPEN,
        BindingWitness.TOGGLED,
    ),
    # Map, research and crafting are tabs of one management window, so the tab
    # index moves whether the window opens, switches tab, or closes. Watching
    # `management_screen_open` instead cannot see a switch between two tabs.
    GameBinding.TOGGLE_MAP: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_MANAGEMENT_TAB,
        BindingWitness.CHANGED,
    ),
    GameBinding.TOGGLE_RESEARCH: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_MANAGEMENT_TAB,
        BindingWitness.CHANGED,
    ),
    GameBinding.TOGGLE_CRAFTING: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_MANAGEMENT_TAB,
        BindingWitness.CHANGED,
    ),
    # Squad selection is witnessed by who ends up selected.
    GameBinding.SELECT_ALL: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_COUNT,
        BindingWitness.CHANGED,
        ("identity.stable_handles", "squad.basic"),
    ),
    GameBinding.CHARACTER_NEXT: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_ID,
        BindingWitness.CHANGED,
    ),
    GameBinding.CHARACTER_PREV: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_ID,
        BindingWitness.CHANGED,
    ),
    GameBinding.CHANGE_SQUAD: BindingTerminal(
        FieldConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_ID,
        BindingWitness.CHANGED,
    ),
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
            raise ValueError("required_capabilities accepts capability names, not field paths")
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


# Bindings nothing in telemetry can witness. Pressing them works; proving they
# worked is impossible for the runtime AND for the model, so neither can author
# a causal condition. Each needs a native export before it becomes usable, which
# is different work from wiring a condition to a signal that already exists.
UNWITNESSED_BINDINGS: dict[GameBinding, str] = {
    GameBinding.TOGGLE_BUILD: "Build mode has no exported state.",
    GameBinding.BUILD_APPLY: "Build mode has no exported state.",
    GameBinding.BUILD_UNDO: "Build mode has no exported state.",
    GameBinding.BUILD_MOVE_UP: "Build mode has no exported state.",
    GameBinding.BUILD_MOVE_DOWN: "Build mode has no exported state.",
    GameBinding.BUILD_ROTATE_LEFT: "Build mode has no exported state.",
    GameBinding.BUILD_ROTATE_RIGHT: "Build mode has no exported state.",
    GameBinding.BUILD_TILT_INCREASE: "Build mode has no exported state.",
    GameBinding.BUILD_TILT_DECREASE: "Build mode has no exported state.",
    GameBinding.FLOOR_UP: "The active building floor is not exported.",
    GameBinding.FLOOR_DOWN: "The active building floor is not exported.",
    GameBinding.GIZMO_MOVE: "Editor gizmo mode is not exported.",
    GameBinding.GIZMO_ROTATE: "Editor gizmo mode is not exported.",
    GameBinding.GIZMO_SCALE: "Editor gizmo mode is not exported.",
    GameBinding.EDITOR_TOGGLE: "Editor mode is not exported.",
    GameBinding.EDITOR_DELETE: "Editor mode is not exported.",
    GameBinding.REBUILD_NAVMESH: "World-data rebuilds have no observable result.",
    GameBinding.RELOAD_BIOMES: "World-data reloads have no observable result.",
    GameBinding.TOGGLE_HELP: "The help window is not exported.",
    GameBinding.TOGGLE_HOLD: "Per-character stance is not exported.",
    GameBinding.TOGGLE_BLOCK: "Per-character stance is not exported.",
    GameBinding.TOGGLE_BAR: "Per-character stance is not exported.",
    GameBinding.TOGGLE_PASSIVE: "Per-character stance is not exported.",
    GameBinding.TOGGLE_RANGED: "Per-character stance is not exported.",
    GameBinding.TOGGLE_SNEAK: "Per-character stance is not exported.",
    GameBinding.TOGGLE_TAUNT: "Per-character stance is not exported.",
    GameBinding.TOGGLE_FPS_CAMERA: "Camera mode is not exported.",
    GameBinding.CAMERA_FORWARD: "Camera position is not exported as a condition path.",
    GameBinding.CAMERA_BACK: "Camera position is not exported as a condition path.",
    GameBinding.CAMERA_LEFT: "Camera position is not exported as a condition path.",
    GameBinding.CAMERA_RIGHT: "Camera position is not exported as a condition path.",
    GameBinding.CAMERA_ROTATE_LEFT: "Camera orientation is not exported.",
    GameBinding.CAMERA_ROTATE_RIGHT: "Camera orientation is not exported.",
    GameBinding.CAMERA_TILT_UP: "Camera orientation is not exported.",
    GameBinding.CAMERA_TILT_DOWN: "Camera orientation is not exported.",
    GameBinding.CAMERA_ZOOM_IN: "Camera zoom is not exported.",
    GameBinding.CAMERA_ZOOM_OUT: "Camera zoom is not exported.",
    GameBinding.FOCUS_CHAR: "Camera position is not exported as a condition path.",
    GameBinding.HIGHLIGHT: "The highlight overlay is not exported.",
    GameBinding.CYCLE_RUN_SPEED: "Run-speed mode is not exported.",
    GameBinding.STOP_MOVEMENT: (
        "No exported field distinguishes a stopped order from an idle one."
    ),
    GameBinding.MEDIC: "Squad job flags are not exported.",
    GameBinding.RESCUE: "Squad job flags are not exported.",
    GameBinding.PAUSE: "Time control belongs to PauseAction, which owns its own terminal.",
    GameBinding.SPEED_1: "Time control belongs to SetSpeedAction, which owns its own terminal.",
    GameBinding.SPEED_2: "Time control belongs to SetSpeedAction, which owns its own terminal.",
    GameBinding.SPEED_3: "Time control belongs to SetSpeedAction, which owns its own terminal.",
    GameBinding.QUICKSAVE: (
        "Completion is controller-owned through the save directory, not a field."
    ),
}
for _index in range(10):
    UNWITNESSED_BINDINGS[GameBinding[f"SELECT_GROUP_{_index}"]] = (
        "Squad group membership is not exported, so a group that is already "
        "selected cannot be distinguished from one that failed to select."
    )


def _binding_terminal_value(
    telemetry: TelemetrySnapshot,
    path: FieldConditionPath,
) -> ConditionScalar:
    """Read exactly the fields a binding terminal may watch.

    Deliberately narrow rather than reusing the planner's full path map, which
    lives in `planning` and cannot be imported here. A terminal naming a path
    this cannot read returns None and is caught by a test rather than silently
    producing no condition.
    """

    if path is FieldConditionPath.TELEMETRY_IDENTITY_SESSION_ID:
        return telemetry.identity_session_id
    if path is FieldConditionPath.TELEMETRY_UI_OPEN_INVENTORY_WINDOWS:
        return telemetry.ui.open_inventory_windows
    if path is FieldConditionPath.TELEMETRY_UI_STATS_WINDOW_OPEN:
        return telemetry.ui.stats_window_open
    if path is FieldConditionPath.TELEMETRY_UI_MANAGEMENT_TAB:
        return telemetry.ui.management_tab
    if path is FieldConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_ID:
        return telemetry.ui.selected_character_id
    if path is FieldConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_COUNT:
        return len(telemetry.ui.selected_character_ids)
    return None


def game_binding_success_condition(
    binding: GameBinding,
    telemetry: TelemetrySnapshot | None,
) -> Condition | None:
    """Describe the exact observable state one reversible binding must change.

    Derived from `GAME_BINDING_TERMINALS` rather than written per binding, so a
    newly wired binding inherits a condition instead of landing unusable. Four
    of sixty-eight had one when this was a chain of branches.
    """

    if telemetry is None:
        return None
    terminal = GAME_BINDING_TERMINALS.get(binding)
    if terminal is None:
        return None
    for capability in terminal.required_capabilities:
        if capability not in telemetry.capabilities:
            return None
    current = _binding_terminal_value(telemetry, terminal.path)
    if current is None:
        return None
    if binding is GameBinding.SELECT_ALL:
        return Condition(
            kind=ConditionKind.FIELD,
            path=terminal.path,
            operator=ConditionOperator.EQUALS,
            expected=len(telemetry.squad),
            max_age_seconds=3.0,
            required_capabilities=list(terminal.required_capabilities),
        )
    if terminal.witness is BindingWitness.TOGGLED:
        if not isinstance(current, bool):
            return None
        return Condition(
            kind=ConditionKind.FIELD,
            path=terminal.path,
            operator=ConditionOperator.EQUALS,
            expected=not current,
            max_age_seconds=3.0,
            required_capabilities=list(terminal.required_capabilities),
        )
    return Condition(
        kind=ConditionKind.FIELD,
        path=terminal.path,
        operator=ConditionOperator.NOT_EQUALS,
        expected=current,
        max_age_seconds=3.0,
        required_capabilities=list(terminal.required_capabilities),
    )


SCREEN_BINDINGS: dict[GameScreen, GameBinding] = {
    GameScreen.INVENTORY: GameBinding.TOGGLE_INVENTORY,
    GameScreen.STATS: GameBinding.TOGGLE_STATS,
    GameScreen.MAP: GameBinding.TOGGLE_MAP,
    GameScreen.RESEARCH: GameBinding.TOGGLE_RESEARCH,
    GameScreen.CRAFTING: GameBinding.TOGGLE_CRAFTING,
}


def screen_is_open(screen: GameScreen, telemetry: TelemetrySnapshot | None) -> bool | None:
    """Whether the exact named screen is up, or None when it cannot be read.

    The distinction matters more than it looks: pressing a toggle to "open" a
    screen that is already open closes it, so an agent that wanted the inventory
    and pressed I twice ends with no inventory and a receipt saying something
    changed both times.
    """

    if telemetry is None:
        return None
    if screen is GameScreen.INVENTORY:
        windows = telemetry.ui.open_inventory_windows
        return None if windows is None else windows > 0
    if screen is GameScreen.STATS:
        return telemetry.ui.stats_window_open
    tab = telemetry.ui.management_tab
    if tab is None:
        return None
    return tab == MANAGEMENT_TAB_INDICES[screen]


def open_screen_success_condition(
    screen: GameScreen,
    telemetry: TelemetrySnapshot | None,
) -> Condition | None:
    """The exact observation proving this screen, not merely some screen, is up."""

    if telemetry is None:
        return None
    if screen is GameScreen.INVENTORY:
        if telemetry.ui.open_inventory_windows is None:
            return None
        return Condition(
            kind=ConditionKind.FIELD,
            path=FieldConditionPath.TELEMETRY_UI_OPEN_INVENTORY_WINDOWS,
            operator=ConditionOperator.GREATER_THAN,
            expected=0,
            max_age_seconds=3.0,
        )
    if screen is GameScreen.STATS:
        if telemetry.ui.stats_window_open is None:
            return None
        return Condition(
            kind=ConditionKind.FIELD,
            path=FieldConditionPath.TELEMETRY_UI_STATS_WINDOW_OPEN,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=3.0,
        )
    if telemetry.ui.management_tab is None:
        return None
    return Condition(
        kind=ConditionKind.FIELD,
        path=FieldConditionPath.TELEMETRY_UI_MANAGEMENT_TAB,
        operator=ConditionOperator.EQUALS,
        expected=MANAGEMENT_TAB_INDICES[screen],
        max_age_seconds=3.0,
    )


def close_screen_success_condition(
    screen: GameScreen,
    telemetry: TelemetrySnapshot | None,
) -> Condition | None:
    """The exact observation proving one named screen is no longer open."""

    if telemetry is None:
        return None
    if screen is GameScreen.INVENTORY:
        if telemetry.ui.open_inventory_windows is None:
            return None
        return Condition(
            kind=ConditionKind.FIELD,
            path=FieldConditionPath.TELEMETRY_UI_OPEN_INVENTORY_WINDOWS,
            operator=ConditionOperator.EQUALS,
            expected=0,
            max_age_seconds=3.0,
        )
    if screen is GameScreen.STATS:
        if telemetry.ui.stats_window_open is None:
            return None
        return Condition(
            kind=ConditionKind.FIELD,
            path=FieldConditionPath.TELEMETRY_UI_STATS_WINDOW_OPEN,
            operator=ConditionOperator.EQUALS,
            expected=False,
            max_age_seconds=3.0,
        )
    if telemetry.ui.management_tab is None:
        return None
    return Condition(
        kind=ConditionKind.FIELD,
        path=FieldConditionPath.TELEMETRY_UI_MANAGEMENT_TAB,
        operator=ConditionOperator.NOT_EQUALS,
        expected=MANAGEMENT_TAB_INDICES[screen],
        max_age_seconds=3.0,
    )


class ConditionEvaluation(StrictModel):
    condition: Condition
    result: ConditionResult
    actual: ConditionScalar = None
    reason: str = Field(min_length=1, max_length=1000)


class RiskBudget(StrictModel):
    """What a plan may spend before a model sees the world again.

    Counting actions was a proxy for a hazard it cannot measure. The risk of a
    purchase is how much money leaves the purse, not how many clicks it took:
    live-hub-survival-pair-20260729-r3 took 632 cats to 83 in ONE purchase,
    while five purchases of a cheap item would be trivial. A count both refuses
    harmless plans and permits expensive ones.

    `max_spend` is the bound that matches the hazard, and it is enforced against
    the observed debit rather than the planner's declared price, because the
    shop's charge is never exported and a declaration cannot bound it.
    """

    max_pointer_actions: int = Field(ge=0, le=32)
    max_purchase_actions: int = Field(ge=0, le=8)
    max_native_assisted_actions: int = Field(ge=0, le=8)
    # Cats this plan may lose in total. Zero means the plan buys nothing, which
    # is the honest default for a plan that never intended to.
    max_spend: int = Field(default=0, ge=0, le=1_000_000)


def _unique_conditions(conditions: list[Condition]) -> list[Condition]:
    """Return one copy of each logical predicate, preserving authored order."""

    unique: list[Condition] = []
    for condition in conditions:
        if condition not in unique:
            unique.append(condition)
    return unique


class PlanStep(StrictModel):
    step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    # Runtime-private operation materialized from an exact current affordance.
    # Hosted planner schemas never expose this union.
    action: Action
    affordance: BoundAffordance | None = None
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


class PlannerDecision(StrictModel):
    intent: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1500)
    action: UnmonitoredRuntimeAction
    # Present for hosted play. Deterministic safety/reflex decisions remain
    # runtime-internal and therefore do not pretend to have been offered.
    affordance: BoundAffordance | None = None
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
