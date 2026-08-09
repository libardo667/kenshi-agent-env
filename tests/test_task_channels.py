"""Kenshi's retained-work channels and current activity stay independent."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.core.lifecycle import OrderDisposition
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.telemetry import (
    CharacterState,
    CharacterWorkState,
    GameState,
    TaskCollection,
    TaskCollectionCompleteness,
    TaskEntry,
    TelemetrySnapshot,
)
from kenshi_agent.execution.monitoring import _order_disposition_now

FIXTURES = Path(__file__).parent / "fixtures" / "native_telemetry"


def task(
    name: str,
    value: int,
    *,
    subject_id: str | None = None,
    position: int | None = None,
) -> TaskEntry:
    return TaskEntry(
        task_value=value,
        task_name=name,
        subject_id=subject_id,
        description=None,
        position=position,
    )


def complete(*items: TaskEntry) -> TaskCollection:
    return TaskCollection(
        items=list(items),
        completeness=TaskCollectionCompleteness.COMPLETE,
        known_total=len(items),
    )


def work(**overrides: object) -> CharacterWorkState:
    values: dict[str, object] = {
        "has_player_orders": False,
        "ordinary_orders": complete(),
        "jobs_enabled": False,
        "jobs": complete(),
        "permanent_jobs": complete(),
        "current_activity": None,
    }
    values.update(overrides)
    return CharacterWorkState(**values)


def test_retained_order_activity_and_empty_jobs_are_independent() -> None:
    """The exact done-condition shape is representable without a fake Job."""

    state = work(
        has_player_orders=True,
        ordinary_orders=complete(
            task("OPERATE_MACHINERY", 87, subject_id="node-1", position=0)
        ),
        jobs_enabled=True,
        current_activity=task("OPERATE_MACHINERY", 87),
    )

    assert state.ordinary_orders.known_total == 1
    assert state.jobs.known_total == 0
    assert state.jobs_enabled is True
    assert state.permanent_jobs.known_total == 0
    assert state.current_activity is not None


def test_current_activity_alone_is_not_retained_work() -> None:
    active = work(current_activity=task("OPERATE_MACHINERY", 87))

    assert not active.has_retained_work


def test_monitor_does_not_adopt_activity_or_jobs_as_its_ordinary_order() -> None:
    observed = Observation(
        run_id="task-channels",
        step_index=1,
        mode="live",
        telemetry=TelemetrySnapshot(
            sequence=44,
            game=GameState(loaded=True, paused=False),
            roster=[
                CharacterState(
                    id="c1",
                    name="Barth",
                    work=work(
                        jobs=complete(task("OPERATE_MACHINERY", 87, position=0)),
                        current_activity=task("OPERATE_MACHINERY", 87),
                    ),
                )
            ],
        ),
    )

    disposition, sequence = _order_disposition_now(observed, issued=True)

    assert disposition is OrderDisposition.UNKNOWN_WITHOUT_CAUSAL_LINK
    assert sequence == 44


def test_monitor_reports_matching_ordinary_work_as_unattributed() -> None:
    observed = Observation(
        run_id="task-channels",
        step_index=1,
        mode="live",
        telemetry=TelemetrySnapshot(
            sequence=45,
            game=GameState(loaded=True, paused=False),
            roster=[
                CharacterState(
                    id="c1",
                    name="Barth",
                    work=work(
                        has_player_orders=True,
                        ordinary_orders=complete(
                            task("OPERATE_MACHINERY", 87, position=0)
                        ),
                    ),
                )
            ],
        ),
    )

    disposition, sequence = _order_disposition_now(observed, issued=True)

    assert disposition is OrderDisposition.OBSERVED_UNATTRIBUTED_WORK
    assert sequence == 45


def test_monitor_preserves_direct_has_player_orders_evidence_without_a_sample() -> None:
    observed = Observation(
        run_id="task-channels",
        step_index=1,
        mode="live",
        telemetry=TelemetrySnapshot(
            sequence=46,
            game=GameState(loaded=True, paused=False),
            roster=[
                CharacterState(
                    id="c1",
                    name="Barth",
                    work=work(
                        has_player_orders=True,
                        ordinary_orders=TaskCollection(
                            items=[],
                            completeness=TaskCollectionCompleteness.TRUNCATED,
                            known_total=None,
                        ),
                    ),
                )
            ],
        ),
    )

    disposition, sequence = _order_disposition_now(observed, issued=True)

    assert disposition is OrderDisposition.OBSERVED_UNATTRIBUTED_WORK
    assert sequence == 46


def test_an_unreachable_task_system_is_absent_not_empty() -> None:
    character = CharacterState(id="c1", name="Barth")

    assert character.work is None


def test_unknown_order_total_target_and_tail_position_stay_null() -> None:
    state = work(
        has_player_orders=True,
        ordinary_orders=TaskCollection(
            items=[
                task("A", 1, position=0),
                task("B", 2, position=1),
                task("C", 3, position=None),
            ],
            completeness=TaskCollectionCompleteness.TRUNCATED,
            known_total=None,
        ),
    )

    assert state.ordinary_orders.known_total is None
    assert state.ordinary_orders.items[-1].position is None
    assert state.ordinary_orders.items[-1].subject_id is None


def test_task_collection_rejects_a_sample_count_disguised_as_total() -> None:
    with pytest.raises(ValidationError, match="must exceed"):
        TaskCollection(
            items=[task("A", 1)],
            completeness=TaskCollectionCompleteness.TRUNCATED,
            known_total=1,
        )


def test_superseded_task_state_shape_has_no_fallback_reader() -> None:
    with pytest.raises(ValidationError):
        TelemetrySnapshot.model_validate_json(
            (FIXTURES / "invalid_superseded_task_state.json").read_bytes()
        )
