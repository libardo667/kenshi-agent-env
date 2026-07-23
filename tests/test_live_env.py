import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.config import CaptureConfig, ControlsConfig, MacroConfig, RuntimeConfig
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.models import (
    ActionReceipt,
    CharacterState,
    ClickAction,
    CommandDispatchContext,
    ControlMode,
    Disposition,
    GameState,
    HotkeyAction,
    KeyAction,
    MouseButton,
    NativeCommandAcknowledgement,
    NativeCommandRequest,
    NativeCommandStatus,
    NativeControlState,
    NearbyEntity,
    PauseAction,
    SkillAction,
    TelemetrySnapshot,
    UIState,
)
from kenshi_agent.skills import MacroRegistry
from kenshi_agent.telemetry import TelemetryRead


class PulseTelemetry:
    def __init__(self, *, auto_pause_after_reads: int | None = None) -> None:
        self.paused = True
        self.sequence = 0
        self.auto_pause_after_reads = auto_pause_after_reads
        self.capabilities: list[str] = []
        self.native_control = NativeControlState()
        self.path = Path("telemetry.json")

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
                game=GameState(loaded=True, paused=self.paused),
                native_control=self.native_control,
            ),
            age_seconds=0.0,
            stale=False,
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
    ) -> None:
        self.telemetry = telemetry
        self.actions: list[PrimitiveInputAction] = []
        self.emergency_after = emergency_after
        self.emergency_checks = 0
        self.user_input_after = user_input_after
        self.user_input_checks = 0
        self.continuous_user_input = continuous_user_input

    def focus_window(self) -> None:
        return None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        self.actions.append(action)
        if isinstance(action, KeyAction) and action.key == "space":
            self.telemetry.paused = not self.telemetry.paused
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

    def client_rect(self) -> WindowRect:
        return WindowRect(left=0, top=0, right=1920, bottom=1080)


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
        ),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        available_skills=["move_visible_terrain"],
        control_mode=control_mode,
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


def test_movement_pulse_unpauses_and_guarantees_repause(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry)
        environment = live_environment(tmp_path, telemetry, controller, movement_registry())

        initial = await environment.reset()
        transition = await environment.step(movement_action())

        assert initial.objective == "Explore nearby."
        assert initial.available_skills == ["move_visible_terrain"]
        assert telemetry.paused is True
        assert transition.observation.telemetry is not None
        assert transition.observation.telemetry.game.paused is True
        assert [action.kind for action in controller.actions] == ["click", "key", "key"]
        assert transition.receipt.primitive_actions == 5
        assert "confirmed re-paused state" in transition.receipt.message

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
        transition = await environment.step(movement_action())

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
        await environment.step(PauseAction(paused=False))
        await environment.step(PauseAction(paused=False))
        await environment.step(PauseAction(paused=True))
        await environment.step(PauseAction(paused=True))

        clicks = [action for action in controller.actions if isinstance(action, ClickAction)]
        assert [(action.x, action.y) for action in clicks] == [(0.792, 0.723), (0.765, 0.723)]
        assert telemetry.paused is True

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

        transition = await environment.step(movement_action(duration_seconds=0.02))

        assert telemetry.paused is True
        assert "Advanced Kenshi for 0.02s" in transition.receipt.message

    asyncio.run(scenario())


def test_movement_pulse_preserves_unexpected_game_auto_pause(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry(auto_pause_after_reads=3)
        controller = PulseController(telemetry)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(pulse_seconds=0.2),
        )
        await environment.reset()

        transition = await environment.step(movement_action())

        assert telemetry.paused is True
        assert [action.kind for action in controller.actions] == ["click", "key"]
        assert "auto-paused" in transition.receipt.message

    asyncio.run(scenario())


