from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..action_contracts import (
    ACTIVATE_VISIBLE_CONTROL_CONTRACT,
    APPROACH_DIALOGUE_TARGET_CONTRACT,
    DISMISS_SCREEN_CONTRACT,
    EQUIP_ITEM_CONTRACT,
    EXIT_CURRENT_BUILDING_CONTRACT,
    MOVE_IN_DIRECTION_CONTRACT,
    MOVE_TO_CHARACTER_CONTRACT,
    NATIVE_APPROACH_WIRE_COMMAND,
    NATIVE_DIRECTION_WIRE_COMMAND,
    NATIVE_EXIT_BUILDING_WIRE_COMMAND,
    NATIVE_MOVE_WIRE_COMMAND,
    NATIVE_OPERATE_RESOURCE_WIRE_COMMAND,
    PERFORM_CONTEXT_ACTION_CONTRACT,
    PURCHASE_ITEM_CONTRACT,
    RECOVER_CAMERA_VIEW_CONTRACT,
    SCROLL_SCREEN_CONTRACT,
    SELL_ITEM_CONTRACT,
    USE_GAME_BINDING_CONTRACT,
    ActionContract,
    ReferenceBinding,
    contract_for,
)
from ..camera_recovery import score_camera_observation
from ..config import CaptureConfig, ControlsConfig, RuntimeConfig
from ..control.base import InputController, PrimitiveInputAction
from ..control.calibration import (
    calibration_allows_input,
    evaluate_calibration_identity,
    validate_expected_client_size,
)
from ..control.capture import WindowCapture
from ..final_safe_state import (
    FinalSafeStateOutcome,
    FinalSafeStateStatus,
    ensure_final_safe_state,
)
from ..input_boundary import ExecutionToken
from ..models import (
    GAME_BINDING_KEYS,
    Action,
    ActionReceipt,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    CalibrationReport,
    CameraFrameScore,
    CameraRecoveryEvidence,
    CameraRecoveryStatus,
    ClickAction,
    CommandDispatchContext,
    ControlMode,
    DismissScreenAction,
    EquipItemAction,
    ExitCurrentBuildingAction,
    HotkeyAction,
    InputBoundaryDecision,
    KeyAction,
    MouseButton,
    MoveCursorAction,
    MoveInDirectionAction,
    MoveToCharacterAction,
    NativeCommandAcknowledgement,
    NativeCommandRequest,
    NativeCommandStatus,
    NativeControlState,
    NoopAction,
    NormalizedPointerBounds,
    Observation,
    PauseAction,
    PerformContextAction,
    PointerActionClass,
    PurchaseItemAction,
    RecoverCameraViewAction,
    ScrollAction,
    ScrollScreenAction,
    SellItemAction,
    SemanticActionReceipt,
    SetSpeedAction,
    SkillAction,
    SkillArgument,
    StopAction,
    TelemetrySnapshot,
    Transition,
    UseGameBindingAction,
    WaitAction,
    WorldStateRevision,
    window_close_point,
)
from ..native_commands import write_native_command_request_atomic
from ..skills import MacroRegistry
from ..telemetry import TelemetryReader, TelemetryReadError
from .base import AgentEnvironment


