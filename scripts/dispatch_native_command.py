#!/usr/bin/env python3
"""Dispatch one native command and read its acknowledgement, for diagnosis.

The plug-in reads its request file only when the fixed trigger hotkey fires, so
writing the file is not dispatching. This does both through the same primitive
path the agent uses, which is the difference between testing the transport and
testing a JSON file.

Diagnostic tooling: it drives one command against a live session and prints
what came back. It is not a second execution path - it holds no input lease
discipline of its own and must never be used inside a run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kenshi_agent.config import load_config  # noqa: E402
from kenshi_agent.control.base import HotkeyAction  # noqa: E402
from kenshi_agent.core.transport import NativeCommandRequest, new_command_id  # noqa: E402
from kenshi_agent.core.world import WorldStateRevision  # noqa: E402
from kenshi_agent.native_commands import (  # noqa: E402
    write_native_command_request_atomic,
)
from kenshi_agent.telemetry import TelemetryReader  # noqa: E402
from kenshi_agent.tooling.live_dev import _controller  # noqa: E402

TRIGGER = HotkeyAction(keys=["ctrl", "shift", "f10"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config" / "live.yaml"))
    parser.add_argument("--command", required=True)
    parser.add_argument("--target-id", default="")
    parser.add_argument(
        "--context-action",
        default="",
        help="Reviewed context semantic, required by perform_context_action.",
    )
    # The transfer's own address. These lagged the protocol: a transfer could
    # not be dispatched by hand at all, which is why its first diagnosis needed
    # a planner and a model in the loop to press the button.
    parser.add_argument("--destination-id", default="")
    parser.add_argument("--section-name", default="")
    parser.add_argument("--slot-x", type=int, default=0)
    parser.add_argument("--slot-y", type=int, default=0)
    parser.add_argument("--bearing-degrees", type=float, default=0.0)
    parser.add_argument("--distance-units", type=float, default=0.0)
    parser.add_argument("--minimum-output-quantity", type=int, default=1)
    parser.add_argument("--quantity", type=int, default=0)
    parser.add_argument(
        "--paused",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Requested state for the pause command (default: running).",
    )
    parser.add_argument("--speed-multiplier", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    config = load_config(Path(args.config))
    reader = TelemetryReader(
        config.telemetry.file,
        max_age_seconds=config.telemetry.max_age_seconds,
        retries=config.telemetry.read_retries,
        retry_delay_seconds=config.telemetry.retry_delay_seconds,
        require_protocol_major=config.telemetry.require_protocol_major,
    )
    read = reader.read()
    snapshot = read.snapshot
    if read.stale:
        print("telemetry is stale; refusing to dispatch", file=sys.stderr)
        return 1

    command_id = new_command_id()
    request = NativeCommandRequest(
        schema_version="1.4",
        command_id=command_id,
        command=args.command,  # type: ignore[arg-type]
        control_mode="native_assisted",
        identity_session_id=snapshot.identity_session_id,
        based_on_revision=WorldStateRevision(
            # The capability epoch is a runtime counter the coordinator owns,
            # not a snapshot field. The plug-in's transport window checks the
            # telemetry sequence; zero is the honest value for a one-off
            # diagnostic that owns no run state.
            telemetry_sequence=snapshot.sequence,
            capability_epoch=0,
        ),
        selected_character_ids=list(snapshot.selected_character_ids),
        target_id=args.target_id,
        context_action=args.context_action,
        bearing_degrees=args.bearing_degrees,
        distance_units=args.distance_units,
        minimum_output_quantity=args.minimum_output_quantity,
        destination_id=args.destination_id,
        section_name=args.section_name,
        slot_x=args.slot_x,
        slot_y=args.slot_y,
        paused=args.paused,
        speed_multiplier=args.speed_multiplier,
        quantity=args.quantity,
    )
    write_native_command_request_atomic(
        config.telemetry.file.parent / "native_command.request.json",
        request,
    )
    print(f"wrote request {command_id} for {args.command}")

    # Built exactly as the supported live path builds it, so the trigger
    # travels the same focus, politeness, and restore discipline.
    controller = _controller(config)

    async def send_trigger() -> None:
        async with controller.input_lease():
            await controller.execute(TRIGGER)

    asyncio.run(send_trigger())
    print("sent the native command trigger")

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        current = reader.read().snapshot
        acknowledgement = current.controller_commands.command_for(command_id)
        if acknowledgement is not None:
            print(
                json.dumps(
                    acknowledgement.model_dump(mode="json"),
                    indent=2,
                )
            )
            return 0
        time.sleep(0.25)
    print("no acknowledgement before timeout", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
