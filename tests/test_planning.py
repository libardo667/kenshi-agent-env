from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.condition_evaluation import evaluate_condition
from kenshi_agent.config import PlanningConfig
from kenshi_agent.core.evidence import (
    ActionOutcome,
    ActionOutcomeAssessment,
)
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    ControlMode,
    IdempotencyPolicy,
    InterruptPolicy,
    PauseAction,
    PurchaseItemAction,
    SetSpeedAction,
    StopAction,
)
from kenshi_agent.core.planning import (
    ActivePlanContext,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConditionResult,
    FieldConditionPath,
    PlanEnvelope,
    PlannerDecision,
    PlanPatch,
    PlanStep,
    RiskBudget,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    Disposition,
    GameState,
    NativeControlState,
    NearbyEntity,
    TelemetrySnapshot,
    UIState,
    Vec3,
)
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.planners import HeuristicPlanner, ScriptedPlanner
from kenshi_agent.planning import (
    PlanBudgetLedger,
    PlanValidationError,
    SystemPlanningClock,
    game_elapsed_seconds,
    validate_future_plan_patch,
    validate_plan,
)


def revision(sequence: int, *, capability_epoch: int = 1) -> WorldStateRevision:
    return WorldStateRevision(
        telemetry_sequence=sequence,
        frame_sequence=sequence,
        capability_epoch=capability_epoch,
        observed_at_monotonic=float(sequence),
    )


def field_condition(
    path: str,
    expected: str | int | float | bool,
    *,
    operator: ConditionOperator = ConditionOperator.EQUALS,
    required_capabilities: list[str] | None = None,
    target_id: str | None = None,
) -> Condition:
    return Condition(
        kind=ConditionKind.FIELD,
        path=path,
        operator=operator,
        expected=expected,
        max_age_seconds=3.0,
        required_capabilities=required_capabilities or [],
        target_id=target_id,
    )


def fresh_condition() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def pause_step(
    step_id: str = "resume",
    *,
    on_success: str | None = None,
    on_failure: str | None = None,
    retry_budget: int = 0,
    idempotency: IdempotencyPolicy = IdempotencyPolicy.AT_MOST_ONCE,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=PauseAction(paused=False),
        preconditions=[
            field_condition(
                "telemetry.game.paused",
                True,
                required_capabilities=["game.pause"],
            )
        ],
        success_conditions=[
            field_condition(
                "telemetry.game.paused",
                False,
                required_capabilities=["game.pause"],
            )
        ],
        failure_conditions=[],
        timeout_seconds=1.0,
        retry_budget=retry_budget,
        idempotency=idempotency,
        on_success=on_success,
        on_failure=on_failure,
    )


def speed_step(step_id: str = "accelerate") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=SetSpeedAction(speed=3),
        preconditions=[
            field_condition(
                "telemetry.game.paused",
                False,
                required_capabilities=["game.pause"],
            )
        ],
        success_conditions=[
            field_condition(
                "telemetry.game.speed_multiplier",
                3.0,
                required_capabilities=["game.speed"],
            )
        ],
        failure_conditions=[],
        timeout_seconds=1.0,
        retry_budget=0,
        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    )


def interruption_pause_step(
    step_id: str = "pause-interrupted-movement",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=PauseAction(paused=True),
        preconditions=[
            field_condition(
                "telemetry.game.paused",
                False,
                required_capabilities=["game.pause"],
            )
        ],
        success_conditions=[
            field_condition(
                "telemetry.game.paused",
                True,
                required_capabilities=["game.pause"],
            ),
            field_condition(
                "telemetry.native_control.command_active",
                False,
            ),
        ],
        failure_conditions=[],
        timeout_seconds=2.0,
        retry_budget=0,
        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
    )


def plan_for(
    current_revision: WorldStateRevision,
    *,
    steps: list[PlanStep] | None = None,
    entry_step_id: str = "resume",
    max_actions: int = 2,
) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="survival-setup",
        plan_version=1,
        objective="Resume and accelerate safe mock time.",
        control_mode=ControlMode.INTERFACE_ONLY,
        based_on_revision=current_revision,
        assumptions=[fresh_condition()],
        steps=steps or [pause_step(on_success="accelerate"), speed_step()],
        entry_step_id=entry_step_id,
        max_actions=max_actions,
        max_wall_seconds=5.0,
        max_game_seconds=10.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )


def observation(
    *,
    sequence: int = 4,
    paused: bool | None = True,
    speed: float | None = 1.0,
    capabilities: list[str] | None = None,
    stale: bool = False,
    age_seconds: float = 0.0,
) -> Observation:
    return Observation(
        run_id="planning",
        step_index=0,
        mode="mock",
        control_mode=ControlMode.INTERFACE_ONLY,
        world_revision=revision(sequence),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            captured_at=datetime.now(UTC),
            identity_session_id=(
                "session-planning"
                if capabilities is not None and "identity.stable_handles" in capabilities
                else None
            ),
            capabilities=capabilities
            if capabilities is not None
            else ["game.pause", "game.speed", "game.time"],
            game=GameState(
                loaded=True,
                paused=paused,
                speed_multiplier=speed,
                elapsed_minutes=0.0,
            ),
        ),
        telemetry_stale=stale,
        telemetry_age_seconds=age_seconds,
    )


