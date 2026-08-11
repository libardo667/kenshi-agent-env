from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class SessionLogger:
    """Append-only JSONL logger with immediate flush for crash-tolerant runs."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        needs_separator = self._repair_incomplete_tail()
        self._next_event_sequence = self._next_sequence_from_existing_log()
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)
        self._separate_unterminated_tail(needs_separator)

    def write(self, event_type: str, *, step_index: int | None = None, payload: Any = None) -> None:
        jsonable_payload = self._jsonable(payload)
        with self._lock:
            record = {
                "event_type": event_type,
                "event_sequence": self._next_event_sequence,
                "run_id": self.run_id,
                "step_index": step_index,
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": jsonable_payload,
            }
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            # Retire the sequence before I/O because a write or flush failure is
            # ambiguous about whether the complete line reached the file.
            self._next_event_sequence += 1
            self._handle.write(line + "\n")
            self._handle.flush()

    def _repair_incomplete_tail(self) -> bool:
        """Remove only an invalid unterminated tail; preserve complete JSON."""
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return False

        with self.path.open("rb") as handle:
            handle.seek(-1, 2)
            if handle.read(1) == b"\n":
                return False
            tail_end = handle.tell()
            tail_start = tail_end - 1
            while tail_start > 0:
                handle.seek(tail_start - 1)
                if handle.read(1) == b"\n":
                    break
                tail_start -= 1
            handle.seek(tail_start)
            tail = handle.read()

        try:
            record = json.loads(tail.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            record = None
        if isinstance(record, dict):
            return True

        with self.path.open("r+b") as handle:
            handle.truncate(tail_start)
            handle.flush()
        return False

    def _next_sequence_from_existing_log(self) -> int:
        """Continue one run's order when the append-only logger is reopened."""
        if not self.path.is_file():
            return 1

        matching_records = 0
        greatest_sequence = 0
        with self.path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("run_id") != self.run_id:
                    continue
                matching_records += 1
                sequence = record.get("event_sequence")
                if (
                    isinstance(sequence, int)
                    and not isinstance(sequence, bool)
                    and sequence > greatest_sequence
                ):
                    greatest_sequence = sequence
        return max(matching_records, greatest_sequence) + 1

    def _separate_unterminated_tail(self, needs_separator: bool) -> None:
        """Terminate a complete final record before appending another one."""
        if needs_separator:
            self._handle.write("\n")
            self._handle.flush()

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): SessionLogger._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [SessionLogger._jsonable(v) for v in value]
        return value

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()

    def __enter__(self) -> SessionLogger:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