class LiveEnvironment(AgentEnvironment):
    _NATIVE_COMMAND_REQUEST_FILE = "native_command.request.json"
    _NATIVE_COMMAND_ACK_TIMEOUT_SECONDS = 2.0
    _NATIVE_COMMAND_POLL_SECONDS = 0.025
    _NATIVE_DIALOGUE_SETTLE_SECONDS = 1.0

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
        final_pause_timeout_seconds: float = 2.0,
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
        self.final_pause_timeout_seconds = final_pause_timeout_seconds
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
            available_skills=self.available_skills,
            skill_specs=[self.macros.spec(name) for name in self.available_skills],
        )
        self._last_observation = observation
        return observation

    def _apply_control_mode(self, snapshot: TelemetrySnapshot) -> TelemetrySnapshot:
        """Withhold native-control evidence that `interface_only` may not use."""

        capabilities = list(snapshot.capabilities)
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
                "native_control": NativeControlState(),
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
            1
            if capability_signature != self._last_capability_signature
            else 0
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

        # A contracted action declares its own pointer class, so a semantic
        # action never inherits a calibrated profile requirement from the macro
        # whose primitives it happens to reuse.
        contract = contract_for(action)
        if contract is not None:
            return contract.pointer_class
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
        if isinstance(action, ApproachDialogueTargetAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_semantic_approach(action, started, command)
        if isinstance(action, PerformContextAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_context_action(action, started, command)
        if isinstance(action, MoveInDirectionAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_directional_move(action, started, command)
        if isinstance(action, ExitCurrentBuildingAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_exit_current_building(action, started, command)
        if isinstance(action, MoveToCharacterAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_semantic_move(action, started, command)
        if isinstance(action, ActivateVisibleControlAction):
            return await self._execute_visible_control(action, started)
        if isinstance(action, DismissScreenAction):
            return await self._execute_dismiss_screen(action, started)
        if isinstance(action, PurchaseItemAction):
            return await self._execute_purchase_item(action, started)
        if isinstance(action, RecoverCameraViewAction):
            return await self._execute_recover_camera_view(action, started)
        if isinstance(action, UseGameBindingAction):
            return await self._execute_game_binding(action, started)
        if isinstance(action, ScrollScreenAction):
            return await self._execute_scroll_screen(action, started)
        if isinstance(action, SellItemAction):
            return await self._execute_sell_item(action, started)
        if isinstance(action, EquipItemAction):
            return await self._execute_equip_item(action, started)
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
                    target_id = action.argument_map().get("target_id")
                    if not isinstance(target_id, str) or not target_id:
                        raise RuntimeError(
                            "Native vendor approach requires an exact target_id."
                        )
                    return await self._execute_native_approach(
                        action,
                        started,
                        command,
                        target_id=target_id,
                        pulse_seconds=pulse_seconds,
                        primitive_skill=action,
                        require_vendor_role=True,
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
        if self.controls_config.require_paused_between_actions and paused is not True:
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

    async def _execute_semantic_approach(
        self,
        action: ApproachDialogueTargetAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Issue one exact native talk-to order through the Kenshi bridge.

        The action never converts a projected world position into a pointer
        click. Kenshi receives `PLAYER_TALK_TO` for the stable entity handle,
        which can open nearby dialogue while paused and paths when movement is
        actually necessary.
        """

        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                "Semantic approach requires a configured native approach skill to "
                "supply its bounded primitives."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value=action.target_id)],
        )
        pulse_seconds = self.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=APPROACH_DIALOGUE_TARGET_CONTRACT.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Bound to the exact stable dialogue target and issued at most one "
                "native PLAYER_TALK_TO order for this option lifecycle."
            ),
        )
        return await self._execute_native_approach(
            action,
            started,
            command,
            target_id=action.target_id,
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
        )

    async def _execute_context_action(
        self,
        action: PerformContextAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Issue one reviewed default task on one exact observed world object."""

        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                "Context actions require a configured native transport skill."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value=action.target_id)],
        )
        pulse_seconds = self.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native transport skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=PERFORM_CONTEXT_ACTION_CONTRACT.version,
            target_id=action.target_id,
            resolved_label=action.context_action.value,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact advertised world object/action pair and delegated "
                "the reviewed Kenshi default task plus terminal AI-goal proof to "
                "native code."
            ),
        )
        return await self._execute_native_approach(
            action,
            started,
            command,
            target_id=action.target_id,
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=NATIVE_OPERATE_RESOURCE_WIRE_COMMAND,
            require_dialogue_target=False,
        )

    async def _execute_directional_move(
        self,
        action: MoveInDirectionAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Walk a bearing and distance from wherever the character stands.

        The one movement that names nobody, so the one that still works
        somewhere empty. Everything else about the lifecycle - the bounded
        primitives, the acknowledgement, the monitored walk - is the approach's.
        """

        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                "Directional movement requires a configured native approach skill "
                "to supply its bounded primitives."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value="")],
        )
        pulse_seconds = self.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=MOVE_IN_DIRECTION_CONTRACT.version,
            source_revision=command.based_on_revision,
            revalidation=(
                f"Ordered a walk of {action.distance_units:.0f} units on bearing "
                f"{action.bearing_degrees:.0f} from the selected character's own "
                "position."
            ),
        )
        return await self._execute_native_approach(
            action,
            started,
            command,
            target_id="",
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=NATIVE_DIRECTION_WIRE_COMMAND,
            require_dialogue_target=False,
            bearing_degrees=action.bearing_degrees,
            distance_units=action.distance_units,
        )

    async def _execute_exit_current_building(
        self,
        action: ExitCurrentBuildingAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Ask native code to resolve and traverse the current building's door."""

        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                "Building exit requires a configured native approach skill to "
                "supply its bounded transport primitive."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value="")],
        )
        pulse_seconds = self.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=EXIT_CURRENT_BUILDING_CONTRACT.version,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-proved one selected character indoors, then delegated door "
                "choice, outdoor destination, and terminal judgment to native code."
            ),
        )
        return await self._execute_native_approach(
            action,
            started,
            command,
            target_id="",
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=NATIVE_EXIT_BUILDING_WIRE_COMMAND,
            require_dialogue_target=False,
        )

    async def _execute_semantic_move(
        self,
        action: MoveToCharacterAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Walk to one exact observed character without opening dialogue.

        Shares the approach's bounded primitives and pulse timing, which are
        proven, and differs in exactly two ways: the destination need not be
        talkable, and the native order is a move rather than a talk-to, so
        arriving starts no conversation.
        """

        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                "Semantic move requires a configured native approach skill to "
                "supply its bounded primitives."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value=action.target_id)],
        )
        pulse_seconds = self.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=MOVE_TO_CHARACTER_CONTRACT.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Bound to the exact stable nearby character and issued at most one "
                "native move order for this option lifecycle."
            ),
        )
        return await self._execute_native_approach(
            action,
            started,
            command,
            target_id=action.target_id,
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=NATIVE_MOVE_WIRE_COMMAND,
            require_dialogue_target=False,
        )

    async def _execute_visible_control(
        self,
        action: ActivateVisibleControlAction,
        started: datetime,
    ) -> ActionReceipt:
        """Click exactly one currently advertised control, re-resolved in-lease.

        This runs inside the acquired input lease, after the generic input
        boundary already revalidated the plan's typed authority. What is checked
        here is the part only this action knows: that the exact label, role,
        uniqueness, and bounds it bound to are still what the interface reports.
        Any drift emits zero input.
        """

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "No input was sent: telemetry became stale inside the input lease."
            )
        observation = self._observation_from_snapshot(result.snapshot)
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(action, observation)
        if not binding.bound or binding.resolved_bounds is None:
            raise RuntimeError(
                f"No input was sent: {binding.reason}"
            )
        bounds = binding.resolved_bounds
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        primitive_receipt = await self.controller.execute(
            ClickAction(
                x=x,
                y=y,
                hold_seconds=self.controls_config.control_activation_hold_seconds,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=ACTIVATE_VISIBLE_CONTROL_CONTRACT.version,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-resolved to exactly one current control inside the input lease "
                f"before the click. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Activated the current {binding.resolved_role} control "
                    f"{binding.resolved_label!r} at its observed bounds. "
                    "A later observation must confirm the resulting transition."
                ),
            }
        )

    async def _execute_purchase_item(
        self,
        action: PurchaseItemAction,
        started: datetime,
    ) -> ActionReceipt:
        """Buy the item in one cell, re-proving its tooltip inside the lease.

        The binding is re-run here rather than trusted from validation time,
        because the whole guarantee is that the tooltip on screen at the instant
        of the click still names this item at this price.
        """

        binding, observation = self._rebind_in_lease(PURCHASE_ITEM_CONTRACT, action)
        bounds = binding.resolved_bounds
        assert bounds is not None
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        await self.controller.execute(MoveCursorAction(x=x, y=y))
        if self.controls_config.item_cell_hover_seconds:
            await asyncio.sleep(self.controls_config.item_cell_hover_seconds)
        primitive_receipt = await self.controller.execute(
            ClickAction(
                x=x,
                y=y,
                button=MouseButton.RIGHT,
                hold_seconds=self.controls_config.control_activation_hold_seconds,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=PURCHASE_ITEM_CONTRACT.version,
            target_id=action.seller_id,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-proved the cell and its own tooltip inside the input lease "
                f"before buying. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Sent the buy gesture at cell {binding.resolved_label!r} for "
                    f"{action.item_name!r}. This is not proof of a purchase: only a "
                    "later observation showing money spent and the item carried is. "
                    "Check both before buying again."
                ),
            }
        )

    def _rebind_in_lease(
        self,
        contract: ActionContract,
        action: Action,
    ) -> tuple[ReferenceBinding, Observation]:
        """Re-resolve an action's reference against telemetry read right now."""

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "No input was sent: telemetry became stale inside the input lease."
            )
        observation = self._observation_from_snapshot(result.snapshot)
        binding = contract.bind(action, observation)
        if not binding.bound or binding.resolved_bounds is None:
            raise RuntimeError(f"No input was sent: {binding.reason}")
        return binding, observation

    def _ensure_camera_recovery_can_continue(self) -> None:
        if self.controller.emergency_stop_pressed(self.emergency_stop_key):
            raise RuntimeError(
                "Emergency stop interrupted camera recovery; no further input was sent."
            )
        if self.controller.user_input_detected():
            raise RuntimeError(
                "Human input interrupted camera recovery; no further input was sent."
            )

    async def _camera_recovery_primitive(
        self,
        primitive: PrimitiveInputAction,
        *,
        safety: bool = False,
    ) -> int:
        self._ensure_camera_recovery_can_continue()
        execute = self.controller.execute_safety if safety else self.controller.execute
        await execute(primitive)
        # One call accepts exactly one ControllerPrimitive. Count the semantic
        # primitive, not backend bookkeeping inside its receipt.
        return 1

    async def _capture_camera_candidate(
        self,
        action: RecoverCameraViewAction,
        *,
        candidate: str,
    ) -> tuple[CameraFrameScore, ReferenceBinding, Observation]:
        """Retain and score one causally current frame inside the input lease."""

        if self._capture is None:
            raise RuntimeError("Camera recovery requires an enabled capture backend.")
        settle = self.controls_config.camera_recovery.candidate_settle_seconds
        if settle:
            await asyncio.sleep(settle)
        self._ensure_camera_recovery_can_continue()
        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "Camera recovery stopped because telemetry became stale before capture."
            )
        snapshot = self._apply_control_mode(result.snapshot)
        self._capture_sequence += 1
        frame = self._capture.capture(self._capture_sequence)
        snapshot = snapshot.model_copy(
            update={
                "ui": snapshot.ui.model_copy(
                    update={"client_width": frame.width, "client_height": frame.height}
                )
            }
        )
        observation = Observation(
            run_id=self.run_id,
            step_index=self._step_index,
            mode="live",
            control_mode=self.control_mode,
            world_revision=WorldStateRevision(
                telemetry_sequence=snapshot.sequence,
                frame_sequence=self._capture_sequence,
                capability_epoch=self._capability_epoch,
                observed_at_monotonic=time.monotonic(),
            ),
            telemetry=snapshot,
            telemetry_stale=False,
            telemetry_age_seconds=result.age_seconds,
            screenshot_path=frame.path,
            screenshot_sha256=frame.sha256,
            objective=self.runtime_config.objective,
            available_skills=self.available_skills,
            skill_specs=[self.macros.spec(name) for name in self.available_skills],
        )
        binding = RECOVER_CAMERA_VIEW_CONTRACT.bind(action, observation)
        if not binding.bound or binding.floor is None:
            raise RuntimeError(
                "Camera recovery stopped because its selected-character/HUD binding "
                f"changed: {binding.reason}"
            )
        recovery = self.controls_config.camera_recovery
        score = score_camera_observation(
            observation,
            candidate=candidate,
            floor=binding.floor,
            clear_score_threshold=recovery.clear_score_threshold,
            anchor_max_distance=recovery.anchor_max_distance,
        )
        return score, binding, observation

    @staticmethod
    def _camera_click(
        bounds: object,
        *,
        clicks: int = 1,
        hold_seconds: float,
        interval_seconds: float = 0.08,
    ) -> ClickAction:
        if not isinstance(bounds, NormalizedPointerBounds):
            raise RuntimeError("Camera recovery lost a required semantic control bound.")
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        return ClickAction(
            x=x,
            y=y,
            clicks=clicks,
            hold_seconds=hold_seconds,
            interval_seconds=interval_seconds,
        )

    async def _execute_recover_camera_view(
        self,
        action: RecoverCameraViewAction,
        started: datetime,
    ) -> ActionReceipt:
        """Run the fixed follow/floor/zoom/orbit/tilt recovery transaction."""

        recovery = self.controls_config.camera_recovery
        candidates: list[CameraFrameScore] = []
        primitive_count = 0
        paused_for_recovery = False

        initial, binding, observation = await self._capture_camera_candidate(
            action, candidate="initial"
        )
        candidates.append(initial)
        assert binding.target_id is not None
        assert binding.selected_character_name is not None
        assert binding.floor is not None
        selected_character_id = binding.target_id
        selected_character_name = binding.selected_character_name
        initial_floor = binding.floor

        def finish(
            status: CameraRecoveryStatus,
            chosen: CameraFrameScore,
            *,
            follow_method: Literal["already_anchored", "portrait_double_click"],
        ) -> ActionReceipt:
            if primitive_count > RECOVER_CAMERA_VIEW_CONTRACT.max_primitive_actions:
                raise RuntimeError(
                    "Camera recovery exceeded its authoritative primitive bound."
                )
            evidence = CameraRecoveryEvidence(
                status=status,
                selected_character_id=selected_character_id,
                selected_character_name=selected_character_name,
                initial_floor=initial_floor,
                final_floor=chosen.floor,
                clear_score_threshold=recovery.clear_score_threshold,
                anchor_max_distance=recovery.anchor_max_distance,
                paused_for_recovery=paused_for_recovery,
                primitive_actions=primitive_count,
                follow_method=follow_method,
                chosen_candidate=chosen.candidate,
                candidates=candidates,
            )
            semantic = SemanticActionReceipt(
                action_kind=action.kind,
                contract_version=RECOVER_CAMERA_VIEW_CONTRACT.version,
                target_id=selected_character_id,
                resolved_label=selected_character_name,
                resolved_role="selected_character",
                resolved_bounds=binding.resolved_bounds,
                source_revision=binding.source_revision,
                revalidation=(
                    "Controller re-bound the selected character, portrait, floor "
                    "controls, fresh telemetry, and every retained candidate frame "
                    "inside one input lease."
                ),
                camera_recovery=evidence,
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
                    f"Camera recovery returned {status.value!r}; chose "
                    f"{chosen.candidate!r} on floor {chosen.floor} with score "
                    f"{chosen.score:.3f} after {primitive_count} input primitives."
                ),
                semantic=semantic,
            )

        if initial.clear:
            return finish(
                CameraRecoveryStatus.ALREADY_CLEAR,
                initial,
                follow_method="already_anchored",
            )

        paused = observation.telemetry.game.paused if observation.telemetry is not None else None
        if paused is None:
            raise RuntimeError(
                "Camera recovery requires a known pause state before it can emit input."
            )
        if paused is False:
            pause_primitives, _ = self._pause_primitives(True)
            if len(pause_primitives) != 1:
                raise RuntimeError(
                    "Camera recovery requires the configured pause control to "
                    "expand to exactly one primitive so its fifteen-primitive "
                    "transaction bound remains invariant."
                )
            for pause_primitive in pause_primitives:
                primitive_count += await self._camera_recovery_primitive(
                    pause_primitive, safety=True
                )
            if not await self._wait_for_pause_state(True):
                raise RuntimeError(
                    "Camera recovery requested pause but Kenshi did not confirm it; "
                    "no camera input followed."
                )
            paused_for_recovery = True

        # Kenshi's stable follow gesture: double-click the currently selected
        # character's lower-HUD portrait. This also brings the view back to that
        # character's building context before floor search.
        primitive_count += await self._camera_recovery_primitive(
            self._camera_click(
                binding.resolved_bounds,
                clicks=2,
                hold_seconds=recovery.portrait_click_hold_seconds,
                interval_seconds=recovery.portrait_click_interval_seconds,
            )
        )
        followed, binding, _ = await self._capture_camera_candidate(
            action, candidate="portrait_follow"
        )
        candidates.append(followed)
        if followed.clear:
            return finish(
                CameraRecoveryStatus.RECOVERED,
                followed,
                follow_method="portrait_double_click",
            )

        floor_candidates = [followed]
        current_floor = followed.floor
        for floor_attempt in range(1, recovery.max_lower_floors + 1):
            previous_floor = current_floor
            primitive_count += await self._camera_recovery_primitive(
                self._camera_click(
                    binding.floor_down_bounds,
                    hold_seconds=recovery.floor_click_hold_seconds,
                )
            )
            lowered, binding, _ = await self._capture_camera_candidate(
                action, candidate=f"floor_down_{floor_attempt}"
            )
            candidates.append(lowered)
            floor_candidates.append(lowered)
            current_floor = lowered.floor
            if lowered.clear:
                return finish(
                    CameraRecoveryStatus.RECOVERED,
                    lowered,
                    follow_method="portrait_double_click",
                )
            if current_floor >= previous_floor:
                # Bottom floor or a swallowed click. Continuing would only
                # repeat the same evidence.
                break

        best_floor_frame = max(
            floor_candidates,
            key=lambda item: (item.clear, item.score),
        )
        target_floor = best_floor_frame.floor
        restored_frame: CameraFrameScore | None = None
        while current_floor < target_floor:
            primitive_count += await self._camera_recovery_primitive(
                self._camera_click(
                    binding.floor_up_bounds,
                    hold_seconds=recovery.floor_click_hold_seconds,
                )
            )
            restored, binding, _ = await self._capture_camera_candidate(
                action, candidate=f"restore_floor_{best_floor_frame.floor}"
            )
            candidates.append(restored)
            if restored.floor <= current_floor:
                raise RuntimeError(
                    "Camera recovery could not restore the chosen floor; stopped "
                    "rather than emitting another blind floor click."
                )
            current_floor = restored.floor
            restored_frame = restored
        if restored_frame is not None:
            best_floor_frame = restored_frame
        if best_floor_frame.clear:
            return finish(
                CameraRecoveryStatus.RECOVERED,
                best_floor_frame,
                follow_method="portrait_double_click",
            )

        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.zoom_out_key,
                hold_seconds=recovery.zoom_out_hold_seconds,
            )
        )
        zoomed, binding, _ = await self._capture_camera_candidate(
            action, candidate="zoom_out"
        )
        candidates.append(zoomed)
        if zoomed.clear:
            return finish(
                CameraRecoveryStatus.RECOVERED,
                zoomed,
                follow_method="portrait_double_click",
            )

        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.rotate_right_key,
                hold_seconds=recovery.orbit_hold_seconds,
            )
        )
        orbit_right, binding, _ = await self._capture_camera_candidate(
            action, candidate="orbit_right"
        )
        candidates.append(orbit_right)

        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.rotate_left_key,
                hold_seconds=recovery.orbit_hold_seconds * 2.0,
            )
        )
        orbit_left, binding, _ = await self._capture_camera_candidate(
            action, candidate="orbit_left"
        )
        candidates.append(orbit_left)

        # Return to the zoomed baseline, then apply exactly one fixed offset if
        # a side candidate scored better. This makes the final camera position
        # a deterministic function of the three retained candidates.
        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.rotate_right_key,
                hold_seconds=recovery.orbit_hold_seconds,
            )
        )
        chosen_angle = max(
            (zoomed, orbit_right, orbit_left),
            key=lambda item: (item.clear, item.score),
        )
        if chosen_angle is orbit_right:
            primitive_count += await self._camera_recovery_primitive(
                KeyAction(
                    key=recovery.rotate_right_key,
                    hold_seconds=recovery.orbit_hold_seconds,
                )
            )
        elif chosen_angle is orbit_left:
            primitive_count += await self._camera_recovery_primitive(
                KeyAction(
                    key=recovery.rotate_left_key,
                    hold_seconds=recovery.orbit_hold_seconds,
                )
            )

        final_angle, binding, _ = await self._capture_camera_candidate(
            action, candidate=f"angle_{chosen_angle.candidate}"
        )
        candidates.append(final_angle)
        if final_angle.clear:
            return finish(
                CameraRecoveryStatus.RECOVERED,
                final_angle,
                follow_method="portrait_double_click",
            )

        # The persistent camera-distance lock can make End intentionally inert.
        # Tilt remains follow-preserving, so compare one symmetric comma/period
        # sequence around the selected orbit without giving the planner a new
        # adjustment loop.
        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.tilt_up_key,
                hold_seconds=recovery.tilt_hold_seconds,
            )
        )
        tilt_up, binding, _ = await self._capture_camera_candidate(
            action, candidate="tilt_up"
        )
        candidates.append(tilt_up)

        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.tilt_down_key,
                hold_seconds=recovery.tilt_hold_seconds * 2.0,
            )
        )
        tilt_down, binding, _ = await self._capture_camera_candidate(
            action, candidate="tilt_down"
        )
        candidates.append(tilt_down)

        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.tilt_up_key,
                hold_seconds=recovery.tilt_hold_seconds,
            )
        )
        chosen_tilt = max(
            (final_angle, tilt_up, tilt_down),
            key=lambda item: (item.clear, item.score),
        )
        if chosen_tilt is tilt_up:
            primitive_count += await self._camera_recovery_primitive(
                KeyAction(
                    key=recovery.tilt_up_key,
                    hold_seconds=recovery.tilt_hold_seconds,
                )
            )
        elif chosen_tilt is tilt_down:
            primitive_count += await self._camera_recovery_primitive(
                KeyAction(
                    key=recovery.tilt_down_key,
                    hold_seconds=recovery.tilt_hold_seconds,
                )
            )

        final_frame, binding, _ = await self._capture_camera_candidate(
            action, candidate=f"final_{chosen_tilt.candidate}"
        )
        candidates.append(final_frame)
        status = (
            CameraRecoveryStatus.RECOVERED
            if final_frame.clear
            else CameraRecoveryStatus.FAILED_AFTER_BOUNDED_ATTEMPTS
        )
        return finish(
            status,
            final_frame,
            follow_method="portrait_double_click",
        )

    async def _execute_game_binding(
        self,
        action: UseGameBindingAction,
        started: datetime,
    ) -> ActionReceipt:
        """Send the key Kenshi itself binds to this control.

        Re-checks inside the lease that a game is still loaded, because a key
        pressed at a loading screen is swallowed with no evidence either way -
        the silent failure this action exists to replace.
        """

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "No input was sent: telemetry became stale inside the input lease."
            )
        observation = self._observation_from_snapshot(result.snapshot)
        binding = USE_GAME_BINDING_CONTRACT.bind(action, observation)
        if not binding.bound:
            raise RuntimeError(f"No input was sent: {binding.reason}")
        key = GAME_BINDING_KEYS[action.binding]
        primitive_receipt = await self.controller.execute(KeyAction(key=key))
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=USE_GAME_BINDING_CONTRACT.version,
            resolved_label=action.binding.value,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-confirmed a game was loaded inside the input lease before "
                f"pressing the key. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Pressed Kenshi's {action.binding.value!r} binding ({key!r}), "
                    f"expecting: {action.expected_effect}. A later observation "
                    "must confirm the transition."
                ),
            }
        )

    async def _execute_scroll_screen(
        self,
        action: ScrollScreenAction,
        started: datetime,
    ) -> ActionReceipt:
        """Scroll at the centre of one window's own observed bounds.

        Re-resolves the window inside the lease, because a window that closed
        during the polite wait would otherwise have its scroll delivered to
        whatever is behind it.
        """

        binding, observation = self._rebind_in_lease(SCROLL_SCREEN_CONTRACT, action)
        bounds = binding.resolved_bounds
        assert bounds is not None
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        primitive_receipt = await self.controller.execute(
            ScrollAction(x=x, y=y, notches=action.notches)
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=SCROLL_SCREEN_CONTRACT.version,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                f"Re-resolved window {action.window!r} inside the input lease. "
                f"{binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Scrolled {action.notches:+d} notches inside {action.window!r}. "
                    "A later observation must report the newly visible controls."
                ),
            }
        )

    async def _execute_sell_item(
        self,
        action: SellItemAction,
        started: datetime,
    ) -> ActionReceipt:
        """Sell one cell from our own inventory, re-proving ownership in-lease.

        Right-click is Kenshi's own auto-trade gesture (`RClickAutoTrade`), the
        same primitive a purchase uses; what makes this a sale rather than a
        purchase is entirely which inventory the cell belongs to, so that is
        re-checked here rather than trusted from validation time.
        """

        binding, observation = self._rebind_in_lease(SELL_ITEM_CONTRACT, action)
        bounds = binding.resolved_bounds
        assert bounds is not None
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        await self.controller.execute(MoveCursorAction(x=x, y=y))
        if self.controls_config.item_cell_hover_seconds:
            await asyncio.sleep(self.controls_config.item_cell_hover_seconds)
        primitive_receipt = await self.controller.execute(
            ClickAction(
                x=x,
                y=y,
                button=MouseButton.RIGHT,
                hold_seconds=self.controls_config.control_activation_hold_seconds,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=SELL_ITEM_CONTRACT.version,
            target_id=action.buyer_id,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-proved the cell belongs to the selected character's own "
                f"inventory inside the input lease. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Sold {action.item_name!r} from {action.window!r} to "
                    f"{action.buyer_id}. A later observation must confirm the "
                    "money and inventory change."
                ),
            }
        )

    async def _execute_equip_item(
        self,
        action: EquipItemAction,
        started: datetime,
    ) -> ActionReceipt:
        """Right-click one of our own cells to equip it, with no trade open.

        The in-lease re-bind matters more here than anywhere else: the contract
        refuses while a trade is open because the identical gesture sells
        instead, and a trade window that opened during the polite wait would
        turn this equip into a sale.
        """

        binding, observation = self._rebind_in_lease(EQUIP_ITEM_CONTRACT, action)
        bounds = binding.resolved_bounds
        assert bounds is not None
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        await self.controller.execute(MoveCursorAction(x=x, y=y))
        if self.controls_config.item_cell_hover_seconds:
            await asyncio.sleep(self.controls_config.item_cell_hover_seconds)
        primitive_receipt = await self.controller.execute(
            ClickAction(
                x=x,
                y=y,
                button=MouseButton.RIGHT,
                hold_seconds=self.controls_config.control_activation_hold_seconds,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=EQUIP_ITEM_CONTRACT.version,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-proved no trade was open inside the input lease, so this "
                f"right-click equips rather than sells. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Equipped {action.item_name!r} from {action.window!r}. A later "
                    "observation must confirm the equipped gear change."
                ),
            }
        )

    async def _execute_dismiss_screen(
        self,
        action: DismissScreenAction,
        started: datetime,
    ) -> ActionReceipt:
        """Back out of the currently open screen with one configured key.

        Re-checks inside the lease that the screen the planner named is still
        the one that is open, so a screen that changed during the polite wait
        cannot be closed by a stale intention.
        """

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "No input was sent: telemetry became stale inside the input lease."
            )
        observation = self._observation_from_snapshot(result.snapshot)
        binding = DISMISS_SCREEN_CONTRACT.bind(action, observation)
        if not binding.bound:
            raise RuntimeError(f"No input was sent: {binding.reason}")
        if binding.resolved_bounds is not None:
            # A window closes by its own close box. Escape does not close
            # Kenshi's inventory or trade windows at all - with nothing else
            # open it opens the ESC menu instead.
            close_x, close_y = window_close_point(binding.resolved_bounds)
            primitive_receipt = await self.controller.execute(
                ClickAction(
                    x=close_x,
                    y=close_y,
                    hold_seconds=self.controls_config.control_activation_hold_seconds,
                )
            )
        else:
            primitive_receipt = await self.controller.execute(
                KeyAction(key=self.controls_config.dismiss_screen_key)
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=DISMISS_SCREEN_CONTRACT.version,
            resolved_label=binding.resolved_label,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-confirmed the expected screen was still open inside the input "
                f"lease before dismissing it. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    (
                        f"Closed the {action.window!r} window on the "
                        f"{action.expected_screen!r} screen via its own close box."
                        if binding.resolved_bounds is not None
                        else f"Dismissed the current {action.expected_screen!r} screen "
                        f"with the configured "
                        f"{self.controls_config.dismiss_screen_key!r} key."
                    )
                    + " A later observation must confirm the transition."
                ),
            }
        )

    async def _execute_native_approach(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext,
        *,
        target_id: str,
        pulse_seconds: float,
        primitive_skill: SkillAction,
        require_vendor_role: bool,
        wire_command: Literal[
            "approach_confirmed_vendor",
            "move_to_character",
            "move_in_direction",
            "exit_current_building",
            "operate_natural_resource",
        ] = NATIVE_APPROACH_WIRE_COMMAND,
        require_dialogue_target: bool = True,
        bearing_degrees: float = 0.0,
        distance_units: float = 0.0,
        semantic: SemanticActionReceipt | None = None,
        continue_until_terminal: bool = False,
    ) -> ActionReceipt:
        adopted = (
            self._active_native_order_for(
                wire_command=wire_command,
                target_id=target_id,
                bearing_degrees=bearing_degrees,
                distance_units=distance_units,
            )
            if continue_until_terminal
            else None
        )
        if adopted is not None:
            # This exact pathing order is already issued and still walking. The
            # order is at-most-once, so continuing it is advancing time, not
            # sending a second command.
            primitive_count, messages = 0, [
                f"Continuing the already active approach {adopted.command_id} "
                f"toward the same target; no second order was issued."
            ]
            acknowledgement = adopted
            if semantic is not None:
                semantic = semantic.model_copy(
                    update={
                        "revalidation": (
                            "Adopted this target's already active native order and "
                            "continued it; no second pathing order was issued."
                        )
                    }
                )
        else:
            request = self._native_approach_request(
                target_id,
                command,
                require_vendor_role=require_vendor_role,
                wire_command=wire_command,
                require_dialogue_target=require_dialogue_target,
                bearing_degrees=bearing_degrees,
                distance_units=distance_units,
            )
            request_path = self.telemetry_reader.path.parent / self._NATIVE_COMMAND_REQUEST_FILE
            write_native_command_request_atomic(request_path, request)
            primitive_count, messages = await self._execute_skill_primitives(primitive_skill)
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
                semantic=semantic,
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
                semantic=semantic,
            )
        if (
            isinstance(action, ApproachDialogueTargetAction)
            and self._fresh_pause_state() is True
        ):
            dialogue_open, current = await self._wait_for_exact_native_dialogue(
                target_id=target_id,
                command_id=acknowledgement.command_id,
            )
            if current is not None:
                acknowledgement = current
            if dialogue_open:
                messages.append(
                    "Kenshi opened dialogue with the exact native target while "
                    "remaining paused; no movement pulse or pause toggle was sent."
                )
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
                    native_acknowledgement=acknowledgement,
                    semantic=semantic,
                )
        if not self.controls_config.require_paused_between_actions:
            # A continuous profile wants the world running while the option
            # monitors it, but every completed run deliberately leaves Kenshi
            # paused. Resolve that handoff here: the semantic movement owns
            # starting time when necessary, so the planner does not need a
            # fragile prerequisite unpause step. Never toggle blindly.
            paused = self._fresh_pause_state()
            if paused is None:
                raise RuntimeError(
                    "Native movement cannot determine whether Kenshi is paused."
                )
            if paused:
                unpause_count, unpause_control = await self._execute_pause_request(
                    False
                )
                primitive_count += unpause_count
                if not await self._wait_for_pause_state(False):
                    raise RuntimeError(
                        "Native movement did not confirm Kenshi running after "
                        f"using {unpause_control}."
                    )
                messages.append(
                    f"Started the paused world with {unpause_control}; the "
                    "monitored option now owns the running movement."
                )
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
                message=(
                    "Issued the pathing order; the character walks while the world "
                    "runs. " + " ".join(messages)
                ),
                native_acknowledgement=acknowledgement,
                semantic=semantic,
            )
        receipt = await self._execute_movement_pulse(
            primitive_skill,
            started,
            pulse_seconds=pulse_seconds,
            prepared_primitives=(primitive_count, messages),
        )
        if not continue_until_terminal:
            # The legacy macro's contract is exactly one bounded pulse; the
            # planner was responsible for asking to continue.
            return receipt.model_copy(
                update={
                    "action": action,
                    "native_acknowledgement": acknowledgement,
                    "semantic": semantic,
                }
            )

        # The order is accepted, but Kenshi is paused, so the character has not
        # walked a single step yet. Advance time in bounded pulses until the
        # native side reports a terminal outcome for *this* command. The pathing
        # order is never reissued — continuation is time, not another command —
        # and every pulse still guarantees its own confirmed re-pause, so the
        # human keeps a stopping point roughly every `pulse_seconds`.
        elapsed = pulse_seconds
        budget = self.controls_config.native_approach_max_seconds
        while elapsed < budget:
            latest = await self.observe_without_capture()
            if latest.telemetry is None or latest.telemetry_stale:
                break
            current = latest.telemetry.native_control.acknowledgement_for(
                acknowledgement.command_id
            )
            if current is None or current.status in {
                NativeCommandStatus.COMPLETED,
                NativeCommandStatus.REJECTED,
                NativeCommandStatus.CANCELLED,
            }:
                if current is not None:
                    acknowledgement = current
                break
            remaining = min(pulse_seconds, budget - elapsed)
            if remaining <= 0.0:
                break
            # No primitives: the hotkey already issued the one native order.
            receipt = await self._execute_movement_pulse(
                primitive_skill,
                started,
                pulse_seconds=remaining,
                prepared_primitives=(0, []),
            )
            elapsed += remaining

        return receipt.model_copy(
            update={
                "action": action,
                "native_acknowledgement": acknowledgement,
                "semantic": semantic,
                "message": (
                    f"Approached for up to {elapsed:.1f}s across bounded pulses, "
                    f"re-pausing after each. Native status "
                    f"{acknowledgement.status.value!r}: {acknowledgement.reason}."
                ),
            }
        )

    def _active_native_order_for(
        self,
        *,
        wire_command: Literal[
            "approach_confirmed_vendor",
            "move_to_character",
            "move_in_direction",
            "exit_current_building",
            "operate_natural_resource",
        ],
        target_id: str,
        bearing_degrees: float,
        distance_units: float,
    ) -> NativeCommandAcknowledgement | None:
        """An already accepted, still active order with this exact identity.

        A pathing order survives the run that issued it, so a later run can find
        the character mid-walk. For targeted orders identity is command plus
        target; for a targetless direction it is command plus bearing and
        distance. Treating every empty target as the same order could adopt a
        northbound walk when the plan asked to go east.
        """

        observation = self._last_observation
        if observation is None or observation.telemetry is None or observation.telemetry_stale:
            return None
        native = observation.telemetry.native_control
        if native.active_command_id is None:
            return None
        acknowledgement = native.acknowledgement_for(native.active_command_id)
        if (
            acknowledgement is None
            or acknowledgement.status is not NativeCommandStatus.ACCEPTED
            or acknowledgement.command != wire_command
        ):
            return None
        if wire_command == NATIVE_DIRECTION_WIRE_COMMAND:
            if (
                acknowledgement.target_id
                or acknowledgement.bearing_degrees != bearing_degrees
                or acknowledgement.distance_units != distance_units
            ):
                return None
        elif wire_command == NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            if (
                acknowledgement.target_id
                or acknowledgement.bearing_degrees != 0.0
                or acknowledgement.distance_units != 0.0
            ):
                return None
            selected = [
                character
                for character in observation.telemetry.squad
                if character.selected
            ]
            if len(selected) != 1 or selected[0].indoors is not True:
                return None
        elif (
            acknowledgement.target_id != target_id
            or acknowledgement.bearing_degrees != 0.0
            or acknowledgement.distance_units != 0.0
        ):
            return None
        selected_ids = observation.telemetry.ui.selected_character_ids
        if acknowledgement.selected_character_ids != selected_ids:
            return None
        return acknowledgement

    def _native_approach_request(
        self,
        target_id: str,
        command: CommandDispatchContext,
        *,
        require_vendor_role: bool,
        wire_command: Literal[
            "approach_confirmed_vendor",
            "move_to_character",
            "move_in_direction",
            "exit_current_building",
            "operate_natural_resource",
        ] = NATIVE_APPROACH_WIRE_COMMAND,
        require_dialogue_target: bool = True,
        bearing_degrees: float = 0.0,
        distance_units: float = 0.0,
    ) -> NativeCommandRequest:
        """Build the native pathing request for one exact stable target.

        `require_vendor_role` is what separates the legacy vendor macro from the
        generic dialogue-target action. The generic action asks only for the
        authorization fact it actually needs — a conscious, non-hostile,
        non-animal person with dialogue — because approaching and talking is not
        a commerce affordance.
        """

        # The plug-in fences a request against the telemetry sequence that is
        # current when it reads the file, and telemetry only advances at ~2Hz,
        # so the basis has a working life of about half a second. The executor's
        # revision is older than that by the time the polite input lease is
        # acquired, which made roughly a fifth of native orders die on
        # `stale_revision` for no reason but elapsed time. So re-read telemetry
        # here and issue on the newest sequence, re-proving every authorization
        # fact against that same snapshot - the same discipline `_rebind_in_lease`
        # applies to semantic actions, one layer deeper.
        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError("Native command requires fresh telemetry.")
        observation = self._observation_from_snapshot(result.snapshot)
        if observation.telemetry is None:
            raise RuntimeError("Native command requires a current telemetry observation.")
        # Re-basing may only move forward. A snapshot older than the revision the
        # executor authorized would mean acting on evidence it never saw. This
        # compares telemetry sequence directly because that is the exact fence
        # the plug-in applies; `is_later_than` also weighs wall-clock and frame
        # counters, which cannot express "ahead of what telemetry can supply."
        current_sequence = observation.world_revision.telemetry_sequence
        authorized_sequence = command.based_on_revision.telemetry_sequence
        if current_sequence is None or (
            authorized_sequence is not None and current_sequence < authorized_sequence
        ):
            raise RuntimeError(
                "Native command basis regressed behind the authorized revision."
            )
        telemetry = observation.telemetry
        if wire_command == NATIVE_DIRECTION_WIRE_COMMAND:
            native_contract = MOVE_IN_DIRECTION_CONTRACT
        elif wire_command == NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            native_contract = EXIT_CURRENT_BUILDING_CONTRACT
        elif wire_command == NATIVE_OPERATE_RESOURCE_WIRE_COMMAND:
            native_contract = PERFORM_CONTEXT_ACTION_CONTRACT
        elif wire_command == NATIVE_MOVE_WIRE_COMMAND:
            native_contract = MOVE_TO_CHARACTER_CONTRACT
        else:
            native_contract = APPROACH_DIALOGUE_TARGET_CONTRACT
        missing = native_contract.missing_capabilities(
            set(telemetry.capabilities)
        )
        if missing:
            raise RuntimeError(
                "Native command lacks required capabilities: " + ", ".join(sorted(missing))
            )
        if not telemetry.identity_session_id:
            raise RuntimeError("Native command requires a current identity session.")
        selected_ids = telemetry.ui.selected_character_ids
        if len(selected_ids) != 1 or telemetry.ui.selected_character_id != selected_ids[0]:
            raise RuntimeError("Native command requires one exact primary selection.")
        if wire_command == NATIVE_DIRECTION_WIRE_COMMAND:
            # References nobody: the destination is derived from where the
            # character already stands, which is what makes it available in a
            # place a destination list would be empty.
            return NativeCommandRequest(
                schema_version="1.0",
                command_id=command.command_id,
                command=wire_command,
                control_mode=ControlMode.NATIVE_ASSISTED,
                identity_session_id=telemetry.identity_session_id,
                based_on_revision=observation.world_revision,
                selected_character_ids=list(selected_ids),
                bearing_degrees=bearing_degrees,
                distance_units=distance_units,
            )
        if wire_command == NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            selected = [
                character for character in telemetry.squad if character.selected
            ]
            if len(selected) != 1 or selected[0].indoors is not True:
                raise RuntimeError(
                    "Native building exit requires the selected character to be "
                    "confirmed indoors at issue time."
                )
            return NativeCommandRequest(
                schema_version="1.0",
                command_id=command.command_id,
                command=wire_command,
                control_mode=ControlMode.NATIVE_ASSISTED,
                identity_session_id=telemetry.identity_session_id,
                based_on_revision=observation.world_revision,
                selected_character_ids=list(selected_ids),
            )
        if not target_id:
            raise RuntimeError("Native approach requires an exact target_id.")
        if wire_command == NATIVE_OPERATE_RESOURCE_WIRE_COMMAND:
            matches = [
                target
                for target in telemetry.world_targets
                if target.id == target_id
                and "operate" in target.context_actions
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    "Native context target is absent or no longer advertises "
                    "the exact operate action."
                )
            return NativeCommandRequest(
                schema_version="1.0",
                command_id=command.command_id,
                command=wire_command,
                control_mode=ControlMode.NATIVE_ASSISTED,
                identity_session_id=telemetry.identity_session_id,
                based_on_revision=observation.world_revision,
                selected_character_ids=list(selected_ids),
                target_id=target_id,
            )
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == target_id),
            None,
        )
        if target is None:
            raise RuntimeError("Native command target is absent from current nearby telemetry.")
        if require_dialogue_target and (
            not target.is_dialogue_target() or target.conscious is not True
        ):
            raise RuntimeError(
                "Native command target lacks exact current conscious non-hostile "
                "dialogue evidence."
            )
        if require_vendor_role and not target.is_confirmed_vendor():
            raise RuntimeError("Native command target lacks exact safe current vendor evidence.")
        return NativeCommandRequest(
            schema_version="1.0",
            command_id=command.command_id,
            # The wire name is a legacy alias retained so the proven installed
            # plug-in keeps parsing this request without a rebuild.
            command=wire_command,
            control_mode=ControlMode.NATIVE_ASSISTED,
            identity_session_id=telemetry.identity_session_id,
            based_on_revision=observation.world_revision,
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
                        or acknowledgement.bearing_degrees
                        != request.bearing_degrees
                        or acknowledgement.distance_units != request.distance_units
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
                # Named for what it means to whoever reads it. An approach is
                # only finished when a dialogue opens and a walk when the
                # character arrives, so a target that wandered off, a blocked
                # path or a stopped world all end up here - and "no causally
                # later matching acknowledgement" told nobody any of that.
                raise RuntimeError(
                    f"Kenshi never confirmed the {request.command!r} order finished, "
                    f"after {self._NATIVE_COMMAND_ACK_TIMEOUT_SECONDS:.0f}s. "
                    "The order was accepted, so the character may still be walking: "
                    "check whether it arrived before ordering it again."
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

    async def _wait_for_exact_native_dialogue(
        self,
        *,
        target_id: str,
        command_id: str,
    ) -> tuple[bool, NativeCommandAcknowledgement | None]:
        """Give a paused native talk order one telemetry interval to finish.

        Nearby `PLAYER_TALK_TO` opens dialogue without advancing world time.
        The generic movement pulse must therefore wait for that exact terminal
        before it tries to toggle pause, because Kenshi removes the pause
        control while the dialogue modal is open.
        """

        deadline = time.monotonic() + self._NATIVE_DIALOGUE_SETTLE_SECONDS
        latest_acknowledgement: NativeCommandAcknowledgement | None = None
        while True:
            try:
                result = self.telemetry_reader.read()
            except TelemetryReadError:
                result = None
            if result is not None and not result.stale:
                snapshot = result.snapshot
                current = snapshot.native_control.acknowledgement_for(command_id)
                if current is not None:
                    latest_acknowledgement = current
                if (
                    snapshot.ui.dialogue_open
                    and snapshot.ui.dialogue_target_id == target_id
                ):
                    return True, latest_acknowledgement
                if snapshot.game.paused is not True:
                    return False, latest_acknowledgement
                if (
                    latest_acknowledgement is not None
                    and latest_acknowledgement.status
                    in {
                        NativeCommandStatus.REJECTED,
                        NativeCommandStatus.CANCELLED,
                        NativeCommandStatus.COMPLETED,
                    }
                ):
                    return False, latest_acknowledgement
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, latest_acknowledgement
            await asyncio.sleep(min(self._NATIVE_COMMAND_POLL_SECONDS, remaining))

    def _fresh_pause_state(self) -> bool | None:
        try:
            result = self.telemetry_reader.read()
        except TelemetryReadError:
            return None
        if result.stale:
            return None
        return result.snapshot.game.paused

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
                pause_primitives, _ = self._pause_primitives(True)
            except Exception as exc:
                self._close_outcome = FinalSafeStateOutcome(
                    status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
                    reason=(
                        "Final-pause control could not be resolved "
                        f"({type(exc).__name__}: {exc})."
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