def rich_observation() -> Observation:
    current = observation(
        capabilities=[
            "game.pause",
            "game.speed",
            "game.time",
            "game.money",
            "game.location",
            "ui.inventory",
            "ui.dialogue",
            "ui.dialogue.target",
            "ui.dialogue.options",
            "ui.visible_controls",
            "ui.tooltip",
            "squad.basic",
            "squad.indoors",
            "squad.hunger",
            "squad.health",
            "squad.inventory",
            "squad.current_goal",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.visible_entities",
            "nearby.roles",
            "nearby.shop_owners",
            "control.perform_context_action",
        ]
    )
    assert current.telemetry is not None
    current.telemetry = current.telemetry.model_copy(
        update={
            "game": GameState(
                loaded=True,
                paused=False,
                speed_multiplier=2.5,
                elapsed_minutes=12.25,
                money=1234,
                location_name="The Hub",
                day=7,
                hour=8,
                minute=9,
            ),
            "ui": UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=True,
                dialogue_target_id="talker",
                dialogue_options=["First", "Second"],
                visible_controls=[],
                stats_window_open=True,
                open_inventory_windows=2,
                management_screen_open=True,
                management_tab=4,
                tooltip_visible=True,
                tooltip_text="tooltip",
                context_menu_open=False,
                selected_character_id="chosen",
                selected_character_ids=["chosen", "flagged"],
            ),
            "native_control": NativeControlState(
                available=True,
                last_command_sequence=19,
                last_command="perform_context_action",
                last_result="completed",
                last_target="Copper Resource",
                last_target_id="mine",
            ),
            "active_shop_trader_count": 3,
            "squad": [
                CharacterState(id="flagged", name="Flagged", selected=True),
                CharacterState(
                    id="chosen",
                    name="Chosen",
                    selected=True,
                    alive=True,
                    conscious=False,
                    down=True,
                    in_combat=False,
                    position=Vec3(x=1.25, y=2.5, z=3.75),
                    movement_speed=4.5,
                    indoors=True,
                    hunger=2.75,
                    bleeding_rate=0.125,
                    food_items=2,
                    first_aid_kits=1,
                    current_goal="Operating machine",
                ),
            ],
            "nearby_entities": [
                NearbyEntity(
                    id="target",
                    name="Target",
                    disposition=Disposition.FRIENDLY,
                    distance=42.5,
                    visible=True,
                    conscious=False,
                    has_vendor_list=True,
                    is_squad_leader=False,
                    has_dialogue=True,
                    shop_inventory_owner=False,
                )
            ],
        }
    )
    return current


def test_every_field_condition_path_resolves_its_observed_scalar() -> None:
    current = rich_observation()
    expected: dict[FieldConditionPath, str | int | float | bool] = {
        FieldConditionPath.CONTROL_MODE: ControlMode.INTERFACE_ONLY.value,
        FieldConditionPath.TELEMETRY_STALE: False,
        FieldConditionPath.TELEMETRY_IDENTITY_SESSION_ID: "session-planning",
        FieldConditionPath.TELEMETRY_GAME_LOADED: True,
        FieldConditionPath.TELEMETRY_GAME_PAUSED: False,
        FieldConditionPath.TELEMETRY_GAME_SPEED_MULTIPLIER: 2.5,
        FieldConditionPath.TELEMETRY_GAME_ELAPSED_MINUTES: 12.25,
        FieldConditionPath.TELEMETRY_GAME_MONEY: 1234,
        FieldConditionPath.TELEMETRY_GAME_LOCATION_NAME: "The Hub",
        FieldConditionPath.TELEMETRY_GAME_DAY: 7,
        FieldConditionPath.TELEMETRY_GAME_HOUR: 8,
        FieldConditionPath.TELEMETRY_GAME_MINUTE: 9,
        FieldConditionPath.TELEMETRY_UI_ACTIVE_SCREEN: "world",
        FieldConditionPath.TELEMETRY_UI_MODAL_OPEN: False,
        FieldConditionPath.TELEMETRY_UI_DIALOGUE_OPEN: True,
        FieldConditionPath.TELEMETRY_UI_DIALOGUE_TARGET_ID: "talker",
        FieldConditionPath.TELEMETRY_UI_DIALOGUE_OPTION_COUNT: 2,
        FieldConditionPath.TELEMETRY_UI_DIALOGUE_OPTION_0: "First",
        FieldConditionPath.TELEMETRY_UI_VISIBLE_CONTROL_COUNT: 0,
        FieldConditionPath.TELEMETRY_UI_STATS_WINDOW_OPEN: True,
        FieldConditionPath.TELEMETRY_UI_OPEN_INVENTORY_WINDOWS: 2,
        FieldConditionPath.TELEMETRY_UI_MANAGEMENT_SCREEN_OPEN: True,
        FieldConditionPath.TELEMETRY_UI_MANAGEMENT_TAB: 4,
        FieldConditionPath.TELEMETRY_UI_TOOLTIP_VISIBLE: True,
        FieldConditionPath.TELEMETRY_UI_TOOLTIP_TEXT: "tooltip",
        FieldConditionPath.TELEMETRY_UI_CONTEXT_MENU_OPEN: False,
        FieldConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_ID: "chosen",
        FieldConditionPath.TELEMETRY_UI_SELECTED_CHARACTER_COUNT: 2,
        FieldConditionPath.TELEMETRY_ACTIVE_SHOP_TRADER_COUNT: 3,
        FieldConditionPath.TELEMETRY_NATIVE_CONTROL_AVAILABLE: True,
        FieldConditionPath.TELEMETRY_NATIVE_CONTROL_COMMAND_ACTIVE: False,
        FieldConditionPath.TELEMETRY_NATIVE_CONTROL_LAST_COMMAND_SEQUENCE: 19,
        FieldConditionPath.TELEMETRY_NATIVE_CONTROL_LAST_COMMAND: ("perform_context_action"),
        FieldConditionPath.TELEMETRY_NATIVE_CONTROL_LAST_RESULT: "completed",
        FieldConditionPath.TELEMETRY_NATIVE_CONTROL_LAST_TARGET: "Copper Resource",
        FieldConditionPath.TELEMETRY_NATIVE_CONTROL_LAST_TARGET_ID: "mine",
        FieldConditionPath.SELECTED_ALIVE: True,
        FieldConditionPath.SELECTED_CONSCIOUS: False,
        FieldConditionPath.SELECTED_DOWN: True,
        FieldConditionPath.SELECTED_IN_COMBAT: False,
        FieldConditionPath.SELECTED_POSITION_X: 1.25,
        FieldConditionPath.SELECTED_POSITION_Y: 2.5,
        FieldConditionPath.SELECTED_POSITION_Z: 3.75,
        FieldConditionPath.SELECTED_MOVEMENT_SPEED: 4.5,
        FieldConditionPath.SELECTED_INDOORS: True,
        FieldConditionPath.SELECTED_NUTRITION_RESERVE: 2.75,
        FieldConditionPath.SELECTED_BLEEDING_RATE: 0.125,
        FieldConditionPath.SELECTED_FOOD_ITEMS: 2,
        FieldConditionPath.SELECTED_FIRST_AID_KITS: 1,
        FieldConditionPath.SELECTED_CURRENT_GOAL: "Operating machine",
        FieldConditionPath.TARGET_DISPOSITION: Disposition.FRIENDLY.value,
        FieldConditionPath.TARGET_DISTANCE: 42.5,
        FieldConditionPath.TARGET_VISIBLE: True,
        FieldConditionPath.TARGET_CONSCIOUS: False,
        FieldConditionPath.TARGET_HAS_VENDOR_LIST: True,
        FieldConditionPath.TARGET_IS_SQUAD_LEADER: False,
        FieldConditionPath.TARGET_HAS_DIALOGUE: True,
        FieldConditionPath.TARGET_SHOP_INVENTORY_OWNER: False,
    }

    assert set(expected) == set(FieldConditionPath)
    for path, value in expected.items():
        evaluation = evaluate_condition(
            field_condition(
                path.value,
                value,
                target_id="target" if path.value.startswith("target.") else None,
            ),
            current,
        )
        assert evaluation.result is ConditionResult.TRUE, path
        assert evaluation.actual == value, path


