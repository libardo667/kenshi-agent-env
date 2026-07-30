"""The agent's ability to reach a screen at all.

Every one of these covers a way the agent was previously stuck: it could see an
inventory it could not open, and it tried to unpause by clicking the time-speed
buttons, which live telemetry showed leaves `game.paused` true.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kenshi_agent.action_contracts import (
    ACTION_CONTRACTS,
    USE_GAME_BINDING_CONTRACT,
    CompletionOwner,
    completion_contract_for,
    contract_for,
)
from kenshi_agent.affordance_parity import (
    AffordanceRoute,
    BindingDecision,
    BindingStatus,
    audit_binding_parity,
)
from kenshi_agent.models import (
    GAME_BINDING_KEYS,
    GAME_BINDING_MOUSE_BUTTONS,
    TOGGLE_GAME_BINDINGS,
    GameBinding,
    GameState,
    HotkeyAction,
    Observation,
    TelemetrySnapshot,
    UIState,
    UseGameBindingAction,
    WorldStateRevision,
)


def observation(*, loaded: bool = True, stale: bool = False) -> Observation:
    return Observation(
        run_id="binding-test",
        step_index=0,
        mode="live",
        world_revision=WorldStateRevision(telemetry_sequence=7),
        telemetry=TelemetrySnapshot(
            sequence=7,
            captured_at=datetime.now(UTC),
            capabilities=["game.money", "game.pause", "ui.inventory"],
            game=GameState(loaded=loaded, paused=True, money=1000),
            ui=UIState(
                open_inventory_windows=0,
                management_screen_open=False,
                stats_window_open=False,
            ),
        ),
        telemetry_stale=stale,
        objective="Play Kenshi.",
    )


def test_every_binding_maps_to_exactly_one_input() -> None:
    """A binding must resolve to one physical input, never zero or two."""

    for binding in GameBinding:
        assert (binding in GAME_BINDING_KEYS) != (
            binding in GAME_BINDING_MOUSE_BUTTONS
        ), binding


@pytest.mark.parametrize(
    ("binding", "expected_key", "expected_virtual_key"),
    [
        (GameBinding.BUILD_APPLY, "space", 0x20),
        (GameBinding.BUILD_MOVE_DOWN, "minus", 0xBD),
        (GameBinding.BUILD_MOVE_UP, "equals", 0xBB),
        (GameBinding.BUILD_ROTATE_LEFT, "comma", 0xBC),
        (GameBinding.BUILD_ROTATE_RIGHT, "period", 0xBE),
        (GameBinding.BUILD_TILT_DECREASE, "[", 0xDB),
        (GameBinding.BUILD_TILT_INCREASE, "]", 0xDD),
        (GameBinding.BUILD_UNDO, "backspace", 0x08),
        (GameBinding.CAMERA_TILT_UP, "comma", 0xBC),
        (GameBinding.CAMERA_TILT_DOWN, "period", 0xBE),
        (GameBinding.CYCLE_RUN_SPEED, "numpad6", 0x66),
        (GameBinding.EDITOR_DELETE, "delete", 0x2E),
        (GameBinding.FLOOR_DOWN, "pagedown", 0x22),
        (GameBinding.FLOOR_UP, "pageup", 0x21),
        (GameBinding.GIZMO_MOVE, "h", 0x48),
        (GameBinding.GIZMO_ROTATE, "j", 0x4A),
        (GameBinding.GIZMO_SCALE, "k", 0x4B),
    ],
)
def test_queued_binding_is_reachable_through_the_semantic_binding_action(
    binding: GameBinding,
    expected_key: str,
    expected_virtual_key: int,
) -> None:
    """Each parity item has a planner route, a bindable action, and real input."""

    report = audit_binding_parity()
    assert report.decisions[binding.value] == BindingDecision(
        status=BindingStatus.WIRED,
        route=AffordanceRoute("use_game_binding", binding.value),
    )

    action = UseGameBindingAction(
        binding=binding,
        expected_effect=f"apply {binding.value}",
    )
    result = USE_GAME_BINDING_CONTRACT.bind(action, observation())
    assert result.bound
    assert result.resolved_label == binding.value

    schema = UseGameBindingAction.model_json_schema()
    assert binding.value in schema["$defs"]["GameBinding"]["enum"]
    assert GAME_BINDING_KEYS[binding] == expected_key
    from kenshi_agent.control.win32 import Win32InputController

    assert Win32InputController._vk(expected_key) == expected_virtual_key


def test_editor_toggle_is_reachable_through_a_semantic_hotkey_binding() -> None:
    binding = GameBinding.EDITOR_TOGGLE
    from kenshi_agent.models import game_binding_primitive

    assert audit_binding_parity().decisions[binding.value] == BindingDecision(
        status=BindingStatus.WIRED,
        route=AffordanceRoute("use_game_binding", binding.value),
    )
    action = UseGameBindingAction(
        binding=binding,
        expected_effect="toggle the in-game editor",
    )
    assert USE_GAME_BINDING_CONTRACT.bind(action, observation()).bound
    assert game_binding_primitive(binding) == HotkeyAction(keys=["shift", "f12"])


def test_highlight_is_reachable_through_a_held_fifth_mouse_button() -> None:
    binding = GameBinding.HIGHLIGHT
    from kenshi_agent.control.win32 import mouse_button_input_spec
    from kenshi_agent.models import (
        MouseButton,
        MouseButtonAction,
        game_binding_primitive,
    )

    assert audit_binding_parity().decisions[binding.value] == BindingDecision(
        status=BindingStatus.WIRED,
        route=AffordanceRoute("use_game_binding", binding.value),
    )
    action = UseGameBindingAction(
        binding=binding,
        expected_effect="highlight world items while the binding is held",
    )
    assert USE_GAME_BINDING_CONTRACT.bind(action, observation()).bound
    assert game_binding_primitive(binding) == MouseButtonAction(
        button=MouseButton.X2,
        hold_seconds=0.25,
    )
    assert mouse_button_input_spec(MouseButton.X2) == (0x0080, 0x0100, 0x0002)


def test_medic_is_reachable_through_a_semantic_toggle_binding() -> None:
    binding = GameBinding.MEDIC
    from kenshi_agent.control.win32 import Win32InputController
    from kenshi_agent.models import KeyAction, game_binding_primitive

    assert audit_binding_parity().decisions[binding.value] == BindingDecision(
        status=BindingStatus.WIRED,
        route=AffordanceRoute("use_game_binding", binding.value),
    )
    action = UseGameBindingAction(
        binding=binding,
        expected_effect="toggle the medic job for the selected squad members",
    )
    assert USE_GAME_BINDING_CONTRACT.bind(action, observation()).bound
    assert game_binding_primitive(binding) == KeyAction(key="numpad7")
    assert Win32InputController._vk("numpad7") == 0x67
    assert binding in TOGGLE_GAME_BINDINGS


def test_mouse_command_binds_one_current_world_target_at_observed_geometry() -> None:
    from kenshi_agent.action_contracts import COMMAND_WORLD_TARGET_CONTRACT
    from kenshi_agent.models import (
        CommandWorldTargetAction,
        ContextActionKind,
        NormalizedPointerBounds,
        PointerActionClass,
        Vec2,
        Vec3,
        WorldTarget,
    )

    target = WorldTarget(
        id="entity-copper",
        name="Copper Resource",
        kind="natural_resource",
        position=Vec3(x=10.0, y=0.0, z=20.0),
        distance=30.0,
        context_actions=[ContextActionKind.OPERATE],
        default_task="operate_machinery",
        mining_resource_level=0.8,
        screen_position=Vec2(x=0.4, y=0.6),
    )
    state = observation()
    assert state.telemetry is not None
    state = state.model_copy(
        update={
            "telemetry": state.telemetry.model_copy(
                update={
                    "capabilities": [
                        *state.telemetry.capabilities,
                        "world.context_targets",
                        "world.context_target_screen_positions",
                    ],
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                    ),
                    "world_targets": [target],
                }
            )
        }
    )
    action = CommandWorldTargetAction(
        target_id=target.id,
        context_action=ContextActionKind.OPERATE,
    )

    assert audit_binding_parity().decisions["mouse_command"] == BindingDecision(
        status=BindingStatus.WIRED,
        route=AffordanceRoute("command_world_target"),
    )
    binding = COMMAND_WORLD_TARGET_CONTRACT.bind(action, state)
    assert binding.bound
    assert binding.target_id == target.id
    assert binding.resolved_bounds == NormalizedPointerBounds(
        min_x=0.4,
        max_x=0.4,
        min_y=0.6,
        max_y=0.6,
    )
    assert COMMAND_WORLD_TARGET_CONTRACT.pointer_class is PointerActionClass.SEMANTIC_CURRENT
    assert state.context_target_digest()[0]["screen_position"] == {
        "x": 0.4,
        "y": 0.6,
    }


def test_mouse_rotate_is_reachable_through_a_bounded_semantic_drag() -> None:
    from kenshi_agent.action_contracts import ROTATE_CAMERA_CONTRACT
    from kenshi_agent.models import (
        CameraRotationDirection,
        MouseButton,
        MouseDragAction,
        PointerActionClass,
        RotateCameraAction,
        camera_rotation_primitive,
    )

    state = observation()
    assert state.telemetry is not None
    state = state.model_copy(
        update={
            "telemetry": state.telemetry.model_copy(
                update={
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                    )
                }
            )
        }
    )
    action = RotateCameraAction(direction=CameraRotationDirection.RIGHT)

    assert audit_binding_parity().decisions["mouse_rotate"] == BindingDecision(
        status=BindingStatus.WIRED,
        route=AffordanceRoute("rotate_camera"),
    )
    assert ROTATE_CAMERA_CONTRACT.bind(action, state).bound
    primitive = camera_rotation_primitive(action)
    assert primitive == MouseDragAction(
        button=MouseButton.MIDDLE,
        delta_x=-96,
        delta_y=0,
        steps=8,
    )
    assert ROTATE_CAMERA_CONTRACT.pointer_class is (
        PointerActionClass.COORDINATE_INDEPENDENT
    )


def test_binding_catalog_contains_only_wired_decisions() -> None:
    names = {binding.value for binding in GameBinding}
    report = audit_binding_parity()

    assert not names & set(report.with_status(BindingStatus.MISSING))
    assert not names & set(report.with_status(BindingStatus.EXEMPT))


def test_the_binding_action_is_contracted_and_planner_visible() -> None:
    action = UseGameBindingAction(
        binding=GameBinding.TOGGLE_INVENTORY,
        expected_effect="the inventory screen opens",
    )
    assert contract_for(action) is USE_GAME_BINDING_CONTRACT
    assert ACTION_CONTRACTS["use_game_binding"].planner_visible


def test_binding_vocabulary_is_schema_side_while_completion_state_stays_dynamic() -> None:
    closed = observation()
    closed_binding_action = next(
        action
        for action in closed.semantic_action_digest()
        if action["kind"] == "use_game_binding"
    )
    assert "available_bindings" not in closed_binding_action

    schema = UseGameBindingAction.model_json_schema()
    assert set(schema["$defs"]["GameBinding"]["enum"]) == {
        binding.value for binding in GameBinding
    }

    assert closed.telemetry is not None
    opened_telemetry = closed.telemetry.model_copy(
        update={
            "ui": closed.telemetry.ui.model_copy(
                update={"management_screen_open": True}
            )
        }
    )
    opened = closed.model_copy(update={"telemetry": opened_telemetry})
    opened_binding_action = next(
        action
        for action in opened.semantic_action_digest()
        if action["kind"] == "use_game_binding"
    )
    closed_map = closed_binding_action["runtime_completion_conditions"]["toggle_map"]
    opened_map = opened_binding_action["runtime_completion_conditions"]["toggle_map"]
    assert closed_map["expected"] is True
    assert opened_map["expected"] is False


def test_binding_runtime_conditions_contain_only_observable_transitions() -> None:

    binding_action = next(
        action
        for action in observation().semantic_action_digest()
        if action["kind"] == "use_game_binding"
    )

    inventory_condition = binding_action["runtime_completion_conditions"][
        "toggle_inventory"
    ]
    assert inventory_condition["path"] == "telemetry.ui.open_inventory_windows"
    assert inventory_condition["operator"] == "not_equals"
    assert inventory_condition["expected"] == 0
    assert not {
        "pause",
        "speed_1",
        "speed_2",
        "speed_3",
    } & set(binding_action["runtime_completion_conditions"])


@pytest.mark.parametrize(
    "binding",
    [
        GameBinding.PAUSE,
        GameBinding.SPEED_1,
        GameBinding.SPEED_2,
        GameBinding.SPEED_3,
    ],
)
def test_raw_time_key_cannot_bind_as_a_planner_affordance(
    binding: GameBinding,
) -> None:
    action = UseGameBindingAction(
        binding=binding,
        expected_effect="change playback",
    )

    result = USE_GAME_BINDING_CONTRACT.bind(action, observation())

    assert not result.bound
    assert "set_speed" in result.reason


def test_inventory_binding_owns_its_inventory_signal() -> None:
    from kenshi_agent.dialogue_interaction import _step_action_errors
    from kenshi_agent.models import (
        Condition,
        ConditionKind,
        ConditionOperator,
        ControlMode,
        IdempotencyPolicy,
        PlanStep,
    )

    unrelated_screen = Condition(
        kind=ConditionKind.FIELD,
        path="telemetry.ui.active_screen",
        operator=ConditionOperator.EQUALS,
        expected="trade",
        max_age_seconds=2.0,
    )
    step = PlanStep(
        step_id="open-inventory",
        action=UseGameBindingAction(
            binding=GameBinding.TOGGLE_INVENTORY,
            expected_effect="open the selected character inventory",
        ),
        preconditions=[unrelated_screen],
        success_conditions=[unrelated_screen],
        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
        retry_budget=0,
        timeout_seconds=10.0,
    )

    errors = _step_action_errors(
        step,
        observation(),
        control_mode=ControlMode.NATIVE_ASSISTED,
        require_binding=False,
    )
    assert errors == []
    completion = completion_contract_for(step.action, observation())
    assert completion.owner is CompletionOwner.RUNTIME_CONDITIONS
    assert [condition.path for condition in completion.conditions] == [
        "telemetry.ui.open_inventory_windows"
    ]


def test_a_binding_binds_on_a_loaded_game() -> None:
    action = UseGameBindingAction(
        binding=GameBinding.TOGGLE_MAP,
        expected_effect="the map opens",
    )
    binding = USE_GAME_BINDING_CONTRACT.bind(action, observation())
    assert binding.bound
    assert binding.resolved_label == "toggle_map"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"loaded": False}, "no loaded game"),
        ({"stale": True}, "stale"),
    ],
)
def test_a_binding_refuses_when_the_key_would_vanish(
    kwargs: dict[str, bool], expected: str
) -> None:
    """A key sent at a loading screen leaves no evidence either way."""

    action = UseGameBindingAction(
        binding=GameBinding.PAUSE,
        expected_effect="the game unpauses",
    )
    binding = USE_GAME_BINDING_CONTRACT.bind(action, observation(**kwargs))
    assert not binding.bound
    assert expected in binding.reason


def test_pause_uses_the_key_kenshi_actually_binds() -> None:
    """Live evidence: clicking the time-speed buttons left game.paused true."""

    assert GAME_BINDING_KEYS[GameBinding.PAUSE] == "space"
    assert GAME_BINDING_KEYS[GameBinding.TOGGLE_INVENTORY] == "i"
    assert GAME_BINDING_KEYS[GameBinding.TOGGLE_MAP] == "m"
    assert GAME_BINDING_KEYS[GameBinding.TOGGLE_STATS] == "c"


def test_toggles_are_marked_and_non_toggles_are_not() -> None:
    """A retried toggle undoes itself; a retried camera pan is just more pan."""

    assert GameBinding.TOGGLE_INVENTORY in TOGGLE_GAME_BINDINGS
    assert GameBinding.PAUSE in TOGGLE_GAME_BINDINGS
    assert GameBinding.CYCLE_RUN_SPEED in TOGGLE_GAME_BINDINGS
    assert GameBinding.CAMERA_LEFT not in TOGGLE_GAME_BINDINGS
    assert GameBinding.SPEED_2 not in TOGGLE_GAME_BINDINGS


def _control(role: str, index: int) -> object:
    from kenshi_agent.models import NormalizedPointerBounds, VisibleUIControl

    return VisibleUIControl(
        label=f"{role}_{index}",
        role=role,
        window="w",
        bounds=NormalizedPointerBounds(min_x=0.0, min_y=0.0, max_x=0.1, max_y=0.1),
    )


def test_the_control_budget_never_starves_a_role() -> None:
    """A trade screen exports 206 controls with text emitted last.

    A flat prefix therefore dropped every text widget, which is where Kenshi
    puts its refusals: the agent could be told "you can't afford that" and see a
    screen identical to the one before it acted.
    """

    from collections import Counter

    from kenshi_agent.models import budgeted_visible_controls

    controls = (
        [_control("button", i) for i in range(60)]
        + [_control("item", i) for i in range(120)]
        + [_control("text", i) for i in range(26)]
    )

    prefix_roles = Counter(c.role for c in controls[:120])
    assert prefix_roles["text"] == 0, "precondition: the old prefix dropped all text"

    budgeted = budgeted_visible_controls(controls, 120)
    assert len(budgeted) == 120
    roles = Counter(c.role for c in budgeted)
    assert roles["text"] == 26, "every text widget fits and must survive"
    assert roles["button"] > 0 and roles["item"] > 0

    # Document order is preserved, so positional reasoning still holds.
    positions = [controls.index(c) for c in budgeted]
    assert positions == sorted(positions)


def test_a_short_control_list_is_returned_untouched() -> None:
    from kenshi_agent.models import budgeted_visible_controls

    controls = [_control("button", i) for i in range(5)]
    assert budgeted_visible_controls(controls, 120) == controls


def _windowed(window: str, role: str, index: int, y: float) -> object:
    from kenshi_agent.models import NormalizedPointerBounds, VisibleUIControl

    return VisibleUIControl(
        label=f"{window}_{role}_{index}",
        role=role,
        window=window,
        bounds=NormalizedPointerBounds(min_x=0.2, min_y=y, max_x=0.6, max_y=y + 0.05),
    )


def _observation_with(controls: list[object]) -> Observation:
    base = observation()
    telemetry = base.telemetry
    assert telemetry is not None
    return base.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "capabilities": [*telemetry.capabilities, "ui.visible_controls"],
                    "ui": telemetry.ui.model_copy(update={"visible_controls": controls}),
                }
            )
        }
    )


def test_a_scroll_binds_to_the_named_window_bounds() -> None:
    """Shop stock past the first screenful is not exported at all."""

    from kenshi_agent.action_contracts import SCROLL_SCREEN_CONTRACT
    from kenshi_agent.models import ScrollScreenAction

    controls = [
        _windowed("BARMAN", "item", 0, 0.10),
        _windowed("BARMAN", "item", 1, 0.30),
        _windowed("HEP", "item", 0, 0.70),
    ]
    action = ScrollScreenAction(window="BARMAN", notches=-3)
    binding = SCROLL_SCREEN_CONTRACT.bind(action, _observation_with(controls))

    assert binding.bound
    assert binding.resolved_bounds is not None
    # Spans only the named window, never the one behind it.
    assert binding.resolved_bounds.min_y == 0.10
    assert binding.resolved_bounds.max_y == 0.35


def test_a_scroll_refuses_a_window_that_is_not_open() -> None:
    """Otherwise the notches land on whatever is behind it."""

    from kenshi_agent.action_contracts import SCROLL_SCREEN_CONTRACT
    from kenshi_agent.models import ScrollScreenAction

    action = ScrollScreenAction(window="TRADER", notches=2)
    binding = SCROLL_SCREEN_CONTRACT.bind(
        action, _observation_with([_windowed("HEP", "item", 0, 0.5)])
    )
    assert not binding.bound
    assert "nothing to scroll" in binding.reason


def test_a_scroll_must_actually_move() -> None:
    import pydantic

    from kenshi_agent.models import ScrollScreenAction

    with pytest.raises(pydantic.ValidationError):
        ScrollScreenAction(window="BARMAN", notches=0)


def _trade_observation(*, selected_name: str = "HEP") -> Observation:
    """A trade screen: our inventory and the trader's, side by side."""

    from kenshi_agent.models import (
        CharacterState,
        Disposition,
        NearbyEntity,
        NormalizedPointerBounds,
        VisibleUIControl,
    )

    def cell(window: str, index: int, name: str, value: int) -> VisibleUIControl:
        return VisibleUIControl(
            label=f"item_{index}",
            role="item",
            window=window,
            item_name=name,
            item_value=value,
            bounds=NormalizedPointerBounds(
                min_x=0.1, min_y=0.1, max_x=0.15, max_y=0.15
            ),
        )

    base = observation()
    telemetry = base.telemetry
    assert telemetry is not None
    return base.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "capabilities": [
                        *telemetry.capabilities,
                        "ui.visible_controls",
                        "ui.inventory",
                        "squad.inventory",
                        "game.money",
                        "identity.stable_handles",
                        "nearby.characters",
                        "nearby.shop_owners",
                    ],
                    "squad": [
                        CharacterState(id="c-hep", name=selected_name, selected=True)
                    ],
                    "active_shop_trader_count": 1,
                    "nearby_entities": [
                        NearbyEntity(
                            id="e-barman",
                            name="Barman",
                            disposition=Disposition.NEUTRAL,
                            shop_inventory_owner=True,
                        )
                    ],
                    "ui": telemetry.ui.model_copy(
                        update={
                            "visible_controls": [
                                cell("HEP", 0, "Iron Club", 240),
                                cell("BARMAN", 1, "Foodcube", 60),
                            ]
                        }
                    ),
                }
            )
        }
    )


