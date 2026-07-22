from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kenshi_agent.models import TelemetrySnapshot
from kenshi_agent.telemetry import TelemetryReadError, TelemetryReader, write_snapshot_atomic


def test_atomic_writer_and_reader(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    snapshot = TelemetrySnapshot(sequence=4, captured_at=datetime.now(UTC), source="test")
    write_snapshot_atomic(path, snapshot)
    result = TelemetryReader(path, max_age_seconds=5, retries=1).read()
    assert result.snapshot.sequence == 4
    assert not result.stale


def test_stale_snapshot_is_marked(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    snapshot = TelemetrySnapshot(captured_at=datetime.now(UTC) - timedelta(seconds=30))
    write_snapshot_atomic(path, snapshot)
    result = TelemetryReader(path, max_age_seconds=1, retries=1).read()
    assert result.stale
    assert result.age_seconds >= 29


def test_invalid_protocol_raises(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    write_snapshot_atomic(path, TelemetrySnapshot(protocol_version="1.0.0"))
    with pytest.raises(TelemetryReadError):
        TelemetryReader(path, require_protocol_major=0, retries=1).read()
