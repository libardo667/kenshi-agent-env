from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from ..config import CaptureConfig, ControlsConfig, RuntimeConfig
from ..control.base import InputController, PrimitiveInputAction
from ..control.calibration import (
    calibration_allows_input,
    evaluate_calibration_identity,
    validate_expected_client_size,
)
from ..control.capture import WindowCapture
from ..input_boundary import ExecutionToken
from ..models import (
    Action,
    ActionReceipt,
    CalibrationReport,
    ClickAction,
    CommandDispatchContext,
    ControlMode,
    HotkeyAction,
    InputBoundaryDecision,
    KeyAction,
    MoveCursorAction,
    NativeCommandAcknowledgement,
    NativeCommandRequest,
    NativeCommandStatus,
    NativeControlState,
    NoopAction,
    Observation,
    PauseAction,
    PointerActionClass,
    ScrollAction,
    SetSpeedAction,
    SkillAction,
    StopAction,
    Transition,
    WaitAction,
    WorldStateRevision,
)
from ..native_commands import write_native_command_request_atomic
from ..skills import MacroRegistry
from ..telemetry import TelemetryReader, TelemetryReadError
from .base import AgentEnvironment


class LiveEnvironment(AgentEnvironment):
    _NATIVE_COMMAND_REQUEST_FILE = "native_command.request.json"
    _NATIVE_COMMAND_ACK_TIMEOUT_SECONDS = 2.0
    _NATIVE_COMMAND_POLL_SECONDS = 0.025

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
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
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
        self.control_mode = control_mode
        self.available_skills = macros.available_names(
            available_skills or macros.names(),
            control_mode=control_mode,
        )
        self._step_index = 0
        self._capture_sequence = 0
        self._last_observation: Observation | None = None
        self._capability_epoch = 0
        self._last_capability_signature: tuple[str, ...] | None = None
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
        self._capability_epoch = 0
        self._last_capability_signature = None
        return await self.observe()

    async def observe(self) -> Observation:
        return await self._observe(capture=True)

    async def observe_without_capture(self) -> Observation:
        return await self._observe(capture=False)

    async def _observe(self, *, capture: bool) -> Observation:
        events: list[str] = []
        if self.execute_actions and self.controller.continuous_user_input_detected():
            events.append("human_input_detected")
            diagnostic = self.controller.continuous_user_input_diagnostic()
            if diagnostic is not None:
                events.append(diagnostic)
        if self.controller.emergency_stop_pressed(self.emergency_stop_key):
            events.append("emergency_stop_detected")
        telemetry_snapshot = None
        telemetry_stale = True
        telemetry_age = None
        try:
            result = self.telemetry_reader.read()
            telemetry_snapshot = result.snapshot
            if self.control_mode == ControlMode.INTERFACE_ONLY:
                telemetry_snapshot = telemetry_snapshot.model_copy(
                    update={
                        "capabilities": [
                            capability
                            for capability in telemetry_snapshot.capabilities
                            if not capability.startswith("control.")
                        ],
                        "native_control": NativeControlState(),
                    }
                )
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
            available_skills=self.available_skills,
            skill_specs=[self.macros.spec(name) for name in self.available_skills],
        )
        self._last_observation = observation
        return observation

    async def step(self, action: Action) -> Transition:
        return await self._step(action, command=None)

    async def dispatch(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None = None,
    ) -> Transition:
        return await self._step(action, command=command, token=token)

    async def _step(
        self,
        action: Action,
        *,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None = None,
    ) -> Transition:
        started = datetime.now(UTC)
        terminated = isinstance(action, StopAction)
        if (
            isinstance(action, SkillAction)
            and self.macros.has(action.name)
            and self.macros.requires_native_assisted(action.name)
            and self.control_mode != ControlMode.NATIVE_ASSISTED
        ):
            raise RuntimeError(f"Skill {action.name!r} requires native_assisted control mode.")
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
                receipt = await self._execute_live(action, started, command)
            else:
                async with self.controller.input_lease(alt_tab_on_restore=True):
                    # The lease wait is unbounded, so calibration and the caller's
                    # typed authorization are both re-checked here, after the wait
                    # and immediately before the first primitive can be emitted.
                    calibration = self.calibration_report(action)
                    lease_wait = self.controller.input_lease_wait_seconds()
                    boundary = (
                        token.revalidate(
                            lease_wait_seconds=lease_wait,
                            calibration=calibration,
                        )
                        if token is not None
                        else None
                    )
                    if boundary is not None and boundary.decision is (
                        InputBoundaryDecision.REJECTED
                    ):
                        # A plan step carries the rejection gracefully: zero input,
                        # and the executor releases the reservation.
                        receipt = ActionReceipt(
                            action=action,
                            accepted=False,
                            executed=False,
                            dry_run=False,
                            started_at=started,
                            finished_at=datetime.now(UTC),
                            primitive_actions=0,
                            message=(
                                "No input was emitted: the state that authorized this "
                                f"action changed while the input lease was pending. "
                                f"{boundary.reason}"
                            ),
                            error_type="InputBoundaryRejected",
                        )
                    elif not calibration_allows_input(calibration):
                        # No token to carry the rejection (single-step or bare
                        # step()): preserve the proven fail-closed raise.
                        self._raise_for_calibration(calibration)
                    else:
                        receipt = await self._execute_live(action, started, command)
                    receipt = receipt.model_copy(
                        update={"input_boundary": boundary, "calibration": calibration}
                    )
                if lease_wait >= 0.01:
                    receipt = receipt.model_copy(
                        update={
                            "message": (
                                f"Waited {lease_wait:.2f}s for a quiet input turn. "
                                + receipt.message
                            )
                        }
                    )

        receipt = receipt.model_copy(update={"control_mode": self.control_mode})
        self._step_index += 1
        if self.runtime_config.settle_seconds:
            await asyncio.sleep(self.runtime_config.settle_seconds)
        observation = await self.observe()
        if receipt.native_acknowledgement is not None and observation.telemetry is not None:
            latest_acknowledgement = observation.telemetry.native_control.acknowledgement_for(
                receipt.native_acknowledgement.command_id
            )
            if (
                latest_acknowledgement is not None
                and latest_acknowledgement != receipt.native_acknowledgement
            ):
                terminal_message = (
                    f" Latest native status is "
                    f"{latest_acknowledgement.status.value!r}: "
                    f"{latest_acknowledgement.reason}."
                )
                receipt = receipt.model_copy(
                    update={
                        "native_acknowledgement": latest_acknowledgement,
                        "message": receipt.message + terminal_message,
                        "error_type": (
                            "NativeCommandCancelled"
                            if latest_acknowledgement.status == NativeCommandStatus.CANCELLED
                            else receipt.error_type
                        ),
                    }
                )
        if command is not None:
            receipt = receipt.model_copy(
                update={
                    "command_id": command.command_id,
                    "started_after_revision": command.based_on_revision,
                    "completed_at_revision": observation.world_revision,
                    "causal_revision_advanced": (
                        observation.world_revision.is_later_than(command.based_on_revision)
                    ),
                }
            )
        return Transition(
            receipt=receipt,
            observation=observation,
            terminated=terminated,
            success=None,
            events=observation.events,
        )

    def classify_pointer_action(self, action: Action) -> PointerActionClass:
        """Decide what an action's coordinates depend on.

        Configured semantic skills resolve their position from live control,
        tooltip, or entity bounds re-read inside the input lease, so they are
        resolution-independent. Everything else that emits a pointer primitive
        replays profile coordinates and needs an exact calibration identity.
        """

        if isinstance(action, SkillAction):
            if action.name in self.controls_config.semantic_pointer_skills:
                return PointerActionClass.SEMANTIC_CURRENT
            if not self.macros.has(action.name):
                return PointerActionClass.UNSUPPORTED
            pointer = any(
                isinstance(primitive, (ClickAction, MoveCursorAction, ScrollAction))
                for primitive in self.macros.expand(action)
            )
        else:
            pointer = isinstance(action, (ClickAction, MoveCursorAction, ScrollAction))
        return (
            PointerActionClass.PROFILE_CALIBRATED
            if pointer
            else PointerActionClass.COORDINATE_INDEPENDENT
        )

    def calibration_report(self, action: Action) -> CalibrationReport:
        return evaluate_calibration_identity(
            action_class=self.classify_pointer_action(action),
            expected=self.controls_config.expected_calibration_identity(),
            observed=self.controller.observed_calibration_identity(),
        )

    def _raise_for_calibration(self, report: CalibrationReport) -> None:
        # Preserve the proven exact-client-size message and fail-closed raise for
        # the size case, which existing live evidence and tests depend on.
        if report.observed is not None and (
            "client_width" in report.mismatched_fields
            or "client_height" in report.mismatched_fields
        ):
            validate_expected_client_size(
                report.observed.client_width or 0,
                report.observed.client_height or 0,
                expected_width=self.controls_config.calibrated_client_width,
                expected_height=self.controls_config.calibrated_client_height,
            )
        raise RuntimeError(f"No pointer input was sent. {report.reason}")

    async def _execute_live(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
    ) -> ActionReceipt:
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
                    "Refusing to change Kenshi pause because the current pause state is unknown."
                )
            primitive_count, pause_control = await self._execute_pause_request(action.paused)
            return ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=primitive_count,
                message=(
                    f"Used {pause_control} to request paused={action.paused}. "
                    "A later observation must confirm the state."
                ),
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
                if (
                    self.macros.requires_native_assisted(action.name)
                    and action.name == "approach_confirmed_vendor"
                ):
                    if command is None:
                        raise RuntimeError(
                            "Native command execution requires caller-owned command context."
                        )
                    return await self._execute_native_vendor_approach(
                        action,
                        started,
                        command,
                        pulse_seconds=pulse_seconds,
                    )
                return await self._execute_movement_pulse(
                    action, started, pulse_seconds=pulse_seconds
                )
            return await self._execute_skill(action, started)
        if isinstance(
            action, (KeyAction, HotkeyAction, MoveCursorAction, ClickAction, ScrollAction)
        ):
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
                (KeyAction, HotkeyAction, MoveCursorAction, ClickAction, ScrollAction),
            ):
                raise TypeError(
                    f"Live macro {action.name!r} contains unsupported primitive "
                    f"{macro_primitive.kind!r}."
                )
            primitive_receipt = await self.controller.execute(macro_primitive)
            primitive_count += primitive_receipt.primitive_actions
            messages.append(primitive_receipt.message)
        return primitive_count, messages

    def _pause_primitives(self, paused: bool) -> tuple[list[PrimitiveInputAction], str]:
        skill_name = (
            self.controls_config.pause_skill
            if paused
            else self.controls_config.unpause_skill or self.controls_config.pause_skill
        )
        if skill_name is None:
            return [KeyAction(key=self.controls_config.pause_key)], (
                f"pause key {self.controls_config.pause_key!r}"
            )
        primitives = self.macros.expand(SkillAction(name=skill_name))
        if not primitives or not all(
            isinstance(item, (KeyAction, ClickAction)) for item in primitives
        ):
            raise RuntimeError(
                f"Configured pause control {skill_name!r} must contain only key or click actions."
            )
        pause_primitives: list[PrimitiveInputAction] = []
        pause_primitives.extend(
            item for item in primitives if isinstance(item, (KeyAction, ClickAction))
        )
        return pause_primitives, f"pause control {skill_name!r}"

    async def _execute_pause_request(
        self,
        paused: bool,
        *,
        safety: bool = False,
    ) -> tuple[int, str]:
        primitives, description = self._pause_primitives(paused)
        primitive_count = 0
        for primitive in primitives:
            execute = self.controller.execute_safety if safety else self.controller.execute
            receipt = await execute(primitive)
            primitive_count += receipt.primitive_actions
        return primitive_count, description

    async def _execute_movement_pulse(
        self,
        action: SkillAction,
        started: datetime,
        *,
        pulse_seconds: float,
        prepared_primitives: tuple[int, list[str]] | None = None,
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

        if prepared_primitives is None:
            primitive_count, messages = await self._execute_skill_primitives(action)
        else:
            primitive_count, messages = prepared_primitives
        unpause_sent = False
        emergency_stop = False
        user_interrupted = False
        auto_paused = False
        try:
            unpause_count, _ = await self._execute_pause_request(False)
            unpause_sent = True
            primitive_count += unpause_count
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
                if self._fresh_pause_state() is True:
                    auto_paused = True
                    unpause_sent = False
                    break
                await asyncio.sleep(min(0.1, remaining))
        finally:
            if unpause_sent:
                pause_count, _ = await self._execute_pause_request(True, safety=True)
                primitive_count += pause_count
                if not await self._wait_for_pause_state(True):
                    if self._fresh_pause_state() is False:
                        retry_count, _ = await self._execute_pause_request(True, safety=True)
                        primitive_count += retry_count
                    if not await self._wait_for_pause_state(True):
                        raise RuntimeError(
                            "Movement pulse ended but Kenshi did not confirm re-paused state."
                        )

        if emergency_stop:
            raise RuntimeError("Emergency stop ended the movement pulse after re-pausing Kenshi.")
        if user_interrupted:
            outcome = "Human input ended the pulse; confirmed re-paused state and yielded control."
        elif auto_paused:
            outcome = "Kenshi auto-paused during the pulse; preserved the paused state."
        else:
            outcome = f"Advanced Kenshi for {pulse_seconds:.2f}s and confirmed re-paused state."
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=primitive_count,
            message=(f"Executed skill {action.name!r}. {outcome} " + " ".join(messages)),
        )

    async def _execute_native_vendor_approach(
        self,
        action: SkillAction,
        started: datetime,
        command: CommandDispatchContext,
        *,
        pulse_seconds: float,
    ) -> ActionReceipt:
        request = self._native_vendor_request(action, command)
        request_path = self.telemetry_reader.path.parent / self._NATIVE_COMMAND_REQUEST_FILE
        write_native_command_request_atomic(request_path, request)
        primitive_count, messages = await self._execute_skill_primitives(action)
        acknowledgement = await self._wait_for_native_acknowledgement(request)
        acknowledgement_message = (
            f"Native acknowledgement {acknowledgement.status.value!r} "
            f"for {acknowledgement.command_id}: {acknowledgement.reason}."
        )
        messages.append(acknowledgement_message)

        if acknowledgement.status == NativeCommandStatus.REJECTED:
            return ActionReceipt(
                action=action,
                command_id=command.command_id,
                started_after_revision=command.based_on_revision,
                accepted=False,
                executed=False,
                dry_run=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=primitive_count,
                message=" ".join(messages),
                error_type="NativeCommandRejected",
                native_acknowledgement=acknowledgement,
            )
        if acknowledgement.status in {
            NativeCommandStatus.CANCELLED,
            NativeCommandStatus.COMPLETED,
        }:
            return ActionReceipt(
                action=action,
                command_id=command.command_id,
                started_after_revision=command.based_on_revision,
                accepted=True,
                executed=True,
                dry_run=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=primitive_count,
                message=" ".join(messages),
                error_type=(
                    "NativeCommandCancelled"
                    if acknowledgement.status == NativeCommandStatus.CANCELLED
                    else None
                ),
                native_acknowledgement=acknowledgement,
            )
        receipt = await self._execute_movement_pulse(
            action,
            started,
            pulse_seconds=pulse_seconds,
            prepared_primitives=(primitive_count, messages),
        )
        return receipt.model_copy(update={"native_acknowledgement": acknowledgement})

    def _native_vendor_request(
        self,
        action: SkillAction,
        command: CommandDispatchContext,
    ) -> NativeCommandRequest:
        observation = self._last_observation
        if observation is None or observation.telemetry is None:
            raise RuntimeError("Native command requires a current telemetry observation.")
        if observation.telemetry_stale:
            raise RuntimeError("Native command requires fresh telemetry.")
        if not observation.world_revision.same_telemetry_snapshot_as(
            command.based_on_revision
        ):
            raise RuntimeError(
                "Native command basis does not match the current telemetry snapshot."
            )
        telemetry = observation.telemetry
        required_capabilities = {
            "control.approach_vendor",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        }
        missing = required_capabilities - set(telemetry.capabilities)
        if missing:
            raise RuntimeError(
                "Native command lacks required capabilities: " + ", ".join(sorted(missing))
            )
        if not telemetry.identity_session_id:
            raise RuntimeError("Native command requires a current identity session.")
        selected_ids = telemetry.ui.selected_character_ids
        if len(selected_ids) != 1 or telemetry.ui.selected_character_id != selected_ids[0]:
            raise RuntimeError("Native command requires one exact primary selection.")
        target_id = action.argument_map().get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise RuntimeError("Native vendor approach requires an exact target_id.")
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == target_id),
            None,
        )
        if target is None:
            raise RuntimeError("Native command target is absent from current nearby telemetry.")
        if (
            target.is_animal is not False
            or target.has_vendor_list is not True
            or target.is_squad_leader is not True
            or target.has_dialogue is not True
            or target.conscious is not True
            or target.disposition.value not in {"friendly", "neutral"}
        ):
            raise RuntimeError("Native command target lacks exact safe current vendor evidence.")
        return NativeCommandRequest(
            schema_version="1.0",
            command_id=command.command_id,
            command="approach_confirmed_vendor",
            control_mode=ControlMode.NATIVE_ASSISTED,
            identity_session_id=telemetry.identity_session_id,
            based_on_revision=command.based_on_revision,
            selected_character_ids=list(selected_ids),
            target_id=target_id,
        )

    async def _wait_for_native_acknowledgement(
        self,
        request: NativeCommandRequest,
    ) -> NativeCommandAcknowledgement:
        basis = request.based_on_revision.telemetry_sequence
        assert basis is not None
        deadline = time.monotonic() + self._NATIVE_COMMAND_ACK_TIMEOUT_SECONDS
        while True:
            try:
                result = self.telemetry_reader.read()
            except TelemetryReadError:
                result = None
            if result is not None and not result.stale:
                snapshot = result.snapshot
                if snapshot.identity_session_id != request.identity_session_id:
                    raise RuntimeError(
                        "Native identity session changed while awaiting acknowledgement."
                    )
                acknowledgement = snapshot.native_control.acknowledgement_for(request.command_id)
                if acknowledgement is not None and snapshot.sequence > basis:
                    if (
                        acknowledgement.based_on_telemetry_sequence != basis
                        or acknowledgement.target_id != request.target_id
                        or acknowledgement.selected_character_ids != request.selected_character_ids
                    ):
                        raise RuntimeError(
                            "Matching native acknowledgement violated request fences."
                        )
                    if acknowledgement.acknowledged_at_telemetry_sequence > snapshot.sequence:
                        raise RuntimeError(
                            "Native acknowledgement claims a future telemetry sequence."
                        )
                    return acknowledgement
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Timed out without a causally later matching native acknowledgement."
                )
            await asyncio.sleep(min(self._NATIVE_COMMAND_POLL_SECONDS, remaining))

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
