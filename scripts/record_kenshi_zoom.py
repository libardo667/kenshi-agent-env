from __future__ import annotations

import argparse
import ctypes
import json
import os
from ctypes import wintypes

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_MOUSEWHEEL = 0x020A
WM_TIMER = 0x0113

ZOOM_KEYS = {
    0x21: "page_up",
    0x22: "page_down",
    0x23: "end",
    0x24: "home",
    0x6B: "numpad_plus",
    0x6D: "numpad_minus",
    0xBB: "plus",
    0xBD: "minus",
}


class MouseHookData(ctypes.Structure):
    _fields_ = [
        ("point", wintypes.POINT),
        ("mouse_data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", wintypes.WPARAM),
    ]


class KeyboardHookData(ctypes.Structure):
    _fields_ = [
        ("vk_code", wintypes.DWORD),
        ("scan_code", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", wintypes.WPARAM),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record one zoom gesture while Kenshi owns the foreground."
    )
    parser.add_argument("--title", default="Kenshi 1.0.")
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args()


def main() -> int:
    if os.name != "nt":
        raise SystemExit("This calibration helper must run on Windows.")

    args = parse_args()
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    result_type = ctypes.c_ssize_t
    hook_callback_type = ctypes.WINFUNCTYPE(
        result_type, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
    user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = result_type
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        hook_callback_type,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    result: dict[str, object] = {}
    target: int | None = None

    enum_callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def find_window(hwnd: int, _: int) -> bool:
        nonlocal target
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if args.title.casefold() in buffer.value.casefold():
            if target is not None:
                raise RuntimeError("More than one matching Kenshi window is visible.")
            target = int(hwnd)
        return True

    enum_callback = enum_callback_type(find_window)
    user32.EnumWindows(enum_callback, 0)
    if target is None:
        raise SystemExit(f"No visible window title contains {args.title!r}.")

    client = wintypes.RECT()
    user32.GetClientRect(target, ctypes.byref(client))
    client_origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(target, ctypes.byref(client_origin))
    client_width = client.right - client.left
    client_height = client.bottom - client.top

    def foreground_is_kenshi() -> bool:
        return int(user32.GetForegroundWindow() or 0) == target

    def finish(payload: dict[str, object]) -> None:
        result.update(payload)
        user32.PostQuitMessage(0)

    def on_mouse(code: int, message: int, data_pointer: int) -> int:
        if code >= 0 and message == WM_MOUSEWHEEL and foreground_is_kenshi():
            data = ctypes.cast(data_pointer, ctypes.POINTER(MouseHookData)).contents
            delta = ctypes.c_short((data.mouse_data >> 16) & 0xFFFF).value
            client_x = data.point.x - client_origin.x
            client_y = data.point.y - client_origin.y
            finish(
                {
                    "kind": "mouse_wheel",
                    "delta": delta,
                    "screen": {"x": data.point.x, "y": data.point.y},
                    "client": {"x": client_x, "y": client_y},
                    "normalized": {
                        "x": client_x / max(1, client_width - 1),
                        "y": client_y / max(1, client_height - 1),
                    },
                }
            )
        return user32.CallNextHookEx(None, code, message, data_pointer)

    def on_keyboard(code: int, message: int, data_pointer: int) -> int:
        if code >= 0 and message in {WM_KEYDOWN, WM_SYSKEYDOWN} and foreground_is_kenshi():
            data = ctypes.cast(data_pointer, ctypes.POINTER(KeyboardHookData)).contents
            if data.vk_code in ZOOM_KEYS:
                finish({"kind": "key", "key": ZOOM_KEYS[data.vk_code]})
        return user32.CallNextHookEx(None, code, message, data_pointer)

    mouse_callback = hook_callback_type(on_mouse)
    keyboard_callback = hook_callback_type(on_keyboard)
    mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, None, 0)
    keyboard_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_callback, None, 0)
    if not mouse_hook or not keyboard_hook:
        raise ctypes.WinError()

    timer_id = user32.SetTimer(None, 0, max(1, args.timeout) * 1000, None)
    print(
        "Watching for one Kenshi zoom gesture. Focus Kenshi and zoom once.",
        flush=True,
    )
    message = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_TIMER:
                break
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
    finally:
        if timer_id:
            user32.KillTimer(None, timer_id)
        user32.UnhookWindowsHookEx(mouse_hook)
        user32.UnhookWindowsHookEx(keyboard_hook)

    if not result:
        print(json.dumps({"status": "timeout"}))
        return 1
    result["status"] = "recorded"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
