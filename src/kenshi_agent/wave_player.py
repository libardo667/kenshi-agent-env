from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any


def play_windows_wave(path: Path) -> None:
    """Play one wave synchronously in the current Windows process."""

    size = path.stat().st_size
    if size < 45:
        raise ValueError(
            f"Refusing to play a wave without audio data: {path} is {size} bytes."
        )
    winsound: Any = import_module("winsound")
    winsound.PlaySound(str(path), winsound.SND_FILENAME)
