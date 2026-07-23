from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .models import NativeCommandRequest


def write_native_command_request_atomic(
    path: Path,
    request: NativeCommandRequest,
) -> None:
    """Atomically publish one complete, strictly validated native request."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = request.model_dump_json(indent=2).encode("utf-8")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
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