def test_selling_binds_to_our_own_inventory_cell() -> None:
    from kenshi_agent.action_contracts import SELL_ITEM_CONTRACT
    from kenshi_agent.models import SellItemAction

    action = SellItemAction(
        cell_label="item_0",
        item_name="Iron Club",
        window="HEP",
        buyer_id="e-barman",
    )
    binding = SELL_ITEM_CONTRACT.bind(action, _trade_observation())
    assert binding.bound
    assert binding.target_id == "e-barman"


def test_selling_refuses_a_cell_in_the_traders_window() -> None:
    """Cell ordinals run across both inventories; the window is the owner."""

    from kenshi_agent.action_contracts import SELL_ITEM_CONTRACT
    from kenshi_agent.models import SellItemAction

    action = SellItemAction(
        cell_label="item_1",
        item_name="Foodcube",
        window="BARMAN",
        buyer_id="e-barman",
    )
    binding = SELL_ITEM_CONTRACT.bind(action, _trade_observation())
    assert not binding.bound
    assert "not the selected character's own inventory" in binding.reason


def test_selling_refuses_when_the_cell_holds_something_else() -> None:
    from kenshi_agent.action_contracts import SELL_ITEM_CONTRACT
    from kenshi_agent.models import SellItemAction

    action = SellItemAction(
        cell_label="item_0",
        item_name="Foodcube",
        window="HEP",
        buyer_id="e-barman",
    )
    binding = SELL_ITEM_CONTRACT.bind(action, _trade_observation())
    assert not binding.bound
    assert "holds 'Iron Club'" in binding.reason