def test_selected_character_resolution_has_a_total_precedence_order() -> None:
    """Kenshi's exported primary, or nothing - never roster order."""

    current = rich_observation()
    assert current.telemetry is not None
    selected_name = field_condition("selected.current_goal", "Operating machine")

    assert evaluate_condition(selected_name, current).result is ConditionResult.TRUE

    flagged = current.telemetry.model_copy(
        update={"ui": UIState(), "squad": current.telemetry.squad}
    )
    assert (
        evaluate_condition(
            field_condition("selected.current_goal", "missing"),
            current.model_copy(update={"telemetry": flagged}),
        ).result
        is ConditionResult.UNKNOWN
    )

    # A primary Kenshi names but the roster does not contain is unresolvable.
    # This used to fall back to the first squad member - who need not be
    # selected in any sense - so a fact about "the selected character" was
    # answered about somebody else and reported as TRUE. Kenshi exports its
    # primary; when that primary cannot be found, the answer is unknown.
    absent_primary = current.telemetry.model_copy(
        update={
            "ui": UIState(selected_character_id="absent"),
            "squad": [
                CharacterState(
                    id="first",
                    name="First",
                    current_goal="Fallback goal",
                )
            ],
        }
    )
    assert (
        evaluate_condition(
            field_condition("selected.current_goal", "Fallback goal"),
            current.model_copy(update={"telemetry": absent_primary}),
        ).result
        is ConditionResult.UNKNOWN
    )

    empty = current.telemetry.model_copy(update={"ui": UIState(), "squad": []})
    assert (
        evaluate_condition(
            selected_name,
            current.model_copy(update={"telemetry": empty}),
        ).result
        is ConditionResult.UNKNOWN
    )


def test_telemetry_condition_classification_fails_stale_fields_closed() -> None:
    current = rich_observation().model_copy(update={"telemetry_stale": True})
    for path in FieldConditionPath:
        evaluation = evaluate_condition(
            field_condition(
                path.value,
                True,
                target_id="target" if path.value.startswith("target.") else None,
            ),
            current,
        )
        if path in {
            FieldConditionPath.CONTROL_MODE,
            FieldConditionPath.TELEMETRY_STALE,
        }:
            assert evaluation.result is not ConditionResult.STALE, path
        else:
            assert evaluation.result is ConditionResult.STALE, path

    capability_bound_control = field_condition(
        "control_mode",
        ControlMode.INTERFACE_ONLY.value,
        required_capabilities=["missing.capability"],
    )
    assert evaluate_condition(capability_bound_control, current).result is ConditionResult.STALE


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "result"),
    [
        (ConditionOperator.EQUALS, 4, 4, ConditionResult.TRUE),
        (ConditionOperator.EQUALS, 4, 5, ConditionResult.FALSE),
        (ConditionOperator.NOT_EQUALS, 4, 5, ConditionResult.TRUE),
        (ConditionOperator.NOT_EQUALS, 4, 4, ConditionResult.FALSE),
        (ConditionOperator.CONTAINS, "Operating machine", "machine", ConditionResult.TRUE),
        (ConditionOperator.CONTAINS, "Operating machine", "sleeping", ConditionResult.FALSE),
        (ConditionOperator.LESS_THAN, 4, 5, ConditionResult.TRUE),
        (ConditionOperator.LESS_THAN, 5, 5, ConditionResult.FALSE),
        (ConditionOperator.LESS_THAN_OR_EQUAL, 5, 5, ConditionResult.TRUE),
        (ConditionOperator.LESS_THAN_OR_EQUAL, 6, 5, ConditionResult.FALSE),
        (ConditionOperator.GREATER_THAN, 6, 5, ConditionResult.TRUE),
        (ConditionOperator.GREATER_THAN, 5, 5, ConditionResult.FALSE),
        (ConditionOperator.GREATER_THAN_OR_EQUAL, 5, 5, ConditionResult.TRUE),
        (ConditionOperator.GREATER_THAN_OR_EQUAL, 4, 5, ConditionResult.FALSE),
    ],
)
def test_condition_operators_follow_their_truth_tables(
    operator: ConditionOperator,
    actual: str | int,
    expected: str | int,
    result: ConditionResult,
) -> None:
    current = rich_observation()
    assert current.telemetry is not None
    current.telemetry = current.telemetry.model_copy(
        update={
            "game": current.telemetry.game.model_copy(
                update={"money": actual if isinstance(actual, int) else 4}
            ),
            "squad": [
                CharacterState(
                    id="selected",
                    name="Selected",
                    selected=True,
                    current_goal=actual if isinstance(actual, str) else "Operating machine",
                )
            ],
            "ui": UIState(
                selected_character_id="selected",
                selected_character_ids=["selected"],
            ),
        }
    )
    path = "selected.current_goal" if isinstance(actual, str) else "telemetry.game.money"
    evaluation = evaluate_condition(
        field_condition(path, expected, operator=operator),
        current,
    )

    assert evaluation.result is result
    assert evaluation.actual == actual


def test_ordered_and_contains_comparisons_reject_wrong_scalar_types() -> None:
    current = rich_observation()

    bool_order = evaluate_condition(
        field_condition(
            "telemetry.game.loaded",
            0,
            operator=ConditionOperator.GREATER_THAN,
        ),
        current,
    )
    string_order = evaluate_condition(
        field_condition(
            "telemetry.game.location_name",
            "A",
            operator=ConditionOperator.GREATER_THAN,
        ),
        current,
    )
    numeric_contains = evaluate_condition(
        field_condition(
            "telemetry.game.money",
            "23",
            operator=ConditionOperator.CONTAINS,
        ),
        current,
    )

    assert bool_order.result is ConditionResult.UNKNOWN
    assert string_order.result is ConditionResult.UNKNOWN
    assert numeric_contains.result is ConditionResult.UNKNOWN
    assert bool_order.actual is True
    assert string_order.actual == "The Hub"
    assert numeric_contains.actual == 1234


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (1.0, 1.5, 30.0),
        (1.5, 1.0, 0.0),
        (1.0, 1.0, 0.0),
        (None, 1.0, None),
        (1.0, None, None),
    ],
)
def test_game_elapsed_seconds_is_total_and_never_negative(
    before: float | None,
    after: float | None,
    expected: float | None,
) -> None:
    start = observation()
    current = observation(sequence=5)
    assert start.telemetry is not None
    assert current.telemetry is not None
    start.telemetry = start.telemetry.model_copy(
        update={"game": start.telemetry.game.model_copy(update={"elapsed_minutes": before})}
    )
    current.telemetry = current.telemetry.model_copy(
        update={"game": current.telemetry.game.model_copy(update={"elapsed_minutes": after})}
    )

    assert game_elapsed_seconds(start, current) == expected


