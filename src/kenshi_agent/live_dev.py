from __future__ import annotations

import argparse
import asyncio
import csv
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

from .cli import main as agent_main
from .config import AppConfig, ControlsConfig, load_config
from .control.base import InputController, PrimitiveInputAction, WindowRect
from .control.calibration import validate_expected_client_size
from .control.capture import WindowCapture
from .control.win32 import Win32InputController
from .display_lease import (
    DisplayLeaseError,
    DisplayTopologyController,
    external_display_lease,
)
from .final_safe_state import ensure_final_safe_state
from .gpu_events import (
    GpuTdrDetected,
    GpuTdrEvent,
    GpuTdrMonitor,
    query_windows_gpu_tdr_events,
)
from .graphics_profile import (
    GraphicsMismatch,
    GraphicsProfile,
    apply_graphics_profile,
    load_graphics_profile,
    verify_graphics_profile,
)
from .models import (
    ClickAction,
    HotkeyAction,
    KeyAction,
    TelemetrySnapshot,
    VisibleUIControl,
)
from .telemetry import TelemetryReader, TelemetryReadError


class LaunchInterrupted(RuntimeError):
    pass


class LaunchFailed(RuntimeError):
    pass


_TERMINAL_WINDOW_MARKERS = (
    "crash reporter",
    "has crashed",
    "steam dll error",
    "steam - error",
)


def _controller(
    config: AppConfig,
    *,
    window_title_contains: str | None = None,
) -> Win32InputController:
    return Win32InputController(
        window_title_contains or config.capture.window_title_contains,
        focus_before_input=config.controls.focus_before_input,
        post_input_delay_seconds=config.controls.post_input_delay_seconds,
        polite_input_enabled=True,
        idle_seconds_before_input=0.0,
        max_wait_for_input_turn_seconds=1.0,
        restore_foreground_after_input=True,
        restore_cursor_after_input=True,
        alt_tab_after_input=False,
        pointer_mode=config.controls.pointer_mode,
        relative_pointer_max_step_pixels=config.controls.relative_pointer_max_step_pixels,
        relative_pointer_tolerance_pixels=config.controls.relative_pointer_tolerance_pixels,
        relative_pointer_settle_seconds=config.controls.relative_pointer_settle_seconds,
        relative_pointer_max_attempts=config.controls.relative_pointer_max_attempts,
        relative_pointer_warp_enabled=config.controls.relative_pointer_warp_enabled,
        relative_pointer_warp_threshold_pixels=(
            config.controls.relative_pointer_warp_threshold_pixels
        ),
        relative_pointer_warp_offset_pixels=(
            config.controls.relative_pointer_warp_offset_pixels
        ),
    )


def _abort_if_human_input(controller: InputController) -> None:
    if controller.continuous_user_input_detected():
        raise LaunchInterrupted(
            "Kenshi startup automation stopped because human input was detected; "
            "all remaining startup clicks were permanently cancelled."
        )


def _terminal_window_title(controller: InputController) -> str | None:
    for title in controller.visible_window_titles():
        normalized = title.strip().casefold()
        if normalized == "bad stuff" or any(
            marker in normalized for marker in _TERMINAL_WINDOW_MARKERS
        ):
            return title
    return None


async def _wait_until(
    predicate: Callable[[], bool],
    timeout: float,
    description: str,
    *,
    controller: InputController,
    health_check: Callable[[], None] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health_check is not None:
            health_check()
        try:
            title = _terminal_window_title(controller)
        except (OSError, RuntimeError, ValueError):
            title = None
        if title is not None:
            raise LaunchFailed(
                f"Kenshi startup stopped because the terminal window {title!r} appeared."
            )
        _abort_if_human_input(controller)
        try:
            if predicate():
                return
        except LaunchFailed:
            raise
        except (OSError, RuntimeError, ValueError):
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {description}.")


def _plugin_ready(status_path: Path, launched_at: datetime) -> bool:
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    captured = datetime.fromisoformat(payload["captured_at"].replace("Z", "+00:00"))
    if captured < launched_at:
        return False
    state = payload.get("state")
    if state == "error":
        message = payload.get("message", "unknown native plug-in error")
        raise LaunchFailed(f"Telemetry plug-in startup failed: {message}")
    return bool(state == "ready")


def _shortcut() -> Path:
    override = os.environ.get("KENSHI_AGENT_SHORTCUT")
    candidates = [
        Path(override) if override else None,
        Path.home() / "OneDrive" / "Desktop" / "RE_Kenshi.lnk",
        Path.home() / "Desktop" / "RE_Kenshi.lnk",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "RE_Kenshi.lnk was not found. Set KENSHI_AGENT_SHORTCUT to its full path."
    )


def _re_kenshi_settings_path() -> Path:
    override = os.environ.get("KENSHI_AGENT_RE_KENSHI_SETTINGS")
    candidates = [Path(override)] if override else []
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if program_files_x86:
        candidates.append(
            Path(program_files_x86)
            / "Steam"
            / "steamapps"
            / "common"
            / "Kenshi"
            / "RE_Kenshi.ini"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "RE_Kenshi.ini was not found. Set KENSHI_AGENT_RE_KENSHI_SETTINGS "
        "to its full path."
    )


def _kenshi_settings_path() -> Path:
    override = os.environ.get("KENSHI_AGENT_SETTINGS")
    candidates = [Path(override)] if override else []
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if program_files_x86:
        candidates.append(
            Path(program_files_x86)
            / "Steam"
            / "steamapps"
            / "common"
            / "Kenshi"
            / "settings.cfg"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Kenshi settings.cfg was not found. Set KENSHI_AGENT_SETTINGS "
        "to its full path."
    )


def _kenshi_renderer_path() -> Path:
    override = os.environ.get("KENSHI_AGENT_RENDERER_SETTINGS")
    candidates = [Path(override)] if override else []
    try:
        candidates.append(_kenshi_settings_path().with_name("kenshi.cfg"))
    except FileNotFoundError:
        pass
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Kenshi kenshi.cfg was not found. Set KENSHI_AGENT_RENDERER_SETTINGS "
        "to its full path."
    )