def test_equipping_refuses_while_a_trade_is_open() -> None:
    """The same right-click sells instead, and the item is gone irreversibly."""

    from kenshi_agent.action_contracts import EQUIP_ITEM_CONTRACT
    from kenshi_agent.models import EquipItemAction

    base = _trade_observation()
    telemetry = base.telemetry
    assert telemetry is not None
    trading = base.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "ui": telemetry.ui.model_copy(
                        update={"open_inventory_windows": 2}
                    )
                }
            )
        }
    )

    action = EquipItemAction(cell_label="item_0", item_name="Iron Club", window="HEP")
    binding = EQUIP_ITEM_CONTRACT.bind(action, trading)
    assert not binding.bound
    assert "sells the item instead" in binding.reason


def test_equipping_binds_with_no_trade_open() -> None:
    from kenshi_agent.action_contracts import EQUIP_ITEM_CONTRACT
    from kenshi_agent.models import EquipItemAction

    base = _trade_observation()
    telemetry = base.telemetry
    assert telemetry is not None
    no_trade = base.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "nearby_entities": [],
                    "ui": telemetry.ui.model_copy(
                        update={"open_inventory_windows": 1}
                    ),
                }
            )
        }
    )

    action = EquipItemAction(cell_label="item_0", item_name="Iron Club", window="HEP")
    binding = EQUIP_ITEM_CONTRACT.bind(action, no_trade)
    assert binding.bound
    assert "no trade open" in binding.reason