def test_game_elapsed_seconds_requires_both_telemetry_snapshots() -> None:
    present = observation()
    absent = present.model_copy(update={"telemetry": None})

    assert game_elapsed_seconds(absent, present) is None
    assert game_elapsed_seconds(present, absent) is None
    assert game_elapsed_seconds(absent, absent) is None


def test_system_planning_clock_forwards_the_requested_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float] = []

    async def record_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    asyncio.run(SystemPlanningClock().sleep(0.125))

    assert calls == [0.125]


def test_condition_evaluator_preserves_false_unknown_unavailable_and_stale() -> None:
    paused_false = field_condition(
        "telemetry.game.paused",
        False,
        required_capabilities=["game.pause"],
    )

    assert (
        evaluate_condition(paused_false, observation(paused=False)).result is ConditionResult.TRUE
    )
    assert (
        evaluate_condition(paused_false, observation(paused=True)).result is ConditionResult.FALSE
    )
    assert (
        evaluate_condition(paused_false, observation(paused=None)).result is ConditionResult.UNKNOWN
    )
    assert (
        evaluate_condition(
            paused_false,
            observation(paused=False, capabilities=["game.speed"]),
        ).result
        is ConditionResult.UNAVAILABLE
    )
    assert (
        evaluate_condition(paused_false, observation(paused=False, stale=True)).result
        is ConditionResult.STALE
    )
    inferred_capability_gate = field_condition(
        "telemetry.game.paused",
        False,
    )
    assert (
        evaluate_condition(
            inferred_capability_gate,
            observation(paused=False, capabilities=["game.speed"]),
        ).result
        is ConditionResult.UNAVAILABLE
    )


def test_exact_selection_count_requires_stable_identity_capability() -> None:
    exact_one = field_condition("telemetry.ui.selected_character_count", 1)
    current = observation(capabilities=["squad.basic", "identity.stable_handles"])
    assert current.telemetry is not None
    current.telemetry = current.telemetry.model_copy(
        update={
            "ui": UIState(
                selected_character_id="entity-player",
                selected_character_ids=["entity-player"],
            ),
            "squad": [CharacterState(id="entity-player", name="Wanderer", selected=True)],
        }
    )

    assert evaluate_condition(exact_one, current).result is ConditionResult.TRUE
    assert (
        evaluate_condition(
            exact_one,
            observation(capabilities=["squad.basic"]),
        ).result
        is ConditionResult.UNAVAILABLE
    )


def test_postcondition_requires_a_later_relevant_revision() -> None:
    condition = field_condition(
        "telemetry.game.paused",
        False,
        required_capabilities=["game.pause"],
    )
    action_revision = revision(8)

    same_revision = observation(sequence=8, paused=False)
    later_revision = observation(sequence=9, paused=False)

    assert (
        evaluate_condition(
            condition,
            same_revision,
            after_revision=action_revision,
        ).result
        is ConditionResult.STALE
    )
    assert (
        evaluate_condition(
            condition,
            later_revision,
            after_revision=action_revision,
        ).result
        is ConditionResult.TRUE
    )


def test_revision_gates_cover_missing_telemetry_sequences_and_world_only_facts() -> None:
    telemetry_condition = field_condition(
        "telemetry.game.paused",
        True,
        required_capabilities=["game.pause"],
    )
    prior = revision(8)
    missing_current_sequence = observation(sequence=9)
    missing_current_sequence = missing_current_sequence.model_copy(
        update={
            "world_revision": missing_current_sequence.world_revision.model_copy(
                update={"telemetry_sequence": None}
            )
        }
    )
    missing_prior_sequence = prior.model_copy(update={"telemetry_sequence": None})

    assert (
        evaluate_condition(
            telemetry_condition,
            missing_current_sequence,
            after_revision=prior,
        ).result
        is ConditionResult.STALE
    )
    assert (
        evaluate_condition(
            telemetry_condition,
            observation(sequence=9),
            after_revision=missing_prior_sequence,
        ).result
        is ConditionResult.STALE
    )
    assert (
        evaluate_condition(
            telemetry_condition,
            observation(sequence=7),
            after_revision=prior,
        ).result
        is ConditionResult.STALE
    )

    world_condition = field_condition(
        "control_mode",
        ControlMode.INTERFACE_ONLY.value,
    )
    assert (
        evaluate_condition(
            world_condition,
            observation(sequence=8),
            after_revision=prior,
        ).result
        is ConditionResult.STALE
    )
    assert (
        evaluate_condition(
            world_condition,
            observation(sequence=9),
            after_revision=prior,
        ).result
        is ConditionResult.TRUE
    )


def test_condition_availability_and_age_gates_are_closed_at_their_boundaries() -> None:
    paused = field_condition("telemetry.game.paused", True)

    assert (
        evaluate_condition(
            paused,
            observation().model_copy(update={"telemetry": None}),
        ).result
        is ConditionResult.UNAVAILABLE
    )
    assert (
        evaluate_condition(
            paused,
            observation(age_seconds=3.0),
        ).result
        is ConditionResult.TRUE
    )
    assert (
        evaluate_condition(
            paused,
            observation(age_seconds=3.001),
        ).result
        is ConditionResult.STALE
    )

    unknown_age = observation()
    unknown_age = unknown_age.model_copy(update={"telemetry_age_seconds": None})
    assert evaluate_condition(fresh_condition(), unknown_age).result is ConditionResult.UNKNOWN
    assert (
        evaluate_condition(fresh_condition(), observation(age_seconds=0.0)).result
        is ConditionResult.TRUE
    )


