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
    MOVE_IN_DIRECTION_CONTRACT,
    MOVE_TO_CHARACTER_CONTRACT,
    NATIVE_APPROACH_WIRE_COMMAND,
    NATIVE_DIRECTION_WIRE_COMMAND,
    NATIVE_MOVE_WIRE_COMMAND,
    PURCHASE_ITEM_CONTRACT,
    SCROLL_SCREEN_CONTRACT,
    SELL_ITEM_CONTRACT,
    USE_GAME_BINDING_CONTRACT,
    ActionContract,
    ReferenceBinding,
    contract_for,
)
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
    GAME_BINDING_KEYS,
    Action,
    ActionReceipt,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    CalibrationReport,
    ClickAction,
    CommandDispatchContext,
    ControlMode,
    DismissScreenAction,
    EquipItemAction,
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
    Observation,
    PauseAction,
    PointerActionClass,
    PurchaseItemAction,
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

        if self.control_mode != ControlMode.INTERFACE_ONLY:
            return snapshot
        return snapshot.model_copy(
            update={
                "capabilities": [
                    capability
                    for capability in snapshot.capabilities
                    if not capability.startswith("control.")
                ],
                "native_control": NativeControlState(),
            }
        )

    def _observation_from_snapshot(self, snapshot: TelemetrySnapshot) -> Observation:
        """A minimal current observation for in-lease reference re-resolution.

        Deliberately not a full `observe()`: no capture, no event collection, no
        `_last_observation` mutation. It exists so an action can re-bind its
        reference against fresh telemetry at the moment of input without
        disturbing the canonical stream the executor and supervisor share.
        """

        telemetry = self._apply_control_mode(snapshot)
        return Observation(
            run_id=self.run_id,
            step_index=self._step_index,
            mode="live",
            control_mode=self.control_mode,
            world_revision=WorldStateRevision(
                telemetry_sequence=telemetry.sequence,
                capability_epoch=self._capability_epoch,
                observed_at_monotonic=time.monotonic(),
            ),
            telemetry=telemetry,
            telemetry_stale=False,
            telemetry_age_seconds=0.0,
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
        if isinstance(action, MoveInDirectionAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_directional_move(action, started, command)
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
        """Issue one generic dialogue-target approach through the native bridge.

        The underlying primitives and pulse timing still come from the
        configured native approach macro — that hotkey and its calibrated pulse
        are proven — but the authorization is the generic dialogue-target fence,
        so a non-vendor target is equally valid here.
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
                "native pathing order for this option lifecycle."
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
            "approach_confirmed_vendor", "move_to_character", "move_in_direction"
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
        if not self.controls_config.require_paused_between_actions:
            # The world is already running: the pathing order is enough, and the
            # monitored option watches the character walk. Pulsing here would
            # mean unpausing an unpaused game and then pausing a game the
            # operator wants running.
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
            "approach_confirmed_vendor", "move_to_character", "move_in_direction"
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
            "approach_confirmed_vendor", "move_to_character", "move_in_direction"
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
        if not target_id:
            raise RuntimeError("Native approach requires an exact target_id.")
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