def test_equipping_refuses_another_owners_window() -> None:
    from kenshi_agent.action_contracts import EQUIP_ITEM_CONTRACT
    from kenshi_agent.models import EquipItemAction

    base = _trade_observation()
    telemetry = base.telemetry
    assert telemetry is not None
    no_trade = base.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "ui": telemetry.ui.model_copy(
                        update={"open_inventory_windows": 1}
                    )
                }
            )
        }
    )

    action = EquipItemAction(cell_label="item_1", item_name="Foodcube", window="BARMAN")
    binding = EQUIP_ITEM_CONTRACT.bind(action, no_trade)
    assert not binding.bound
    assert "own inventory" in binding.reason


def test_price_separates_cells_that_share_a_name() -> None:
    """The live Barman stocks five cells all labelled "Tooth Pick".

    Two are worth c.809 and three c.390 - different weapon grades wearing the
    same name. Refusing on the shared label made every one of them unbuyable, so
    the price the planner already states is part of the reference.
    """

    from kenshi_agent.action_contracts import PURCHASE_ITEM_CONTRACT
    from kenshi_agent.models import (
        Disposition,
        NearbyEntity,
        NormalizedPointerBounds,
        PurchaseItemAction,
        VisibleUIControl,
    )

    def pick(value: int, y: float) -> VisibleUIControl:
        return VisibleUIControl(
            label="Tooth Pick",
            role="item",
            window="BARMAN",
            item_name="Tooth Pick",
            item_value=value,
            bounds=NormalizedPointerBounds(
                min_x=0.3, min_y=y, max_x=0.34, max_y=y + 0.04
            ),
        )

    base = observation()
    telemetry = base.telemetry
    assert telemetry is not None
    state = base.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "capabilities": [
                        *telemetry.capabilities,
                        "ui.visible_controls",
                        "ui.tooltip",
                        "nearby.shop_owners",
                    ],
                    "nearby_entities": [
                        NearbyEntity(
                            id="e-barman",
                            name="Barman",
                            disposition=Disposition.NEUTRAL,
                            shop_inventory_owner=True,
                        )
                    ],
                    "ui": telemetry.ui.model_copy(
                        update={
                            "visible_controls": [
                                pick(809, 0.18),
                                pick(390, 0.23),
                                pick(809, 0.28),
                                pick(390, 0.33),
                                pick(390, 0.38),
                            ]
                        }
                    ),
                }
            )
        }
    )

    def buy(price: int):
        return PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Tooth Pick",
                item_name="Tooth Pick",
                expected_price=price,
                window="BARMAN",
                seller_id="e-barman",
            ),
            state,
        )

    # Both grades are reachable; interchangeable duplicates do not block.
    assert buy(809).bound, buy(809).reason
    assert buy(390).bound, buy(390).reason
    # A price nothing is offered at is still refused.
    assert not buy(5).bound


