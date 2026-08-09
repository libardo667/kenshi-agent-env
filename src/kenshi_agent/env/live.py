from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ..config import CaptureConfig, ControlsConfig, RuntimeConfig
from ..control.base import InputController
from ..control.capture import WindowCapture
from ..core.observation import Observation
from ..core.operation import (
    QUICKSAVE_COMPLETION_CAPABILITY,
    ControlMode,
)
from ..core.telemetry import (
    NativeControlState,
    TelemetrySnapshot,
)
from ..core.world import WorldStateRevision
from ..execution.handlers.kenshi import KenshiOperationMechanics
from ..execution.handlers.kenshi_surface import KenshiControlSurface
from ..final_safe_state import (
    FinalSafeStateOutcome,
    FinalSafeStateStatus,
    ensure_final_safe_state,
)
from ..telemetry import TelemetryReader, TelemetryReadError
from ..terminal_state import terminal_window_event, terminal_window_title
from .base import AgentEnvironment


class LiveEnvironment(AgentEnvironment):
    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        telemetry: TelemetryReader,
        controller: InputController,
        runtime_config: RuntimeConfig,
        controls_config: ControlsConfig,
        capture_config: CaptureConfig,
        execute_actions: bool,
        emergency_stop_key: str,
        final_pause_timeout_seconds: float = 2.0,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
        quicksave_dir: Path | None = None,
        quicksave_timeout_seconds: float = 10.0,
        quicksave_stable_seconds: float = 0.5,
    ) -> None:
        if quicksave_timeout_seconds <= 0.0 or quicksave_stable_seconds <= 0.0:
            raise ValueError("Quicksave monitoring times must be positive.")
        if quicksave_stable_seconds >= quicksave_timeout_seconds:
            raise ValueError("Quicksave stable time must be shorter than its completion timeout.")
        self.run_id = run_id
        self.run_dir = run_dir
        self.telemetry_reader = telemetry
        self.controller = controller
        self.runtime_config = runtime_config
        self.controls_config = controls_config
        self.capture_config = capture_config
        self.execute_actions = execute_actions
        self.emergency_stop_key = emergency_stop_key
        self.final_pause_timeout_seconds = final_pause_timeout_seconds
        self.control_mode = control_mode
        self.quicksave_dir = quicksave_dir
        self.quicksave_timeout_seconds = quicksave_timeout_seconds
        self.quicksave_stable_seconds = quicksave_stable_seconds
        self._step_index = 0
        self._capture_sequence = 0
        self._last_observation: Observation | None = None
        self._capability_epoch = 0
        self._last_capability_signature: tuple[str, ...] | None = None
        self._close_outcome: FinalSafeStateOutcome | None = None
        self._close_lock = asyncio.Lock()
        self._capture = (
            WindowCapture(
                controller,
                run_dir / "frames",
                image_format=capture_config.image_format,
                jpeg_quality=capture_config.jpeg_quality,
            )
            if capture_config.enabled
            else None
        )
        self._surface = KenshiControlSurface(self)
        self._mechanics = KenshiOperationMechanics(self._surface)

    @property
    def control_surface(self) -> KenshiControlSurface:
        """External delivery mechanics: lease, primitives, native transport."""

        return self._surface

    @property
    def operation_mechanics(self) -> KenshiOperationMechanics:
        """Kenshi mechanics assembled around this external adapter's ports."""

        return self._mechanics

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        self._step_index = 0
        self._capability_epoch = 0
        self._last_capability_signature = None
        return await self.observe()

    async def observe(self) -> Observation:
        return await self._observe(capture=True)

    async def observe_without_capture(self) -> Observation:
        return await self._observe(capture=False)

    def input_boundary_observation(self) -> Observation:
        """Read telemetry and ownership again inside the acquired input lease."""

        events: list[str] = []
        if self.execute_actions and self.controller.continuous_user_input_detected():
            events.append("human_input_detected")
            diagnostic = self.controller.continuous_user_input_diagnostic()
            if diagnostic is not None:
                events.append(diagnostic)
        if self.controller.emergency_stop_pressed(self.emergency_stop_key):
            events.append("emergency_stop_detected")
        try:
            terminal_title = terminal_window_title(self.controller)
        except (OSError, RuntimeError, ValueError) as exc:
            events.append(f"Terminal-window probe failed: {type(exc).__name__}: {exc}")
        else:
            if terminal_title is not None:
                events.append(terminal_window_event(terminal_title))
        try:
            result = self.telemetry_reader.read()
        except TelemetryReadError as exc:
            return Observation(
                run_id=self.run_id,
                step_index=self._step_index,
                mode="live",
                control_mode=self.control_mode,
                world_revision=WorldStateRevision(
                    capability_epoch=self._capability_epoch,
                    observed_at_monotonic=time.monotonic(),
                ),
                telemetry_stale=True,
                events=[*events, str(exc)],
            )
        if result.stale:
            events.append(f"Telemetry is stale by {result.age_seconds:.2f} seconds.")
        return self._observation_from_snapshot(
            result.snapshot,
            telemetry_stale=result.stale,
            telemetry_age_seconds=result.age_seconds,
            events=events,
        )

    def input_boundary_max_telemetry_age_seconds(self) -> float:
        """Use the same configured freshness ceiling as the telemetry reader."""

        return self.telemetry_reader.max_age_seconds

    async def _observe(self, *, capture: bool) -> Observation:
        events: list[str] = []
        if self.execute_actions and self.controller.continuous_user_input_detected():
            events.append("human_input_detected")
            diagnostic = self.controller.continuous_user_input_diagnostic()
            if diagnostic is not None:
                events.append(diagnostic)
        if self.controller.emergency_stop_pressed(self.emergency_stop_key):
            events.append("emergency_stop_detected")
        try:
            terminal_title = terminal_window_title(self.controller)
        except (OSError, RuntimeError, ValueError) as exc:
            events.append(f"Terminal-window probe failed: {type(exc).__name__}: {exc}")
        else:
            if terminal_title is not None:
                events.append(terminal_window_event(terminal_title))
        telemetry_snapshot = None
        telemetry_stale = True
        telemetry_age = None
        try:
            result = self.telemetry_reader.read()
            telemetry_snapshot = self._apply_control_mode(result.snapshot)
            telemetry_stale = result.stale
            telemetry_age = result.age_seconds
            if result.stale:
                events.append(f"Telemetry is stale by {result.age_seconds:.2f} seconds.")
        except TelemetryReadError as exc:
            events.append(str(exc))

        screenshot_path = None
        screenshot_hash = None
        if capture and self._capture is not None:
            try:
                self._capture_sequence += 1
                async with self.controller.input_lease():
                    frame = self._capture.capture(self._capture_sequence)
                screenshot_path = frame.path
                screenshot_hash = frame.sha256
                if telemetry_snapshot is not None:
                    telemetry_snapshot = telemetry_snapshot.model_copy(
                        update={
                            "ui": telemetry_snapshot.ui.model_copy(
                                update={"client_width": frame.width, "client_height": frame.height}
                            )
                        }
                    )
            except Exception as exc:
                events.append(f"Screenshot capture failed: {type(exc).__name__}: {exc}")

        capability_signature = tuple(
            sorted(telemetry_snapshot.capabilities) if telemetry_snapshot is not None else []
        )
        if capability_signature != self._last_capability_signature:
            self._capability_epoch += 1
            self._last_capability_signature = capability_signature

        observation = Observation(
            run_id=self.run_id,
            step_index=self._step_index,
            mode="live",
            control_mode=self.control_mode,
            world_revision=WorldStateRevision(
                telemetry_sequence=(
                    telemetry_snapshot.sequence if telemetry_snapshot is not None else None
                ),
                frame_sequence=(self._capture_sequence if screenshot_path is not None else None),
                capability_epoch=self._capability_epoch,
                observed_at_monotonic=time.monotonic(),
            ),
            telemetry=telemetry_snapshot,
            telemetry_stale=telemetry_stale,
            telemetry_age_seconds=telemetry_age,
            screenshot_path=screenshot_path,
            screenshot_sha256=screenshot_hash,
            events=events,
            objective=self.runtime_config.objective,
        )
        self._last_observation = observation
        return observation

    def _apply_control_mode(self, snapshot: TelemetrySnapshot) -> TelemetrySnapshot:
        """Withhold native-control evidence that `interface_only` may not use."""

        capabilities = list(snapshot.capabilities)
        if self.quicksave_dir is not None and QUICKSAVE_COMPLETION_CAPABILITY not in capabilities:
            capabilities.append(QUICKSAVE_COMPLETION_CAPABILITY)
        if self._capture is not None and "camera.recovery" not in capabilities:
            # This is a controller capability, not a native plug-in claim. It is
            # advertised only when this environment actually owns a capture
            # backend with which the recovery handler can retain and score
            # candidate frames.
            capabilities.append("camera.recovery")
        if self.control_mode != ControlMode.INTERFACE_ONLY:
            return snapshot.model_copy(update={"capabilities": capabilities})
        return snapshot.model_copy(
            update={
                "capabilities": [
                    capability
                    for capability in capabilities
                    if not capability.startswith("control.")
                ],
                "controller_commands": NativeControlState(),
            }
        )

    def _observation_from_snapshot(
        self,
        snapshot: TelemetrySnapshot,
        *,
        telemetry_stale: bool = False,
        telemetry_age_seconds: float = 0.0,
        events: list[str] | None = None,
    ) -> Observation:
        """A minimal current observation for in-lease reference re-resolution.

        Deliberately not a full `observe()`: no capture, no event collection, no
        `_last_observation` mutation. It exists so an action can re-bind its
        reference against fresh telemetry at the moment of input without
        disturbing the canonical stream the executor and supervisor share.
        """

        telemetry = self._apply_control_mode(snapshot)
        capability_signature = tuple(sorted(telemetry.capabilities))
        capability_epoch = self._capability_epoch + (
            1 if capability_signature != self._last_capability_signature else 0
        )
        return Observation(
            run_id=self.run_id,
            step_index=self._step_index,
            mode="live",
            control_mode=self.control_mode,
            world_revision=WorldStateRevision(
                telemetry_sequence=telemetry.sequence,
                capability_epoch=capability_epoch,
                observed_at_monotonic=time.monotonic(),
            ),
            telemetry=telemetry,
            telemetry_stale=telemetry_stale,
            telemetry_age_seconds=telemetry_age_seconds,
            events=events or [],
        )

    async def close(self) -> FinalSafeStateOutcome:
        async with self._close_lock:
            if self._close_outcome is not None:
                return self._close_outcome
            if not self.execute_actions:
                self._close_outcome = await ensure_final_safe_state(
                    controller=self.controller,
                    telemetry=self.telemetry_reader,
                    pause_primitives=[],
                    timeout_seconds=self.final_pause_timeout_seconds,
                    input_authorized=False,
                )
                return self._close_outcome
            try:
                pause_primitives, _ = self._surface.pause_primitives(True)
            except Exception as exc:
                self._close_outcome = FinalSafeStateOutcome(
                    status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
                    reason=(
                        f"Final-pause control could not be resolved ({type(exc).__name__}: {exc})."
                    ),
                )
                return self._close_outcome
            self._close_outcome = await ensure_final_safe_state(
                controller=self.controller,
                telemetry=self.telemetry_reader,
                pause_primitives=pause_primitives,
                timeout_seconds=self.final_pause_timeout_seconds,
                input_authorized=True,
            )
            return self._close_outcome
