"""Deterministic approach-progress monitoring for P6."""

from __future__ import annotations

import pytest

from kenshi_agent.approach import ApproachMonitor
from kenshi_agent.models import (
    Disposition,
    NearbyEntity,
    Observation,
    TelemetrySnapshot,
    UIState,
)

TARGET_ID = "entity-barman"


def scene(
    *,
    target_distance: float | None = 40.0,
    target_present: bool = True,
    dialogue_target: str | None = None,
    hostile_distance: float | None = None,
) -> Observation:
    entities: list[NearbyEntity] = []
    if target_present:
        entities.append(
            NearbyEntity(
                id=TARGET_ID,
                name="Barman",
                is_animal=False,
                has_vendor_list=True,
                is_squad_leader=True,
                has_dialogue=True,
                disposition=Disposition.NEUTRAL,
                distance=target_distance,
            )
        )
    if hostile_distance is not None:
        entities.append(
            NearbyEntity(
                id="entity-bandit",
                name="Bandit",
                disposition=Disposition.HOSTILE,
                distance=hostile_distance,
            )
        )
    return Observation(
        run_id="approach-test",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            nearby_entities=entities,
            ui=UIState(
                dialogue_open=dialogue_target is not None,
                dialogue_target_id=dialogue_target,
            ),
        ),
    )


def monitor() -> ApproachMonitor:
    return ApproachMonitor(target_id=TARGET_ID, arrival_distance=5.0, threat_distance=15.0)


def test_progress_closes_distance_without_arriving() -> None:
    m = monitor()
    start = m.begin(scene(target_distance=40.0))
    assert start.start_distance == 40.0
    assert start.arrived is False

    mid = m.assess(scene(target_distance=22.0))
    assert mid.current_distance == 22.0
    assert mid.closed_distance == 18.0
    assert mid.arrived is False
    assert "closer" in mid.reason
    assert mid.is_terminal is False


def test_arrival_by_distance() -> None:
    m = monitor()
    m.begin(scene(target_distance=40.0))
    arrived = m.assess(scene(target_distance=4.0))
    assert arrived.arrived is True
    assert arrived.is_terminal is True
    assert arrived.should_abort is False


def test_arrival_by_dialogue_regardless_of_distance() -> None:
    # The native approach ends in dialogue; that is success even if the reported
    # distance is still large (the character is at the counter, telemetry lags).
    m = monitor()
    m.begin(scene(target_distance=40.0))
    arrived = m.assess(scene(target_distance=30.0, dialogue_target=TARGET_ID))
    assert arrived.dialogue_open_with_target is True
    assert arrived.arrived is True
    assert "Dialogue opened" in arrived.reason


def test_dialogue_with_a_different_target_is_not_arrival() -> None:
    m = monitor()
    m.begin(scene(target_distance=40.0))
    status = m.assess(scene(target_distance=30.0, dialogue_target="entity-someone-else"))
    assert status.dialogue_open_with_target is False
    assert status.arrived is False


def test_target_loss_is_a_terminal_abort() -> None:
    m = monitor()
    m.begin(scene(target_distance=40.0))
    lost = m.assess(scene(target_present=False))
    assert lost.target_present is False
    assert lost.arrived is False
    assert lost.is_terminal is True
    assert lost.should_abort is True
    assert "no longer" in lost.reason


def test_hostile_in_range_triggers_abort() -> None:
    m = monitor()
    m.begin(scene(target_distance=40.0))
    threatened = m.assess(scene(target_distance=30.0, hostile_distance=10.0))
    assert threatened.hostile_in_threat_range is True
    assert threatened.should_abort is True
    assert "hostile" in threatened.reason


def test_hostile_out_of_range_does_not_abort() -> None:
    m = monitor()
    m.begin(scene(target_distance=40.0))
    ok = m.assess(scene(target_distance=30.0, hostile_distance=200.0))
    assert ok.hostile_in_threat_range is False
    assert ok.should_abort is False


def test_missing_distance_is_unknown_not_arrival() -> None:
    m = monitor()
    m.begin(scene(target_distance=None))
    status = m.assess(scene(target_distance=None))
    assert status.current_distance is None
    assert status.closed_distance is None
    assert status.arrived is False
    assert "unavailable" in status.reason


def test_assess_before_begin_raises() -> None:
    m = monitor()
    with pytest.raises(RuntimeError, match="begin"):
        m.assess(scene())


def test_invalid_thresholds_rejected() -> None:
    with pytest.raises(ValueError):
        ApproachMonitor(target_id=TARGET_ID, arrival_distance=0.0)
    with pytest.raises(ValueError):
        ApproachMonitor(target_id=TARGET_ID, threat_distance=-1.0)
