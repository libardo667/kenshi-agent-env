from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..action_contracts import (
    ACTIVATE_VISIBLE_CONTROL_CONTRACT,
    APPROACH_DIALOGUE_TARGET_CONTRACT,
    COLLECT_RESOURCE_OUTPUT_CONTRACT,
    COMMAND_WORLD_TARGET_CONTRACT,
    DISMISS_SCREEN_CONTRACT,
    EQUIP_ITEM_CONTRACT,
    EXIT_CURRENT_BUILDING_CONTRACT,
    MOVE_IN_DIRECTION_CONTRACT,
    MOVE_TO_CHARACTER_CONTRACT,
    NATIVE_APPROACH_WIRE_COMMAND,
    NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
    NATIVE_DIRECTION_WIRE_COMMAND,
    NATIVE_EXIT_BUILDING_WIRE_COMMAND,
    NATIVE_MAP_TRAVEL_WIRE_COMMAND,
    NATIVE_MOVE_WIRE_COMMAND,
    NATIVE_OPEN_CONTEXT_INVENTORY_WIRE_COMMAND,
    NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
    NATIVE_SQUAD_REGROUP_WIRE_COMMAND,
    NATIVE_SQUAD_SELECTION_WIRE_COMMAND,
    OPEN_CONTEXT_INVENTORY_CONTRACT,
    OPEN_SCREEN_CONTRACT,
    PERFORM_CONTEXT_ACTION_CONTRACT,
    PRODUCE_RESOURCE_OUTPUT_CONTRACT,
    PURCHASE_ITEM_CONTRACT,
    RECOVER_CAMERA_VIEW_CONTRACT,
    REGROUP_WITH_SQUAD_MEMBER_CONTRACT,
    ROTATE_CAMERA_CONTRACT,
    SCROLL_SCREEN_CONTRACT,
    SELECT_SQUAD_MEMBER_CONTRACT,
    SELECT_SQUAD_MEMBER_EXACT_CONTRACT,
    SELL_ITEM_CONTRACT,
    TRAVEL_TO_MAP_DESTINATION_CONTRACT,
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
    GAME_SPEED_MULTIPLIER_BY_GEAR,
    QUICKSAVE_COMPLETION_CAPABILITY,
    SCREEN_BINDINGS,
    Action,
    ActionReceipt,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    CalibrationReport,
    CameraFrameScore,
    CameraRecoveryEvidence,
    CameraRecoveryStatus,
    ClickAction,
    CollectResourceOutputAction,
    CommandDispatchContext,
    CommandWorldTargetAction,
    ContextActionKind,
    ControlMode,
    DismissScreenAction,
    EquipItemAction,
    ExitCurrentBuildingAction,
    GameBinding,
    HotkeyAction,
    InputBoundaryDecision,
    KeyAction,
    MouseButton,
    MouseButtonAction,
    MouseDragAction,
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
    OpenContextInventoryAction,
    OpenScreenAction,
    PauseAction,
    PerformContextAction,
    PointerActionClass,
    ProduceResourceOutputAction,
    PurchaseEvidence,
    PurchaseItemAction,
    PurchaseStatus,
    QuicksaveEvidence,
    QuicksaveStatus,
    RecoverCameraViewAction,
    RegroupWithSquadMemberAction,
    ResourceTransferStatus,
    RespondToImmediateThreatAction,
    RotateCameraAction,
    SaleEvidence,
    SaleStatus,
    ScrollAction,
    ScrollScreenAction,
    SelectSquadMemberAction,
    SelectSquadMemberExactAction,
    SellItemAction,
    SemanticActionReceipt,
    SetSpeedAction,
    SkillAction,
    SkillArgument,
    StopAction,
    TelemetrySnapshot,
    ThreatResponseStrategy,
    Transition,
    TravelToMapDestinationAction,
    UseGameBindingAction,
    WaitAction,
    WorldStateRevision,
    camera_rotation_primitive,
    game_binding_primitive,
    normalize_control_label,
    screen_is_open,
    window_close_point,
)
from ..native_commands import write_native_command_request_atomic
from ..resource_transfer import (
    begin_resource_transfer,
    finalize_resource_transfer,
)
from ..skills import MacroRegistry
from ..telemetry import TelemetryReader, TelemetryReadError
from ..terminal_state import terminal_window_event, terminal_window_title
from ..ui_messages import causally_new_game_message, game_message_panel_texts
from .base import AgentEnvironment


@dataclass(frozen=True, slots=True)
class _BoundedTradeOutcome:
    status: Literal["completed", "partial", "not_completed", "outcome_unknown"]
    completed_quantity: int
    selected_character_id: str
    money_before: int
    money_after: int | None
    inventory_quantity_before: int
    inventory_quantity_after: int | None
    observed_after_sequence: int | None
    primitive_actions: int
    initial_binding: ReferenceBinding
    initial_observation: Observation
    reason: str


@dataclass(frozen=True, slots=True)
class _QuicksaveTreeState:
    files: tuple[tuple[str, int, int], ...]
    quick_save_size_bytes: int | None


def _quicksave_tree_state(path: Path) -> _QuicksaveTreeState:
    """Read one exact save slot without following links or opening its contents."""

    if not path.exists():
        return _QuicksaveTreeState(files=(), quick_save_size_bytes=None)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"Quicksave slot is not a real directory: {path}")
    files: list[tuple[str, int, int]] = []
    quick_save_size: int | None = None
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise RuntimeError(
                f"Quicksave completion refuses symbolic links: {candidate}"
            )
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise RuntimeError(
                f"Quicksave completion found an unsupported entry: {candidate}"
            )
        stat = candidate.stat()
        relative = candidate.relative_to(path).as_posix()
        files.append((relative, stat.st_size, stat.st_mtime_ns))
        if relative == "quick.save" and stat.st_size > 0:
            quick_save_size = stat.st_size
    return _QuicksaveTreeState(
        files=tuple(files),
        quick_save_size_bytes=quick_save_size,
    )


def _changed_quicksave_files(
    before: _QuicksaveTreeState,
    after: _QuicksaveTreeState,
) -> int:
    before_by_path = {path: (size, modified) for path, size, modified in before.files}
    after_by_path = {path: (size, modified) for path, size, modified in after.files}
    return sum(
        before_by_path.get(path) != after_by_path.get(path)
        for path in before_by_path.keys() | after_by_path.keys()
    )


