"""External Kenshi mechanics shared by every operation handler family.

This surface owns only what talks to the host: the input lease, calibration,
primitive delivery, native command transport and acknowledgement, playback
state, and the causal receipt envelope. It holds no operation semantics, so a
family handler decides *what* to do and this decides *how* it reaches Kenshi.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from ... import native_commands
from ...config import CaptureConfig, ControlsConfig, RuntimeConfig
from ...control.base import InputController, PrimitiveInputAction
from ...control.calibration import (
    evaluate_calibration_identity,
    validate_expected_client_size,
)
from ...control.capture import CapturedFrame
from ...core.authority import InputBoundaryDecision
from ...core.evidence import SemanticActionReceipt
from ...core.interaction import AuthoredRecipientBasis, RecipientScope
from ...core.observation import Observation
from ...core.operation import (
    GAME_SPEED_MULTIPLIER_BY_GEAR,
    Action,
    ControlMode,
    KeyAction,
    OpenTradeWindowAction,
    PauseAction,
    PointerActionClass,
    SelectDialogueOptionAction,
    SetSpeedAction,
    TransferItemAction,
)
from ...core.telemetry import (
    ContextActionKind,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NativeWireCommand,
    NearbyEntity,
    TelemetrySnapshot,
    WorldTarget,
    inventory_owner_distance_from_primary,
    inventory_owner_is_within_trade_authoring_distance,
)
from ...core.transport import (
    ActionReceipt,
    CalibrationReport,
    CommandDispatchContext,
    NativeCommandRequest,
    Transition,
    new_command_id,
)
from ...core.world import WorldStateRevision
from ...input_boundary import ExecutionToken
from ...telemetry import TelemetryReader, TelemetryReadError

NATIVE_COMMAND_REQUEST_FILE = "native_command.request.json"
NATIVE_COMMAND_ACK_TIMEOUT_SECONDS = 2.0
NATIVE_COMMAND_POLL_SECONDS = 0.025
NATIVE_DIALOGUE_SETTLE_SECONDS = 1.0

class LiveCapturePort(Protocol):
    def capture(self, sequence: int) -> CapturedFrame: ...


class LiveExternalPort(Protocol):
    """External host state available to live operation mechanics."""

    controller: InputController
    telemetry_reader: TelemetryReader
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


def _observes_inventory_owner(telemetry: TelemetrySnapshot, target_id: str) -> bool:
    """Whether anything currently observed could be the owner of an inventory.

    An inventory owner is not necessarily a nearby character. A squad member is
    in `squad`, a container in `world_targets`, a body in `nearby_entities`, and
    Kenshi opens all three by handle without asking which. Proving the owner is
    still observed somewhere is the honest check; proving it is a nearby
    character would refuse the agent its own squad.
    """

    return (
        any(member.id == target_id for member in telemetry.roster)
        or any(entity.id == target_id for entity in telemetry.nearby_entities)
        or any(world.id == target_id for world in telemetry.world_targets)
        or any(found.id == target_id for found in telemetry.discovered_objects)
    )


def _transfer_precondition_error(
    action: Action,
    telemetry: TelemetrySnapshot,
) -> str | None:
    """Why a transfer cannot be issued now, or None.

    Both ends must be proved open rather than merely observed, because a slot is
    only meaningful inside the inventory that reported it. The fields the
    request carries come from the operation's projection; this only decides
    whether the world still supports sending it.
    """

    if not isinstance(action, TransferItemAction):
        return "A native transfer request requires a transfer_item action."
    held = {inventory.owner_id for inventory in telemetry.ui.open_inventories}
    if action.source_owner_id not in held:
        return "Native transfer source inventory is not open."
    if action.destination_owner_id not in held:
        return "Native transfer destination inventory is not open."
    return None


# Commands whose whole request is "this command, at that target". They differed
# only in which list they proved the target still sat in, and each carried its
# own copy of the same build call - so a fourth could arrive with a validation
# and no request, or a request and no validation. Keeping the proofs together
# and the build once makes that pairing structural.
_TARGET_ONLY_WIRE_COMMANDS: frozenset[str] = frozenset(
    {
        native_commands.NATIVE_SQUAD_SELECTION_WIRE_COMMAND,
        native_commands.NATIVE_SQUAD_REGROUP_WIRE_COMMAND,
    }
)


def _target_only_command_error(
    wire_command: NativeWireCommand,
    telemetry: TelemetrySnapshot,
    target_id: str,
    selected_ids: Sequence[str],
) -> str | None:
    """Why a target-only native command cannot be issued now, or None."""

    if wire_command == native_commands.NATIVE_SQUAD_SELECTION_WIRE_COMMAND:
        matches = [member for member in telemetry.roster if member.id == target_id]
        if len(matches) != 1:
            return "Native squad-selection target is absent or ambiguous at issue time."
        return None
    if wire_command == native_commands.NATIVE_SQUAD_REGROUP_WIRE_COMMAND:
        matches = [
            member
            for member in telemetry.roster
            if member.id == target_id and member.id not in selected_ids
        ]
        if len(matches) != 1 or matches[0].alive is not True:
            return (
                "Native squad-regroup target is absent, not distinct from the "
                "actor, ambiguous, or not confirmed alive at issue time."
            )
        return None
    raise RuntimeError(
        f"{wire_command!r} is listed as target-only but has no validation rule."
    )


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

    def calibration_report(self, pointer_class: PointerActionClass) -> CalibrationReport:
        return evaluate_calibration_identity(
            action_class=pointer_class,
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
                primitive_actions=command.primitive_action_bound,
                message="Live action withheld by the dry-run safety gate.",
            )
        else:
            if token is None:
                raise RuntimeError(
                    "Live input delivery requires an input-boundary execution token."
                )
            if self.controller.emergency_stop_pressed(self._port.emergency_stop_key):
                raise RuntimeError(
                    f"Emergency stop key {self._port.emergency_stop_key!r} is pressed; "
                    "action aborted."
                )
            async with self.controller.input_lease(alt_tab_on_restore=True):
                lease_wait = self.controller.input_lease_wait_seconds()
                boundary = (
                    token.revalidate(lease_wait_seconds=lease_wait)
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
                else:
                    action_receipt = await receipt(action, started, command)
                action_receipt = action_receipt.model_copy(
                    update={"input_boundary": boundary}
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

    async def run_control_pause(
        self,
        action: PauseAction,
        *,
        command: CommandDispatchContext,
        receipt: Callable[
            [Action, datetime, CommandDispatchContext | None],
            Awaitable[ActionReceipt],
        ],
    ) -> Transition:
        """Deliver the supervisor/ownership pause path from fresh host state.

        This authority is intentionally independent of a plan execution token:
        human input and F12 are exactly the conditions under which supervision
        must still be able to establish a safe pause. The caller owns the
        decision; this adapter owns the narrow fresh-state and safety-lease
        delivery boundary.
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
                primitive_actions=0,
                message="Live control pause withheld by the dry-run safety gate.",
            )
            return await self._finish_transition(action_receipt, command)

        async with self.controller.safety_input_lease():
            observation = await self._port.observe_without_capture()
            telemetry = observation.telemetry
            if observation.telemetry_stale or telemetry is None:
                raise RuntimeError(
                    "Control pause requires fresh canonical telemetry inside its safety lease."
                )
            age = observation.telemetry_age_seconds
            if age is None or age > self.telemetry_reader.max_age_seconds:
                raise RuntimeError(
                    "Control pause telemetry age is unknown or exceeds its freshness ceiling."
                )
            if not telemetry.game.loaded or "game.pause" not in telemetry.capabilities:
                raise RuntimeError(
                    "Control pause requires a loaded game with the pause capability."
                )
            if telemetry.game.paused is None:
                raise RuntimeError("Control pause requires a known current pause state.")
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
            latest = observation.telemetry.controller_commands.command_for(
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

    async def run_primitives(
        self,
        primitives: tuple[PrimitiveInputAction, ...],
    ) -> tuple[int, list[str]]:
        primitive_count = 0
        messages: list[str] = []
        for primitive in primitives:
            if self.controller.user_input_detected():
                raise RuntimeError(
                    "User input resumed during operation delivery; yielding control."
                )
            if self.controller.emergency_stop_pressed(self._port.emergency_stop_key):
                raise RuntimeError("Emergency stop pressed during operation delivery.")
            primitive_receipt = await self.controller.execute(primitive)
            primitive_count += primitive_receipt.primitive_actions
            messages.append(primitive_receipt.message)
        return primitive_count, messages

    def pause_primitives(self, paused: bool) -> tuple[list[PrimitiveInputAction], str]:
        del paused
        return [KeyAction(key=self.controls_config.pause_key)], (
            f"pause key {self.controls_config.pause_key!r}"
        )

    def native_time_control_available(self) -> bool:
        """Whether the plug-in can be asked to control the clock right now.

        A native command needs an identity session, which only the plug-in
        supplies. Healthy loaded sessions always use that authoritative path,
        including safety cleanup. The keyboard is reserved for degraded or
        emergency cleanup when there is no fresh native identity to command.
        """

        try:
            result = self.telemetry_reader.read()
        except TelemetryReadError:
            return False
        return not result.stale and bool(result.snapshot.identity_session_id)

    async def _dispatch_time_control(
        self,
        wire_command: NativeWireCommand,
        **wire_fields: object,
    ) -> NativeCommandAcknowledgement:
        """One time-control command, straight to the engine.

        No keystroke and no trigger hotkey. The plug-in dispatches when the
        request file changes, so nothing is sent to the desktop at all.
        """

        result = self.telemetry_reader.read()
        if result.stale:
            raise RuntimeError("Refusing to control playback from stale telemetry.")
        snapshot = result.snapshot
        identity_session_id = snapshot.identity_session_id
        if not identity_session_id:
            raise RuntimeError("Native time control requires an identity session.")
        request = NativeCommandRequest(
            schema_version="1.6",
            command_id=new_command_id(),
            command=wire_command,
            control_mode=ControlMode.NATIVE_ASSISTED,
            identity_session_id=identity_session_id,
            based_on_revision=WorldStateRevision(
                telemetry_sequence=snapshot.sequence,
                capability_epoch=0,
            ),
            selected_character_ids=list(snapshot.selected_character_ids),
            **wire_fields,  # type: ignore[arg-type]
        )
        request_path = self.telemetry_reader.path.parent / NATIVE_COMMAND_REQUEST_FILE
        native_commands.write_native_command_request_atomic(request_path, request)
        return await self._wait_for_native_acknowledgement(request)

    async def apply_pause_request(
        self,
        paused: bool,
        *,
        safety: bool = False,
    ) -> tuple[int, str]:
        """Pause or resume through the native clock whenever it is healthy.

        Kenshi owns the clock through `GameWorld::userPause`, so an ordinary
        pause and a safety pause share one idempotent native command. The
        keyboard remains only as the explicit degraded boundary when fresh
        native authority is absent; a healthy loaded plug-in is never bypassed
        merely because the caller is the supervisor.
        """

        if not self.native_time_control_available():
            primitives, description = self.pause_primitives(paused)
            primitive_count = 0
            for primitive in primitives:
                execute = (
                    self.controller.execute_safety if safety else self.controller.execute
                )
                receipt = await execute(primitive)
                primitive_count += receipt.primitive_actions
            return primitive_count, description

        acknowledgement = await self._dispatch_time_control(
            "pause",
            paused=paused,
        )
        expected_reason = "world_paused" if paused else "world_running"
        if (
            acknowledgement.status is not NativeCommandStatus.COMPLETED
            or acknowledgement.reason != expected_reason
        ):
            raise RuntimeError(
                "Native pause did not return its exact terminal result: "
                f"{acknowledgement.status.value}/{acknowledgement.reason}."
            )
        return 0, (
            f"native pause(paused={paused}) -> {acknowledgement.reason}"
        )

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

        if not self.native_time_control_available():
            primitive_count = 0
            if paused:
                primitive_count += await self._establish_degraded_playback_gear(1)
            if action.speed != 1:
                primitive_count += await self._establish_degraded_playback_gear(action.speed)
            elif not paused:
                primitive_count += await self._establish_degraded_playback_gear(1)
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

        # One command, not a keystroke composite.
        #
        # Kenshi's speed keys select a rate without resuming, so a paused world
        # needed gear 1 pressed first and then the requested gear -- an ordering
        # that lived here as a rule *about* Kenshi rather than as something
        # Kenshi said. `GameWorld::setGameSpeed` takes the multiplier and
        # `userPause` takes the state, and the plug-in does both, so the
        # composite is gone along with the last gameplay keystroke.
        acknowledgement = await self._dispatch_time_control(
            "set_speed",
            speed_multiplier=expected,
        )
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=started,
            finished_at=datetime.now(UTC),
            primitive_actions=0,
            message=(
                f"Kenshi set speed gear {action.speed} ({expected:g}x) natively: "
                f"{acknowledgement.reason}."
            ),
        )

    async def _establish_playback_gear(self, gear: int) -> int:
        """Set one playback gear through Kenshi while gameplay work remains active."""

        expected = GAME_SPEED_MULTIPLIER_BY_GEAR[gear]
        acknowledgement = await self._dispatch_time_control(
            "set_speed",
            speed_multiplier=expected,
        )
        if (
            acknowledgement.status is not NativeCommandStatus.COMPLETED
            or acknowledgement.reason != "world_speed_set"
        ):
            raise RuntimeError(
                "Native playback gear selection did not return its exact terminal "
                f"result: {acknowledgement.status.value}/{acknowledgement.reason}."
            )
        return 0

    async def _establish_degraded_playback_gear(self, gear: int) -> int:
        """Explicit no-plug-in fallback; native-assisted play never reaches it."""

        expected = GAME_SPEED_MULTIPLIER_BY_GEAR[gear]
        primitive_count = 0
        for _attempt in range(2):
            if self.controller.emergency_stop_pressed(self._port.emergency_stop_key):
                raise RuntimeError(
                    "Emergency stop interrupted degraded playback control."
                )
            if self.controller.user_input_detected():
                raise RuntimeError("Human input interrupted degraded playback control.")
            receipt = await self.controller.execute(
                KeyAction(key=self.controls_config.speed_keys[gear])
            )
            if not receipt.executed:
                raise RuntimeError(receipt.message or "Playback key was not executed.")
            primitive_count += receipt.primitive_actions
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    result = self.telemetry_reader.read()
                except TelemetryReadError:
                    result = None
                if (
                    result is not None
                    and not result.stale
                    and result.snapshot.game.paused is False
                    and result.snapshot.game.speed_multiplier == expected
                ):
                    return primitive_count
                await asyncio.sleep(0.05)
        raise RuntimeError(
            f"Kenshi did not confirm degraded speed gear {gear} ({expected:g}x)."
        )

    async def _establish_native_running_state(
        self,
        acknowledgement: NativeCommandAcknowledgement,
        *,
        timeout_seconds: float = 3.0,
    ) -> tuple[int, NativeCommandAcknowledgement | None]:
        """Start at 1x natively unless this exact gameplay command finishes first."""

        expected = GAME_SPEED_MULTIPLIER_BY_GEAR[1]
        playback = await self._dispatch_time_control(
            "set_speed",
            speed_multiplier=expected,
        )
        if (
            playback.status is not NativeCommandStatus.COMPLETED
            or playback.reason != "world_speed_set"
        ):
            raise RuntimeError(
                "Native playback resume did not return its exact terminal "
                f"result: {playback.status.value}/{playback.reason}."
            )
        current = self._fresh_matching_native_acknowledgement(acknowledgement)
        if current is not None and current.status in {
            NativeCommandStatus.CANCELLED,
            NativeCommandStatus.COMPLETED,
        }:
            return 0, current
        return 0, None

    async def run_movement_pulse(
        self,
        action: Action,
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
                f"Movement pulse {action.kind!r} requires confirmed paused live state."
            )

        primitive_count: int
        messages: list[str]
        if prepared_primitives is None:
            primitive_count, messages = 0, []
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
            message=(f"Executed {action.kind!r}. {outcome} " + " ".join(messages)),
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

    async def run_native_order(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext,
        *,
        target_id: str,
        pulse_seconds: float,
        require_vendor_role: bool,
        # The operation's own projection of itself onto wire fields. Supplied by
        # the handler because it is operation semantics, which this surface
        # deliberately holds none of - it only delivers.
        wire_fields: dict[str, object],
        wire_command: NativeWireCommand = native_commands.NATIVE_APPROACH_WIRE_COMMAND,
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
        task_started_reasons: frozenset[str] = frozenset(),
        paused_dialogue_terminal: bool = False,
        await_terminal_without_playback: bool = False,
        deferred_terminal_timeout_seconds: float = NATIVE_COMMAND_ACK_TIMEOUT_SECONDS,
    ) -> ActionReceipt:
        adopted = (
            self._active_native_order_for(
                wire_command=wire_command,
                target_id=target_id,
                bearing_degrees=bearing_degrees,
                distance_units=distance_units,
                minimum_output_quantity=minimum_output_quantity,
                context_action=context_action,
                authored_basis=command.authored_basis(),
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
                action=action,
                require_vendor_role=require_vendor_role,
                wire_fields=wire_fields,
                wire_command=wire_command,
                require_dialogue_target=require_dialogue_target,
                bearing_degrees=bearing_degrees,
                distance_units=distance_units,
                minimum_output_quantity=minimum_output_quantity,
                expected_actor_id=expected_actor_id,
                context_action=context_action,
            )
            request_path = self.telemetry_reader.path.parent / NATIVE_COMMAND_REQUEST_FILE
            native_commands.write_native_command_request_atomic(request_path, request)
            primitive_count = 0
            messages = [
                "Published the atomic native request; the game-thread file watcher "
                "dispatches it without desktop input."
            ]
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
        } and not (
            acknowledgement.status is NativeCommandStatus.COMPLETED
            and acknowledgement.reason in task_started_reasons
        ):
            return self._accepted_native_terminal_receipt(
                action=action,
                command=command,
                started=started,
                primitive_count=primitive_count,
                messages=messages,
                acknowledgement=acknowledgement,
                semantic=semantic,
            )
        if await_terminal_without_playback:
            acknowledgement = await self._wait_for_native_terminal_acknowledgement(
                acknowledgement,
                timeout_seconds=deferred_terminal_timeout_seconds,
            )
            messages.append(
                "Native deferred terminal "
                f"{acknowledgement.status.value!r}: {acknowledgement.reason}."
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
        if paused_dialogue_terminal and self._fresh_pause_state() is True:
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
            action,
            started,
            pulse_seconds=pulse_seconds,
            prepared_primitives=(primitive_count, messages),
        )
        if not continue_until_terminal:
            # This operation owns exactly one bounded pulse; the planner is
            # responsible for asking to continue.
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
                messages.append(
                    "Established controller-owned 5x playback speed for the "
                    "monitored operation."
                )
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
                "Issued the monitored native order; gameplay advances while the world runs. "
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
            current = latest.telemetry.controller_commands.command_for(
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
                action,
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
        wire_command: NativeWireCommand,
        target_id: str,
        bearing_degrees: float,
        distance_units: float,
        minimum_output_quantity: int,
        context_action: ContextActionKind | None,
        authored_basis: AuthoredRecipientBasis | None = None,
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
        native = observation.telemetry.controller_commands
        candidates = [
            command
            for command in native.active_commands()
            if command.command == wire_command
            and command.minimum_output_quantity == minimum_output_quantity
            and command.context_action == (context_action or "")
        ]
        if wire_command == native_commands.NATIVE_DIRECTION_WIRE_COMMAND:
            candidates = [
                command
                for command in candidates
                if not command.target_id
                and command.bearing_degrees == bearing_degrees
                and command.distance_units == distance_units
            ]
        elif wire_command == native_commands.NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            candidates = [
                command
                for command in candidates
                if not command.target_id
                and command.bearing_degrees == 0.0
                and command.distance_units == 0.0
            ]
        else:
            candidates = [
                command
                for command in candidates
                if command.target_id == target_id
                and command.bearing_degrees == 0.0
                and command.distance_units == 0.0
            ]
        selected_ids = observation.telemetry.selected_character_ids
        candidates = [
            command
            for command in candidates
            if len(command.selected_character_ids) == len(selected_ids)
            and set(command.selected_character_ids) == set(selected_ids)
        ]
        if len(candidates) != 1:
            return None
        acknowledgement = candidates[0]
        if wire_command == native_commands.NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            selected = observation.telemetry.selected_characters()
            # Being indoors is mechanics; how many characters may be ordered
            # out is the contract's. Requiring one contradicted the
            # declared CURRENT_SELECTION scope, and Kenshi broadcasts a
            # move order to the whole selection.
            if not selected or any(member.indoors is not True for member in selected):
                return None
        # Adoption compared the live order against the *current* selection only,
        # so it was a way around the recipient check rather than a case of it:
        # an order authored for A and B could be satisfied by continuing an
        # order Kenshi holds for A alone, issuing nothing and reporting success.
        # Declining adoption here lets the request path refuse with the precise
        # reason instead of quietly commanding the wrong characters.
        if authored_basis is not None:
            current = AuthoredRecipientBasis.capture(
                authored_basis.scope,
                primary=observation.telemetry.primary_character_id,
                selection=selected_ids,
                explicit_recipients=authored_basis.explicit_recipients,
            )
            if not authored_basis.matches(current):
                return None
        return acknowledgement

    def _context_action_for_target(
        self,
        wire_command: NativeWireCommand,
        target_id: str,
        world_targets: Sequence[WorldTarget],
        context_action: ContextActionKind | None,
    ) -> ContextActionKind | Literal[""] | None:
        """The context semantic a target must advertise, or None if not this route.

        Deliberately blind to the selection. Classifying which commands need a
        world target describes what the request looks like, not who receives it,
        and keeping the two apart is what stops a shape check from quietly
        becoming a recipient rule - which is how every previous private scope
        model started.
        """

        # `open_context_inventory` was in this set and is not any more. It names
        # an owner, not a world target advertising a context action, so routing
        # it here demanded that a squad member be a `natural_resource` offering
        # `operate` - and every attempt to open a person's inventory was refused
        # with "no longer advertises the exact 'operate' action" about someone
        # who had never advertised anything of the kind.
        if wire_command not in {
            native_commands.NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
            native_commands.NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
        }:
            return None
        if wire_command == native_commands.NATIVE_CONTEXT_ACTION_WIRE_COMMAND:
            if context_action is None:
                raise RuntimeError("Native context execution requires an exact semantic.")
            expected: ContextActionKind = context_action
            requested: ContextActionKind | Literal[""] = context_action
        else:
            expected = ContextActionKind.OPERATE
            requested = ""
        matches = [
            target
            for target in world_targets
            if target.id == target_id and expected in target.context_actions
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Native context target is absent or no longer advertises "
                f"the exact {expected.value!r} action."
            )
        return requested

    def _context_action_for_person(
        self,
        wire_command: NativeWireCommand,
        target: NearbyEntity,
        context_action: ContextActionKind | None,
    ) -> ContextActionKind | Literal[""]:
        """The order semantic a person must still advertise, or empty.

        Separate from `_context_action_for_target` because they read different
        evidence: that one resolves against `world_targets.context_actions`, and
        a person's orders live in `nearby_entities.advertised_tasks`. Routing an
        order through the world-target resolver returned None -- "not this
        route" -- and the semantic was dropped silently, so the request failed
        its own shape validation for naming no action. That surfaced as a plan
        abort three steps into a live run, nowhere near the resolver.

        Re-proved here because the offer, the binding, and the request are three
        separate moments and Kenshi can withdraw an order between any two.
        """

        if wire_command != native_commands.NATIVE_CHARACTER_ORDER_WIRE_COMMAND:
            return ""
        if context_action is None:
            raise RuntimeError("A native character order requires an exact order name.")
        if not target.advertised_tasks_probed:
            raise RuntimeError(
                "Native order target was not probed this observation, so what it "
                "affords is unknown rather than empty."
            )
        if str(context_action) not in target.orderable_task_names():
            raise RuntimeError(
                f"Native order target no longer advertises {str(context_action)!r}."
            )
        return context_action

    def _native_request(
        self,
        command: CommandDispatchContext,
        wire_command: NativeWireCommand,
        observation: Observation,
        selected_ids: list[str],
        **distinguishing: object,
    ) -> NativeCommandRequest:
        """One native request, carrying the fields every command shares.

        Each command's branch is then only what makes it that command. The
        shared six were restated at every construction site, which is how a new
        command's branch became eight lines of boilerplate and one line of
        meaning.
        """

        telemetry = observation.telemetry
        assert telemetry is not None and telemetry.identity_session_id
        return NativeCommandRequest(
            schema_version="1.6",
            command_id=command.command_id,
            command=wire_command,
            control_mode=ControlMode.NATIVE_ASSISTED,
            identity_session_id=telemetry.identity_session_id,
            based_on_revision=observation.world_revision,
            selected_character_ids=list(selected_ids),
            **distinguishing,  # type: ignore[arg-type]
        )

    def _native_approach_request(
        self,
        target_id: str,
        command: CommandDispatchContext,
        *,
        action: Action,
        require_vendor_role: bool,
        wire_fields: dict[str, object],
        wire_command: NativeWireCommand = native_commands.NATIVE_APPROACH_WIRE_COMMAND,
        require_dialogue_target: bool = True,
        bearing_degrees: float = 0.0,
        distance_units: float = 0.0,
        minimum_output_quantity: int = 1,
        expected_actor_id: str | None = None,
        context_action: ContextActionKind | None = None,
    ) -> NativeCommandRequest:
        """Build the native pathing request for one exact stable target.

        `require_vendor_role` separates a vendor-only request from the generic
        dialogue-target action. The generic action asks only for the
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
        if not telemetry.identity_session_id:
            raise RuntimeError("Native command requires a current identity session.")
        selected_ids = telemetry.selected_character_ids
        # Selection cardinality is the contract's to decide, and the contract
        # is resolved in the operation layer - this surface is external
        # delivery and deliberately knows nothing about definitions. What it
        # receives is the already-decided recipient basis, and what it does is
        # prove the world still matches it at the moment the bytes are formed.
        #
        # This replaced the fifth private copy of a singleton rule, keyed here
        # on a hardcoded wire command name set that treated
        # `perform_context_action` as singleton-only while its contract
        # declares CURRENT_SELECTION - so a two-character party could not mine
        # even after option preparation was fixed.
        authored_basis = command.authored_basis()
        if authored_basis is None:
            # No recorded basis means nothing was proven about recipients, and
            # a command that cannot say who it is for must not be delivered to
            # whoever happens to be selected.
            raise RuntimeError(
                "Native command carries no authored recipient basis, so the "
                "characters it would command cannot be proven."
            )
        current_basis = AuthoredRecipientBasis.capture(
            authored_basis.scope,
            primary=telemetry.primary_character_id,
            selection=selected_ids,
            explicit_recipients=authored_basis.explicit_recipients,
        )
        drift = authored_basis.differences_from(current_basis)
        if drift:
            raise RuntimeError(
                "Native command would be delivered to different recipients than "
                f"it was authored for: {'; '.join(drift)}."
            )
        if authored_basis.scope not in {
            RecipientScope.NONE,
            RecipientScope.NAMED_BODY,
        } and (
            not selected_ids or telemetry.primary_character_id not in selected_ids
        ):
            # Every order that broadcasts to the selection needs one, and needs
            # Kenshi's own primary inside it. An order that names its own
            # recipient does not, and the case it serves is the one where there
            # is no selection to have: every character dead, the squad empty.
            # Requiring a primary there would make the recovery unreachable at
            # exactly the moment it is the only thing left to do.
            raise RuntimeError(
                "Native command requires a current selection containing Kenshi's "
                "exported primary."
            )
        if expected_actor_id is not None and selected_ids != [expected_actor_id]:
            raise RuntimeError(
                "Native squad regrouping requires actor_id to remain the exact "
                "current selection at issue time."
            )
        # Preconditions first, then one build for every command.
        #
        # Each branch used to end in its own `_native_request(...)` call with
        # the wire fields listed by hand, which made this the second copy of a
        # mapping the acknowledgement matcher also kept. They disagreed exactly
        # once and it cost a live run: `perform_character_order` lost its order
        # name here while the matcher had no rule for it there -- two symptoms
        # of one missing entry, in two places that had to be edited together
        # and were not.
        #
        # The fields now come from the operation's own projection, so a request
        # and the acknowledgement it will be matched against cannot describe
        # different things. What is left in the branches is what they were
        # always really for: proving the world still supports this command.
        failure = self._native_request_precondition_error(
            wire_command,
            telemetry,
            action,
            target_id=target_id,
            selected_ids=selected_ids,
            context_action=context_action,
            require_dialogue_target=require_dialogue_target,
            require_vendor_role=require_vendor_role,
        )
        if failure is not None:
            raise RuntimeError(failure)
        return self._native_request(
            command,
            wire_command,
            observation,
            selected_ids,
            **wire_fields,
        )

    def _native_request_precondition_error(
        self,
        wire_command: NativeWireCommand,
        telemetry: TelemetrySnapshot,
        action: Action,
        *,
        target_id: str,
        selected_ids: list[str],
        context_action: ContextActionKind | None,
        require_dialogue_target: bool,
        require_vendor_role: bool,
    ) -> str | None:
        """Why the world cannot currently support this command, or None."""

        if wire_command == native_commands.NATIVE_CLOSE_INTERFACE_WIRE_COMMAND:
            # Game-wide UI state; there is no world target to re-resolve.
            return None
        if wire_command == native_commands.NATIVE_DIALOGUE_OPTION_WIRE_COMMAND:
            if not isinstance(action, SelectDialogueOptionAction):
                return "Native dialogue selection requires a typed dialogue option."
            ui = telemetry.ui
            if (
                ui.dialogue_open is not True
                or ui.dialogue_target_id != action.dialogue_target_id
                or ui.dialogue_options is None
            ):
                return "The exact offered dialogue is no longer open at issue time."
            if action.option_index >= len(ui.dialogue_options):
                return "The exact offered dialogue option index is no longer present."
            if ui.dialogue_options[action.option_index] != action.option_text:
                return "The exact offered dialogue caption changed before issue time."
            return None
        if wire_command in {
            native_commands.NATIVE_DIRECTION_WIRE_COMMAND,
            native_commands.NATIVE_RESOURCE_SURVEY_WIRE_COMMAND,
        }:
            # Reference no world target: a direction derives its destination
            # from where the character stands, and a survey reads the field
            # under the primary.
            return None
        if wire_command == native_commands.NATIVE_EXIT_BUILDING_WIRE_COMMAND:
            selected = telemetry.selected_characters()
            # Being indoors is mechanics; how many characters may be ordered out
            # is the contract's. Requiring one contradicted the declared
            # CURRENT_SELECTION scope, and Kenshi broadcasts a move order to the
            # whole selection.
            if not selected or any(member.indoors is not True for member in selected):
                return (
                    "Native building exit requires the selected character to be "
                    "confirmed indoors at issue time."
                )
            return None
        if wire_command == native_commands.NATIVE_MAP_TRAVEL_WIRE_COMMAND:
            destinations = [
                destination
                for destination in telemetry.known_map_destinations
                if destination.id == target_id
            ]
            if len(destinations) != 1:
                return (
                    "Native map destination is absent, undiscovered, or ambiguous "
                    "at issue time."
                )
            return None
        if wire_command == native_commands.NATIVE_TRANSFER_WIRE_COMMAND:
            return _transfer_precondition_error(action, telemetry)
        if wire_command == native_commands.NATIVE_TRADE_WINDOW_WIRE_COMMAND:
            # Both parties, wherever they are observed. Without this the command
            # fell through to the nearby-character lookup and was refused for a
            # squad member being absent from `nearby_entities` - which is true
            # and irrelevant, since a squadmate is exactly who you pair with.
            # Fifth operation to reach the wire through a fall-through that did
            # not fit it.
            if not isinstance(action, OpenTradeWindowAction):
                return "A native trade-window request requires an open_trade_window action."
            parties = (action.first_owner_id, action.second_owner_id)
            for owner in parties:
                if not _observes_inventory_owner(telemetry, owner):
                    return (
                        f"Native trade-window party {owner!r} is absent from "
                        "current telemetry."
                    )
            if action.first_owner_id != telemetry.primary_character_id:
                return (
                    "Native trade-window first owner is not the exact current "
                    "primary character."
                )
            if action.second_owner_id == action.first_owner_id:
                return "Native trade-window owners are not distinct."
            if not inventory_owner_is_within_trade_authoring_distance(
                telemetry,
                action.second_owner_id,
            ):
                distance = inventory_owner_distance_from_primary(
                    telemetry,
                    action.second_owner_id,
                )
                suffix = "unknown" if distance is None else f"{distance:.1f} units"
                return (
                    "Native trade-window second owner is outside the current local "
                    f"interaction fence ({suffix})."
                )
            return None
        if wire_command in _TARGET_ONLY_WIRE_COMMANDS:
            return _target_only_command_error(
                wire_command, telemetry, target_id, selected_ids
            )
        if not target_id:
            return "Native approach requires an exact target_id."
        if self._world_target_advertises(wire_command, target_id, telemetry, context_action):
            return None
        target = next(
            (entity for entity in telemetry.nearby_entities if entity.id == target_id),
            None,
        )
        if target is None:
            return "Native command target is absent from current nearby telemetry."
        if require_dialogue_target and (
            not target.is_dialogue_target() or target.conscious is not True
        ):
            return (
                "Native command target lacks exact current conscious non-hostile "
                "dialogue evidence."
            )
        if require_vendor_role and not target.is_confirmed_vendor():
            return "Native command target lacks exact safe current vendor evidence."
        self._context_action_for_person(wire_command, target, context_action)
        return None

    def _world_target_advertises(
        self,
        wire_command: NativeWireCommand,
        target_id: str,
        telemetry: TelemetrySnapshot,
        context_action: ContextActionKind | None,
    ) -> bool:
        """Whether this command resolves against an advertising world target."""

        return (
            self._context_action_for_target(
                wire_command,
                target_id,
                telemetry.world_targets,
                context_action,
            )
            is not None
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
                acknowledgement = snapshot.controller_commands.command_for(request.command_id)
                if acknowledgement is not None and snapshot.sequence > basis:
                    if (
                        acknowledgement.based_on_telemetry_sequence != basis
                        or acknowledgement.target_id != request.target_id
                        or acknowledgement.bearing_degrees != request.bearing_degrees
                        or acknowledgement.distance_units != request.distance_units
                        or acknowledgement.minimum_output_quantity
                        != request.minimum_output_quantity
                        or acknowledgement.dialogue_option_index
                        != request.dialogue_option_index
                        or acknowledgement.dialogue_option_text
                        != request.dialogue_option_text
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

    async def _wait_for_native_terminal_acknowledgement(
        self,
        previous: NativeCommandAcknowledgement,
        *,
        timeout_seconds: float,
    ) -> NativeCommandAcknowledgement:
        """Wait for one accepted UI transaction without advancing playback."""

        deadline = time.monotonic() + timeout_seconds
        while True:
            current = self._fresh_matching_native_acknowledgement(previous)
            if current is not None and current.status in {
                NativeCommandStatus.REJECTED,
                NativeCommandStatus.CANCELLED,
                NativeCommandStatus.COMPLETED,
            }:
                return current
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Kenshi did not publish the deferred native UI terminal after "
                    f"{timeout_seconds:.0f}s."
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
        current = snapshot.controller_commands.command_for(previous.command_id)
        if current is None:
            return None
        identity = (
            current.command,
            current.target_id,
            current.bearing_degrees,
            current.distance_units,
            current.minimum_output_quantity,
            current.dialogue_option_index,
            current.dialogue_option_text,
            current.selected_character_ids,
            current.based_on_telemetry_sequence,
        )
        previous_identity = (
            previous.command,
            previous.target_id,
            previous.bearing_degrees,
            previous.distance_units,
            previous.minimum_output_quantity,
            previous.dialogue_option_index,
            previous.dialogue_option_text,
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
                current = snapshot.controller_commands.command_for(command_id)
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
