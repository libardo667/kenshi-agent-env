from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .models import PlannerDecision
from .reporting import format_action


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
    "environment_error": "error",
    "safety_supervisor_preempted": "safety",
    "control_ownership_changed": "control",
    "agent_takeover_countdown": "control",
    "agent_takeover_cancelled": "control",
    "agent_takeover_ready": "control",
    "run_finished": "goal",
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
    return category


def show_overlay(
    log_path: Path,
    *,
    title: str = "Kenshi Agent",
    opacity: float = 0.82,
    auto_close_seconds: float = 0.0,
) -> None:
    if not 0.25 <= opacity <= 1.0:
        raise ValueError("opacity must be between 0.25 and 1.0")

    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title(title)
    root.configure(bg="#101216")
    root.attributes("-topmost", True)
    root.attributes("-alpha", opacity)
    root.update_idletasks()
    if not _exclude_from_capture(root.winfo_id()):
        root.destroy()
        raise RuntimeError(
            "Windows could not exclude the decision overlay from screenshots; "
            "the viewer was closed so it cannot contaminate model input."
        )

    width = 620
    height = 520
    x = max(0, root.winfo_screenwidth() - width - 24)
    root.geometry(f"{width}x{height}+{x}+48")

    heading = tk.Label(
        root,
        text="KENSHI AGENT  |  LIVE DECISIONS",
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

    for name, colour in EVENT_COLOURS.items():
        text.tag_configure(name, foreground=colour)

    def append(value: str, category: str = "plain") -> None:
        text.configure(state="normal")
        text.insert("end", value + "\n", category)
        line_count = int(text.index("end-1c").split(".")[0])
        if line_count > 240:
            text.delete("1.0", f"{line_count - 200}.0")
        text.see("end")
        text.configure(state="disabled")

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
                        append(rendered, event_category(record))
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

    root.after(50, poll)
    root.mainloop()


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
