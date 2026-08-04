import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from operation_test_support import execute_operation

from kenshi_agent.affordances import OperationBindingError
from kenshi_agent.authorization import AuthorizationCode
from kenshi_agent.config import CaptureConfig, ControlsConfig, MacroConfig, RuntimeConfig
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.execution.handlers import kenshi_surface
from kenshi_agent.models import (
    ActionReceipt,
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    CalibrationStatus,
    CameraRotationDirection,
    CharacterState,
    ClickAction,
    CollectResourceOutputAction,
    CommandDispatchContext,
    CommandWorldTargetAction,
    ContextActionKind,
    ControlMode,
    Disposition,
    ExitCurrentBuildingAction,
    GameBinding,
    GameState,
    HotkeyAction,
    InventoryItem,
    KeyAction,
    KnownMapDestination,
    MouseButton,
    MouseButtonAction,
    MouseDragAction,
    MoveInDirectionAction,
    MoveToCharacterAction,
    NativeCommandAcknowledgement,
    NativeCommandRequest,
    NativeCommandStatus,
    NativeControlState,
    NearbyEntity,
    NormalizedPointerBounds,
    OpenContextInventoryAction,
    PauseAction,
    PerformContextAction,
    PointerActionClass,
    ProduceResourceOutputAction,
    RegroupWithSquadMemberAction,
    ResourceTransferStatus,
    RespondToImmediateThreatAction,
    RotateCameraAction,
    SelectSquadMemberAction,
    SelectSquadMemberExactAction,
    SetSpeedAction,
    SkillAction,
    TelemetrySnapshot,
    ThreatResponseStrategy,
    TravelToMapDestinationAction,
    UIState,
    UseGameBindingAction,
    Vec2,
    Vec3,
    VisibleUIControl,
    WorldTarget,
)
from kenshi_agent.skills import MacroRegistry
from kenshi_agent.telemetry import TelemetryRead


class PulseTelemetry:
    def __init__(
        self,
        *,
        auto_pause_after_reads: int | None = None,
        stale: bool = False,
    ) -> None:
        self.paused = True
        self.speed_multiplier = 0.0
        self.sequence = 0
        self.auto_pause_after_reads = auto_pause_after_reads
        self.stale = stale
        self.capabilities: list[str] = []
        self.native_control = NativeControlState()
        self.path = Path("telemetry.json")
        self.max_age_seconds = 3.0

    def read(self) -> TelemetryRead:
        self.sequence += 1
        if (
            self.auto_pause_after_reads is not None
            and self.sequence >= self.auto_pause_after_reads
            and not self.paused
        ):
            self.paused = True
        return TelemetryRead(
            snapshot=TelemetrySnapshot(
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                capabilities=self.capabilities,
                game=GameState(
                    loaded=True,
                    paused=self.paused,
                    speed_multiplier=self.speed_multiplier,
                ),
                native_control=self.native_control,
            ),
            age_seconds=0.0,
            stale=self.stale,
            path=Path("telemetry.json"),
        )


class PulseController(InputController):
    def __init__(
        self,
        telemetry: PulseTelemetry,
        *,
        emergency_after: int | None = None,
        user_input_after: int | None = None,
        continuous_user_input: bool = False,
        client_width: int = 1920,
        client_height: int = 1080,
        ignore_speed_key_once: str | None = None,
        visible_titles: list[str] | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.actions: list[PrimitiveInputAction] = []
        self.emergency_after = emergency_after
        self.emergency_checks = 0
        self.user_input_after = user_input_after
        self.user_input_checks = 0
        self.continuous_user_input = continuous_user_input
        self.client_width = client_width
        self.client_height = client_height
        self.ignore_speed_key_once = ignore_speed_key_once
        self.ignored_speed_key = False
        self.visible_titles = visible_titles

    def focus_window(self) -> None:
        return None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        self.actions.append(action)
        ignore_speed_key = (
            isinstance(action, KeyAction)
            and action.key == self.ignore_speed_key_once
            and not self.ignored_speed_key
        )
        if ignore_speed_key:
            self.ignored_speed_key = True
        if not ignore_speed_key and isinstance(action, KeyAction) and action.key == "space":
            self.telemetry.paused = not self.telemetry.paused
        if not ignore_speed_key and isinstance(action, KeyAction) and action.key == "f2":
            self.telemetry.paused = False
            self.telemetry.speed_multiplier = 1.0
        if (
            not ignore_speed_key
            and isinstance(action, KeyAction)
            and action.key in {"f3", "f4"}
            and not self.telemetry.paused
        ):
            self.telemetry.speed_multiplier = 3.0 if action.key == "f3" else 5.0
        if (
            isinstance(action, ClickAction)
            and action.button == MouseButton.LEFT
            and action.x == 0.765
            and action.y == 0.723
        ):
            self.telemetry.paused = True
        if (
            isinstance(action, ClickAction)
            and action.button == MouseButton.LEFT
            and action.x == 0.792
            and action.y == 0.723
        ):
            self.telemetry.paused = False
        now = datetime.now(UTC)
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
            started_at=now,
            finished_at=now,
            primitive_actions=3 if isinstance(action, ClickAction) else 1,
            message="test input",
        )

    def emergency_stop_pressed(self, key: str) -> bool:
        del key
        self.emergency_checks += 1
        return self.emergency_after is not None and self.emergency_checks >= self.emergency_after

    def user_input_detected(self) -> bool:
        self.user_input_checks += 1
        return self.user_input_after is not None and self.user_input_checks >= self.user_input_after

    def continuous_user_input_detected(self) -> bool:
        return self.continuous_user_input

    def visible_window_titles(self) -> list[str]:
        if self.visible_titles is not None:
            return self.visible_titles
        return super().visible_window_titles()

    def client_rect(self) -> WindowRect:
        return WindowRect(
            left=0,
            top=0,
            right=self.client_width,
            bottom=self.client_height,
        )


class ResizeInsideLeaseController(PulseController):
    @asynccontextmanager
    async def input_lease(self, *, alt_tab_on_restore: bool = False):
        del alt_tab_on_restore
        self.client_width = 1280
        self.client_height = 720
        yield


def movement_registry(
    *,
    pulse_seconds: float = 0.01,
    minimum: float | None = None,
    maximum: float | None = None,
    include_pause_skill: bool = False,
) -> MacroRegistry:
    macros = {
        "move_visible_terrain": MacroConfig(
            movement_pulse_seconds=pulse_seconds,
            movement_pulse_min_seconds=minimum,
            movement_pulse_max_seconds=maximum,
            actions=[
                {
                    "kind": "click",
                    "x": "{{x}}",
                    "y": "{{y}}",
                    "space": "normalized",
                    "button": "right",
                }
            ],
        )
    }
    if include_pause_skill:
        macros["pause_game"] = MacroConfig(
            actions=[
                {
                    "kind": "click",
                    "x": 0.765,
                    "y": 0.723,
                    "space": "normalized",
                    "button": "left",
                }
            ]
        )
        macros["unpause_game"] = MacroConfig(
            actions=[
                {
                    "kind": "click",
                    "x": 0.792,
                    "y": 0.723,
                    "space": "normalized",
                    "button": "left",
                }
            ]
        )
    return MacroRegistry(macros)


def live_environment(
    tmp_path: Path,
    telemetry: PulseTelemetry,
    controller: PulseController,
    registry: MacroRegistry,
    *,
    pause_skill: str | None = None,
    unpause_skill: str | None = None,
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
    quicksave_dir: Path | None = None,
    quicksave_timeout_seconds: float = 10.0,
    quicksave_stable_seconds: float = 0.5,
) -> LiveEnvironment:
    return LiveEnvironment(
        run_id="pulse-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        macros=registry,
        runtime_config=RuntimeConfig(settle_seconds=0.0, objective="Explore nearby."),
        controls_config=ControlsConfig(
            post_input_delay_seconds=0.0,
            pause_skill=pause_skill,
            unpause_skill=unpause_skill,
            # A real live movement config declares its calibrated client size;
            # the default PulseController renders at this exact size.
            calibrated_client_width=1920,
            calibrated_client_height=1080,
        ),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        available_skills=["move_visible_terrain"],
        control_mode=control_mode,
        quicksave_dir=quicksave_dir,
        quicksave_timeout_seconds=quicksave_timeout_seconds,
        quicksave_stable_seconds=quicksave_stable_seconds,
    )


def movement_action(*, duration_seconds: float | None = None) -> SkillAction:
    arguments = {"x": 0.5, "y": 0.5}
    if duration_seconds is not None:
        arguments["duration_seconds"] = duration_seconds
    return SkillAction.model_validate(
        {
            "name": "move_visible_terrain",
            "args": arguments,
        }
    )


def test_semantic_hotkey_binding_dispatches_one_hotkey(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )
        action = UseGameBindingAction(
            binding=GameBinding.EDITOR_TOGGLE,
            expected_effect="toggle the in-game editor",
        )

        await environment.reset()
        transition = await execute_operation(environment, action)
        receipt = transition.receipt

        assert controller.actions == [HotkeyAction(keys=["shift", "f12"])]
        assert receipt.action == action
        assert receipt.semantic is not None
        assert receipt.semantic.resolved_label == "editor_toggle"
        assert "shift+f12" in receipt.message

    asyncio.run(scenario())


def test_semantic_mouse_binding_dispatches_one_held_button(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )
        action = UseGameBindingAction(
            binding=GameBinding.HIGHLIGHT,
            expected_effect="highlight world items while the binding is held",
        )

        await environment.reset()
        transition = await execute_operation(environment, action)
        receipt = transition.receipt

        assert controller.actions == [MouseButtonAction(button=MouseButton.X2, hold_seconds=0.25)]
        assert receipt.action == action
        assert receipt.semantic is not None
        assert receipt.semantic.resolved_label == "highlight"
        assert "x2" in receipt.message

    asyncio.run(scenario())


