from __future__ import annotations

import asyncio
import time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .control.base import InputController, PrimitiveInputAction
from .telemetry import TelemetryReader, TelemetryReadError


class FinalSafeStateStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PAUSE_CONFIRMED = "pause_confirmed"
    PAUSE_UNVERIFIED = "pause_unverified"


class FinalSafeStateOutcome(BaseModel):
    """Causal result of the one final live-run cleanup owner."""

    model_config = ConfigDict(extra="forbid")

    status: FinalSafeStateStatus
    reason: str
    initial_sequence: int | None = None
    confirmed_sequence: int | None = None
    input_attempted: bool = False
    input_executed: bool = False


async def ensure_final_safe_state(
    *,
    controller: InputController,
    telemetry: TelemetryReader,
    pause_primitives: list[PrimitiveInputAction],
    timeout_seconds: float,
    input_authorized: bool,
) -> FinalSafeStateOutcome:
    """Leave a loaded live game paused, or report why that could not be proved."""

    if not input_authorized:
        return FinalSafeStateOutcome(
            status=FinalSafeStateStatus.NOT_REQUIRED,
            reason="Live action execution was not authorized; no cleanup input was sent.",
        )

    try:
        initial = telemetry.read()
    except TelemetryReadError as exc:
        return FinalSafeStateOutcome(
            status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
            reason=f"Pause state unavailable; no cleanup input was sent ({exc}).",
        )
    snapshot = initial.snapshot
    initial_sequence = snapshot.sequence
    if initial.stale or not snapshot.game.loaded:
        return FinalSafeStateOutcome(
            status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
            reason="Game is not freshly loaded; no cleanup input was sent.",
            initial_sequence=initial_sequence,
        )
    if "game.pause" not in snapshot.capabilities:
        return FinalSafeStateOutcome(
            status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
            reason="Pause capability unavailable; no cleanup input was sent.",
            initial_sequence=initial_sequence,
        )
    if snapshot.game.paused is True:
        return FinalSafeStateOutcome(
            status=FinalSafeStateStatus.PAUSE_CONFIRMED,
            reason=f"Already confirmed paused at telemetry sequence {initial_sequence}.",
            initial_sequence=initial_sequence,
            confirmed_sequence=initial_sequence,
        )
    if snapshot.game.paused is not False:
        return FinalSafeStateOutcome(
            status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
            reason="Pause state or capability unavailable; no cleanup input was sent.",
            initial_sequence=initial_sequence,
        )
    if not pause_primitives:
        return FinalSafeStateOutcome(
            status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
            reason="No configured final-pause primitive was available.",
            initial_sequence=initial_sequence,
        )

    input_executed = False
    try:
        async with controller.safety_input_lease():
            for primitive in pause_primitives:
                receipt = await controller.execute_safety(primitive)
                input_executed = input_executed or receipt.executed
                if not receipt.executed:
                    return FinalSafeStateOutcome(
                        status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
                        reason=(
                            "A final-pause primitive was not executed "
                            f"({receipt.message})."
                        ),
                        initial_sequence=initial_sequence,
                        input_attempted=True,
                        input_executed=input_executed,
                    )
    except Exception as exc:
        return FinalSafeStateOutcome(
            status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
            reason=(
                "Final-pause input failed "
                f"({type(exc).__name__}: {exc})."
            ),
            initial_sequence=initial_sequence,
            input_attempted=True,
            input_executed=input_executed,
        )

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            result = telemetry.read()
        except TelemetryReadError:
            result = None
        if result is not None and not result.stale:
            current = result.snapshot
            if (
                current.sequence > initial_sequence
                and current.game.loaded
                and current.game.paused is True
                and "game.pause" in current.capabilities
            ):
                return FinalSafeStateOutcome(
                    status=FinalSafeStateStatus.PAUSE_CONFIRMED,
                    reason=(
                        "Confirmed paused at telemetry sequence "
                        f"{current.sequence}."
                    ),
                    initial_sequence=initial_sequence,
                    confirmed_sequence=current.sequence,
                    input_attempted=True,
                    input_executed=input_executed,
                )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return FinalSafeStateOutcome(
                status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
                reason=(
                    "Final-pause input was not confirmed on a causally later "
                    "fresh telemetry sequence."
                ),
                initial_sequence=initial_sequence,
                input_attempted=True,
                input_executed=input_executed,
            )
        await asyncio.sleep(min(0.05, remaining))
