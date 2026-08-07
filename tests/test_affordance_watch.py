"""The read-only affordance watcher reports the menu a planner would receive.

These run on fixtures rather than a live Kenshi so they stay deterministic and
runnable without the game. The behaviour they pin was discovered live against a
two-character start; the fixture is what keeps it from silently reverting.
"""

from __future__ import annotations

from datetime import UTC, datetime

from kenshi_agent.core.telemetry import (
    CharacterState,
    GameState,
    TelemetrySnapshot,
    UIState,
)
from kenshi_agent.tooling.affordance_watch import (
    AffordanceMenu,
    current_menu,
    menu_payload,
    observation_from_snapshot,
    render_menu,
)

PRIMARY = "char-primary"
SECOND = "char-second"


def snapshot(*, selected: list[str], sequence: int = 10) -> TelemetrySnapshot:
    roster = [
        CharacterState(id=PRIMARY, name="Bombingham", selected=PRIMARY in selected, alive=True),
        CharacterState(id=SECOND, name="Barth", selected=SECOND in selected, alive=True),
    ]
    return TelemetrySnapshot(
        sequence=sequence,
        captured_at=datetime.now(UTC),
        capabilities=["squad.basic", "game.pause"],
        game=GameState(loaded=True, paused=True),
        ui=UIState(
            selected_character_id=selected[0] if selected else None,
            selected_character_ids=list(selected),
        ),
        squad=roster,
    )


def test_observation_is_synthesised_without_a_run_or_planner() -> None:
    """Enumeration needs telemetry, not a coordinator."""

    observation = observation_from_snapshot(snapshot(selected=[PRIMARY]))

    assert observation.mode == "live"
    assert observation.telemetry is not None
    assert observation.telemetry.sequence == 10
    assert observation.active_plan is None


def test_menu_reports_selection_and_roster_from_telemetry() -> None:
    menu = current_menu(observation_from_snapshot(snapshot(selected=[PRIMARY, SECOND])))

    assert menu.roster_size == 2
    assert menu.primary_character_id == PRIMARY
    assert menu.selected_character_ids == (PRIMARY, SECOND)
    assert menu.loaded is True


def test_fingerprint_ignores_sequence_but_tracks_selection() -> None:
    """Watch loops re-render on menu change, not on every telemetry tick."""

    first = current_menu(observation_from_snapshot(snapshot(selected=[PRIMARY], sequence=1)))
    later = current_menu(observation_from_snapshot(snapshot(selected=[PRIMARY], sequence=999)))
    changed = current_menu(
        observation_from_snapshot(snapshot(selected=[PRIMARY, SECOND], sequence=1))
    )

    assert first.fingerprint() == later.fingerprint()
    assert first.fingerprint() != changed.fingerprint()



def test_menu_never_offers_what_the_registry_would_refuse() -> None:
    """Enumeration and authorability answer to one owner now.

    The watcher keeps reporting this cross-check because a future adapter can
    reintroduce the disagreement; the assertion is that it is currently empty,
    not that the check is unnecessary.
    """

    for selected in ([PRIMARY], [PRIMARY, SECOND], []):
        menu = current_menu(observation_from_snapshot(snapshot(selected=selected)))
        assert menu.unauthorable_offer_kinds == (), (selected, menu.unauthorable_offer_kinds)


def test_payload_reports_the_authorability_cross_check() -> None:
    payload = menu_payload(
        current_menu(observation_from_snapshot(snapshot(selected=[PRIMARY, SECOND])))
    )

    assert payload["offer_count"] == len(payload["offers"])
    assert payload["unauthorable_offer_kinds"] == []
    assert payload["selected_character_ids"] == [PRIMARY, SECOND]


def test_render_names_unauthorable_offers_by_recipient_scope() -> None:
    """Rendered only when the cross-check finds something, so build one."""

    menu = AffordanceMenu(
        telemetry_sequence=1,
        stale=False,
        loaded=True,
        location_id="",
        primary_character_id=PRIMARY,
        selected_character_ids=(PRIMARY,),
        roster_size=1,
        offers=(),
        adapters_offering=(),
        adapters_silent=(),
        unauthorable_offer_kinds=("move_in_direction",),
    )

    body = "\n".join(render_menu(menu))

    assert "OFFERED BUT NOT AUTHORABLE" in body
    assert "recipient_scope=current_selection" in body


def test_empty_menu_states_that_absence_is_ambiguous() -> None:
    """An empty menu is a question until withholding reasons are typed.

    Constructed directly rather than from a snapshot, because no snapshot
    currently produces an empty menu - see the characterization below.
    """

    body = "\n".join(
        render_menu(
            AffordanceMenu(
                telemetry_sequence=1,
                stale=False,
                loaded=True,
                location_id="",
                primary_character_id="",
                selected_character_ids=(),
                roster_size=0,
                offers=(),
                adapters_offering=(),
                adapters_silent=("runtime",),
                unauthorable_offer_kinds=(),
            )
        )
    )

    assert "NO AFFORDANCES OFFERED" in body
    assert "never modeled" in body