def test_emergency_stop_ends_pulse_after_repausing(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry, emergency_after=4)
        environment = live_environment(
            tmp_path,
            telemetry,
            controller,
            movement_registry(pulse_seconds=0.2),
        )
        await environment.reset()

        with pytest.raises(RuntimeError, match="after re-pausing"):
            await environment.step(movement_action())

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

        transition = await environment.step(movement_action())

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
            await environment.step(SkillAction(name="approach_confirmed_vendor"))

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
        native_transition = await native_environment.step(
            SkillAction(name="approach_confirmed_vendor")
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
        ]

    def read(self) -> TelemetryRead:
        self.sequence += 1
        return TelemetryRead(
            snapshot=TelemetrySnapshot(
                protocol_version="0.3.0",
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                identity_session_id="session-native-test",
                capabilities=self.capabilities,
                game=GameState(loaded=True, paused=self.paused),
                ui=UIState(
                    selected_character_id="entity-selected",
                    selected_character_ids=["entity-selected"],
                ),
                native_control=self.native_control,
                squad=[
                    CharacterState(
                        id="entity-selected",
                        name="Wanderer",
                        selected=True,
                    )
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
                    )
                ],
            ),
            age_seconds=0.0,
            stale=False,
            path=self.path,
        )


class NativeAckController(PulseController):
    def __init__(
        self,
        telemetry: NativePulseTelemetry,
        request_path: Path,
        *,
        status: NativeCommandStatus = NativeCommandStatus.ACCEPTED,
        acknowledgement_command_id: str | None = None,
    ) -> None:
        super().__init__(telemetry)
        self.request_path = request_path
        self.status = status
        self.acknowledgement_command_id = acknowledgement_command_id
        self.request_seen_before_hotkey = False
        self.request: NativeCommandRequest | None = None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        if isinstance(action, HotkeyAction):
            assert self.request_path.is_file()
            self.request_seen_before_hotkey = True
            self.request = NativeCommandRequest.model_validate_json(self.request_path.read_bytes())
            request = self.request
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
                            "issued"
                            if self.status == NativeCommandStatus.ACCEPTED
                            else self.status.value
                        ),
                        target_id=request.target_id,
                        selected_character_ids=request.selected_character_ids,
                        based_on_telemetry_sequence=basis,
                        acknowledged_at_telemetry_sequence=acknowledgement_sequence,
                        accepted_at_telemetry_sequence=accepted_sequence,
                        terminal_at_telemetry_sequence=terminal_sequence,
                    )
                ],
            )
        return await super().execute(action)


def native_vendor_environment(
    tmp_path: Path,
    *,
    status: NativeCommandStatus = NativeCommandStatus.ACCEPTED,
    acknowledgement_command_id: str | None = None,
) -> tuple[LiveEnvironment, NativePulseTelemetry, NativeAckController]:
    telemetry_path = tmp_path / "telemetry.latest.json"
    request_path = tmp_path / "native_command.request.json"
    telemetry = NativePulseTelemetry(telemetry_path)
    controller = NativeAckController(
        telemetry,
        request_path,
        status=status,
        acknowledgement_command_id=acknowledgement_command_id,
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

        transition = await environment.dispatch(
            native_vendor_action(),
            command=command,
        )

        assert controller.request_seen_before_hotkey
        assert controller.request is not None
        assert controller.request.command_id == command.command_id
        assert controller.request.based_on_revision == initial.world_revision
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


def test_old_native_ack_cannot_satisfy_new_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            acknowledgement_command_id=("cmd-ffffffffffffffffffffffffffffffff"),
        )
        monkeypatch.setattr(
            environment,
            "_NATIVE_COMMAND_ACK_TIMEOUT_SECONDS",
            0.03,
        )
        monkeypatch.setattr(environment, "_NATIVE_COMMAND_POLL_SECONDS", 0.005)
        initial = await environment.reset()

        with pytest.raises(RuntimeError, match="matching native acknowledgement"):
            await environment.dispatch(
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

        transition = await environment.dispatch(
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
            await environment.dispatch(
                native_vendor_action("entity-replaced"),
                command=CommandDispatchContext(
                    command_id="cmd-0123456789abcdef0123456789abcdef",
                    based_on_revision=initial.world_revision,
                ),
            )

        assert controller.actions == []
        assert telemetry.paused is True

    asyncio.run(scenario())
