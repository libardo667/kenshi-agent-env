
import pytest

from kenshi_agent.action_budget import ActionBudgetLedger
from kenshi_agent.affordances import OPERATION_BINDING_AUTHORITY
from kenshi_agent.config import SafetyConfig
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    Action,
    ApproachDialogueTargetAction,
    ClickAction,
    ControlMode,
    CoordinateSpace,
    MoveCursorAction,
    PauseAction,
    ScrollAction,
    SelectSquadMemberExactAction,
    SetSpeedAction,
    TravelToMapDestinationAction,
    WaitAction,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    Disposition,
    GameState,
    KnownMapDestination,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from kenshi_agent.safety import OperationPolicy, SafetyViolation


def reserve_action(
    ledger: ActionBudgetLedger,
    action: Action,
    observation: Observation,
):
    bound = OPERATION_BINDING_AUTHORITY.bind(action, observation, affordance=None)
    return ledger.reserve(bound, observation)


def safety_config() -> SafetyConfig:
    return SafetyConfig(
        allow_action_kinds=[
            "noop",
            "stop",
            "pause",
            "set_speed",
            "wait",
            "key",
            "hotkey",
            "click",
            "move_cursor",
            "scroll",
        ],
        max_wait_seconds=3.0,
        max_actions_per_minute=100,
    )


def test_normalized_click_outside_bounds_is_blocked() -> None:
    guard = OperationPolicy(safety_config())
    observation = Observation(run_id="run", step_index=0, mode="mock")
    with pytest.raises(SafetyViolation):
        guard.validate(ClickAction(x=1.1, y=0.5), observation)


def test_stale_live_click_is_blocked() -> None:
    guard = OperationPolicy(safety_config())
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(),
        telemetry_stale=True,
    )
    with pytest.raises(SafetyViolation):
        guard.validate(ClickAction(x=0.5, y=0.5), observation)


def test_stale_live_scroll_is_blocked() -> None:
    guard = OperationPolicy(safety_config())
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(),
        telemetry_stale=True,
    )
    with pytest.raises(SafetyViolation, match="telemetry is stale"):
        guard.validate(ScrollAction(x=0.5, y=0.5, notches=1), observation)


def test_live_screen_space_pointer_action_is_blocked() -> None:
    guard = OperationPolicy(safety_config())
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="Screen-space"):
        guard.validate(ClickAction(x=100, y=100, space=CoordinateSpace.SCREEN), observation)


def test_live_move_cursor_uses_the_same_bounds_as_clicks() -> None:
    guard = OperationPolicy(safety_config())
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    with pytest.raises(SafetyViolation, match="outside the Kenshi window"):
        guard.validate(MoveCursorAction(x=1280, y=20, space=CoordinateSpace.CLIENT), observation)


def test_live_client_pointer_requires_known_window_dimensions() -> None:
    guard = OperationPolicy(safety_config())
    observation = Observation(
        run_id="run", step_index=0, mode="live", telemetry=TelemetrySnapshot()
    )
    with pytest.raises(SafetyViolation, match="dimensions are unknown"):
        guard.validate(ClickAction(x=20, y=20, space=CoordinateSpace.CLIENT), observation)


def test_exact_squad_selection_can_reduce_a_current_multi_selection() -> None:
    config = safety_config().model_copy(
        update={"allow_action_kinds": ["select_squad_member_exact"]}
    )
    guard = OperationPolicy(
        config,
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    action = SelectSquadMemberExactAction(target_id="entity-nam")
    observation = Observation(
        run_id="squad-selection",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        telemetry=TelemetrySnapshot(
            capabilities=[
                "squad.basic",
                "ui.visible_controls",
                "control.select_squad_member",
                "identity.stable_handles",
            ],
            identity_session_id="session-exact-squad-selection",
            game=GameState(loaded=True, paused=True),
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
                selected_character_id="entity-twitch",
                selected_character_ids=["entity-nam", "entity-twitch"],
                visible_controls=[
                    VisibleUIControl(
                        label="Nam",
                        role="text",
                        bounds=NormalizedPointerBounds(
                            min_x=0.32,
                            max_x=0.38,
                            min_y=0.84,
                            max_y=0.95,
                        ),
                    )
                ],
                visible_controls_complete=True,
            ),
            squad=[
                CharacterState(
                    id="entity-nam",
                    name="Nam",
                    selected=True,
                ),
                CharacterState(
                    id="entity-twitch",
                    name="Twitch",
                    selected=True,
                ),
            ],
        ),
    )

    assert guard.validate(action, observation) == action


