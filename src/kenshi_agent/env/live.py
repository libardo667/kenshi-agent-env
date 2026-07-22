from __future__ import annotations

import asyncio
import time
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
        available_skills: list[str] | None = None,
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
        self.available_skills = sorted(set(available_skills or macros.names()))
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
            objective=self.runtime_config.objective,
            available_skills=self.available_skills,
            skill_specs=[self.macros.spec(name) for name in self.available_skills],
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
            if isinstance(action, (NoopAction, StopAction, WaitAction)):
                receipt = await self._execute_live(action, started)
            else:
                async with self.controller.input_lease(alt_tab_on_restore=True):
                    receipt = await self._execute_live(action, started)
                lease_wait = self.controller.input_lease_wait_seconds()
                if lease_wait >= 0.01:
                    receipt = receipt.model_copy(
                        update={
                            "message": (
                                f"Waited {lease_wait:.2f}s for a quiet input turn. "
                                + receipt.message
                            )
                        }
                    )

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
            pulse_seconds = self.macros.resolve_movement_pulse_seconds(action)
            if pulse_seconds is not None:
                return await self._execute_movement_pulse(
                    action, started, pulse_seconds=pulse_seconds
                )
            return await self._execute_skill(action, started)
        if isinstance(action, (KeyAction, HotkeyAction, MoveCursorAction, ClickAction)):
            return await self.controller.execute(action)
        raise TypeError(f"Unsupported live action: {type(action).__name__}")

    async def _execute_skill(self, action: SkillAction, started: datetime) -> ActionReceipt:
        primitive_count, messages = await self._execute_skill_primitives(action)
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

    async def _execute_skill_primitives(self, action: SkillAction) -> tuple[int, list[str]]:
        primitives = self.macros.expand(action)
        primitive_count = 0
        messages: list[str] = []
        for macro_primitive in primitives:
            if self.controller.user_input_detected():
                raise RuntimeError("User input resumed during macro execution; yielding control.")
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
        return primitive_count, messages

    async def _execute_movement_pulse(
        self,
        action: SkillAction,
        started: datetime,
        *,
        pulse_seconds: float,
    ) -> ActionReceipt:
        paused = (
            self._last_observation.telemetry.game.paused
            if self._last_observation is not None and self._last_observation.telemetry is not None
            else None
        )
        if paused is not True:
            raise RuntimeError(
                f"Movement pulse {action.name!r} requires confirmed paused live state."
            )

        primitive_count, messages = await self._execute_skill_primitives(action)
        pause_key = KeyAction(key=self.controls_config.pause_key)
        unpause_sent = False
        emergency_stop = False
        user_interrupted = False
        try:
            unpause_receipt = await self.controller.execute(pause_key)
            unpause_sent = True
            primitive_count += unpause_receipt.primitive_actions
            if not await self._wait_for_pause_state(False):
                if self._fresh_pause_state() is True:
                    unpause_sent = False
                raise RuntimeError("Kenshi did not confirm unpaused state for movement pulse.")

            deadline = time.monotonic() + pulse_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self.controller.emergency_stop_pressed(self.emergency_stop_key):
                    emergency_stop = True
                    break
                if self.controller.user_input_detected():
                    user_interrupted = True
                    break
                await asyncio.sleep(min(0.1, remaining))
        finally:
            if unpause_sent:
                pause_receipt = await self.controller.execute_safety(pause_key)
                primitive_count += pause_receipt.primitive_actions
                if not await self._wait_for_pause_state(True):
                    if self._fresh_pause_state() is False:
                        retry_receipt = await self.controller.execute_safety(pause_key)
                        primitive_count += retry_receipt.primitive_actions
                    if not await self._wait_for_pause_state(True):
                        raise RuntimeError(
                            "Movement pulse ended but Kenshi did not confirm re-paused state."
                        )

        if emergency_stop:
            raise RuntimeError("Emergency stop ended the movement pulse after re-pausing Kenshi.")
        outcome = (
            "Human input ended the pulse; confirmed re-paused state and yielded control."
            if user_interrupted
            else f"Advanced Kenshi for {pulse_seconds:.2f}s and confirmed re-paused state."
        )
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=primitive_count,
            message=(
                f"Executed skill {action.name!r}. {outcome} " + " ".join(messages)
            ),
        )

    async def _wait_for_pause_state(self, expected: bool, *, timeout_seconds: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                result = self.telemetry_reader.read()
                if not result.stale and result.snapshot.game.paused is expected:
                    return True
            except TelemetryReadError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.05, remaining))

    def _fresh_pause_state(self) -> bool | None:
        try:
            result = self.telemetry_reader.read()
        except TelemetryReadError:
            return None
        if result.stale:
            return None
        return result.snapshot.game.paused

    async def close(self) -> None:
        return None
