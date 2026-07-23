from __future__ import annotations

import asyncio
import ctypes
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    ScrollAction,
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


def wheel_delta_data(notches: int) -> int:
    """Encode signed Win32 wheel notches in MOUSEINPUT's unsigned DWORD."""
    return ctypes.c_uint32(notches * 120).value


def relative_pointer_delta(
    current: tuple[int, int],
    target: tuple[int, int],
    *,
    max_step_pixels: int,
    tolerance_pixels: int,
) -> tuple[int, int]:
    """Return one bounded relative correction toward a screen-space target."""
    error_x = target[0] - current[0]
    error_y = target[1] - current[1]
    if abs(error_x) <= tolerance_pixels and abs(error_y) <= tolerance_pixels:
        return 0, 0

    def damped(error: int) -> int:
        if error == 0:
            return 0
        half_error = max(1, abs(error) // 2)
        magnitude = min(max_step_pixels, half_error)
        return magnitude if error > 0 else -magnitude

    return (
        damped(error_x),
        damped(error_y),
    )


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

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class Win32InputController(InputController):
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
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
    _EXTENDED_VIRTUAL_KEYS = {
        0x21,  # Page Up
        0x22,  # Page Down
        0x23,  # End
        0x24,  # Home
        0x25,  # Left
        0x26,  # Up
        0x27,  # Right
        0x28,  # Down
        0x2D,  # Insert
        0x2E,  # Delete
    }

    def __init__(
        self,
        window_title_contains: str,
        *,
        focus_before_input: bool = True,
        post_input_delay_seconds: float = 0.08,
        polite_input_enabled: bool = True,
        idle_seconds_before_input: float = 1.25,
        max_wait_for_input_turn_seconds: float = 60.0,
        restore_foreground_after_input: bool = True,
        restore_cursor_after_input: bool = True,
        alt_tab_after_input: bool = True,
        pointer_mode: str = "absolute",
        relative_pointer_max_step_pixels: int = 12,
        relative_pointer_tolerance_pixels: int = 1,
        relative_pointer_settle_seconds: float = 0.006,
        relative_pointer_max_attempts: int = 500,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("Win32InputController is available only on Windows.")
        self.window_title_contains = window_title_contains.casefold()
        self.focus_before_input = focus_before_input
        self.post_input_delay_seconds = post_input_delay_seconds
        self.polite_input_enabled = polite_input_enabled
        self.idle_seconds_before_input = idle_seconds_before_input
        self.max_wait_for_input_turn_seconds = max_wait_for_input_turn_seconds
        self.restore_foreground_after_input = restore_foreground_after_input
        self.restore_cursor_after_input = restore_cursor_after_input
        self.alt_tab_after_input = alt_tab_after_input
        if pointer_mode not in {"absolute", "relative"}:
            raise ValueError(f"Unsupported pointer mode: {pointer_mode!r}")
        self.pointer_mode = pointer_mode
        self.relative_pointer_max_step_pixels = relative_pointer_max_step_pixels
        self.relative_pointer_tolerance_pixels = relative_pointer_tolerance_pixels
        self.relative_pointer_settle_seconds = relative_pointer_settle_seconds
        self.relative_pointer_max_attempts = relative_pointer_max_attempts
        self.user32 = getattr(ctypes, "windll").user32  # noqa: B009 - Windows-only
        self.kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - Windows-only
        enable_per_monitor_dpi_awareness(self.user32)
        self._configure_signatures()
        self._lease_active = False
        self._lease_interrupted = False
        self._safety_override_active = False
        self._expected_foreground: int | None = None
        self._expected_cursor: tuple[int, int] | None = None
        self._last_agent_input_tick: int | None = None
        self._restore_foreground: int | None = None
        self._restore_cursor: tuple[int, int] | None = None
        self._last_lease_wait_seconds = 0.0
        self._lease_alt_tab_on_restore = False
        self._lease_kenshi_foreground: int | None = None

    def _configure_signatures(self) -> None:
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.GetAsyncKeyState.argtypes = [wintypes.INT]
        self.user32.GetAsyncKeyState.restype = wintypes.SHORT
        self.user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self.user32.MapVirtualKeyW.restype = wintypes.UINT
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL
        self.user32.SetCursorPos.argtypes = [wintypes.INT, wintypes.INT]
        self.user32.SetCursorPos.restype = wintypes.BOOL
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        self.user32.GetLastInputInfo.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        self.user32.AttachThreadInput.restype = wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.BringWindowToTop.argtypes = [wintypes.HWND]
        self.user32.BringWindowToTop.restype = wintypes.BOOL
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.kernel32.GetTickCount.restype = wintypes.DWORD

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

    def _focus_handle(self, hwnd: int, *, restore_window: bool, strict: bool) -> bool:
        if self.user32.GetForegroundWindow() == hwnd:
            return True
        if restore_window:
            self.user32.ShowWindow(hwnd, self.SW_RESTORE)
        if self.user32.SetForegroundWindow(hwnd) and self.user32.GetForegroundWindow() == hwnd:
            return True

        current_thread = self.kernel32.GetCurrentThreadId()
        foreground = self.user32.GetForegroundWindow()
        thread_ids = {
            self.user32.GetWindowThreadProcessId(foreground, None) if foreground else 0,
            self.user32.GetWindowThreadProcessId(hwnd, None),
        }
        attached: list[int] = []
        try:
            for thread_id in thread_ids:
                if (
                    thread_id
                    and thread_id != current_thread
                    and self.user32.AttachThreadInput(current_thread, thread_id, True)
                ):
                    attached.append(thread_id)
            self.user32.BringWindowToTop(hwnd)
            self.user32.SetForegroundWindow(hwnd)
        finally:
            for thread_id in reversed(attached):
                self.user32.AttachThreadInput(current_thread, thread_id, False)

        if self.user32.GetForegroundWindow() != hwnd:
            if strict:
                raise RuntimeError(
                    "Windows refused foreground focus. Click Kenshi once, then retry."
                )
            return False
        return True

    def _focus(self) -> None:
        hwnd = self._find_window()
        self._focus_handle(hwnd, restore_window=True, strict=True)
        if self._lease_active:
            self._expected_foreground = hwnd
            self._lease_kenshi_foreground = hwnd

    def focus_window(self) -> None:
        self._focus()

    def _cursor_position(self) -> tuple[int, int]:
        point = wintypes.POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise getattr(ctypes, "WinError")()  # noqa: B009 - Windows-only
        return point.x, point.y

    def _last_input_tick(self) -> int:
        info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO), dwTime=0)
        if not self.user32.GetLastInputInfo(ctypes.byref(info)):
            raise getattr(ctypes, "WinError")()  # noqa: B009 - Windows-only
        return int(info.dwTime)

    def _idle_seconds(self) -> float:
        now = int(self.kernel32.GetTickCount())
        return ((now - self._last_input_tick()) & 0xFFFFFFFF) / 1000.0

    def _any_input_down(self) -> bool:
        return any(self.user32.GetAsyncKeyState(vk) & 0x8000 for vk in range(1, 256))

    async def _wait_for_input_turn(self) -> None:
        deadline = time.monotonic() + self.max_wait_for_input_turn_seconds
        while self._idle_seconds() < self.idle_seconds_before_input or self._any_input_down():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Timed out waiting for a quiet keyboard/mouse interval; "
                    "the planned input was not sent."
                )
            await asyncio.sleep(min(0.1, remaining))

    def _capture_interruption(self) -> None:
        self._lease_interrupted = True
        self._restore_foreground = int(self.user32.GetForegroundWindow() or 0) or None
        self._restore_cursor = self._cursor_position()

    def user_input_detected(self) -> bool:
        if not self._lease_active:
            return False
        current_tick = self._last_input_tick()
        current_foreground = int(self.user32.GetForegroundWindow() or 0) or None
        current_cursor = self._cursor_position()
        changed = (
            (
                self._last_agent_input_tick is not None
                and current_tick != self._last_agent_input_tick
            )
            or (
                self._expected_foreground is not None
                and current_foreground != self._expected_foreground
            )
            or (self._expected_cursor is not None and current_cursor != self._expected_cursor)
        )
        if changed and (not self._lease_interrupted or not self._safety_override_active):
            self._capture_interruption()
        return self._lease_interrupted

    def _mark_agent_input(self) -> None:
        if not self._lease_active:
            return
        self._last_agent_input_tick = self._last_input_tick()
        self._expected_foreground = int(self.user32.GetForegroundWindow() or 0) or None
        self._expected_cursor = self._cursor_position()

    def _alt_tab_to_previous_context(self) -> None:
        alt = self._vk("alt")
        tab = self._vk("tab")
        previous_override = self._safety_override_active
        self._safety_override_active = True
        try:
            self._send(
                [
                    self._keyboard_input(alt, key_up=False),
                    self._keyboard_input(tab, key_up=False),
                    self._keyboard_input(tab, key_up=True),
                    self._keyboard_input(alt, key_up=True),
                ]
            )
        finally:
            self._safety_override_active = previous_override
        time.sleep(0.05)

    def _restore_desktop_state(self) -> None:
        if (
            self.restore_foreground_after_input
            and self._restore_foreground is not None
            and self.user32.IsWindow(self._restore_foreground)
        ):
            use_alt_tab = (
                self._lease_alt_tab_on_restore
                and self.alt_tab_after_input
                and self._lease_kenshi_foreground is not None
                and int(self.user32.GetForegroundWindow() or 0) == self._lease_kenshi_foreground
            )
            if use_alt_tab:
                self._alt_tab_to_previous_context()
            current = int(self.user32.GetForegroundWindow() or 0) or None
            restore_target_is_kenshi = self._restore_foreground == self._lease_kenshi_foreground
            if current != self._restore_foreground and not (
                use_alt_tab and restore_target_is_kenshi
            ):
                self._focus_handle(self._restore_foreground, restore_window=False, strict=False)
        if self.restore_cursor_after_input and self._restore_cursor is not None:
            self.user32.SetCursorPos(*self._restore_cursor)

    @asynccontextmanager
    async def input_lease(self, *, alt_tab_on_restore: bool = False) -> AsyncIterator[None]:
        if not self.polite_input_enabled or self._lease_active:
            self._last_lease_wait_seconds = 0.0
            yield
            return
        wait_started = time.monotonic()
        await self._wait_for_input_turn()
        self._last_lease_wait_seconds = time.monotonic() - wait_started
        self._lease_active = True
        self._lease_alt_tab_on_restore = alt_tab_on_restore
        self._lease_kenshi_foreground = None
        self._lease_interrupted = False
        self._restore_foreground = int(self.user32.GetForegroundWindow() or 0) or None
        self._restore_cursor = self._cursor_position()
        self._expected_foreground = self._restore_foreground
        self._expected_cursor = self._restore_cursor
        self._last_agent_input_tick = self._last_input_tick()
        try:
            yield
        finally:
            self.user_input_detected()
            self._restore_desktop_state()
            self._lease_active = False
            self._expected_foreground = None
            self._expected_cursor = None
            self._last_agent_input_tick = None
            self._lease_alt_tab_on_restore = False
            self._lease_kenshi_foreground = None

    def input_lease_wait_seconds(self) -> float:
        return self._last_lease_wait_seconds

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
        if self._lease_active and self.user_input_detected() and not self._safety_override_active:
            raise RuntimeError("User input resumed; yielding the planned input turn.")
        array_type = INPUT * len(inputs)
        array = array_type(*inputs)
        sent = self.user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            error = getattr(ctypes, "get_last_error")()  # noqa: B009 - Windows-only
            raise RuntimeError(
                f"SendInput inserted {sent}/{len(inputs)} events (GetLastError={error}). "
                "Check window focus and Windows integrity levels."
            )
        self._mark_agent_input()

    def _keyboard_input(self, vk: int, *, key_up: bool) -> Any:
        scan_code = self.user32.MapVirtualKeyW(vk, 0)
        if not scan_code:
            raise RuntimeError(f"Windows could not map virtual key 0x{vk:02X} to a scan code.")
        flags = self.KEYEVENTF_SCANCODE
        if vk in self._EXTENDED_VIRTUAL_KEYS:
            flags |= self.KEYEVENTF_EXTENDEDKEY
        if key_up:
            flags |= self.KEYEVENTF_KEYUP
        return INPUT(
            type=self.INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=0,
                wScan=scan_code,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            ),
        )

    def _mouse_input(self, x: int, y: int, flags: int, *, mouse_data: int = 0) -> Any:
        return INPUT(
            type=self.INPUT_MOUSE,
            mi=MOUSEINPUT(
                dx=x,
                dy=y,
                mouseData=mouse_data,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            ),
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

    async def _move_cursor(self, x: float, y: float, space: CoordinateSpace) -> None:
        if self._lease_active and self.user_input_detected() and not self._safety_override_active:
            raise RuntimeError("User input resumed; yielding the planned input turn.")
        screen_x, screen_y = self._screen_point(x, y, space)
        if self.pointer_mode == "absolute":
            if not self.user32.SetCursorPos(screen_x, screen_y):
                raise getattr(ctypes, "WinError")()  # noqa: B009 - Windows-only
            actual = self._cursor_position()
            if actual != (screen_x, screen_y):
                raise RuntimeError(
                    "Windows did not place the cursor at the requested target: "
                    f"requested=({screen_x}, {screen_y}) actual={actual}."
                )
            self._mark_agent_input()
            return

        target = (screen_x, screen_y)
        for _ in range(self.relative_pointer_max_attempts):
            actual = self._cursor_position()
            delta_x, delta_y = relative_pointer_delta(
                actual,
                target,
                max_step_pixels=self.relative_pointer_max_step_pixels,
                tolerance_pixels=self.relative_pointer_tolerance_pixels,
            )
            if (delta_x, delta_y) == (0, 0):
                return
            self._send([self._mouse_input(delta_x, delta_y, self.MOUSEEVENTF_MOVE)])
            if self.relative_pointer_settle_seconds:
                await asyncio.sleep(self.relative_pointer_settle_seconds)
        actual = self._cursor_position()
        if relative_pointer_delta(
            actual,
            target,
            max_step_pixels=self.relative_pointer_max_step_pixels,
            tolerance_pixels=self.relative_pointer_tolerance_pixels,
        ) != (0, 0):
            raise RuntimeError(
                "Relative mouse input did not reach the requested target: "
                f"requested={target} actual={actual} after "
                f"{self.relative_pointer_max_attempts} attempts."
            )

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        return await self._execute(action, safety_override=False)

    async def execute_safety(self, action: PrimitiveInputAction) -> ActionReceipt:
        self._safety_override_active = True
        try:
            return await self._execute(action, safety_override=True)
        finally:
            self._safety_override_active = False

    async def _execute(
        self,
        action: PrimitiveInputAction,
        *,
        safety_override: bool,
    ) -> ActionReceipt:
        started = datetime.now(UTC)
        if self._lease_active and self.user_input_detected() and not safety_override:
            raise RuntimeError("User input resumed; yielding the planned input turn.")
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
                    self._send([self._keyboard_input(vk, key_up=True) for vk in reversed(pressed)])
            primitive_count = len(keys) * 2
        elif isinstance(action, MoveCursorAction):
            await self._move_cursor(action.x, action.y, action.space)
        elif isinstance(action, ClickAction):
            await self._move_cursor(action.x, action.y, action.space)
            button_flags = {
                MouseButton.LEFT: (self.MOUSEEVENTF_LEFTDOWN, self.MOUSEEVENTF_LEFTUP),
                MouseButton.RIGHT: (self.MOUSEEVENTF_RIGHTDOWN, self.MOUSEEVENTF_RIGHTUP),
                MouseButton.MIDDLE: (self.MOUSEEVENTF_MIDDLEDOWN, self.MOUSEEVENTF_MIDDLEUP),
            }[action.button]
            for click_index in range(action.clicks):
                if action.hold_seconds:
                    self._send([self._mouse_input(0, 0, button_flags[0])])
                    try:
                        await asyncio.sleep(action.hold_seconds)
                    finally:
                        self._send([self._mouse_input(0, 0, button_flags[1])])
                else:
                    self._send(
                        [
                            self._mouse_input(0, 0, button_flags[0]),
                            self._mouse_input(0, 0, button_flags[1]),
                        ]
                    )
                if click_index + 1 < action.clicks and action.interval_seconds:
                    await asyncio.sleep(action.interval_seconds)
            primitive_count = action.clicks * 2 + 1
        elif isinstance(action, ScrollAction):
            await self._move_cursor(action.x, action.y, action.space)
            wheel_data = wheel_delta_data(action.notches)
            self._send(
                [
                    self._mouse_input(
                        0,
                        0,
                        self.MOUSEEVENTF_WHEEL,
                        mouse_data=wheel_data,
                    ),
                ]
            )
            primitive_count = 2
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
