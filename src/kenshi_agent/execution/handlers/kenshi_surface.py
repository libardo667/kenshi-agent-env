"""External Kenshi mechanics shared by every operation handler family.

This surface owns only what talks to the host: the input lease, calibration,
primitive delivery, native command transport and acknowledgement, playback
state, and the causal receipt envelope. It holds no operation semantics, so a
family handler decides *what* to do and this decides *how* it reaches Kenshi.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from ... import operation_definitions as operations
from ...config import CaptureConfig, ControlsConfig, RuntimeConfig
from ...control.base import InputController, PrimitiveInputAction
from ...control.calibration import (
    calibration_allows_input,
    evaluate_calibration_identity,
    validate_expected_client_size,
)
from ...control.capture import CapturedFrame
from ...input_boundary import ExecutionToken
from ...models import (
    GAME_SPEED_MULTIPLIER_BY_GEAR,
    Action,
    ActionReceipt,
    ApproachDialogueTargetAction,
    CalibrationReport,
    ClickAction,
    CommandDispatchContext,
    ContextActionKind,
    ControlMode,
    HotkeyAction,
    InputBoundaryDecision,
    KeyAction,
    MouseButtonAction,
    MouseDragAction,
    MoveCursorAction,
    NativeCommandAcknowledgement,
    NativeCommandRequest,
    NativeCommandStatus,
    Observation,
    PointerActionClass,
    ScrollAction,
    SemanticActionReceipt,
    SetSpeedAction,
    SkillAction,
    SkillArgument,
    TelemetrySnapshot,
    Transition,
)
from ...native_commands import write_native_command_request_atomic
from ...skills import MacroRegistry
from ...telemetry import TelemetryReader, TelemetryReadError

NATIVE_COMMAND_REQUEST_FILE = "native_command.request.json"
NATIVE_COMMAND_ACK_TIMEOUT_SECONDS = 2.0
NATIVE_COMMAND_POLL_SECONDS = 0.025
NATIVE_DIALOGUE_SETTLE_SECONDS = 1.0

BindingT = TypeVar("BindingT")

_NATIVE_DEFINITION_BY_WIRE_COMMAND = {
    operations.NATIVE_DIRECTION_WIRE_COMMAND: operations.MOVE_IN_DIRECTION_DEFINITION,
    operations.NATIVE_MAP_TRAVEL_WIRE_COMMAND: operations.TRAVEL_TO_MAP_DESTINATION_DEFINITION,
    operations.NATIVE_SQUAD_SELECTION_WIRE_COMMAND: (
        operations.SELECT_SQUAD_MEMBER_EXACT_DEFINITION
    ),
    operations.NATIVE_SQUAD_REGROUP_WIRE_COMMAND: (operations.REGROUP_WITH_SQUAD_MEMBER_DEFINITION),
    operations.NATIVE_EXIT_BUILDING_WIRE_COMMAND: operations.EXIT_CURRENT_BUILDING_DEFINITION,
    operations.NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND: (
        operations.PRODUCE_RESOURCE_OUTPUT_DEFINITION
    ),
    operations.NATIVE_OPEN_CONTEXT_INVENTORY_WIRE_COMMAND: (
        operations.OPEN_CONTEXT_INVENTORY_DEFINITION
    ),
    operations.NATIVE_CONTEXT_ACTION_WIRE_COMMAND: operations.PERFORM_CONTEXT_ACTION_DEFINITION,
    operations.NATIVE_MOVE_WIRE_COMMAND: operations.MOVE_TO_CHARACTER_DEFINITION,
}


class LiveCapturePort(Protocol):
    def capture(self, sequence: int) -> CapturedFrame: ...


class LiveExternalPort(Protocol):
    """External host state available to live operation mechanics."""

    controller: InputController
    telemetry_reader: TelemetryReader
    macros: MacroRegistry
    runtime_config: RuntimeConfig
    controls_config: ControlsConfig
    capture_config: CaptureConfig
    execute_actions: bool
    emergency_stop_key: str
    quicksave_dir: Path | None
    quicksave_stable_seconds: float
    quicksave_timeout_seconds: float
    run_id: str
    control_mode: ControlMode
    available_skills: list[str]
    _step_index: int
    _capture_sequence: int
    _capability_epoch: int
    _last_observation: Observation | None

    @property
    def _capture(self) -> LiveCapturePort | None: ...

    async def observe(self) -> Observation: ...

    async def observe_without_capture(self) -> Observation: ...

    def _apply_control_mode(self, snapshot: TelemetrySnapshot) -> TelemetrySnapshot: ...

    def _observation_from_snapshot(
        self,
        snapshot: TelemetrySnapshot,
        *,
        telemetry_stale: bool = False,
        telemetry_age_seconds: float = 0.0,
        events: list[str] | None = None,
    ) -> Observation: ...


class KenshiControlSurface:
    """One external delivery path into a running Kenshi process."""

    def __init__(self, port: LiveExternalPort) -> None:
        self._port = port

    @property
    def port(self) -> LiveExternalPort:
        """The external adapter this surface drives."""

        return self._port

    @property
    def controller(self) -> InputController:
        return self._port.controller

    @property
    def telemetry_reader(self) -> TelemetryReader:
        return self._port.telemetry_reader

    @property
    def macros(self) -> MacroRegistry:
        return self._port.macros

    @property
    def runtime_config(self) -> RuntimeConfig:
        return self._port.runtime_config

    @property
    def controls_config(self) -> ControlsConfig:
        return self._port.controls_config

    @property
    def capture_config(self) -> CaptureConfig:
        return self._port.capture_config

    @property
    def last_observation(self) -> Observation | None:
        return self._port._last_observation

    def classify_pointer_action(self, action: Action) -> PointerActionClass:
        """Classify the calibration authority required by one exact operation."""

        contract = operations.definition_for(action)
        if contract is not None and not isinstance(action, SkillAction):
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

    async def run_exact(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        token: ExecutionToken | None,
        receipt: Callable[
            [Action, datetime, CommandDispatchContext | None],
            Awaitable[ActionReceipt],
        ],
    ) -> Transition:
        """Own one live delivery lifecycle around a handler-selected mechanic.

        The caller selects the exact receipt function. This method contains no
        operation routing; it owns only the live input lease, fresh boundary
        check, observation, and causal receipt envelope shared by all Kenshi
        mechanics.
        """

        started = datetime.now(UTC)
        if not self._port.execute_actions:
            action_receipt = ActionReceipt(
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
            if self.controller.emergency_stop_pressed(self._port.emergency_stop_key):
                raise RuntimeError(
                    f"Emergency stop key {self._port.emergency_stop_key!r} is pressed; "
                    "action aborted."
                )
            async with self.controller.input_lease(alt_tab_on_restore=True):
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
                if boundary is not None and boundary.decision is (InputBoundaryDecision.REJECTED):
                    action_receipt = ActionReceipt(
                        action=action,
                        accepted=False,
                        executed=False,
                        dry_run=False,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                        primitive_actions=0,
                        message=(
                            "No input was emitted: the state that authorized this "
                            "action changed while the input lease was pending. "
                            f"{boundary.reason}"
                        ),
                        error_type="InputBoundaryRejected",
                    )
                elif not calibration_allows_input(calibration):
                    self._raise_for_calibration(calibration)
                    raise AssertionError("Calibration rejection did not raise.")
                else:
                    action_receipt = await receipt(action, started, command)
                action_receipt = action_receipt.model_copy(
                    update={"input_boundary": boundary, "calibration": calibration}
                )
            if lease_wait >= 0.01:
                action_receipt = action_receipt.model_copy(
                    update={
                        "message": (
                            f"Waited {lease_wait:.2f}s for a quiet input turn. "
                            + action_receipt.message
                        )
                    }
                )
        return await self._finish_transition(action_receipt, command)

    async def run_without_input(
        self,
        action: Action,
        *,
        command: CommandDispatchContext,
        receipt: Callable[
            [Action, datetime, CommandDispatchContext | None],
            Awaitable[ActionReceipt],
        ],
    ) -> Transition:
        """Deliver a deterministic zero-input operation through the same envelope."""

        started = datetime.now(UTC)
        action_receipt = await receipt(action, started, command)
        return await self._finish_transition(action_receipt, command)

    async def _finish_transition(
        self,
        receipt: ActionReceipt,
        command: CommandDispatchContext,
    ) -> Transition:
        receipt = receipt.model_copy(update={"control_mode": self._port.control_mode})
        self._port._step_index += 1
        if self.runtime_config.settle_seconds:
            await asyncio.sleep(self.runtime_config.settle_seconds)
        observation = await self._port.observe()
        if receipt.native_acknowledgement is not None and observation.telemetry is not None:
            latest = observation.telemetry.native_control.acknowledgement_for(
                receipt.native_acknowledgement.command_id
            )
            if latest is not None and latest != receipt.native_acknowledgement:
                receipt = receipt.model_copy(
                    update={
                        "native_acknowledgement": latest,
                        "message": (
                            receipt.message
                            + f" Latest native status is {latest.status.value!r}: "
                            + f"{latest.reason}."
                        ),
                        "error_type": (
                            "NativeCommandCancelled"
                            if latest.status is NativeCommandStatus.CANCELLED
                            else receipt.error_type
                        ),
                    }
                )
        receipt = receipt.model_copy(
            update={
                "command_id": command.command_id,
                "started_after_revision": command.based_on_revision,
                "completed_at_revision": observation.world_revision,
                "causal_revision_advanced": observation.world_revision.is_later_than(
                    command.based_on_revision
                ),
            }
        )
        return Transition(
            receipt=receipt,
            observation=observation,
            terminated=False,
            success=None,
            events=observation.events,
        )

    async def require_command(
        self, command: CommandDispatchContext | None
    ) -> CommandDispatchContext:
        if command is None:
            raise RuntimeError("Native command execution requires caller-owned command context.")
        return command

    async def run_skill_primitives(self, action: SkillAction) -> tuple[int, list[str]]:
        primitives = self.macros.expand(action)
        primitive_count = 0
        messages: list[str] = []
        for macro_primitive in primitives:
            if self.controller.user_input_detected():
                raise RuntimeError("User input resumed during macro execution; yielding control.")
            if self.controller.emergency_stop_pressed(self._port.emergency_stop_key):
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

    def pause_primitives(self, paused: bool) -> tuple[list[PrimitiveInputAction], str]:
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

    async def apply_pause_request(
        self,
        paused: bool,
        *,
        safety: bool = False,
    ) -> tuple[int, str]:
        primitives, description = self.pause_primitives(paused)
        primitive_count = 0
        for primitive in primitives:
            execute = self.controller.execute_safety if safety else self.controller.execute
            receipt = await execute(primitive)
            primitive_count += receipt.primitive_actions
        return primitive_count, description

    async def apply_playback_speed(
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
            raise RuntimeError("Refusing to set playback speed from stale telemetry.")
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
                    f"Kenshi already reports running at speed gear {action.speed} ({expected:g}x)."
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
                    if current is not None and current.status in {
                        NativeCommandStatus.CANCELLED,
                        NativeCommandStatus.COMPLETED,
                    }:
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
        if self.controller.emergency_stop_pressed(self._port.emergency_stop_key):
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

    async def run_movement_pulse(
        self,
        action: SkillAction,
        started: datetime,
        *,
        pulse_seconds: float,
        prepared_primitives: tuple[int, list[str]] | None = None,
    ) -> ActionReceipt:
        paused = (
            self.last_observation.telemetry.game.paused
            if self.last_observation is not None and self.last_observation.telemetry is not None
            else None
        )
        if self.controls_config.require_paused_between_actions and paused is not True:
            raise RuntimeError(
                f"Movement pulse {action.name!r} requires confirmed paused live state."
            )

        if prepared_primitives is None:
            primitive_count, messages = await self.run_skill_primitives(action)
        else:
            primitive_count, messages = prepared_primitives
        unpause_sent = False
        emergency_stop = False
        user_interrupted = False
        auto_paused = False
        try:
            unpause_count, _ = await self.apply_pause_request(False)
            unpause_sent = True
            primitive_count += unpause_count
            if not await self.wait_for_pause_state(False):
                if self._fresh_pause_state() is True:
                    unpause_sent = False
                raise RuntimeError("Kenshi did not confirm unpaused state for movement pulse.")

            deadline = time.monotonic() + pulse_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self.controller.emergency_stop_pressed(self._port.emergency_stop_key):
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
                pause_count, _ = await self.apply_pause_request(True, safety=True)
                primitive_count += pause_count
                if not await self.wait_for_pause_state(True):
                    if self._fresh_pause_state() is False:
                        retry_count, _ = await self.apply_pause_request(True, safety=True)
                        primitive_count += retry_count
                    if not await self.wait_for_pause_state(True):
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

    def native_transport_skill(
        self,
        *,
        target_id: str,
        purpose: str,
    ) -> tuple[SkillAction, float]:
        skill_name = self.controls_config.native_approach_skill
        if skill_name is None or not self.macros.has(skill_name):
            raise RuntimeError(f"{purpose} requires a configured native transport skill.")
        primitive_skill = SkillAction(
            name=skill_name,
            args=[SkillArgument(name="target_id", value=target_id)],
        )
        pulse_seconds = self.macros.resolve_movement_pulse_seconds(primitive_skill)
        if pulse_seconds is None:
            raise RuntimeError(
                f"Configured native transport skill {skill_name!r} has no movement pulse."
            )
        return primitive_skill, pulse_seconds

    def rebind_in_lease(
        self,
        contract: operations.OperationDefinition,
        action: Action,
        binding_type: type[BindingT],
    ) -> tuple[BindingT, Observation]:
        """Re-resolve an action's reference against telemetry read right now."""

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError("No input was sent: telemetry became stale inside the input lease.")
        observation = self._port._observation_from_snapshot(result.snapshot)
        binding = operations.require_bound(contract.bind(action, observation), binding_type)
        return binding, observation

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

    async def run_native_order(
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
        ] = operations.NATIVE_APPROACH_WIRE_COMMAND,
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
            primitive_count, messages = (
                0,
                [
                    f"Continuing the already active approach {adopted.command_id} "
                    f"toward the same target; no second order was issued."
                ],
            )
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
            request_path = self.telemetry_reader.path.parent / NATIVE_COMMAND_REQUEST_FILE
            write_native_command_request_atomic(request_path, request)
            primitive_count, messages = await self.run_skill_primitives(primitive_skill)
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
                    " ".join(messages) + " This command requires an immediate native terminal; "
                    "accepted-only is inconclusive and will not be retried."
                ),
                error_type="NativeCommandIncomplete",
                native_acknowledgement=acknowledgement,
                semantic=semantic,
            )
        if isinstance(action, ApproachDialogueTargetAction) and self._fresh_pause_state() is True:
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
            return await self._resume_native_movement(
                action=action,
                command=command,
                started=started,
                acknowledgement=acknowledgement,
                primitive_count=primitive_count,
                messages=messages,
                semantic=semantic,
                running_speed_gear=running_speed_gear,
            )
        receipt = await self.run_movement_pulse(
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

        return await self._continue_native_approach(
            action=action,
            started=started,
            primitive_skill=primitive_skill,
            pulse_seconds=pulse_seconds,
            acknowledgement=acknowledgement,
            receipt=receipt,
            semantic=semantic,
        )

    async def _resume_native_movement(
        self,
        *,
        action: Action,
        command: CommandDispatchContext,
        started: datetime,
        acknowledgement: NativeCommandAcknowledgement,
        primitive_count: int,
        messages: list[str],
        semantic: SemanticActionReceipt | None,
        running_speed_gear: int,
    ) -> ActionReceipt:
        """Establish running playback or accept a racing native terminal."""

        paused = self._fresh_pause_state()
        if paused is None:
            raise RuntimeError("Native movement cannot determine whether Kenshi is paused.")
        try:
            if paused:
                running_count, playback_terminal = await self._establish_native_running_state(
                    acknowledgement
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
                    "Started the paused world at speed gear 1; the monitored option "
                    "now owns the running movement."
                )
            if running_speed_gear != 1:
                primitive_count += await self._establish_playback_gear(running_speed_gear)
                messages.append("Established controller-owned 5x playback speed for long travel.")
        except RuntimeError:
            terminal = self._fresh_matching_native_acknowledgement(acknowledgement)
            if terminal is None or terminal.status not in {
                NativeCommandStatus.CANCELLED,
                NativeCommandStatus.COMPLETED,
            }:
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
                "Issued the pathing order; the character walks while the world runs. "
                + " ".join(messages)
            ),
            native_acknowledgement=acknowledgement,
            semantic=semantic,
        )

    async def _continue_native_approach(
        self,
        *,
        action: Action,
        started: datetime,
        primitive_skill: SkillAction,
        pulse_seconds: float,
        acknowledgement: NativeCommandAcknowledgement,
        receipt: ActionReceipt,
        semantic: SemanticActionReceipt | None,
    ) -> ActionReceipt:
        """Advance one accepted native order without ever reissuing it."""

        elapsed = pulse_seconds
        budget = self.controls_config.native_approach_max_seconds
        while elapsed < budget:
            latest = await self._port.observe_without_capture()
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
            receipt = await self.run_movement_pulse(
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

        observation = self.last_observation
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
            or acknowledgement.minimum_output_quantity != minimum_output_quantity
            or acknowledgement.context_action != (context_action or "")
        ):
            return None
        if wire_command == operations.NATIVE_DIRECTION_WIRE_COMMAND:
            if (
                acknowledgement.target_id
                or acknowledgement.bearing_degrees != bearing_degrees
                or acknowledgement.distance_units != distance_units
            ):
                return None
        elif wire_command == operations.NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            if (
                acknowledgement.target_id
                or acknowledgement.bearing_degrees != 0.0
                or acknowledgement.distance_units != 0.0
            ):
                return None
            selected = [
                character for character in observation.telemetry.squad if character.selected
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
        if len(acknowledgement.selected_character_ids) != len(selected_ids) or set(
            acknowledgement.selected_character_ids
        ) != set(selected_ids):
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
        ] = operations.NATIVE_APPROACH_WIRE_COMMAND,
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
        observation = self._port._observation_from_snapshot(result.snapshot)
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
            raise RuntimeError("Native command basis regressed behind the authorized revision.")
        telemetry = observation.telemetry
        native_contract = _NATIVE_DEFINITION_BY_WIRE_COMMAND.get(
            wire_command,
            operations.APPROACH_DIALOGUE_TARGET_DEFINITION,
        )
        missing = native_contract.missing_capabilities(set(telemetry.capabilities))
        if missing:
            raise RuntimeError(
                "Native command lacks required capabilities: " + ", ".join(sorted(missing))
            )
        if not telemetry.identity_session_id:
            raise RuntimeError("Native command requires a current identity session.")
        selected_ids = telemetry.ui.selected_character_ids
        group_selection_command = wire_command in {
            operations.NATIVE_APPROACH_WIRE_COMMAND,
            operations.NATIVE_MOVE_WIRE_COMMAND,
            operations.NATIVE_SQUAD_SELECTION_WIRE_COMMAND,
            operations.NATIVE_MAP_TRAVEL_WIRE_COMMAND,
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
        if wire_command == operations.NATIVE_DIRECTION_WIRE_COMMAND:
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
        if wire_command == operations.NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            selected = [character for character in telemetry.squad if character.selected]
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
        if wire_command == operations.NATIVE_MAP_TRAVEL_WIRE_COMMAND:
            map_destinations = [
                destination
                for destination in telemetry.known_map_destinations
                if destination.id == target_id
            ]
            if len(map_destinations) != 1:
                raise RuntimeError(
                    "Native map destination is absent, undiscovered, or ambiguous at issue time."
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
        if wire_command == operations.NATIVE_SQUAD_SELECTION_WIRE_COMMAND:
            target_matches = [member for member in telemetry.squad if member.id == target_id]
            if len(target_matches) != 1:
                raise RuntimeError(
                    "Native squad-selection target is absent or ambiguous at issue time."
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
        if wire_command == operations.NATIVE_SQUAD_REGROUP_WIRE_COMMAND:
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
            operations.NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
            operations.NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
            operations.NATIVE_OPEN_CONTEXT_INVENTORY_WIRE_COMMAND,
        }:
            if wire_command == operations.NATIVE_CONTEXT_ACTION_WIRE_COMMAND:
                if context_action is None:
                    raise RuntimeError("Native context execution requires an exact semantic.")
                expected_context_action = context_action
                request_context_action: ContextActionKind | Literal[""] = context_action
            else:
                expected_context_action = ContextActionKind.OPERATE
                request_context_action = ""
            matches = [
                target
                for target in telemetry.world_targets
                if target.id == target_id and expected_context_action in target.context_actions
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
                "Native command target lacks exact current conscious non-hostile dialogue evidence."
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
        deadline = time.monotonic() + NATIVE_COMMAND_ACK_TIMEOUT_SECONDS
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
                        or acknowledgement.bearing_degrees != request.bearing_degrees
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
                    f"after {NATIVE_COMMAND_ACK_TIMEOUT_SECONDS:.0f}s. "
                    "The order was accepted, so the character may still be walking: "
                    "check whether it arrived before ordering it again."
                )
            await asyncio.sleep(min(NATIVE_COMMAND_POLL_SECONDS, remaining))

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
        current = snapshot.native_control.acknowledgement_for(previous.command_id)
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

    async def wait_for_pause_state(self, expected: bool, *, timeout_seconds: float = 3.0) -> bool:
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

        deadline = time.monotonic() + NATIVE_DIALOGUE_SETTLE_SECONDS
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
                if snapshot.ui.dialogue_open and snapshot.ui.dialogue_target_id == target_id:
                    return True, latest_acknowledgement
                if snapshot.game.paused is not True:
                    return False, latest_acknowledgement
                if latest_acknowledgement is not None and latest_acknowledgement.status in {
                    NativeCommandStatus.REJECTED,
                    NativeCommandStatus.CANCELLED,
                    NativeCommandStatus.COMPLETED,
                }:
                    return False, latest_acknowledgement
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, latest_acknowledgement
            await asyncio.sleep(min(NATIVE_COMMAND_POLL_SECONDS, remaining))

    def _fresh_pause_state(self) -> bool | None:
        try:
            result = self.telemetry_reader.read()
        except TelemetryReadError:
            return None
        if result.stale:
            return None
        return result.snapshot.game.paused