def test_capability_conditions_and_aliases_report_exact_availability() -> None:
    capability = Condition(
        kind=ConditionKind.CAPABILITY,
        path="game.pause",
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )
    generic_approach = Condition(
        kind=ConditionKind.CAPABILITY,
        path="control.approach_dialogue_target",
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )

    assert (
        evaluate_condition(capability, observation(capabilities=["game.pause"])).result
        is ConditionResult.TRUE
    )
    assert (
        evaluate_condition(capability, observation(capabilities=["game.speed"])).result
        is ConditionResult.FALSE
    )
    assert (
        evaluate_condition(
            generic_approach,
            observation(capabilities=["control.approach_vendor"]),
        ).result
        is ConditionResult.TRUE
    )

    alias_required = field_condition(
        "control_mode",
        ControlMode.INTERFACE_ONLY.value,
        required_capabilities=["control.approach_dialogue_target"],
    )
    assert (
        evaluate_condition(
            alias_required,
            observation(capabilities=["control.approach_vendor"]),
        ).result
        is ConditionResult.TRUE
    )
    assert (
        evaluate_condition(
            alias_required,
            observation(capabilities=[]),
        ).result
        is ConditionResult.UNAVAILABLE
    )


def test_capability_epoch_advance_is_a_later_world_revision() -> None:
    before = revision(8, capability_epoch=1)
    after = before.model_copy(
        update={
            "capability_epoch": 2,
            "observed_at_monotonic": before.observed_at_monotonic + 0.1,
        }
    )

    assert after.is_later_than(before)
    assert not before.is_later_than(after)


@pytest.mark.parametrize(
    "steps",
    [
        [pause_step(on_success="missing")],
        [
            pause_step(on_success="accelerate"),
            speed_step("accelerate").model_copy(update={"on_success": "resume"}),
        ],
        [pause_step(), speed_step("unreachable")],
    ],
)
def test_plan_graph_rejects_invalid_branch_cycle_and_unreachable_steps(
    steps: list[PlanStep],
) -> None:
    with pytest.raises(ValidationError):
        plan_for(revision(1), steps=steps, max_actions=2)


def test_plan_rejects_retry_for_at_most_once_step() -> None:
    with pytest.raises(ValidationError, match="retry_budget"):
        pause_step(retry_budget=1)


def test_plan_policy_rejects_excessive_horizon_budget_and_stale_basis() -> None:
    current = observation(sequence=5)
    plan = plan_for(revision(5))

    with pytest.raises(PlanValidationError, match="steps"):
        validate_plan(
            plan,
            current,
            PlanningConfig(max_plan_steps=1),
        )

    with pytest.raises(PlanValidationError, match="max_actions"):
        validate_plan(
            plan.model_copy(update={"max_actions": 3}),
            current,
            PlanningConfig(max_actions_per_plan=2),
        )

    with pytest.raises(PlanValidationError, match="stale"):
        validate_plan(
            plan_for(revision(4)),
            current,
            PlanningConfig(),
        )


def test_plan_validation_fails_closed_for_every_outer_authority_boundary() -> None:
    valid_observation = observation(sequence=5)
    valid_plan = plan_for(valid_observation.world_revision)

    no_revision = valid_observation.world_revision.model_copy(
        update={"telemetry_sequence": None, "frame_sequence": None}
    )
    no_revision_observation = valid_observation.model_copy(update={"world_revision": no_revision})
    base_telemetry = valid_observation.telemetry
    assert base_telemetry is not None
    no_elapsed_telemetry = base_telemetry.model_copy(
        update={"game": base_telemetry.game.model_copy(update={"elapsed_minutes": None})}
    )
    no_time_capability = base_telemetry.model_copy(
        update={"capabilities": ["game.pause", "game.speed"]}
    )
    native_observation = valid_observation.model_copy(
        update={"control_mode": ControlMode.NATIVE_ASSISTED}
    )
    native_plan = valid_plan.model_copy(update={"control_mode": ControlMode.NATIVE_ASSISTED})

    invalid_cases = [
        (
            valid_plan,
            valid_observation.model_copy(update={"control_mode": ControlMode.NATIVE_ASSISTED}),
            PlanningConfig(),
        ),
        (
            plan_for(revision(4)),
            valid_observation,
            PlanningConfig(),
        ),
        (
            valid_plan.model_copy(update={"based_on_revision": no_revision}),
            no_revision_observation,
            PlanningConfig(),
        ),
        (
            valid_plan,
            valid_observation.model_copy(update={"telemetry": None}),
            PlanningConfig(),
        ),
        (
            valid_plan,
            valid_observation.model_copy(update={"telemetry": no_elapsed_telemetry}),
            PlanningConfig(),
        ),
        (
            valid_plan,
            valid_observation.model_copy(update={"telemetry": no_time_capability}),
            PlanningConfig(),
        ),
        (
            valid_plan,
            valid_observation,
            PlanningConfig(max_plan_steps=1),
        ),
        (
            valid_plan.model_copy(update={"max_actions": 3}),
            valid_observation,
            PlanningConfig(max_actions_per_plan=2),
        ),
        (
            valid_plan.model_copy(update={"max_wall_seconds": 31.0}),
            valid_observation,
            PlanningConfig(max_plan_wall_seconds=30.0),
        ),
        (
            valid_plan.model_copy(update={"max_game_seconds": 13.0}),
            valid_observation,
            PlanningConfig(max_plan_game_seconds=12.0),
        ),
        (
            valid_plan.model_copy(
                update={
                    "risk_budget": RiskBudget(
                        max_pointer_actions=1,
                        max_purchase_actions=0,
                        max_native_assisted_actions=0,
                    )
                }
            ),
            valid_observation,
            PlanningConfig(max_pointer_actions_per_plan=0),
        ),
        (
            valid_plan.model_copy(
                update={
                    "risk_budget": RiskBudget(
                        max_pointer_actions=0,
                        max_purchase_actions=1,
                        max_native_assisted_actions=0,
                    )
                }
            ),
            valid_observation,
            PlanningConfig(max_purchase_actions_per_plan=0),
        ),
        (
            native_plan.model_copy(
                update={
                    "risk_budget": RiskBudget(
                        max_pointer_actions=0,
                        max_purchase_actions=0,
                        max_native_assisted_actions=1,
                    )
                }
            ),
            native_observation,
            PlanningConfig(max_native_assisted_actions_per_plan=0),
        ),
        (
            valid_plan.model_copy(
                update={
                    "risk_budget": RiskBudget(
                        max_pointer_actions=0,
                        max_purchase_actions=0,
                        max_native_assisted_actions=1,
                    )
                }
            ),
            valid_observation,
            PlanningConfig(max_native_assisted_actions_per_plan=1),
        ),
        (
            valid_plan.model_copy(
                update={
                    "assumptions": [
                        field_condition(
                            "telemetry.game.paused",
                            False,
                            required_capabilities=["game.pause"],
                        )
                    ]
                }
            ),
            valid_observation,
            PlanningConfig(),
        ),
    ]

    for plan, current, config in invalid_cases:
        with pytest.raises(PlanValidationError):
            validate_plan(plan, current, config)


