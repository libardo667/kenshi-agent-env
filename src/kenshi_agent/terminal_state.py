from __future__ import annotations

from collections.abc import Iterable

from .control.base import InputController

TERMINAL_WINDOW_EVENT_PREFIX = "terminal_window_detected: "

_TERMINAL_WINDOW_MARKERS = (
    "crash reporter",
    "has crashed",
    "steam dll error",
    "steam - error",
)


def terminal_window_title(controller: InputController) -> str | None:
    """Return the exact visible title that proves Kenshi cannot continue."""

    for title in controller.visible_window_titles():
        normalized = title.strip().casefold()
        if normalized == "bad stuff" or any(
            marker in normalized for marker in _TERMINAL_WINDOW_MARKERS
        ):
            return title
    return None


def terminal_window_event(title: str) -> str:
    return TERMINAL_WINDOW_EVENT_PREFIX + title


def terminal_window_from_events(events: Iterable[str]) -> str | None:
    for event in events:
        if event.startswith(TERMINAL_WINDOW_EVENT_PREFIX):
            return event.removeprefix(TERMINAL_WINDOW_EVENT_PREFIX)
    return None
