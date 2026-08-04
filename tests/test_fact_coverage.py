"""The fact-coverage audit: what the agent can know, and what it costs."""

from __future__ import annotations

from kenshi_agent.core.telemetry import (
    CharacterState,
    GameState,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from kenshi_agent.fact_coverage import FACTS, FactState, audit


def snapshot(
    *,
    capabilities: list[str] | None = None,
    screen: str | None = "world",
    dialogue_open: bool = False,
    controls: list[VisibleUIControl] | None = None,
    hunger: float | None = None,
    current_goal: str | None = None,
    location: str | None = None,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=1,
        capabilities=capabilities if capabilities is not None else ["game.pause"],
        game=GameState(loaded=True, paused=True, money=1000, location_name=location),
        ui=UIState(
            active_screen=screen,
            dialogue_open=dialogue_open,
            visible_controls=controls,
        ),
        squad=[
            CharacterState(
                id="hep",
                name="Hep",
                selected=True,
                hunger=hunger,
                current_goal=current_goal,
            )
        ],
    )


def _state(report_snapshot: TelemetrySnapshot, key: str) -> FactState:
    fact = next(item for item in FACTS if item.key == key)
    return fact.state(report_snapshot)


def test_a_fact_the_snapshot_carries_is_exported() -> None:
    assert _state(snapshot(), "world.money") is FactState.EXPORTED


def test_current_goal_requires_its_truthfulness_capability() -> None:
    unadvertised = snapshot(current_goal="Operating machine")
    advertised = snapshot(
        capabilities=["squad.current_goal"],
        current_goal="Operating machine",
    )

    assert _state(unadvertised, "self.current_goal") is FactState.DARK
    assert _state(advertised, "self.current_goal") is FactState.EXPORTED


def test_a_fact_reachable_only_by_acting_is_discoverable() -> None:
    """Without the native capability, hunger needs an exploratory UI action."""

    assert _state(snapshot(), "self.hunger") is FactState.DISCOVERABLE
    # Judged by advertised capability, not by whether a value happens to be set:
    # an empty inventory is information, and a null hunger is absence.
    assert (
        _state(snapshot(capabilities=["squad.hunger"]), "self.hunger")
        is FactState.EXPORTED
    )


def test_a_fact_with_no_route_at_all_is_dark() -> None:
    assert _state(snapshot(), "world.location_name") is FactState.DARK
    assert _state(snapshot(location="The Hub"), "world.location_name") is FactState.EXPORTED


def test_a_fact_its_context_cannot_speak_to_is_not_counted_against_us() -> None:
    """Dialogue options say nothing when no conversation is open.

    Counting those as missing would bury the real gaps in noise.
    """

    assert _state(snapshot(), "ui.dialogue_options") is FactState.NOT_APPLICABLE
    assert (
        _state(snapshot(dialogue_open=True), "ui.dialogue_options")
        is FactState.DISCOVERABLE
    ) or (
        _state(snapshot(dialogue_open=True), "ui.dialogue_options") is FactState.DARK
    )
    # Shop facts likewise only apply with a trading window open.
    assert _state(snapshot(), "shop.item_price") is FactState.NOT_APPLICABLE
    assert _state(snapshot(screen="trade"), "shop.item_price") is FactState.DISCOVERABLE


def test_named_cells_count_as_exported_but_ordinals_do_not() -> None:
    """The difference between reading a shop and hovering it blind."""

    bounds = NormalizedPointerBounds(min_x=0.1, max_x=0.2, min_y=0.1, max_y=0.2)
    ordinals = snapshot(
        screen="trade",
        controls=[VisibleUIControl(label="item_0", role="item", bounds=bounds)],
    )
    named = snapshot(
        screen="trade",
        controls=[VisibleUIControl(label="Dried Meat", role="item", bounds=bounds)],
    )
    assert _state(ordinals, "shop.item_names") is FactState.DISCOVERABLE
    assert _state(named, "shop.item_names") is FactState.EXPORTED


def test_the_report_totals_the_exploration_cost() -> None:
    """One number to drive down: round-trips before the agent can decide."""

    report = audit(snapshot(screen="trade"))
    assert report.exploration_cost == sum(
        fact.discovery_actions for fact in report.discoverable
    )
    assert report.exploration_cost > 0
    rendered = "\n".join(report.as_lines())
    assert "agent actions to learn" in rendered
    for fact in report.dark:
        assert fact.key in rendered


def test_every_fact_declares_how_it_would_be_learned() -> None:
    for fact in FACTS:
        assert fact.purpose, fact.key
        if fact.discovery:
            assert fact.discovery_actions > 0, fact.key
        else:
            # No route means it is dark, and dark facts cost nothing to *try*.
            assert fact.discovery_actions == 0, fact.key