class LiveEnvironment(AgentEnvironment):
    _NATIVE_COMMAND_REQUEST_FILE = "native_command.request.json"
    _NATIVE_COMMAND_ACK_TIMEOUT_SECONDS = 2.0
    _RESOURCE_TRANSFER_OBSERVATION_TIMEOUT_SECONDS = 2.0
    _PURCHASE_OBSERVATION_TIMEOUT_SECONDS = 2.0
    _SALE_OBSERVATION_TIMEOUT_SECONDS = 2.0
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
        quicksave_dir: Path | None = None,
        quicksave_timeout_seconds: float = 10.0,
        quicksave_stable_seconds: float = 0.5,
    ) -> None:
        if quicksave_timeout_seconds <= 0.0 or quicksave_stable_seconds <= 0.0:
            raise ValueError("Quicksave monitoring times must be positive.")
        if quicksave_stable_seconds >= quicksave_timeout_seconds:
            raise ValueError(
                "Quicksave stable time must be shorter than its completion timeout."
            )
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
        self.quicksave_dir = quicksave_dir
        self.quicksave_timeout_seconds = quicksave_timeout_seconds
        self.quicksave_stable_seconds = quicksave_stable_seconds
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
            terminal_title = terminal_window_title(self.controller)
        except (OSError, RuntimeError, ValueError) as exc:
            events.append(
                "Terminal-window probe failed: "
                f"{type(exc).__name__}: {exc}"
            )
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
            events.append(
                "Terminal-window probe failed: "
                f"{type(exc).__name__}: {exc}"
            )
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
            available_skills=self.available_skills,
            skill_specs=[self.macros.spec(name) for name in self.available_skills],
        )
        self._last_observation = observation
        return observation

    def _apply_control_mode(self, snapshot: TelemetrySnapshot) -> TelemetrySnapshot:
        """Withhold native-control evidence that `interface_only` may not use."""

        capabilities = list(snapshot.capabilities)
        if (
            self.quicksave_dir is not None
            and QUICKSAVE_COMPLETION_CAPABILITY not in capabilities
        ):
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
        if (
            isinstance(action, CollectResourceOutputAction)
            and receipt.semantic is not None
            and receipt.semantic.resource_transfer is not None
            and receipt.semantic.source_revision is not None
        ):
            deadline = (
                time.monotonic()
                + self._RESOURCE_TRANSFER_OBSERVATION_TIMEOUT_SECONDS
            )
            evidence = finalize_resource_transfer(
                action,
                baseline=receipt.semantic.resource_transfer,
                before_revision=receipt.semantic.source_revision,
                after=observation,
            )
            while (
                evidence.status is ResourceTransferStatus.UNVERIFIED
                and not observation.world_revision.is_later_than(
                    receipt.semantic.source_revision
                )
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.05)
                observation = await self.observe_without_capture()
                evidence = finalize_resource_transfer(
                    action,
                    baseline=receipt.semantic.resource_transfer,
                    before_revision=receipt.semantic.source_revision,
                    after=observation,
                )
            receipt = receipt.model_copy(
                update={
                    "semantic": receipt.semantic.model_copy(
                        update={"resource_transfer": evidence}
                    ),
                    "message": receipt.message + " " + evidence.reason,
                    "error_type": (
                        None
                        if evidence.status is ResourceTransferStatus.TRANSFERRED
                        else "ResourceTransferNotProven"
                    ),
                }
            )
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
            return await self._execute_playback_speed(action, started)
        if isinstance(action, RespondToImmediateThreatAction):
            if action.strategy is not ThreatResponseStrategy.ENGAGE:
                raise RuntimeError(
                    "Withdrawal must be compiled to runtime-owned native movement."
                )
            playback = await self._execute_playback_speed(
                SetSpeedAction(speed=1),
                started,
            )
            return playback.model_copy(
                update={
                    "action": action,
                    "message": (
                        "Runtime established normal-speed playback for the chosen "
                        "engagement; threat and squad-health monitoring now own "
                        "the terminal. "
                        + playback.message
                    ),
                    "semantic": SemanticActionReceipt(
                        action_kind=action.kind,
                        contract_version="1.0",
                        target_id=action.actor_id,
                        revalidation=(
                            "The exact selected actor and immediate hostile state "
                            "were revalidated inside the input lease."
                        ),
                    ),
                }
            )
        if isinstance(action, ApproachDialogueTargetAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_semantic_approach(action, started, command)
        if isinstance(action, CommandWorldTargetAction):
            return await self._execute_world_target_command(action, started)
        if isinstance(action, SelectSquadMemberAction):
            return await self._execute_select_squad_member(action, started)
        if isinstance(action, SelectSquadMemberExactAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_select_squad_member_exact(
                action,
                started,
                command,
            )
        if isinstance(action, RotateCameraAction):
            return await self._execute_rotate_camera(action, started)
        if isinstance(action, PerformContextAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_context_action(action, started, command)
        if isinstance(action, ProduceResourceOutputAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_produce_resource_output(
                action,
                started,
                command,
            )
        if isinstance(action, OpenContextInventoryAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_open_context_inventory(
                action,
                started,
                command,
            )
        if isinstance(action, MoveInDirectionAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_directional_move(action, started, command)
        if isinstance(action, TravelToMapDestinationAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_map_travel(action, started, command)
        if isinstance(action, RegroupWithSquadMemberAction):
            if command is None:
                raise RuntimeError(
                    "Native command execution requires caller-owned command context."
                )
            return await self._execute_squad_regroup(action, started, command)
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
        if isinstance(action, OpenScreenAction):
            return await self._execute_open_screen(action, started)
        if isinstance(action, UseGameBindingAction):
            return await self._execute_game_binding(action, started)
        if isinstance(action, ScrollScreenAction):
            return await self._execute_scroll_screen(action, started)
        if isinstance(action, SellItemAction):
            return await self._execute_sell_item(action, started)
        if isinstance(action, EquipItemAction):
            return await self._execute_equip_item(action, started)
        if isinstance(action, CollectResourceOutputAction):
            return await self._execute_collect_resource_output(action, started)
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
            action,
            (
                KeyAction,
                HotkeyAction,
                MouseButtonAction,
                MouseDragAction,
                MoveCursorAction,
                ClickAction,
                ScrollAction,
            ),
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
                (
                    KeyAction,
                    HotkeyAction,
                    MouseButtonAction,
                    MouseDragAction,
                    MoveCursorAction,
                    ClickAction,
                    ScrollAction,
                ),
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

    async def _execute_playback_speed(
        self,
        action: SetSpeedAction,
        started: datetime,
    ) -> ActionReceipt:
        """Causally establish one running playback state.

        Kenshi's faster-speed keys select a rate but do not start a paused
        world. Starting at gear 1 and then selecting the requested faster gear
        is a controller detail, not a second planner decision.
        """

        initial = self.telemetry_reader.read()
        if initial.stale:
            raise RuntimeError(
                "Refusing to set playback speed from stale telemetry."
            )
        paused = initial.snapshot.game.paused
        multiplier = initial.snapshot.game.speed_multiplier
        expected = GAME_SPEED_MULTIPLIER_BY_GEAR[action.speed]
        if paused is None or multiplier is None:
            raise RuntimeError(
                "Refusing to set playback speed while pause or speed state is unknown."
            )
        if paused is False and multiplier == expected:
            return ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                primitive_actions=0,
                message=(
                    f"Kenshi already reports running at speed gear {action.speed} "
                    f"({expected:g}x)."
                ),
            )

        primitive_count = 0
        if paused:
            primitive_count += await self._establish_playback_gear(1)

        if action.speed != 1:
            primitive_count += await self._establish_playback_gear(action.speed)
        elif not paused:
            primitive_count += await self._establish_playback_gear(1)

        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=primitive_count,
            message=(
                "Controller causally confirmed Kenshi running at "
                f"speed gear {action.speed} ({expected:g}x)."
            ),
        )

    async def _establish_playback_gear(self, gear: int) -> int:
        """Retry one idempotent gear selection only after confirmation fails."""

        expected = GAME_SPEED_MULTIPLIER_BY_GEAR[gear]
        primitive_count = 0
        for _attempt in range(2):
            primitive_count += await self._execute_speed_key(gear)
            if await self._wait_for_playback_state(
                paused=False,
                multiplier=expected,
            ):
                return primitive_count
        raise RuntimeError(
            f"Kenshi did not confirm running at speed gear {gear} "
            f"({expected:g}x) after two idempotent selections."
        )

    async def _establish_native_running_state(
        self,
        acknowledgement: NativeCommandAcknowledgement,
        *,
        timeout_seconds: float = 3.0,
    ) -> tuple[int, NativeCommandAcknowledgement | None]:
        """Start at 1x unless this exact native command finishes first."""

        expected = GAME_SPEED_MULTIPLIER_BY_GEAR[1]
        primitive_count = 0
        for _attempt in range(2):
            primitive_count += await self._execute_speed_key(1)
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    result = self.telemetry_reader.read()
                except TelemetryReadError:
                    result = None
                if result is not None and not result.stale:
                    current = self._matching_native_acknowledgement(
                        result.snapshot,
                        acknowledgement,
                    )
                    if (
                        current is not None
                        and current.status
                        in {
                            NativeCommandStatus.CANCELLED,
                            NativeCommandStatus.COMPLETED,
                        }
                    ):
                        return primitive_count, current
                    if (
                        result.snapshot.game.paused is False
                        and result.snapshot.game.speed_multiplier == expected
                    ):
                        return primitive_count, None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.05, remaining))
        raise RuntimeError(
            "Kenshi did not confirm native movement running at speed gear 1 "
            f"({expected:g}x) after two idempotent selections."
        )

    async def _execute_speed_key(self, gear: int) -> int:
        if self.controller.emergency_stop_pressed(self.emergency_stop_key):
            raise RuntimeError(
                "Emergency stop interrupted playback control; no further input was sent."
            )
        if self.controller.user_input_detected():
            raise RuntimeError(
                "Human input interrupted playback control; no further input was sent."
            )
        receipt = await self.controller.execute(
            KeyAction(key=self.controls_config.speed_keys[gear])
        )
        if not receipt.executed:
            raise RuntimeError(receipt.message or "Playback key was not executed.")
        return receipt.primitive_actions

    async def _wait_for_playback_state(
        self,
        *,
        paused: bool,
        multiplier: float,
        timeout_seconds: float = 3.0,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                result = self.telemetry_reader.read()
                if (
                    not result.stale
                    and result.snapshot.game.paused is paused
                    and result.snapshot.game.speed_multiplier == multiplier
                ):
                    return True
            except TelemetryReadError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.05, remaining))

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
            wire_command=NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
            context_action=action.context_action,
            require_dialogue_target=False,
        )

    async def _execute_world_target_command(
        self,
        action: CommandWorldTargetAction,
        started: datetime,
    ) -> ActionReceipt:
        """Right-click one exact target at geometry re-read inside the input lease."""

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "No input was sent: telemetry became stale inside the input lease."
            )
        observation = self._observation_from_snapshot(result.snapshot)
        binding = COMMAND_WORLD_TARGET_CONTRACT.bind(action, observation)
        if not binding.bound or binding.resolved_bounds is None:
            raise RuntimeError(f"No input was sent: {binding.reason}")
        bounds = binding.resolved_bounds
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        primitive_receipt = await self.controller.execute(
            ClickAction(
                x=x,
                y=y,
                button=MouseButton.RIGHT,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=COMMAND_WORLD_TARGET_CONTRACT.version,
            target_id=binding.target_id,
            resolved_label=binding.resolved_label,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-resolved the exact world target and its current screen position "
                f"inside the input lease before Mouse2. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Commanded current target {binding.target_id!r} with Mouse2 "
                    f"for {binding.resolved_label!r}. A later observation must "
                    "confirm the resulting world task."
                ),
            }
        )

    async def _execute_rotate_camera(
        self,
        action: RotateCameraAction,
        started: datetime,
    ) -> ActionReceipt:
        """Apply one bounded held-Mouse3 drag after in-lease world revalidation."""

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "No input was sent: telemetry became stale inside the input lease."
            )
        observation = self._observation_from_snapshot(result.snapshot)
        binding = ROTATE_CAMERA_CONTRACT.bind(action, observation)
        if not binding.bound:
            raise RuntimeError(f"No input was sent: {binding.reason}")
        primitive = camera_rotation_primitive(action)
        primitive_receipt = await self.controller.execute(primitive)
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=ROTATE_CAMERA_CONTRACT.version,
            resolved_label=binding.resolved_label,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-confirmed the unobstructed world screen inside the input lease "
                f"before the held-Mouse3 drag. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Rotated the camera {binding.resolved_label!r} through "
                    "Kenshi's bounded Mouse3 rotation mode."
                ),
            }
        )

    async def _execute_select_squad_member(
        self,
        action: SelectSquadMemberAction,
        started: datetime,
    ) -> ActionReceipt:
        """Left-click one exact squad portrait after in-lease revalidation."""

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "No input was sent: telemetry became stale inside the input lease."
            )
        observation = self._observation_from_snapshot(result.snapshot)
        binding = SELECT_SQUAD_MEMBER_CONTRACT.bind(action, observation)
        if not binding.bound or binding.resolved_bounds is None:
            raise RuntimeError(f"No input was sent: {binding.reason}")
        bounds = binding.resolved_bounds
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        primitive_receipt = await self.controller.execute(
            ClickAction(
                x=x,
                y=y,
                button=MouseButton.LEFT,
            )
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=SELECT_SQUAD_MEMBER_CONTRACT.version,
            target_id=binding.target_id,
            resolved_label=binding.resolved_label,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-resolved the exact squad member and current lower-HUD portrait "
                f"inside the input lease before Mouse1. {binding.reason}"
            ),
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Selected current squad member {binding.target_id!r} with "
                    "Mouse1. A later observation must confirm the singular "
                    "selected character."
                ),
            }
        )

    async def _execute_select_squad_member_exact(
        self,
        action: SelectSquadMemberExactAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Select and verify one exact squad identity through native code."""

        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                "Exact squad selection requires a configured native transport skill."
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
            contract_version=SELECT_SQUAD_MEMBER_EXACT_CONTRACT.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact current squad target and singular selection "
                "basis; native code owns selection and terminal verification."
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
            wire_command=NATIVE_SQUAD_SELECTION_WIRE_COMMAND,
            require_dialogue_target=False,
            accepted_is_terminal_error=True,
        )

    def _context_native_transport(
        self,
        *,
        target_id: str,
        purpose: str,
    ) -> tuple[SkillAction, float]:
        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                f"{purpose} requires a configured native transport skill."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value=target_id)],
        )
        pulse_seconds = self.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native transport skill {skill_name!r} has no "
                "movement pulse."
            )
        return primitive_skill, pulse_seconds

    async def _execute_produce_resource_output(
        self,
        action: ProduceResourceOutputAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Retain one exact mining job until native output proof is terminal."""

        primitive_skill, pulse_seconds = self._context_native_transport(
            target_id=action.target_id,
            purpose="Resource production",
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=PRODUCE_RESOURCE_OUTPUT_CONTRACT.version,
            target_id=action.target_id,
            resolved_label="produce_output",
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact reviewed natural resource. Native code owns "
                "the task through actual output, and adopts matching active work "
                "without reissuing it."
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
            wire_command=NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
            require_dialogue_target=False,
            minimum_output_quantity=action.minimum_output_quantity,
        )

    async def _execute_open_context_inventory(
        self,
        action: OpenContextInventoryAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Open the ordinary inventory window for one exact resource handle."""

        primitive_skill, pulse_seconds = self._context_native_transport(
            target_id=action.target_id,
            purpose="Contextual inventory opening",
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=OPEN_CONTEXT_INVENTORY_CONTRACT.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact natural-resource handle and required native "
                "terminal proof that its contextual inventory is open."
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
            wire_command=NATIVE_OPEN_CONTEXT_INVENTORY_WIRE_COMMAND,
            require_dialogue_target=False,
            accepted_is_terminal_error=True,
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

    async def _execute_map_travel(
        self,
        action: TravelToMapDestinationAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Issue one exact long-distance order to a discovered settlement."""

        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                "Map travel requires a configured native approach skill to "
                "supply its bounded transport primitive."
            )
        primitive_skill = SkillAction(
            name=skill_name,
            args=[
                SkillArgument(
                    name="target_id",
                    value=action.destination_id,
                )
            ],
        )
        pulse_seconds = self.macros.resolve_movement_pulse_seconds(
            primitive_skill
        )
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native approach skill {skill_name!r} has no movement pulse."
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=TRAVEL_TO_MAP_DESTINATION_CONTRACT.version,
            target_id=action.destination_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound one exact currently discovered settlement marker; "
                "native code owns its waypoint, route, camera, and arrival."
            ),
        )
        return await self._execute_native_approach(
            action,
            started,
            command,
            target_id=action.destination_id,
            pulse_seconds=pulse_seconds,
            primitive_skill=primitive_skill,
            require_vendor_role=False,
            semantic=semantic,
            continue_until_terminal=True,
            wire_command=NATIVE_MAP_TRAVEL_WIRE_COMMAND,
            require_dialogue_target=False,
            running_speed_gear=3,
        )

    async def _execute_squad_regroup(
        self,
        action: RegroupWithSquadMemberAction,
        started: datetime,
        command: CommandDispatchContext,
    ) -> ActionReceipt:
        """Issue one global, exact order from the selected actor to a squadmate."""

        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(
                "Squad regrouping requires a configured native approach skill to "
                "supply its bounded transport primitive."
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
            contract_version=REGROUP_WITH_SQUAD_MEMBER_CONTRACT.version,
            target_id=action.target_id,
            source_revision=command.based_on_revision,
            revalidation=(
                "Re-bound the exact selected actor and distinct current squadmate; "
                "native code owns global lookup, pathing, playback, and arrival."
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
            wire_command=NATIVE_SQUAD_REGROUP_WIRE_COMMAND,
            require_dialogue_target=False,
            running_speed_gear=3,
            expected_actor_id=action.actor_id,
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
        """Buy a bounded quantity with per-unit identity and conservation proof."""

        outcome = await self._execute_bounded_trade(
            action,
            PURCHASE_ITEM_CONTRACT,
            direction="purchase",
            observation_timeout_seconds=self._PURCHASE_OBSERVATION_TIMEOUT_SECONDS,
        )
        status = {
            "completed": PurchaseStatus.PURCHASED,
            "partial": PurchaseStatus.PARTIALLY_PURCHASED,
            "not_completed": PurchaseStatus.NOT_PURCHASED,
            "outcome_unknown": PurchaseStatus.OUTCOME_UNKNOWN,
        }[outcome.status]
        evidence = PurchaseEvidence(
            status=status,
            seller_id=action.seller_id,
            selected_character_id=outcome.selected_character_id,
            item_name=action.item_name,
            requested_quantity=action.quantity,
            purchased_quantity=outcome.completed_quantity,
            money_before=outcome.money_before,
            money_after=outcome.money_after,
            inventory_quantity_before=outcome.inventory_quantity_before,
            inventory_quantity_after=outcome.inventory_quantity_after,
            observed_after_sequence=outcome.observed_after_sequence,
            reason=outcome.reason,
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=PURCHASE_ITEM_CONTRACT.version,
            target_id=action.seller_id,
            resolved_label=outcome.initial_binding.resolved_label,
            resolved_role=outcome.initial_binding.resolved_role,
            resolved_bounds=outcome.initial_binding.resolved_bounds,
            source_revision=outcome.initial_observation.world_revision,
            revalidation=(
                "Re-bound the exact seller-owned item cell before every unit and "
                "required a later matching purse loss plus selected-character "
                "inventory gain before continuing. "
                f"{outcome.initial_binding.reason}"
            ),
            purchase=evidence,
        )
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=outcome.primitive_actions > 0,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=outcome.primitive_actions,
            message=outcome.reason,
            semantic=semantic,
        )

    def _trade_refusal_before_input(
        self,
        action: PurchaseItemAction | SellItemAction,
        binding: ReferenceBinding,
        *,
        direction: Literal["purchase", "sale"],
        money: int,
    ) -> str | None:
        """Name a reason this unit cannot trade, before any input is sent.

        A trade that binds, clicks and moves nothing used to report only "later
        telemetry showed no purse or selected-inventory change" - a sentence
        every possible cause produces, so an agent reading it learns nothing it
        can act on and neither does a reader of the log. Cells now carry their
        own price and stack size, so the resource reasons are answerable here,
        without spending input to discover them.

        Returning None means the known preconditions hold. That is the point:
        it turns a silent failure afterwards into evidence that the mechanism
        is at fault rather than the purse or the shelf.
        """

        if direction != "purchase":
            return None
        price = binding.item_base_value
        if price is not None and money < price:
            return (
                f"{action.item_name!r} costs {price} and the purse holds "
                f"{money}; sell something or choose an item at or under {money}."
            )
        available = binding.item_quantity
        if available is not None and available < 1:
            return (
                f"the bound cell no longer holds any {action.item_name!r}; "
                "the shelf changed after an earlier transfer."
            )
        if binding.item_name is not None and binding.item_name != action.item_name:
            return (
                f"the bound cell now holds {binding.item_name!r}, not "
                f"{action.item_name!r}; the shelf re-indexed under this binding."
            )
        return None

    def _trade_preconditions_note(
        self,
        binding: ReferenceBinding,
        *,
        direction: Literal["purchase", "sale"],
        money: int,
    ) -> str:
        """State what was true *at binding*, for a failure that follows.

        Without this a reader cannot tell an unaffordable purchase from a
        right-click that did not land, because both end in no observed delta.

        Every number here comes from the binding, which is why the tense
        matters. A first version said "the cell held 1" in the present, and a
        live run then produced exactly that sentence while telemetry showed the
        trade window holding no item cells at all - the binding was up to the
        full conservation timeout out of date. Reporting remembered state as
        current is the same defect this whole message exists to remove, so the
        wording pins when it was true and claims nothing about now.
        """

        if direction != "purchase":
            return ""
        price = binding.item_base_value
        if price is None:
            return ""
        stock = binding.item_quantity
        stocked = f" and the bound cell held {stock}" if stock is not None else ""
        return (
            f" When the click was sent the purse held {money} against a price "
            f"of {price}{stocked}, so neither the purse nor the shelf explains "
            "this; the cell may also have gone since."
        )

    async def _execute_bounded_trade(
        self,
        action: PurchaseItemAction | SellItemAction,
        contract: ActionContract,
        *,
        direction: Literal["purchase", "sale"],
        observation_timeout_seconds: float,
    ) -> _BoundedTradeOutcome:
        initial_binding, initial_observation = self._rebind_in_lease(
            contract,
            action,
        )
        telemetry = initial_observation.telemetry
        assert telemetry is not None
        selected_character_id, money_before, inventory_before = self._trade_state(
            telemetry,
            action.item_name,
        )

        current_money = money_before
        current_inventory = inventory_before
        current_sequence = telemetry.sequence
        final_money: int | None = money_before
        final_inventory: int | None = inventory_before
        final_sequence: int | None = telemetry.sequence
        completed_quantity = 0
        primitive_actions = 0
        status: Literal[
            "completed", "partial", "not_completed", "outcome_unknown"
        ] = "not_completed"
        reason = "No trade input was sent."
        binding = initial_binding
        operation = "purchase" if direction == "purchase" else "sale"

        for unit_index in range(action.quantity):
            if unit_index:
                rebound, rebind_reason, rebound_snapshot = self._try_rebind_trade(
                    action,
                    contract,
                    selected_character_id=selected_character_id,
                    expected_money=current_money,
                    expected_inventory=current_inventory,
                )
                if rebound is None:
                    status = "partial" if completed_quantity else "not_completed"
                    reason = (
                        f"Stopped after {completed_quantity}/{action.quantity}: "
                        f"{rebind_reason}"
                    )
                    break
                binding = rebound
                assert rebound_snapshot is not None
                message_baseline = game_message_panel_texts(rebound_snapshot)
            else:
                message_baseline = game_message_panel_texts(telemetry)

            remaining = action.quantity - completed_quantity
            refusal = self._trade_refusal_before_input(
                action,
                binding,
                direction=direction,
                money=current_money,
            )
            if refusal is not None:
                status = "partial" if completed_quantity else "not_completed"
                reason = (
                    f"Stopped after {completed_quantity}/{action.quantity}: "
                    f"{refusal}"
                )
                break
            self._ensure_trade_can_continue(operation)
            bounds = binding.resolved_bounds
            assert bounds is not None
            x = (bounds.min_x + bounds.max_x) / 2.0
            y = (bounds.min_y + bounds.max_y) / 2.0
            move_receipt = await self.controller.execute(MoveCursorAction(x=x, y=y))
            primitive_actions += move_receipt.primitive_actions
            if self.controls_config.item_cell_hover_seconds:
                await asyncio.sleep(self.controls_config.item_cell_hover_seconds)
            self._ensure_trade_can_continue(operation)
            click_receipt = await self.controller.execute(
                ClickAction(
                    x=x,
                    y=y,
                    button=MouseButton.RIGHT,
                    hold_seconds=self.controls_config.control_activation_hold_seconds,
                )
            )
            primitive_actions += click_receipt.primitive_actions

            (
                transfer_status,
                observed_money,
                observed_inventory,
                observed_sequence,
                outcome_reason,
            ) = await self._wait_for_trade_conservation(
                item_name=action.item_name,
                direction=direction,
                selected_character_id=selected_character_id,
                money_before=current_money,
                inventory_before=current_inventory,
                after_sequence=current_sequence,
                remaining_quantity=remaining,
                message_baseline=message_baseline,
                timeout_seconds=observation_timeout_seconds,
            )
            final_money = observed_money
            final_inventory = observed_inventory
            final_sequence = observed_sequence
            if transfer_status == "transferred":
                assert observed_money is not None
                assert observed_inventory is not None
                assert observed_sequence is not None
                transferred = (
                    observed_inventory - current_inventory
                    if direction == "purchase"
                    else current_inventory - observed_inventory
                )
                completed_quantity += transferred
                current_money = observed_money
                current_inventory = observed_inventory
                current_sequence = observed_sequence
                if completed_quantity >= action.quantity:
                    status = "completed"
                    reason = (
                        f"Conserved {completed_quantity}/{action.quantity} "
                        f"{action.item_name!r} {operation}s through matching "
                        + (
                            "purse loss and selected-character inventory gain."
                            if direction == "purchase"
                            else "purse gain and selected-character inventory loss."
                        )
                    )
                    break
                continue

            if transfer_status == "not_transferred":
                status = "partial" if completed_quantity else "not_completed"
                reason = (
                    f"Stopped after {completed_quantity}/{action.quantity}: "
                    f"{outcome_reason}"
                    + self._trade_preconditions_note(
                        binding,
                        direction=direction,
                        money=current_money,
                    )
                )
            elif transfer_status == "refused":
                status = "partial" if completed_quantity else "not_completed"
                reason = (
                    f"Stopped after {completed_quantity}/{action.quantity}: "
                    f"{outcome_reason}"
                )
            else:
                status = "outcome_unknown"
                reason = (
                    f"Stopped after {completed_quantity}/{action.quantity} "
                    f"confirmed {operation}s because the last delivery is "
                    f"ambiguous: {outcome_reason}"
                )
            break
        else:
            if completed_quantity == action.quantity:
                status = "completed"
                reason = (
                    f"Conserved all {completed_quantity} requested "
                    f"{action.item_name!r} {operation}s."
                )

        return _BoundedTradeOutcome(
            status=status,
            completed_quantity=completed_quantity,
            selected_character_id=selected_character_id,
            money_before=money_before,
            money_after=final_money,
            inventory_quantity_before=inventory_before,
            inventory_quantity_after=final_inventory,
            observed_after_sequence=final_sequence,
            primitive_actions=primitive_actions,
            initial_binding=initial_binding,
            initial_observation=initial_observation,
            reason=reason,
        )

    def _trade_state(
        self,
        telemetry: TelemetrySnapshot,
        item_name: str,
        *,
        expected_character_id: str | None = None,
    ) -> tuple[str, int, int]:
        selected_ids = telemetry.ui.selected_character_ids
        selected_character_id = telemetry.ui.selected_character_id
        if (
            len(selected_ids) != 1
            or selected_character_id != selected_ids[0]
            or (
                expected_character_id is not None
                and selected_character_id != expected_character_id
            )
        ):
            raise RuntimeError(
                "Trade conservation requires the same one exact selected character."
            )
        selected = [
            character
            for character in telemetry.squad
            if character.id == selected_character_id and character.selected
        ]
        if len(selected) != 1 or selected[0].inventory_complete is not True:
            raise RuntimeError(
                "Trade conservation requires one selected character with a "
                "complete inventory export."
            )
        if telemetry.game.money is None:
            raise RuntimeError("Trade conservation requires known current money.")
        normalized_name = normalize_control_label(item_name)
        quantity = sum(
            (
                item.item_quantity
                if item.item_quantity is not None
                else item.quantity
            )
            for item in selected[0].inventory
            if normalize_control_label(item.name) == normalized_name
        )
        return selected_character_id, telemetry.game.money, quantity

    def _try_rebind_trade(
        self,
        action: PurchaseItemAction | SellItemAction,
        contract: ActionContract,
        *,
        selected_character_id: str,
        expected_money: int,
        expected_inventory: int,
    ) -> tuple[ReferenceBinding | None, str, TelemetrySnapshot | None]:
        try:
            result = self.telemetry_reader.read()
        except TelemetryReadError as exc:
            return None, f"telemetry could not be read ({exc}).", None
        if result.stale:
            return None, "telemetry became stale before the next unit.", None
        observation = self._observation_from_snapshot(result.snapshot)
        binding = contract.bind(action, observation)
        if not binding.bound or binding.resolved_bounds is None:
            return None, binding.reason, None
        try:
            character_id, money, inventory = self._trade_state(
                result.snapshot,
                action.item_name,
                expected_character_id=selected_character_id,
            )
        except RuntimeError as exc:
            return None, str(exc), None
        if (
            character_id != selected_character_id
            or money != expected_money
            or inventory != expected_inventory
        ):
            return (
                None,
                "purse or selected-character inventory changed between bound units.",
                None,
            )
        return binding, binding.reason, result.snapshot

    def _ensure_trade_can_continue(self, operation: str) -> None:
        if self.controller.emergency_stop_pressed(self.emergency_stop_key):
            raise RuntimeError(
                f"Emergency stop interrupted the {operation}; "
                "no further input was sent."
            )
        if self.controller.user_input_detected():
            raise RuntimeError(
                f"Human input interrupted the {operation}; no further input was sent."
            )

    async def _wait_for_trade_conservation(
        self,
        *,
        item_name: str,
        direction: Literal["purchase", "sale"],
        selected_character_id: str,
        money_before: int,
        inventory_before: int,
        after_sequence: int,
        remaining_quantity: int,
        message_baseline: dict[str, str],
        timeout_seconds: float,
    ) -> tuple[
        Literal["transferred", "refused", "not_transferred", "outcome_unknown"],
        int | None,
        int | None,
        int | None,
        str,
    ]:
        deadline = time.monotonic() + timeout_seconds
        latest: tuple[int, int, int] | None = None
        mismatch_reason: str | None = None
        while True:
            try:
                result = self.telemetry_reader.read()
            except TelemetryReadError:
                result = None
            if (
                result is not None
                and not result.stale
                and result.snapshot.sequence > after_sequence
            ):
                try:
                    _, money_after, inventory_after = self._trade_state(
                        result.snapshot,
                        item_name,
                        expected_character_id=selected_character_id,
                    )
                except RuntimeError as exc:
                    mismatch_reason = str(exc)
                else:
                    latest = (
                        money_after,
                        inventory_after,
                        result.snapshot.sequence,
                    )
                    money_delta = (
                        money_before - money_after
                        if direction == "purchase"
                        else money_after - money_before
                    )
                    inventory_delta = (
                        inventory_after - inventory_before
                        if direction == "purchase"
                        else inventory_before - inventory_after
                    )
                    money_label = (
                        "purse loss" if direction == "purchase" else "purse gain"
                    )
                    inventory_label = (
                        "carried-item gain"
                        if direction == "purchase"
                        else "carried-item loss"
                    )
                    if (
                        money_delta > 0
                        and 1 <= inventory_delta <= remaining_quantity
                    ):
                        return (
                            "transferred",
                            money_after,
                            inventory_after,
                            result.snapshot.sequence,
                            (
                                f"Observed c.{money_delta} {money_label} and "
                                f"{inventory_delta} matching {inventory_label}."
                            ),
                        )
                    if money_delta != 0 or inventory_delta != 0:
                        mismatch_reason = (
                            f"{money_label} {money_delta} and {inventory_label} "
                            f"{inventory_delta} do not conservatively match the "
                            f"remaining bound {remaining_quantity}."
                        )
                    else:
                        refusal = causally_new_game_message(
                            result.snapshot,
                            message_baseline,
                        )
                        if refusal is not None:
                            return (
                                "refused",
                                money_after,
                                inventory_after,
                                result.snapshot.sequence,
                                f"Kenshi refused the {direction}: {refusal}",
                            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if mismatch_reason is not None:
                    values = latest or (None, None, None)
                    return (
                        "outcome_unknown",
                        values[0],
                        values[1],
                        values[2],
                        mismatch_reason,
                    )
                if latest is not None:
                    return (
                        "not_transferred",
                        latest[0],
                        latest[1],
                        latest[2],
                        "later telemetry showed no purse or selected-inventory change.",
                    )
                return (
                    "outcome_unknown",
                    None,
                    None,
                    None,
                    "no causally later complete inventory observation arrived.",
                )
            await asyncio.sleep(min(self._NATIVE_COMMAND_POLL_SECONDS, remaining))

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

    async def _execute_open_screen(
        self,
        action: OpenScreenAction,
        started: datetime,
    ) -> ActionReceipt:
        """Have the named screen open, pressing nothing when it already is.

        The agent used to name a binding and author its own proof that the
        binding worked. One live run then looped: a window it had asked for
        opened, telemetry did not show it, and nothing could tell the agent it
        had already succeeded. Here the controller reads the exact screen state,
        presses only when it needs to, and the contract's terminal proves which
        screen arrived rather than that something changed.
        """

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "No input was sent: telemetry became stale inside the input lease."
            )
        observation = self._observation_from_snapshot(result.snapshot)
        binding = OPEN_SCREEN_CONTRACT.bind(action, observation)
        if not binding.bound:
            raise RuntimeError(f"No input was sent: {binding.reason}")

        already = screen_is_open(action.screen, result.snapshot)
        control = SCREEN_BINDINGS[action.screen]
        if already:
            semantic = SemanticActionReceipt(
                action_kind=action.kind,
                contract_version=OPEN_SCREEN_CONTRACT.version,
                resolved_label=action.screen.value,
                source_revision=observation.world_revision,
                revalidation=binding.reason,
            )
            return ActionReceipt(
                action=action,
                control_mode=self.control_mode,
                accepted=True,
                executed=True,
                dry_run=False,
                primitive_actions=0,
                started_at=started,
                finished_at=datetime.now(UTC),
                message=(
                    f"The {action.screen.value} screen was already open, so no "
                    "key was pressed."
                ),
                semantic=semantic,
            )

        primitive = game_binding_primitive(control)
        await self.controller.execute(primitive)
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=OPEN_SCREEN_CONTRACT.version,
            resolved_label=action.screen.value,
            source_revision=observation.world_revision,
            revalidation=binding.reason,
        )
        return ActionReceipt(
            action=action,
            control_mode=self.control_mode,
            accepted=True,
            executed=True,
            dry_run=False,
            primitive_actions=1,
            started_at=started,
            finished_at=datetime.now(UTC),
            message=(
                f"Pressed {control.value} to open the {action.screen.value} "
                "screen. A later observation must confirm it arrived."
            ),
            semantic=semantic,
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
        quicksave_before = (
            _quicksave_tree_state(self.quicksave_dir)
            if action.binding is GameBinding.QUICKSAVE
            and self.quicksave_dir is not None
            else None
        )
        primitive = game_binding_primitive(action.binding)
        primitive_receipt = await self.controller.execute(primitive)
        if isinstance(primitive, KeyAction):
            mapped_input = primitive.key
        elif isinstance(primitive, HotkeyAction):
            mapped_input = "+".join(primitive.keys)
        else:
            mapped_input = primitive.button.value
        quicksave = None
        if action.binding is GameBinding.QUICKSAVE:
            if self.quicksave_dir is None or quicksave_before is None:
                raise RuntimeError(
                    "Quicksave completion monitoring disappeared before input."
                )
            quicksave = await self._wait_for_quicksave_completion(
                self.quicksave_dir,
                quicksave_before,
            )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=USE_GAME_BINDING_CONTRACT.version,
            resolved_label=action.binding.value,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-confirmed a game was loaded inside the input lease before "
                f"pressing the key. {binding.reason}"
            ),
            quicksave=quicksave,
        )
        completion_message = (
            f" {quicksave.reason}"
            if quicksave is not None
            else " A later observation must confirm the transition."
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "message": (
                    f"Pressed Kenshi's {action.binding.value!r} binding "
                    f"({mapped_input!r}), "
                    f"expecting: {action.expected_effect}."
                    f"{completion_message}"
                ),
            }
        )

    async def _wait_for_quicksave_completion(
        self,
        path: Path,
        before: _QuicksaveTreeState,
    ) -> QuicksaveEvidence:
        """Require an exact changed slot to stop mutating after F5."""

        deadline = time.monotonic() + self.quicksave_timeout_seconds
        previous = before
        latest = before
        stable_since: float | None = None
        last_error: OSError | RuntimeError | None = None
        while time.monotonic() < deadline:
            now = time.monotonic()
            try:
                current = _quicksave_tree_state(path)
                last_error = None
            except (OSError, RuntimeError) as exc:
                last_error = exc
                stable_since = None
                await asyncio.sleep(0.05)
                continue
            latest = current
            changed = current.files != before.files
            complete_file = current.quick_save_size_bytes is not None
            if changed and complete_file:
                if current.files != previous.files:
                    stable_since = now
                elif stable_since is None:
                    stable_since = now
                elif now - stable_since >= self.quicksave_stable_seconds:
                    changed_files = _changed_quicksave_files(before, current)
                    return QuicksaveEvidence(
                        status=QuicksaveStatus.SAVED,
                        changed_files=changed_files,
                        quick_save_size_bytes=current.quick_save_size_bytes,
                        quiescent_seconds=now - stable_since,
                        reason=(
                            "Observed the exact quicksave tree change after F5 "
                            f"and remain quiescent for {now - stable_since:.3f}s."
                        ),
                    )
            else:
                stable_since = None
            previous = current
            await asyncio.sleep(
                min(0.05, max(0.005, self.quicksave_stable_seconds / 2.0))
            )
        changed_files = _changed_quicksave_files(before, latest)
        error = (
            f" Last monitor error: {type(last_error).__name__}: {last_error}."
            if last_error is not None
            else ""
        )
        return QuicksaveEvidence(
            status=QuicksaveStatus.NOT_OBSERVED,
            changed_files=changed_files,
            quick_save_size_bytes=latest.quick_save_size_bytes,
            quiescent_seconds=0.0,
            reason=(
                "F5 was sent, but the exact quicksave tree did not produce a "
                "changed, nonempty, quiescent quick.save before the completion "
                f"timeout.{error}"
            ),
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
        """Sell a bounded quantity with per-unit identity and conservation proof."""

        outcome = await self._execute_bounded_trade(
            action,
            SELL_ITEM_CONTRACT,
            direction="sale",
            observation_timeout_seconds=self._SALE_OBSERVATION_TIMEOUT_SECONDS,
        )
        status = {
            "completed": SaleStatus.SOLD,
            "partial": SaleStatus.PARTIALLY_SOLD,
            "not_completed": SaleStatus.NOT_SOLD,
            "outcome_unknown": SaleStatus.OUTCOME_UNKNOWN,
        }[outcome.status]
        evidence = SaleEvidence(
            status=status,
            buyer_id=action.buyer_id,
            selected_character_id=outcome.selected_character_id,
            item_name=action.item_name,
            requested_quantity=action.quantity,
            sold_quantity=outcome.completed_quantity,
            money_before=outcome.money_before,
            money_after=outcome.money_after,
            inventory_quantity_before=outcome.inventory_quantity_before,
            inventory_quantity_after=outcome.inventory_quantity_after,
            observed_after_sequence=outcome.observed_after_sequence,
            reason=outcome.reason,
        )
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=SELL_ITEM_CONTRACT.version,
            target_id=action.buyer_id,
            resolved_label=outcome.initial_binding.resolved_label,
            resolved_role=outcome.initial_binding.resolved_role,
            resolved_bounds=outcome.initial_binding.resolved_bounds,
            source_revision=outcome.initial_observation.world_revision,
            revalidation=(
                "Re-bound the exact selected-character-owned item cell before "
                "every unit and required a later matching purse gain plus "
                "selected-character inventory loss before continuing. "
                f"{outcome.initial_binding.reason}"
            ),
            sale=evidence,
        )
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=outcome.primitive_actions > 0,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=outcome.primitive_actions,
            message=outcome.reason,
            semantic=semantic,
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

    async def _execute_collect_resource_output(
        self,
        action: CollectResourceOutputAction,
        started: datetime,
    ) -> ActionReceipt:
        """Right-click exact output, retaining both inventory baselines."""

        del started
        binding, observation = self._rebind_in_lease(
            COLLECT_RESOURCE_OUTPUT_CONTRACT,
            action,
        )
        bounds = binding.resolved_bounds
        assert bounds is not None
        baseline = begin_resource_transfer(action, observation)
        if (
            baseline.source_quantity_before is None
            or baseline.destination_quantity_before is None
            or baseline.selected_character_id is None
        ):
            raise RuntimeError(
                "No input was sent: complete source and destination baselines "
                "could not be retained."
            )
        x = (bounds.min_x + bounds.max_x) / 2.0
        y = (bounds.min_y + bounds.max_y) / 2.0
        move_receipt = await self.controller.execute(MoveCursorAction(x=x, y=y))
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
            contract_version=COLLECT_RESOURCE_OUTPUT_CONTRACT.version,
            target_id=action.target_id,
            resolved_label=binding.resolved_label,
            resolved_role=binding.resolved_role,
            resolved_bounds=bounds,
            source_revision=observation.world_revision,
            revalidation=(
                "Re-proved exact resource identity, output section, item, "
                f"quantity, bounds, and complete destination in-lease. {binding.reason}"
            ),
            resource_transfer=baseline,
        )
        return primitive_receipt.model_copy(
            update={
                "action": action,
                "semantic": semantic,
                "primitive_actions": (
                    move_receipt.primitive_actions
                    + primitive_receipt.primitive_actions
                ),
                "message": (
                    f"Sent the transfer gesture for {action.source_quantity} "
                    f"{action.item_name!r}; awaiting conserved source loss and "
                    "destination gain."
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

    @staticmethod
    def _accepted_native_terminal_receipt(
        *,
        action: Action,
        command: CommandDispatchContext,
        started: datetime,
        primitive_count: int,
        messages: list[str],
        acknowledgement: NativeCommandAcknowledgement,
        semantic: SemanticActionReceipt | None,
    ) -> ActionReceipt:
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
                if acknowledgement.status is NativeCommandStatus.CANCELLED
                else None
            ),
            native_acknowledgement=acknowledgement,
            semantic=semantic,
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
            "select_squad_member",
            "regroup_with_squad_member",
            "move_in_direction",
            "travel_to_map_destination",
            "exit_current_building",
            "perform_context_action",
            "produce_resource_output",
            "open_context_inventory",
        ] = NATIVE_APPROACH_WIRE_COMMAND,
        require_dialogue_target: bool = True,
        bearing_degrees: float = 0.0,
        distance_units: float = 0.0,
        semantic: SemanticActionReceipt | None = None,
        continue_until_terminal: bool = False,
        accepted_is_terminal_error: bool = False,
        minimum_output_quantity: int = 1,
        running_speed_gear: int = 1,
        expected_actor_id: str | None = None,
        context_action: ContextActionKind | None = None,
    ) -> ActionReceipt:
        adopted = (
            self._active_native_order_for(
                wire_command=wire_command,
                target_id=target_id,
                bearing_degrees=bearing_degrees,
                distance_units=distance_units,
                minimum_output_quantity=minimum_output_quantity,
                context_action=context_action,
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
                minimum_output_quantity=minimum_output_quantity,
                expected_actor_id=expected_actor_id,
                context_action=context_action,
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
            return self._accepted_native_terminal_receipt(
                action=action,
                command=command,
                started=started,
                primitive_count=primitive_count,
                messages=messages,
                acknowledgement=acknowledgement,
                semantic=semantic,
            )
        if accepted_is_terminal_error:
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
                    " ".join(messages)
                    + " This command requires an immediate native terminal; "
                    "accepted-only is inconclusive and will not be retried."
                ),
                error_type="NativeCommandIncomplete",
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
            try:
                if paused:
                    # A relative-pointer trip to the Play button can consume the
                    # native command's paused-order watchdog after acceptance.
                    # Gear 1 is an idempotent running-state selector, and the
                    # native-aware playback helper composes running confirmation
                    # with the same command's exact terminal.
                    running_count, playback_terminal = (
                        await self._establish_native_running_state(
                            acknowledgement
                        )
                    )
                    primitive_count += running_count
                    if playback_terminal is not None:
                        acknowledgement = playback_terminal
                        messages.append(
                            "The keyed native command reached its terminal while "
                            "playback ownership was being established; that "
                            "terminal takes precedence over running-state "
                            "confirmation: "
                            f"{playback_terminal.status.value} "
                            f"({playback_terminal.reason or 'no reason'})."
                        )
                        return self._accepted_native_terminal_receipt(
                            action=action,
                            command=command,
                            started=started,
                            primitive_count=primitive_count,
                            messages=messages,
                            acknowledgement=acknowledgement,
                            semantic=semantic,
                        )
                    messages.append(
                        "Started the paused world at speed gear 1; the monitored "
                        "option now owns the running movement."
                    )
                if running_speed_gear != 1:
                    primitive_count += await self._establish_playback_gear(
                        running_speed_gear
                    )
                    messages.append(
                        "Established controller-owned 5x playback speed for long travel."
                    )
            except RuntimeError:
                terminal = self._fresh_matching_native_acknowledgement(
                    acknowledgement
                )
                if (
                    terminal is None
                    or terminal.status
                    not in {
                        NativeCommandStatus.CANCELLED,
                        NativeCommandStatus.COMPLETED,
                    }
                ):
                    raise
                acknowledgement = terminal
                messages.append(
                    "The keyed native command reached its terminal while playback "
                    "ownership was being established; that terminal takes "
                    "precedence over an intermediate running-state confirmation: "
                    f"{terminal.status.value} ({terminal.reason or 'no reason'})."
                )
                return self._accepted_native_terminal_receipt(
                    action=action,
                    command=command,
                    started=started,
                    primitive_count=primitive_count,
                    messages=messages,
                    acknowledgement=acknowledgement,
                    semantic=semantic,
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
            "select_squad_member",
            "regroup_with_squad_member",
            "move_in_direction",
            "travel_to_map_destination",
            "exit_current_building",
            "perform_context_action",
            "produce_resource_output",
            "open_context_inventory",
        ],
        target_id: str,
        bearing_degrees: float,
        distance_units: float,
        minimum_output_quantity: int,
        context_action: ContextActionKind | None,
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
            or acknowledgement.minimum_output_quantity
            != minimum_output_quantity
            or acknowledgement.context_action != (context_action or "")
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
        if (
            len(acknowledgement.selected_character_ids) != len(selected_ids)
            or set(acknowledgement.selected_character_ids) != set(selected_ids)
        ):
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
            "select_squad_member",
            "regroup_with_squad_member",
            "move_in_direction",
            "travel_to_map_destination",
            "exit_current_building",
            "perform_context_action",
            "produce_resource_output",
            "open_context_inventory",
        ] = NATIVE_APPROACH_WIRE_COMMAND,
        require_dialogue_target: bool = True,
        bearing_degrees: float = 0.0,
        distance_units: float = 0.0,
        minimum_output_quantity: int = 1,
        expected_actor_id: str | None = None,
        context_action: ContextActionKind | None = None,
    ) -> NativeCommandRequest:
        """Build the native pathing request for one exact stable target.

        `require_vendor_role` is what separates the legacy vendor macro from the
        generic dialogue-target action. The generic action asks only for the
        authorization fact it actually needs — a conscious, non-hostile,
        non-animal person with dialogue — because approaching and talking is not
        a commerce affordance.
        """

        # Re-read at issue time and re-prove every authorization fact on the
        # newest sequence. The plug-in admits only its four-publication
        # cross-process transport window, then independently revalidates the
        # current selection, target, role, UI state, and command-specific
        # authority. Issuing on anything older would waste that bounded window
        # before the atomic file + hotkey + UI-hook handoff even begins.
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
        elif wire_command == NATIVE_MAP_TRAVEL_WIRE_COMMAND:
            native_contract = TRAVEL_TO_MAP_DESTINATION_CONTRACT
        elif wire_command == NATIVE_SQUAD_SELECTION_WIRE_COMMAND:
            native_contract = SELECT_SQUAD_MEMBER_EXACT_CONTRACT
        elif wire_command == NATIVE_SQUAD_REGROUP_WIRE_COMMAND:
            native_contract = REGROUP_WITH_SQUAD_MEMBER_CONTRACT
        elif wire_command == NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            native_contract = EXIT_CURRENT_BUILDING_CONTRACT
        elif wire_command == NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND:
            native_contract = PRODUCE_RESOURCE_OUTPUT_CONTRACT
        elif wire_command == NATIVE_OPEN_CONTEXT_INVENTORY_WIRE_COMMAND:
            native_contract = OPEN_CONTEXT_INVENTORY_CONTRACT
        elif wire_command == NATIVE_CONTEXT_ACTION_WIRE_COMMAND:
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
        group_selection_command = wire_command in {
            NATIVE_SQUAD_SELECTION_WIRE_COMMAND,
            NATIVE_MAP_TRAVEL_WIRE_COMMAND,
        }
        if (
            not selected_ids
            or telemetry.ui.selected_character_id not in selected_ids
            or (not group_selection_command and len(selected_ids) != 1)
        ):
            raise RuntimeError(
                "Native command selection does not satisfy the action's exact "
                "selection-cardinality contract."
            )
        if expected_actor_id is not None and selected_ids != [expected_actor_id]:
            raise RuntimeError(
                "Native squad regrouping requires actor_id to remain the exact "
                "current selection at issue time."
            )
        if wire_command == NATIVE_DIRECTION_WIRE_COMMAND:
            # References nobody: the destination is derived from where the
            # character already stands, which is what makes it available in a
            # place a destination list would be empty.
            return NativeCommandRequest(
                schema_version="1.2",
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
                schema_version="1.2",
                command_id=command.command_id,
                command=wire_command,
                control_mode=ControlMode.NATIVE_ASSISTED,
                identity_session_id=telemetry.identity_session_id,
                based_on_revision=observation.world_revision,
                selected_character_ids=list(selected_ids),
            )
        if wire_command == NATIVE_MAP_TRAVEL_WIRE_COMMAND:
            map_destinations = [
                destination
                for destination in telemetry.known_map_destinations
                if destination.id == target_id
            ]
            if len(map_destinations) != 1:
                raise RuntimeError(
                    "Native map destination is absent, undiscovered, or ambiguous "
                    "at issue time."
                )
            return NativeCommandRequest(
                schema_version="1.2",
                command_id=command.command_id,
                command=wire_command,
                control_mode=ControlMode.NATIVE_ASSISTED,
                identity_session_id=telemetry.identity_session_id,
                based_on_revision=observation.world_revision,
                selected_character_ids=list(selected_ids),
                target_id=target_id,
            )
        if wire_command == NATIVE_SQUAD_SELECTION_WIRE_COMMAND:
            target_matches = [
                member for member in telemetry.squad if member.id == target_id
            ]
            if len(target_matches) != 1:
                raise RuntimeError(
                    "Native squad-selection target is absent or ambiguous at "
                    "issue time."
                )
            return NativeCommandRequest(
                schema_version="1.2",
                command_id=command.command_id,
                command=wire_command,
                control_mode=ControlMode.NATIVE_ASSISTED,
                identity_session_id=telemetry.identity_session_id,
                based_on_revision=observation.world_revision,
                selected_character_ids=list(selected_ids),
                target_id=target_id,
            )
        if wire_command == NATIVE_SQUAD_REGROUP_WIRE_COMMAND:
            target_matches = [
                member
                for member in telemetry.squad
                if member.id == target_id and member.id not in selected_ids
            ]
            if len(target_matches) != 1 or target_matches[0].alive is not True:
                raise RuntimeError(
                    "Native squad-regroup target is absent, not distinct from the "
                    "actor, ambiguous, or not confirmed alive at issue time."
                )
            return NativeCommandRequest(
                schema_version="1.2",
                command_id=command.command_id,
                command=wire_command,
                control_mode=ControlMode.NATIVE_ASSISTED,
                identity_session_id=telemetry.identity_session_id,
                based_on_revision=observation.world_revision,
                selected_character_ids=list(selected_ids),
                target_id=target_id,
            )
        if not target_id:
            raise RuntimeError("Native approach requires an exact target_id.")
        if wire_command in {
            NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
            NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
            NATIVE_OPEN_CONTEXT_INVENTORY_WIRE_COMMAND,
        }:
            if wire_command == NATIVE_CONTEXT_ACTION_WIRE_COMMAND:
                if context_action is None:
                    raise RuntimeError(
                        "Native context execution requires an exact semantic."
                    )
                expected_context_action = context_action
                request_context_action: ContextActionKind | Literal[""] = context_action
            else:
                expected_context_action = ContextActionKind.OPERATE
                request_context_action = ""
            matches = [
                target
                for target in telemetry.world_targets
                if target.id == target_id
                and expected_context_action in target.context_actions
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    "Native context target is absent or no longer advertises "
                    f"the exact {expected_context_action.value!r} action."
                )
            return NativeCommandRequest(
                schema_version="1.2",
                command_id=command.command_id,
                command=wire_command,
                control_mode=ControlMode.NATIVE_ASSISTED,
                identity_session_id=telemetry.identity_session_id,
                based_on_revision=observation.world_revision,
                selected_character_ids=list(selected_ids),
                target_id=target_id,
                context_action=request_context_action,
                minimum_output_quantity=minimum_output_quantity,
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
            schema_version="1.2",
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
                        or acknowledgement.minimum_output_quantity
                        != request.minimum_output_quantity
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

    def _fresh_matching_native_acknowledgement(
        self,
        previous: NativeCommandAcknowledgement,
    ) -> NativeCommandAcknowledgement | None:
        """Read a later verdict without weakening the command identity fence."""

        try:
            result = self.telemetry_reader.read()
        except TelemetryReadError:
            return None
        if result.stale:
            return None
        return self._matching_native_acknowledgement(
            result.snapshot,
            previous,
        )

    @staticmethod
    def _matching_native_acknowledgement(
        snapshot: TelemetrySnapshot,
        previous: NativeCommandAcknowledgement,
    ) -> NativeCommandAcknowledgement | None:
        current = snapshot.native_control.acknowledgement_for(
            previous.command_id
        )
        if current is None:
            return None
        identity = (
            current.command,
            current.target_id,
            current.bearing_degrees,
            current.distance_units,
            current.minimum_output_quantity,
            current.selected_character_ids,
            current.based_on_telemetry_sequence,
        )
        previous_identity = (
            previous.command,
            previous.target_id,
            previous.bearing_degrees,
            previous.distance_units,
            previous.minimum_output_quantity,
            previous.selected_character_ids,
            previous.based_on_telemetry_sequence,
        )
        if identity != previous_identity:
            raise RuntimeError(
                "Native command acknowledgement changed identity while playback "
                "ownership was being established."
            )
        return current

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
