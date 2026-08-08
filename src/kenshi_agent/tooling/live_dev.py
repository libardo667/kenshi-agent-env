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
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
    nullcontext,
)
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Literal

from ..application import main as application_main
from ..config import AppConfig, ControlsConfig, load_config
from ..control.base import InputController, PrimitiveInputAction, WindowRect
from ..control.calibration import validate_expected_client_size
from ..control.capture import WindowCapture
from ..control.win32 import Win32InputController
from ..control_ownership import (
    ControlOwnershipEvent,
    ControlOwnershipEventType,
    ControlOwnershipMachine,
    ControlOwnershipState,
)
from ..core.observation import Observation
from ..core.operation import (
    ClickAction,
    HotkeyAction,
    KeyAction,
)
from ..core.scenario import MANAGED_SAVE_NAME, ScenarioFixtureManifest
from ..core.telemetry import (
    Disposition,
    NormalizedPointerBounds,
    ScenarioIdentity,
    TelemetrySnapshot,
    VisibleUIControl,
    window_close_point,
)
from ..display_lease import (
    DisplayLeaseError,
    DisplayTopologyController,
    external_display_lease,
)
from ..final_safe_state import FinalSafeStateStatus, ensure_final_safe_state
from ..scenario_validation import (
    ScenarioFixtureError,
    attest_loaded_scenario,
    validate_current_scenario,
)
from ..telemetry import TelemetryRead, TelemetryReader, TelemetryReadError
from ..terminal_state import terminal_window_title
from .affordance_watch import (
    AffordanceMenu,
    current_menu,
    menu_payload,
    observation_from_snapshot,
    render_discovery,
    render_menu,
)
from .authored_starts import (
    AuthoredGameStart,
    install_authored_starts,
    load_authored_starts_bundle,
    resolve_authored_game_start,
    verify_authored_game_start_snapshot,
    verify_installed_authored_starts,
)
from .dev_cli import LIVE_CONFIG
from .dev_cli import build_parser as build_dev_parser
from .dev_tui import run_from_dev_args
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
from .scenario_fixtures import (
    capture_scenario_fixture,
    current_attestation_path,
    load_scenario_attestation,
    load_scenario_fixture,
    load_verified_scenario_attestation,
    restore_scenario_fixture,
    verify_staged_scenario,
    write_scenario_attestation,
)


def agent_main(argv: list[str]) -> int:
    """Enter the same application root as the public console adapter."""

    return application_main(
        argv,
        scenario_proof_loader=load_verified_scenario_attestation,
    )


class LaunchInterrupted(RuntimeError):
    pass


class LaunchFailed(RuntimeError):
    pass


class LowPhysicalMemory(LaunchFailed):
    def __init__(self, available_mib: int, threshold_mib: int) -> None:
        self.available_mib = available_mib
        self.threshold_mib = threshold_mib
        super().__init__(
            f"Only {available_mib} MiB physical memory is available; this configuration "
            f"requires at least {threshold_mib} MiB before launch."
        )


def _config_path(args: argparse.Namespace) -> str | Path:
    if getattr(args, "config", None) is not None:
        return str(args.config)
    return Path(__file__).resolve().parents[3] / LIVE_CONFIG


def _windows_runtime() -> bool:
    return os.name == "nt"


