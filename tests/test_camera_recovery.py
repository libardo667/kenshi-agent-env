from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from kenshi_agent.camera_recovery import score_camera_observation
from kenshi_agent.config import (
    CameraRecoveryConfig,
    CaptureConfig,
    ControlsConfig,
    RuntimeConfig,
    SafetyConfig,
)
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.control.capture import CapturedFrame
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.models import (
    ActionReceipt,
    CameraRecoveryStatus,
    CameraState,
    CharacterState,
    ClickAction,
    Condition,
    ConditionKind,
    ConditionOperator,
    ControlMode,
    GameState,
    KeyAction,
    NormalizedPointerBounds,
    PlanStep,
    RecoverCameraViewAction,
    TelemetrySnapshot,
    UIState,
    Vec3,
    VisibleUIControl,
)
from kenshi_agent.operation_definitions import RECOVER_CAMERA_VIEW_DEFINITION
from kenshi_agent.safety import ActionGuard, SafetyViolation
from kenshi_agent.skills import MacroRegistry
from kenshi_agent.telemetry import TelemetryRead


def _bounds(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> NormalizedPointerBounds:
    return NormalizedPointerBounds(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
    )


class CameraTelemetry:
    def __init__(self, *, paused: bool = True) -> None:
        self.paused = paused
        self.floor = 0
        self.followed = False
        self.zoomed = False
        self.angle = 0
        self.tilt = 0
        self.sequence = 0
        self.path = Path("camera-telemetry.json")

    def read(self) -> TelemetryRead:
        self.sequence += 1
        controls = [
            VisibleUIControl(
                label="Hep",
                role="text",
                bounds=_bounds(0.30, 0.84, 0.38, 0.95),
            ),
            VisibleUIControl(
                label="[Hep]",
                role="text",
                bounds=_bounds(0.45, 0.34, 0.50, 0.38),
            ),
            VisibleUIControl(
                label=f"Floor {self.floor}",
                role="text",
                bounds=_bounds(0.70, 0.69, 0.74, 0.72),
            ),
            VisibleUIControl(
                label="hud_FloorArrowUp",
                role="button",
                bounds=_bounds(0.70, 0.66, 0.73, 0.69),
            ),
            VisibleUIControl(
                label="hud_FloorArrowDown",
                role="button",
                bounds=_bounds(0.70, 0.72, 0.73, 0.75),
            ),
        ]
        snapshot = TelemetrySnapshot(
            sequence=self.sequence,
            captured_at=datetime.now(UTC),
            capabilities=[
                "camera.position",
                "game.pause",
                "squad.basic",
                "ui.visible_controls",
            ],
            game=GameState(loaded=True, paused=self.paused),
            camera=CameraState(
                position=Vec3(x=0.0, y=20.0, z=0.0),
                center=Vec3(x=0.0, y=0.0, z=0.0),
            ),
            ui=UIState(
                active_screen="world",
                modal_open=False,
                dialogue_open=False,
                selected_character_id="char-hep",
                selected_character_ids=["char-hep"],
                visible_controls=controls,
            ),
            squad=[
                CharacterState(
                    id="char-hep",
                    name="Hep",
                    selected=True,
                    position=Vec3(x=0.0, y=0.0, z=0.0),
                )
            ],
        )
        return TelemetryRead(
            snapshot=snapshot,
            age_seconds=0.0,
            stale=False,
            path=self.path,
        )


class CameraController(InputController):
    def __init__(
        self,
        telemetry: CameraTelemetry,
        *,
        user_input_after: int | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.actions: list[PrimitiveInputAction] = []
        self.user_input_after = user_input_after
        self.user_input_checks = 0

    def focus_window(self) -> None:
        return None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        self.actions.append(action)
        if isinstance(action, KeyAction):
            if action.key == "space":
                self.telemetry.paused = not self.telemetry.paused
            elif action.key == "end":
                self.telemetry.zoomed = True
            elif action.key == "e":
                self.telemetry.angle += 1
            elif action.key == "q":
                self.telemetry.angle -= 2 if action.hold_seconds > 0.5 else 1
            elif action.key == "comma":
                self.telemetry.tilt += 1
            elif action.key == "period":
                self.telemetry.tilt -= 2 if action.hold_seconds > 0.5 else 1
        elif isinstance(action, ClickAction):
            if action.y > 0.80:
                self.telemetry.followed = True
            elif action.y > 0.70:
                self.telemetry.floor -= 1
            else:
                self.telemetry.floor += 1
        now = datetime.now(UTC)
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=now,
            finished_at=now,
            primitive_actions=1,
        )

    def emergency_stop_pressed(self, key: str) -> bool:
        del key
        return False

    def user_input_detected(self) -> bool:
        self.user_input_checks += 1
        return (
            self.user_input_after is not None
            and self.user_input_checks >= self.user_input_after
        )

    def client_rect(self) -> WindowRect:
        return WindowRect(left=0, top=0, right=640, bottom=360)


class CameraCapture:
    def __init__(self, path: Path, telemetry: CameraTelemetry, mode: str) -> None:
        self.path = path
        self.telemetry = telemetry
        self.mode = mode
        self.path.mkdir(parents=True, exist_ok=True)

    def capture(self, sequence: int) -> CapturedFrame:
        clear = self.mode == "clear"
        if self.mode == "follow":
            clear = self.telemetry.followed
        elif self.mode == "orbit":
            clear = (
                self.telemetry.followed
                and self.telemetry.zoomed
                and self.telemetry.angle == 1
            )
        elif self.mode == "failure":
            clear = False
        elif self.mode == "tilt":
            clear = self.telemetry.followed and self.telemetry.tilt == 1

        image = Image.new("RGB", (640, 360), (115, 103, 84))
        if clear:
            draw = ImageDraw.Draw(image)
            colors = [
                (25, 50, 80),
                (205, 175, 95),
                (50, 125, 75),
                (155, 55, 45),
                (110, 80, 155),
                (220, 220, 205),
            ]
            for y in range(0, 360, 8):
                for x in range(0, 640, 8):
                    draw.rectangle(
                        (x, y, x + 7, y + 7),
                        fill=colors[((x // 8) + 2 * (y // 8)) % len(colors)],
                    )
        elif self.mode == "usable_follow":
            draw = ImageDraw.Draw(image)
            colors = [
                (60, 70, 80),
                (100, 110, 120),
                (140, 150, 160),
                (80, 120, 80),
                (160, 100, 80),
                (80, 80, 150),
            ]
            for y in range(0, 360, 16):
                for x in range(0, 640, 16):
                    draw.rectangle(
                        (x, y, x + 15, y + 15),
                        fill=colors[((x // 16) + 2 * (y // 16)) % len(colors)],
                    )
        output = self.path / f"candidate_{sequence:06d}.png"
        image.save(output)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        return CapturedFrame(
            path=output,
            sha256=digest,
            width=image.width,
            height=image.height,
        )


def camera_environment(
    tmp_path: Path,
    *,
    mode: str,
    paused: bool = True,
    user_input_after: int | None = None,
) -> tuple[LiveEnvironment, CameraTelemetry, CameraController]:
    telemetry = CameraTelemetry(paused=paused)
    controller = CameraController(telemetry, user_input_after=user_input_after)
    environment = LiveEnvironment(
        run_id="camera-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        macros=MacroRegistry({}),
        runtime_config=RuntimeConfig(settle_seconds=0.0, objective="Test camera recovery."),
        controls_config=ControlsConfig(
            post_input_delay_seconds=0.0,
            camera_recovery=CameraRecoveryConfig(candidate_settle_seconds=0.0),
        ),
        capture_config=CaptureConfig(enabled=True),
        execute_actions=True,
        emergency_stop_key="f12",
        control_mode=ControlMode.INTERFACE_ONLY,
    )
    environment._capture = CameraCapture(tmp_path / "frames", telemetry, mode)  # type: ignore[assignment]
    return environment, telemetry, controller


def test_action_has_no_arguments_and_is_the_only_empty_postcondition_step() -> None:
    action = RecoverCameraViewAction.model_validate({"kind": "recover_camera_view"})
    assert action == RecoverCameraViewAction()
    PlanStep(
        step_id="recover",
        action=action,
        preconditions=[
            Condition(
                kind=ConditionKind.TELEMETRY_FRESH,
                operator=ConditionOperator.EQUALS,
                expected=True,
                max_age_seconds=3.0,
            )
        ],
        success_conditions=[],
        timeout_seconds=30,
    )
    wait = PlanStep(
        step_id="wait",
        action={"kind": "wait", "seconds": 0.0},  # type: ignore[arg-type]
        preconditions=[
            Condition(
                kind=ConditionKind.TELEMETRY_FRESH,
                operator=ConditionOperator.EQUALS,
                expected=True,
                max_age_seconds=3.0,
            )
        ],
        success_conditions=[],
        timeout_seconds=30,
    )
    assert wait.success_conditions == []


def test_contract_is_controller_verified_and_binds_current_hud(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment, _, _ = camera_environment(tmp_path, mode="clear")
        observation = await environment.reset()
        binding = RECOVER_CAMERA_VIEW_DEFINITION.bind(
            RecoverCameraViewAction(), observation
        )
        assert binding.bound
        assert binding.target_id == "char-hep"
        assert binding.floor == 0
        assert binding.resolved_bounds is not None
        assert RECOVER_CAMERA_VIEW_DEFINITION.controller_verified
        assert RECOVER_CAMERA_VIEW_DEFINITION.max_primitive_actions == 15

    asyncio.run(scenario())


def test_controller_transaction_limit_does_not_loosen_ordinary_primitive_limit(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, _ = camera_environment(tmp_path, mode="clear")
        observation = await environment.reset()
        action = RecoverCameraViewAction()
        guard = ActionGuard(
            SafetyConfig(
                allow_action_kinds=[action.kind],
                max_primitive_actions_per_step=4,
                max_controller_verified_primitive_actions_per_step=15,
                max_actions_per_minute=100,
            ),
            MacroRegistry({}),
            control_mode=ControlMode.INTERFACE_ONLY,
        )
        assert guard.validate(action, observation) == action

        too_tight = ActionGuard(
            SafetyConfig(
                allow_action_kinds=[action.kind],
                max_primitive_actions_per_step=4,
                max_controller_verified_primitive_actions_per_step=14,
                max_actions_per_minute=100,
            ),
            MacroRegistry({}),
            control_mode=ControlMode.INTERFACE_ONLY,
        )
        with pytest.raises(SafetyViolation, match="maximum is 14"):
            too_tight.validate(action, observation)

    asyncio.run(scenario())


def test_contract_fails_closed_on_missing_capture_ambiguous_portrait_or_modal(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, _ = camera_environment(tmp_path, mode="clear")
        observation = await environment.reset()
        assert observation.telemetry is not None

        without_capture = observation.model_copy(
            update={
                "telemetry": observation.telemetry.model_copy(
                    update={
                        "capabilities": [
                            capability
                            for capability in observation.telemetry.capabilities
                            if capability != "camera.recovery"
                        ]
                    }
                )
            },
            deep=True,
        )
        missing = RECOVER_CAMERA_VIEW_DEFINITION.bind(
            RecoverCameraViewAction(), without_capture
        )
        assert not missing.bound
        assert "capture/scoring" in missing.reason

        controls = list(observation.telemetry.ui.visible_controls or [])
        controls.append(
            VisibleUIControl(
                label="Hep",
                role="text",
                bounds=_bounds(0.40, 0.84, 0.48, 0.95),
            )
        )
        ambiguous = observation.model_copy(
            update={
                "telemetry": observation.telemetry.model_copy(
                    update={
                        "ui": observation.telemetry.ui.model_copy(
                            update={"visible_controls": controls}
                        )
                    }
                )
            },
            deep=True,
        )
        duplicate = RECOVER_CAMERA_VIEW_DEFINITION.bind(
            RecoverCameraViewAction(), ambiguous
        )
        assert not duplicate.bound
        assert "2 unambiguous lower-HUD portrait" in duplicate.reason

        modal = observation.model_copy(
            update={
                "telemetry": observation.telemetry.model_copy(
                    update={
                        "ui": observation.telemetry.ui.model_copy(
                            update={"modal_open": True}
                        )
                    }
                )
            },
            deep=True,
        )
        blocked = RECOVER_CAMERA_VIEW_DEFINITION.bind(RecoverCameraViewAction(), modal)
        assert not blocked.bound
        assert "closed modal" in blocked.reason

    asyncio.run(scenario())


def test_high_visual_score_still_requires_character_anchor(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment, _, _ = camera_environment(tmp_path, mode="clear")
        observation = await environment.reset()
        assert observation.telemetry is not None
        far_camera = observation.model_copy(
            update={
                "telemetry": observation.telemetry.model_copy(
                    update={
                        "camera": observation.telemetry.camera.model_copy(
                            update={"center": Vec3(x=100.0, y=0.0, z=0.0)}
                        )
                    }
                )
            },
            deep=True,
        )
        score = score_camera_observation(
            far_camera,
            candidate="far_anchor",
            floor=0,
            clear_score_threshold=0.72,
            anchor_max_distance=30.0,
        )
        assert score.score >= 0.72
        assert score.anchor_distance == pytest.approx(100.0)
        assert not score.clear

    asyncio.run(scenario())


def test_already_clear_emits_zero_input_and_returns_scored_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, controller = camera_environment(tmp_path, mode="clear")
        await environment.reset()
        transition = await environment.step(RecoverCameraViewAction())

        assert controller.actions == []
        evidence = transition.receipt.semantic.camera_recovery  # type: ignore[union-attr]
        assert evidence is not None
        assert evidence.status is CameraRecoveryStatus.ALREADY_CLEAR
        assert evidence.primitive_actions == 0
        assert evidence.candidates[0].clear
        assert evidence.candidates[0].screenshot_path.exists()

    asyncio.run(scenario())


def test_anchored_structured_view_below_cosmetic_threshold_emits_zero_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, controller = camera_environment(
            tmp_path,
            mode="usable_follow",
        )
        await environment.reset()
        transition = await environment.step(RecoverCameraViewAction())

        evidence = transition.receipt.semantic.camera_recovery  # type: ignore[union-attr]
        assert evidence is not None
        initial = evidence.candidates[0]
        assert initial.score < evidence.clear_score_threshold
        assert initial.selected_world_label_visible
        assert initial.anchor_distance == pytest.approx(0.0)
        assert initial.clear
        assert evidence.status is CameraRecoveryStatus.ALREADY_CLEAR
        assert evidence.primitive_actions == 0
        assert controller.actions == []

    asyncio.run(scenario())


def test_recovery_pauses_once_and_never_unpauses(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = camera_environment(
            tmp_path, mode="follow", paused=False
        )
        await environment.reset()
        transition = await environment.step(RecoverCameraViewAction())

        assert telemetry.paused is True
        assert isinstance(controller.actions[0], KeyAction)
        assert controller.actions[0].key == "space"
        assert not any(
            isinstance(action, KeyAction) and action.key == "space"
            for action in controller.actions[1:]
        )
        evidence = transition.receipt.semantic.camera_recovery  # type: ignore[union-attr]
        assert evidence is not None
        assert evidence.status is CameraRecoveryStatus.RECOVERED
        assert evidence.paused_for_recovery
        assert evidence.follow_method == "portrait_double_click"

    asyncio.run(scenario())


def test_fixed_floor_zoom_orbit_sequence_selects_best_scored_angle(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, controller = camera_environment(tmp_path, mode="orbit")
        await environment.reset()
        transition = await environment.step(RecoverCameraViewAction())

        evidence = transition.receipt.semantic.camera_recovery  # type: ignore[union-attr]
        assert evidence is not None
        assert evidence.status is CameraRecoveryStatus.RECOVERED
        assert evidence.chosen_candidate == "angle_orbit_right"
        assert evidence.final_floor == 0
        assert evidence.primitive_actions == 10
        assert len(controller.actions) == 10
        keys = [action.key for action in controller.actions if isinstance(action, KeyAction)]
        assert keys == ["end", "e", "q", "e", "e"]
        clicks = [action for action in controller.actions if isinstance(action, ClickAction)]
        assert clicks[0].clicks == 2
        assert [round(action.y, 3) for action in clicks[1:]] == [
            0.735,
            0.735,
            0.675,
            0.675,
        ]

    asyncio.run(scenario())


def test_fixed_tilt_sequence_recovers_when_zoom_and_orbit_cannot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, controller = camera_environment(tmp_path, mode="tilt")
        await environment.reset()
        transition = await environment.step(RecoverCameraViewAction())

        evidence = transition.receipt.semantic.camera_recovery  # type: ignore[union-attr]
        assert evidence is not None
        assert evidence.status is CameraRecoveryStatus.RECOVERED
        assert evidence.chosen_candidate == "final_tilt_up"
        assert evidence.final_floor == 0
        assert evidence.primitive_actions == 13
        keys = [action.key for action in controller.actions if isinstance(action, KeyAction)]
        assert keys == [
            "end",
            "e",
            "q",
            "e",
            "comma",
            "period",
            "comma",
            "comma",
        ]

    asyncio.run(scenario())


def test_failure_is_terminal_after_bounded_attempts(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment, _, controller = camera_environment(tmp_path, mode="failure")
        await environment.reset()
        transition = await environment.step(RecoverCameraViewAction())

        evidence = transition.receipt.semantic.camera_recovery  # type: ignore[union-attr]
        assert evidence is not None
        assert evidence.status is CameraRecoveryStatus.FAILED_AFTER_BOUNDED_ATTEMPTS
        assert not evidence.candidates[-1].clear
        assert evidence.primitive_actions == len(controller.actions)
        assert evidence.primitive_actions <= RECOVER_CAMERA_VIEW_DEFINITION.max_primitive_actions

    asyncio.run(scenario())


def test_human_input_aborts_before_the_next_primitive(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment, _, controller = camera_environment(
            tmp_path, mode="failure", user_input_after=1
        )
        await environment.reset()
        with pytest.raises(RuntimeError, match="Human input interrupted camera recovery"):
            await environment.step(RecoverCameraViewAction())
        assert controller.actions == []

    asyncio.run(scenario())
