from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MemoryKind(StrEnum):
    FACT = "fact"
    EPISODE = "episode"
    COMMITMENT = "commitment"
    HYPOTHESIS = "hypothesis"


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
    faction: str | None = None
    disposition: Disposition = Disposition.UNKNOWN
    distance: float | None = None
    position: Vec3 | None = None
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


class UIState(StrictModel):
    active_screen: str | None = None
    modal_open: bool | None = None
    dialogue_open: bool | None = None
    dialogue_options: list[str] = Field(default_factory=list)
    context_menu_open: bool | None = None
    selected_character_id: str | None = None
    client_width: int | None = Field(default=None, gt=0)
    client_height: int | None = Field(default=None, gt=0)


class TelemetrySnapshot(StrictModel):
    protocol_version: str = "0.1.0"
    sequence: int = Field(default=0, ge=0)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = "unknown"
    capabilities: list[str] = Field(default_factory=list)
    game: GameState = Field(default_factory=GameState)
    camera: CameraState = Field(default_factory=CameraState)
    ui: UIState = Field(default_factory=UIState)
    squad: list[CharacterState] = Field(default_factory=list)
    nearby_entities: list[NearbyEntity] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


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


class NormalizedPointerBounds(StrictModel):
    min_x: float = Field(ge=0.0, le=1.0)
    max_x: float = Field(ge=0.0, le=1.0)
    min_y: float = Field(ge=0.0, le=1.0)
    max_y: float = Field(ge=0.0, le=1.0)


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


class Observation(StrictModel):
    run_id: str
    step_index: int = Field(ge=0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mode: Literal["mock", "live", "replay"]
    telemetry: TelemetrySnapshot | None = None
    telemetry_stale: bool = False
    telemetry_age_seconds: float | None = None
    screenshot_path: Path | None = None
    screenshot_sha256: str | None = None
    events: list[str] = Field(default_factory=list)
    objective: str | None = Field(default=None, max_length=1000)
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


class ActionReceipt(StrictModel):
    action: Action
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
