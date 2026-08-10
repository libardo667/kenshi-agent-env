"""The world-target ceiling is derived from source, not asserted."""

from __future__ import annotations

from kenshi_agent.tooling.coverage_frontier import (
    CONTEXT_MENU_PROBE,
    NATIVE_TARGET_SOURCES,
    assess_coverage_frontier,
    render_coverage_frontier,
)


def test_hardcoded_surface_is_parsed_from_the_native_source() -> None:
    """The report reads the literals rather than restating them.

    If the plug-in stops hardcoding, this stops finding them, which is the
    point: the ceiling must disappear from the report by being fixed, not by
    someone remembering to edit a list.
    """

    frontier = assess_coverage_frontier()

    assert frontier.surface_is_hardcoded
    surfaces = {
        surface.target_kind: surface.context_actions
        for surface in frontier.hardcoded_surfaces
    }
    assert surfaces == {
        "natural_resource": ("operate",),
        "squad_character": ("first_aid",),
    }


def test_the_emittable_surface_bounds_coverage_not_the_witness_set() -> None:
    """Three wired pairs is the exporter's ceiling, not a young witness set."""

    frontier = assess_coverage_frontier()

    assert frontier.vocabulary_size == 291
    assert len(frontier.emittable_semantics) == 2
    assert len(frontier.emittable_target_kinds) == 2
    assert frontier.unreachable_vocabulary == 289

    # Every witnessed target kind is one the exporter can emit. Nothing was
    # ever witnessed outside the hardcoded surface, because nothing could be.
    assert set(frontier.witnessed_target_kinds) <= set(frontier.emittable_target_kinds)


def test_the_lifting_mechanism_is_present_in_the_plugin() -> None:
    """The exact context-menu reader, not an inferred predicate, is active."""

    frontier = assess_coverage_frontier()

    assert frontier.context_menu_probe_call_sites >= 1
    combined = "".join(
        path.read_text(encoding="utf-8", errors="replace") for path in NATIVE_TARGET_SOURCES
    )
    assert CONTEXT_MENU_PROBE in combined


def test_render_names_the_ceiling_and_the_mechanism() -> None:
    body = "\n".join(render_coverage_frontier(assess_coverage_frontier()))

    assert "HARDCODED WORLD-TARGET SURFACE" in body
    assert "DISCOVERY MECHANISM ACTIVE" in body
    assert "natural_resource" in body
    assert "first_aid" in body


def test_task_vocabulary_reaches_cpp_from_the_captured_enum() -> None:
    """The probe's task list is source-derived, not a curated guess.

    If the plug-in iterated a hand-picked subset, the ceiling would move rather
    than lift. This header is generated from the same `TaskType.h` capture the
    parity report parses, so the runtime probe and the Python reconciliation are
    bounded by one vocabulary with one provenance.
    """

    from kenshi_agent.tooling.context_action_vocabulary import load_task_types

    header = (
        NATIVE_TARGET_SOURCES[0].parent / "TaskTypeVocabulary.generated.h"
    ).read_text(encoding="utf-8")
    entries = load_task_types().entries

    assert len(entries) == 291
    for entry in entries:
        assert f'{{ {entry.value}, "{entry.name}" }}' in header


def test_generated_vocabulary_header_is_not_stale() -> None:
    import tempfile
    from pathlib import Path

    from kenshi_agent.tooling.native_contract_export import (
        export_task_type_vocabulary_header,
    )

    committed = (
        NATIVE_TARGET_SOURCES[0].parent / "TaskTypeVocabulary.generated.h"
    ).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as directory:
        regenerated = export_task_type_vocabulary_header(Path(directory))
        assert regenerated.read_text(encoding="utf-8") == committed
