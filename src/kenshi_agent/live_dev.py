from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .cli import main as agent_main
from .config import AppConfig, ControlsConfig, load_config
from .control.base import InputController, PrimitiveInputAction, WindowRect
from .control.calibration import validate_expected_client_size
from .control.capture import WindowCapture
from .control.win32 import Win32InputController
from .models import ClickAction, KeyAction, TelemetrySnapshot, VisibleUIControl
from .telemetry import TelemetryReader, TelemetryReadError


class LaunchInterrupted(RuntimeError):
    pass


class LaunchFailed(RuntimeError):
    pass


def _controller(config: AppConfig) -> Win32InputController:
    return Win32InputController(
        config.capture.window_title_contains,
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
    )


def _abort_if_human_input(controller: InputController) -> None:
    if controller.continuous_user_input_detected():
        raise LaunchInterrupted(
            "Kenshi startup automation stopped because human input was detected; "
            "all remaining startup clicks were permanently cancelled."
        )


async def _wait_until(
    predicate: Callable[[], bool],
    timeout: float,
    description: str,
    *,
    controller: InputController,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            title = controller.target_window_title()
        except (OSError, RuntimeError, ValueError):
            title = None
        if title is not None and "crash reporter" in title.casefold():
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
    await _execute_primitive(controller, ClickAction(x=x, y=y))


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
        receipt = await controller.execute(ClickAction(x=x, y=y))
    if not receipt.executed:
        raise RuntimeError(receipt.message)


async def _wait_for_loaded_or_semantic_control(
    reader: TelemetryReader,
    labels: list[str],
    *,
    timeout: float,
    controller: InputController,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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
    try:
        initial = reader.read()
    except TelemetryReadError as exc:
        return f"pause state unavailable; no cleanup input sent ({exc})"
    snapshot = initial.snapshot
    if initial.stale or not snapshot.game.loaded:
        return "game not freshly loaded; no cleanup input sent"
    if snapshot.game.paused is True:
        return f"already confirmed paused at telemetry sequence {snapshot.sequence}"
    if snapshot.game.paused is not False or "game.pause" not in snapshot.capabilities:
        return "pause state or capability unavailable; no cleanup input sent"

    try:
        async with controller.input_lease():
            receipt = await controller.execute_safety(KeyAction(key=pause_key))
    except Exception as exc:
        return f"single safety-pause attempt failed ({type(exc).__name__}: {exc})"
    if not receipt.executed:
        return f"single safety-pause attempt was not executed ({receipt.message})"

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            result = reader.read()
        except TelemetryReadError:
            await asyncio.sleep(0.05)
            continue
        current = result.snapshot
        if (
            not result.stale
            and current.sequence > snapshot.sequence
            and current.game.loaded
            and current.game.paused is True
            and "game.pause" in current.capabilities
        ):
            return f"confirmed paused at telemetry sequence {current.sequence}"
        await asyncio.sleep(0.05)
    return "single safety-pause attempt was not confirmed on a later telemetry sequence"


async def _launch(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("The live developer launcher must run with Windows Python.")
    config = load_config(args.config)
    controller = _controller(config)
    _disable_re_kenshi_startup_panel(_re_kenshi_settings_path())
    launched_at = datetime.now(UTC)
    os.startfile(_shortcut())  # type: ignore[attr-defined]

    try:
        await _wait_until(
            lambda: controller.client_rect().width > 0,
            args.timeout,
            "Kenshi launcher",
            controller=controller,
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
        )
        await _wait_until(
            lambda: controller.client_rect().width >= 1200,
            args.timeout,
            "full-size Kenshi window",
            controller=controller,
        )
        await asyncio.sleep(2.0)
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
            )
    except LaunchFailed as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except LaunchInterrupted as exc:
        safe_state = await _ensure_interrupted_safe_state(
            controller,
            _telemetry_read(config),
            pause_key=config.controls.pause_key,
            timeout_seconds=min(2.0, args.timeout),
        )
        print(f"{exc} Terminal safety: {safe_state}.", file=sys.stderr)
        return 3

    print("Kenshi launched" + (", loaded, and paused." if args.continue_game else "."))
    return 0


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


def _journey(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
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
    if args.execute:
        argv.append("--execute-live-actions")
    if args.native_assisted:
        argv.append("--acknowledge-native-assisted-control")
    if args.exclusive:
        argv.append("--exclusive-input-session")

    overlay: subprocess.Popen[bytes] | None = None
    if (
        args.execute
        and config.safety.automatic_takeover_enabled
        and not args.no_ownership_overlay
    ):
        event_log = config.paths.runs_dir / run_id / "events.jsonl"
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
                "--auto-close-seconds",
                "30",
            ],
            cwd=Path.cwd(),
        )
    result = agent_main(argv)
    if result != 0 and overlay is not None and overlay.poll() is None:
        overlay.terminate()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./dev", description="Live Kenshi development console.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    launch = subparsers.add_parser("launch", help="Launch RE_Kenshi through the launcher.")
    launch.add_argument("--config", required=True)
    launch.add_argument("--timeout", type=float, default=60.0)
    launch.add_argument("--no-continue", dest="continue_game", action="store_false")
    launch.set_defaults(continue_game=True)

    shot = subparsers.add_parser("shot", help="Capture the current Kenshi client.")
    shot.add_argument("--config", required=True)
    shot.add_argument("--label", default="shot")

    telemetry = subparsers.add_parser("telemetry", help="Print a concise live-state snapshot.")
    telemetry.add_argument("--config", required=True)

    journey = subparsers.add_parser("journey", help="Run an ad-hoc agent objective.")
    journey.add_argument("--config", required=True)
    journey.add_argument("--objective")
    journey.add_argument("--planner", choices=["openai", "openrouter"], default="openai")
    journey.add_argument("--steps", type=int, default=8)
    journey.add_argument("--run-id")
    journey.add_argument("--execute", action="store_true")
    journey.add_argument(
        "--native-assisted",
        action="store_true",
        help="Acknowledge execution through configured native-assisted command bridges.",
    )
    journey.add_argument("--exclusive", action="store_true")
    journey.add_argument(
        "--no-ownership-overlay",
        action="store_true",
        help="Do not open the visible human/agent ownership and countdown window.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "launch":
        return asyncio.run(_launch(args))
    if args.command == "shot":
        return _shot(args)
    if args.command == "telemetry":
        return _telemetry(args)
    if args.command == "journey":
        return _journey(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