def test_live_plan_policy_receives_the_exact_plan_and_configured_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kenshi_agent import planning as planning_module

    current = observation().model_copy(update={"mode": "live"})
    plan = plan_for(current.world_revision)
    calls: list[tuple[PlanEnvelope, int]] = []

    def policy_errors(
        candidate: PlanEnvelope,
        *,
        max_steps: int,
    ) -> list[str]:
        calls.append((candidate, max_steps))
        return []

    monkeypatch.setattr(
        planning_module,
        "live_plan_policy_errors",
        policy_errors,
    )
    results = validate_plan(
        plan,
        current,
        PlanningConfig(),
    )

    assert calls == [(plan, 4)]
    assert [item.result for item in results] == [ConditionResult.TRUE]


def test_plan_validation_accepts_every_configured_boundary_at_equality() -> None:
    current = observation()
    plan = plan_for(current.world_revision)

    results = validate_plan(
        plan,
        current,
        PlanningConfig(
            max_plan_steps=len(plan.steps),
            max_actions_per_plan=plan.max_actions,
            max_plan_wall_seconds=plan.max_wall_seconds,
            max_plan_game_seconds=plan.max_game_seconds,
            max_pointer_actions_per_plan=plan.risk_budget.max_pointer_actions,
            max_purchase_actions_per_plan=plan.risk_budget.max_purchase_actions,
            max_native_assisted_actions_per_plan=(plan.risk_budget.max_native_assisted_actions),
        ),
    )

    assert [item.result for item in results] == [ConditionResult.TRUE]

    for telemetry_sequence, frame_sequence in [(4, None), (None, 4)]:
        one_channel_revision = current.world_revision.model_copy(
            update={
                "telemetry_sequence": telemetry_sequence,
                "frame_sequence": frame_sequence,
            }
        )
        one_channel_observation = current.model_copy(
            update={"world_revision": one_channel_revision}
        )
        one_channel_plan = plan.model_copy(update={"based_on_revision": one_channel_revision})
        validate_plan(
            one_channel_plan,
            one_channel_observation,
            PlanningConfig(),
        )


def purchase_step(step_id: str, *, on_success: str | None = None) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        action=PurchaseItemAction(
            cell_label=f"Item {step_id}",
            item_name="Dried Meat",
            expected_price=75,
            window="Trader",
            seller_id="seller",
        ),
        preconditions=[fresh_condition()],
        success_conditions=[
            field_condition(
                "telemetry.game.money",
                925,
                required_capabilities=["game.money"],
            )
        ],
        timeout_seconds=1.0,
        on_success=on_success,
    )


def test_plan_structure_does_not_restate_the_affordance_non_progress_barrier() -> None:
    current = observation(
        sequence=5,
        capabilities=["game.pause", "game.time", "game.money"],
    )
    step = purchase_step("purchase")
    prior = ActionOutcome(
        outcome_id="ao-1",
        run_id=current.run_id,
        plan_id="failed-purchase",
        plan_version=1,
        step_id="purchase",
        step_index=0,
        intent="Buy one Dried Meat.",
        action=step.action,
        executed=True,
        assessment=ActionOutcomeAssessment.NO_OP,
        causal_revision_advanced=True,
        semantic_status="not_purchased",
        feedback="Kenshi refused the purchase because there was no room.",
        completed_at_revision=revision(4),
    )
    current = current.model_copy(update={"recent_action_outcomes": [prior]})
    plan = plan_for(
        current.world_revision,
        steps=[step],
        entry_step_id=step.step_id,
        max_actions=1,
    ).model_copy(
        update={
            "risk_budget": RiskBudget(
                max_pointer_actions=1,
                max_purchase_actions=1,
                max_native_assisted_actions=0,
            )
        }
    )

    results = validate_plan(plan, current, PlanningConfig())
    assert [item.result for item in results] == [ConditionResult.TRUE]



def test_plan_patch_carries_optimistic_concurrency_basis() -> None:
    patch = PlanPatch(
        schema_version="1.0",
        plan_id="survival-setup",
        based_on_plan_version=1,
        based_on_revision=revision(7),
        replace_future_steps=[speed_step()],
        rationale="The safe setup still applies, but acceleration remains.",
    )

    assert patch.based_on_plan_version == 1
    assert patch.based_on_revision.telemetry_sequence == 7


def test_plan_patch_names_the_exact_active_step_when_requesting_interruption() -> None:
    patch = PlanPatch(
        schema_version="1.0",
        plan_id="survival-setup",
        based_on_plan_version=1,
        based_on_revision=revision(7),
        interrupt_active_step_id="resume",
        replace_future_steps=[speed_step()],
        rationale="Conditions changed enough to interrupt the exact active step.",
    )

    assert patch.interrupt_active_step_id == "resume"


def test_future_patch_rebases_to_current_state_and_cannot_restart_protected_steps() -> None:
    current = observation(sequence=7)
    active_plan = plan_for(current.world_revision)
    ledger = PlanBudgetLedger.from_plan(active_plan)
    ledger.reserve(PauseAction(paused=False))
    ledger.commit()
    patch = PlanPatch(
        schema_version="1.0",
        plan_id=active_plan.plan_id,
        based_on_plan_version=active_plan.plan_version,
        based_on_revision=current.world_revision,
        replace_future_steps=[speed_step("patched-speed")],
        rationale="Use the remaining safe speed step.",
    )

    candidate = validate_future_plan_patch(
        patch,
        active_plan=active_plan,
        planner_observation=current,
        current_observation=current,
        config=PlanningConfig(),
        budget=ledger,
        remaining_run_actions=1,
        protected_step_ids={"resume"},
    )

    assert candidate.plan_version == 2
    assert candidate.entry_step_id == "patched-speed"
    assert candidate.max_actions == 1

    rebased = validate_future_plan_patch(
        patch,
        active_plan=active_plan,
        planner_observation=current,
        current_observation=observation(sequence=8),
        config=PlanningConfig(),
        budget=ledger,
        remaining_run_actions=1,
        protected_step_ids={"resume"},
    )
    assert rebased.based_on_revision.same_snapshot_as(revision(8))

    protected_patch = patch.model_copy(update={"replace_future_steps": [speed_step("resume")]})
    with pytest.raises(PlanValidationError, match="active or completed"):
        validate_future_plan_patch(
            protected_patch,
            active_plan=active_plan,
            planner_observation=current,
            current_observation=current,
            config=PlanningConfig(),
            budget=ledger,
            remaining_run_actions=1,
            protected_step_ids={"resume"},
        )