class _StartupInputGate:
    """Yield launcher input, then visibly reclaim it after a quiet countdown."""

    def __init__(
        self,
        controller: InputController,
        *,
        emergency_stop_key: str,
        quiet_seconds: float,
        countdown_seconds: float,
        poll_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.controller = controller
        self.emergency_stop_key = emergency_stop_key
        self.poll_seconds = poll_seconds
        self.monotonic = monotonic
        self.sleep = sleep
        self.machine = ControlOwnershipMachine(
            quiet_seconds=quiet_seconds,
            countdown_seconds=countdown_seconds,
        )

    def _check_emergency_stop(self) -> None:
        if self.machine.state is ControlOwnershipState.DISARMED:
            raise LaunchInterrupted(
                "Kenshi startup automation remains disarmed by the emergency stop."
            )
        if not self.controller.emergency_stop_pressed(self.emergency_stop_key):
            return
        self.machine.disarm(
            reason=(
                f"Emergency stop {self.emergency_stop_key!r} pressed; launcher "
                "takeover is permanently disarmed."
            )
        )
        raise LaunchInterrupted(
            f"Kenshi startup automation was permanently disarmed by "
            f"{self.emergency_stop_key!r}."
        )

    @staticmethod
    def _announce(events: tuple[ControlOwnershipEvent, ...]) -> None:
        for event in events:
            if event.event_type is ControlOwnershipEventType.COUNTDOWN:
                print(
                    "Launcher takeover in "
                    f"{event.seconds_remaining}s; move the mouse or press a key "
                    "to retain control."
                )
            elif event.event_type is ControlOwnershipEventType.READY:
                print(
                    "Launcher takeover countdown complete; revalidating current "
                    "startup state."
                )
            elif (
                event.event_type is ControlOwnershipEventType.CHANGED
                and event.state is ControlOwnershipState.HUMAN_CONTROL
            ):
                print(
                    "Human input detected; launcher yielded control and cancelled "
                    "its pending input."
                )

    def _observe_human_input(self) -> bool:
        self._check_emergency_stop()
        detected = self.controller.continuous_user_input_detected()
        if not detected:
            return False
        now = self.monotonic()
        events = (
            self.machine.yield_to_human(
                now,
                reason="Human input preempted Kenshi startup automation.",
            )
            if self.machine.state is ControlOwnershipState.AGENT_ACTIVE
            else self.machine.advance(now, human_input=True)
        )
        self._announce(events)
        return True

    async def wait_for_turn(self) -> float:
        """Wait without consuming startup timeout; F12 never auto-recovers."""

        started = self.monotonic()
        if (
            not self._observe_human_input()
            and self.machine.state is ControlOwnershipState.AGENT_ACTIVE
        ):
            return 0.0
        while self.machine.state is not ControlOwnershipState.AGENT_ACTIVE:
            await self.sleep(self.poll_seconds)
            self._check_emergency_stop()
            human_input = self.controller.continuous_user_input_detected()
            events = self.machine.advance(
                self.monotonic(),
                human_input=human_input,
            )
            self._announce(events)
        return self.monotonic() - started

    async def _yield_input_lease(self) -> AsyncIterator[None]:
        """Reacquire when input lands after the polite lease begins."""

        while True:
            await self.wait_for_turn()
            async with self.controller.input_lease():
                if self._observe_human_input():
                    continue
                yield
                return

    def input_lease(self) -> AbstractAsyncContextManager[None]:
        """Expose the lease without hiding its decisions behind a decorator."""

        return asynccontextmanager(self._yield_input_lease)()


def _validate_safe_close_snapshot(
    payload: object,
    *,
    max_age_seconds: float,
    now: datetime | None = None,
) -> Literal["loaded_paused", "title"]:
    """Require current controller-idle evidence before closing the game window."""

    if not isinstance(payload, dict):
        raise LaunchFailed("Safe close requires a telemetry JSON object.")
    captured_raw = payload.get("captured_at")
    if not isinstance(captured_raw, str):
        raise LaunchFailed("Safe close requires a telemetry captured_at timestamp.")
    try:
        captured_at = datetime.fromisoformat(
            captured_raw.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise LaunchFailed(
            "Safe close requires a valid telemetry captured_at timestamp."
        ) from exc
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    observed_at = now or datetime.now(UTC)
    age_seconds = (observed_at - captured_at).total_seconds()
    if age_seconds < -1.0 or age_seconds > max_age_seconds:
        raise LaunchFailed(
            f"Safe close requires telemetry no older than {max_age_seconds:g}s; "
            f"the snapshot age is {age_seconds:.3f}s."
        )

    game = payload.get("game")
    ui = payload.get("ui")
    native_control = payload.get("native_control")
    if not isinstance(game, dict) or not isinstance(ui, dict):
        raise LaunchFailed("Safe close requires game and UI telemetry.")
    if not isinstance(native_control, dict):
        raise LaunchFailed("Safe close requires native-control telemetry.")
    if native_control.get("active_command_id") not in (None, ""):
        raise LaunchFailed("Safe close refuses while a native command is active.")
    if game.get("loaded") is True and game.get("paused") is True:
        if ui.get("modal_open") is not False or ui.get("dialogue_open") is not False:
            raise LaunchFailed(
                "Safe close refuses a loaded world while a modal or dialogue is open."
            )
        return "loaded_paused"
    if (
        game.get("loaded") is False
        and ui.get("active_screen") == "title"
        and ui.get("modal_open") in (None, True, False)
        and ui.get("dialogue_open") in (None, False)
    ):
        return "title"
    raise LaunchFailed(
        "Safe close requires a fresh loaded paused world or the title screen."
    )


def _safe_close_inventory_window(
    snapshot: TelemetrySnapshot,
) -> tuple[str, NormalizedPointerBounds]:
    """Resolve one exact non-commercial inventory window safe to dismiss.

    Shutdown may clean up an inventory layout it can fully explain, but it does
    not gain generic modal-closing authority. The exact contextual source and
    selected-character destination are the only recognized owners.
    """

    refusal = (
        "Safe close refuses a loaded world while a modal or dialogue is open; "
        "automatic inventory cleanup"
    )
    ui = snapshot.ui
    if (
        snapshot.game.loaded is not True
        or snapshot.game.paused is not True
        or ui.modal_open is not True
        or ui.dialogue_open is not False
    ):
        raise LaunchFailed(f"{refusal} requires a paused inventory modal.")
    if snapshot.native_control.active_command_id is not None:
        raise LaunchFailed("Safe close refuses while a native command is active.")
    if ui.active_screen not in {"inventory", "trade"}:
        raise LaunchFailed(f"{refusal} does not recognize this screen.")
    if ui.context_menu_open is True:
        raise LaunchFailed(f"{refusal} refuses an open context menu.")
    if ui.open_inventory_windows not in {1, 2}:
        raise LaunchFailed(f"{refusal} requires one or two exact inventory windows.")
    if (
        "ui.visible_controls" not in snapshot.capabilities
        or ui.visible_controls_complete is not True
        or ui.visible_controls is None
    ):
        raise LaunchFailed(f"{refusal} requires complete visible-control telemetry.")

    selected = [
        character
        for character in snapshot.squad
        if character.selected
        and character.id == ui.selected_character_id
        and character.id in ui.selected_character_ids
    ]
    if len(selected) != 1 or not selected[0].name:
        raise LaunchFailed(f"{refusal} requires one exact selected character.")
    destination_caption = selected[0].name

    source_caption: str | None = None
    if ui.context_inventory_target_id is not None:
        targets = [
            target
            for target in snapshot.world_targets
            if target.id == ui.context_inventory_target_id
            and target.kind == "natural_resource"
        ]
        if len(targets) != 1 or not targets[0].name:
            raise LaunchFailed(f"{refusal} cannot resolve the contextual source.")
        source_caption = targets[0].name
    elif ui.open_inventory_windows == 2:
        # A real trade can arrive with active_screen="inventory" when Kenshi's
        # transient inventoryWindowTrader pointer is empty. Resolve authority
        # from the stronger evidence instead: exactly one observed non-hostile
        # registered shop owner has an exact named inventory root beside ours.
        shop_captions: list[str] = []
        for entity in snapshot.nearby_entities:
            if (
                entity.shop_inventory_owner is not True
                or entity.disposition
                not in {Disposition.FRIENDLY, Disposition.NEUTRAL}
                or not entity.name
            ):
                continue
            normalized = _normalize_control_label(entity.name)
            roots = [
                control
                for control in ui.visible_controls
                if control.role == "text"
                and _normalize_control_label(control.window) == normalized
                and _normalize_control_label(control.label) == normalized
            ]
            if len(roots) == 1:
                shop_captions.append(entity.name)
            elif len(roots) > 1:
                raise LaunchFailed(
                    f"{refusal} found duplicate roots for {entity.name!r}."
                )
        if len(shop_captions) != 1:
            raise LaunchFailed(
                f"{refusal} cannot resolve one exact shop-owner window."
            )
        source_caption = shop_captions[0]

    captions = [
        caption
        for caption in (source_caption, destination_caption)
        if caption is not None
    ]
    normalized_captions = {
        _normalize_control_label(caption): caption for caption in captions
    }
    if len(normalized_captions) != len(captions):
        raise LaunchFailed(f"{refusal} found ambiguous inventory owners.")

    resolved: dict[str, NormalizedPointerBounds] = {}
    for normalized, caption in normalized_captions.items():
        roots = [
            control
            for control in ui.visible_controls
            if control.role == "text"
            and _normalize_control_label(control.window) == normalized
            and _normalize_control_label(control.label) == normalized
        ]
        if len(roots) > 1:
            raise LaunchFailed(
                f"{refusal} found duplicate roots for {caption!r}."
            )
        if len(roots) == 1:
            resolved[normalized] = roots[0].bounds

    if source_caption is not None:
        source_key = _normalize_control_label(source_caption)
        if source_key not in resolved:
            raise LaunchFailed(f"{refusal} cannot see the exact source window.")
    else:
        destination_key = _normalize_control_label(destination_caption)
        if destination_key not in resolved:
            raise LaunchFailed(
                f"{refusal} cannot see the selected character's inventory."
            )
    if len(resolved) != ui.open_inventory_windows:
        raise LaunchFailed(
            f"{refusal} found an unexplained or missing inventory window."
        )

    chosen_caption = source_caption or destination_caption
    return (
        chosen_caption,
        resolved[_normalize_control_label(chosen_caption)].model_copy(deep=True),
    )


async def _dismiss_safe_close_inventories(
    controller: InputController,
    telemetry: TelemetryReader,
    current: TelemetryRead,
    *,
    timeout_seconds: float,
) -> TelemetryRead:
    """Dismiss at most the exact source and destination, with causal proof."""

    deadline = time.monotonic() + timeout_seconds
    for _ in range(2):
        baseline = current.snapshot
        if baseline.ui.modal_open is False:
            return current
        caption, bounds = _safe_close_inventory_window(baseline)
        open_count = baseline.ui.open_inventory_windows
        if open_count is None:
            raise LaunchFailed("Safe close inventory count became unknown.")
        action = ClickAction(
            x=window_close_point(bounds)[0],
            y=window_close_point(bounds)[1],
            hold_seconds=MYGUI_CLICK_HOLD_SECONDS,
        )

        _abort_if_human_input(controller)
        async with controller.input_lease():
            _abort_if_human_input(controller)
            in_lease = telemetry.read()
            if in_lease.stale:
                raise LaunchFailed(
                    "Safe close inventory telemetry became stale inside the input lease."
                )
            current_caption, current_bounds = _safe_close_inventory_window(
                in_lease.snapshot
            )
            current_action = ClickAction(
                x=window_close_point(current_bounds)[0],
                y=window_close_point(current_bounds)[1],
                hold_seconds=MYGUI_CLICK_HOLD_SECONDS,
            )
            if current_caption != caption or current_action != action:
                raise LaunchFailed(
                    "Safe close inventory layout changed inside the input lease; "
                    "no pointer input was sent."
                )
            receipt = await controller.execute(action)
        if not receipt.executed:
            raise LaunchFailed(
                receipt.message or f"Safe close could not dismiss {caption!r}."
            )

        while time.monotonic() < deadline:
            candidate = telemetry.read()
            if (
                not candidate.stale
                and candidate.snapshot.sequence > in_lease.snapshot.sequence
                and candidate.snapshot.game.loaded is True
                and candidate.snapshot.game.paused is True
                and candidate.snapshot.native_control.active_command_id is None
                and candidate.snapshot.ui.dialogue_open is False
                and candidate.snapshot.ui.open_inventory_windows is not None
                and candidate.snapshot.ui.open_inventory_windows < open_count
            ):
                current = candidate
                break
            await asyncio.sleep(0.25)
        else:
            raise LaunchFailed(
                f"Safe close could not causally confirm {caption!r} closed."
            )
    if current.snapshot.ui.modal_open is False:
        return current
    raise LaunchFailed("Safe close inventory cleanup exceeded two exact windows.")


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


async def _wait_for_startup_input(
    controller: InputController,
    input_gate: _StartupInputGate | None,
) -> float:
    if input_gate is not None:
        return await input_gate.wait_for_turn()
    _abort_if_human_input(controller)
    return 0.0


def _terminal_window_title(controller: InputController) -> str | None:
    return terminal_window_title(controller)


async def _wait_until(
    predicate: Callable[[], bool],
    timeout: float,
    description: str,
    *,
    controller: InputController,
    health_check: Callable[[], None] | None = None,
    input_gate: _StartupInputGate | None = None,
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
        deadline += await _wait_for_startup_input(controller, input_gate)
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


def _re_kenshi_executable() -> Path | None:
    """RE_Kenshi's patched launcher inside the Kenshi install, if present."""

    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if not program_files_x86:
        return None
    return (
        Path(program_files_x86)
        / "Steam"
        / "steamapps"
        / "common"
        / "Kenshi"
        / "RE_Kenshi"
        / "Kenshi_x64.exe"
    )


def _shortcut() -> Path:
    """What to start to bring up the RE_Kenshi launcher.

    A desktop `.lnk` used to be the only accepted answer, which made the whole
    live path depend on a hand-made shortcut that nothing in this repository
    creates, documents, or checks. Deleting a cluttered desktop icon then broke
    launching, after preflight had already reported everything ready and a
    display lease had been taken. The installed executable is the durable
    target; the shortcuts stay ahead of it so an operator's own launcher
    arrangement still wins.
    """

    override = os.environ.get("KENSHI_AGENT_SHORTCUT")
    candidates = [
        Path(override) if override else None,
        Path.home() / "OneDrive" / "Desktop" / "RE_Kenshi.lnk",
        Path.home() / "Desktop" / "RE_Kenshi.lnk",
        _re_kenshi_executable(),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No RE_Kenshi launcher was found. Expected a desktop RE_Kenshi.lnk, "
        "the installed RE_Kenshi/Kenshi_x64.exe, or KENSHI_AGENT_SHORTCUT set "
        "to a launcher path."
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


def _local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if not value:
        raise FileNotFoundError("Windows LOCALAPPDATA is unavailable.")
    return Path(value)


def _kenshi_save_root() -> Path:
    override = os.environ.get("KENSHI_AGENT_SAVE_ROOT")
    return Path(override) if override else _local_app_data() / "kenshi" / "save"


def _kenshi_root() -> Path:
    override = os.environ.get("KENSHI_AGENT_KENSHI_ROOT")
    return Path(override) if override else _kenshi_settings_path().parent


def _scenario_store() -> Path:
    override = os.environ.get("KENSHI_AGENT_SCENARIO_STORE")
    return (
        Path(override)
        if override
        else _local_app_data() / "KenshiAgent" / "scenarios"
    )


def _named_save(root: Path, name: str) -> Path:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", name)
    ):
        raise ScenarioFixtureError(
            "Save names must be one plain Windows directory name."
        )
    return root / name


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


def _recover_low_launch_memory(
    *,
    threshold_mib: int,
    distribution: str,
    available_memory_mib: Callable[[], int] = _available_physical_memory_mib,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    settle_timeout_seconds: float = 45.0,
    poll_seconds: float = 1.0,
) -> tuple[int, int]:
    """Drop this WSL VM's file cache once, then verify Windows page reporting."""

    before = available_memory_mib()
    if before >= threshold_mib:
        return before, before
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", distribution):
        raise LaunchFailed(
            "Low-memory recovery received an invalid WSL distribution name; "
            "no root command was attempted."
        )
    command = [
        "wsl.exe",
        "--distribution",
        distribution,
        "--user",
        "root",
        "--exec",
        "sh",
        "-c",
        "sync; echo 3 > /proc/sys/vm/drop_caches",
    ]
    try:
        result = run_command(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaunchFailed(
            "WSL cache reclaim could not run; no launch input was sent: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip()
        if len(detail) > 500:
            detail = detail[:497] + "..."
        raise LaunchFailed(
            "WSL cache reclaim failed"
            + (f": {detail}" if detail else ".")
            + " No launch input was sent."
        )

    deadline = monotonic() + settle_timeout_seconds
    after = available_memory_mib()
    while after < threshold_mib:
        remaining = deadline - monotonic()
        if remaining <= 0.0:
            break
        sleep(min(poll_seconds, remaining))
        after = available_memory_mib()
    if after < threshold_mib:
        raise LaunchFailed(
            "WSL cache reclaim completed, but Windows available physical memory "
            f"only changed from {before} MiB to {after} MiB; the live configuration still "
            f"requires {threshold_mib} MiB before launch. No launch input was sent."
        )
    return before, after


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
    allow_existing_client: bool = False,
) -> None:
    if terminal_window_title is not None:
        raise LaunchFailed(
            f"Kenshi is in terminal state {terminal_window_title!r}. Run './dev recover' "
            "to archive evidence, or './dev recover --dismiss-crash' to archive it "
            "before closing the unsent report."
        )
    names = process_names if process_names is not None else _running_process_names()
    if (
        "kenshi_x64.exe" in names
        and not resume_launcher
        and not allow_existing_client
    ):
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
            raise LowPhysicalMemory(available, threshold)

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
                f"{details}. Run './dev setup graphics' while Kenshi is stopped."
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
    *,
    input_gate: _StartupInputGate | None = None,
) -> None:
    if input_gate is None:
        _abort_if_human_input(controller)
        async with controller.input_lease():
            _abort_if_human_input(controller)
            receipt = await controller.execute(action)
    else:
        async with input_gate.input_lease():
            receipt = await controller.execute(action)
    if not receipt.executed:
        raise RuntimeError(receipt.message)


async def _click(
    controller: InputController,
    x: float,
    y: float,
    *,
    input_gate: _StartupInputGate | None = None,
) -> None:
    await _execute_primitive(
        controller,
        ClickAction(x=x, y=y, hold_seconds=MYGUI_CLICK_HOLD_SECONDS),
        input_gate=input_gate,
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


def _fresh_semantic_control_visible(
    reader: TelemetryReader,
    labels: list[str],
) -> bool:
    result = reader.read()
    return (
        not result.stale
        and _unique_visible_control(result.snapshot, labels) is not None
    )


def _game_start_carousel_state(
    snapshot: TelemetrySnapshot,
) -> tuple[VisibleUIControl, str] | None:
    """Resolve the carousel's left arrow and current label by their geometry."""

    if "ui.visible_controls" not in snapshot.capabilities:
        return None
    controls = snapshot.ui.visible_controls or []
    left_matches = [
        control
        for control in controls
        if control.role == "button"
        and _normalize_control_label(control.label).endswith("_leftbutton")
    ]
    right_matches = [
        control
        for control in controls
        if control.role == "button"
        and _normalize_control_label(control.label).endswith("_rightbutton")
    ]
    if len(left_matches) != 1 or len(right_matches) != 1:
        return None
    left = left_matches[0]
    right = right_matches[0]
    left_x, _ = left.center
    right_x, _ = right.center
    if left_x >= right_x:
        return None

    min_arrow_y = min(left.bounds.min_y, right.bounds.min_y)
    max_arrow_y = max(left.bounds.max_y, right.bounds.max_y)
    labels = [
        control
        for control in controls
        if control.role == "text"
        and left_x < control.center[0] < right_x
        and min_arrow_y <= control.center[1] <= max_arrow_y
    ]
    if len(labels) != 1:
        return None
    label = " ".join(labels[0].label.split())
    if not label:
        return None
    return left, label


async def _wait_for_game_start_carousel(
    reader: TelemetryReader,
    *,
    timeout: float,
    controller: InputController,
    description: str,
    health_check: Callable[[], None] | None = None,
    previous_label: str | None = None,
    minimum_sequence: int | None = None,
    input_gate: _StartupInputGate | None = None,
) -> tuple[VisibleUIControl, str, int]:
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
        deadline += await _wait_for_startup_input(controller, input_gate)
        try:
            result = reader.read()
        except TelemetryReadError:
            await asyncio.sleep(0.1)
            continue
        state = _game_start_carousel_state(result.snapshot)
        changed = (
            previous_label is None
            or (
                minimum_sequence is not None
                and result.snapshot.sequence > minimum_sequence
                and state is not None
                and _normalize_control_label(state[1]) != previous_label
            )
        )
        if not result.stale and state is not None and changed:
            return state[0], state[1], result.snapshot.sequence
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {description}.")


async def _click_game_start_left(
    controller: InputController,
    reader: TelemetryReader,
    *,
    expected_left: VisibleUIControl,
    expected_label: str,
    input_gate: _StartupInputGate | None = None,
) -> int:
    """Click left only while the exact observed carousel state remains current."""

    expected_normalized = _normalize_control_label(expected_label)
    await _wait_for_startup_input(controller, input_gate)
    initial = reader.read()
    initial_state = (
        None
        if initial.stale
        else _game_start_carousel_state(initial.snapshot)
    )
    if (
        initial_state is None
        or initial_state[0] != expected_left
        or _normalize_control_label(initial_state[1]) != expected_normalized
    ):
        raise RuntimeError(
            "Authored Game Start carousel changed before the input lease; "
            "no pointer input was sent."
        )

    lease = (
        input_gate.input_lease()
        if input_gate is not None
        else controller.input_lease()
    )
    async with lease:
        if input_gate is None:
            _abort_if_human_input(controller)
        current = reader.read()
        current_state = (
            None
            if current.stale
            else _game_start_carousel_state(current.snapshot)
        )
        if (
            current_state is None
            or current_state[0] != expected_left
            or _normalize_control_label(current_state[1]) != expected_normalized
        ):
            raise RuntimeError(
                "Authored Game Start carousel changed inside the input lease; "
                "no pointer input was sent."
            )
        x, y = current_state[0].center
        receipt = await controller.execute(
            ClickAction(x=x, y=y, hold_seconds=MYGUI_CLICK_HOLD_SECONDS)
        )
    if not receipt.executed:
        raise RuntimeError(receipt.message)
    return current.snapshot.sequence


async def _click_semantic_control(
    controller: InputController,
    reader: TelemetryReader,
    labels: list[str],
    *,
    input_gate: _StartupInputGate | None = None,
) -> None:
    await _wait_for_startup_input(controller, input_gate)
    initial = reader.read()
    if initial.stale:
        raise RuntimeError("Semantic startup control requires fresh telemetry.")
    control = _unique_visible_control(initial.snapshot, labels)
    if control is None:
        raise RuntimeError(
            "Expected exactly one visible startup control matching "
            f"{labels!r} on telemetry sequence {initial.snapshot.sequence}."
        )

    lease = (
        input_gate.input_lease()
        if input_gate is not None
        else controller.input_lease()
    )
    async with lease:
        if input_gate is None:
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


async def _open_exact_scenario_save(
    controller: InputController,
    reader: TelemetryReader,
    *,
    load_control_labels: list[str],
    save_control_label: str,
    timeout: float,
    health_check: Callable[[], None] | None = None,
    input_gate: _StartupInputGate | None = None,
) -> None:
    """Open the load screen and one exact save row; never auto-Continue."""

    await _wait_until(
        lambda: (
            not (result := reader.read()).stale
            and _unique_visible_control(
                result.snapshot,
                load_control_labels,
            )
            is not None
        ),
        timeout,
        "semantic Load Game control",
        controller=controller,
        health_check=health_check,
        input_gate=input_gate,
    )
    await _click_semantic_control(
        controller,
        reader,
        load_control_labels,
        input_gate=input_gate,
    )
    await _wait_until(
        lambda: (
            not (result := reader.read()).stale
            and _unique_visible_control(
                result.snapshot,
                [save_control_label],
            )
            is not None
        ),
        timeout,
        f"exact scenario save control {save_control_label!r}",
        controller=controller,
        health_check=health_check,
        input_gate=input_gate,
    )
    await _click_semantic_control(
        controller,
        reader,
        [save_control_label],
        input_gate=input_gate,
    )


async def _open_exact_authored_game_start(
    controller: InputController,
    reader: TelemetryReader,
    *,
    new_game_control_labels: list[str],
    game_start_label: str,
    begin_control_labels: list[str],
    confirm_control_labels: list[str],
    warning_confirm_control_labels: list[str],
    max_carousel_steps: int,
    timeout: float,
    health_check: Callable[[], None] | None = None,
    input_gate: _StartupInputGate | None = None,
) -> None:
    """Select one bundled Game Start through exact semantic controls."""

    await _wait_until(
        partial(_fresh_semantic_control_visible, reader, new_game_control_labels),
        timeout,
        "semantic New Game control",
        controller=controller,
        health_check=health_check,
        input_gate=input_gate,
    )
    await _click_semantic_control(
        controller,
        reader,
        new_game_control_labels,
        input_gate=input_gate,
    )

    description = f"exact authored Game Start {game_start_label!r} carousel"
    left, current_label, sequence = await _wait_for_game_start_carousel(
        reader,
        timeout=timeout,
        controller=controller,
        description=description,
        health_check=health_check,
        input_gate=input_gate,
    )
    target = _normalize_control_label(game_start_label)
    seen = {_normalize_control_label(current_label)}
    steps = 0
    while _normalize_control_label(current_label) != target:
        if steps >= max_carousel_steps:
            raise LaunchFailed(
                f"Authored Game Start {game_start_label!r} was not found within "
                f"{max_carousel_steps} carousel transitions."
            )
        click_sequence = await _click_game_start_left(
            controller,
            reader,
            expected_left=left,
            expected_label=current_label,
            input_gate=input_gate,
        )
        left, current_label, sequence = await _wait_for_game_start_carousel(
            reader,
            timeout=timeout,
            controller=controller,
            description=(
                f"Game Start carousel to advance from {current_label!r}"
            ),
            health_check=health_check,
            previous_label=_normalize_control_label(current_label),
            minimum_sequence=max(sequence, click_sequence),
            input_gate=input_gate,
        )
        steps += 1
        normalized = _normalize_control_label(current_label)
        if normalized in seen:
            raise LaunchFailed(
                f"Authored Game Start carousel cycled after {steps} transitions "
                f"without reaching {game_start_label!r}."
            )
        seen.add(normalized)

    for labels, stage_description in (
        (begin_control_labels, "semantic Begin control"),
        (confirm_control_labels, "semantic character Confirm control"),
    ):
        await _wait_until(
            partial(_fresh_semantic_control_visible, reader, labels),
            timeout,
            stage_description,
            controller=controller,
            health_check=health_check,
            input_gate=input_gate,
        )
        await _click_semantic_control(
            controller,
            reader,
            labels,
            input_gate=input_gate,
        )
    loaded = await _wait_for_loaded_or_semantic_control(
        reader,
        warning_confirm_control_labels,
        timeout=timeout,
        controller=controller,
        health_check=health_check,
        input_gate=input_gate,
    )
    if not loaded:
        await _click_semantic_control(
            controller,
            reader,
            warning_confirm_control_labels,
            input_gate=input_gate,
        )


async def _wait_for_loaded_or_semantic_control(
    reader: TelemetryReader,
    labels: list[str],
    *,
    timeout: float,
    controller: InputController,
    health_check: Callable[[], None] | None = None,
    input_gate: _StartupInputGate | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health_check is not None:
            health_check()
        deadline += await _wait_for_startup_input(controller, input_gate)
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
    input_gate: _StartupInputGate | None = None,
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
        deadline += await _wait_for_startup_input(controller, input_gate)
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
    input_gate: _StartupInputGate,
    scenario_manifest: ScenarioFixtureManifest | None = None,
    game_start: AuthoredGameStart | None = None,
) -> None:
    health_check = monitor.raise_if_new if monitor is not None else None
    _disable_re_kenshi_startup_panel(_re_kenshi_settings_path())
    launched_at = datetime.now(UTC)
    if not args.resume_launcher:
        target = _shortcut()
        if target.suffix.lower() == ".lnk":
            # A shortcut carries its own working directory; overriding it would
            # discard whatever the operator configured.
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            # Kenshi resolves data, mods and RE_Kenshi.ini against the install
            # root, not against the patched executable's own folder.
            os.startfile(target, cwd=str(target.parent.parent))  # type: ignore[attr-defined]
    else:
        print("Resuming the existing verified RE_Kenshi pre-game launcher.")
    await _wait_until(
        lambda: controller.client_rect().width > 0,
        args.timeout,
        "Kenshi launcher",
        controller=controller,
        health_check=health_check,
        input_gate=input_gate,
    )
    launcher_rect = controller.client_rect()
    if launcher_rect.width < 1200:
        await _execute_primitive(
            controller,
            KeyAction(key="enter"),
            input_gate=input_gate,
        )

    status_path = config.telemetry.file.parent / "plugin_status.json"
    await _wait_until(
        lambda: _plugin_ready(status_path, launched_at),
        args.timeout,
        "fresh telemetry plugin startup",
        controller=controller,
        health_check=health_check,
        input_gate=input_gate,
    )
    await _wait_until(
        lambda: controller.client_rect().width >= 1200,
        args.timeout,
        "full-size Kenshi window",
        controller=controller,
        health_check=health_check,
        input_gate=input_gate,
    )
    await asyncio.sleep(2.0)
    if monitor is not None:
        monitor.raise_if_new(force=True)
    await input_gate.wait_for_turn()

    if args.continue_game:
        reader = _telemetry_read(config)
        if scenario_manifest is not None:
            await _open_exact_scenario_save(
                controller,
                reader,
                load_control_labels=config.controls.startup_load_control_labels,
                save_control_label=scenario_manifest.managed_save_name,
                timeout=args.timeout,
                health_check=health_check,
                input_gate=input_gate,
            )
        elif game_start is not None:
            await _open_exact_authored_game_start(
                controller,
                reader,
                new_game_control_labels=config.controls.startup_new_game_control_labels,
                game_start_label=game_start.label,
                begin_control_labels=config.controls.startup_begin_control_labels,
                confirm_control_labels=config.controls.startup_confirm_control_labels,
                warning_confirm_control_labels=(
                    config.controls.startup_warning_confirm_control_labels
                ),
                max_carousel_steps=(
                    config.controls.startup_game_start_max_carousel_steps
                ),
                timeout=args.timeout,
                health_check=health_check,
                input_gate=input_gate,
            )
        else:
            save_control_labels = config.controls.startup_save_control_labels
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
                input_gate=input_gate,
            )
            await _click_semantic_control(
                controller,
                reader,
                config.controls.startup_continue_control_labels,
                input_gate=input_gate,
            )
            loaded = await _wait_for_loaded_or_semantic_control(
                reader,
                save_control_labels,
                timeout=args.timeout,
                controller=controller,
                health_check=health_check,
                input_gate=input_gate,
            )
            if not loaded:
                await _click_semantic_control(
                    controller,
                    reader,
                    save_control_labels,
                    input_gate=input_gate,
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
            input_gate=input_gate,
        )
        snapshot = reader.read().snapshot
        if snapshot.game.paused is False:
            await _execute_primitive(
                controller,
                KeyAction(key=config.controls.pause_key),
                input_gate=input_gate,
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
            input_gate=input_gate,
        )
        await _observe_loaded_paused_health(
            reader,
            controller,
            duration_seconds=config.launch.post_load_health_seconds,
            health_check=health_check,
            input_gate=input_gate,
        )
        if game_start is not None:
            result = reader.read()
            if result.stale:
                raise LaunchFailed(
                    "Authored Game Start proof requires fresh post-load telemetry."
                )
            verify_authored_game_start_snapshot(result.snapshot, game_start)
        if scenario_manifest is not None:
            result = reader.read()
            if result.stale:
                raise LaunchFailed(
                    "Scenario attestation requires fresh post-load telemetry."
                )
            attestation = attest_loaded_scenario(
                scenario_manifest,
                result.snapshot,
            )
            write_scenario_attestation(
                current_attestation_path(_scenario_store()),
                attestation,
            )
    if monitor is not None:
        monitor.raise_if_new(force=True)


@contextmanager
def _retained_display_context() -> Iterator[None]:
    print(
        "Display mode active: internal panel and external 1920x1080 display "
        "left on in the current topology."
    )
    try:
        yield
    finally:
        print("Display mode released: display topology was not changed.")


def _prepared_display_context(
    config: AppConfig,
    args: argparse.Namespace,
    *,
    enabled: bool = True,
) -> AbstractContextManager[None]:
    """Validate one display authority and return its bounded context."""

    if not enabled or not config.launch.require_dual_display_topology:
        return nullcontext()
    controller = DisplayTopologyController()
    controller.validate_ready()
    if getattr(args, "focus_display", False):
        return external_display_lease(controller)
    return _retained_display_context()


async def _launch(
    args: argparse.Namespace,
    *,
    manage_display_lease: bool = True,
) -> int:
    if os.name != "nt":
        raise SystemExit("The live developer launcher must run with Windows Python.")
    config = load_config(_config_path(args))
    monitor = GpuTdrMonitor() if config.launch.monitor_gpu_tdr else None
    scenario_manifest: ScenarioFixtureManifest | None = None
    game_start: AuthoredGameStart | None = None
    display_context: AbstractContextManager[None] = nullcontext()
    try:
        if args.scenario is not None:
            if not args.continue_game:
                raise LaunchFailed("--scenario cannot be combined with --no-continue.")
            scenario_manifest = verify_staged_scenario(
                _scenario_store(),
                args.scenario,
                _kenshi_save_root(),
            )
        if args.game_start is not None:
            if not args.continue_game:
                raise LaunchFailed(
                    "--game-start cannot be combined with --no-continue."
                )
            authored_bundle = load_authored_starts_bundle()
            game_start = resolve_authored_game_start(
                authored_bundle,
                args.game_start,
            )
            verify_installed_authored_starts(authored_bundle, _kenshi_root())
        controller = _controller(config)
        input_gate = _StartupInputGate(
            controller,
            emergency_stop_key=config.safety.emergency_stop_key,
            quiet_seconds=config.safety.human_control_quiet_seconds,
            countdown_seconds=config.safety.takeover_countdown_seconds,
            poll_seconds=config.safety.takeover_poll_seconds,
        )
        try:
            terminal_window_title = _terminal_window_title(controller)
        except (OSError, RuntimeError, ValueError):
            terminal_window_title = None
        try:
            _validate_launch_preconditions(
                config,
                terminal_window_title=terminal_window_title,
                resume_launcher=args.resume_launcher,
                allow_existing_client=args.preflight_only,
            )
        except LowPhysicalMemory as low_memory:
            if (
                args.preflight_only
                or not config.launch.reclaim_wsl_cache_on_low_memory
            ):
                raise
            distribution = os.environ.get("WSL_DISTRO_NAME", "").strip()
            if not distribution:
                raise LaunchFailed(
                    "Low-memory recovery is enabled, but ./dev did not forward "
                    "WSL_DISTRO_NAME; no root command or launch input was sent."
                ) from low_memory
            before, after = _recover_low_launch_memory(
                threshold_mib=config.launch.min_free_physical_memory_mib,
                distribution=distribution,
                settle_timeout_seconds=(
                    config.launch.wsl_cache_reclaim_settle_timeout_seconds
                ),
                poll_seconds=config.launch.wsl_cache_reclaim_poll_seconds,
            )
            print(
                "WSL cache reclaim restored Windows launch headroom: "
                f"{before} MiB -> {after} MiB available."
            )
            # Re-run the complete authority check. Recovery does not waive
            # Steam, process, graphics, display, or memory requirements, and a
            # concurrent drop below the threshold still fails closed.
            _validate_launch_preconditions(
                config,
                terminal_window_title=terminal_window_title,
                resume_launcher=args.resume_launcher,
                allow_existing_client=False,
            )
        if args.resume_launcher:
            _validate_resumable_launcher_rect(controller.client_rect())
        display_context = _prepared_display_context(
            config,
            args,
            enabled=manage_display_lease,
        )
        if monitor is not None:
            monitor.start()
    except (FileNotFoundError, LaunchFailed, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 4
    if args.preflight_only:
        scenario_suffix = (
            f" Scenario {scenario_manifest.scenario.scenario_id!r} is staged exactly."
            if scenario_manifest is not None
            else ""
        )
        start_suffix = (
            f" Authored Game Start {game_start.start_id!r} is installed exactly."
            if game_start is not None
            else ""
        )
        print(
            "Doctor passed: Steam, memory, graphics, display, and Windows "
            "GPU-event checks are ready."
            + scenario_suffix
            + start_suffix
        )
        return 0

    current_attestation_path(_scenario_store()).unlink(missing_ok=True)

    try:
        with display_context:
            try:
                await _perform_launch(
                    args,
                    config,
                    controller,
                    monitor,
                    input_gate,
                    scenario_manifest,
                    game_start,
                )
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

    scenario_suffix = (
        f" Scenario {scenario_manifest.scenario.scenario_id!r} was fixture-attested."
        if scenario_manifest is not None
        else ""
    )
    start_suffix = (
        f" Authored Game Start {game_start.start_id!r} was telemetry-proven."
        if game_start is not None
        else ""
    )
    print(
        "Kenshi launched"
        + (", loaded, and paused." if args.continue_game else ".")
        + scenario_suffix
        + start_suffix
    )
    return 0


async def _close_kenshi_safely(
    config: AppConfig,
    controller: Win32InputController,
    telemetry: TelemetryReader,
    *,
    timeout_seconds: float,
    process_names: Callable[[], set[str]],
) -> Literal["loaded_paused", "title"]:
    """Own pause-before-close and refuse to close through unresolved UI state."""

    if "kenshi_x64.exe" not in process_names():
        raise LaunchFailed("Kenshi is not running.")

    initial = telemetry.read()
    if initial.stale:
        raise LaunchFailed("Safe close requires fresh telemetry.")
    if initial.snapshot.game.loaded and initial.snapshot.game.paused is not True:
        outcome = await ensure_final_safe_state(
            controller=controller,
            telemetry=telemetry,
            pause_primitives=[KeyAction(key=config.controls.pause_key)],
            timeout_seconds=timeout_seconds,
            input_authorized=True,
        )
        if outcome.status is not FinalSafeStateStatus.PAUSE_CONFIRMED:
            raise LaunchFailed(
                "Safe close could not causally confirm a pause; "
                f"WM_CLOSE was not sent. {outcome.reason}"
            )

    current = telemetry.read()
    if current.stale:
        raise LaunchFailed("Safe close requires fresh telemetry.")
    if (
        current.snapshot.game.loaded is True
        and current.snapshot.game.paused is True
        and current.snapshot.ui.modal_open is True
    ):
        current = await _dismiss_safe_close_inventories(
            controller,
            telemetry,
            current,
            timeout_seconds=timeout_seconds,
        )
    safe_state = _validate_safe_close_snapshot(
        current.snapshot.model_dump(mode="json"),
        max_age_seconds=config.telemetry.max_age_seconds,
    )
    controller.request_close()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if "kenshi_x64.exe" not in process_names():
            return safe_state
        await asyncio.sleep(0.25)
    raise LaunchFailed(
        "Kenshi did not close before timeout; no force-termination was attempted."
    )


async def _recover_kenshi_safe_state(
    config: AppConfig,
    controller: Win32InputController,
    telemetry: TelemetryReader,
    *,
    timeout_seconds: float,
    process_names: Callable[[], set[str]],
) -> Literal["loaded_paused", "title", "not_running"]:
    """Recover a running journey to an idle state without closing Kenshi."""

    if "kenshi_x64.exe" not in process_names():
        return "not_running"

    initial = telemetry.read()
    if initial.stale:
        raise LaunchFailed("Interrupted recovery requires fresh telemetry.")
    if initial.snapshot.game.loaded and initial.snapshot.game.paused is not True:
        outcome = await ensure_final_safe_state(
            controller=controller,
            telemetry=telemetry,
            pause_primitives=[KeyAction(key=config.controls.pause_key)],
            timeout_seconds=timeout_seconds,
            input_authorized=True,
        )
        if outcome.status is not FinalSafeStateStatus.PAUSE_CONFIRMED:
            raise LaunchFailed(
                "Interrupted recovery could not causally confirm a pause. "
                f"{outcome.reason}"
            )

    current = telemetry.read()
    if current.stale:
        raise LaunchFailed("Interrupted recovery requires fresh telemetry.")
    active_command_id = current.snapshot.native_control.active_command_id
    if (
        current.snapshot.game.loaded is True
        and current.snapshot.game.paused is True
        and active_command_id is not None
    ):
        current = await _wait_for_paused_native_command_terminal(
            telemetry,
            current,
            command_id=active_command_id,
            timeout_seconds=timeout_seconds,
        )
    if (
        current.snapshot.game.loaded is True
        and current.snapshot.game.paused is True
        and current.snapshot.ui.modal_open is True
    ):
        current = await _dismiss_safe_close_inventories(
            controller,
            telemetry,
            current,
            timeout_seconds=timeout_seconds,
        )
    return _validate_safe_close_snapshot(
        current.snapshot.model_dump(mode="json"),
        max_age_seconds=config.telemetry.max_age_seconds,
    )


async def _wait_for_paused_native_command_terminal(
    telemetry: TelemetryReader,
    current: TelemetryRead,
    *,
    command_id: str,
    timeout_seconds: float,
) -> TelemetryRead:
    """Wait for the plug-in to causally cancel one command after a safety pause."""

    initial_sequence = current.snapshot.sequence
    deadline = time.monotonic() + timeout_seconds
    while True:
        if (
            not current.stale
            and current.snapshot.sequence > initial_sequence
            and current.snapshot.game.loaded is True
            and current.snapshot.game.paused is True
            and current.snapshot.native_control.active_command_id is None
        ):
            return current
        active_command_id = current.snapshot.native_control.active_command_id
        if active_command_id not in {None, command_id}:
            raise LaunchFailed(
                "Interrupted recovery observed a different native command after "
                "the safety pause."
            )
        if (
            current.snapshot.game.loaded is not True
            or current.snapshot.game.paused is not True
        ):
            raise LaunchFailed(
                "Interrupted recovery lost the confirmed paused loaded world "
                "while waiting for native command cancellation."
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LaunchFailed(
                "Interrupted recovery timed out waiting for the paused native "
                "command to reach a terminal acknowledgement."
            )
        await asyncio.sleep(min(0.05, remaining))
        try:
            current = telemetry.read()
        except TelemetryReadError:
            continue


async def _close(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("The live developer stop command must run with Windows Python.")
    config = load_config(_config_path(args))
    try:
        safe_state = await _close_kenshi_safely(
            config,
            _controller(config),
            _telemetry_read(config),
            timeout_seconds=args.timeout,
            process_names=_running_process_names,
        )
        if safe_state == "title":
            print("Kenshi closed from a fresh idle title screen.")
        else:
            print("Kenshi closed from a fresh paused idle state.")
        return 0
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        LaunchFailed,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 4


async def _recover(args: argparse.Namespace) -> int:
    if not _windows_runtime():
        raise SystemExit("The live developer recovery command must run with Windows Python.")
    config = load_config(_config_path(args))
    terminal_window_title = _terminal_window_title(_controller(config))
    if terminal_window_title is not None:
        crash_args = argparse.Namespace(
            config=_config_path(args),
            dismiss=args.dismiss_crash,
            timeout=args.timeout,
        )
        crash_result = await _crash(crash_args)
        if crash_result != 0:
            return crash_result
        if not args.dismiss_crash:
            print(
                "Terminal crash evidence was archived. Re-run './dev recover "
                "--dismiss-crash' to explicitly close the unsent reporter.",
                file=sys.stderr,
            )
            return 3
    failures: list[str] = []
    game_state: Literal["loaded_paused", "title", "not_running"] | None = None
    display_changed: bool | None = None

    try:
        game_state = await _recover_kenshi_safe_state(
            config,
            _controller(config),
            _telemetry_read(config),
            timeout_seconds=args.timeout,
            process_names=_running_process_names,
        )
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        LaunchFailed,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        failures.append(f"game recovery failed: {exc}")

    try:
        _, display_changed = DisplayTopologyController().restore_if_stranded()
    except (DisplayLeaseError, OSError, RuntimeError, ValueError) as exc:
        failures.append(f"display recovery failed: {exc}")

    if failures:
        print("; ".join(failures), file=sys.stderr)
        return 4

    assert game_state is not None and display_changed is not None
    game_message = {
        "loaded_paused": "Kenshi is paused with no unresolved modal.",
        "title": "Kenshi is idle at the title screen.",
        "not_running": "Kenshi is not running.",
    }[game_state]
    display_message = (
        "The stranded display lease was restored."
        if display_changed
        else "The display lease was already released."
    )
    print(f"Recovery complete: {game_message} {display_message}")
    return 0


async def _doctor(args: argparse.Namespace) -> int:
    """Run read-only launch checks, archiving terminal crash evidence on sight."""

    if not _windows_runtime():
        raise SystemExit("The live developer doctor must run with Windows Python.")
    config = load_config(_config_path(args))
    if _terminal_window_title(_controller(config)) is None:
        return await _launch(args)

    crash_result = await _crash(
        argparse.Namespace(
            config=_config_path(args),
            dismiss=False,
            timeout=args.timeout,
        )
    )
    if crash_result == 0:
        print(
            "Doctor found a terminal crash. Evidence was archived; the unsent "
            "reporter was not dismissed.",
            file=sys.stderr,
        )
        return 4
    return crash_result


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
    config = load_config(_config_path(args))
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
    config = load_config(_config_path(args))
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


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _snapshot(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args))
    controller = _controller(config)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    label = "".join(
        character for character in args.label if character.isalnum() or character in "-_"
    )
    evidence_dir = (
        config.paths.runs_dir
        / "dev-snapshots"
        / f"{stamp}-{label or 'snapshot'}"
    )
    frame = WindowCapture(
        controller,
        evidence_dir,
        image_format=config.capture.image_format,
        jpeg_quality=config.capture.jpeg_quality,
    ).capture(1)
    telemetry = _telemetry_read(config).read()
    _write_json_atomic(
        evidence_dir / "telemetry.json",
        {
            "source": str(telemetry.path),
            "age_seconds": telemetry.age_seconds,
            "stale": telemetry.stale,
            "snapshot": telemetry.snapshot.model_dump(mode="json"),
        },
    )
    _write_json_atomic(
        evidence_dir / "manifest.json",
        {
            "captured_at": datetime.now(UTC).isoformat(),
            "label": label or "snapshot",
            "frame": str(frame.path),
            "telemetry": "telemetry.json",
            "telemetry_sequence": telemetry.snapshot.sequence,
        },
    )
    print(evidence_dir)
    return 0


def _telemetry_payload(result: TelemetryRead) -> dict[str, object]:
    snapshot = result.snapshot
    selected = next((character for character in snapshot.squad if character.selected), None)
    nearest_entities = sorted(
        snapshot.nearby_entities,
        key=lambda entity: (
            entity.distance is None,
            entity.distance if entity.distance is not None else 0.0,
        ),
    )[:12]
    context_targets = [
        target
        for target in snapshot.world_targets
        if target.context_actions
    ]
    nearest_targets = sorted(
        snapshot.world_targets,
        key=lambda target: target.distance,
    )[:12]
    return {
        "sequence": snapshot.sequence,
        "age_seconds": round(result.age_seconds, 3),
        "stale": result.stale,
        "loaded": snapshot.game.loaded,
        "paused": snapshot.game.paused,
        "screen": snapshot.ui.active_screen,
        "money": snapshot.game.money,
        "active_shop_trader_count": snapshot.active_shop_trader_count,
        # The trade state, because every question asked of this digest during
        # the transfer work had to be answered by opening the raw telemetry file
        # instead: is a shop trade open, whose windows are up, and can they
        # reach each other. A digest that omits the subject of the current work
        # is a digest you read once and then stop trusting.
        "shop_trader_name": snapshot.ui.shop_trader_name,
        "open_inventories": [
            {
                "owner_name": inventory.owner_name,
                "owner_id": inventory.owner_id,
                "owner_kind": inventory.owner_kind,
                "player_owned": inventory.player_owned,
                "within_trade_range": inventory.within_trade_range,
                "money": inventory.money,
                "sections": [
                    {
                        "name": section.name,
                        "equipped": section.equipped,
                        "items": len(section.items),
                    }
                    for section in inventory.sections
                ],
            }
            for inventory in snapshot.ui.open_inventories
        ],
        "native_control": snapshot.native_control.model_dump(mode="json"),
        "selected": selected.model_dump(mode="json") if selected else None,
        "nearby_entity_count": len(snapshot.nearby_entities),
        "nearest_nearby_entities": [
            entity.model_dump(mode="json", exclude_none=True)
            for entity in nearest_entities
        ],
        "known_map_destinations": [
            destination.model_dump(mode="json", exclude_none=True)
            for destination in snapshot.known_map_destinations
        ],
        "world_target_count": len(snapshot.world_targets),
        "context_targets": [
            target.model_dump(mode="json")
            for target in context_targets
        ],
        "nearest_world_targets": [
            target.model_dump(mode="json")
            for target in nearest_targets
        ],
        "warnings": list(snapshot.warnings),
    }


def _telemetry(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args))
    reader = _telemetry_read(config)
    if not args.watch:
        result = reader.read()
        print(json.dumps(_telemetry_payload(result), indent=2))
        return 1 if result.stale else 0
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero.")

    status = 0
    try:
        while True:
            result = reader.read()
            status = 1 if result.stale else 0
            print(
                json.dumps(_telemetry_payload(result), separators=(",", ":")),
                flush=True,
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return status


def _affordance_observation(result: TelemetryRead) -> Observation:
    return observation_from_snapshot(result.snapshot, stale=result.stale)


def _emit_affordance_menu(
    menu: AffordanceMenu,
    args: argparse.Namespace,
    *,
    capture: Path | None,
    observation: Observation | None = None,
) -> None:
    payload = menu_payload(menu)
    if args.json:
        print(json.dumps(payload, separators=(",", ":")), flush=True)
    else:
        lines = render_menu(menu)
        if observation is not None:
            lines.extend(render_discovery(observation))
        print("\n".join(lines), flush=True)
    if capture is not None:
        capture.parent.mkdir(parents=True, exist_ok=True)
        with capture.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")


def _affordances(args: argparse.Namespace) -> int:
    """Show what the planner would currently be offered, without touching Kenshi.

    Enumeration is pure over one observation, so this acquires no input lease
    and emits nothing to the game. It is safe to run beside a live session that
    a person is driving by hand.
    """

    config = load_config(_config_path(args))
    reader = _telemetry_read(config)
    capture: Path | None = args.capture
    if not args.watch:
        result = reader.read()
        observation = _affordance_observation(result)
        _emit_affordance_menu(
            current_menu(observation),
            args,
            capture=capture,
            observation=observation,
        )
        return 1 if result.stale else 0
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero.")

    status = 0
    previous: str | None = None
    try:
        while True:
            result = reader.read()
            status = 1 if result.stale else 0
            observation = _affordance_observation(result)
            menu = current_menu(observation)
            fingerprint = menu.fingerprint()
            if fingerprint != previous:
                previous = fingerprint
                _emit_affordance_menu(
                    menu,
                    args,
                    capture=capture,
                    observation=observation,
                )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return status


def _scenario_identity_from_args(args: argparse.Namespace) -> ScenarioIdentity:
    return ScenarioIdentity(
        scenario_id=args.scenario_id,
        save_id=args.save_id,
        environment=args.environment,
        danger=args.danger,
        economy=args.economy,
        party=args.party,
        time_of_day=args.time_of_day,
    )


def _scenario_command(args: argparse.Namespace) -> int:
    try:
        if args.scenario_action == "list":
            store = _scenario_store()
            fixtures_root = store / "fixtures"
            manifests = (
                [
                    load_scenario_fixture(store, path.name)
                    for path in sorted(fixtures_root.iterdir())
                    if path.is_dir() and not path.name.startswith(".")
                ]
                if fixtures_root.is_dir()
                else []
            )
            print(
                json.dumps(
                    [
                        {
                            "scenario": manifest.scenario.model_dump(mode="json"),
                            "fixture_digest": manifest.fixture_digest,
                            "captured_at": manifest.captured_at.isoformat(),
                            "managed_save_name": manifest.managed_save_name,
                        }
                        for manifest in manifests
                    ],
                    indent=2,
                )
            )
            return 0
        running = _running_process_names()
        if {
            "kenshi_x64.exe",
            "forgotten construction set.exe",
        } & running:
            raise ScenarioFixtureError(
                "Kenshi and FCS must be closed before changing scenario artifacts "
                "or saves."
            )
        if args.scenario_action == "install-starts":
            bundle = load_authored_starts_bundle()
            install_result = install_authored_starts(
                bundle,
                _kenshi_root(),
            )
            verified_path = verify_installed_authored_starts(bundle, _kenshi_root())
            changes: list[str] = []
            if install_result.mod_changed:
                changes.append("installed exact mod bytes")
            if install_result.enabled_changed:
                changes.append("enabled the mod")
            summary = ", ".join(changes) if changes else "already exact"
            recovery = (
                f" Enabled-mod backup: {install_result.backup_path}."
                if install_result.backup_path is not None
                else ""
            )
            print(
                f"Authored Game Starts: {summary}; verified exact at "
                f"{verified_path}.{recovery}"
            )
            return 0
        if args.scenario_action == "capture":
            store = _scenario_store()
            scenario = _scenario_identity_from_args(args)
            source = _named_save(_kenshi_save_root(), args.source_save)
            manifest = capture_scenario_fixture(source, store, scenario)
            print(
                f"Captured {scenario.scenario_id!r} from {args.source_save!r} "
                f"as fixture {manifest.fixture_digest}."
            )
            return 0
        if args.scenario_action == "restore":
            store = _scenario_store()
            restore_result = restore_scenario_fixture(
                store,
                args.scenario_id,
                _kenshi_save_root(),
            )
            if restore_result.changed:
                recovery = (
                    " Prior managed state is recoverable at "
                    f"{restore_result.recovery_path}."
                    if restore_result.recovery_path is not None
                    else ""
                )
                print(
                    f"Restored {restore_result.scenario.scenario_id!r} into "
                    f"{MANAGED_SAVE_NAME!r}.{recovery}"
                )
            else:
                print(
                    f"Scenario {restore_result.scenario.scenario_id!r} is already restored "
                    f"exactly in {MANAGED_SAVE_NAME!r}."
                )
            return 0
        raise AssertionError(args.scenario_action)
    except (
        FileNotFoundError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 4


def _agent_argv(
    args: argparse.Namespace,
    run_id: str,
    *,
    scenario_attestation: Path | None = None,
) -> list[str]:
    """Translate one explicit dev control mode into the core run contract.

    The lower-level runner retains separate authority gates.  The dev surface
    makes them one comprehensible choice and expands that choice exactly here.
    """

    argv = [
        "run",
        "--config",
        str(_config_path(args)),
        "--mode",
        "live",
        "--run-id",
        run_id,
    ]
    if args.steps is not None:
        argv.extend(["--steps", str(args.steps)])
    if args.objective:
        argv.extend(["--objective", args.objective])
    if args.campaign:
        argv.extend(["--campaign", args.campaign])
    if args.prompt_file:
        argv.extend(["--prompt-file", str(args.prompt_file)])
    if args.advisor_corpus_file:
        argv.extend(["--advisor-corpus-file", str(args.advisor_corpus_file)])
    if scenario_attestation is not None:
        argv.extend(["--scenario-attestation", str(scenario_attestation)])
    argv.append("--tts")
    if args.control != "plan-only":
        argv.extend(
            [
                "--execute-live-actions",
                "--acknowledge-native-assisted-control",
                "--acknowledge-continuous-live",
            ]
        )
    if args.control == "live":
        argv.append("--exclusive-input-session")
    return argv


def _run_agent(
    args: argparse.Namespace,
    *,
    manage_display_lease: bool = True,
) -> int:
    config = load_config(_config_path(args))
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    scenario_attestation_path: Path | None = None
    try:
        if args.scenario is not None:
            store = _scenario_store()
            manifest = load_scenario_fixture(store, args.scenario)
            scenario_attestation_path = current_attestation_path(store)
            attestation = load_scenario_attestation(scenario_attestation_path)
            telemetry_result = _telemetry_read(config).read()
            if telemetry_result.stale:
                raise ScenarioFixtureError(
                    "The loaded scenario cannot be verified from stale telemetry."
                )
            validate_current_scenario(
                attestation,
                manifest,
                telemetry_result.snapshot,
            )
        argv = _agent_argv(
            args,
            run_id,
            scenario_attestation=scenario_attestation_path,
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 4
    event_log = config.paths.runs_dir / run_id / "events.jsonl"
    display_context: AbstractContextManager[None] = nullcontext()
    monitor: GpuTdrMonitor | None = None
    try:
        if manage_display_lease and args.control != "plan-only" and os.name == "nt":
            display_context = _prepared_display_context(config, args)
            if config.launch.monitor_gpu_tdr:
                monitor = GpuTdrMonitor()
                monitor.start()
    except (DisplayLeaseError, OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 4

    result: int | None = None
    try:
        with display_context:
            overlay: subprocess.Popen[bytes] | None = None
            if (
                args.control == "live"
                and config.safety.automatic_takeover_enabled
            ):
                overlay = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "kenshi_agent.tooling.overlay",
                        "--log",
                        str(event_log),
                        "--title",
                        "Kenshi Control Ownership",
                        "--layout",
                        "companion",
                        "--owner-pid",
                        str(os.getpid()),
                    ],
                    cwd=Path.cwd(),
                )
            try:
                result = agent_main(argv)
                if monitor is not None:
                    monitor.raise_if_new(force=True)
            finally:
                if overlay is not None:
                    _terminate_owned_process(overlay)
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


def _terminate_owned_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float = 1.0,
) -> None:
    """End one child process with a bounded terminate-to-kill fallback."""

    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)
    except OSError as exc:
        if process.poll() is None:
            print(
                f"Could not close the run-owned companion process: {exc}",
                file=sys.stderr,
            )


def _launch_and_run(args: argparse.Namespace) -> int:
    """Launch and run under one display lease."""

    try:
        config = load_config(_config_path(args))
        display_context = _prepared_display_context(config, args)
        with display_context:
            launch_result = asyncio.run(
                _launch(args, manage_display_lease=False)
            )
            if launch_result != 0:
                return launch_result
            return _run_agent(args, manage_display_lease=False)
    except (
        DisplayLeaseError,
        FileNotFoundError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 4


def _choose_run_path(
    *,
    process_names: set[str],
    telemetry: TelemetryRead | None,
    terminal_window_title: str | None,
) -> Literal["launch", "loaded"]:
    """Choose only between two proven run starts; ambiguity always fails closed."""

    if terminal_window_title is not None:
        raise LaunchFailed(
            f"Kenshi is in terminal state {terminal_window_title!r}; run "
            "'./dev recover' before starting an agent."
        )
    if "kenshi_x64.exe" not in process_names:
        return "launch"
    if telemetry is None:
        raise LaunchFailed(
            "Kenshi is running but no telemetry state was read; run './dev recover'."
        )
    if telemetry.stale:
        raise LaunchFailed(
            "Kenshi is running but telemetry is stale; run './dev recover'."
        )
    snapshot = telemetry.snapshot
    if not snapshot.game.loaded:
        raise LaunchFailed(
            "Kenshi is running without a loaded world; run './dev recover'."
        )
    if snapshot.native_control.active_command_id is not None:
        raise LaunchFailed(
            "Kenshi has an unresolved native command; run './dev recover'."
        )
    return "loaded"


def _run(args: argparse.Namespace) -> int:
    """Use one safe loaded world or perform a fresh launch before agent play."""

    try:
        config = load_config(_config_path(args))
        process_names = _running_process_names()
        controller = _controller(config)
        terminal_window_title = _terminal_window_title(controller)
        telemetry = (
            _telemetry_read(config).read()
            if "kenshi_x64.exe" in process_names
            else None
        )
        path = _choose_run_path(
            process_names=process_names,
            telemetry=telemetry,
            terminal_window_title=terminal_window_title,
        )
        if path == "loaded":
            if args.game_start is not None:
                raise LaunchFailed(
                    "--game-start requires a fresh client, but a world is already loaded; "
                    "run './dev stop' first."
                )
            return _run_agent(args)
        return _launch_and_run(args)
    except (
        FileNotFoundError,
        LaunchFailed,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 4


def build_parser() -> argparse.ArgumentParser:
    return build_dev_parser(include_transport=True)


def _setup(args: argparse.Namespace) -> int:
    if args.setup_action != "graphics":
        raise AssertionError(args.setup_action)
    args.graphics_action = "apply"
    return _graphics(args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return asyncio.run(_doctor(args))
    if args.command == "launch":
        return asyncio.run(_launch(args))
    if args.command == "run":
        return _run(args)
    if args.command == "tui":
        return run_from_dev_args(
            args,
            config_loader=load_config,
            run_command=_run,
        )
    if args.command == "telemetry":
        return _telemetry(args)
    if args.command == "affordances":
        return _affordances(args)
    if args.command == "snapshot":
        return _snapshot(args)
    if args.command == "recover":
        return asyncio.run(_recover(args))
    if args.command == "stop":
        return asyncio.run(_close(args))
    if args.command == "scenario":
        return _scenario_command(args)
    if args.command == "setup":
        return _setup(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
