from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .models import PlannerDecision
from .reporting import format_action

OverlayFeedOperation = Literal["append", "replace", "skip"]
OverlayLayout = Literal["companion", "overlay"]


@dataclass(slots=True)
class OverlayFeedState:
    """Coalesce high-frequency progress samples into one visible feed row."""

    progress_id: str | None = None
    rendered_progress: str | None = None

    def operation(
        self,
        record: dict[str, Any],
        rendered: str,
    ) -> OverlayFeedOperation:
        if record.get("event_type") != "option_progress":
            self.progress_id = None
            self.rendered_progress = None
            return "append"

        payload = record.get("payload") or {}
        evidence = payload.get("evidence") or {}
        progress_id = str(
            evidence.get("option_id")
            or (
                f"{payload.get('plan_id', '?')}:"
                f"{payload.get('plan_version', '?')}:"
                f"{payload.get('step_id', '?')}"
            )
        )
        if progress_id != self.progress_id:
            self.progress_id = progress_id
            self.rendered_progress = rendered
            return "append"
        if rendered == self.rendered_progress:
            return "skip"
        self.rendered_progress = rendered
        return "replace"


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


@dataclass(frozen=True, slots=True)
class CompanionLayout:
    viewer: WindowRect
    resized_anchor: WindowRect | None = None