def test_inert_condition_fields_are_normalised_not_rejected() -> None:
    """Six different models annotated these conditions the same harmless way.

    `telemetry_fresh` asks one question - is telemetry current - and evaluation
    reads neither `path` nor `target_id` when answering it; the same is true of
    `target_id` on a capability condition. Refusing a whole plan over a field
    that cannot change its meaning threw away every plan every model produced,
    while the field branch of the same validator had always normalised the
    equivalent redundancy instead.
    """

    from kenshi_agent.models import (
        Condition,
        ConditionKind,
        ConditionOperator,
    )

    fresh = Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        path="telemetry.game.paused",
        target_id="entity-barman",
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=2.0,
    )
    assert fresh.path is None
    assert fresh.target_id is None

    capability = Condition(
        kind=ConditionKind.CAPABILITY,
        path="ui.visible_controls",
        target_id="entity-barman",
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=2.0,
    )
    assert capability.path == "ui.visible_controls"
    assert capability.target_id is None


def test_a_capability_condition_still_requires_a_path() -> None:
    """Unlike the inert fields, this one has no meaning without it."""

    import pydantic

    from kenshi_agent.models import Condition, ConditionKind, ConditionOperator

    with pytest.raises(pydantic.ValidationError):
        Condition(
            kind=ConditionKind.CAPABILITY,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=2.0,
        )