def test_quicksave_waits_for_an_exact_quiescent_save_tree(tmp_path: Path) -> None:
    from kenshi_agent.models import QuicksaveStatus

    class QuicksavePulseController(PulseController):
        def __init__(self, telemetry: PulseTelemetry, quicksave_dir: Path) -> None:
            super().__init__(telemetry)
            self.quicksave_dir = quicksave_dir

        async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
            receipt = await super().execute(action)
            if isinstance(action, KeyAction) and action.key == "f5":
                self.quicksave_dir.mkdir(parents=True)
                (self.quicksave_dir / "quick.save").write_bytes(b"saved game")
            return receipt

    async def scenario() -> None:
        telemetry = PulseTelemetry()
        quicksave_dir = tmp_path / "save" / "quicksave"
        controller = QuicksavePulseController(telemetry, quicksave_dir)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
            quicksave_dir=quicksave_dir,
            quicksave_timeout_seconds=0.2,
            quicksave_stable_seconds=0.01,
        )
        action = UseGameBindingAction(
            binding=GameBinding.QUICKSAVE,
            expected_effect="write the current game to the quicksave slot",
        )

        transition = await execute_operation(environment, action)

        assert controller.actions == [KeyAction(key="f5")]
        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.quicksave
        assert evidence is not None
        assert evidence.status is QuicksaveStatus.SAVED
        assert evidence.slot == "quicksave"
        assert evidence.changed_files == 1
        assert evidence.quick_save_size_bytes == len(b"saved game")
        assert evidence.quiescent_seconds >= 0.01

    asyncio.run(scenario())


def test_quicksave_does_not_promote_an_input_receipt_to_completion(
    tmp_path: Path,
) -> None:
    from kenshi_agent.models import QuicksaveStatus

    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
            quicksave_dir=tmp_path / "save" / "quicksave",
            quicksave_timeout_seconds=0.03,
            quicksave_stable_seconds=0.01,
        )
        action = UseGameBindingAction(
            binding=GameBinding.QUICKSAVE,
            expected_effect="write the current game to the quicksave slot",
        )

        transition = await execute_operation(environment, action)

        assert transition.receipt.executed
        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.quicksave
        assert evidence is not None
        assert evidence.status is QuicksaveStatus.NOT_OBSERVED
        assert evidence.changed_files == 0
        assert evidence.quick_save_size_bytes is None

    asyncio.run(scenario())


def test_semantic_camera_rotation_dispatches_one_bounded_middle_drag(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, controller = native_vendor_environment(tmp_path)
        action = RotateCameraAction(direction=CameraRotationDirection.RIGHT)

        transition = await execute_operation(environment, action)

        assert controller.actions == [
            MouseDragAction(
                button=MouseButton.MIDDLE,
                delta_x=-96,
                delta_y=0,
                steps=8,
            )
        ]
        assert transition.receipt.semantic is not None
        assert transition.receipt.semantic.resolved_label == "right"

    asyncio.run(scenario())


def test_live_close_causally_pauses_once_and_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        telemetry.paused = False
        telemetry.capabilities = ["game.pause"]
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )

        outcome = await environment.close()
        repeated = await environment.close()

        assert outcome.status == "pause_confirmed"
        assert outcome.initial_sequence is not None
        assert outcome.confirmed_sequence is not None
        assert outcome.confirmed_sequence > outcome.initial_sequence
        assert outcome.input_attempted is True
        assert outcome.input_executed is True
        assert repeated == outcome
        assert controller.actions == [KeyAction(key="space")]
        assert telemetry.paused is True

    asyncio.run(scenario())


def test_terminal_crash_invalidates_frozen_pause_and_emits_no_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        telemetry.paused = True
        telemetry.capabilities = ["game.pause"]
        controller = PulseController(
            telemetry,
            visible_titles=["Kenshi 1.0.65", "Kenshi has crashed"],
        )
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )

        observation = await environment.observe_without_capture()
        outcome = await environment.close()

        assert "terminal_window_detected: Kenshi has crashed" in observation.events
        assert outcome.status == "pause_unverified"
        assert "Kenshi has crashed" in outcome.reason
        assert outcome.input_attempted is False
        assert controller.actions == []

    asyncio.run(scenario())


def test_live_close_emits_no_input_without_fresh_pause_authority(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry(stale=True)
        telemetry.paused = False
        telemetry.capabilities = ["game.pause"]
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )

        outcome = await environment.close()

        assert outcome.status == "pause_unverified"
        assert outcome.input_attempted is False
        assert outcome.input_executed is False
        assert controller.actions == []
        assert telemetry.paused is False

    asyncio.run(scenario())


def test_live_close_does_not_trust_paused_without_pause_capability(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        telemetry.paused = True
        telemetry.capabilities = []
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )

        outcome = await environment.close()

        assert outcome.status == "pause_unverified"
        assert outcome.input_attempted is False
        assert controller.actions == []

    asyncio.run(scenario())


def test_live_close_reports_unverified_when_pause_has_no_causal_effect(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        telemetry.paused = False
        telemetry.capabilities = ["game.pause"]
        # The controller receives the safety key, but the authoritative
        # telemetry source never observes an effect from it.
        controller = PulseController(PulseTelemetry())
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )
        environment.final_pause_timeout_seconds = 0.01

        outcome = await environment.close()

        assert outcome.status == "pause_unverified"
        assert outcome.input_attempted is True
        assert outcome.input_executed is True
        assert outcome.confirmed_sequence is None
        assert controller.actions == [KeyAction(key="space")]
        assert telemetry.paused is False

    asyncio.run(scenario())


def test_control_pause_remains_available_after_human_input_without_a_plan_token(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        telemetry.paused = False
        telemetry.capabilities = ["game.pause"]
        controller = PulseController(telemetry, continuous_user_input=True)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )
        observation = await environment.reset()

        transition = await environment.operation_mechanics.control_pause(
            PauseAction(paused=True),
            command=CommandDispatchContext(
                command_id="cmd-" + "0" * 32,
                based_on_revision=observation.world_revision,
            ),
        )

        assert telemetry.paused is True
        assert controller.actions == [KeyAction(key="space")]
        assert transition.receipt.executed
        assert transition.receipt.causal_revision_advanced is True
        assert "human_input_detected" in transition.observation.events

    asyncio.run(scenario())


def test_movement_pulse_unpauses_and_guarantees_repause(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(tmp_path, telemetry, controller, movement_registry())

        initial = await environment.reset()
        transition = await execute_operation(environment, movement_action())

        assert initial.objective == "Explore nearby."
        assert initial.available_skills == ["move_visible_terrain"]
        assert telemetry.paused is True
        assert transition.observation.telemetry is not None
        assert transition.observation.telemetry.game.paused is True
        assert [action.kind for action in controller.actions] == ["click", "key", "key"]
        assert transition.receipt.primitive_actions == 5
        assert "confirmed re-paused state" in transition.receipt.message

    asyncio.run(scenario())


def test_pointer_skill_rejects_mismatched_calibrated_client_before_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(
            telemetry,
            client_width=1280,
            client_height=720,
        )
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )
        environment.controls_config = ControlsConfig(
            post_input_delay_seconds=0.0,
            calibrated_client_width=1920,
            calibrated_client_height=1080,
        )

        await environment.reset()
        transition = await execute_operation(environment, movement_action())

        assert controller.actions == []
        assert telemetry.paused is True
        assert transition.receipt.accepted is False
        assert transition.receipt.calibration is not None
        assert transition.receipt.calibration.status is CalibrationStatus.MISMATCHED
        assert transition.receipt.input_boundary is not None

    asyncio.run(scenario())


def test_pointer_skill_rechecks_calibrated_client_inside_input_lease(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = ResizeInsideLeaseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )
        environment.controls_config = ControlsConfig(
            post_input_delay_seconds=0.0,
            calibrated_client_width=1920,
            calibrated_client_height=1080,
        )

        await environment.reset()
        transition = await execute_operation(environment, movement_action())

        assert controller.actions == []
        assert telemetry.paused is True
        assert transition.receipt.accepted is False
        assert transition.receipt.input_boundary is not None
        assert (
            transition.receipt.input_boundary.code
            is AuthorizationCode.CALIBRATION_DRIFTED
        )

    asyncio.run(scenario())


def test_live_observation_reports_human_input_and_emergency_stop(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(
            telemetry,
            emergency_after=1,
            continuous_user_input=True,
        )
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )

        current = await environment.reset()

        assert "human_input_detected" in current.events
        assert "emergency_stop_detected" in current.events

    asyncio.run(scenario())


def test_movement_pulse_can_use_click_based_pause_skill(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(include_pause_skill=True),
            pause_skill="pause_game",
            unpause_skill="unpause_game",
        )

        await environment.reset()
        transition = await execute_operation(environment, movement_action())

        assert telemetry.paused is True
        assert [action.kind for action in controller.actions] == ["click", "click", "click"]
        assert transition.receipt.primitive_actions == 9
        assert "confirmed re-paused state" in transition.receipt.message

    asyncio.run(scenario())


def test_separate_transport_controls_are_state_specific(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(include_pause_skill=True),
            pause_skill="pause_game",
            unpause_skill="unpause_game",
        )

        await environment.reset()
        await execute_operation(environment, PauseAction(paused=False))
        await execute_operation(environment, PauseAction(paused=False))
        await execute_operation(environment, PauseAction(paused=True))
        await execute_operation(environment, PauseAction(paused=True))

        clicks = [action for action in controller.actions if isinstance(action, ClickAction)]
        assert [(action.x, action.y) for action in clicks] == [(0.792, 0.723), (0.765, 0.723)]
        assert telemetry.paused is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("speed", "target_key", "multiplier"),
    [(1, "f2", 1.0), (2, "f3", 3.0), (3, "f4", 5.0)],
)
def test_set_speed_owns_starting_a_paused_world(
    tmp_path: Path,
    speed: Literal[1, 2, 3],
    target_key: str,
    multiplier: float,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        telemetry.capabilities = ["game.pause", "game.speed"]
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )

        await environment.reset()
        transition = await execute_operation(environment, SetSpeedAction(speed=speed))

        expected_keys = ["f2"] if speed == 1 else ["f2", target_key]
        assert [
            action.key for action in controller.actions if isinstance(action, KeyAction)
        ] == expected_keys
        assert telemetry.paused is False
        assert telemetry.speed_multiplier == multiplier
        assert transition.receipt.primitive_actions == len(expected_keys)
        assert "running" in transition.receipt.message

    asyncio.run(scenario())