def test_future_patch_rebase_leaves_latest_entry_authority_to_submission() -> None:
    planner_observation = observation(sequence=7, paused=False).model_copy(update={"mode": "live"})
    active_plan = plan_for(planner_observation.world_revision)
    patch = PlanPatch(
        schema_version="1.0",
        plan_id=active_plan.plan_id,
        based_on_plan_version=active_plan.plan_version,
        based_on_revision=planner_observation.world_revision,
        replace_future_steps=[speed_step("future-speed")],
        rationale="Keep the future speed intent only while latest state authorizes it.",
    )
    current = observation(sequence=8, paused=True).model_copy(update={"mode": "live"})

    candidate = validate_future_plan_patch(
        patch,
        active_plan=active_plan,
        planner_observation=planner_observation,
        current_observation=current,
        config=PlanningConfig(),
        budget=PlanBudgetLedger.from_plan(active_plan),
        remaining_run_actions=1,
        protected_step_ids=set(),
    )
    assert candidate.entry_step_id == "future-speed"


def test_interrupt_patch_requires_exact_opt_in_and_a_pause_handoff() -> None:
    current = observation(sequence=7)
    interruptible_resume = pause_step(on_success="accelerate").model_copy(
        update={
            "interrupt_policy": InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH,
        }
    )
    active_plan = plan_for(
        current.world_revision,
        steps=[interruptible_resume, speed_step()],
        max_actions=3,
    )
    ledger = PlanBudgetLedger.from_plan(active_plan)
    ledger.reserve(active_plan.steps[0].action)
    ledger.commit()
    planner_observation = current.model_copy(
        update={
            "active_plan": ActivePlanContext(
                plan_id=active_plan.plan_id,
                plan_version=active_plan.plan_version,
                objective=active_plan.objective,
                active_step_id="resume",
                active_step_interrupt_policy=(InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH),
                remaining_actions=ledger.remaining_actions,
            )
        }
    )

    wrong_step = PlanPatch(
        schema_version="1.0",
        plan_id=active_plan.plan_id,
        based_on_plan_version=active_plan.plan_version,
        based_on_revision=current.world_revision,
        interrupt_active_step_id="accelerate",
        replace_future_steps=[interruption_pause_step()],
        rationale="This names a future step, not the exact active step.",
    )
    with pytest.raises(PlanValidationError, match="exact active step"):
        validate_future_plan_patch(
            wrong_step,
            active_plan=active_plan,
            planner_observation=planner_observation,
            current_observation=current,
            config=PlanningConfig(),
            budget=ledger,
            remaining_run_actions=2,
            protected_step_ids={"resume"},
        )

    no_pause = wrong_step.model_copy(
        update={
            "interrupt_active_step_id": "resume",
            "replace_future_steps": [speed_step("unsafe-replacement")],
        }
    )
    with pytest.raises(PlanValidationError, match="confirmed pause handoff"):
        validate_future_plan_patch(
            no_pause,
            active_plan=active_plan,
            planner_observation=planner_observation,
            current_observation=current,
            config=PlanningConfig(),
            budget=ledger,
            remaining_run_actions=2,
            protected_step_ids={"resume"},
        )

    non_interruptible = active_plan.model_copy(
        update={
            "steps": [
                interruptible_resume.model_copy(
                    update={"interrupt_policy": InterruptPolicy.CANCEL_ON_REFLEX}
                ),
                speed_step(),
            ]
        }
    )
    valid = no_pause.model_copy(update={"replace_future_steps": [interruption_pause_step()]})
    with pytest.raises(PlanValidationError, match="does not permit"):
        validate_future_plan_patch(
            valid,
            active_plan=non_interruptible,
            planner_observation=planner_observation,
            current_observation=current,
            config=PlanningConfig(),
            budget=ledger,
            remaining_run_actions=2,
            protected_step_ids={"resume"},
        )

    candidate = validate_future_plan_patch(
        valid,
        active_plan=active_plan,
        planner_observation=planner_observation,
        current_observation=current,
        config=PlanningConfig(),
        budget=ledger,
        remaining_run_actions=2,
        protected_step_ids={"resume"},
    )

    assert candidate.steps == [interruption_pause_step()]


def test_future_patch_rejects_wrong_identity_protected_steps_and_empty_budget() -> None:
    current = observation(sequence=7)
    active_plan = plan_for(current.world_revision)
    ledger = PlanBudgetLedger.from_plan(active_plan)
    base = PlanPatch(
        schema_version="1.0",
        plan_id=active_plan.plan_id,
        based_on_plan_version=active_plan.plan_version,
        based_on_revision=current.world_revision,
        replace_future_steps=[speed_step("patched")],
        rationale="Replace only future work.",
    )

    invalid_cases = [
        (
            base.model_copy(update={"plan_id": "different"}),
            current,
            current,
            ledger,
            1,
            set(),
        ),
        (
            base.model_copy(update={"based_on_plan_version": 2}),
            current,
            current,
            ledger,
            1,
            set(),
        ),
        (
            base.model_copy(update={"based_on_revision": revision(6)}),
            current,
            current,
            ledger,
            1,
            set(),
        ),
        (
            base,
            current,
            current,
            ledger,
            1,
            {"patched"},
        ),
        (
            base,
            current,
            current,
            PlanBudgetLedger(
                remaining_actions=0,
                remaining_pointer_actions=0,
                remaining_purchase_actions=0,
                remaining_native_assisted_actions=0,
            ),
            1,
            set(),
        ),
        (
            base,
            current,
            current,
            ledger,
            0,
            set(),
        ),
    ]

    for (
        patch,
        planner_observation,
        current_observation,
        budget,
        remaining_run_actions,
        protected,
    ) in invalid_cases:
        with pytest.raises(PlanValidationError):
            validate_future_plan_patch(
                patch,
                active_plan=active_plan,
                planner_observation=planner_observation,
                current_observation=current_observation,
                config=PlanningConfig(),
                budget=budget,
                remaining_run_actions=remaining_run_actions,
                protected_step_ids=protected,
            )

    candidate = validate_future_plan_patch(
        base,
        active_plan=active_plan,
        planner_observation=current,
        current_observation=observation(sequence=8),
        config=PlanningConfig(),
        budget=ledger,
        remaining_run_actions=1,
        protected_step_ids=set(),
    )
    assert candidate.based_on_revision.same_snapshot_as(revision(8))