def test_a_field_path_in_required_capabilities_does_not_kill_the_plan() -> None:
    """Three of five benchmarked models made exactly this mistake.

    Evaluation independently enforces the capability behind a condition's own
    field path, so an entry here can only add strictness - a wrong one cannot
    let an unsafe condition through, only destroy a sound plan.
    """

    from kenshi_agent.models import Condition, ConditionKind, ConditionOperator

    condition = Condition(
        kind=ConditionKind.FIELD,
        path="telemetry.ui.active_screen",
        operator=ConditionOperator.EQUALS,
        expected="trade",
        max_age_seconds=2.0,
        required_capabilities=["telemetry.ui.active_screen", "ui.inventory"],
    )
    # The field path is dropped; the real capability name survives.
    assert condition.required_capabilities == ["ui.inventory"]


def test_a_capability_condition_reads_its_subject_from_required_capabilities() -> None:
    """The commonest single failure across benchmarked models.

    They state the capability in `required_capabilities` and leave `path` unset.
    One named capability is an unambiguous subject, so read it.
    """

    from kenshi_agent.models import Condition, ConditionKind, ConditionOperator

    condition = Condition(
        kind=ConditionKind.CAPABILITY,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=2.0,
        required_capabilities=["ui.inventory"],
    )
    assert condition.path == "ui.inventory"


