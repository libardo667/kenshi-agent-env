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


def test_enumeration_does_not_consult_authorability() -> None:
    """The defect the watcher exists to surface.

    An `exactly_one` operation stays on the menu when a second character is
    selected, because enumeration and `is_currently_authorable` are separate
    authorities and the first never asks the second. Confirmed live against a
    two-character start: harvest_resource, move_in_direction, and
    perform_context_action were all offered and all refused.
    """

    pair = current_menu(observation_from_snapshot(snapshot(selected=[PRIMARY, SECOND])))
    single = current_menu(observation_from_snapshot(snapshot(selected=[PRIMARY])))

    offered_kinds = {row.operation_kind for row in pair.offers}

    assert "move_in_direction" in offered_kinds
    assert "move_in_direction" in pair.unauthorable_offer_kinds
    assert "move_in_direction" not in single.unauthorable_offer_kinds


def test_payload_carries_the_unauthorable_kinds() -> None:
    payload = menu_payload(
        current_menu(observation_from_snapshot(snapshot(selected=[PRIMARY, SECOND])))
    )

    assert payload["offer_count"] == len(payload["offers"])
    assert "move_in_direction" in payload["unauthorable_offer_kinds"]
    assert payload["selected_character_ids"] == [PRIMARY, SECOND]


def test_render_names_the_unauthorable_offers_with_their_requirement() -> None:
    body = "\n".join(
        render_menu(current_menu(observation_from_snapshot(snapshot(selected=[PRIMARY, SECOND]))))
    )

    assert "OFFERED BUT NOT AUTHORABLE" in body
    assert "selection_requirement=exactly_one" in body


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


def test_game_bindings_are_offered_regardless_of_world_state() -> None:
    """Characterization of a defect, not an endorsement of it.

    Discovered live: the game-binding adapter consults neither `game.loaded`,
    nor capabilities, nor staleness. With no world loaded and stale telemetry
    the planner is still offered every binding, including `quickload`,
    `editor_delete`, and `rebuild_navmesh`. Sections 3.7 and 10.4 of the
    interaction-scope plan require these to be withheld until a semantic
    adapter can state their contract; Slice 5 and Slice 7 own the fix.

    This test exists so that fix is a visible, deliberate change rather than a
    silent one. When bindings become gated, it fails and is rewritten.
    """

    unloaded = TelemetrySnapshot(
        sequence=1,
        captured_at=datetime.now(UTC),
        capabilities=[],
        game=GameState(loaded=False),
        ui=UIState(),
        squad=[],
    )
    menu = current_menu(observation_from_snapshot(unloaded, stale=True))

    binding_semantics = {
        row.semantic for row in menu.offers if row.operation_kind == "use_game_binding"
    }

    assert menu.offers
    assert {"quickload", "editor_delete", "rebuild_navmesh"} <= binding_semantics
