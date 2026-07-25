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
    contract_for,
)
from kenshi_agent.models import (
    GAME_BINDING_KEYS,
    TOGGLE_GAME_BINDINGS,
    GameBinding,
    GameState,
    Observation,
    TelemetrySnapshot,
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
            capabilities=["game.pause"],
            game=GameState(loaded=loaded, paused=True),
        ),
        telemetry_stale=stale,
        objective="Play Kenshi.",
    )


def test_every_binding_maps_to_a_key() -> None:
    """A binding with no key would bind successfully and then send nothing."""

    for binding in GameBinding:
        assert binding in GAME_BINDING_KEYS, binding
        assert GAME_BINDING_KEYS[binding]


def test_destructive_bindings_are_absent_from_the_catalog() -> None:
    """An unattended agent must not be one keystroke from overwriting a save."""

    names = {binding.value for binding in GameBinding}
    assert not names & {
        "quicksave",
        "quickload",
        "editor_toggle",
        "rebuild_navmesh",
        "reload_biomes",
    }


def test_the_binding_action_is_contracted_and_planner_visible() -> None:
    action = UseGameBindingAction(
        binding=GameBinding.TOGGLE_INVENTORY,
        expected_effect="the inventory screen opens",
    )
    assert contract_for(action) is USE_GAME_BINDING_CONTRACT
    assert ACTION_CONTRACTS["use_game_binding"].planner_visible


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
