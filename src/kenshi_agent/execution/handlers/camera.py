"""Camera rotation and controller-verified recovery handlers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from ... import operation_definitions as operations
from ...affordances import OPERATION_BINDING_AUTHORITY
from ...camera_recovery import score_camera_observation
from ...control.base import PrimitiveInputAction
from ...core.evidence import (
    CameraFrameScore,
    CameraRecoveryEvidence,
    CameraRecoveryStatus,
    SemanticActionReceipt,
)
from ...core.observation import Observation
from ...core.operation import (
    Action,
    ClickAction,
    KeyAction,
    RecoverCameraViewAction,
    RotateCameraAction,
    camera_rotation_primitive,
)
from ...core.telemetry import NormalizedPointerBounds
from ...core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    Transition,
)
from ...core.world import WorldStateRevision
from ...input_boundary import ExecutionToken
from ...operation_definitions import BoundOperation
from ..types import (
    ActiveOperation,
    OperationContext,
    OperationHandler,
    OperationResult,
    OperationStatus,
)
from .input_binding import authorized_input_binding
from .kenshi_surface import KenshiControlSurface


class CameraMechanicsPort(Protocol):
    async def rotate_camera(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...

    async def recover_camera_view(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition: ...


@dataclass(frozen=True, slots=True)
class CameraRotationHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        transition = await _execute(self.operation, bound.operation, context)
        accepted = transition.receipt.accepted or transition.receipt.executed
        return _result(
            transition,
            status=(OperationStatus.SUCCEEDED if accepted else OperationStatus.REJECTED),
            reason=transition.receipt.message,
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


@dataclass(frozen=True, slots=True)
class CameraRecoveryHandler:
    operation: Callable[..., Awaitable[Transition]]

    async def execute(
        self,
        bound: BoundOperation,
        context: OperationContext,
    ) -> OperationResult:
        action = cast(RecoverCameraViewAction, bound.operation)
        transition = await _execute(self.operation, action, context)
        evidence = (
            transition.receipt.semantic.camera_recovery
            if transition.receipt.semantic is not None
            else None
        )
        succeeded = evidence is not None and evidence.status in {
            CameraRecoveryStatus.ALREADY_CLEAR,
            CameraRecoveryStatus.RECOVERED,
        }
        if evidence is not None:
            context.progress(
                "Accepted the controller-owned terminal camera-recovery verdict.",
                transition.observation,
                evidence={
                    "controller_verified": True,
                    "status": evidence.status.value,
                    "chosen_candidate": evidence.chosen_candidate,
                    "candidate_count": len(evidence.candidates),
                },
            )
        return _result(
            transition,
            status=(OperationStatus.SUCCEEDED if succeeded else OperationStatus.FAILED),
            reason=(
                f"Controller-owned camera recovery returned {evidence.status.value!r}."
                if evidence is not None
                else "Controller returned no typed camera-recovery evidence."
            ),
        )

    async def cancel(
        self,
        active: ActiveOperation,
        context: OperationContext,
    ) -> OperationResult:
        return _cancelled(active, context)


async def _execute(
    operation: Callable[..., Awaitable[Transition]],
    action: Action,
    context: OperationContext,
) -> Transition:
    if context.command is None:
        raise RuntimeError("Camera operation has no command authority.")
    return await operation(action, command=context.command, token=context.token)


def _result(
    transition: Transition,
    *,
    status: OperationStatus,
    reason: str,
) -> OperationResult:
    return OperationResult(
        status=status,
        observation=transition.observation,
        reason=reason,
        transition=transition,
        terminated=transition.terminated,
        success=transition.success,
    )


def _cancelled(
    active: ActiveOperation,
    context: OperationContext,
) -> OperationResult:
    return OperationResult(
        status=OperationStatus.CANCELLED,
        observation=context.world.latest or active.started_observation,
        reason="Camera operation was cancelled.",
    )


def camera_handlers(port: CameraMechanicsPort) -> dict[str, OperationHandler]:
    return {
        "camera.rotate_camera": CameraRotationHandler(port.rotate_camera),
        "camera.recover_camera_view": CameraRecoveryHandler(port.recover_camera_view),
    }


class KenshiCameraMechanics:
    """Camera rotation and view-recovery mechanics."""

    _surface: KenshiControlSurface

    def __init__(self, surface: KenshiControlSurface) -> None:
        self._surface = surface

    async def rotate_camera(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action,
            command=command,
            token=token,
            receipt=lambda current, started, dispatch: self._execute_rotate_operation(
                current, started, dispatch, token
            ),
        )

    async def recover_camera_view(
        self, action: Action, *, command: CommandDispatchContext, token: ExecutionToken | None
    ) -> Transition:
        return await self._surface.run_exact(
            action, command=command, token=token, receipt=self._execute_recovery_operation
        )

    async def _execute_rotate_operation(
        self,
        action: Action,
        started: datetime,
        command: CommandDispatchContext | None,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        del command
        return await self._execute_rotate_camera(cast(RotateCameraAction, action), started, token)

    async def _execute_recovery_operation(
        self, action: Action, started: datetime, command: CommandDispatchContext | None
    ) -> ActionReceipt:
        del command
        return await self._execute_recover_camera_view(
            cast(RecoverCameraViewAction, action), started
        )

    async def _execute_rotate_camera(
        self,
        action: RotateCameraAction,
        started: datetime,
        token: ExecutionToken | None,
    ) -> ActionReceipt:
        """Apply one bounded held-Mouse3 drag after in-lease world revalidation."""

        binding, observation = authorized_input_binding(
            action,
            token,
            operations.BoundNamedOperation,
        )
        primitive = camera_rotation_primitive(action)
        primitive_receipt = await self._surface.controller.execute(primitive)
        semantic = SemanticActionReceipt(
            action_kind=action.kind,
            contract_version=operations.ROTATE_CAMERA_DEFINITION.version,
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

    def _ensure_camera_recovery_can_continue(self) -> None:
        if self._surface.controller.emergency_stop_pressed(self._surface.port.emergency_stop_key):
            raise RuntimeError(
                "Emergency stop interrupted camera recovery; no further input was sent."
            )
        if self._surface.controller.user_input_detected():
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
        controller = self._surface.controller
        execute = controller.execute_safety if safety else controller.execute
        await execute(primitive)
        # One call accepts exactly one ControllerPrimitive. Count the semantic
        # primitive, not backend bookkeeping inside its receipt.
        return 1

    async def _capture_camera_candidate(
        self,
        action: RecoverCameraViewAction,
        *,
        candidate: str,
    ) -> tuple[CameraFrameScore, operations.BoundCameraRecovery, Observation]:
        """Retain and score one causally current frame inside the input lease."""

        if self._surface.port._capture is None:
            raise RuntimeError("Camera recovery requires an enabled capture backend.")
        settle = self._surface.controls_config.camera_recovery.candidate_settle_seconds
        if settle:
            await asyncio.sleep(settle)
        self._ensure_camera_recovery_can_continue()
        result = self._surface.telemetry_reader.read()
        if result.stale:
            raise RuntimeError(
                "Camera recovery stopped because telemetry became stale before capture."
            )
        snapshot = self._surface.port._apply_control_mode(result.snapshot)
        self._surface.port._capture_sequence += 1
        frame = self._surface.port._capture.capture(self._surface.port._capture_sequence)
        snapshot = snapshot.model_copy(
            update={
                "ui": snapshot.ui.model_copy(
                    update={"client_width": frame.width, "client_height": frame.height}
                )
            }
        )
        observation = Observation(
            run_id=self._surface.port.run_id,
            step_index=self._surface.port._step_index,
            mode="live",
            control_mode=self._surface.port.control_mode,
            world_revision=WorldStateRevision(
                telemetry_sequence=snapshot.sequence,
                frame_sequence=self._surface.port._capture_sequence,
                capability_epoch=self._surface.port._capability_epoch,
                observed_at_monotonic=time.monotonic(),
            ),
            telemetry=snapshot,
            telemetry_stale=False,
            telemetry_age_seconds=result.age_seconds,
            screenshot_path=frame.path,
            screenshot_sha256=frame.sha256,
            objective=self._surface.runtime_config.objective,
        )
        rebound = OPERATION_BINDING_AUTHORITY.bind(
            action,
            observation,
            affordance=None,
        )
        binding = operations.require_bound(
            rebound.binding,
            operations.BoundCameraRecovery,
            context="Camera recovery binding changed",
        )
        recovery = self._surface.controls_config.camera_recovery
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

        recovery = self._surface.controls_config.camera_recovery
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
            if primitive_count > operations.RECOVER_CAMERA_VIEW_DEFINITION.max_primitive_actions:
                raise RuntimeError("Camera recovery exceeded its authoritative primitive bound.")
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
                contract_version=operations.RECOVER_CAMERA_VIEW_DEFINITION.version,
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
            pause_primitives, _ = self._surface.pause_primitives(True)
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
            if not await self._surface.wait_for_pause_state(True):
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

        final_frame, refinement_primitives = await self._refine_camera_angle_and_tilt(
            action,
            binding,
            candidates,
        )
        primitive_count += refinement_primitives
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

    async def _refine_camera_angle_and_tilt(
        self,
        action: RecoverCameraViewAction,
        binding: operations.BoundCameraRecovery,
        candidates: list[CameraFrameScore],
    ) -> tuple[CameraFrameScore, int]:
        """Run the fixed zoom, symmetric orbit, and symmetric tilt phase."""

        recovery = self._surface.controls_config.camera_recovery
        primitive_count = 0
        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.zoom_out_key,
                hold_seconds=recovery.zoom_out_hold_seconds,
            )
        )
        zoomed, binding, _ = await self._capture_camera_candidate(action, candidate="zoom_out")
        candidates.append(zoomed)
        if zoomed.clear:
            return zoomed, primitive_count

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
            return final_angle, primitive_count

        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.tilt_up_key,
                hold_seconds=recovery.tilt_hold_seconds,
            )
        )
        tilt_up, binding, _ = await self._capture_camera_candidate(action, candidate="tilt_up")
        candidates.append(tilt_up)
        primitive_count += await self._camera_recovery_primitive(
            KeyAction(
                key=recovery.tilt_down_key,
                hold_seconds=recovery.tilt_hold_seconds * 2.0,
            )
        )
        tilt_down, binding, _ = await self._capture_camera_candidate(action, candidate="tilt_down")
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
        final_frame, _, _ = await self._capture_camera_candidate(
            action, candidate=f"final_{chosen_tilt.candidate}"
        )
        candidates.append(final_frame)
        return final_frame, primitive_count
