from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ..models import (
    ActionReceipt,
    ClickAction,
    HotkeyAction,
    KeyAction,
    MoveCursorAction,
    ScrollAction,
)

PrimitiveInputAction = KeyAction | HotkeyAction | MoveCursorAction | ClickAction | ScrollAction


@dataclass(frozen=True, slots=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class InputController(ABC):
    @asynccontextmanager
    async def input_lease(self, *, alt_tab_on_restore: bool = False) -> AsyncIterator[None]:
        del alt_tab_on_restore
        yield

    def user_input_detected(self) -> bool:
        return False

    def continuous_user_input_detected(self) -> bool:
        """Report new human input even while no short-lived input lease is active."""

        return False

    def continuous_user_input_diagnostic(self) -> str | None:
        """Describe the last continuous-input classification when available."""

        return None

    def input_lease_wait_seconds(self) -> float:
        return 0.0

    def target_window_title(self) -> str | None:
        """Return the current target-window title when the backend can read it."""

        return None

    @abstractmethod
    def focus_window(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        raise NotImplementedError

    async def execute_safety(self, action: PrimitiveInputAction) -> ActionReceipt:
        return await self.execute(action)

    @abstractmethod
    def emergency_stop_pressed(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def client_rect(self) -> WindowRect:
        raise NotImplementedError
