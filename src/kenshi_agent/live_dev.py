from __future__ import annotations

import argparse
import asyncio
import json
import os
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
from .models import ClickAction, KeyAction
from .telemetry import TelemetryReader, TelemetryReadError


class LaunchInterrupted(RuntimeError):
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
            "Kenshi launcher stopped because human input was detected; "
            "no further launcher input was sent."
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
        _abort_if_human_input(controller)
        try:
            if predicate():
                return
        except (OSError, RuntimeError, ValueError):
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {description}.")


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


async def _launch(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("The live developer launcher must run with Windows Python.")
    config = load_config(args.config)
    controller = _controller(config)
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
            await _click(controller, 0.742, 0.965)

        status_path = config.telemetry.file.parent / "plugin_status.json"

        def plugin_ready() -> bool:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            captured = datetime.fromisoformat(payload["captured_at"].replace("Z", "+00:00"))
            return payload.get("state") == "ready" and captured >= launched_at

        await _wait_until(
            plugin_ready,
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
        _validate_calibrated_client_rect(controller.client_rect(), config.controls)
        await asyncio.sleep(2.0)
        _abort_if_human_input(controller)

        if args.continue_game:
            # One calibrated title-screen sequence only. If RE_Kenshi reopens
            # its panel or load does not begin, time out and yield to the user
            # instead of repeatedly reclaiming focus.
            _validate_calibrated_client_rect(controller.client_rect(), config.controls)
            await _click(controller, 0.300, 0.110)
            _validate_calibrated_client_rect(controller.client_rect(), config.controls)
            await _click(controller, 0.338, 0.171)
            reader = _telemetry_read(config)

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
    except LaunchInterrupted as exc:
        print(str(exc), file=sys.stderr)
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
    ]
    if args.run_id:
        argv.extend(["--run-id", args.run_id])
    if args.objective:
        argv.extend(["--objective", args.objective])
    if args.execute:
        argv.append("--execute-live-actions")
    if args.native_assisted:
        argv.append("--acknowledge-native-assisted-control")
    if args.exclusive:
        argv.append("--exclusive-input-session")
    return agent_main(argv)


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
