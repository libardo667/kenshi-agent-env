from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ..config import CaptureConfig, ControlsConfig, RuntimeConfig
from ..control.base import InputController
from ..control.capture import WindowCapture
from ..models import (
    Action,
    ActionReceipt,
    ClickAction,
    HotkeyAction,
    KeyAction,
    MoveCursorAction,
    NoopAction,
    Observation,
    PauseAction,
    SetSpeedAction,
    SkillAction,
    StopAction,
    Transition,
    WaitAction,
)
from ..skills import MacroRegistry
from ..telemetry import TelemetryReader, TelemetryReadError
from .base import AgentEnvironment


class LiveEnvironment(AgentEnvironment):
    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        telemetry: TelemetryReader,
        controller: InputController,
        macros: MacroRegistry,
        runtime_config: RuntimeConfig,
        controls_config: ControlsConfig,
        capture_config: CaptureConfig,
        execute_actions: bool,
        emergency_stop_key: str,
    ) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.telemetry_reader = telemetry
        self.controller = controller
        self.macros = macros
        self.runtime_config = runtime_config
        self.controls_config = controls_config
        self.capture_config = capture_config
        self.execute_actions = execute_actions
        self.emergency_stop_key = emergency_stop_key
        self._step_index = 0
        self._capture_sequence = 0
        self._last_observation: Observation | None = None
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

    async def reset(self, *, seed: int | None = None) -> Observation:
        del seed
        self._step_index = 0
        return await self.observe()

    async def observe(self) -> Observation:
        events: list[str] = []
        telemetry_snapshot = None
        telemetry_stale = True
        telemetry_age = None
        try:
            result = self.telemetry_reader.read()
            telemetry_snapshot = result.snapshot
            telemetry_stale = result.stale
            telemetry_age = result.age_seconds
            if result.stale:
                events.append(f"Telemetry is stale by {result.age_seconds:.2f} seconds.")
        except TelemetryReadError as exc:
            events.append(str(exc))

        screenshot_path = None
        screenshot_hash = None
        if self._capture is not None:
            try:
                self._capture_sequence += 1
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

        observation = Observation(
            run_id=self.run_id,
            step_index=self._step_index,
            mode="live",
            telemetry=telemetry_snapshot,
            telemetry_stale=telemetry_stale,
            telemetry_age_seconds=telemetry_age,
            screenshot_path=screenshot_path,
            screenshot_sha256=screenshot_hash,
            events=events,
            available_skills=self.macros.names(),
        )
        self._last_observation = observation
        return observation

    async def step(self, action: Action) -> Transition:
        started = datetime.now(UTC)
        terminated = isinstance(action, StopAction)
        if isinstance(action, StopAction):
            receipt = ActionReceipt(
                action=action,
                accepted=True,
                executed=False,
                dry_run=not self.execute_actions,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=0,
                message=action.reason,
            )
        elif not self.execute_actions:
            receipt = ActionReceipt(
                action=action,
                accepted=True,
                executed=False,
                dry_run=True,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=self.macros.primitive_count(action),
                message="Live action withheld by the dry-run safety gate.",
            )
        else:
            if self.controller.emergency_stop_pressed(self.emergency_stop_key):
                raise RuntimeError(
                    f"Emergency stop key {self.emergency_stop_key!r} is pressed; action aborted."
                )
            receipt = await self._execute_live(action, started)

        self._step_index += 1
        if self.runtime_config.settle_seconds:
            await asyncio.sleep(self.runtime_config.settle_seconds)
        observation = await self.observe()
        return Transition(
            receipt=receipt,
            observation=observation,
            terminated=terminated,
            success=None,
            events=observation.events,
        )

    async def _execute_live(self, action: Action, started: datetime) -> ActionReceipt:
        if isinstance(action, NoopAction):
            return ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=0,
                message=action.reason,
            )
        if isinstance(action, WaitAction):
            await asyncio.sleep(action.seconds)
            return ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=0,
                message=f"Observed without input for {action.seconds:.2f} seconds.",
            )
        if isinstance(action, PauseAction):
            paused = (
                self._last_observation.telemetry.game.paused
                if self._last_observation is not None
                and self._last_observation.telemetry is not None
                else None
            )
            if paused is action.paused:
                return ActionReceipt(
                    action=action,
                    accepted=True,
                    executed=True,
                    dry_run=False,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    primitive_actions=0,
                    message=f"Kenshi already reports paused={action.paused}.",
                )
            if paused is None:
                raise RuntimeError(
                    "Refusing to toggle Kenshi pause because the current pause state is unknown."
                )
            primitive = KeyAction(key=self.controls_config.pause_key)
            primitive_receipt = await self.controller.execute(primitive)
            return primitive_receipt.model_copy(
                update={
                    "action": action,
                    "message": (
                        f"Pressed {self.controls_config.pause_key!r} to request "
                        f"paused={action.paused}. "
                        "A later observation must confirm the state."
                    ),
                }
            )
        if isinstance(action, SetSpeedAction):
            primitive = KeyAction(key=self.controls_config.speed_keys[action.speed])
            primitive_receipt = await self.controller.execute(primitive)
            return primitive_receipt.model_copy(
                update={
                    "action": action,
                    "message": (
                        f"Pressed the configured speed-{action.speed} key. "
                        "A later observation must confirm the speed."
                    ),
                }
            )
        if isinstance(action, SkillAction):
            primitives = self.macros.expand(action)
            primitive_count = 0
            messages: list[str] = []
            for macro_primitive in primitives:
                if self.controller.emergency_stop_pressed(self.emergency_stop_key):
                    raise RuntimeError("Emergency stop pressed during macro execution.")
                if not isinstance(
                    macro_primitive,
                    (KeyAction, HotkeyAction, MoveCursorAction, ClickAction),
                ):
                    raise TypeError(
                        f"Live macro {action.name!r} contains unsupported primitive "
                        f"{macro_primitive.kind!r}."
                    )
                primitive_receipt = await self.controller.execute(macro_primitive)
                primitive_count += primitive_receipt.primitive_actions
                messages.append(primitive_receipt.message)
            return ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=primitive_count,
                message=f"Executed skill {action.name!r}. " + " ".join(messages),
            )
        if isinstance(action, (KeyAction, HotkeyAction, MoveCursorAction, ClickAction)):
            return await self.controller.execute(action)
        raise TypeError(f"Unsupported live action: {type(action).__name__}")

    async def close(self) -> None:
        return None
