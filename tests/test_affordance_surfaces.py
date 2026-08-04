"""The surface registry is the one hand-authored inventory, so it is guarded."""

from __future__ import annotations

from pathlib import Path

from kenshi_agent.tooling.affordance_surfaces import (
    SURFACES,
    SurfaceStatus,
    render_surface_registry,
    surfaces_by_status,
)

ROOT = Path(__file__).resolve().parents[1]


def test_a_surface_may_not_claim_a_size_it_has_not_captured() -> None:
    """A count on an unexamined surface is a guess dressed as a measurement.

    Binding parity read `31 / 31 covered` because a number existed where no
    measurement did. A surface that has not been captured reports no size at
    all rather than a plausible one.
    """

    for surface in SURFACES:
        if surface.status is SurfaceStatus.CAPTURED:
            assert surface.enumerated is not None, surface.key
            assert surface.enumerated > 0, surface.key
        else:
            assert surface.enumerated is None, (
                f"{surface.key} is {surface.status.value} but reports a size"
            )


def test_a_captured_surface_points_at_a_denominator_that_exists() -> None:
    """Captured means the file is in the repository, not that someone looked."""

    captured = surfaces_by_status(SurfaceStatus.CAPTURED)
    assert captured, "expected at least one captured surface"
    for surface in captured:
        referenced = [
            token.strip(" ,.`")
            for token in surface.source.split()
            if token.startswith("game_sources/")
        ]
        assert referenced, f"{surface.key} claims capture without naming a file"
        for relative in referenced:
            assert (ROOT / relative).is_file(), f"{surface.key}: {relative} is missing"


def test_unexamined_surfaces_are_visible_rather_than_absent() -> None:
    """The failure this registry exists for: a surface nobody mentions.

    Kenshi's content database is larger than every captured surface combined and
    went unmentioned for a whole session, because no artefact had a row for it.
    An absent surface reads exactly like a covered one.
    """

    unexamined = surfaces_by_status(SurfaceStatus.UNEXAMINED)
    assert unexamined, (
        "no unexamined surfaces: either every surface is genuinely covered, "
        "which would be a first, or a row was quietly deleted rather than done"
    )
    rendered = "\n".join(render_surface_registry())
    for surface in unexamined:
        assert surface.key in rendered
        assert "UNEXAMINED" in rendered


def test_every_surface_states_where_the_game_declares_it() -> None:
    """A surface with no named source cannot be worked on by anyone."""

    for surface in SURFACES:
        assert surface.source.strip(), surface.key
        assert surface.what_it_enumerates.strip(), surface.key
    keys = [surface.key for surface in SURFACES]
    assert len(keys) == len(set(keys))


def test_the_registry_names_the_surfaces_this_project_already_knows_about() -> None:
    """Pinned so a surface cannot be dropped without editing this test.

    The registry cannot be derived — nothing Kenshi ships enumerates its own
    surfaces — so removing a row must be a deliberate, reviewed act.
    """

    assert {surface.key for surface in SURFACES} == {
        "game_binding",
        "ui_widget",
        "world_context_action",
        "game_content",
        "continuous_control",
    }
