from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from kenshi_agent.core.transport import SessionEvent
from kenshi_agent.session_log import SessionLogger


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_concurrent_writes_have_ordered_sequences_without_loss(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    workers = 8
    writes_per_worker = 128
    barrier = threading.Barrier(workers)

    with SessionLogger(path, "concurrent-run") as logger:

        def write_batch(writer: int) -> None:
            barrier.wait(timeout=5)
            for ordinal in range(writes_per_worker):
                logger.write(
                    "concurrent_event",
                    step_index=7,
                    payload={
                        "writer": writer,
                        "ordinal": ordinal,
                        "world_revision": {"telemetry_sequence": 314_159},
                    },
                )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(write_batch, writer) for writer in range(workers)]
            for future in futures:
                future.result(timeout=15)

    records = _records(path)
    expected_count = workers * writes_per_worker
    assert len(records) == expected_count
    assert {(record["payload"]["writer"], record["payload"]["ordinal"]) for record in records} == {
        (writer, ordinal)
        for writer in range(workers)
        for ordinal in range(writes_per_worker)
    }
    sequences = [record["event_sequence"] for record in records]
    assert all(type(sequence) is int for sequence in sequences)
    assert sequences == list(range(1, expected_count + 1))
    assert {record["step_index"] for record in records} == {7}
    assert {
        record["payload"]["world_revision"]["telemetry_sequence"] for record in records
    } == {314_159}


def test_event_sequence_is_run_local_and_independent_of_nullable_step(tmp_path: Path) -> None:
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"

    with SessionLogger(first_path, "first-run") as logger:
        logger.write("run_event", step_index=None)
        assert _records(first_path)[0]["event_sequence"] == 1
        logger.write("same_world_event", step_index=7)
        logger.write(
            "same_world_event",
            step_index=7,
            payload={"world_revision": {"telemetry_sequence": 1}},
        )

    with SessionLogger(second_path, "second-run") as logger:
        logger.write("run_event", step_index=None)

    assert [record["event_sequence"] for record in _records(first_path)] == [1, 2, 3]
    assert [record["step_index"] for record in _records(first_path)] == [None, 7, 7]
    assert _records(second_path)[0]["event_sequence"] == 1


def test_reopen_continues_after_legacy_records_and_repairs_a_partial_tail(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    legacy = {
        "event_type": "legacy_event",
        "run_id": "continued-run",
        "step_index": None,
        "timestamp": "2026-08-11T00:00:00+00:00",
        "payload": {},
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    with SessionLogger(path, "continued-run") as logger:
        logger.write("sequenced_event", step_index=4, payload={})
    with SessionLogger(path, "continued-run") as logger:
        logger.write("reopened_event", step_index=4, payload={})

    records = _records(path)
    assert "event_sequence" not in records[0]
    assert [record["event_sequence"] for record in records[1:]] == [2, 3]
    assert SessionEvent.model_validate(records[0]).event_sequence is None
    assert SessionEvent.model_validate(records[1]).event_sequence == 2

    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event_type":"interrupted"')
    with SessionLogger(path, "continued-run") as logger:
        logger.write("post_restart_event", step_index=4)

    records = _records(path)
    assert len(records) == 4
    assert records[-1]["event_sequence"] == 4


def test_reopen_preserves_a_complete_unterminated_record(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    legacy = {
        "event_type": "legacy_event",
        "run_id": "continued-run",
        "step_index": None,
        "timestamp": "2026-08-11T00:00:00+00:00",
        "payload": {},
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    with SessionLogger(path, "continued-run") as logger:
        logger.write("sequenced_event", payload={})

    records = _records(path)
    assert records[0] == legacy
    assert records[1]["event_sequence"] == 2


def test_an_ambiguous_flush_failure_never_reuses_a_sequence(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    logger = SessionLogger(path, "flush-failure-run")
    wrapped = logger._handle

    class FailFirstFlush:
        def __init__(self) -> None:
            self.failed = False

        def write(self, value: str) -> int:
            return wrapped.write(value)

        def flush(self) -> None:
            wrapped.flush()
            if not self.failed:
                self.failed = True
                raise OSError("ambiguous flush failure")

        @property
        def closed(self) -> bool:
            return wrapped.closed

        def close(self) -> None:
            wrapped.close()

    logger._handle = FailFirstFlush()  # type: ignore[assignment]
    try:
        try:
            logger.write("uncertain_event", payload={})
        except OSError as exc:
            assert str(exc) == "ambiguous flush failure"
        else:  # pragma: no cover - the test double must fail once
            raise AssertionError("expected the injected flush failure")
        logger.write("later_event", payload={})
    finally:
        logger.close()

    assert [record["event_sequence"] for record in _records(path)] == [1, 2]


def test_committed_sequence_and_legacy_fixtures_preserve_both_contracts() -> None:
    fixture_root = Path(__file__).parent / "fixtures"
    sequence_records = _records(fixture_root / "session_logs" / "event_sequence.jsonl")
    legacy_records = _records(
        fixture_root / "run_bundles" / "live_reporting_surface" / "events.jsonl"
    )

    assert [record["event_sequence"] for record in sequence_records] == [1, 2, 3, 4]
    assert [record["step_index"] for record in sequence_records] == [None, 7, 7, 7]
    assert {
        record["payload"]["world_revision"]["telemetry_sequence"]
        for record in sequence_records[1:]
    } == {42}
    assert [
        SessionEvent.model_validate(record).event_sequence for record in sequence_records
    ] == [1, 2, 3, 4]
    assert all("event_sequence" not in record for record in legacy_records)
    assert SessionEvent.model_validate(legacy_records[0]).event_sequence is None
