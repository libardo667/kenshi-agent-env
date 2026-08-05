"""The four task channels Kenshi keeps apart stay apart."""

from __future__ import annotations

from kenshi_agent.core.telemetry import CharacterState, CharacterTaskState, TaskEntry


def test_the_four_channels_are_independent_fields() -> None:
    """No channel is derivable from another.

    Measured live: an `operate` context action put OPERATE_MACHINERY in the
    ordinary order queue with jobs empty, while the native command reported
    `selection_mismatch`. Reading "is it mining?" off the Jobs list, or off the
    command result, would have been wrong in both directions.
    """

    state = CharacterTaskState(
        has_player_orders=True,
        orders=[TaskEntry(task_value=87, task_name="OPERATE_MACHINERY", subject_id="node-1")],
        orders_count=1,
        jobs_enabled=True,
        jobs=[],
        jobs_count=0,
        permajobs=[],
        permajobs_count=0,
        current_activity=TaskEntry(task_value=87, task_name="OPERATE_MACHINERY"),
    )

    assert state.orders_count == 1
    # Holding an ordinary order says nothing about Jobs, and Jobs being enabled
    # says nothing about holding any.
    assert state.jobs_count == 0
    assert state.jobs_enabled is True
    assert state.permajobs_count == 0


def test_retained_work_is_visible_without_inferring_it() -> None:
    """The question the controller could not previously ask."""

    idle = CharacterTaskState(jobs_enabled=True)
    working = CharacterTaskState(
        has_player_orders=True,
        orders=[TaskEntry(task_value=87, task_name="OPERATE_MACHINERY")],
        orders_count=1,
    )

    assert not idle.has_retained_work
    assert working.has_retained_work


def test_an_unreachable_task_system_is_absent_not_empty() -> None:
    """A character whose task system cannot be read holds unknown work.

    Reporting that as empty would say Kenshi is holding nothing, which is a
    claim the export cannot make.
    """

    character = CharacterState(id="c1", name="Barth")

    assert character.task_state is None


def test_a_bounded_list_is_not_mistaken_for_a_short_one() -> None:
    state = CharacterTaskState(
        orders=[TaskEntry(task_value=1, task_name="A")],
        orders_count=40,
        orders_complete=False,
    )

    assert state.orders_count == 40
    assert len(state.orders) == 1
    assert state.orders_complete is False
