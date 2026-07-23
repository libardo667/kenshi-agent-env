from __future__ import annotations

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
    FOOD_PROCUREMENT_V1 = "food_procurement_v1"


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


class InterruptPolicy(StrEnum):
    CANCEL_ON_REFLEX = "cancel_on_reflex"


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
    name: str
    quantity: int = Field(default=1, ge=0)
    category: str | None = None
    charges: float | None = None
    stolen: bool | None = None


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
    hunger: float | None = None
    bleeding_rate: float | None = None
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
    talk_task_available: bool | None = None
    talk_task_probability: float | None = None
    faction: str | None = None
    disposition: Disposition = Disposition.UNKNOWN
    distance: float | None = None
    position: Vec3 | None = None
    camera_bearing_degrees: float | None = Field(default=None, ge=-180.0, le=180.0)
    screen_position: Vec2 | None = None
    visible: bool | None = None
    conscious: bool | None = None


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


class UIState(StrictModel):
    active_screen: str | None = None
    modal_open: bool | None = None
    dialogue_open: bool | None = None
    dialogue_target_id: str | None = None
    dialogue_options: list[str] | None = None
    tooltip_visible: bool | None = None
    tooltip_text: str | None = None
    tooltip_source_bounds: NormalizedPointerBounds | None = None
    context_menu_open: bool | None = None
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
    command: Literal["approach_confirmed_vendor"]
    status: NativeCommandStatus
    reason: str = Field(min_length=1, max_length=200)
    target_id: str = Field(min_length=1, max_length=200)
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
    protocol_version: str = "0.1.0"
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
        all_ids = squad_ids + nearby_ids
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


Action: TypeAlias = (
    NoopAction
    | StopAction
    | PauseAction
    | SetSpeedAction
    | WaitAction
    | KeyAction
    | HotkeyAction
    | MoveCursorAction
    | ClickAction
    | ScrollAction
    | SkillAction
)
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def new_command_id() -> str:
    return f"cmd-{uuid4().hex}"


def parse_action(value: Any) -> Action:
    return ACTION_ADAPTER.validate_python(value)


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


class MemoryRecord(StrictModel):
    id: int
    namespace: str
    run_id: str
    kind: MemoryKind
    content: str
    salience: float
    evidence: str | None = None
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


class CommandDispatchContext(StrictModel):
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    based_on_revision: WorldStateRevision


class NativeCommandRequest(StrictModel):
    schema_version: Literal["1.0"]
    command_id: str = Field(pattern=r"^cmd-[0-9a-f]{32}$")
    command: Literal["approach_confirmed_vendor"]
    control_mode: Literal[ControlMode.NATIVE_ASSISTED]
    identity_session_id: str = Field(min_length=1, max_length=200)
    based_on_revision: WorldStateRevision
    selected_character_ids: list[str] = Field(min_length=1, max_length=1)
    target_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_native_fences(self) -> NativeCommandRequest:
        if self.based_on_revision.telemetry_sequence is None:
            raise ValueError("native command basis requires a telemetry sequence")
        if len(set(self.selected_character_ids)) != 1:
            raise ValueError("native command requires exactly one selected character")
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
    TELEMETRY_UI_TOOLTIP_VISIBLE = "telemetry.ui.tooltip_visible"
    TELEMETRY_UI_TOOLTIP_TEXT = "telemetry.ui.tooltip_text"
    TELEMETRY_UI_CONTEXT_MENU_OPEN = "telemetry.ui.context_menu_open"
    TELEMETRY_UI_SELECTED_CHARACTER_ID = "telemetry.ui.selected_character_id"
    TELEMETRY_UI_SELECTED_CHARACTER_COUNT = "telemetry.ui.selected_character_count"
    TELEMETRY_ACTIVE_SHOP_TRADER_COUNT = "telemetry.active_shop_trader_count"
    TELEMETRY_NATIVE_CONTROL_AVAILABLE = "telemetry.native_control.available"
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
    TARGET_TALK_TASK_AVAILABLE = "target.talk_task_available"
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
    UI_MODAL_CAPABILITY = "ui.modal"
    UI_INVENTORY_CAPABILITY = "ui.inventory"
    UI_DIALOGUE_CAPABILITY = "ui.dialogue"
    UI_DIALOGUE_TARGET_CAPABILITY = "ui.dialogue.target"
    UI_DIALOGUE_OPTIONS_CAPABILITY = "ui.dialogue.options"
    UI_TOOLTIP_CAPABILITY = "ui.tooltip"
    NEARBY_CHARACTERS_CAPABILITY = "nearby.characters"
    NEARBY_VISIBLE_ENTITIES_CAPABILITY = "nearby.visible_entities"
    NEARBY_ROLES_CAPABILITY = "nearby.roles"
    NEARBY_SHOP_OWNERS_CAPABILITY = "nearby.shop_owners"
    CONTROL_APPROACH_VENDOR_CAPABILITY = "control.approach_vendor"
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
                raise ValueError("Capability conditions require path")
            if self.path not in _ALLOWED_CAPABILITY_PATHS:
                raise ValueError(f"Unsupported capability path: {self.path!r}")
            if self.target_id is not None:
                raise ValueError("Capability conditions do not accept target_id")
        elif self.path is not None or self.target_id is not None:
            raise ValueError("telemetry_fresh conditions do not accept path or target_id")
        if self.expected is None:
            raise ValueError(f"{self.operator.value} conditions require expected")
        if self.operator == ConditionOperator.CONTAINS and not isinstance(
            self.expected, str
        ):
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
    action: Action
    preconditions: list[Condition] = Field(min_length=1, max_length=12)
    success_conditions: list[Condition] = Field(min_length=1, max_length=12)
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
    replace_future_steps: list[PlanStep] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=1000)


class ActivePlanContext(StrictModel):
    plan_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,95}$")
    plan_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=1000)
    active_step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
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
    available_skills: list[str] = Field(default_factory=list)
    skill_specs: list[SkillSpec] = Field(default_factory=list)
    memories: list[MemoryRecord] = Field(default_factory=list)

    def planner_payload(self, *, max_chars: int = 24000) -> str:
        payload = self.model_dump(mode="json", exclude={"screenshot_path"})
        text = self.__class__.model_validate(payload).model_dump_json(indent=2)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 120] + '\n... "observation_truncated": true\n'


class PlannerDecision(StrictModel):
    intent: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1500)
    action: Action
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_observation: str | None = Field(default=None, max_length=1000)
    memory_writes: list[MemoryWrite] = Field(default_factory=list, max_length=6)


PlannerOutput: TypeAlias = PlannerDecision | PlanEnvelope | PlanPatch


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
