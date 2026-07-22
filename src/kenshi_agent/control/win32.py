from __future__ import annotations

import asyncio
import ctypes
import os
from ctypes import wintypes
from datetime import UTC, datetime
from typing import Any

from ..models import (
    ActionReceipt,
    ClickAction,
    CoordinateSpace,
    HotkeyAction,
    KeyAction,
    MouseButton,
    MoveCursorAction,
)
from .base import InputController, PrimitiveInputAction, WindowRect


class WindowNotFoundError(RuntimeError):
    pass


class AmbiguousWindowError(RuntimeError):
    pass


def enable_per_monitor_dpi_awareness(user32: Any) -> bool:
    """Use physical pixels consistently for capture geometry and SendInput."""
    setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if setter is None:
        legacy_setter = getattr(user32, "SetProcessDPIAware", None)
        return bool(legacy_setter and legacy_setter())
    return bool(setter(ctypes.c_void_p(-4)))


def select_unique_window(matches: list[tuple[int, str]], title_filter: str) -> int:
    if not matches:
        raise WindowNotFoundError(f"No visible window title contains {title_filter!r}.")
    if len(matches) > 1:
        titles = ", ".join(repr(title) for _, title in matches)
        raise AmbiguousWindowError(
            f"Multiple visible window titles contain {title_filter!r}: {titles}. "
            "Use a narrower window title filter."
        )
    return matches[0][0]


def resolve_screen_point(
    x: float, y: float, space: CoordinateSpace, rect: WindowRect
) -> tuple[int, int]:
    """Convert screen/client/normalized coordinates without touching Win32."""
    if space == CoordinateSpace.SCREEN:
        return round(x), round(y)
    if space == CoordinateSpace.NORMALIZED:
        return (
            rect.left + round(x * max(0, rect.width - 1)),
            rect.top + round(y * max(0, rect.height - 1)),
        )
    return rect.left + round(x), rect.top + round(y)


def normalize_virtual_desktop_point(
    screen_x: int, screen_y: int, *, left: int, top: int, width: int, height: int
) -> tuple[int, int]:
    """Map desktop pixels to SendInput's 0..65535 virtual-desktop coordinates."""
    if width <= 1 or height <= 1:
        raise ValueError("Virtual desktop dimensions must both exceed one pixel.")
    normalized_x = round((screen_x - left) * 65535 / (width - 1))
    normalized_y = round((screen_y - top) * 65535 / (height - 1))
    return normalized_x, normalized_y


if os.name == "nt":
    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


