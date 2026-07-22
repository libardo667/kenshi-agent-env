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
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def write(self, event_type: str, *, step_index: int | None = None, payload: Any = None) -> None:
        record = {
            "event_type": event_type,
            "run_id": self.run_id,
            "step_index": step_index,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": self._jsonable(payload),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._handle.write(line + "\n")
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