def test_several_named_capabilities_are_all_enforced() -> None:
    """`path` names one, but evaluation enforces every entry, so nothing is lost."""

    from kenshi_agent.models import Condition, ConditionKind, ConditionOperator

    condition = Condition(
        kind=ConditionKind.CAPABILITY,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=2.0,
        required_capabilities=["ui.inventory", "ui.dialogue"],
    )
    assert condition.path == "ui.inventory"
    assert condition.required_capabilities == ["ui.inventory", "ui.dialogue"]


def test_a_capability_name_used_as_a_field_path_is_read_as_a_capability() -> None:
    """One flat enum offers both vocabularies with no way to tell them apart."""

    from kenshi_agent.models import Condition, ConditionKind, ConditionOperator

    condition = Condition(
        kind=ConditionKind.FIELD,
        path="squad.inventory",
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=2.0,
    )
    assert condition.kind is ConditionKind.CAPABILITY
    assert condition.path == "squad.inventory"


def test_the_observation_can_carry_planner_feedback() -> None:
    """A deterministic planner mistake must not be remade on every retry.

    A live run ended after 21 identical validation failures, each replanned from
    an observation that said nothing about the previous twenty.
    """

    base = observation()
    with_feedback = base.model_copy(
        update={"planner_feedback": "Fix exactly this: capability needs a path."}
    )
    payload = with_feedback.planner_payload(max_chars=6000)
    assert "capability needs a path" in payload
    # Survives a budget far too small for the whole observation, because a
    # correction the planner cannot see is a correction that does not happen.
    tight = with_feedback.planner_payload(max_chars=4200)
    assert "capability needs a path" in tight


def test_a_long_caption_does_not_blind_the_agent() -> None:
    """One over-long widget caption must not invalidate the whole snapshot.

    Live, a bar rumour running past 500 characters made an entire observation
    unparseable - no cells, no money, no screen - from one label. Telemetry is
    evidence we receive, not a document we author.
    """

    from kenshi_agent.models import NormalizedPointerBounds, VisibleUIControl

    rumour = "#140806Hoo boy, did I get a story for you. " + ("blah " * 200)
    assert len(rumour) > 500

    control = VisibleUIControl(
        label=rumour,
        role="text",
        window="",
        bounds=NormalizedPointerBounds(min_x=0.1, min_y=0.1, max_x=0.2, max_y=0.2),
    )
    assert len(control.label) == 500
    assert control.label.startswith("#140806Hoo boy")
    assert control.label.endswith("...")


