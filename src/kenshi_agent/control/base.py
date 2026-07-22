from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ..models import ActionReceipt, ClickAction, HotkeyAction, KeyAction, MoveCursorAction

PrimitiveInputAction = KeyAction | HotkeyAction | MoveCursorAction | ClickAction


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
    async def input_lease(self) -> AsyncIterator[None]:
        yield

    def user_input_detected(self) -> bool:
        return False

    def input_lease_wait_seconds(self) -> float:
        return 0.0

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