def test_dialogue_approach_preserves_a_valid_multi_selection() -> None:
    config = safety_config().model_copy(
        update={
            "allow_action_kinds": [
                *safety_config().allow_action_kinds,
                "approach_dialogue_target",
            ]
        }
    )
    guard = OperationPolicy(
        config,
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    action = ApproachDialogueTargetAction(target_id="entity-vendor")
    observation = Observation(
        run_id="group-dialogue",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        telemetry=TelemetrySnapshot(
            identity_session_id="session-group-dialogue",
            capabilities=[
                "control.approach_vendor",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.roles",
            ],
            game=GameState(loaded=True, paused=True),
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
                selected_character_id="entity-bark",
                selected_character_ids=["entity-bark", "entity-plant"],
            ),
            squad=[
                CharacterState(id="entity-bark", name="Bark", selected=True),
                CharacterState(id="entity-plant", name="Plant", selected=True),
            ],
            nearby_entities=[
                NearbyEntity(
                    id="entity-vendor",
                    name="Barman",
                    is_animal=False,
                    has_dialogue=True,
                    conscious=True,
                    disposition=Disposition.NEUTRAL,
                )
            ],
        ),
    )

    assert guard.validate(action, observation) == action


@pytest.mark.parametrize(
    "action",
    [
        SelectSquadMemberExactAction(target_id="entity-plant"),
        TravelToMapDestinationAction(destination_id="entity-hub"),
    ],
)
def test_native_party_control_accepts_an_exact_group_basis(
    action: SelectSquadMemberExactAction | TravelToMapDestinationAction,
) -> None:
    guard = OperationPolicy(
        safety_config().model_copy(update={"allow_action_kinds": [action.kind]}),
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    observation = Observation(
        run_id="native-party-control",
        step_index=0,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        telemetry=TelemetrySnapshot(
            identity_session_id="session-native-party-control",
            capabilities=[
                "control.select_squad_member",
                "control.travel_to_map_destination",
                "game.pause",
                "game.speed",
                "identity.stable_handles",
                "squad.basic",
                "squad.health",
                "world.known_map_destinations",
            ],
            game=GameState(loaded=True, paused=True, speed_multiplier=0.0),
            ui=UIState(
                active_screen="world",
                dialogue_open=False,
                modal_open=False,
                selected_character_id="entity-bark",
                selected_character_ids=["entity-bark", "entity-plant"],
            ),
            squad=[
                CharacterState(
                    id="entity-bark",
                    name="Bark",
                    selected=True,
                ),
                CharacterState(
                    id="entity-plant",
                    name="Plant",
                    selected=True,
                ),
            ],
            known_map_destinations=[
                KnownMapDestination(
                    id="entity-hub",
                    name="The Hub",
                    distance=1250.0,
                )
            ],
        ),
    )

    assert guard.validate(action, observation) == action


def test_live_pause_requires_known_current_state() -> None:
    guard = OperationPolicy(safety_config())
    unknown = Observation(run_id="run", step_index=0, mode="live", telemetry=TelemetrySnapshot())
    with pytest.raises(SafetyViolation, match="pause state is unknown"):
        guard.validate(PauseAction(paused=True), unknown)

    known = unknown.model_copy(
        update={"telemetry": TelemetrySnapshot(game=GameState(paused=False))}
    )
    assert guard.validate(PauseAction(paused=True), known).paused is True

    with pytest.raises(SafetyViolation, match="Direct live unpause"):
        guard.validate(PauseAction(paused=False), known)


def test_set_speed_unpause_requires_explicit_profile_authority() -> None:
    action = SetSpeedAction(speed=3)
    paused = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            capabilities=["game.pause", "game.speed"],
            game=GameState(loaded=True, paused=True, speed_multiplier=0.0),
        ),
    )

    with pytest.raises(SafetyViolation, match="Direct live unpause"):
        OperationPolicy(safety_config()).validate(action, paused)

    enabled = safety_config().model_copy(update={"allow_live_unpause_actions": True})
    assert OperationPolicy(enabled).validate(action, paused) == action


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (0.0, 0.0),
        (0.0, 1.0),
        (1.0, 0.0),
        (1.0, 1.0),
    ],
)
def test_normalized_pointer_boundary_is_closed(x: float, y: float) -> None:
    action = MoveCursorAction(x=x, y=y, space=CoordinateSpace.NORMALIZED)
    observation = Observation(run_id="run", step_index=0, mode="mock")
    assert (
        OperationPolicy(safety_config()).validate(action, observation) == action
    )


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (-0.001, 0.5),
        (1.001, 0.5),
        (0.5, -0.001),
        (0.5, 1.001),
    ],
)
def test_each_normalized_pointer_axis_fails_closed(x: float, y: float) -> None:
    with pytest.raises(SafetyViolation):
        OperationPolicy(safety_config()).validate(
            MoveCursorAction(x=x, y=y, space=CoordinateSpace.NORMALIZED),
            Observation(run_id="run", step_index=0, mode="mock"),
        )