def companion_layout(
    anchor: WindowRect,
    work_area: WindowRect,
    *,
    preferred_width: int = 380,
    gap: int = 8,
) -> CompanionLayout:
    """Place a narrow viewer beside a terminal, splitting it only when needed."""

    width = min(preferred_width, max(280, work_area.width // 3))
    if work_area.right - anchor.right >= width + gap:
        return CompanionLayout(
            viewer=WindowRect(
                anchor.right + gap,
                anchor.top,
                anchor.right + gap + width,
                anchor.bottom,
            )
        )
    if anchor.left - work_area.left >= width + gap:
        return CompanionLayout(
            viewer=WindowRect(
                anchor.left - gap - width,
                anchor.top,
                anchor.left - gap,
                anchor.bottom,
            )
        )

    anchor_width = max(640, anchor.width - width - gap)
    anchor_width = min(anchor_width, max(anchor.width - 280 - gap, 1))
    resized = WindowRect(
        anchor.left,
        anchor.top,
        anchor.left + anchor_width,
        anchor.bottom,
    )
    viewer_left = resized.right + gap
    return CompanionLayout(
        viewer=WindowRect(
            viewer_left,
            anchor.top,
            min(viewer_left + width, work_area.right),
            anchor.bottom,
        ),
        resized_anchor=resized,
    )


def ownership_banner(record: dict[str, Any]) -> tuple[str, str] | None:
    event_type = record.get("event_type")
    payload = record.get("payload") or {}
    if event_type == "agent_takeover_countdown":
        remaining = payload.get("seconds_remaining", "?")
        return (
            f"AGENT TAKEOVER IN {remaining}s  |  MOVE MOUSE TO CANCEL  |  F12 DISARMS",
            "#8a5b00",
        )
    if event_type not in {
        "control_ownership_changed",
        "agent_takeover_cancelled",
        "agent_takeover_ready",
    }:
        return None
    state = payload.get("state")
    if state == "human_control":
        return ("HUMAN CONTROL  |  AGENT YIELDED", "#164e63")
    if state == "takeover_pending":
        return ("AGENT TAKEOVER PENDING  |  MOVE MOUSE TO CANCEL", "#8a5b00")
    if state == "agent_active":
        return ("AGENT ACTIVE  |  AUTOMATION OWNS INPUT", "#14532d")
    if state == "disarmed":
        return ("AGENT DISARMED  |  HUMAN CONTROL", "#7f1d1d")
    return None


def _step_action_label(step_payload: dict[str, Any]) -> str:
    """A short, readable name for one plan step's action."""

    action = step_payload.get("action") or {}
    kind = action.get("kind", "?")
    for field in ("target_id", "exact_label", "cell_label", "item_name", "expected_screen"):
        value = action.get(field)
        if value:
            text = str(value)
            if len(text) > 24:
                text = text[:21] + "..."
            return f"{kind}({text})"
    return str(kind)


def format_event(record: dict[str, Any]) -> str | None:
    event_type = record.get("event_type")
    step_index = record.get("step_index")
    step = f"step {step_index:02d}" if isinstance(step_index, int) else "run"
    payload = record.get("payload") or {}

    if event_type == "run_started":
        return (
            f"RUN STARTED | {payload.get('max_steps', '?')} turns | "
            f"CONTROL {payload.get('control_mode', 'unknown')}\n"
        )
    if event_type == "decision":
        decision = PlannerDecision.model_validate(payload["decision"])
        latency = float(payload.get("planner_latency_seconds", 0.0))
        source = payload.get("source", "planner")
        return (
            f"{step} | DECIDE {latency:.2f}s | {source}\n"
            f"INTENT  {decision.intent}\n"
            f"WHY     {decision.rationale}\n"
            f"ACTION  {format_action(decision.action)}\n"
            f"CONF    {decision.confidence:.0%}\n"
        )
    if event_type == "action_receipt":
        status = "DONE" if payload.get("accepted") and not payload.get("error_type") else "FAILED"
        return f"{step} | {status} | {payload.get('message') or 'Action completed.'}\n"
    if event_type == "control_ownership_changed":
        return (
            f"CONTROL {str(payload.get('state', 'unknown')).upper()}\n"
            f"{payload.get('reason', 'Control ownership changed.')}\n"
        )
    if event_type == "agent_takeover_countdown":
        return (
            f"*** AGENT TAKEOVER IN {payload.get('seconds_remaining', '?')}s ***\n"
            "Move the mouse or press a key to keep human control. F12 disarms.\n"
        )
    if event_type == "agent_takeover_cancelled":
        return f"TAKEOVER CANCELLED | {payload.get('reason', 'Human control retained.')}\n"
    if event_type == "agent_takeover_ready":
        return (
            "TAKEOVER COUNTDOWN COMPLETE\n"
            f"{payload.get('reason', 'Current state is being revalidated.')}\n"
        )
    # Continuous mode narrates itself through plan lifecycle events. Without
    # these the overlay stayed blank through an entire run while the agent was
    # planning, being rejected, and replanning - the operator could see the game
    # moving but had no idea why.
    if event_type == "strategic_planner_call":
        latency = float(payload.get("planner_latency_seconds", 0.0))
        source = payload.get("source", "planner")
        if source == "planner_error":
            return f"{step} | THINKING {latency:.1f}s | response was unusable\n"
        return f"{step} | THINKING {latency:.1f}s | {payload.get('output_type', '')}\n"
    if event_type == "plan_proposed":
        evidence = payload.get("evidence") or {}
        plan = evidence.get("plan") or {}
        actions = [
            _step_action_label(item) for item in (plan.get("steps") or [])
        ]
        objective = plan.get("objective") or payload.get("reason", "")
        lines = [f"{step} | PLAN {payload.get('plan_id', '?')}"]
        if objective:
            lines.append(f"GOAL    {objective}")
        if actions:
            lines.append("STEPS   " + " -> ".join(actions))
        return "\n".join(lines) + "\n"
    if event_type == "plan_accepted":
        return f"{step} | PLAN ACCEPTED | {payload.get('plan_id', '?')}\n"
    if event_type == "plan_rejected":
        return f"{step} | PLAN REJECTED\n{payload.get('reason', 'No reason given.')}\n"
    if event_type == "plan_step_started":
        return f"{step} | RUNNING {payload.get('step_id', '?')}\n"
    if event_type == "plan_step_succeeded":
        return f"{step} | STEP OK  {payload.get('step_id', '?')}\n"
    if event_type in {"plan_step_cancelled", "plan_aborted"}:
        return (
            f"{step} | STEP STOPPED {payload.get('step_id', '?')}\n"
            f"{payload.get('reason', '')}\n"
        )
    if event_type == "plan_completed":
        return f"{step} | PLAN COMPLETE | {payload.get('plan_id', '?')}\n"
    if event_type == "planner_error":
        return f"{step} | PLANNER ERROR | {payload.get('message', '')[:300]}\n"
    if event_type == "replan_stalled":
        return (
            f"{step} | REPLAN STALLED | "
            f"{payload.get('identical_failures', '?')} identical failures\n"
            f"{payload.get('reason', '')[:300]}\n"
        )
    if event_type == "safety_supervisor_preempted":
        return (
            f"{step} | SAFETY STOP | {payload.get('cause', 'unknown')}\n"
            f"{payload.get('reason', '')}\n"
        )
    if event_type == "option_progress":
        return f"{step} | ... {payload.get('reason', '')}\n"
    if event_type in {"action_rejected", "environment_error"}:
        message = payload.get("message") or payload.get("error_type") or "Unknown error."
        return f"{step} | ERROR | {message}\n"
    if event_type == "run_finished":
        return (
            f"RUN FINISHED | {payload.get('steps_completed', '?')} turns | "
            f"CONTROL {payload.get('control_mode', 'unknown')}\n"
            f"{payload.get('stop_reason', 'Episode ended.')}\n"
        )
    if event_type == "run_finished_safety":
        confirmed = payload.get("status") == "pause_confirmed"
        state = "PAUSE CONFIRMED" if confirmed else "PAUSE UNVERIFIED"
        return (
            f"FINAL CONTROL | {state}\n"
            f"{payload.get('reason', 'No terminal safety result was recorded.')}\n"
        )
    return None


# Colour by what the operator needs to notice, not by event name: what the
# agent is trying to do, what worked, what refused it, and what stopped it.
EVENT_COLOURS: dict[str, str] = {
    "goal": "#7fd6ff",      # what it is trying to do
    "progress": "#9fe6a0",  # something worked
    "refused": "#ffcf6b",   # refused, but recoverable - it will try again
    "error": "#ff8f8f",     # failed
    "safety": "#ff6b6b",    # a brake fired
    "thinking": "#9aa4ad",  # waiting on the model
    "control": "#d5b3ff",   # ownership changed hands
    "plain": "#e8e8e8",
}

_EVENT_CATEGORIES: dict[str, str] = {
    "run_started": "goal",
    "plan_proposed": "goal",
    "plan_accepted": "goal",
    "decision": "goal",
    "plan_step_started": "thinking",
    "strategic_planner_call": "thinking",
    "option_progress": "thinking",
    "plan_step_succeeded": "progress",
    "plan_completed": "progress",
    "action_receipt": "progress",
    "plan_rejected": "refused",
    "plan_step_cancelled": "refused",
    "plan_aborted": "refused",
    "action_rejected": "refused",
    "planner_error": "error",
    "replan_stalled": "safety",
    "environment_error": "error",
    "safety_supervisor_preempted": "safety",
    "control_ownership_changed": "control",
    "agent_takeover_countdown": "control",
    "agent_takeover_cancelled": "control",
    "agent_takeover_ready": "control",
    "run_finished": "goal",
    "run_finished_safety": "control",
}


def event_category(record: dict[str, Any]) -> str:
    """Which colour band an event belongs to."""

    event_type = str(record.get("event_type", ""))
    category = _EVENT_CATEGORIES.get(event_type, "plain")
    # A failed action reads as an error even though receipts are usually progress.
    if event_type == "action_receipt":
        payload = record.get("payload") or {}
        if payload.get("error_type") or not payload.get("accepted"):
            return "error"
    if event_type == "run_finished_safety":
        payload = record.get("payload") or {}
        if payload.get("status") != "pause_confirmed":
            return "safety"
    return category


def show_overlay(
    log_path: Path,
    *,
    title: str = "Kenshi Agent",
    opacity: float = 0.82,
    auto_close_seconds: float = 0.0,
    layout: OverlayLayout = "companion",
) -> None:
    if not 0.25 <= opacity <= 1.0:
        raise ValueError("opacity must be between 0.25 and 1.0")
    if layout not in {"companion", "overlay"}:
        raise ValueError("layout must be 'companion' or 'overlay'")

    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    if layout == "companion":
        root.withdraw()
    root.title(title)
    root.configure(bg="#101216")
    root.attributes("-topmost", layout == "overlay")
    root.attributes("-alpha", opacity if layout == "overlay" else 1.0)
    root.update_idletasks()
    if layout == "overlay" and not _exclude_from_capture(root.winfo_id()):
        root.destroy()
        raise RuntimeError(
            "Windows could not exclude the decision overlay from screenshots; "
            "the viewer was closed so it cannot contaminate model input."
        )
    def restore_anchor() -> None:
        return None

    if layout == "overlay":
        width = 620
        height = 520
        x = max(0, root.winfo_screenwidth() - width - 24)
        root.geometry(f"{width}x{height}+{x}+48")
    else:
        restore_anchor = _dock_beside_windows_terminal(root)
        # The companion is intentionally not topmost, but it must enter the
        # normal window stack above the terminal it just split. Without this
        # one-time lift Windows can resize Terminal while leaving the new Tk
        # window hidden behind it. Kenshi will still cover both when focused.
        root.deiconify()
        root.lift()

    heading = tk.Label(
        root,
        text=(
            "KENSHI AGENT  |  LIVE DECISIONS"
            if layout == "overlay"
            else "KENSHI AGENT  |  COMPANION FEED"
        ),
        anchor="w",
        padx=14,
        pady=10,
        bg="#181c22",
        fg="#8bd5ca",
        font=tkfont.Font(family="Consolas", size=11, weight="bold"),
    )
    heading.pack(fill="x")

    text = tk.Text(
        root,
        wrap="word",
        padx=14,
        pady=12,
        borderwidth=0,
        highlightthickness=0,
        bg="#101216",
        fg="#e7e9ee",
        insertbackground="#e7e9ee",
        selectbackground="#334155",
        font=tkfont.Font(family="Consolas", size=10),
        state="disabled",
    )
    text.pack(fill="both", expand=True)

    offset = 0
    close_scheduled = False
    feed_state = OverlayFeedState()
    progress_tag = "_coalesced_option_progress"

    for name, colour in EVENT_COLOURS.items():
        text.tag_configure(name, foreground=colour)

    def append(
        value: str,
        category: str = "plain",
        *,
        replaceable_progress: bool = False,
    ) -> None:
        text.configure(state="normal")
        text.tag_remove(progress_tag, "1.0", "end")
        tags = (category, progress_tag) if replaceable_progress else (category,)
        text.insert("end-1c", value + "\n", tags)
        line_count = int(text.index("end-1c").split(".")[0])
        if line_count > 240:
            text.delete("1.0", f"{line_count - 200}.0")
        text.see("end")
        text.configure(state="disabled")

    def replace_progress(value: str, category: str) -> None:
        text.configure(state="normal")
        ranges = text.tag_ranges(progress_tag)
        if len(ranges) == 2:
            start, end = str(ranges[0]), str(ranges[1])
            text.delete(start, end)
            text.insert(start, value + "\n", (category, progress_tag))
            text.see("end")
            text.configure(state="disabled")
            return
        text.configure(state="disabled")
        append(value, category, replaceable_progress=True)

    append("Waiting for the agent run to begin...", "thinking")

    def poll() -> None:
        nonlocal offset, close_scheduled
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                while line := handle.readline():
                    offset = handle.tell()
                    try:
                        record = json.loads(line)
                        rendered = format_event(record)
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
                    if rendered is not None:
                        category = event_category(record)
                        operation = feed_state.operation(record, rendered)
                        if operation == "append":
                            append(
                                rendered,
                                category,
                                replaceable_progress=(
                                    record.get("event_type") == "option_progress"
                                ),
                            )
                        elif operation == "replace":
                            replace_progress(rendered, category)
                    banner = ownership_banner(record)
                    if banner is not None:
                        banner_text, banner_colour = banner
                        heading.configure(text=banner_text, bg=banner_colour)
                    if (
                        record.get("event_type") == "run_finished"
                        and auto_close_seconds > 0
                        and not close_scheduled
                    ):
                        close_scheduled = True
                        root.after(int(auto_close_seconds * 1000), root.destroy)
        root.after(150, poll)

    if layout == "overlay":
        # Applied last, once the window is fully built, positioned and mapped.
        # Applying it before Tk finishes arranging the toplevel left the bit set on
        # a window Tk then re-framed, so the style read back correctly while the
        # overlay carried on swallowing clicks.
        root.update_idletasks()
        if not _make_click_through(root.winfo_id()):
            root.destroy()
            raise RuntimeError(
                "Windows could not make the decision overlay click-through; the "
                "viewer was closed so it cannot swallow input meant for Kenshi."
            )

    root.after(50, poll)
    try:
        root.mainloop()
    finally:
        restore_anchor()


def _dock_beside_windows_terminal(root: Any) -> Callable[[], None]:
    """Dock beside Windows Terminal and restore any temporary split on exit."""

    fallback_width = 380
    fallback_height = max(520, root.winfo_screenheight() - 96)
    fallback_x = max(0, root.winfo_screenwidth() - fallback_width - 24)
    root.geometry(f"{fallback_width}x{fallback_height}+{fallback_x}+48")
    if sys.platform != "win32":
        return lambda: None

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("monitor", Rect),
            ("work", Rect),
            ("flags", wintypes.DWORD),
        ]

    terminal_windows: list[int] = []
    enum_callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [enum_callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [
        wintypes.HMONITOR,
        ctypes.POINTER(MonitorInfo),
    ]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsZoomed.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL

    @enum_callback_type
    def collect_terminal(window: int, _: int) -> bool:
        if not user32.IsWindowVisible(window):
            return True
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(window, class_name, len(class_name))
        if class_name.value == "CASCADIA_HOSTING_WINDOW_CLASS":
            terminal_windows.append(int(window))
        return True

    user32.EnumWindows(collect_terminal, 0)
    if not terminal_windows:
        return lambda: None

    foreground = int(user32.GetForegroundWindow())
    terminal = foreground if foreground in terminal_windows else terminal_windows[0]
    rect = Rect()
    if not user32.GetWindowRect(terminal, ctypes.byref(rect)):
        return lambda: None
    monitor = user32.MonitorFromWindow(terminal, 2)
    monitor_info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
        return lambda: None

    original = WindowRect(rect.left, rect.top, rect.right, rect.bottom)
    work = WindowRect(
        monitor_info.work.left,
        monitor_info.work.top,
        monitor_info.work.right,
        monitor_info.work.bottom,
    )
    split = companion_layout(original, work)
    was_maximized = bool(user32.IsZoomed(terminal))
    swp_no_zorder = 0x0004
    swp_no_activate = 0x0010
    if split.resized_anchor is not None:
        if was_maximized:
            user32.ShowWindow(terminal, 9)
        resized = split.resized_anchor
        user32.SetWindowPos(
            terminal,
            0,
            resized.left,
            resized.top,
            resized.width,
            resized.height,
            swp_no_zorder | swp_no_activate,
        )

    viewer = split.viewer
    # Tk interprets negative geometry coordinates as offsets from the right or
    # bottom edge, which places a left-monitor companion on the right monitor.
    # Size through Tk, then place the actual top-level HWND in absolute virtual-
    # desktop coordinates so negative X means the left display as intended.
    root.geometry(f"{viewer.width}x{viewer.height}+0+0")
    root.update_idletasks()
    root.deiconify()
    root.update_idletasks()
    companion_window = wintypes.HWND(root.winfo_id())
    while parent := user32.GetParent(companion_window):
        companion_window = parent
    swp_show_window = 0x0040
    user32.SetWindowPos(
        companion_window,
        0,
        viewer.left,
        viewer.top,
        viewer.width,
        viewer.height,
        swp_no_zorder | swp_no_activate | swp_show_window,
    )

    restored = False

    def restore() -> None:
        nonlocal restored
        if restored or split.resized_anchor is None:
            return
        restored = True
        if was_maximized:
            user32.ShowWindow(terminal, 3)
            return
        user32.SetWindowPos(
            terminal,
            0,
            original.left,
            original.top,
            original.width,
            original.height,
            swp_no_zorder | swp_no_activate,
        )

    return restore


def _make_click_through(window_id: int) -> bool:
    """Stop the overlay from ever receiving a mouse event.

    It is topmost and sits over the top-right of the screen, which on the trade
    screen is where Kenshi draws the shop's item grid. A layered window still
    swallows input in its own rectangle unless it is marked transparent, so
    every hover and click the agent aimed at a cell under the overlay went to
    the viewer instead. The pointer still renders over the item, which is what
    made it look like Kenshi was ignoring a perfectly good hover.
    """
    if sys.platform != "win32":
        return True

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND

    # GetWindowLongPtrW only exists on 64-bit; the 32-bit name is the fallback.
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_long.restype = ctypes.c_ssize_t
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_long.restype = ctypes.c_ssize_t

    window = wintypes.HWND(window_id)
    while parent := user32.GetParent(window):
        window = parent

    gwl_exstyle = -20
    ws_ex_layered = 0x00080000
    ws_ex_transparent = 0x00000020
    ws_ex_noactivate = 0x08000000

    ctypes.set_last_error(0)
    style = get_long(window, gwl_exstyle)
    # 0 is a legitimate style, so it is only a failure if the call said so.
    if style == 0 and ctypes.get_last_error():
        return False
    wanted = style | ws_ex_layered | ws_ex_transparent | ws_ex_noactivate
    ctypes.set_last_error(0)
    if not set_long(window, gwl_exstyle, wanted) and ctypes.get_last_error():
        return False

    # Setting the bit is not enough: Windows caches the frame and keeps
    # hit-testing the window until it is told the frame changed. Without this
    # the style reads back correctly and the overlay still eats every click,
    # which is exactly how this looked the first time it was "fixed".
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    swp_nomove = 0x0002
    swp_nosize = 0x0001
    swp_nozorder = 0x0004
    swp_noactivate = 0x0010
    swp_framechanged = 0x0020
    user32.SetWindowPos(
        window,
        wintypes.HWND(0),
        0,
        0,
        0,
        0,
        swp_nomove | swp_nosize | swp_nozorder | swp_noactivate | swp_framechanged,
    )
    return bool(get_long(window, gwl_exstyle) & ws_ex_transparent)


def _exclude_from_capture(window_id: int) -> bool:
    if sys.platform != "win32":
        return True

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

    window = wintypes.HWND(window_id)
    while parent := user32.GetParent(window):
        window = parent
    wda_exclude_from_capture = 0x00000011
    return bool(user32.SetWindowDisplayAffinity(window, wda_exclude_from_capture))
