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