def test_set_speed_reissues_an_idempotent_gear_after_a_dropped_key(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        telemetry.paused = False
        telemetry.speed_multiplier = 5.0
        telemetry.capabilities = ["game.pause", "game.speed"]
        controller = PulseController(telemetry, ignore_speed_key_once="f2")
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
        )

        await environment.reset()
        transition = await execute_operation(environment, SetSpeedAction(speed=1))

        assert [action.key for action in controller.actions if isinstance(action, KeyAction)] == [
            "f2",
            "f2",
        ]
        assert telemetry.paused is False
        assert telemetry.speed_multiplier == 1.0
        assert transition.receipt.primitive_actions == 2

    asyncio.run(scenario())


def test_engage_threat_intent_owns_normal_speed_playback(tmp_path: Path) -> None:
    async def scenario() -> None:
        class ThreatTelemetry(PulseTelemetry):
            def read(self) -> TelemetryRead:
                result = super().read()
                return replace(
                    result,
                    snapshot=result.snapshot.model_copy(
                        update={
                            "capabilities": [
                                "game.pause",
                                "game.speed",
                                "nearby.visible_entities",
                                "squad.health",
                                "control.move_in_direction",
                            ],
                            "ui": UIState(
                                selected_character_id="entity-bark",
                                selected_character_ids=["entity-bark"],
                            ),
                            "squad": [
                                CharacterState(
                                    id="entity-bark",
                                    name="Bark",
                                    selected=True,
                                    alive=True,
                                    conscious=True,
                                    down=False,
                                    getting_eaten=False,
                                    blood=100.0,
                                    position=Vec3(x=0.0, y=0.0, z=0.0),
                                )
                            ],
                            "nearby_entities": [
                                NearbyEntity(
                                    id="hostile-1",
                                    name="Bandit",
                                    disposition=Disposition.HOSTILE,
                                    visible=True,
                                    conscious=True,
                                    distance=10.0,
                                    position=Vec3(x=10.0, y=0.0, z=0.0),
                                )
                            ],
                        }
                    ),
                )

        telemetry = ThreatTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(),
            control_mode=ControlMode.NATIVE_ASSISTED,
        )

        await environment.reset()
        action = RespondToImmediateThreatAction(
            actor_id="entity-bark",
            strategy=ThreatResponseStrategy.ENGAGE,
        )
        transition = await execute_operation(environment, action)

        assert [
            primitive.key for primitive in controller.actions if isinstance(primitive, KeyAction)
        ] == ["f2"]
        assert telemetry.paused is False
        assert telemetry.speed_multiplier == 1.0
        assert transition.receipt.action == action
        assert transition.receipt.semantic is not None
        assert transition.receipt.semantic.target_id == "entity-bark"

    asyncio.run(scenario())


def test_model_can_choose_bounded_movement_duration(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(pulse_seconds=0.01, minimum=0.005, maximum=0.03),
        )
        await environment.reset()

        transition = await execute_operation(environment, movement_action(duration_seconds=0.02))

        assert telemetry.paused is True
        assert "Advanced Kenshi for 0.02s" in transition.receipt.message

    asyncio.run(scenario())


def test_movement_pulse_preserves_unexpected_game_auto_pause(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Reset plus the mandatory input-boundary authority read precede the
        # movement controller's own playback confirmation reads.
        telemetry = PulseTelemetry(auto_pause_after_reads=4)
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(pulse_seconds=0.2),
        )
        await environment.reset()

        transition = await execute_operation(environment, movement_action())

        assert telemetry.paused is True
        assert [action.kind for action in controller.actions] == ["click", "key"]
        assert "auto-paused" in transition.receipt.message

    asyncio.run(scenario())


def test_emergency_stop_ends_pulse_after_repausing(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry, emergency_after=6)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(pulse_seconds=0.2),
        )
        await environment.reset()

        with pytest.raises(RuntimeError, match="after re-pausing"):
            await execute_operation(environment, movement_action())

        assert telemetry.paused is True
        assert [action.kind for action in controller.actions][-2:] == ["key", "key"]

    asyncio.run(scenario())


def test_user_input_ends_pulse_after_repausing(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry, user_input_after=2)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(pulse_seconds=0.2),
        )
        await environment.reset()

        transition = await execute_operation(environment, movement_action())

        assert telemetry.paused is True
        assert [action.kind for action in controller.actions][-2:] == ["key", "key"]
        assert "Human input ended the pulse" in transition.receipt.message
        assert "yielded control" in transition.receipt.message
        assert transition.observation.telemetry is not None
        assert transition.observation.telemetry.game.paused is True

    asyncio.run(scenario())


