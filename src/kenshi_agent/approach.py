"""Deterministic progress monitoring for a long approach toward a target.

A movement pulse is one opaque await, but an approach walks for tens of seconds
while the character paths to a target. This monitor turns the world-state stream
into a typed progress verdict: how much distance has closed, whether the target
is still present, whether dialogue has opened with it, and whether a hostile has
entered threat range. It owns no I/O and no lifecycle — it is the deterministic
core the P6 option and typed conditions read, so "are we there yet / is this
still safe" is a fact, not a model judgment.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Disposition, NearbyEntity, Observation


@dataclass(frozen=True, slots=True)
class ApproachStatus:
    target_id: str
    target_present: bool
    start_distance: float | None
    current_distance: float | None
    closed_distance: float | None
    arrived: bool
    dialogue_open_with_target: bool
    hostile_in_threat_range: bool
    reason: str

    @property
    def is_terminal(self) -> bool:
        """The approach cannot usefully continue: arrived, or the target is gone."""

        return self.arrived or not self.target_present

    @property
    def should_abort(self) -> bool:
        """A non-success terminal that safety/policy must react to."""

        return (not self.arrived) and (not self.target_present or self.hostile_in_threat_range)


def _target_entity(observation: Observation, target_id: str) -> NearbyEntity | None:
    telemetry = observation.telemetry
    if telemetry is None:
        return None
    return next(
        (entity for entity in telemetry.nearby_entities if entity.id == target_id),
        None,
    )


def _hostile_in_range(observation: Observation, threat_distance: float) -> bool:
    telemetry = observation.telemetry
    if telemetry is None:
        return False
    return any(
        entity.disposition is Disposition.HOSTILE
        and entity.distance is not None
        and entity.distance <= threat_distance
        for entity in telemetry.nearby_entities
    )


class ApproachMonitor:
    """Tracks progress toward one target across world-state observations.

    Success is dialogue opening with the exact target, or closing inside an
    arrival radius. Abort conditions are the target vanishing or a hostile
    entering threat range. Distance is read from the target's telemetry entry;
    a missing distance is `unknown`, never assumed to be arrival.
    """

    def __init__(
        self,
        *,
        target_id: str,
        arrival_distance: float = 5.0,
        threat_distance: float = 15.0,
    ) -> None:
        if arrival_distance <= 0.0:
            raise ValueError("arrival_distance must be positive")
        if threat_distance <= 0.0:
            raise ValueError("threat_distance must be positive")
        self.target_id = target_id
        self.arrival_distance = arrival_distance
        self.threat_distance = threat_distance
        self.start_distance: float | None = None
        self._begun = False

    def begin(self, observation: Observation) -> ApproachStatus:
        target = _target_entity(observation, self.target_id)
        self.start_distance = target.distance if target is not None else None
        self._begun = True
        return self.assess(observation)

    def assess(self, observation: Observation) -> ApproachStatus:
        if not self._begun:
            raise RuntimeError("ApproachMonitor.begin must be called before assess.")
        telemetry = observation.telemetry
        target = _target_entity(observation, self.target_id)
        present = target is not None
        current = target.distance if target is not None else None
        closed = (
            self.start_distance - current
            if self.start_distance is not None and current is not None
            else None
        )
        dialogue_open_with_target = bool(
            telemetry is not None
            and telemetry.ui.dialogue_open
            and telemetry.ui.dialogue_target_id == self.target_id
        )
        arrived = dialogue_open_with_target or (
            current is not None and current <= self.arrival_distance
        )
        hostile = _hostile_in_range(observation, self.threat_distance)

        if arrived:
            reason = (
                "Dialogue opened with the target."
                if dialogue_open_with_target
                else f"Reached the target within {self.arrival_distance:.1f} units."
            )
        elif not present:
            reason = "The target is no longer among nearby entities."
        elif hostile:
            reason = "A hostile entity entered threat range during the approach."
        elif closed is not None:
            direction = "closer" if closed >= 0 else "farther"
            reason = f"Approaching: {abs(closed):.1f} units {direction} so far."
        else:
            reason = "Approach in progress; distance to target is unavailable."

        return ApproachStatus(
            target_id=self.target_id,
            target_present=present,
            start_distance=self.start_distance,
            current_distance=current,
            closed_distance=closed,
            arrived=arrived,
            dialogue_open_with_target=dialogue_open_with_target,
            hostile_in_threat_range=hostile,
            reason=reason,
        )
