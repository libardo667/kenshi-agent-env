import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from operation_test_support import execute_operation

from kenshi_agent.affordances import OperationBindingError
from kenshi_agent.config import CaptureConfig, ControlsConfig, RuntimeConfig
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.core.authority import AuthorizationCode
from kenshi_agent.core.operation import (
    ApproachDialogueTargetAction,
    ClickAction,
    ControlMode,
    ExitCurrentBuildingAction,
    HotkeyAction,
    KeyAction,
    MouseButton,
    MoveInDirectionAction,
    MoveToCharacterAction,
    PauseAction,
    PerformContextAction,
    ProduceResourceOutputAction,
    RegroupWithSquadMemberAction,
    RespondToImmediateThreatAction,
    SelectSquadMemberExactAction,
    SetSpeedAction,
    ThreatResponseStrategy,
    TravelToMapDestinationAction,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    ContextActionKind,
    Disposition,
    GameState,
    InventoryItem,
    KnownMapDestination,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NativeControlState,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    Vec2,
    Vec3,
    VisibleUIControl,
    WorldTarget,
)
from kenshi_agent.core.transport import (
    ActionReceipt,
    CommandDispatchContext,
    NativeCommandRequest,
)
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.execution.handlers import kenshi_surface
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
        self.elapsed_minutes = 0.0

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
                    elapsed_minutes=self.elapsed_minutes,
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


def _authorized_for(observation, action) -> dict[str, object]:
    """Recipient basis kwargs for one action against one observation.

    Native dispatch refuses a command that cannot say who it is for, so a test
    that exercises the wire has to authorize one - which is the point: an
    unauthorized command reaching delivery is the failure being prevented.

    The scope is read from the action's own definition rather than assumed;
    assuming `current_selection` here would authorize an explicit-recipient
    operation against the wrong basis and prove nothing.
    """

    from kenshi_agent.operation_definitions import capture_recipient_basis, definition_for

    definition = definition_for(action)
    assert definition is not None
    basis = capture_recipient_basis(definition, action, observation)
    assert basis is not None
    return {
        "authored_recipient_scope": basis.scope.value,
        "authored_primary": basis.primary,
        "authored_selection": list(basis.selection),
        "authored_explicit_recipients": list(basis.explicit_recipients),
    }


def live_environment(
    tmp_path: Path,
    telemetry: PulseTelemetry,
    controller: PulseController,
    *,
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
        runtime_config=RuntimeConfig(settle_seconds=0.0, objective="Explore nearby."),
        controls_config=ControlsConfig(
            post_input_delay_seconds=0.0,
            # A real live movement config declares its calibrated client size;
            # the default PulseController renders at this exact size.
            calibrated_client_width=1920,
            calibrated_client_height=1080,
        ),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        control_mode=control_mode,
        quicksave_dir=quicksave_dir,
        quicksave_timeout_seconds=quicksave_timeout_seconds,
        quicksave_stable_seconds=quicksave_stable_seconds,
    )







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
        )

        current = await environment.reset()

        assert "human_input_detected" in current.events
        assert "emergency_stop_detected" in current.events

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
    environment = LiveEnvironment(
        run_id="native-command-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        runtime_config=RuntimeConfig(settle_seconds=0.0),
        controls_config=ControlsConfig(
            post_input_delay_seconds=0.0,
            native_movement_pulse_seconds=0.01,
            # The continuation budget, not the pulse, is what a test with a
            # controller that never reaches a terminal actually spends: 30
            # seconds of it at a 0.01s pulse is 6000 sleeps and was a quarter of
            # the whole suite's runtime in one test.
            native_approach_max_seconds=0.2,
        ),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        control_mode=ControlMode.NATIVE_ASSISTED,
    )
    return environment, telemetry, controller