def test_interface_only_environment_hides_and_rejects_native_assisted_skill(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        telemetry.capabilities = ["game.pause", "control.approach_vendor"]
        telemetry.native_control = NativeControlState(
            available=True,
            last_command_sequence=3,
            last_command="approach_confirmed_vendor",
            last_result="issued",
        )
        controller = PulseController(telemetry)
        registry = MacroRegistry(
            {
                "open_map": MacroConfig(actions=[{"kind": "key", "key": "m"}]),
                "approach_confirmed_vendor": MacroConfig(
                    requires_native_assisted=True,
                    actions=[{"kind": "hotkey", "keys": ["ctrl", "shift", "f10"]}],
                ),
            }
        )
        environment = LiveEnvironment(
            run_id="control-mode-test",
            run_dir=tmp_path,
            telemetry=telemetry,  # type: ignore[arg-type]
            controller=controller,
            macros=registry,
            runtime_config=RuntimeConfig(settle_seconds=0.0),
            controls_config=ControlsConfig(post_input_delay_seconds=0.0),
            capture_config=CaptureConfig(enabled=False),
            execute_actions=False,
            emergency_stop_key="f12",
            available_skills=["open_map", "approach_confirmed_vendor"],
            control_mode=ControlMode.INTERFACE_ONLY,
        )

        observation = await environment.reset()

        assert observation.control_mode == ControlMode.INTERFACE_ONLY
        assert observation.available_skills == ["open_map"]
        assert observation.telemetry is not None
        assert observation.telemetry.capabilities == ["game.pause"]
        assert not observation.telemetry.native_control.available
        assert observation.telemetry.native_control.last_command is None
        with pytest.raises(RuntimeError, match="requires native_assisted"):
            await execute_operation(environment, SkillAction(name="approach_confirmed_vendor"))

        native_environment = LiveEnvironment(
            run_id="native-control-mode-test",
            run_dir=tmp_path,
            telemetry=telemetry,  # type: ignore[arg-type]
            controller=controller,
            macros=registry,
            runtime_config=RuntimeConfig(settle_seconds=0.0),
            controls_config=ControlsConfig(post_input_delay_seconds=0.0),
            capture_config=CaptureConfig(enabled=False),
            execute_actions=False,
            emergency_stop_key="f12",
            available_skills=["open_map", "approach_confirmed_vendor"],
            control_mode=ControlMode.NATIVE_ASSISTED,
        )
        native_observation = await native_environment.reset()
        assert native_observation.available_skills == [
            "approach_confirmed_vendor",
            "open_map",
        ]
        assert native_observation.telemetry is not None
        assert "control.approach_vendor" in native_observation.telemetry.capabilities
        assert native_observation.telemetry.native_control.available
        native_transition = await execute_operation(
            native_environment, SkillAction(name="approach_confirmed_vendor")
        )
        assert native_transition.receipt.control_mode == ControlMode.NATIVE_ASSISTED
        assert native_transition.receipt.dry_run

    asyncio.run(scenario())


class NativePulseTelemetry(PulseTelemetry):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.capabilities = [
            "game.pause",
            "control.approach_vendor",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
            "ui.visible_controls",
            "world.context_targets",
            "world.context_target_screen_positions",
            "control.perform_context_action",
            "control.produce_resource_output",
            "control.open_context_inventory",
        ]
        self.target_distance: float | None = None
        self.target_screen_position: Vec2 | None = None
        self.squad_target_portrait_bounds: NormalizedPointerBounds | None = None
        self.world_target_screen_position: Vec2 | None = None
        self.target_visible: bool | None = None
        self.dialogue_target_id: str | None = None
        self.indoors = False
        self.first_aid_target_enabled = False
        self.known_map_destinations: list[KnownMapDestination] = []
        self.selected_character_id = "entity-selected"
        self.selected_character_ids = ["entity-selected"]

    def read(self) -> TelemetryRead:
        self.sequence += 1
        return TelemetryRead(
            snapshot=TelemetrySnapshot(
                protocol_version="0.3.0",
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                identity_session_id="session-native-test",
                capabilities=self.capabilities,
                game=GameState(
                    loaded=True,
                    paused=self.paused,
                    speed_multiplier=self.speed_multiplier,
                ),
                ui=UIState(
                    selected_character_id=self.selected_character_id,
                    selected_character_ids=self.selected_character_ids,
                    active_screen=("dialogue" if self.dialogue_target_id is not None else "world"),
                    modal_open=self.dialogue_target_id is not None,
                    dialogue_open=self.dialogue_target_id is not None,
                    dialogue_target_id=self.dialogue_target_id,
                    visible_controls=(
                        [
                            VisibleUIControl(
                                label="Ruka",
                                role="text",
                                bounds=self.squad_target_portrait_bounds,
                            )
                        ]
                        if self.squad_target_portrait_bounds is not None
                        else []
                    ),
                    visible_controls_complete=True,
                ),
                native_control=self.native_control,
                squad=[
                    CharacterState(
                        id="entity-selected",
                        name="Wanderer",
                        selected="entity-selected" in self.selected_character_ids,
                        indoors=self.indoors,
                        alive=True,
                        conscious=True,
                        down=False,
                        position=Vec3(x=0.0, y=0.0, z=0.0),
                    ),
                    CharacterState(
                        id="entity-ruka",
                        name="Ruka",
                        selected="entity-ruka" in self.selected_character_ids,
                        alive=True,
                        conscious=False,
                        down=True,
                        position=Vec3(x=500.0, y=0.0, z=750.0),
                    ),
                ],
                nearby_entities=[
                    NearbyEntity(
                        id="entity-vendor",
                        name="Barman",
                        is_animal=False,
                        has_vendor_list=True,
                        is_squad_leader=True,
                        has_dialogue=True,
                        conscious=True,
                        disposition=Disposition.NEUTRAL,
                        distance=self.target_distance,
                        screen_position=self.target_screen_position,
                        visible=self.target_visible,
                    )
                ],
                world_targets=[
                    WorldTarget(
                        id="entity-copper",
                        name="Copper Resource",
                        kind="natural_resource",
                        position=Vec3(x=10.0, y=0.0, z=20.0),
                        distance=30.0,
                        context_actions=[ContextActionKind.OPERATE],
                        default_task="operate_machinery",
                        mining_resource_level=0.8,
                        screen_position=self.world_target_screen_position,
                    ),
                    *(
                        [
                            WorldTarget(
                                id="entity-ruka",
                                name="Ruka",
                                kind="squad_character",
                                position=Vec3(x=500.0, y=0.0, z=750.0),
                                distance=900.0,
                                context_actions=[ContextActionKind("first_aid")],
                                default_task="first_aid",
                            )
                        ]
                        if self.first_aid_target_enabled
                        else []
                    ),
                ],
                known_map_destinations=self.known_map_destinations,
            ),
            age_seconds=0.0,
            stale=False,
            path=self.path,
        )


class ResourceTransferPulseTelemetry(PulseTelemetry):
    def __init__(
        self,
        path: Path,
        *,
        player_inventory_open: bool = True,
        loaded_shop_trader_count: int = 0,
        selected_inventory_accepts_item: bool | None = True,
    ) -> None:
        super().__init__()
        self.path = path
        self.transferred = False
        self.player_inventory_open = player_inventory_open
        self.loaded_shop_trader_count = loaded_shop_trader_count
        self.selected_inventory_accepts_item = selected_inventory_accepts_item

    def read(self) -> TelemetryRead:
        self.sequence += 1
        bounds = NormalizedPointerBounds(
            min_x=0.30,
            max_x=0.36,
            min_y=0.40,
            max_y=0.48,
        )
        return TelemetryRead(
            snapshot=TelemetrySnapshot(
                protocol_version="1.1.0",
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                identity_session_id="session-resource-transfer",
                capabilities=[
                    "identity.stable_handles",
                    "squad.inventory",
                    "ui.context_inventory_target",
                    "ui.inventory",
                    "ui.visible_controls",
                    "world.context_targets",
                ],
                game=GameState(loaded=True, paused=True),
                active_shop_trader_count=self.loaded_shop_trader_count,
                ui=UIState(
                    active_screen=("trade" if self.player_inventory_open else "inventory"),
                    modal_open=True,
                    dialogue_open=False,
                    open_inventory_windows=(2 if self.player_inventory_open else 1),
                    context_inventory_target_id="entity-copper",
                    visible_controls_complete=True,
                    selected_character_id="entity-selected",
                    selected_character_ids=["entity-selected"],
                    visible_controls=[
                        *(
                            []
                            if self.transferred
                            else [
                                VisibleUIControl(
                                    label="Raw Iron 0",
                                    window="COPPER RESOURCE",
                                    role="item",
                                    item_name="Raw Iron",
                                    item_quantity=2,
                                    section="out",
                                    selected_inventory_accepts_item=(
                                        self.selected_inventory_accepts_item
                                    ),
                                    bounds=bounds,
                                )
                            ]
                        ),
                        *(
                            [
                                VisibleUIControl(
                                    label="close",
                                    window="WANDERER",
                                    role="button",
                                    bounds=NormalizedPointerBounds(
                                        min_x=0.70,
                                        max_x=0.72,
                                        min_y=0.20,
                                        max_y=0.24,
                                    ),
                                )
                            ]
                            if self.player_inventory_open
                            else []
                        ),
                    ],
                ),
                squad=[
                    CharacterState(
                        id="entity-selected",
                        name="Wanderer",
                        selected=True,
                        inventory_complete=True,
                        inventory=(
                            [
                                InventoryItem(
                                    name="Raw Iron",
                                    item_name="Raw Iron",
                                    item_quantity=2,
                                    section="main",
                                )
                            ]
                            if self.transferred
                            else []
                        ),
                    )
                ],
                world_targets=[
                    WorldTarget(
                        id="entity-copper",
                        name="Copper Resource",
                        kind="natural_resource",
                        position=Vec3(x=10.0, y=0.0, z=20.0),
                        distance=30.0,
                        context_actions=[ContextActionKind.OPERATE],
                        default_task="operate_machinery",
                    )
                ],
            ),
            age_seconds=0.0,
            stale=False,
            path=self.path,
        )


class ResourceTransferController(PulseController):
    def __init__(self, telemetry: ResourceTransferPulseTelemetry) -> None:
        super().__init__(telemetry)
        self.resource_telemetry = telemetry

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        receipt = await super().execute(action)
        if isinstance(action, ClickAction) and action.button is MouseButton.RIGHT:
            self.resource_telemetry.transferred = True
        return receipt


class NativeAckController(PulseController):
    def __init__(
        self,
        telemetry: NativePulseTelemetry,
        request_path: Path,
        *,
        status: NativeCommandStatus = NativeCommandStatus.ACCEPTED,
        acknowledgement_command_id: str | None = None,
        open_dialogue_on_hotkey: bool = False,
        complete_map_travel_on_unpause: bool = False,
        reason: str | None = None,
    ) -> None:
        super().__init__(telemetry)
        self.request_path = request_path
        self.status = status
        self.acknowledgement_command_id = acknowledgement_command_id
        self.open_dialogue_on_hotkey = open_dialogue_on_hotkey
        self.complete_map_travel_on_unpause = complete_map_travel_on_unpause
        self.reason = reason
        self.request_seen_before_hotkey = False
        self.request: NativeCommandRequest | None = None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        if isinstance(action, HotkeyAction):
            assert self.request_path.is_file()
            self.request_seen_before_hotkey = True
            self.request = NativeCommandRequest.model_validate_json(self.request_path.read_bytes())
            request = self.request
            if self.open_dialogue_on_hotkey:
                self.telemetry.dialogue_target_id = request.target_id
            basis = request.based_on_revision.telemetry_sequence
            assert basis is not None
            acknowledgement_sequence = max(self.telemetry.sequence + 1, basis + 1)
            accepted_sequence = (
                None if self.status == NativeCommandStatus.REJECTED else acknowledgement_sequence
            )
            terminal_sequence = (
                acknowledgement_sequence
                if self.status
                in {
                    NativeCommandStatus.REJECTED,
                    NativeCommandStatus.CANCELLED,
                    NativeCommandStatus.COMPLETED,
                }
                else None
            )
            self.telemetry.native_control = NativeControlState(
                available=True,
                acknowledgements=[
                    NativeCommandAcknowledgement(
                        command_id=(self.acknowledgement_command_id or request.command_id),
                        command=request.command,
                        status=self.status,
                        reason=(
                            self.reason
                            or (
                                "issued"
                                if self.status == NativeCommandStatus.ACCEPTED
                                else self.status.value
                            )
                        ),
                        target_id=request.target_id,
                        context_action=request.context_action,
                        bearing_degrees=request.bearing_degrees,
                        distance_units=request.distance_units,
                        minimum_output_quantity=request.minimum_output_quantity,
                        selected_character_ids=request.selected_character_ids,
                        based_on_telemetry_sequence=basis,
                        acknowledged_at_telemetry_sequence=acknowledgement_sequence,
                        accepted_at_telemetry_sequence=accepted_sequence,
                        terminal_at_telemetry_sequence=terminal_sequence,
                    )
                ],
            )
        receipt = await super().execute(action)
        if (
            self.complete_map_travel_on_unpause
            and isinstance(action, KeyAction)
            and action.key in {"space", "f2"}
            and self.request is not None
        ):
            request = self.request
            basis = request.based_on_revision.telemetry_sequence
            assert basis is not None
            acknowledgement_sequence = max(self.telemetry.sequence + 1, basis + 2)
            self.telemetry.paused = True
            self.telemetry.native_control = NativeControlState(
                available=True,
                acknowledgements=[
                    NativeCommandAcknowledgement(
                        command_id=request.command_id,
                        command=request.command,
                        status=NativeCommandStatus.COMPLETED,
                        reason="map_destination_reached",
                        target_id=request.target_id,
                        selected_character_ids=request.selected_character_ids,
                        based_on_telemetry_sequence=basis,
                        acknowledged_at_telemetry_sequence=acknowledgement_sequence,
                        accepted_at_telemetry_sequence=acknowledgement_sequence,
                        terminal_at_telemetry_sequence=acknowledgement_sequence,
                    )
                ],
            )
        return receipt


def native_vendor_environment(
    tmp_path: Path,
    *,
    status: NativeCommandStatus = NativeCommandStatus.ACCEPTED,
    acknowledgement_command_id: str | None = None,
    open_dialogue_on_hotkey: bool = False,
    complete_map_travel_on_unpause: bool = False,
    reason: str | None = None,
) -> tuple[LiveEnvironment, NativePulseTelemetry, NativeAckController]:
    telemetry_path = tmp_path / "telemetry.latest.json"
    request_path = tmp_path / "native_command.request.json"
    telemetry = NativePulseTelemetry(telemetry_path)
    controller = NativeAckController(
        telemetry,
        request_path,
        status=status,
        acknowledgement_command_id=acknowledgement_command_id,
        open_dialogue_on_hotkey=open_dialogue_on_hotkey,
        complete_map_travel_on_unpause=complete_map_travel_on_unpause,
        reason=reason,
    )
    registry = MacroRegistry(
        {
            "approach_confirmed_vendor": MacroConfig(
                requires_native_assisted=True,
                movement_pulse_seconds=0.01,
                movement_pulse_min_seconds=0.005,
                movement_pulse_max_seconds=0.02,
                actions=[
                    {
                        "kind": "hotkey",
                        "keys": ["ctrl", "shift", "f10"],
                        "hold_seconds": 0.01,
                    }
                ],
            )
        }
    )
    environment = LiveEnvironment(
        run_id="native-command-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        macros=registry,
        runtime_config=RuntimeConfig(settle_seconds=0.0),
        controls_config=ControlsConfig(post_input_delay_seconds=0.0),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        available_skills=["approach_confirmed_vendor"],
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    return environment, telemetry, controller


def native_vendor_action(target_id: str = "entity-vendor") -> SkillAction:
    return SkillAction(
        name="approach_confirmed_vendor",
        args={
            "target_id": target_id,
            "duration_seconds": 0.01,
        },  # type: ignore[arg-type]
    )


def test_world_target_command_rebinds_geometry_inside_input_lease(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.world_target_screen_position = Vec2(x=0.4, y=0.6)
        initial = await environment.reset()
        telemetry.world_target_screen_position = Vec2(x=0.55, y=0.65)
        action = CommandWorldTargetAction(
            target_id="entity-copper",
            context_action=ContextActionKind.OPERATE,
        )

        transition = await execute_operation(
            environment,
            action,
            command=CommandDispatchContext(
                command_id="cmd-" + "f" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert controller.actions == [
            ClickAction(
                x=0.55,
                y=0.65,
                button=MouseButton.RIGHT,
            )
        ]
        assert transition.receipt.semantic is not None
        assert transition.receipt.semantic.target_id == "entity-copper"
        assert transition.receipt.semantic.resolved_label == "operate"
        assert transition.receipt.semantic.resolved_bounds == NormalizedPointerBounds(
            min_x=0.55,
            max_x=0.55,
            min_y=0.65,
            max_y=0.65,
        )

    asyncio.run(scenario())


def test_squad_member_selection_rebinds_geometry_inside_input_lease(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.squad_target_portrait_bounds = NormalizedPointerBounds(
            min_x=0.38,
            max_x=0.44,
            min_y=0.84,
            max_y=0.94,
        )
        initial = await environment.reset()
        telemetry.squad_target_portrait_bounds = NormalizedPointerBounds(
            min_x=0.49,
            max_x=0.55,
            min_y=0.84,
            max_y=0.94,
        )

        transition = await execute_operation(
            environment,
            SelectSquadMemberAction(target_id="entity-ruka"),
            command=CommandDispatchContext(
                command_id="cmd-" + "e" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert controller.actions == [
            ClickAction(
                x=0.52,
                y=(0.84 + 0.94) / 2.0,
                button=MouseButton.LEFT,
            )
        ]
        assert transition.receipt.semantic is not None
        assert transition.receipt.semantic.target_id == "entity-ruka"
        assert transition.receipt.semantic.resolved_bounds == NormalizedPointerBounds(
            min_x=0.49,
            max_x=0.55,
            min_y=0.84,
            max_y=0.94,
        )

    asyncio.run(scenario())


def test_squad_member_selection_uses_exact_native_identity_without_pointer_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
            reason="exact_squad_member_selected",
        )
        telemetry.capabilities.extend(
            [
                "control.select_squad_member",
                "squad.basic",
            ]
        )
        environment.controls_config = environment.controls_config.model_copy(
            update={"native_approach_skill": "approach_confirmed_vendor"}
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            SelectSquadMemberExactAction(target_id="entity-ruka"),
            command=CommandDispatchContext(
                command_id="cmd-" + "f" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert controller.request is not None
        assert controller.request.command == "select_squad_member"
        assert controller.request.selected_character_ids == ["entity-selected"]
        assert controller.request.target_id == "entity-ruka"
        assert [action.kind for action in controller.actions] == ["hotkey"]
        assert transition.receipt.native_acknowledgement is not None
        assert transition.receipt.native_acknowledgement.status is NativeCommandStatus.COMPLETED
        assert transition.receipt.semantic is not None
        assert transition.receipt.semantic.target_id == "entity-ruka"
        assert transition.receipt.semantic.resolved_bounds is None

    asyncio.run(scenario())


def test_exact_native_selection_collapses_a_current_squad_group(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
            reason="exact_squad_member_selected",
        )
        telemetry.capabilities.extend(
            [
                "control.select_squad_member",
                "squad.basic",
            ]
        )
        telemetry.selected_character_ids = ["entity-selected", "entity-ruka"]
        environment.controls_config = environment.controls_config.model_copy(
            update={"native_approach_skill": "approach_confirmed_vendor"}
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            SelectSquadMemberExactAction(target_id="entity-ruka"),
            command=CommandDispatchContext(
                command_id="cmd-" + "a" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert transition.receipt.executed
        assert controller.request is not None
        assert controller.request.command == "select_squad_member"
        assert controller.request.selected_character_ids == [
            "entity-selected",
            "entity-ruka",
        ]
        assert controller.request.target_id == "entity-ruka"

    asyncio.run(scenario())


def test_native_character_movement_carries_the_complete_selected_group(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.capabilities.extend(["control.move_to_character"])
        telemetry.selected_character_ids = ["entity-selected", "entity-ruka"]
        environment.controls_config = environment.controls_config.model_copy(
            update={"native_approach_skill": "approach_confirmed_vendor"}
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            MoveToCharacterAction(target_id="entity-vendor"),
            command=CommandDispatchContext(
                command_id="cmd-" + "b" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert transition.receipt.executed
        assert controller.request is not None
        assert controller.request.command == "move_to_character"
        assert controller.request.selected_character_ids == [
            "entity-selected",
            "entity-ruka",
        ]
        assert controller.request.target_id == "entity-vendor"

    asyncio.run(scenario())


def test_world_target_command_emits_nothing_when_geometry_disappears(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.world_target_screen_position = Vec2(x=0.4, y=0.6)
        initial = await environment.reset()
        telemetry.world_target_screen_position = None

        transition = await execute_operation(
            environment,
            CommandWorldTargetAction(
                target_id="entity-copper",
                context_action=ContextActionKind.OPERATE,
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "f" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert not transition.receipt.executed
        assert transition.receipt.input_boundary is not None
        assert transition.receipt.input_boundary.code.value == "binding_absent"
        assert controller.actions == []

    asyncio.run(scenario())


def test_native_vendor_request_precedes_hotkey_and_matching_later_ack(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        initial = await environment.reset()
        command = CommandDispatchContext(
            command_id="cmd-0123456789abcdef0123456789abcdef",
            based_on_revision=initial.world_revision,
        )

        transition = await execute_operation(
            environment,
            native_vendor_action(),
            command=command,
        )

        assert controller.request_seen_before_hotkey
        assert controller.request is not None
        assert controller.request.command_id == command.command_id
        assert controller.request.based_on_revision.telemetry_sequence is not None
        assert controller.request.based_on_revision.telemetry_sequence >= (
            initial.world_revision.telemetry_sequence or 0
        )
        assert controller.request.selected_character_ids == ["entity-selected"]
        assert controller.request.target_id == "entity-vendor"
        assert [action.kind for action in controller.actions] == [
            "hotkey",
            "key",
            "key",
        ]
        assert telemetry.paused is True
        assert transition.receipt.accepted
        assert transition.receipt.executed
        assert transition.receipt.command_id == command.command_id
        assert transition.receipt.causal_revision_advanced is True
        assert transition.receipt.native_acknowledgement is not None
        assert transition.receipt.native_acknowledgement.command_id == command.command_id
        assert "acknowledgement 'accepted'" in transition.receipt.message

    asyncio.run(scenario())


def test_native_vendor_dispatch_accepts_same_telemetry_without_capture_basis(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, controller = native_vendor_environment(tmp_path)
        initial = await environment.reset()
        command = CommandDispatchContext(
            command_id="cmd-0123456789abcdef0123456789abcdef",
            based_on_revision=initial.world_revision.model_copy(
                update={
                    "frame_sequence": 7,
                    "observed_at_monotonic": (initial.world_revision.observed_at_monotonic + 1.0),
                }
            ),
        )

        transition = await execute_operation(
            environment,
            native_vendor_action(),
            command=command,
        )

        assert transition.receipt.executed
        assert controller.request is not None
        assert controller.request.based_on_revision.telemetry_sequence is not None
        assert controller.request.based_on_revision.telemetry_sequence >= (
            command.based_on_revision.telemetry_sequence or 0
        )

    asyncio.run(scenario())


def test_native_vendor_dispatch_rebases_an_older_authorized_revision(
    tmp_path: Path,
) -> None:
    """An order authorized a few telemetry ticks ago still issues on the newest.

    Re-basing forward after re-proving every fact preserves as much of the
    plug-in's bounded cross-process transit window as possible.
    """

    async def scenario() -> None:
        environment, _, controller = native_vendor_environment(tmp_path)
        initial = await environment.reset()
        sequence = initial.world_revision.telemetry_sequence
        assert sequence is not None

        transition = await execute_operation(
            environment,
            native_vendor_action(),
            command=CommandDispatchContext(
                command_id="cmd-0123456789abcdef0123456789abcdef",
                based_on_revision=initial.world_revision.model_copy(
                    update={"telemetry_sequence": sequence - 1}
                ),
            ),
        )

        assert transition.receipt.executed
        assert controller.request is not None
        # Issued on the snapshot read at dispatch, not the older authorization.
        assert controller.request.based_on_revision.telemetry_sequence is not None
        assert controller.request.based_on_revision.telemetry_sequence > sequence - 1

    asyncio.run(scenario())


def test_native_vendor_dispatch_rejects_a_basis_ahead_of_telemetry(
    tmp_path: Path,
) -> None:
    """Re-basing may only move forward, never onto evidence never observed."""

    async def scenario() -> None:
        environment, _, controller = native_vendor_environment(tmp_path)
        initial = await environment.reset()
        sequence = initial.world_revision.telemetry_sequence
        assert sequence is not None

        with pytest.raises(RuntimeError, match="regressed behind the authorized revision"):
            await execute_operation(
                environment,
                native_vendor_action(),
                command=CommandDispatchContext(
                    command_id="cmd-0123456789abcdef0123456789abcdef",
                    based_on_revision=initial.world_revision.model_copy(
                        update={"telemetry_sequence": sequence + 1000}
                    ),
                ),
            )

        assert controller.actions == []

    asyncio.run(scenario())


def test_old_native_ack_cannot_satisfy_new_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            acknowledgement_command_id=("cmd-ffffffffffffffffffffffffffffffff"),
        )
        monkeypatch.setattr(kenshi_surface, "NATIVE_COMMAND_ACK_TIMEOUT_SECONDS", 0.03)
        monkeypatch.setattr(kenshi_surface, "NATIVE_COMMAND_POLL_SECONDS", 0.005)
        initial = await environment.reset()

        with pytest.raises(RuntimeError, match="never confirmed"):
            await execute_operation(
                environment,
                native_vendor_action(),
                command=CommandDispatchContext(
                    command_id="cmd-0123456789abcdef0123456789abcdef",
                    based_on_revision=initial.world_revision,
                ),
            )

        assert [action.kind for action in controller.actions] == ["hotkey"]
        assert telemetry.paused is True

    asyncio.run(scenario())


def test_definitive_native_rejection_does_not_start_movement(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.REJECTED,
        )
        initial = await environment.reset()
        command = CommandDispatchContext(
            command_id="cmd-0123456789abcdef0123456789abcdef",
            based_on_revision=initial.world_revision,
        )

        transition = await execute_operation(
            environment,
            native_vendor_action(),
            command=command,
        )

        assert [action.kind for action in controller.actions] == ["hotkey"]
        assert telemetry.paused is True
        assert not transition.receipt.accepted
        assert not transition.receipt.executed
        assert transition.receipt.error_type == "NativeCommandRejected"
        assert transition.receipt.command_id == command.command_id
        assert (
            transition.receipt.native_acknowledgement is not None
            and transition.receipt.native_acknowledgement.status == NativeCommandStatus.REJECTED
        )

    asyncio.run(scenario())


def test_native_target_must_still_match_current_stable_observation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        initial = await environment.reset()

        with pytest.raises(RuntimeError, match="absent from current nearby"):
            await execute_operation(
                environment,
                native_vendor_action("entity-replaced"),
                command=CommandDispatchContext(
                    command_id="cmd-0123456789abcdef0123456789abcdef",
                    based_on_revision=initial.world_revision,
                ),
            )

        assert controller.actions == []
        assert telemetry.paused is True

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Generic visible-control activation
#
# Bounds come from telemetry and are re-resolved inside the acquired lease.
# These tests exercise the drift cases that must emit zero input.
# ---------------------------------------------------------------------------


class ControlTelemetry(PulseTelemetry):
    """Telemetry whose advertised controls can change between reads."""

    def __init__(self, controls: list[VisibleUIControl] | None) -> None:
        super().__init__()
        self.capabilities = ["game.pause", "ui.visible_controls"]
        self.controls = controls
        self.controls_after_first_read: list[VisibleUIControl] | None = None
        self._reads = 0

    def read(self) -> TelemetryRead:
        self._reads += 1
        if self._reads > 1 and self.controls_after_first_read is not None:
            self.controls = self.controls_after_first_read
        self.sequence += 1
        return TelemetryRead(
            snapshot=TelemetrySnapshot(
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                capabilities=self.capabilities,
                game=GameState(loaded=True, paused=self.paused),
                ui=UIState(visible_controls=self.controls),
                native_control=self.native_control,
            ),
            age_seconds=0.0,
            stale=False,
            path=Path("telemetry.json"),
        )


def control(label: str, y: float, role: str = "button") -> VisibleUIControl:
    return VisibleUIControl(
        label=label,
        role=role,  # type: ignore[arg-type]
        bounds=NormalizedPointerBounds(min_x=0.2, max_x=0.6, min_y=y, max_y=y + 0.04),
    )


def control_environment(
    tmp_path: Path, telemetry: ControlTelemetry
) -> tuple[LiveEnvironment, PulseController]:
    controller = PulseController(telemetry)  # type: ignore[arg-type]
    environment = live_environment(
        tmp_path,
        telemetry,  # type: ignore[arg-type]
        controller,
        movement_registry(),
    )
    return environment, controller


def test_visible_control_clicks_the_observed_bounds_center(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = ControlTelemetry([control("Show me your goods.", 0.50)])
        environment, controller = control_environment(tmp_path, telemetry)
        await environment.observe()

        transition = await execute_operation(
            environment,
            ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
        )

        clicks = [a for a in controller.actions if isinstance(a, ClickAction)]
        assert len(clicks) == 1
        # Center of the telemetry-reported bounds, never a model-authored point.
        assert clicks[0].x == pytest.approx(0.4)
        assert clicks[0].y == pytest.approx(0.52)
        assert transition.receipt.executed
        semantic = transition.receipt.semantic
        assert semantic is not None
        assert semantic.resolved_label == "Show me your goods."
        assert semantic.resolved_role == "button"
        assert semantic.resolved_bounds is not None
        assert semantic.source_revision is not None
        assert "Re-resolved" in semantic.revalidation

    asyncio.run(scenario())


def test_two_different_labels_use_the_same_action(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = ControlTelemetry(
            [control("Show me your goods.", 0.50), control("Goodbye.", 0.70)]
        )
        environment, controller = control_environment(tmp_path, telemetry)
        await environment.observe()

        await execute_operation(
            environment, ActivateVisibleControlAction(exact_label="Goodbye.", role="button")
        )
        clicks = [a for a in controller.actions if isinstance(a, ClickAction)]
        assert clicks[-1].y == pytest.approx(0.72)

        await execute_operation(
            environment,
            ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
        )
        clicks = [a for a in controller.actions if isinstance(a, ClickAction)]
        assert clicks[-1].y == pytest.approx(0.52)

    asyncio.run(scenario())


def test_control_that_disappears_inside_the_lease_emits_zero_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = ControlTelemetry([control("Show me your goods.", 0.50)])
        telemetry.controls_after_first_read = [control("Goodbye.", 0.70)]
        environment, controller = control_environment(tmp_path, telemetry)
        await environment.observe()

        transition = await execute_operation(
            environment,
            ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
        )

        assert not transition.receipt.executed
        assert transition.receipt.input_boundary is not None
        assert transition.receipt.input_boundary.code.value == "binding_absent"
        assert not [a for a in controller.actions if isinstance(a, ClickAction)]

    asyncio.run(scenario())


def test_control_that_becomes_ambiguous_inside_the_lease_emits_zero_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = ControlTelemetry([control("Trade", 0.50)])
        telemetry.controls_after_first_read = [control("Trade", 0.50), control("Trade", 0.80)]
        environment, controller = control_environment(tmp_path, telemetry)
        await environment.observe()

        transition = await execute_operation(
            environment, ActivateVisibleControlAction(exact_label="Trade", role="button")
        )

        assert not transition.receipt.executed
        assert transition.receipt.input_boundary is not None
        assert transition.receipt.input_boundary.code.value == "binding_ambiguous"
        assert not [a for a in controller.actions if isinstance(a, ClickAction)]

    asyncio.run(scenario())


def test_visible_control_is_semantic_current_not_profile_calibrated(
    tmp_path: Path,
) -> None:
    """A resolution change must not block an action whose bounds are re-read."""

    async def scenario() -> None:
        telemetry = ControlTelemetry([control("Show me your goods.", 0.50)])
        controller = ResizeInsideLeaseController(telemetry)  # type: ignore[arg-type]
        environment = live_environment(
            tmp_path,
            telemetry,  # type: ignore[arg-type]
            controller,
            movement_registry(),
        )
        await environment.observe()

        action = ActivateVisibleControlAction(exact_label="Show me your goods.", role="button")
        transition = await execute_operation(environment, action)
        assert transition.receipt.executed
        assert transition.receipt.calibration is not None
        assert transition.receipt.calibration.action_class is PointerActionClass.SEMANTIC_CURRENT
        assert transition.receipt.calibration.status is CalibrationStatus.NOT_REQUIRED

    asyncio.run(scenario())


def test_semantic_approach_adopts_an_already_active_order_for_the_same_target(
    tmp_path: Path,
) -> None:
    """A pathing order outlives the run that issued it.

    Finding the character already walking toward the exact target must not
    produce a second at-most-once command; the action adopts the in-flight order
    and continues it with time instead.
    """

    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        # Advance past the acknowledgement's own sequences so the snapshot
        # invariant (an ack cannot claim a future sequence) holds.
        telemetry.sequence = 10
        # An accepted order toward this exact target is already active.
        active_id = "cmd-" + "b" * 32
        telemetry.native_control = NativeControlState(
            available=True,
            active_command_id=active_id,
            acknowledgements=[
                NativeCommandAcknowledgement(
                    command_id=active_id,
                    command="approach_confirmed_vendor",
                    status=NativeCommandStatus.ACCEPTED,
                    reason="issued",
                    target_id="entity-vendor",
                    selected_character_ids=["entity-selected"],
                    based_on_telemetry_sequence=1,
                    acknowledged_at_telemetry_sequence=2,
                    accepted_at_telemetry_sequence=2,
                )
            ],
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ApproachDialogueTargetAction(target_id="entity-vendor"),
            command=CommandDispatchContext(
                command_id="cmd-" + "c" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        # No second native order: the hotkey was never pressed and no request
        # file was written by this dispatch.
        assert not [a for a in controller.actions if isinstance(a, HotkeyAction)]
        assert controller.request is None
        # It adopted the in-flight order rather than inventing a new identity.
        ack = transition.receipt.native_acknowledgement
        assert ack is not None
        assert ack.command_id == active_id
        semantic = transition.receipt.semantic
        assert semantic is not None
        assert "Adopted" in semantic.revalidation
        assert telemetry.paused is True

    asyncio.run(scenario())


def test_semantic_approach_issues_one_order_when_none_is_active(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.selected_character_ids = ["entity-selected", "entity-ruka"]
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ApproachDialogueTargetAction(target_id="entity-vendor"),
            command=CommandDispatchContext(
                command_id="cmd-" + "d" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        hotkeys = [a for a in controller.actions if isinstance(a, HotkeyAction)]
        assert len(hotkeys) == 1, "exactly one pathing order per option lifecycle"
        assert controller.request is not None
        assert controller.request.target_id == "entity-vendor"
        assert controller.request.selected_character_ids == [
            "entity-selected",
            "entity-ruka",
        ]
        assert transition.receipt.executed
        assert telemetry.paused is True

    asyncio.run(scenario())


def test_context_action_issues_exact_native_resource_task_without_world_click(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
        )
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            PerformContextAction(
                target_id="entity-copper",
                context_action=ContextActionKind.OPERATE,
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "2" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert not [action for action in controller.actions if isinstance(action, ClickAction)]
        assert (
            len([action for action in controller.actions if isinstance(action, HotkeyAction)]) == 1
        )
        assert controller.request is not None
        assert controller.request.command == "perform_context_action"
        assert controller.request.context_action == "operate"
        assert controller.request.target_id == "entity-copper"
        assert transition.receipt.executed
        assert transition.receipt.semantic is not None
        assert transition.receipt.semantic.resolved_label == "operate"

    asyncio.run(scenario())


def test_a_started_context_task_leaves_the_world_running(tmp_path: Path) -> None:
    """A context order Kenshi has only *started* still owes the caller a running world.

    Native code reports "completed"/context_task_started the moment the selected
    character adopts the exact AI goal. Treating that as a finished terminal
    leaves the character holding a job in a world that never advances, so it
    walks nowhere and mines nothing - which is exactly what a live run showed.
    """

    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
            reason="context_task_started",
        )
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                # The canonical live configuration; a run that plays continuously
                # is allowed to leave the world running.
                "require_paused_between_actions": False,
            }
        )
        telemetry.paused = True
        initial = await environment.reset()

        await execute_operation(
            environment,
            PerformContextAction(
                target_id="entity-copper",
                context_action=ContextActionKind.OPERATE,
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "3" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        speed_key = environment.controls_config.speed_keys[1]
        assert [
            action
            for action in controller.actions
            if isinstance(action, KeyAction) and action.key == speed_key
        ], "the started context task left Kenshi paused, so the job could never run"

    asyncio.run(scenario())


def test_first_aid_uses_the_same_exact_semantic_native_route(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
            reason="context_task_started",
        )
        telemetry.first_aid_target_enabled = True
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            PerformContextAction(
                target_id="entity-ruka",
                context_action=ContextActionKind("first_aid"),
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "a" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert not [action for action in controller.actions if isinstance(action, ClickAction)]
        assert (
            len([action for action in controller.actions if isinstance(action, HotkeyAction)]) == 1
        )
        assert controller.request is not None
        assert controller.request.command == "perform_context_action"
        assert controller.request.context_action == "first_aid"
        assert controller.request.target_id == "entity-ruka"
        assert transition.receipt.executed
        assert transition.receipt.semantic is not None
        assert transition.receipt.semantic.resolved_label == "first_aid"

    asyncio.run(scenario())


def test_resource_production_issues_exact_monitored_native_command(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
            reason="resource_output_ready",
        )
        environment.controls_config = environment.controls_config.model_copy(
            update={"native_approach_skill": "approach_confirmed_vendor"}
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ProduceResourceOutputAction(target_id="entity-copper"),
            command=CommandDispatchContext(
                command_id="cmd-" + "3" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert not [action for action in controller.actions if isinstance(action, ClickAction)]
        assert (
            len([action for action in controller.actions if isinstance(action, HotkeyAction)]) == 1
        )
        assert controller.request is not None
        assert controller.request.command == "produce_resource_output"
        assert controller.request.target_id == "entity-copper"
        assert transition.receipt.executed
        acknowledgement = transition.receipt.native_acknowledgement
        assert acknowledgement is not None
        assert acknowledgement.reason == "resource_output_ready"

    asyncio.run(scenario())


def test_context_inventory_requires_exact_native_terminal(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
            reason="exact_context_inventory_open",
        )
        environment.controls_config = environment.controls_config.model_copy(
            update={"native_approach_skill": "approach_confirmed_vendor"}
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            OpenContextInventoryAction(target_id="entity-copper"),
            command=CommandDispatchContext(
                command_id="cmd-" + "4" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert (
            len([action for action in controller.actions if isinstance(action, HotkeyAction)]) == 1
        )
        assert controller.request is not None
        assert controller.request.command == "open_context_inventory"
        assert controller.request.target_id == "entity-copper"
        acknowledgement = transition.receipt.native_acknowledgement
        assert acknowledgement is not None
        assert acknowledgement.status is NativeCommandStatus.COMPLETED
        assert acknowledgement.reason == "exact_context_inventory_open"

    asyncio.run(scenario())


def test_collect_resource_output_requires_conserved_transfer(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = ResourceTransferPulseTelemetry(
            tmp_path / "telemetry.latest.json",
            loaded_shop_trader_count=2,
        )
        controller = ResourceTransferController(telemetry)
        environment = LiveEnvironment(
            run_id="resource-transfer-test",
            run_dir=tmp_path,
            telemetry=telemetry,  # type: ignore[arg-type]
            controller=controller,
            macros=MacroRegistry({}),
            runtime_config=RuntimeConfig(settle_seconds=0.0),
            controls_config=ControlsConfig(
                post_input_delay_seconds=0.0,
                item_cell_hover_seconds=0.0,
            ),
            capture_config=CaptureConfig(enabled=False),
            execute_actions=True,
            emergency_stop_key="f12",
            available_skills=[],
            control_mode=ControlMode.NATIVE_ASSISTED,
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            CollectResourceOutputAction(
                target_id="entity-copper",
                cell_label="Raw Iron 0",
                item_name="Raw Iron",
                source_quantity=2,
                window="COPPER RESOURCE",
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "5" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert [action.kind for action in controller.actions] == ["move_cursor", "click"]
        evidence = transition.receipt.semantic
        assert evidence is not None
        assert evidence.resource_transfer is not None
        assert evidence.resource_transfer.status is ResourceTransferStatus.TRANSFERRED
        assert evidence.resource_transfer.source_quantity_before == 2
        assert evidence.resource_transfer.source_quantity_after == 0
        assert evidence.resource_transfer.destination_quantity_before == 0
        assert evidence.resource_transfer.destination_quantity_after == 2
        assert transition.receipt.error_type is None

    asyncio.run(scenario())


def test_collect_resource_output_emits_zero_input_without_destination_window(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = ResourceTransferPulseTelemetry(
            tmp_path / "telemetry.latest.json",
            player_inventory_open=False,
        )
        controller = ResourceTransferController(telemetry)
        environment = LiveEnvironment(
            run_id="resource-transfer-missing-destination",
            run_dir=tmp_path,
            telemetry=telemetry,  # type: ignore[arg-type]
            controller=controller,
            macros=MacroRegistry({}),
            runtime_config=RuntimeConfig(settle_seconds=0.0),
            controls_config=ControlsConfig(
                post_input_delay_seconds=0.0,
                item_cell_hover_seconds=0.0,
            ),
            capture_config=CaptureConfig(enabled=False),
            execute_actions=True,
            emergency_stop_key="f12",
            available_skills=[],
            control_mode=ControlMode.NATIVE_ASSISTED,
        )
        initial = await environment.reset()

        with pytest.raises(
            OperationBindingError,
            match="selected character.*inventory",
        ) as rejected:
            await execute_operation(
                environment,
                CollectResourceOutputAction(
                    target_id="entity-copper",
                    cell_label="Raw Iron 0",
                    item_name="Raw Iron",
                    source_quantity=2,
                    window="COPPER RESOURCE",
                ),
                command=CommandDispatchContext(
                    command_id="cmd-" + "6" * 32,
                    based_on_revision=initial.world_revision,
                ),
            )

        assert rejected.value.code is AuthorizationCode.BINDING_ABSENT
        assert controller.actions == []

    asyncio.run(scenario())


def test_collect_resource_output_emits_zero_input_when_destination_rejects_item(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        telemetry = ResourceTransferPulseTelemetry(
            tmp_path / "telemetry.latest.json",
            selected_inventory_accepts_item=False,
        )
        controller = ResourceTransferController(telemetry)
        environment = LiveEnvironment(
            run_id="resource-transfer-full-destination",
            run_dir=tmp_path,
            telemetry=telemetry,  # type: ignore[arg-type]
            controller=controller,
            macros=MacroRegistry({}),
            runtime_config=RuntimeConfig(settle_seconds=0.0),
            controls_config=ControlsConfig(
                post_input_delay_seconds=0.0,
                item_cell_hover_seconds=0.0,
            ),
            capture_config=CaptureConfig(enabled=False),
            execute_actions=True,
            emergency_stop_key="f12",
            available_skills=[],
            control_mode=ControlMode.NATIVE_ASSISTED,
        )
        initial = await environment.reset()

        with pytest.raises(
            OperationBindingError,
            match="does not explicitly accept",
        ) as rejected:
            await execute_operation(
                environment,
                CollectResourceOutputAction(
                    target_id="entity-copper",
                    cell_label="Raw Iron 0",
                    item_name="Raw Iron",
                    source_quantity=2,
                    window="COPPER RESOURCE",
                ),
                command=CommandDispatchContext(
                    command_id="cmd-" + "7" * 32,
                    based_on_revision=initial.world_revision,
                ),
            )

        assert rejected.value.code is AuthorizationCode.BINDING_ABSENT
        assert controller.actions == []

    asyncio.run(scenario())


def test_visible_nearby_dialogue_target_still_uses_native_talk_order(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        # These are the exact facts that used to trigger a projected world
        # click. They must now be irrelevant to dispatch.
        telemetry.target_distance = 11.5
        telemetry.target_screen_position = Vec2(x=0.51, y=0.54)
        telemetry.target_visible = True
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ApproachDialogueTargetAction(target_id="entity-vendor"),
            command=CommandDispatchContext(
                command_id="cmd-" + "e" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert not [action for action in controller.actions if isinstance(action, ClickAction)]
        hotkeys = [action for action in controller.actions if isinstance(action, HotkeyAction)]
        assert len(hotkeys) == 1
        assert controller.request is not None
        assert controller.request.command == "approach_confirmed_vendor"
        assert controller.request.target_id == "entity-vendor"
        assert telemetry.paused is True
        assert transition.receipt.native_acknowledgement is not None
        assert transition.receipt.semantic is not None
        assert "PLAYER_TALK_TO" in transition.receipt.semantic.revalidation

    asyncio.run(scenario())


def test_paused_native_talk_stops_before_movement_pulse_when_dialogue_opens(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            open_dialogue_on_hotkey=True,
        )
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ApproachDialogueTargetAction(target_id="entity-vendor"),
            command=CommandDispatchContext(
                command_id="cmd-" + "f" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert telemetry.paused is True
        assert telemetry.dialogue_target_id == "entity-vendor"
        assert not [action for action in controller.actions if isinstance(action, PauseAction)]
        assert "no movement pulse or pause toggle" in transition.receipt.message

    asyncio.run(scenario())


def test_direction_request_is_targetless_and_revalidates_its_own_capabilities(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.capabilities = [
            "game.pause",
            "control.move_in_direction",
            "squad.health",
        ]
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            MoveInDirectionAction(
                bearing_degrees=90.0,
                distance_units=250.0,
                expected_effect="leave the current building",
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "e" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert transition.receipt.executed
        assert controller.request is not None
        assert controller.request.command == "move_in_direction"
        assert controller.request.target_id == ""
        assert controller.request.bearing_degrees == 90.0
        assert controller.request.distance_units == 250.0
        acknowledgement = transition.receipt.native_acknowledgement
        assert acknowledgement is not None
        assert acknowledgement.target_id == ""
        assert acknowledgement.bearing_degrees == 90.0
        assert acknowledgement.distance_units == 250.0

    asyncio.run(scenario())


def test_map_travel_issues_one_exact_order_and_establishes_five_x(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.capabilities = [
            "game.pause",
            "game.speed",
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "identity.stable_handles",
            "squad.health",
        ]
        telemetry.known_map_destinations = [
            KnownMapDestination(
                id="entity-known-town",
                name="The Hub",
                distance=1250.0,
            )
        ]
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "require_paused_between_actions": False,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            TravelToMapDestinationAction(
                destination_id="entity-known-town",
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "d" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert transition.receipt.executed
        assert controller.request is not None
        assert controller.request.command == "travel_to_map_destination"
        assert controller.request.target_id == "entity-known-town"
        assert telemetry.paused is False
        assert telemetry.speed_multiplier == 5.0
        assert [action.kind for action in controller.actions] == ["hotkey", "key", "key"]

    asyncio.run(scenario())


def test_map_travel_carries_the_complete_selected_squad_basis(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.capabilities = [
            "game.pause",
            "game.speed",
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "identity.stable_handles",
            "squad.health",
        ]
        telemetry.selected_character_ids = ["entity-selected", "entity-ruka"]
        telemetry.known_map_destinations = [
            KnownMapDestination(
                id="entity-known-town",
                name="The Hub",
                distance=1250.0,
            )
        ]
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "require_paused_between_actions": False,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            TravelToMapDestinationAction(destination_id="entity-known-town"),
            command=CommandDispatchContext(
                command_id="cmd-" + "c" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert transition.receipt.executed
        assert controller.request is not None
        assert controller.request.selected_character_ids == [
            "entity-selected",
            "entity-ruka",
        ]

    asyncio.run(scenario())


def test_squad_regroup_issues_one_global_exact_order_and_establishes_five_x(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.capabilities = [
            "game.pause",
            "game.speed",
            "control.regroup_with_squad_member",
            "identity.stable_handles",
            "squad.basic",
            "squad.health",
        ]
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
                "require_paused_between_actions": False,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            RegroupWithSquadMemberAction(
                actor_id="entity-selected",
                target_id="entity-ruka",
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "b" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert transition.receipt.executed
        assert controller.request is not None
        assert controller.request.command == "regroup_with_squad_member"
        assert controller.request.selected_character_ids == ["entity-selected"]
        assert controller.request.target_id == "entity-ruka"
        assert telemetry.paused is False
        assert telemetry.speed_multiplier == 5.0
        assert [action.kind for action in controller.actions] == [
            "hotkey",
            "key",
            "key",
        ]

    asyncio.run(scenario())


def test_map_arrival_terminal_wins_race_with_running_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arrival may re-pause the same tick that the controller starts time."""

    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            complete_map_travel_on_unpause=True,
        )
        telemetry.capabilities = [
            "game.pause",
            "game.speed",
            "control.travel_to_map_destination",
            "world.known_map_destinations",
            "identity.stable_handles",
            "squad.health",
        ]
        telemetry.known_map_destinations = [
                KnownMapDestination(
                    id="entity-known-town",
                    name="The Hub",
                    distance=75.0,
                )
        ]
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "require_paused_between_actions": False,
            }
        )

        async def immediate_pause_check(
            expected: bool,
            *,
            timeout_seconds: float = 3.0,
        ) -> bool:
            del timeout_seconds
            return telemetry.paused is expected

        monkeypatch.setattr(
            environment.control_surface,
            "wait_for_pause_state",
            immediate_pause_check,
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            TravelToMapDestinationAction(destination_id="entity-known-town"),
            command=CommandDispatchContext(
                command_id="cmd-" + "a" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        acknowledgement = transition.receipt.native_acknowledgement
        assert transition.receipt.accepted
        assert transition.receipt.executed
        assert acknowledgement is not None
        assert acknowledgement.status is NativeCommandStatus.COMPLETED
        assert acknowledgement.reason == "map_destination_reached"
        assert telemetry.paused is True
        assert [action.kind for action in controller.actions] == ["hotkey", "key"]

    asyncio.run(scenario())


def test_building_exit_request_is_parameterless_and_requires_current_indoor_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.capabilities = [
            "game.pause",
            "control.exit_current_building",
            "identity.stable_handles",
            "squad.indoors",
        ]
        telemetry.indoors = True
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ExitCurrentBuildingAction(),
            command=CommandDispatchContext(
                command_id="cmd-" + "d" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert transition.receipt.executed
        assert controller.request is not None
        assert controller.request.command == "exit_current_building"
        assert controller.request.target_id == ""
        assert controller.request.bearing_degrees == 0.0
        assert controller.request.distance_units == 0.0

        telemetry.indoors = False
        later = await environment.observe_without_capture()
        with pytest.raises(OperationBindingError, match="not confirmed inside") as rejected:
            await execute_operation(
                environment,
                ExitCurrentBuildingAction(),
                command=CommandDispatchContext(
                    command_id="cmd-" + "c" * 32,
                    based_on_revision=later.world_revision,
                ),
            )
        assert rejected.value.code is AuthorizationCode.BINDING_ABSENT

    asyncio.run(scenario())


def test_continuous_native_movement_starts_a_paused_world_without_repausing(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.capabilities = [
            "game.pause",
            "control.exit_current_building",
            "identity.stable_handles",
            "squad.indoors",
        ]
        telemetry.indoors = True
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "require_paused_between_actions": False,
            }
        )
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ExitCurrentBuildingAction(),
            command=CommandDispatchContext(
                command_id="cmd-" + "b" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert telemetry.paused is False
        assert [action.kind for action in controller.actions] == ["hotkey", "key"]
        assert "Started the paused world" in transition.receipt.message

    asyncio.run(scenario())


def test_continuous_native_handoff_uses_idempotent_speed_key_not_pointer_unpause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        environment.macros = MacroRegistry(
            {
                "approach_confirmed_vendor": MacroConfig(
                    requires_native_assisted=True,
                    movement_pulse_seconds=0.01,
                    movement_pulse_min_seconds=0.005,
                    movement_pulse_max_seconds=0.02,
                    actions=[
                        {
                            "kind": "hotkey",
                            "keys": ["ctrl", "shift", "f10"],
                            "hold_seconds": 0.01,
                        }
                    ],
                ),
                # This is the live profile's current relative-pointer route.
                # Native movement must not spend its pause watchdog walking the
                # cursor to this button after the order has been accepted.
                "unpause_game": MacroConfig(
                    actions=[
                        {
                            "kind": "click",
                            "x": 0.792,
                            "y": 0.723,
                            "space": "normalized",
                            "button": "left",
                        }
                    ]
                ),
            }
        )
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "require_paused_between_actions": False,
                "unpause_skill": "unpause_game",
            }
        )
        monkeypatch.setattr(kenshi_surface, "NATIVE_DIALOGUE_SETTLE_SECONDS", 0.0)
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ApproachDialogueTargetAction(target_id="entity-vendor"),
            command=CommandDispatchContext(
                command_id="cmd-" + "9" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert telemetry.paused is False
        assert [action.kind for action in controller.actions] == ["hotkey", "key"]
        assert controller.actions[-1] == KeyAction(key="f2")
        assert "speed gear 1" in transition.receipt.message

    asyncio.run(scenario())


def test_direction_does_not_adopt_an_active_order_for_another_vector(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        telemetry.capabilities = [
            "game.pause",
            "control.move_in_direction",
            "squad.health",
        ]
        telemetry.sequence = 10
        active_id = "cmd-" + "a" * 32
        telemetry.native_control = NativeControlState(
            available=True,
            active_command_id=active_id,
            acknowledgements=[
                NativeCommandAcknowledgement(
                    command_id=active_id,
                    command="move_in_direction",
                    status=NativeCommandStatus.ACCEPTED,
                    reason="issued",
                    target_id="",
                    bearing_degrees=0.0,
                    distance_units=100.0,
                    selected_character_ids=["entity-selected"],
                    based_on_telemetry_sequence=1,
                    acknowledged_at_telemetry_sequence=2,
                    accepted_at_telemetry_sequence=2,
                )
            ],
        )
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "native_approach_skill": "approach_confirmed_vendor",
                "native_approach_max_seconds": 0.02,
            }
        )
        initial = await environment.reset()

        await execute_operation(
            environment,
            MoveInDirectionAction(
                bearing_degrees=90.0,
                distance_units=250.0,
                expected_effect="walk east",
            ),
            command=CommandDispatchContext(
                command_id="cmd-" + "f" * 32,
                based_on_revision=initial.world_revision,
            ),
        )

        assert controller.request is not None
        assert controller.request.command_id == "cmd-" + "f" * 32
        assert (
            len([action for action in controller.actions if isinstance(action, HotkeyAction)]) == 1
        )

    asyncio.run(scenario())
