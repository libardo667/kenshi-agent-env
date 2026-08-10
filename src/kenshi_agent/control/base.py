from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ..core.operation import (
    ClickAction,
    HotkeyAction,
    KeyAction,
    MouseButtonAction,
    MouseDragAction,
    MoveCursorAction,
    ScrollAction,
)
from ..core.transport import (
    ActionReceipt,
    CalibrationIdentity,
)

PrimitiveInputAction = (
    KeyAction
    | HotkeyAction
    | MouseButtonAction
    | MouseDragAction
    | MoveCursorAction
    | ClickAction
    | ScrollAction
)


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

    @asynccontextmanager
    async def safety_input_lease(
        self,
        *,
        alt_tab_on_restore: bool = False,
    ) -> AsyncIterator[None]:
        """Acquire the input boundary without delaying deterministic cleanup."""

        async with self.input_lease(alt_tab_on_restore=alt_tab_on_restore):
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

    def visible_window_titles(self) -> list[str]:
        """Return visible top-level titles when the backend can enumerate them."""

        title = self.target_window_title()
        return [title] if title else []

    def request_dialog_command(self, *, button_text: str, control_id: int) -> None:
        """Invoke one exact native dialog command without synthesizing input."""

        del button_text, control_id
        raise RuntimeError("This controller cannot route native dialog commands.")

    def observed_calibration_identity(self) -> CalibrationIdentity:
        """Report only calibration facts this backend can actually read.

        Anything the backend cannot observe stays `None`. Callers treat a null
        as unknown and refuse calibrated pointer input; they must never read it
        as agreement with the expected profile.
        """

        try:
            rect = self.client_rect()
        except (OSError, RuntimeError, ValueError):
            return CalibrationIdentity()
        return CalibrationIdentity(
            client_width=rect.width or None,
            client_height=rect.height or None,
        )

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
