"""Who owns which part of an `Observation`, and what happens when that is lost.

An `Observation` is written by two authorities. The environment observes the
world; the runtime knows what the run is for, what it remembers, and what it has
already tried. They share one model, and every field carries a default, so code
holding only a telemetry snapshot could build a whole observation and silently
assert defaults for the other author's half.

That is not hypothetical. Run 20260806T115133.521810Z refused `operate` on an
iron deposit three times out of three with "Affordance is absent from the current
observation" while the deposit was present, unchanged, in every telemetry
publication. The live input boundary builds its observation from a snapshot, so
the runtime-owned planning mode read as its default, and affordance enumeration
treated that default as authority.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kenshi_agent.core.observation import (
    PLANNER_CONTEXT_FIELDS,
    PUBLICATION_SURVIVING_FIELDS,
    RUNTIME_CONTEXT_FIELDS,
    WORLD_FACT_FIELDS,
    Observation,
)
from kenshi_agent.core.operation import ControlMode
from kenshi_agent.core.telemetry import GameState, TelemetrySnapshot, UIState
from kenshi_agent.core.world import WorldStateRevision


def _retained() -> Observation:
    """What the runtime knows, as the world-state store holds it."""

    return Observation(
        run_id="ownership",
        step_index=7,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=181, capability_epoch=1),
        telemetry=TelemetrySnapshot(sequence=181, game=GameState(loaded=True)),
        objective="Mine iron near The Hub.",
        planner_feedback="The previous response had the wrong shape.",
    )


def _observed() -> Observation:
    """What the environment can answer for, holding only a fresh snapshot."""

    return Observation(
        run_id="ownership",
        step_index=7,
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(telemetry_sequence=206, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=206,
            game=GameState(loaded=True, paused=True),
            ui=UIState(active_screen="world"),
        ),
        telemetry_age_seconds=0.05,
    )


def test_every_field_is_owned_by_exactly_one_author() -> None:
    """The partition is the invariant; the import-time check is its enforcement."""

    declared = set(Observation.model_fields)

    assert WORLD_FACT_FIELDS | RUNTIME_CONTEXT_FIELDS == declared
    assert not (WORLD_FACT_FIELDS & RUNTIME_CONTEXT_FIELDS)


def test_an_unclassified_field_is_refused_rather_than_assumed() -> None:
    """Adding a field without an owner must fail loudly, not default quietly."""

    from kenshi_agent.core import observation as module

    with pytest.raises(RuntimeError, match="no declared owner"):
        original = module.WORLD_FACT_FIELDS
        try:
            module.WORLD_FACT_FIELDS = original - {"telemetry"}
            module._assert_every_field_has_an_owner()
        finally:
            module.WORLD_FACT_FIELDS = original


def test_the_narrower_carry_forward_sets_name_only_runtime_fields() -> None:
    assert PLANNER_CONTEXT_FIELDS <= RUNTIME_CONTEXT_FIELDS
    assert PUBLICATION_SURVIVING_FIELDS <= RUNTIME_CONTEXT_FIELDS


def test_fresh_world_facts_arrive_without_erasing_what_the_run_knows() -> None:
    composed = _retained().with_world_facts(_observed())

    # Every world fact is the fresh one.
    assert composed.world_revision.telemetry_sequence == 206
    assert composed.telemetry is not None
    assert composed.telemetry.sequence == 206
    assert composed.telemetry.game.paused is True
    assert composed.telemetry_age_seconds == 0.05

    # Every runtime-owned fact is the retained one, not the observer's default.
    assert composed.objective == "Mine iron near The Hub."
    assert composed.planner_feedback == "The previous response had the wrong shape."


def test_composition_carries_every_world_fact_not_a_remembered_subset() -> None:
    """A field-by-field copy rots; this asserts the whole set moves."""

    retained, observed = _retained(), _observed()
    composed = retained.with_world_facts(observed)

    for name in WORLD_FACT_FIELDS:
        assert getattr(composed, name) == getattr(observed, name), name
    for name in RUNTIME_CONTEXT_FIELDS:
        assert getattr(composed, name) == getattr(retained, name), name


def test_the_input_boundary_evaluates_authority_on_composed_state(
    tmp_path: Path,
) -> None:
    """The kernel must not hand the boundary a snapshot-only observation.

    This is the exact seam that broke: `input_boundary_observation()` answers
    for the world alone, and using it directly meant every runtime-owned field
    the boundary consulted was a default.
    """

    from kenshi_agent.execution.kernel import ExecutionKernel

    del tmp_path

    class _Store:
        latest = _retained()

    class _Kernel:
        state_store = _Store()

        def input_boundary_observation(self) -> Observation:
            return _observed()

        _latest_input_authority = ExecutionKernel._latest_input_authority

    authority = _Kernel()._latest_input_authority()

    assert authority is not None
    assert authority.telemetry is not None
    assert authority.telemetry.sequence == 206, "the boundary must see fresh telemetry"
    assert authority.objective == "Mine iron near The Hub.", (
        "the boundary must not evaluate authority against a defaulted objective"
    )
