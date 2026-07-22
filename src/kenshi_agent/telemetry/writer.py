from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..models import TelemetrySnapshot


def write_snapshot_atomic(path: Path, snapshot: TelemetrySnapshot) -> None:
    """Write a complete snapshot and atomically replace the public file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.model_dump_json(indent=2).encode("utf-8")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