class Win32InputController(InputController):
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    MOUSEEVENTF_ABSOLUTE = 0x8000
    SW_RESTORE = 9
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    _KEYS = {
        "backspace": 0x08,
        "tab": 0x09,
        "enter": 0x0D,
        "shift": 0x10,
        "ctrl": 0x11,
        "control": 0x11,
        "alt": 0x12,
        "pause": 0x13,
        "capslock": 0x14,
        "escape": 0x1B,
        "esc": 0x1B,
        "space": 0x20,
        "pageup": 0x21,
        "pagedown": 0x22,
        "end": 0x23,
        "home": 0x24,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "insert": 0x2D,
        "delete": 0x2E,
        "f1": 0x70,
        "f2": 0x71,
        "f3": 0x72,
        "f4": 0x73,
        "f5": 0x74,
        "f6": 0x75,
        "f7": 0x76,
        "f8": 0x77,
        "f9": 0x78,
        "f10": 0x79,
        "f11": 0x7A,
        "f12": 0x7B,
    }

    def __init__(
        self,
        window_title_contains: str,
        *,
        focus_before_input: bool = True,
        post_input_delay_seconds: float = 0.08,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("Win32InputController is available only on Windows.")
        self.window_title_contains = window_title_contains.casefold()
        self.focus_before_input = focus_before_input
        self.post_input_delay_seconds = post_input_delay_seconds
        self.user32 = getattr(ctypes, "windll").user32  # noqa: B009 - Windows-only
        enable_per_monitor_dpi_awareness(self.user32)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.GetAsyncKeyState.argtypes = [wintypes.INT]
        self.user32.GetAsyncKeyState.restype = wintypes.SHORT
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL

    def _find_window(self) -> int:
        matches: list[tuple[int, str]] = []
        enum_proc_type = getattr(ctypes, "WINFUNCTYPE")(  # noqa: B009 - Windows-only
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def callback(hwnd: int, _: int) -> bool:
            if not self.user32.IsWindowVisible(hwnd):
                return True
            length = self.user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, buffer, length + 1)
            if self.window_title_contains in buffer.value.casefold():
                matches.append((int(hwnd), buffer.value))
            return True

        callback_ref = enum_proc_type(callback)
        self.user32.EnumWindows(callback_ref, 0)
        return select_unique_window(matches, self.window_title_contains)

    def client_rect(self) -> WindowRect:
        hwnd = self._find_window()
        client = wintypes.RECT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(client)):
            raise getattr(ctypes, "WinError")()  # noqa: B009 - Windows-only
        top_left = wintypes.POINT(client.left, client.top)
        bottom_right = wintypes.POINT(client.right, client.bottom)
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
            raise getattr(ctypes, "WinError")()  # noqa: B009 - Windows-only
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
            raise getattr(ctypes, "WinError")()  # noqa: B009 - Windows-only
        return WindowRect(top_left.x, top_left.y, bottom_right.x, bottom_right.y)

    def _focus(self) -> None:
        hwnd = self._find_window()
        self.user32.ShowWindow(hwnd, self.SW_RESTORE)
        if not self.user32.SetForegroundWindow(hwnd):
            raise RuntimeError(
                "Windows refused foreground focus. Click Kenshi once, then retry."
            )

    @classmethod
    def _vk(cls, key: str) -> int:
        normalized = key.strip().casefold()
        if normalized in cls._KEYS:
            return cls._KEYS[normalized]
        if len(normalized) == 1 and (normalized.isalpha() or normalized.isdigit()):
            return ord(normalized.upper())
        raise ValueError(f"Unsupported key name: {key!r}")

    def emergency_stop_pressed(self, key: str) -> bool:
        vk = self._vk(key)
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)

    def _send(self, inputs: list[Any]) -> None:
        if not inputs:
            return
        array_type = INPUT * len(inputs)
        array = array_type(*inputs)
        sent = self.user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            error = getattr(ctypes, "get_last_error")()  # noqa: B009 - Windows-only
            raise RuntimeError(
                f"SendInput inserted {sent}/{len(inputs)} events (GetLastError={error}). "
                "Check window focus and Windows integrity levels."
            )

    def _keyboard_input(self, vk: int, *, key_up: bool) -> Any:
        flags = self.KEYEVENTF_KEYUP if key_up else 0
        return INPUT(
            type=self.INPUT_KEYBOARD,
            ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0),
        )

    def _mouse_input(self, x: int, y: int, flags: int) -> Any:
        return INPUT(
            type=self.INPUT_MOUSE,
            mi=MOUSEINPUT(dx=x, dy=y, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0),
        )

    def _screen_point(self, x: float, y: float, space: CoordinateSpace) -> tuple[int, int]:
        return resolve_screen_point(x, y, space, self.client_rect())

    def _absolute_virtual_coordinates(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        left = self.user32.GetSystemMetrics(self.SM_XVIRTUALSCREEN)
        top = self.user32.GetSystemMetrics(self.SM_YVIRTUALSCREEN)
        width = self.user32.GetSystemMetrics(self.SM_CXVIRTUALSCREEN)
        height = self.user32.GetSystemMetrics(self.SM_CYVIRTUALSCREEN)
        try:
            return normalize_virtual_desktop_point(
                screen_x,
                screen_y,
                left=left,
                top=top,
                width=width,
                height=height,
            )
        except ValueError as exc:
            raise RuntimeError("Windows reported an invalid virtual desktop size.") from exc

    def _move_cursor(self, x: float, y: float, space: CoordinateSpace) -> None:
        screen_x, screen_y = self._screen_point(x, y, space)
        absolute_x, absolute_y = self._absolute_virtual_coordinates(screen_x, screen_y)
        self._send(
            [
                self._mouse_input(
                    absolute_x,
                    absolute_y,
                    self.MOUSEEVENTF_MOVE
                    | self.MOUSEEVENTF_ABSOLUTE
                    | self.MOUSEEVENTF_VIRTUALDESK,
                )
            ]
        )

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        started = datetime.now(UTC)
        if self.focus_before_input:
            self._focus()
        primitive_count = 1

        if isinstance(action, KeyAction):
            vk = self._vk(action.key)
            self._send([self._keyboard_input(vk, key_up=False)])
            try:
                if action.hold_seconds:
                    await asyncio.sleep(action.hold_seconds)
            finally:
                self._send([self._keyboard_input(vk, key_up=True)])
        elif isinstance(action, HotkeyAction):
            keys = [self._vk(key) for key in action.keys]
            pressed: list[int] = []
            try:
                for vk in keys:
                    self._send([self._keyboard_input(vk, key_up=False)])
                    pressed.append(vk)
                if action.hold_seconds:
                    await asyncio.sleep(action.hold_seconds)
            finally:
                if pressed:
                    self._send(
                        [self._keyboard_input(vk, key_up=True) for vk in reversed(pressed)]
                    )
            primitive_count = len(keys) * 2
        elif isinstance(action, MoveCursorAction):
            self._move_cursor(action.x, action.y, action.space)
        elif isinstance(action, ClickAction):
            self._move_cursor(action.x, action.y, action.space)
            button_flags = {
                MouseButton.LEFT: (self.MOUSEEVENTF_LEFTDOWN, self.MOUSEEVENTF_LEFTUP),
                MouseButton.RIGHT: (self.MOUSEEVENTF_RIGHTDOWN, self.MOUSEEVENTF_RIGHTUP),
                MouseButton.MIDDLE: (self.MOUSEEVENTF_MIDDLEDOWN, self.MOUSEEVENTF_MIDDLEUP),
            }[action.button]
            for click_index in range(action.clicks):
                self._send(
                    [
                        self._mouse_input(0, 0, button_flags[0]),
                        self._mouse_input(0, 0, button_flags[1]),
                    ]
                )
                if click_index + 1 < action.clicks and action.interval_seconds:
                    await asyncio.sleep(action.interval_seconds)
            primitive_count = action.clicks * 2 + 1
        else:
            raise TypeError(f"Unsupported primitive input action: {type(action).__name__}")

        if self.post_input_delay_seconds:
            await asyncio.sleep(self.post_input_delay_seconds)
        finished = datetime.now(UTC)
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=finished,
            primitive_actions=primitive_count,
            message="Input sent to the Kenshi window.",
        )