def _purchase_guard_state(*, paused: bool):
    """A live trade screen with everything a purchase needs except pause."""

    from datetime import UTC, datetime

    from kenshi_agent.models import (
        CharacterState,
        Disposition,
        GameState,
        NearbyEntity,
        NormalizedPointerBounds,
        TelemetrySnapshot,
        UIState,
        VisibleUIControl,
        WorldStateRevision,
    )

    cell = VisibleUIControl(
        label="Dried Meat",
        role="item",
        window="BARMAN",
        item_name="Dried Meat",
        item_value=38,
        bounds=NormalizedPointerBounds(min_x=0.3, min_y=0.2, max_x=0.34, max_y=0.24),
    )
    return Observation(
        run_id="guard",
        step_index=0,
        mode="live",
        world_revision=WorldStateRevision(telemetry_sequence=11),
        telemetry=TelemetrySnapshot(
            sequence=11,
            captured_at=datetime.now(UTC),
            identity_session_id="sess-1",
            capabilities=[
                "ui.visible_controls", "ui.tooltip", "ui.inventory", "game.money",
                "game.pause", "identity.stable_handles", "nearby.characters",
                "nearby.shop_owners", "squad.inventory", "squad.basic",
            ],
            game=GameState(loaded=True, paused=paused, money=1000),
            squad=[CharacterState(id="c-hep", name="Hep", selected=True)],
            active_shop_trader_count=1,
            nearby_entities=[
                NearbyEntity(
                    id="e-barman", name="Barman",
                    disposition=Disposition.NEUTRAL, shop_inventory_owner=True,
                )
            ],
            ui=UIState(
                active_screen="trade",
                open_inventory_windows=2,
                selected_character_ids=["c-hep"],
                selected_character_id="c-hep",
                visible_controls=[cell],
            ),
        ),
        telemetry_stale=False,
        objective="buy",
    )


def test_a_running_world_does_not_block_a_purchase_by_default() -> None:
    """An agent has to unpause to walk anywhere it could shop.

    Two unconditional `paused is not True` checks refused every purchase a live
    run could reach, ignoring the profile's require_paused_between_actions=false.
    """

    from kenshi_agent.config import SafetyConfig
    from kenshi_agent.models import PurchaseItemAction
    from kenshi_agent.safety import ActionGuard, SafetyViolation
    from kenshi_agent.skills import MacroRegistry

    action = PurchaseItemAction(
        cell_label="Dried Meat", item_name="Dried Meat", expected_price=38,
        window="BARMAN", seller_id="e-barman",
    )
    running = _purchase_guard_state(paused=False)

    macros = MacroRegistry({})
    lenient = ActionGuard(
        SafetyConfig(
            require_paused_between_actions=False,
            allow_action_kinds=["purchase_item"],
        ),
        macros,
    )
    lenient.validate(action, running)  # must not raise

    strict = ActionGuard(
        SafetyConfig(
            require_paused_between_actions=True,
            allow_action_kinds=["purchase_item"],
        ),
        macros,
    )
    with pytest.raises(SafetyViolation, match="require_paused_between_actions"):
        strict.validate(action, running)


def test_a_purchase_contract_owns_transfer_conservation() -> None:
    """A no-op purchase once reported DONE three times running.

    The controller now owns the whole terminal: it must prove both money loss
    and selected-character inventory gain for every requested unit. The planner
    neither restates that motor effect nor gets to call an unverified click done.
    """

    from kenshi_agent.dialogue_interaction import _step_action_errors
    from kenshi_agent.models import (
        Condition,
        ConditionKind,
        ConditionOperator,
        ControlMode,
        IdempotencyPolicy,
        PlanStep,
        PurchaseItemAction,
    )

    action = PurchaseItemAction(
        cell_label="Dried Meat", item_name="Dried Meat", expected_price=38,
        window="BARMAN", seller_id="e-barman",
    )

    screen_only = Condition(
        kind=ConditionKind.FIELD, path="telemetry.ui.active_screen",
        operator=ConditionOperator.EQUALS, expected="trade", max_age_seconds=2.0,
    )

    def step_with(*conditions: Condition) -> PlanStep:
        return PlanStep(
            step_id="buy",
            action=action,
            preconditions=[screen_only],
            success_conditions=list(conditions),
            idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            retry_budget=0,
            timeout_seconds=10.0,
        )

    planner_did_not_duplicate = _step_action_errors(
        step_with(screen_only), observation(),
        control_mode=ControlMode.NATIVE_ASSISTED, require_binding=False,
    )
    assert not any(
        "completion" in error or "causal success" in error
        for error in planner_did_not_duplicate
    )

    completion = completion_contract_for(action, observation())
    assert completion.owner is CompletionOwner.CONTROLLER_TERMINAL
    assert completion.conditions == ()