def test_interrupt_patch_requires_every_fact_in_the_pause_handoff() -> None:
    current = observation(sequence=7)
    active_step = pause_step(on_success="accelerate").model_copy(
        update={"interrupt_policy": InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH}
    )
    active_plan = plan_for(
        current.world_revision,
        steps=[active_step, speed_step()],
        max_actions=3,
    )
    ledger = PlanBudgetLedger.from_plan(active_plan)
    planner_observation = current.model_copy(
        update={
            "active_plan": ActivePlanContext(
                plan_id=active_plan.plan_id,
                plan_version=active_plan.plan_version,
                objective=active_plan.objective,
                active_step_id="resume",
                active_step_interrupt_policy=(InterruptPolicy.CANCEL_ON_REFLEX_OR_PLAN_PATCH),
                remaining_actions=3,
            )
        }
    )

    def patch_for(step: PlanStep, *, interrupt_id: str = "resume") -> PlanPatch:
        return PlanPatch(
            schema_version="1.0",
            plan_id=active_plan.plan_id,
            based_on_plan_version=active_plan.plan_version,
            based_on_revision=current.world_revision,
            interrupt_active_step_id=interrupt_id,
            replace_future_steps=[step],
            rationale="Interrupt only through a fully proved pause handoff.",
        )

    valid_pause = interruption_pause_step()
    wrong_paused_terminal = valid_pause.model_copy(
        update={
            "success_conditions": [
                field_condition("telemetry.game.speed_multiplier", True),
                field_condition("telemetry.native_control.command_active", False),
            ]
        }
    )
    wrong_command_terminal = valid_pause.model_copy(
        update={
            "success_conditions": [
                field_condition("telemetry.game.paused", True),
                field_condition("telemetry.game.paused", False),
            ]
        }
    )
    unpausing_action = valid_pause.model_copy(update={"action": PauseAction(paused=False)})
    no_active_context = planner_observation.model_copy(update={"active_plan": None})
    active_context = planner_observation.active_plan
    assert active_context is not None
    ghost_context = planner_observation.model_copy(
        update={"active_plan": active_context.model_copy(update={"active_step_id": "ghost"})}
    )

    invalid_cases = [
        (patch_for(wrong_paused_terminal), planner_observation),
        (patch_for(wrong_command_terminal), planner_observation),
        (patch_for(unpausing_action), planner_observation),
        (patch_for(valid_pause), no_active_context),
        (patch_for(valid_pause, interrupt_id="ghost"), ghost_context),
    ]
    for patch, snapshot in invalid_cases:
        with pytest.raises(PlanValidationError):
            validate_future_plan_patch(
                patch,
                active_plan=active_plan,
                planner_observation=snapshot,
                current_observation=current,
                config=PlanningConfig(),
                budget=ledger,
                remaining_run_actions=2,
                protected_step_ids=set(),
            )


def test_future_patch_labels_an_invalid_replacement_graph() -> None:
    current = observation()
    active_plan = plan_for(current.world_revision)
    patch = PlanPatch(
        schema_version="1.0",
        plan_id=active_plan.plan_id,
        based_on_plan_version=active_plan.plan_version,
        based_on_revision=current.world_revision,
        replace_future_steps=[speed_step("first"), speed_step("unreachable")],
        rationale="This malformed graph is test input.",
    )

    with pytest.raises(PlanValidationError, match="replacement graph is invalid"):
        validate_future_plan_patch(
            patch,
            active_plan=active_plan,
            planner_observation=current,
            current_observation=current,
            config=PlanningConfig(),
            budget=PlanBudgetLedger.from_plan(active_plan),
            remaining_run_actions=2,
            protected_step_ids=set(),
        )


def test_plan_budget_reservations_release_or_commit_transactionally() -> None:
    plan = plan_for(revision(1))
    ledger = PlanBudgetLedger.from_plan(plan)

    risk = ledger.reserve(PauseAction(paused=False))
    assert ledger.remaining_actions == 1
    ledger.release(risk)
    assert ledger.remaining_actions == 2
    assert ledger.released_actions == 1

    ledger.reserve(PauseAction(paused=False))
    ledger.commit()
    assert ledger.remaining_actions == 1
    assert ledger.committed_actions == 1



def test_builtin_heuristic_emits_a_two_step_continuous_plan() -> None:
    current = observation()

    output = asyncio.run(HeuristicPlanner().decide(current))

    assert isinstance(output, PlanEnvelope)
    assert [step.step_id for step in output.steps] == ["resume", "accelerate"]
    assert output.based_on_revision.same_snapshot_as(current.world_revision)


def test_scripted_adapter_parses_continuous_plan(
    tmp_path: Path,
) -> None:
    current = observation()
    plan = plan_for(current.world_revision)
    script_path = tmp_path / "plan.jsonl"
    script_path.write_text(plan.model_dump_json() + "\n", encoding="utf-8")

    scripted_output = asyncio.run(ScriptedPlanner(script_path).decide(current))
    assert isinstance(scripted_output, PlanEnvelope)


def test_planner_adapters_declare_the_representation_they_consume(
    tmp_path: Path,
) -> None:
    current = observation()
    script_path = tmp_path / "decision.jsonl"
    script_path.write_text(
        PlannerDecision(
            intent="Stop.",
            rationale="The adapter contract is under test.",
            action=StopAction(reason="done"),
            confidence=1.0,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )

    heuristic = HeuristicPlanner().prepare_input(current, context_id="pc-1")
    scripted = ScriptedPlanner(script_path).prepare_input(
        current,
        context_id="pc-3",
    )

    assert heuristic.context.manifest.input_kind == "full_observation"
    assert heuristic.context.manifest.current_observation_delivered is True
    assert scripted.context.manifest.input_kind == "scripted"
    assert scripted.context.manifest.current_observation_delivered is False
    assert scripted.context.manifest.current_target_ids == []
    assert scripted.context.manifest.action_outcome_ids == []
    assert scripted.context.manifest.plan_outcome_ids == []
    assert scripted.context.manifest.memory_ids == []
    assert scripted.context.manifest.advisor_brief_ids == []
