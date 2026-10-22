"""Planning domain types."""

from __future__ import annotations

from collections.abc import Mapping
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
    Action,
    ControlMode,
    IdempotencyPolicy,
    InterruptPolicy,
    ObservationPolicy,
    UnmonitoredRuntimeAction,
)
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
    TELEMETRY_UI_PROSPECTING_WINDOW_OPEN = "telemetry.ui.prospecting_window_open"
    TELEMETRY_UI_OPEN_INVENTORY_WINDOWS = "telemetry.ui.open_inventory_windows"
    TELEMETRY_UI_MANAGEMENT_SCREEN_OPEN = "telemetry.ui.management_screen_open"
    TELEMETRY_UI_MANAGEMENT_TAB = "telemetry.ui.management_tab"
    TELEMETRY_UI_TOOLTIP_VISIBLE = "telemetry.ui.tooltip_visible"
    TELEMETRY_UI_TOOLTIP_TEXT = "telemetry.ui.tooltip_text"
    TELEMETRY_UI_CONTEXT_MENU_OPEN = "telemetry.ui.context_menu_open"
    TELEMETRY_UI_SELECTED_CHARACTER_ID = "telemetry.primary_character_id"
    TELEMETRY_SELECTED_CHARACTER_COUNT = "telemetry.selected_character_count"
    TELEMETRY_ACTIVE_SHOP_TRADER_COUNT = "telemetry.active_shop_trader_count"
    TELEMETRY_CONTROLLER_COMMANDS_AVAILABLE = "telemetry.controller_commands.available"
    TELEMETRY_CONTROLLER_COMMANDS_COMMAND_ACTIVE = "telemetry.controller_commands.command_active"
    TELEMETRY_CONTROLLER_COMMANDS_LAST_COMMAND_SEQUENCE = (
        "telemetry.controller_commands.last_command_sequence"
    )
    TELEMETRY_CONTROLLER_COMMANDS_LAST_COMMAND = "telemetry.controller_commands.last_command"
    TELEMETRY_CONTROLLER_COMMANDS_LAST_RESULT = "telemetry.controller_commands.last_result"
    TELEMETRY_CONTROLLER_COMMANDS_LAST_TARGET = "telemetry.controller_commands.last_target"
    TELEMETRY_CONTROLLER_COMMANDS_LAST_TARGET_ID = "telemetry.controller_commands.last_target_id"
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