def _steam_connection_log_path() -> Path:
    override = os.environ.get("KENSHI_AGENT_STEAM_CONNECTION_LOG")
    candidates = [Path(override)] if override else []
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if program_files_x86:
        candidates.append(
            Path(program_files_x86) / "Steam" / "logs" / "connection_log.txt"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Steam connection_log.txt was not found. Set "
        "KENSHI_AGENT_STEAM_CONNECTION_LOG to its full path."
    )


def _steam_connection_state(path: Path) -> str | None:
    state: str | None = None
    state_pattern = re.compile(r"^\[[^\]]+\] \[([^,\]]+)")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = state_pattern.match(line)
        if match is not None:
            state = match.group(1).strip()
    return state


def _running_process_names() -> set[str]:
    result = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10.0,
    )
    names: set[str] = set()
    for row in csv.reader(result.stdout.splitlines()):
        if row and row[0].casefold().endswith(".exe"):
            names.add(row[0].casefold())
    return names


def _available_physical_memory_mib() -> int:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.length = ctypes.sizeof(status)
    kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - Windows-only
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise getattr(ctypes, "WinError")()  # noqa: B009 - Windows-only
    return int(status.available_physical // (1024 * 1024))


def _configured_graphics_profile(config: AppConfig) -> GraphicsProfile:
    path = config.launch.graphics_profile_file
    if path is None:
        raise LaunchFailed("No launch.graphics_profile_file is configured.")
    return load_graphics_profile(path)


def _format_graphics_mismatches(
    mismatches: tuple[GraphicsMismatch, ...],
) -> str:
    details: list[str] = []
    for mismatch in mismatches:
        actual_text = "<missing>" if mismatch.actual is None else repr(mismatch.actual)
        details.append(
            f"{mismatch.document}:{mismatch.key} expected "
            f"{mismatch.expected!r}, found {actual_text}"
        )
    return "; ".join(details)


def _validate_launch_preconditions(
    config: AppConfig,
    *,
    process_names: set[str] | None = None,
    terminal_window_title: str | None = None,
    available_physical_memory_mib: int | None = None,
    settings_path: Path | None = None,
    renderer_path: Path | None = None,
    steam_connection_log_path: Path | None = None,
    resume_launcher: bool = False,
) -> None:
    if terminal_window_title is not None:
        raise LaunchFailed(
            f"Kenshi is in terminal state {terminal_window_title!r}. Run './dev crash' "
            "to archive evidence, or './dev crash --dismiss' to archive it before "
            "closing the unsent report."
        )
    names = process_names if process_names is not None else _running_process_names()
    if "kenshi_x64.exe" in names and not resume_launcher:
        raise LaunchFailed("Kenshi is already running; refusing to start a second client.")
    if "kenshi_x64.exe" not in names and resume_launcher:
        raise LaunchFailed(
            "No existing Kenshi launcher process is available to resume."
        )

    if config.launch.require_steam_logged_on:
        if "steam.exe" not in names:
            raise LaunchFailed("Steam is not running; no launch input was sent.")
        connection_log = steam_connection_log_path or _steam_connection_log_path()
        state = _steam_connection_state(connection_log)
        if state != "Logged On":
            raise LaunchFailed(
                "Steam is running but its latest connection state is "
                f"{state!r}, not 'Logged On'; no launch input was sent."
            )

    threshold = config.launch.min_free_physical_memory_mib
    if threshold:
        available = (
            available_physical_memory_mib
            if available_physical_memory_mib is not None
            else _available_physical_memory_mib()
        )
        if available < threshold:
            raise LaunchFailed(
                f"Only {available} MiB physical memory is available; this profile "
                f"requires at least {threshold} MiB before launch."
            )

    if config.launch.require_graphics_profile:
        profile = _configured_graphics_profile(config)
        installed = settings_path or _kenshi_settings_path()
        installed_renderer = renderer_path or _kenshi_renderer_path()
        verification = verify_graphics_profile(
            installed,
            profile,
            renderer_path=installed_renderer,
        )
        if not verification.matches:
            details = _format_graphics_mismatches(verification.mismatches)
            raise LaunchFailed(
                f"Graphics profile {profile.profile_id!r} is not installed exactly: "
                f"{details}. Run './dev graphics apply' while Kenshi is stopped."
            )


def _validate_resumable_launcher_rect(rect: WindowRect) -> None:
    if rect.width <= 0 or rect.height <= 0 or rect.width >= 1200:
        raise LaunchFailed(
            "--resume-launcher requires the exact small RE_Kenshi pre-game "
            "launcher window; no input was sent."
        )


def _disable_re_kenshi_startup_panel(path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("RE_Kenshi.ini must contain a JSON object.")
    if payload.get("OpenSettingOnStart") is False:
        return False
    if "OpenSettingOnStart" not in payload:
        raise ValueError("RE_Kenshi.ini has no OpenSettingOnStart setting.")

    backup = path.with_name(path.name + ".kenshi-agent.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + ".kenshi-agent.tmp")
    temporary.write_text(
        json.dumps(payload | {"OpenSettingOnStart": False}, indent=4) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return True


def _telemetry_read(config: AppConfig) -> TelemetryReader:
    return TelemetryReader(
        config.telemetry.file,
        max_age_seconds=config.telemetry.max_age_seconds,
        retries=config.telemetry.read_retries,
        retry_delay_seconds=config.telemetry.retry_delay_seconds,
        require_protocol_major=config.telemetry.require_protocol_major,
    )


async def _execute_primitive(
    controller: InputController,
    action: PrimitiveInputAction,
) -> None:
    _abort_if_human_input(controller)
    async with controller.input_lease():
        _abort_if_human_input(controller)
        receipt = await controller.execute(action)
    if not receipt.executed:
        raise RuntimeError(receipt.message)


async def _click(controller: InputController, x: float, y: float) -> None:
    await _execute_primitive(
        controller, ClickAction(x=x, y=y, hold_seconds=MYGUI_CLICK_HOLD_SECONDS)
    )


# Kenshi's MyGUI needs a measurable press; an instantaneous down/up moves the
# cursor and activates nothing. Matches controls.control_activation_hold_seconds.
MYGUI_CLICK_HOLD_SECONDS = 0.12


def _normalize_control_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _unique_visible_control(
    snapshot: TelemetrySnapshot,
    labels: list[str],
) -> VisibleUIControl | None:
    if "ui.visible_controls" not in snapshot.capabilities:
        return None
    expected = {_normalize_control_label(label) for label in labels}
    matches = [
        control
        for control in snapshot.ui.visible_controls or []
        if _normalize_control_label(control.label) in expected
    ]
    return matches[0] if len(matches) == 1 else None


async def _click_semantic_control(
    controller: InputController,
    reader: TelemetryReader,
    labels: list[str],
) -> None:
    _abort_if_human_input(controller)
    initial = reader.read()
    if initial.stale:
        raise RuntimeError("Semantic startup control requires fresh telemetry.")
    control = _unique_visible_control(initial.snapshot, labels)
    if control is None:
        raise RuntimeError(
            "Expected exactly one visible startup control matching "
            f"{labels!r} on telemetry sequence {initial.snapshot.sequence}."
        )

    async with controller.input_lease():
        _abort_if_human_input(controller)
        current = reader.read()
        if current.stale:
            raise RuntimeError(
                "Semantic startup control became stale inside the input lease."
            )
        current_control = _unique_visible_control(current.snapshot, labels)
        if current_control is None or current_control != control:
            raise RuntimeError(
                "Semantic startup control changed inside the input lease; no "
                "pointer input was sent."
            )
        x, y = current_control.center
        # Kenshi's MyGUI ignores an instantaneous press. This used to squeak
        # through only because relative stepping walked the cursor to the target
        # slowly; once the pointer began warping, a zero-duration click stopped
        # registering and startup silently stalled on the title screen.
        receipt = await controller.execute(
            ClickAction(x=x, y=y, hold_seconds=MYGUI_CLICK_HOLD_SECONDS)
        )
    if not receipt.executed:
        raise RuntimeError(receipt.message)


async def _wait_for_loaded_or_semantic_control(
    reader: TelemetryReader,
    labels: list[str],
    *,
    timeout: float,
    controller: InputController,
    health_check: Callable[[], None] | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health_check is not None:
            health_check()
        _abort_if_human_input(controller)
        try:
            result = reader.read()
        except TelemetryReadError:
            await asyncio.sleep(0.1)
            continue
        if not result.stale:
            if result.snapshot.game.loaded and bool(result.snapshot.squad):
                return True
            if _unique_visible_control(result.snapshot, labels) is not None:
                return False
        await asyncio.sleep(0.1)
    raise TimeoutError(
        "Timed out waiting for a loaded squad or the next semantic startup control."
    )


def _validate_calibrated_client_rect(
    rect: WindowRect,
    controls: ControlsConfig,
) -> None:
    expected_width = getattr(controls, "calibrated_client_width", None)
    expected_height = getattr(controls, "calibrated_client_height", None)
    validate_expected_client_size(
        rect.width,
        rect.height,
        expected_width=expected_width,
        expected_height=expected_height,
    )


async def _ensure_interrupted_safe_state(
    controller: InputController,
    reader: TelemetryReader,
    *,
    pause_key: str,
    timeout_seconds: float,
) -> str:
    outcome = await ensure_final_safe_state(
        controller=controller,
        telemetry=reader,
        pause_primitives=[KeyAction(key=pause_key)],
        timeout_seconds=timeout_seconds,
        input_authorized=True,
    )
    return outcome.reason


async def _observe_loaded_paused_health(
    reader: TelemetryReader,
    controller: InputController,
    *,
    duration_seconds: float,
    health_check: Callable[[], None] | None = None,
) -> None:
    if duration_seconds <= 0:
        return
    initial = reader.read()
    if initial.stale:
        raise LaunchFailed("Post-load health observation began with stale telemetry.")
    initial_sequence = initial.snapshot.sequence
    last_sequence = initial_sequence
    deadline = time.monotonic() + duration_seconds
    while time.monotonic() < deadline:
        if health_check is not None:
            health_check()
        try:
            title = _terminal_window_title(controller)
        except (OSError, RuntimeError, ValueError):
            title = None
        if title is not None:
            raise LaunchFailed(
                f"Post-load health observation failed because {title!r} appeared."
            )
        _abort_if_human_input(controller)
        try:
            result = reader.read()
        except TelemetryReadError as exc:
            raise LaunchFailed(
                f"Post-load health observation lost telemetry: {exc}"
            ) from exc
        snapshot = result.snapshot
        if (
            result.stale
            or not snapshot.game.loaded
            or not snapshot.squad
            or snapshot.game.paused is not True
        ):
            raise LaunchFailed(
                "Post-load health observation lost a fresh loaded paused squad."
            )
        last_sequence = max(last_sequence, snapshot.sequence)
        await asyncio.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    if last_sequence <= initial_sequence:
        raise LaunchFailed(
            "Post-load health observation saw no advancing telemetry sequence."
        )


async def _perform_launch(
    args: argparse.Namespace,
    config: AppConfig,
    controller: InputController,
    monitor: GpuTdrMonitor | None,
) -> None:
    health_check = monitor.raise_if_new if monitor is not None else None
    _disable_re_kenshi_startup_panel(_re_kenshi_settings_path())
    launched_at = datetime.now(UTC)
    if not args.resume_launcher:
        os.startfile(_shortcut())  # type: ignore[attr-defined]
    else:
        print("Resuming the existing verified RE_Kenshi pre-game launcher.")
    await _wait_until(
        lambda: controller.client_rect().width > 0,
        args.timeout,
        "Kenshi launcher",
        controller=controller,
        health_check=health_check,
    )
    launcher_rect = controller.client_rect()
    if launcher_rect.width < 1200:
        await _execute_primitive(controller, KeyAction(key="enter"))

    status_path = config.telemetry.file.parent / "plugin_status.json"
    await _wait_until(
        lambda: _plugin_ready(status_path, launched_at),
        args.timeout,
        "fresh telemetry plugin startup",
        controller=controller,
        health_check=health_check,
    )
    await _wait_until(
        lambda: controller.client_rect().width >= 1200,
        args.timeout,
        "full-size Kenshi window",
        controller=controller,
        health_check=health_check,
    )
    await asyncio.sleep(2.0)
    if monitor is not None:
        monitor.raise_if_new(force=True)
    _abort_if_human_input(controller)

    if args.continue_game:
        reader = _telemetry_read(config)
        await _wait_until(
            lambda: (
                not (result := reader.read()).stale
                and _unique_visible_control(
                    result.snapshot,
                    config.controls.startup_continue_control_labels,
                )
                is not None
            ),
            args.timeout,
            "semantic Continue control",
            controller=controller,
            health_check=health_check,
        )
        await _click_semantic_control(
            controller,
            reader,
            config.controls.startup_continue_control_labels,
        )
        loaded = await _wait_for_loaded_or_semantic_control(
            reader,
            config.controls.startup_save_control_labels,
            timeout=args.timeout,
            controller=controller,
            health_check=health_check,
        )
        if not loaded:
            await _click_semantic_control(
                controller,
                reader,
                config.controls.startup_save_control_labels,
            )

        def game_loaded() -> bool:
            try:
                result = reader.read()
            except TelemetryReadError:
                return False
            return (
                not result.stale
                and result.snapshot.game.loaded
                and bool(result.snapshot.squad)
            )

        await _wait_until(
            game_loaded,
            args.timeout,
            "loaded player squad",
            controller=controller,
            health_check=health_check,
        )
        snapshot = reader.read().snapshot
        if snapshot.game.paused is False:
            await _execute_primitive(
                controller,
                KeyAction(key=config.controls.pause_key),
            )

        def game_paused() -> bool:
            try:
                result = reader.read()
            except TelemetryReadError:
                return False
            return (
                not result.stale
                and result.snapshot.game.loaded
                and bool(result.snapshot.squad)
                and result.snapshot.game.paused is True
            )

        await _wait_until(
            game_paused,
            args.timeout,
            "causally confirmed paused game",
            controller=controller,
            health_check=health_check,
        )
        await _observe_loaded_paused_health(
            reader,
            controller,
            duration_seconds=config.launch.post_load_health_seconds,
            health_check=health_check,
        )
    if monitor is not None:
        monitor.raise_if_new(force=True)


async def _launch(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("The live developer launcher must run with Windows Python.")
    config = load_config(args.config)
    display_controller = (
        DisplayTopologyController()
        if config.launch.external_display_only
        else None
    )
    monitor = GpuTdrMonitor() if config.launch.monitor_gpu_tdr else None
    try:
        controller = _controller(config)
        try:
            terminal_window_title = _terminal_window_title(controller)
        except (OSError, RuntimeError, ValueError):
            terminal_window_title = None
        _validate_launch_preconditions(
            config,
            terminal_window_title=terminal_window_title,
            resume_launcher=args.resume_launcher,
        )
        if args.resume_launcher:
            _validate_resumable_launcher_rect(controller.client_rect())
        if display_controller is not None:
            display_controller.validate_ready()
        if monitor is not None:
            monitor.start()
    except (FileNotFoundError, LaunchFailed, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 4
    if args.preflight_only:
        print(
            "Launch preflight passed: all configured Steam, memory, graphics, "
            "display, and Windows GPU-event checks are ready."
        )
        return 0

    display_context: AbstractContextManager[None] = (
        external_display_lease(display_controller)
        if display_controller is not None
        else nullcontext()
    )
    try:
        with display_context:
            try:
                await _perform_launch(args, config, controller, monitor)
            except LaunchInterrupted as exc:
                safe_state = await _ensure_interrupted_safe_state(
                    controller,
                    _telemetry_read(config),
                    pause_key=config.controls.pause_key,
                    timeout_seconds=min(2.0, args.timeout),
                )
                print(f"{exc} Terminal safety: {safe_state}.", file=sys.stderr)
                return 3
    except (
        DisplayLeaseError,
        FileNotFoundError,
        GpuTdrDetected,
        LaunchFailed,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 4

    print("Kenshi launched" + (", loaded, and paused." if args.continue_game else "."))
    return 0


_CRASH_LOG_NAMES = (
    "RE_Kenshi_log.txt",
    "kenshi.log",
    "kenshi_info.log",
    "save.log",
    "MyGUI.log",
    "Havok.log",
    "FileIOLog.txt",
    "settings.cfg",
    "kenshi.cfg",
    "RE_Kenshi.ini",
)


def _crash_artifact_record(path: Path, *, source: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "name": path.name,
        "source": str(source),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "sha256": digest.hexdigest(),
    }


def _collect_crash_evidence(
    config: AppConfig,
    controller: InputController,
    *,
    terminal_window_title: str,
    captured_at: datetime | None = None,
    gpu_tdr_events: tuple[GpuTdrEvent, ...] | None = None,
    gpu_event_error: str | None = None,
) -> Path:
    observed_at = captured_at or datetime.now(UTC)
    stamp = observed_at.strftime("%Y%m%dT%H%M%S.%fZ")
    evidence_dir = config.paths.runs_dir / "crashes" / stamp
    evidence_dir.mkdir(parents=True, exist_ok=False)
    game_dir = _kenshi_settings_path().parent
    sources = [
        path
        for name in _CRASH_LOG_NAMES
        if (path := game_dir / name).is_file()
    ]
    crash_dumps = [path for path in game_dir.glob("crashDump*.zip") if path.is_file()]
    if crash_dumps:
        sources.append(max(crash_dumps, key=lambda path: path.stat().st_mtime_ns))
    telemetry_sources = [
        config.telemetry.file,
        config.telemetry.file.parent / "plugin_status.json",
    ]
    sources.extend(path for path in telemetry_sources if path.is_file())

    artifacts: list[dict[str, object]] = []
    copied_names: set[str] = set()
    for source in sources:
        if source.name in copied_names:
            continue
        destination = evidence_dir / source.name
        shutil.copy2(source, destination)
        artifacts.append(_crash_artifact_record(destination, source=source))
        copied_names.add(source.name)

    warnings: list[str] = []
    if gpu_tdr_events is not None:
        gpu_events_path = evidence_dir / "windows_gpu_events.json"
        gpu_events_path.write_text(
            json.dumps(
                [event.as_json() for event in gpu_tdr_events],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        artifacts.append(
            _crash_artifact_record(gpu_events_path, source=gpu_events_path)
        )
    if gpu_event_error is not None:
        warnings.append(f"Windows GPU-event query failed ({gpu_event_error}).")
    try:
        frame = WindowCapture(
            controller,
            evidence_dir,
            image_format=config.capture.image_format,
            jpeg_quality=config.capture.jpeg_quality,
        ).capture(0)
        artifacts.append(_crash_artifact_record(frame.path, source=frame.path))
    except (OSError, RuntimeError, ValueError) as exc:
        warnings.append(f"Crash frame capture failed ({type(exc).__name__}: {exc}).")

    manifest = {
        "captured_at": observed_at.isoformat(),
        "terminal_window_title": terminal_window_title,
        "artifacts": artifacts,
        "warnings": warnings,
    }
    temporary = evidence_dir / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(evidence_dir / "manifest.json")
    return evidence_dir


async def _dismiss_crash_reporter(
    controller: InputController,
    *,
    timeout_seconds: float,
) -> None:
    title = _terminal_window_title(controller)
    if title is None:
        raise LaunchFailed("No terminal Kenshi crash window is visible; no input was sent.")
    await _execute_primitive(controller, HotkeyAction(keys=["alt", "f4"]))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            visible_titles = controller.visible_window_titles()
        except (OSError, RuntimeError, ValueError):
            visible_titles = []
        if title not in visible_titles:
            return
        await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    raise LaunchFailed(
        f"The terminal window {title!r} did not close before timeout."
    )


async def _dismiss_crash_session(
    probe: InputController,
    *,
    timeout_seconds: float,
    controller_for_title: Callable[[str], InputController],
    process_names: Callable[[], set[str]] = _running_process_names,
) -> tuple[str, ...]:
    deadline = time.monotonic() + timeout_seconds
    dismissed: list[str] = []
    while time.monotonic() < deadline:
        try:
            title = _terminal_window_title(probe)
        except (OSError, RuntimeError, ValueError):
            title = None
        if title is None:
            if "kenshi_x64.exe" not in process_names():
                return tuple(dismissed)
            await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            continue
        if title in dismissed:
            raise LaunchFailed(
                f"Terminal window {title!r} reappeared during bounded crash recovery."
            )
        if len(dismissed) >= 4:
            raise LaunchFailed(
                "Crash recovery exceeded four distinct terminal windows."
            )
        remaining = deadline - time.monotonic()
        await _dismiss_crash_reporter(
            controller_for_title(title),
            timeout_seconds=remaining,
        )
        dismissed.append(title)
    raise LaunchFailed(
        "The archived crash session did not exit cleanly before timeout; no "
        "force-termination was attempted."
    )


async def _crash(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("The crash recovery command must run with Windows Python.")
    config = load_config(args.config)
    probe = _controller(config)
    try:
        title = _terminal_window_title(probe)
        if title is None:
            raise LaunchFailed(
                "No terminal Kenshi crash window is visible; no evidence or input "
                "was produced."
            )
        try:
            gpu_tdr_events = query_windows_gpu_tdr_events()
            gpu_event_error = None
        except (OSError, RuntimeError, ValueError) as exc:
            gpu_tdr_events = None
            gpu_event_error = f"{type(exc).__name__}: {exc}"
        evidence_dir = _collect_crash_evidence(
            config,
            probe,
            terminal_window_title=title,
            gpu_tdr_events=gpu_tdr_events,
            gpu_event_error=gpu_event_error,
        )
        print(f"Crash evidence archived at {evidence_dir}", flush=True)
        if not args.dismiss:
            return 0
        dismissed = await _dismiss_crash_session(
            probe,
            timeout_seconds=args.timeout,
            controller_for_title=lambda current_title: _controller(
                config,
                window_title_contains=current_title,
            ),
        )
        print(
            "Crash session dismissed after evidence archival: "
            + ", ".join(repr(current_title) for current_title in dismissed)
        )
        return 0
    except LaunchInterrupted as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 4


def _graphics(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("The live graphics command must run with Windows Python.")
    config = load_config(args.config)
    try:
        names = _running_process_names()
        if "kenshi_x64.exe" in names:
            raise LaunchFailed(
                "Kenshi is running; graphics settings were not read or changed."
            )
        profile = _configured_graphics_profile(config)
        settings_path = _kenshi_settings_path()
        renderer_path = _kenshi_renderer_path()
        if args.graphics_action == "apply":
            result = apply_graphics_profile(
                settings_path,
                profile,
                renderer_path=renderer_path,
            )
            if result.changed:
                assert result.backup_paths
                print(
                    f"Installed graphics profile {profile.profile_id!r}; "
                    "backups: "
                    + ", ".join(str(path) for path in result.backup_paths)
                )
            else:
                print(f"Graphics profile {profile.profile_id!r} already matches exactly.")
            return 0

        verification = verify_graphics_profile(
            settings_path,
            profile,
            renderer_path=renderer_path,
        )
        if verification.matches:
            print(f"Graphics profile {profile.profile_id!r} matches exactly.")
            return 0
        print(
            f"Graphics profile {profile.profile_id!r} does not match: "
            f"{_format_graphics_mismatches(verification.mismatches)}",
            file=sys.stderr,
        )
        return 5
    except (FileNotFoundError, LaunchFailed, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 5


def _shot(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    controller = _controller(config)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    label = "".join(
        character for character in args.label if character.isalnum() or character in "-_"
    )
    run_dir = config.paths.runs_dir / "dev-shots" / f"{stamp}-{label or 'shot'}"
    frame = WindowCapture(
        controller,
        run_dir,
        image_format=config.capture.image_format,
        jpeg_quality=config.capture.jpeg_quality,
    ).capture(1)
    print(frame.path)
    return 0


def _telemetry(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = _telemetry_read(config).read()
    snapshot = result.snapshot
    selected = next((character for character in snapshot.squad if character.selected), None)
    barman = next((entity for entity in snapshot.nearby_entities if entity.name == "Barman"), None)
    payload = {
        "sequence": snapshot.sequence,
        "age_seconds": round(result.age_seconds, 3),
        "stale": result.stale,
        "loaded": snapshot.game.loaded,
        "paused": snapshot.game.paused,
        "screen": snapshot.ui.active_screen,
        "money": snapshot.game.money,
        "active_shop_trader_count": snapshot.active_shop_trader_count,
        "native_control": snapshot.native_control.model_dump(mode="json"),
        "selected": selected.model_dump(mode="json") if selected else None,
        "barman": barman.model_dump(mode="json") if barman else None,
    }
    print(json.dumps(payload, indent=2))
    return 1 if result.stale else 0


def _journey_argv(args: argparse.Namespace, run_id: str) -> list[str]:
    """Build the `run` argv from journey options.

    Every gate is a faithful passthrough of an existing `run` flag; the `run`
    command still enforces that live, native-assisted, and continuous-live
    execution each require their own acknowledgement. Journey never invents or
    relaxes a gate.
    """

    argv = [
        "run",
        "--config",
        args.config,
        "--mode",
        "live",
        "--planner",
        args.planner,
        "--steps",
        str(args.steps),
        "--run-id",
        run_id,
    ]
    if args.objective:
        argv.extend(["--objective", args.objective])
    if args.planner == "subprocess":
        if not args.planner_script:
            raise SystemExit(
                "--planner subprocess requires --planner-script."
            )
        script = Path(args.planner_script).expanduser().resolve()
        if not script.is_file():
            raise SystemExit(f"Subprocess planner script does not exist: {script}")
        command_args = [
            sys.executable,
            str(script),
            *args.planner_arg,
        ]
        argv.extend(f"--command-arg={value}" for value in command_args)
    elif args.planner_script or args.planner_arg:
        raise SystemExit(
            "--planner-script and --planner-arg require --planner subprocess."
        )
    if getattr(args, "continuous", False):
        argv.extend(["--planning-mode", "continuous"])
    if args.execute:
        argv.append("--execute-live-actions")
    if args.native_assisted:
        argv.append("--acknowledge-native-assisted-control")
    if getattr(args, "acknowledge_continuous_live", False):
        argv.append("--acknowledge-continuous-live")
    if args.exclusive:
        argv.append("--exclusive-input-session")
    return argv


def _journey(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    argv = _journey_argv(args, run_id)
    event_log = config.paths.runs_dir / run_id / "events.jsonl"
    display_controller: DisplayTopologyController | None = None
    monitor: GpuTdrMonitor | None = None
    try:
        if args.execute and os.name == "nt":
            if config.launch.external_display_only:
                display_controller = DisplayTopologyController()
                display_controller.validate_ready()
            if config.launch.monitor_gpu_tdr:
                monitor = GpuTdrMonitor()
                monitor.start()
    except (DisplayLeaseError, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 4

    display_context: AbstractContextManager[None] = (
        external_display_lease(display_controller)
        if display_controller is not None
        else nullcontext()
    )
    result: int | None = None
    try:
        with display_context:
            overlay: subprocess.Popen[bytes] | None = None
            if (
                args.execute
                and config.safety.automatic_takeover_enabled
                and args.ownership_overlay
            ):
                overlay = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "kenshi_agent",
                        "overlay",
                        "--log",
                        str(event_log),
                        "--title",
                        "Kenshi Control Ownership",
                        "--layout",
                        "companion",
                        "--auto-close-seconds",
                        "30",
                    ],
                    cwd=Path.cwd(),
                )
            try:
                result = agent_main(argv)
                if monitor is not None:
                    monitor.raise_if_new(force=True)
            finally:
                if (
                    (result is None or result != 0)
                    and overlay is not None
                    and overlay.poll() is None
                ):
                    overlay.terminate()
    except (
        DisplayLeaseError,
        GpuTdrDetected,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 4
    assert result is not None
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./dev", description="Live Kenshi development console.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    launch = subparsers.add_parser("launch", help="Launch RE_Kenshi through the launcher.")
    launch.add_argument("--config", required=True)
    launch.add_argument("--timeout", type=float, default=60.0)
    launch.add_argument("--no-continue", dest="continue_game", action="store_false")
    launch.add_argument(
        "--resume-launcher",
        action="store_true",
        help=(
            "Resume one already-running small RE_Kenshi pre-game launcher "
            "without starting a second process."
        ),
    )
    launch.add_argument(
        "--preflight-only",
        action="store_true",
        help="Check Steam, memory, and graphics state without launching Kenshi.",
    )
    launch.set_defaults(continue_game=True)

    graphics = subparsers.add_parser(
        "graphics",
        help="Verify or reversibly install the configured Kenshi graphics profile.",
    )
    graphics.add_argument("--config", required=True)
    graphics.add_argument("graphics_action", choices=["verify", "apply"])

    shot = subparsers.add_parser("shot", help="Capture the current Kenshi client.")
    shot.add_argument("--config", required=True)
    shot.add_argument("--label", default="shot")

    telemetry = subparsers.add_parser("telemetry", help="Print a concise live-state snapshot.")
    telemetry.add_argument("--config", required=True)

    crash = subparsers.add_parser(
        "crash",
        help="Archive a visible terminal crash before optional reporter dismissal.",
    )
    crash.add_argument("--config", required=True)
    crash.add_argument(
        "--dismiss",
        action="store_true",
        help="After archival, explicitly close the unsent crash report with Alt+F4.",
    )
    crash.add_argument("--timeout", type=float, default=10.0)

    journey = subparsers.add_parser("journey", help="Run an ad-hoc agent objective.")
    journey.add_argument("--config", required=True)
    journey.add_argument("--objective")
    journey.add_argument(
        "--planner",
        choices=["openai", "openrouter", "subprocess"],
        default="openai",
    )
    journey.add_argument(
        "--planner-script",
        help=(
            "Repository-relative Python planner script used with "
            "--planner subprocess."
        ),
    )
    journey.add_argument(
        "--planner-arg",
        action="append",
        default=[],
        help=(
            "Exact argument for --planner-script. Repeat it; values beginning "
            "with '-' use --planner-arg=VALUE."
        ),
    )
    journey.add_argument("--steps", type=int, default=8)
    journey.add_argument("--run-id")
    journey.add_argument(
        "--continuous",
        action="store_true",
        help="Run the continuous scheduler instead of single-step.",
    )
    journey.add_argument("--execute", action="store_true")
    journey.add_argument(
        "--native-assisted",
        action="store_true",
        help="Acknowledge execution through configured native-assisted command bridges.",
    )
    journey.add_argument(
        "--acknowledge-continuous-live",
        action="store_true",
        help=(
            "Required in addition to --continuous and the normal live gates before "
            "an enabled continuous-live policy may execute."
        ),
    )
    journey.add_argument("--exclusive", action="store_true")
    journey.add_argument(
        "--ownership-overlay",
        action="store_true",
        help="Open the visible human/agent ownership and countdown window.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "launch":
        return asyncio.run(_launch(args))
    if args.command == "graphics":
        return _graphics(args)
    if args.command == "shot":
        return _shot(args)
    if args.command == "telemetry":
        return _telemetry(args)
    if args.command == "crash":
        return asyncio.run(_crash(args))
    if args.command == "journey":
        return _journey(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