@pytest.mark.parametrize(
    ("x", "y"),
    [(-0.001, 5.0), (5.0, -0.001)],
)
def test_each_client_pointer_axis_rejects_negative_values(x: float, y: float) -> None:
    with pytest.raises(SafetyViolation):
        OperationPolicy(safety_config()).validate(
            MoveCursorAction(x=x, y=y, space=CoordinateSpace.CLIENT),
            Observation(run_id="run", step_index=0, mode="mock"),
        )


def test_client_pointer_boundary_uses_both_current_dimensions() -> None:
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=1280, client_height=720)),
    )
    accepted = MoveCursorAction(x=1279, y=719, space=CoordinateSpace.CLIENT)
    assert (
        OperationPolicy(safety_config()).validate(accepted, observation)
        == accepted
    )
    for x, y in [(1280, 719), (1279, 720)]:
        with pytest.raises(SafetyViolation, match="outside the Kenshi window"):
            OperationPolicy(safety_config()).validate(
                MoveCursorAction(x=x, y=y, space=CoordinateSpace.CLIENT),
                observation,
            )


@pytest.mark.parametrize(
    ("width", "height"),
    [(None, 720), (1280, None)],
)
def test_live_client_pointer_requires_each_dimension(
    width: int | None,
    height: int | None,
) -> None:
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(ui=UIState(client_width=width, client_height=height)),
    )
    with pytest.raises(SafetyViolation, match="dimensions are unknown"):
        OperationPolicy(safety_config()).validate(
            MoveCursorAction(x=0, y=0, space=CoordinateSpace.CLIENT),
            observation,
        )


def generic_purchase_observation() -> Observation:
    bounds = NormalizedPointerBounds(
        min_x=0.30,
        max_x=0.34,
        min_y=0.20,
        max_y=0.24,
    )
    return Observation(
        run_id="generic-purchase",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            identity_session_id="session-generic-purchase",
            capabilities=[
                "ui.visible_controls",
                "ui.tooltip",
                "ui.inventory",
                "game.money",
                "game.pause",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.shop_owners",
                "squad.basic",
                "squad.inventory",
            ],
            game=GameState(loaded=True, paused=True, money=1000),
            ui=UIState(
                active_screen="trade",
                selected_character_id="player:1",
                selected_character_ids=["player:1"],
                tooltip_visible=True,
                tooltip_text="Dried Meat\n[Food]\nValue: c.38",
                tooltip_source_bounds=bounds,
                visible_controls=[
                    VisibleUIControl(
                        label="HEP",
                        role="text",
                        window="HEP",
                        bounds=NormalizedPointerBounds(
                            min_x=0.10,
                            max_x=0.14,
                            min_y=0.20,
                            max_y=0.24,
                        ),
                    ),
                    VisibleUIControl(
                        label="item_3",
                        role="item",
                        window="BARMAN",
                        item_name="Dried Meat",
                        item_base_value=38,
                        bounds=bounds,
                    ),
                ],
            ),
            squad=[
                CharacterState(
                    id="player:1",
                    name="Hep",
                    selected=True,
                    inventory_complete=True,
                )
            ],
            nearby_entities=[
                NearbyEntity(
                    id="seller:1",
                    name="Barman",
                    shop_inventory_owner=True,
                    disposition=Disposition.NEUTRAL,
                )
            ],
        ),
    )















def test_wait_limit() -> None:
    guard = OperationPolicy(safety_config())
    observation = Observation(run_id="run", step_index=0, mode="mock")
    assert (
        guard.validate(
            WaitAction(seconds=safety_config().max_wait_seconds),
            observation,
        ).seconds
        == safety_config().max_wait_seconds
    )
    with pytest.raises(SafetyViolation):
        guard.validate(WaitAction(seconds=4), observation)


def trade_in_progress_observation() -> Observation:
    """The r2 trade window: two inventories open, the trader's among them.

    Kenshi runs a trade as the player's inventory beside the trader's, moving
    items across before exiting. Live run
    live-shop-ownership-regression-20260729-r2 sat in exactly that state -
    `open_inventory_windows` 2, the seller's own BARMAN window advertising its
    stock - while `active_screen` collapsed to 'inventory', and the purchase
    guard refused four planner calls in ninety seconds saying the trade screen
    was not open. It was open; the operator could see it.
    """

    observation = generic_purchase_observation()
    assert observation.telemetry is not None
    return observation.model_copy(
        update={
            "telemetry": observation.telemetry.model_copy(
                update={
                    "ui": observation.telemetry.ui.model_copy(
                        update={
                            "active_screen": "inventory",
                            "open_inventory_windows": 2,
                        }
                    )
                }
            )
        }
    )


