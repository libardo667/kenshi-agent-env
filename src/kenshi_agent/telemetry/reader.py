from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..core.telemetry import TelemetrySnapshot


class TelemetryReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TelemetryRead:
    snapshot: TelemetrySnapshot
    age_seconds: float
    stale: bool
    path: Path


class TelemetryReader:
    def __init__(
        self,
        path: Path,
        *,
        max_age_seconds: float = 3.0,
        retries: int = 3,
        retry_delay_seconds: float = 0.03,
        require_protocol_major: int = 1,
    ) -> None:
        self.path = path
        self.max_age_seconds = max_age_seconds
        self.retries = retries
        self.retry_delay_seconds = retry_delay_seconds
        self.require_protocol_major = require_protocol_major

    def read(self) -> TelemetryRead:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                raw = self.path.read_bytes()
                envelope = json.loads(raw)
                if not isinstance(envelope, dict):
                    raise ValueError("Telemetry payload must be a JSON object.")
                protocol_version = envelope.get("protocol_version")
                if not isinstance(protocol_version, str):
                    raise ValueError(
                        "Telemetry payload has no string protocol_version."
                    )
                # Major-version rejection belongs before strict payload
                # validation. During a deliberate wire migration, removed
                # fields from the previous producer must report the actionable
                # version mismatch instead of masquerading as schema damage.
                self._check_protocol(protocol_version)
                snapshot = TelemetrySnapshot.model_validate_json(raw)
                captured = snapshot.captured_at
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=UTC)
                age_seconds = max(0.0, (datetime.now(UTC) - captured).total_seconds())
                return TelemetryRead(
                    snapshot=snapshot,
                    age_seconds=age_seconds,
                    stale=age_seconds > self.max_age_seconds,
                    path=self.path,
                )
            except (OSError, ValidationError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.retry_delay_seconds)
        raise TelemetryReadError(f"Could not read valid telemetry from {self.path}: {last_error}")

    def _check_protocol(self, version: str) -> None:
        try:
            major = int(version.split(".", maxsplit=1)[0])
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Invalid telemetry protocol version: {version!r}") from exc
        if major != self.require_protocol_major:
            raise ValueError(
                f"Telemetry protocol major {major} does not match required "
                f"major {self.require_protocol_major}."
            )
