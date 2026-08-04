"""World domain types."""

from __future__ import annotations

from time import monotonic

from pydantic import (
    Field,
)

from .base import StrictModel


class WorldStateRevision(StrictModel):
    telemetry_sequence: int | None = Field(default=None, ge=0)
    frame_sequence: int | None = Field(default=None, ge=0)
    capability_epoch: int = Field(default=0, ge=0)
    observed_at_monotonic: float = Field(default_factory=monotonic, ge=0.0)

    def same_snapshot_as(self, other: WorldStateRevision) -> bool:
        return (
            self.telemetry_sequence == other.telemetry_sequence
            and self.frame_sequence == other.frame_sequence
            and self.capability_epoch == other.capability_epoch
        )

    def same_telemetry_snapshot_as(self, other: WorldStateRevision) -> bool:
        """Compare the exact native-control basis without requiring a capture."""

        return bool(
            self.telemetry_sequence is not None
            and self.telemetry_sequence == other.telemetry_sequence
            and self.capability_epoch == other.capability_epoch
        )

    def is_later_than(self, other: WorldStateRevision) -> bool:
        telemetry_regressed = (
            self.telemetry_sequence is not None
            and other.telemetry_sequence is not None
            and self.telemetry_sequence < other.telemetry_sequence
        )
        frame_regressed = (
            self.frame_sequence is not None
            and other.frame_sequence is not None
            and self.frame_sequence < other.frame_sequence
        )
        capability_regressed = self.capability_epoch < other.capability_epoch
        telemetry_advanced = (
            self.telemetry_sequence is not None
            and other.telemetry_sequence is not None
            and self.telemetry_sequence > other.telemetry_sequence
        )
        frame_advanced = (
            self.frame_sequence is not None
            and other.frame_sequence is not None
            and self.frame_sequence > other.frame_sequence
        )
        capability_advanced = self.capability_epoch > other.capability_epoch
        return bool(
            not telemetry_regressed
            and not frame_regressed
            and not capability_regressed
            and (telemetry_advanced or frame_advanced or capability_advanced)
            and self.observed_at_monotonic >= other.observed_at_monotonic
        )