def native_vendor_action(target_id: str = "entity-vendor") -> ApproachDialogueTargetAction:
    return ApproachDialogueTargetAction(target_id=target_id)



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
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            SelectSquadMemberExactAction(target_id="entity-ruka"),
            command=CommandDispatchContext(
                command_id="cmd-" + "f" * 32,
                based_on_revision=initial.world_revision,
                **_authorized_for(initial, SelectSquadMemberExactAction(target_id="entity-ruka")),
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
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            SelectSquadMemberExactAction(target_id="entity-ruka"),
            command=CommandDispatchContext(
                command_id="cmd-" + "a" * 32,
                based_on_revision=initial.world_revision,
                **_authorized_for(initial, SelectSquadMemberExactAction(target_id="entity-ruka")),
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
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            MoveToCharacterAction(target_id="entity-vendor"),
            command=CommandDispatchContext(
                command_id="cmd-" + "b" * 32,
                based_on_revision=initial.world_revision,
                **_authorized_for(initial, MoveToCharacterAction(target_id="entity-vendor")),
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



def test_native_vendor_request_precedes_hotkey_and_matching_later_ack(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            open_dialogue_on_hotkey=True,
        )
        initial = await environment.reset()
        command = CommandDispatchContext(
            command_id="cmd-0123456789abcdef0123456789abcdef",
            based_on_revision=initial.world_revision,
            **_authorized_for(initial, native_vendor_action()),
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
        assert [action.kind for action in controller.actions] == ["hotkey"]
        assert telemetry.paused is True
        assert transition.receipt.accepted
        assert transition.receipt.executed
        assert transition.receipt.command_id == command.command_id
        assert transition.receipt.causal_revision_advanced is True
        assert transition.receipt.native_acknowledgement is not None
        assert transition.receipt.native_acknowledgement.command_id == command.command_id
        assert "acknowledgement 'accepted'" in transition.receipt.message
        assert "opened dialogue with the exact native target" in transition.receipt.message

    asyncio.run(scenario())


def test_native_vendor_dispatch_accepts_same_telemetry_without_capture_basis(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, _, controller = native_vendor_environment(
            tmp_path,
            open_dialogue_on_hotkey=True,
        )
        initial = await environment.reset()
        command = CommandDispatchContext(
            command_id="cmd-0123456789abcdef0123456789abcdef",
            based_on_revision=initial.world_revision.model_copy(
                update={
                    "frame_sequence": 7,
                    "observed_at_monotonic": (initial.world_revision.observed_at_monotonic + 1.0),
                }
            ),
            **_authorized_for(initial, native_vendor_action()),
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
        environment, _, controller = native_vendor_environment(
            tmp_path,
            open_dialogue_on_hotkey=True,
        )
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
                **_authorized_for(initial, native_vendor_action()),
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
                    **_authorized_for(initial, native_vendor_action()),
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
            **_authorized_for(initial, native_vendor_action("entity-replaced")),
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

        with pytest.raises(
            OperationBindingError,
            match="current valid dialogue target",
        ):
            await execute_operation(
                environment,
                native_vendor_action("entity-replaced"),
                command=CommandDispatchContext(
                    command_id="cmd-0123456789abcdef0123456789abcdef",
                    based_on_revision=initial.world_revision,
                    **_authorized_for(initial, native_vendor_action("entity-replaced")),
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
    )
    return environment, controller







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
                **_authorized_for(initial, ApproachDialogueTargetAction(target_id="entity-vendor")),
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
                **_authorized_for(initial, ApproachDialogueTargetAction(target_id="entity-vendor")),
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
                **_authorized_for(
                    initial,
                    PerformContextAction(
                        target_id="entity-copper",
                        context_action=ContextActionKind.OPERATE,
                    ),
                ),
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
                **_authorized_for(
                    initial,
                    PerformContextAction(
                        target_id="entity-copper",
                        context_action=ContextActionKind.OPERATE,
                    ),
                ),
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
                **_authorized_for(
                    initial,
                    PerformContextAction(
                        target_id="entity-ruka",
                        context_action=ContextActionKind("first_aid"),
                    ),
                ),
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
        initial = await environment.reset()

        transition = await execute_operation(
            environment,
            ProduceResourceOutputAction(target_id="entity-copper"),
            command=CommandDispatchContext(
                command_id="cmd-" + "3" * 32,
                based_on_revision=initial.world_revision,
                **_authorized_for(initial, ProduceResourceOutputAction(target_id="entity-copper")),
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



def test_visible_nearby_dialogue_target_still_uses_native_talk_order(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(tmp_path)
        environment.controls_config = environment.controls_config.model_copy(
            update={
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
                **_authorized_for(initial, ApproachDialogueTargetAction(target_id="entity-vendor")),
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
                **_authorized_for(initial, ApproachDialogueTargetAction(target_id="entity-vendor")),
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
                **_authorized_for(
                    initial,
                    MoveInDirectionAction(
                        bearing_degrees=90.0,
                        distance_units=250.0,
                        expected_effect="leave the current building",
                    ),
                ),
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
                **_authorized_for(
                    initial,
                    TravelToMapDestinationAction(
                        destination_id="entity-known-town",
                    ),
                ),
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
                **_authorized_for(
                    initial, TravelToMapDestinationAction(destination_id="entity-known-town")
                ),
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
                **_authorized_for(
                    initial,
                    RegroupWithSquadMemberAction(
                        actor_id="entity-selected",
                        target_id="entity-ruka",
                    ),
                ),
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
                **_authorized_for(
                    initial, TravelToMapDestinationAction(destination_id="entity-known-town")
                ),
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
                **_authorized_for(initial, ExitCurrentBuildingAction()),
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
                    **_authorized_for(later, ExitCurrentBuildingAction()),
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
                **_authorized_for(initial, ExitCurrentBuildingAction()),
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
        environment.controls_config = environment.controls_config.model_copy(
            update={
                "require_paused_between_actions": False,
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
                **_authorized_for(initial, ApproachDialogueTargetAction(target_id="entity-vendor")),
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
                **_authorized_for(
                    initial,
                    MoveInDirectionAction(
                        bearing_degrees=90.0,
                        distance_units=250.0,
                        expected_effect="walk east",
                    ),
                ),
            ),
        )

        assert controller.request is not None
        assert controller.request.command_id == "cmd-" + "f" * 32
        assert (
            len([action for action in controller.actions if isinstance(action, HotkeyAction)]) == 1
        )

    asyncio.run(scenario())


def test_two_selected_characters_can_be_ordered_to_mine(tmp_path: Path) -> None:
    """A broke-pair start could not mine, and the fix was in two places.

    Option preparation carried a private singleton rule, and so did the request
    builder - keyed there on a hardcoded wire command name set that treated
    `perform_context_action` as singleton-only while its contract declares
    CURRENT_SELECTION. Fixing only the first moved the refusal one layer down
    and reworded it. This proves the order reaches the wire carrying both
    characters, which is what Kenshi's selection-based ordering API expects.
    """

    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
        )
        telemetry.selected_character_ids = ["entity-selected", "entity-ruka"]
        initial = await environment.reset()
        action = PerformContextAction(
            target_id="entity-copper",
            context_action=ContextActionKind.OPERATE,
        )

        await execute_operation(
            environment,
            action,
            command=CommandDispatchContext(
                command_id="cmd-" + "7" * 32,
                based_on_revision=initial.world_revision,
                **_authorized_for(initial, action),
            ),
        )

        assert controller.request is not None
        assert controller.request.command == "perform_context_action"
        assert sorted(controller.request.selected_character_ids) == [
            "entity-ruka",
            "entity-selected",
        ]

    asyncio.run(scenario())


def test_a_recipient_change_during_the_lease_writes_no_request(tmp_path: Path) -> None:
    """The seam, at the layer that forms the bytes.

    The command is authorized for one pair and dispatched after selection has
    become somebody else. Nothing may reach the game: not the request file, not
    the hotkey that tells the plug-in to read it.
    """

    async def scenario() -> None:
        environment, telemetry, controller = native_vendor_environment(
            tmp_path,
            status=NativeCommandStatus.COMPLETED,
        )
        telemetry.selected_character_ids = ["entity-selected", "entity-ruka"]
        initial = await environment.reset()
        action = PerformContextAction(
            target_id="entity-copper",
            context_action=ContextActionKind.OPERATE,
        )
        authorized = _authorized_for(initial, action)

        # The lease wait happens here. One of the two authorized recipients is
        # deselected, so the order would now command one character instead of
        # the pair it was authored for.
        telemetry.selected_character_ids = ["entity-selected"]

        with pytest.raises(RuntimeError, match="different recipients"):
            await execute_operation(
                environment,
                action,
                command=CommandDispatchContext(
                    command_id="cmd-" + "8" * 32,
                    based_on_revision=initial.world_revision,
                    **authorized,
                ),
            )

        assert controller.request is None
        assert controller.actions == []

    asyncio.run(scenario())
