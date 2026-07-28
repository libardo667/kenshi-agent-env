import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent import live_dev
from kenshi_agent.config import ControlsConfig, load_config
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.live_dev import (
    MYGUI_CLICK_HOLD_SECONDS,
    LaunchFailed,
    LaunchInterrupted,
    _click,
    _click_semantic_control,
    _disable_re_kenshi_startup_panel,
    _ensure_interrupted_safe_state,
    _journey_argv,
    _observe_loaded_paused_health,
    _open_exact_authored_game_start,
    _open_exact_scenario_save,
    _plugin_ready,
    _steam_connection_state,
    _telemetry_payload,
    _unique_visible_control,
    _validate_calibrated_client_rect,
    _validate_launch_preconditions,
    _validate_resumable_launcher_rect,
    _validate_safe_close_snapshot,
    _wait_until,
)
from kenshi_agent.models import (
    ActionReceipt,
    CharacterState,
    ClickAction,
    GameState,
    HotkeyAction,
    KeyAction,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    Vec3,
    VisibleUIControl,
    WorldTarget,
)
from kenshi_agent.telemetry import TelemetryRead


class LaunchController(InputController):
    def __init__(
        self,
        *,
        rect: WindowRect | None = None,
        human_input: bool = False,
        interrupt_inside_lease: bool = False,
        title: str | None = None,
        visible_titles: list[str] | None = None,
    ) -> None:
        self.rect = rect or WindowRect(0, 0, 1920, 1080)
        self.human_input = human_input
        self.interrupt_inside_lease = interrupt_inside_lease
        self.actions: list[PrimitiveInputAction] = []
        self.safety_actions: list[PrimitiveInputAction] = []
        self.lease_entries = 0
        self.title = title
        self.visible_titles = visible_titles
        self.close_requested = False

    @asynccontextmanager
    async def input_lease(self, *, alt_tab_on_restore: bool = False):
        del alt_tab_on_restore
        self.lease_entries += 1
        if self.interrupt_inside_lease:
            self.human_input = True
        yield

    def focus_window(self) -> None:
        return None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        self.actions.append(action)
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
        )

    async def execute_safety(self, action: PrimitiveInputAction) -> ActionReceipt:
        self.safety_actions.append(action)
        return ActionReceipt(
            action=action,
            accepted=True,
            executed=True,
            dry_run=False,
        )

    def emergency_stop_pressed(self, key: str) -> bool:
        del key
        return False

    def continuous_user_input_detected(self) -> bool:
        return self.human_input

    def target_window_title(self) -> str | None:
        return self.title

    def visible_window_titles(self) -> list[str]:
        if self.visible_titles is not None:
            return self.visible_titles
        return super().visible_window_titles()

    def client_rect(self) -> WindowRect:
        return self.rect

    def request_close(self) -> None:
        self.close_requested = True


def test_safe_close_requires_fresh_paused_idle_telemetry() -> None:
    observed_at = datetime(2026, 7, 27, tzinfo=UTC)
    payload = {
        "captured_at": observed_at.isoformat(),
        "game": {"loaded": True, "paused": True},
        "ui": {
            "active_screen": "world",
            "modal_open": False,
            "dialogue_open": False,
        },
        "native_control": {"active_command_id": None},
    }

    _validate_safe_close_snapshot(
        payload,
        max_age_seconds=3.0,
        now=observed_at,
    )

    for path, value in (
        (("game", "loaded"), False),
        (("game", "paused"), False),
        (("native_control", "active_command_id"), "command-active"),
        (("ui", "modal_open"), True),
        (("ui", "dialogue_open"), True),
    ):
        unsafe = json.loads(json.dumps(payload))
        unsafe[path[0]][path[1]] = value
        with pytest.raises(LaunchFailed):
            _validate_safe_close_snapshot(
                unsafe,
                max_age_seconds=3.0,
                now=observed_at,
            )

    stale = json.loads(json.dumps(payload))
    stale["captured_at"] = "2026-07-26T23:59:50+00:00"
    with pytest.raises(LaunchFailed, match="no older than"):
        _validate_safe_close_snapshot(
            stale,
            max_age_seconds=3.0,
            now=observed_at,
        )

    title = json.loads(json.dumps(payload))
    title["game"] = {"loaded": False, "paused": False}
    title["ui"]["active_screen"] = "title"
    assert (
        _validate_safe_close_snapshot(
            title,
            max_age_seconds=3.0,
            now=observed_at,
        )
        == "title"
    )

    title["ui"]["modal_open"] = True
    assert (
        _validate_safe_close_snapshot(
            title,
            max_age_seconds=3.0,
            now=observed_at,
        )
        == "title"
    )

    del title["ui"]["modal_open"]
    del title["ui"]["dialogue_open"]
    assert (
        _validate_safe_close_snapshot(
            title,
            max_age_seconds=3.0,
            now=observed_at,
        )
        == "title"
    )

    title["ui"]["active_screen"] = "loading"
    with pytest.raises(LaunchFailed, match="loaded paused world or the title screen"):
        _validate_safe_close_snapshot(
            title,
            max_age_seconds=3.0,
            now=observed_at,
        )


def test_supported_close_pauses_and_confirms_before_requesting_wm_close() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "live.longform.yaml")
        controller = LaunchController()

        def idle_snapshot(sequence: int, *, paused: bool) -> TelemetrySnapshot:
            return launch_snapshot(sequence, paused=paused).model_copy(
                update={
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                    )
                }
            )

        telemetry = LaunchTelemetry(
            idle_snapshot(40, paused=False),
            idle_snapshot(40, paused=False),
            idle_snapshot(41, paused=True),
            idle_snapshot(41, paused=True),
        )

        await live_dev._close_kenshi_safely(
            config,
            controller,
            telemetry,
            timeout_seconds=0.1,
            process_names=lambda: (
                set() if controller.close_requested else {"kenshi_x64.exe"}
            ),
        )

        assert controller.safety_actions == [KeyAction(key=config.controls.pause_key)]
        assert controller.close_requested is True

    import asyncio

    asyncio.run(scenario())


def test_supported_close_never_closes_an_unresolved_modal() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "live.longform.yaml")
        controller = LaunchController()
        modal = launch_snapshot(51, paused=True).model_copy(
            update={
                "ui": UIState(
                    active_screen="inventory",
                    modal_open=True,
                    dialogue_open=False,
                )
            }
        )

        with pytest.raises(LaunchFailed, match="modal or dialogue"):
            await live_dev._close_kenshi_safely(
                config,
                controller,
                LaunchTelemetry(modal),
                timeout_seconds=0.1,
                process_names=lambda: {"kenshi_x64.exe"},
            )

        assert controller.safety_actions == []
        assert controller.close_requested is False

    import asyncio

    asyncio.run(scenario())


def test_supported_close_dismisses_exact_resource_inventory_before_wm_close() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "live.longform.yaml")
        controller = LaunchController()
        resource_inventory = resource_inventory_snapshot(60)
        world = launch_snapshot(61, paused=True).model_copy(
            update={
                "ui": UIState(
                    active_screen="world",
                    modal_open=False,
                    dialogue_open=False,
                    open_inventory_windows=0,
                    visible_controls_complete=True,
                    visible_controls=[],
                )
            }
        )
        telemetry = LaunchTelemetry(
            resource_inventory,
            resource_inventory,
            resource_inventory,
            world,
            world,
        )

        await live_dev._close_kenshi_safely(
            config,
            controller,
            telemetry,
            timeout_seconds=0.1,
            process_names=lambda: (
                set() if controller.close_requested else {"kenshi_x64.exe"}
            ),
        )

        assert controller.actions == [
            ClickAction(
                x=0.488,
                y=0.311,
                hold_seconds=live_dev.MYGUI_CLICK_HOLD_SECONDS,
            )
        ]
        assert controller.close_requested is True

    import asyncio

    asyncio.run(scenario())


def test_supported_close_never_dismisses_an_incomplete_inventory_layout() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "live.longform.yaml")
        controller = LaunchController()
        incomplete = resource_inventory_snapshot(70).model_copy(
            update={
                "ui": resource_inventory_snapshot(70).ui.model_copy(
                    update={"visible_controls_complete": False}
                )
            },
            deep=True,
        )

        with pytest.raises(LaunchFailed, match="modal or dialogue"):
            await live_dev._close_kenshi_safely(
                config,
                controller,
                LaunchTelemetry(incomplete),
                timeout_seconds=0.1,
                process_names=lambda: {"kenshi_x64.exe"},
            )

        assert controller.actions == []
        assert controller.close_requested is False

    import asyncio

    asyncio.run(scenario())


def test_supported_close_dismisses_source_and_destination_before_wm_close() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "live.longform.yaml")
        controller = LaunchController()
        both = resource_inventory_snapshot(80, destination_open=True)
        destination = resource_inventory_snapshot(
            81,
            source_open=False,
            destination_open=True,
        )
        world = launch_snapshot(82, paused=True).model_copy(
            update={
                "ui": UIState(
                    active_screen="world",
                    modal_open=False,
                    dialogue_open=False,
                    open_inventory_windows=0,
                    visible_controls_complete=True,
                    visible_controls=[],
                )
            }
        )
        telemetry = LaunchTelemetry(
            both,
            both,
            both,
            destination,
            destination,
            world,
        )

        await live_dev._close_kenshi_safely(
            config,
            controller,
            telemetry,
            timeout_seconds=0.1,
            process_names=lambda: (
                set() if controller.close_requested else {"kenshi_x64.exe"}
            ),
        )

        assert len(controller.actions) == 2
        assert all(
            isinstance(action, ClickAction) for action in controller.actions
        )
        clicks = [
            action for action in controller.actions if isinstance(action, ClickAction)
        ]
        assert (clicks[0].x, clicks[0].y) == pytest.approx((0.488, 0.311))
        assert (clicks[1].x, clicks[1].y) == pytest.approx((0.888, 0.211))
        assert controller.close_requested is True

    import asyncio

    asyncio.run(scenario())


class LaunchTelemetry:
    def __init__(self, *snapshots: TelemetrySnapshot) -> None:
        self.snapshots = list(snapshots)
        self.index = 0

    def read(self) -> TelemetryRead:
        snapshot = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return TelemetryRead(
            snapshot=snapshot,
            age_seconds=0.0,
            stale=False,
            path=Path("telemetry.latest.json"),
        )


def launch_snapshot(sequence: int, *, paused: bool) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=sequence,
        captured_at=datetime.now(UTC),
        capabilities=["game.pause"],
        game=GameState(loaded=True, paused=paused),
        squad=[CharacterState(id="entity-hep", name="Hep", selected=True)],
    )


def resource_inventory_snapshot(
    sequence: int,
    *,
    source_open: bool = True,
    destination_open: bool = False,
) -> TelemetrySnapshot:
    source_bounds = NormalizedPointerBounds(
        min_x=0.2,
        max_x=0.5,
        min_y=0.3,
        max_y=0.7,
    )
    destination_bounds = NormalizedPointerBounds(
        min_x=0.6,
        max_x=0.9,
        min_y=0.2,
        max_y=0.8,
    )
    controls = []
    if source_open:
        controls.extend(
            [
                VisibleUIControl(
                    label="IRON RESOURCE",
                    role="text",
                    window="IRON RESOURCE",
                    bounds=source_bounds,
                ),
                VisibleUIControl(
                    label="Raw Iron",
                    role="item",
                    window="IRON RESOURCE",
                    item_name="Raw Iron",
                    item_quantity=1,
                    section="out",
                    bounds=NormalizedPointerBounds(
                        min_x=0.25,
                        max_x=0.3,
                        min_y=0.4,
                        max_y=0.45,
                    ),
                ),
            ]
        )
    if destination_open:
        controls.append(
            VisibleUIControl(
                label="HEP",
                role="text",
                window="HEP",
                bounds=destination_bounds,
            )
        )
    open_count = int(source_open) + int(destination_open)
    return TelemetrySnapshot(
        sequence=sequence,
        captured_at=datetime.now(UTC),
        capabilities=[
            "identity.stable_handles",
            "ui.context_inventory_target",
            "ui.inventory",
            "ui.visible_controls",
            "world.context_targets",
        ],
        identity_session_id="session-close",
        game=GameState(loaded=True, paused=True),
        active_shop_trader_count=0,
        ui=UIState(
            active_screen="trade" if open_count == 2 else "inventory",
            modal_open=open_count > 0,
            dialogue_open=False,
            open_inventory_windows=open_count,
            context_inventory_target_id=(
                "entity-iron" if source_open else None
            ),
            visible_controls_complete=True,
            selected_character_id="entity-hep",
            selected_character_ids=["entity-hep"],
            visible_controls=controls,
        ),
        squad=[
            CharacterState(
                id="entity-hep",
                name="Hep",
                selected=True,
            )
        ],
        world_targets=[
            WorldTarget(
                id="entity-iron",
                name="Iron Resource",
                kind="natural_resource",
                position=Vec3(x=0, y=0, z=0),
                distance=0,
                default_task="operate_machinery",
            )
        ],
    )


def semantic_snapshot(
    sequence: int,
    *,
    label: str,
    bounds: NormalizedPointerBounds | None = None,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=sequence,
        capabilities=["ui.visible_controls"],
        ui=UIState(
            visible_controls=[
                VisibleUIControl(
                    label=label,
                    role="button",
                    bounds=bounds
                    or NormalizedPointerBounds(
                        min_x=0.2,
                        max_x=0.4,
                        min_y=0.1,
                        max_y=0.2,
                    ),
                )
            ]
        ),
    )


def carousel_snapshot(
    sequence: int,
    *,
    label: str,
    duplicate_left: bool = False,
) -> TelemetrySnapshot:
    left = VisibleUIControl(
        label="session_LeftButton",
        role="button",
        bounds=NormalizedPointerBounds(
            min_x=0.55,
            max_x=0.57,
            min_y=0.08,
            max_y=0.13,
        ),
    )
    controls = [
        left,
        *([left.model_copy(deep=True)] if duplicate_left else []),
        VisibleUIControl(
            label="session_RightButton",
            role="button",
            bounds=NormalizedPointerBounds(
                min_x=0.73,
                max_x=0.75,
                min_y=0.08,
                max_y=0.13,
            ),
        ),
        VisibleUIControl(
            label=label,
            role="text",
            bounds=NormalizedPointerBounds(
                min_x=0.57,
                max_x=0.73,
                min_y=0.078,
                max_y=0.134,
            ),
        ),
    ]
    return TelemetrySnapshot(
        sequence=sequence,
        capabilities=["ui.visible_controls"],
        ui=UIState(visible_controls=controls),
    )


def test_launch_click_aborts_before_lease_when_human_input_is_detected() -> None:
    async def scenario() -> None:
        controller = LaunchController(human_input=True)

        with pytest.raises(LaunchInterrupted, match="human input"):
            await _click(controller, 0.3, 0.1)

        assert controller.lease_entries == 0
        assert controller.actions == []

    import asyncio

    asyncio.run(scenario())


def test_launch_click_aborts_inside_lease_without_emitting_input() -> None:
    async def scenario() -> None:
        controller = LaunchController(interrupt_inside_lease=True)

        with pytest.raises(LaunchInterrupted, match="human input"):
            await _click(controller, 0.3, 0.1)

        assert controller.lease_entries == 1
        assert controller.actions == []

    import asyncio

    asyncio.run(scenario())


def test_launcher_wait_fails_immediately_on_crash_reporter() -> None:
    async def scenario() -> None:
        controller = LaunchController(title="RE_Kenshi Crash Reporter")

        with pytest.raises(LaunchFailed, match="Crash Reporter"):
            await _wait_until(
                lambda: False,
                10.0,
                "anything",
                controller=controller,
            )

    import asyncio

    asyncio.run(scenario())


def test_launcher_wait_fails_immediately_on_kenshi_has_crashed_window() -> None:
    async def scenario() -> None:
        controller = LaunchController(title="Kenshi has crashed")

        with pytest.raises(LaunchFailed, match="Kenshi has crashed"):
            await _wait_until(
                lambda: False,
                10.0,
                "anything",
                controller=controller,
            )

    import asyncio

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal_title", ["BAD STUFF", "Steam DLL Error"])
def test_launcher_wait_detects_unfiltered_terminal_dialog(
    terminal_title: str,
) -> None:
    async def scenario() -> None:
        controller = LaunchController(
            title="Kenshi 1.0.65 - x64",
            visible_titles=["Kenshi 1.0.65 - x64", terminal_title],
        )

        with pytest.raises(LaunchFailed, match=terminal_title):
            await _wait_until(
                lambda: False,
                10.0,
                "anything",
                controller=controller,
            )

    import asyncio

    asyncio.run(scenario())


def test_plugin_ready_fails_immediately_on_fresh_native_error(tmp_path: Path) -> None:
    launched_at = datetime.now(UTC)
    status = tmp_path / "plugin_status.json"
    status.write_text(
        json.dumps(
            {
                "state": "error",
                "message": "MyGUI instance unavailable",
                "captured_at": launched_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LaunchFailed, match="MyGUI instance unavailable"):
        _plugin_ready(status, launched_at)


def test_calibrated_client_rect_rejects_resolution_change() -> None:
    controls = ControlsConfig(
        calibrated_client_width=1920,
        calibrated_client_height=1080,
    )

    with pytest.raises(RuntimeError, match=r"1280x720.*1920x1080"):
        _validate_calibrated_client_rect(
            WindowRect(0, 0, 1280, 720),
            controls,
        )


def test_calibrated_client_rect_accepts_exact_size() -> None:
    controls = ControlsConfig(
        calibrated_client_width=1920,
        calibrated_client_height=1080,
    )

    _validate_calibrated_client_rect(
        WindowRect(0, 0, 1920, 1080),
        controls,
    )


def test_launcher_controller_forces_polite_restoring_input_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_controller(title: str, **kwargs: object) -> object:
        captured["title"] = title
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(live_dev, "Win32InputController", fake_controller)
    config = load_config(Path(__file__).resolve().parents[1] / "config" / "live.burnin.yaml")

    assert live_dev._controller(config) is sentinel
    assert captured["polite_input_enabled"] is True
    assert captured["idle_seconds_before_input"] == 0.0
    assert captured["max_wait_for_input_turn_seconds"] == 1.0
    assert captured["restore_foreground_after_input"] is True
    assert captured["restore_cursor_after_input"] is True
    assert captured["alt_tab_after_input"] is False


def test_interrupted_loaded_game_gets_one_causally_confirmed_safety_pause() -> None:
    async def scenario() -> None:
        controller = LaunchController(human_input=True)
        reader = LaunchTelemetry(
            launch_snapshot(10, paused=False),
            launch_snapshot(11, paused=True),
        )

        outcome = await _ensure_interrupted_safe_state(
            controller,
            reader,  # type: ignore[arg-type]
            pause_key="space",
            timeout_seconds=0.2,
        )

        assert outcome == "Confirmed paused at telemetry sequence 11."
        assert controller.actions == []
        assert controller.safety_actions == [KeyAction(key="space")]
        assert controller.lease_entries == 1

    import asyncio

    asyncio.run(scenario())


def test_interrupted_already_paused_game_emits_no_cleanup_input() -> None:
    async def scenario() -> None:
        controller = LaunchController(human_input=True)
        reader = LaunchTelemetry(launch_snapshot(20, paused=True))

        outcome = await _ensure_interrupted_safe_state(
            controller,
            reader,  # type: ignore[arg-type]
            pause_key="space",
            timeout_seconds=0.2,
        )

        assert outcome == "Already confirmed paused at telemetry sequence 20."
        assert controller.actions == []
        assert controller.safety_actions == []
        assert controller.lease_entries == 0

    import asyncio

    asyncio.run(scenario())


def test_re_kenshi_startup_panel_is_disabled_with_one_backup(tmp_path: Path) -> None:
    settings = tmp_path / "RE_Kenshi.ini"
    settings.write_text(
        json.dumps({"OpenSettingOnStart": True, "CacheShaders": True}),
        encoding="utf-8",
    )

    assert _disable_re_kenshi_startup_panel(settings) is True
    assert _disable_re_kenshi_startup_panel(settings) is False
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "OpenSettingOnStart": False,
        "CacheShaders": True,
    }
    backup = tmp_path / "RE_Kenshi.ini.kenshi-agent.bak"
    assert json.loads(backup.read_text(encoding="utf-8"))[
        "OpenSettingOnStart"
    ] is True


def test_steam_connection_state_uses_latest_explicit_transition(tmp_path: Path) -> None:
    log = tmp_path / "connection_log.txt"
    log.write_text(
        "[2026-07-23 16:45:00] [Logged On, 4, 7] message\n"
        "[2026-07-23 16:45:01] unrelated detail\n"
        "[2026-07-23 16:45:02] [Logged Off, 0, 0] disconnected\n",
        encoding="utf-8",
    )

    assert _steam_connection_state(log) == "Logged Off"


def test_launch_preflight_accepts_logged_on_exact_profile_and_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.burnin.yaml")
    assert config.launch.graphics_profile_file is not None
    loaded_profile = live_dev.load_graphics_profile(
        config.launch.graphics_profile_file
    )
    settings = tmp_path / "settings.cfg"
    settings.write_text(
        "".join(f"{key}={value}\n" for key, value in loaded_profile.settings.items()),
        encoding="utf-8",
    )
    assert loaded_profile.renderer is not None
    renderer = tmp_path / "kenshi.cfg"
    renderer.write_text(
        f"[{loaded_profile.renderer.section}]\n"
        + "".join(
            f"{key}={value}\n"
            for key, value in loaded_profile.renderer.settings.items()
        ),
        encoding="utf-8",
    )
    steam_log = tmp_path / "connection_log.txt"
    steam_log.write_text(
        "[2026-07-23 16:53:46] [Logged On, 4, 7] ready\n",
        encoding="utf-8",
    )

    _validate_launch_preconditions(
        config,
        process_names={"steam.exe"},
        available_physical_memory_mib=8192,
        settings_path=settings,
        renderer_path=renderer,
        steam_connection_log_path=steam_log,
    )


def test_launch_preflight_rejects_steam_process_that_is_not_logged_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.burnin.yaml")
    steam_log = tmp_path / "connection_log.txt"
    steam_log.write_text(
        "[2026-07-23 16:45:00] [Logged Off, 4, 0] Logged In Elsewhere\n",
        encoding="utf-8",
    )

    with pytest.raises(LaunchFailed, match=r"Logged Off.*not 'Logged On'"):
        _validate_launch_preconditions(
            config,
            process_names={"steam.exe"},
            available_physical_memory_mib=8192,
            steam_connection_log_path=steam_log,
        )


def test_launch_preflight_rejects_second_kenshi_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.burnin.yaml")

    with pytest.raises(LaunchFailed, match="already running"):
        _validate_launch_preconditions(
            config,
            process_names={"steam.exe", "kenshi_x64.exe"},
            available_physical_memory_mib=8192,
        )


def test_non_launching_preflight_can_validate_an_existing_kenshi_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.burnin.yaml")
    config = config.model_copy(
        update={
            "launch": config.launch.model_copy(
                update={"require_graphics_profile": False}
            )
        }
    )
    steam_log = tmp_path / "connection_log.txt"
    steam_log.write_text(
        "[2026-07-23 16:53:46] [Logged On, 4, 7] ready\n",
        encoding="utf-8",
    )

    _validate_launch_preconditions(
        config,
        process_names={"steam.exe", "kenshi_x64.exe"},
        available_physical_memory_mib=8192,
        steam_connection_log_path=steam_log,
        allow_existing_client=True,
    )


def test_resume_launcher_requires_the_exact_small_pre_game_window() -> None:
    _validate_resumable_launcher_rect(WindowRect(0, 0, 900, 700))

    with pytest.raises(LaunchFailed, match="exact small RE_Kenshi"):
        _validate_resumable_launcher_rect(WindowRect(0, 0, 1920, 1080))


def test_launch_preflight_prioritizes_terminal_crash_over_duplicate_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.burnin.yaml")

    with pytest.raises(
        LaunchFailed,
        match=r"RE_Kenshi Crash Reporter.*\./dev crash",
    ):
        _validate_launch_preconditions(
            config,
            process_names={"steam.exe", "kenshi_x64.exe"},
            terminal_window_title="RE_Kenshi Crash Reporter",
        )


def test_crash_evidence_archives_latest_dump_logs_telemetry_and_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    local_app_data = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    config = load_config(root / "config" / "live.burnin.yaml")
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir()
    telemetry_file = telemetry_dir / "telemetry.latest.json"
    telemetry_file.write_text('{"sequence": 44}\n', encoding="utf-8")
    (telemetry_dir / "plugin_status.json").write_text(
        '{"state": "ready"}\n',
        encoding="utf-8",
    )
    evidence_root = tmp_path / "runs"
    config = config.model_copy(
        update={
            "paths": config.paths.model_copy(update={"runs_dir": evidence_root}),
            "telemetry": config.telemetry.model_copy(update={"file": telemetry_file}),
        }
    )
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    settings = game_dir / "settings.cfg"
    settings.write_text("view distance=1500\n", encoding="utf-8")
    (game_dir / "RE_Kenshi_log.txt").write_text(
        "Crash detected\n",
        encoding="utf-8",
    )
    (game_dir / "kenshi.log").write_text(
        "DXGI_ERROR_DEVICE_REMOVED\n",
        encoding="utf-8",
    )
    older_dump = game_dir / "crashDump-old.zip"
    older_dump.write_bytes(b"old")
    newest_dump = game_dir / "crashDump-new.zip"
    newest_dump.write_bytes(b"new")
    older_dump.touch()
    newest_dump.touch()
    older_dump_mtime = older_dump.stat().st_mtime - 60
    os.utime(older_dump, (older_dump_mtime, older_dump_mtime))
    monkeypatch.setattr(live_dev, "_kenshi_settings_path", lambda: settings)

    class FakeCapture:
        def __init__(self, controller: object, run_dir: Path, **_: object) -> None:
            del controller
            self.run_dir = run_dir

        def capture(self, step_index: int) -> object:
            path = self.run_dir / f"live_frame_{step_index:06d}.png"
            path.write_bytes(b"frame")
            return type("Frame", (), {"path": path})()

    monkeypatch.setattr(live_dev, "WindowCapture", FakeCapture)
    captured_at = datetime(2026, 7, 27, 2, 2, 0, tzinfo=UTC)

    evidence_dir = live_dev._collect_crash_evidence(
        config,
        LaunchController(title="RE_Kenshi Crash Reporter"),
        terminal_window_title="RE_Kenshi Crash Reporter",
        captured_at=captured_at,
        gpu_tdr_events=(
            live_dev.GpuTdrEvent(
                record_id=9798,
                observed_at=captured_at,
                bucket="LKD_0x141_Tdr:6_IMAGE_igdkmdn64.sys_GEN12LP_DX10_BBHANG",
                watchdog_dump=(
                    r"\\?\C:\WINDOWS\LiveKernelReports\WATCHDOG"
                    r"\WATCHDOG-20260726-1901.dmp"
                ),
            ),
        ),
    )

    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    artifact_names = {artifact["name"] for artifact in manifest["artifacts"]}
    assert evidence_dir.parent == evidence_root / "crashes"
    assert {
        "RE_Kenshi_log.txt",
        "kenshi.log",
        "crashDump-new.zip",
        "telemetry.latest.json",
        "plugin_status.json",
        "windows_gpu_events.json",
        "live_frame_000000.png",
    } <= artifact_names
    assert "crashDump-old.zip" not in artifact_names
    assert (evidence_dir / "crashDump-new.zip").read_bytes() == b"new"
    assert all(len(artifact["sha256"]) == 64 for artifact in manifest["artifacts"])
    assert manifest["terminal_window_title"] == "RE_Kenshi Crash Reporter"


def test_crash_reporter_dismissal_is_exact_bounded_and_human_preemptible() -> None:
    class DismissController(LaunchController):
        async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
            receipt = await super().execute(action)
            self.title = None
            self.visible_titles = []
            return receipt

    async def scenario() -> None:
        controller = DismissController(
            title="RE_Kenshi Crash Reporter",
            visible_titles=["RE_Kenshi Crash Reporter"],
        )

        await live_dev._dismiss_crash_reporter(
            controller,
            timeout_seconds=0.1,
        )

        assert controller.actions == [HotkeyAction(keys=["alt", "f4"])]

        interrupted = DismissController(
            title="RE_Kenshi Crash Reporter",
            human_input=True,
        )
        with pytest.raises(LaunchInterrupted, match="human input"):
            await live_dev._dismiss_crash_reporter(
                interrupted,
                timeout_seconds=0.1,
            )
        assert interrupted.actions == []

    import asyncio

    asyncio.run(scenario())


def test_crash_session_dismisses_each_exact_terminal_layer_before_exit() -> None:
    terminal_titles = [
        "RE_Kenshi Crash Reporter",
        "Kenshi has crashed",
    ]
    actions: list[tuple[str, PrimitiveInputAction]] = []

    class LayerController(LaunchController):
        def __init__(self, target: str | None = None) -> None:
            super().__init__()
            self.target = target

        def visible_window_titles(self) -> list[str]:
            return list(terminal_titles)

        async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
            assert self.target == terminal_titles[0]
            actions.append((self.target, action))
            terminal_titles.pop(0)
            return ActionReceipt(
                action=action,
                accepted=True,
                executed=True,
                dry_run=False,
            )

    async def scenario() -> None:
        dismissed = await live_dev._dismiss_crash_session(
            LayerController(),
            timeout_seconds=0.2,
            controller_for_title=lambda title: LayerController(title),
            process_names=lambda: (
                {"kenshi_x64.exe"} if terminal_titles else set()
            ),
        )

        assert dismissed == (
            "RE_Kenshi Crash Reporter",
            "Kenshi has crashed",
        )
        assert actions == [
            (
                "RE_Kenshi Crash Reporter",
                HotkeyAction(keys=["alt", "f4"]),
            ),
            (
                "Kenshi has crashed",
                HotkeyAction(keys=["alt", "f4"]),
            ),
        ]

    import asyncio

    asyncio.run(scenario())


def test_crash_session_never_force_terminates_a_lingering_process() -> None:
    async def scenario() -> None:
        with pytest.raises(LaunchFailed, match="no force-termination"):
            await live_dev._dismiss_crash_session(
                LaunchController(visible_titles=[]),
                timeout_seconds=0.01,
                controller_for_title=lambda _: (_ for _ in ()).throw(
                    AssertionError("no terminal window may receive input")
                ),
                process_names=lambda: {"kenshi_x64.exe"},
            )

    import asyncio

    asyncio.run(scenario())


def test_crash_parser_requires_explicit_dismissal_flag() -> None:
    inspect_args = live_dev.build_parser().parse_args(
        ["crash", "--config", "config/live.burnin.yaml"]
    )
    dismiss_args = live_dev.build_parser().parse_args(
        ["crash", "--config", "config/live.burnin.yaml", "--dismiss"]
    )

    assert inspect_args.dismiss is False
    assert dismiss_args.dismiss is True


def test_launch_preflight_rejects_low_memory_before_profile_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.burnin.yaml")
    steam_log = tmp_path / "connection_log.txt"
    steam_log.write_text(
        "[2026-07-23 16:53:46] [Logged On, 4, 7] ready\n",
        encoding="utf-8",
    )

    with pytest.raises(LaunchFailed, match=r"2048 MiB.*at least 4096 MiB"):
        _validate_launch_preconditions(
            config,
            process_names={"steam.exe"},
            available_physical_memory_mib=2048,
            steam_connection_log_path=steam_log,
        )


def test_launch_preflight_rejects_profile_drift_with_recovery_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.burnin.yaml")
    steam_log = tmp_path / "connection_log.txt"
    steam_log.write_text(
        "[2026-07-23 16:53:46] [Logged On, 4, 7] ready\n",
        encoding="utf-8",
    )
    settings = tmp_path / "settings.cfg"
    settings.write_text("view distance=2500\n", encoding="utf-8")
    profile = live_dev._configured_graphics_profile(config)
    assert profile.renderer is not None
    renderer = tmp_path / "kenshi.cfg"
    renderer.write_text(
        f"[{profile.renderer.section}]\n"
        + "".join(
            f"{key}={value}\n"
            for key, value in profile.renderer.settings.items()
        ),
        encoding="utf-8",
    )

    with pytest.raises(LaunchFailed, match=r"view distance.*graphics apply"):
        _validate_launch_preconditions(
            config,
            process_names={"steam.exe"},
            available_physical_memory_mib=8192,
            settings_path=settings,
            renderer_path=renderer,
            steam_connection_log_path=steam_log,
        )


def test_launch_parser_accepts_non_launching_preflight() -> None:
    args = live_dev.build_parser().parse_args(
        [
            "launch",
            "--config",
            "config/live.burnin.yaml",
            "--preflight-only",
        ]
    )

    assert args.preflight_only is True


def test_launch_parser_accepts_explicit_existing_launcher_resume() -> None:
    args = live_dev.build_parser().parse_args(
        [
            "launch",
            "--config",
            "config/live.burnin.yaml",
            "--resume-launcher",
        ]
    )

    assert args.resume_launcher is True


def test_launch_parser_accepts_only_one_exact_startup_source() -> None:
    args = live_dev.build_parser().parse_args(
        [
            "launch",
            "--config",
            "config/live.burnin.yaml",
            "--game-start",
            "kae-03-broke-pair",
        ]
    )

    assert args.game_start == "kae-03-broke-pair"

    with pytest.raises(SystemExit):
        live_dev.build_parser().parse_args(
            [
                "launch",
                "--config",
                "config/live.burnin.yaml",
                "--scenario",
                "fixture-a",
                "--game-start",
                "kae-03-broke-pair",
            ]
        )


def test_supported_telemetry_keeps_every_actionable_target_outside_nearest_sample() -> None:
    unadvertised = [
        WorldTarget(
            id=f"resource-{index}",
            name=f"Iron Resource {index}",
            kind="natural_resource",
            position=Vec3(x=float(index), y=0.0, z=0.0),
            distance=float(index),
            context_actions=[],
            default_task="operate_machinery",
        )
        for index in range(14)
    ]
    actionable = WorldTarget(
        id="resource-actionable",
        name="Copper Resource",
        kind="natural_resource",
        position=Vec3(x=100.0, y=0.0, z=0.0),
        distance=100.0,
        context_actions=["operate"],
        default_task="operate_machinery",
    )
    snapshot = launch_snapshot(70, paused=True).model_copy(
        update={
            "world_targets": [*unadvertised, actionable],
            "warnings": ["world target query reached capacity"],
        }
    )

    payload = _telemetry_payload(
        TelemetryRead(
            snapshot=snapshot,
            age_seconds=0.25,
            stale=False,
            path=Path("telemetry.latest.json"),
        )
    )

    assert payload["world_target_count"] == 15
    assert [
        target["id"]  # type: ignore[index]
        for target in payload["context_targets"]  # type: ignore[union-attr]
    ] == ["resource-actionable"]
    assert len(payload["nearest_world_targets"]) == 12  # type: ignore[arg-type]
    assert "resource-actionable" not in {
        target["id"]  # type: ignore[index]
        for target in payload["nearest_world_targets"]  # type: ignore[union-attr]
    }
    assert payload["warnings"] == ["world target query reached capacity"]


def test_post_load_health_requires_advancing_loaded_paused_telemetry() -> None:
    async def scenario() -> None:
        controller = LaunchController(
            title="Kenshi 1.0.65 - x64",
            visible_titles=["Kenshi 1.0.65 - x64"],
        )
        reader = LaunchTelemetry(
            launch_snapshot(30, paused=True),
            launch_snapshot(31, paused=True),
        )

        await _observe_loaded_paused_health(
            reader,  # type: ignore[arg-type]
            controller,
            duration_seconds=0.01,
        )

    import asyncio

    asyncio.run(scenario())


def test_post_load_health_fails_on_bad_stuff_before_success() -> None:
    async def scenario() -> None:
        controller = LaunchController(
            title="Kenshi 1.0.65 - x64",
            visible_titles=["Kenshi 1.0.65 - x64", "BAD STUFF"],
        )
        reader = LaunchTelemetry(launch_snapshot(40, paused=True))

        with pytest.raises(LaunchFailed, match="BAD STUFF"):
            await _observe_loaded_paused_health(
                reader,  # type: ignore[arg-type]
                controller,
                duration_seconds=0.01,
            )

    import asyncio

    asyncio.run(scenario())


def test_post_load_health_fails_when_telemetry_does_not_advance() -> None:
    async def scenario() -> None:
        controller = LaunchController(
            title="Kenshi 1.0.65 - x64",
            visible_titles=["Kenshi 1.0.65 - x64"],
        )
        reader = LaunchTelemetry(launch_snapshot(50, paused=True))

        with pytest.raises(LaunchFailed, match="no advancing telemetry"):
            await _observe_loaded_paused_health(
                reader,  # type: ignore[arg-type]
                controller,
                duration_seconds=0.01,
            )

    import asyncio

    asyncio.run(scenario())


def test_post_load_health_rejects_recovered_kernel_gpu_timeout() -> None:
    async def scenario() -> None:
        controller = LaunchController(
            title="Kenshi 1.0.65 - x64",
            visible_titles=["Kenshi 1.0.65 - x64"],
        )
        reader = LaunchTelemetry(
            launch_snapshot(60, paused=True),
            launch_snapshot(61, paused=True),
        )
        snapshots = iter(
            (
                (),
                (
                    live_dev.GpuTdrEvent(
                        record_id=100,
                        observed_at=datetime.now(UTC),
                        bucket="LKD_0x141_Tdr:6_IMAGE_igdkmdn64.sys_BBHANG",
                        watchdog_dump="WATCHDOG.dmp",
                    ),
                ),
            )
        )
        monitor = live_dev.GpuTdrMonitor(
            query_events=lambda: next(snapshots),
            min_query_interval_seconds=0,
        )
        monitor.start()

        with pytest.raises(live_dev.GpuTdrDetected, match="record 100"):
            await _observe_loaded_paused_health(
                reader,  # type: ignore[arg-type]
                controller,
                duration_seconds=0.01,
                health_check=monitor.raise_if_new,
            )

    import asyncio

    asyncio.run(scenario())


def test_semantic_control_matches_normalized_label_and_live_bounds() -> None:
    snapshot = semantic_snapshot(1, label="  Continue\n")

    control = _unique_visible_control(snapshot, ["continue"])

    assert control is not None
    assert control.center == pytest.approx((0.3, 0.15))


def test_semantic_control_click_rechecks_exact_anchor_inside_input_lease() -> None:
    async def scenario() -> None:
        controller = LaunchController()
        initial = semantic_snapshot(1, label="Continue")
        changed = semantic_snapshot(
            2,
            label="Continue",
            bounds=NormalizedPointerBounds(
                min_x=0.6,
                max_x=0.8,
                min_y=0.6,
                max_y=0.8,
            ),
        )
        reader = LaunchTelemetry(initial, changed)

        with pytest.raises(RuntimeError, match="changed inside the input lease"):
            await _click_semantic_control(
                controller,
                reader,  # type: ignore[arg-type]
                ["Continue"],
            )

        assert controller.actions == []

    import asyncio

    asyncio.run(scenario())


def test_semantic_control_click_uses_current_center_at_any_client_size() -> None:
    async def scenario() -> None:
        controller = LaunchController()
        snapshot = semantic_snapshot(
            3,
            label="Continue",
            bounds=NormalizedPointerBounds(
                min_x=0.55,
                max_x=0.75,
                min_y=0.25,
                max_y=0.35,
            ),
        )
        reader = LaunchTelemetry(snapshot, snapshot)

        await _click_semantic_control(
            controller,
            reader,  # type: ignore[arg-type]
            ["Continue"],
        )

        assert controller.actions == [
            live_dev.ClickAction(x=0.65, y=0.3, hold_seconds=MYGUI_CLICK_HOLD_SECONDS)
        ]

    import asyncio

    asyncio.run(scenario())


def test_scenario_start_uses_load_game_then_exact_managed_save() -> None:
    async def scenario() -> None:
        controller = LaunchController()
        load_bounds = NormalizedPointerBounds(
            min_x=0.1,
            max_x=0.3,
            min_y=0.2,
            max_y=0.4,
        )
        save_bounds = NormalizedPointerBounds(
            min_x=0.4,
            max_x=0.8,
            min_y=0.5,
            max_y=0.7,
        )
        reader = LaunchTelemetry(
            semantic_snapshot(1, label="Load Game", bounds=load_bounds),
            semantic_snapshot(2, label="Load Game", bounds=load_bounds),
            semantic_snapshot(3, label="Load Game", bounds=load_bounds),
            semantic_snapshot(
                4,
                label="KenshiAgentScenario",
                bounds=save_bounds,
            ),
            semantic_snapshot(
                5,
                label="KenshiAgentScenario",
                bounds=save_bounds,
            ),
            semantic_snapshot(
                6,
                label="KenshiAgentScenario",
                bounds=save_bounds,
            ),
        )

        await _open_exact_scenario_save(
            controller,
            reader,  # type: ignore[arg-type]
            load_control_labels=["Load Game"],
            save_control_label="KenshiAgentScenario",
            timeout=0.5,
        )

        assert len(controller.actions) == 2
        first, second = controller.actions
        assert isinstance(first, live_dev.ClickAction)
        assert isinstance(second, live_dev.ClickAction)
        assert (first.x, first.y) == pytest.approx((0.2, 0.3))
        assert (second.x, second.y) == pytest.approx((0.6, 0.6))
        assert first.hold_seconds == second.hold_seconds == MYGUI_CLICK_HOLD_SECONDS

    import asyncio

    asyncio.run(scenario())


def test_authored_start_traverses_carousel_then_begins_and_confirms() -> None:
    async def scenario() -> None:
        controller = LaunchController()
        snapshots = [
            semantic_snapshot(1, label="New Game"),
            semantic_snapshot(2, label="New Game"),
            semantic_snapshot(3, label="New Game"),
            carousel_snapshot(4, label="Wanderer"),
            carousel_snapshot(5, label="Wanderer"),
            carousel_snapshot(6, label="Wanderer"),
            carousel_snapshot(7, label="Freedom Seekers"),
            carousel_snapshot(8, label="Freedom Seekers"),
            carousel_snapshot(9, label="Freedom Seekers"),
            carousel_snapshot(10, label="Freedom Seekers"),
            carousel_snapshot(11, label="KAE 03 - Broke Pair"),
            semantic_snapshot(12, label="Begin"),
            semantic_snapshot(13, label="Begin"),
            semantic_snapshot(14, label="Begin"),
            semantic_snapshot(15, label="Confirm"),
            semantic_snapshot(16, label="Confirm"),
            semantic_snapshot(17, label="Confirm"),
        ]
        reader = LaunchTelemetry(*snapshots)

        await _open_exact_authored_game_start(
            controller,
            reader,  # type: ignore[arg-type]
            new_game_control_labels=["New Game"],
            game_start_label="KAE 03 - Broke Pair",
            begin_control_labels=["Begin"],
            confirm_control_labels=["Confirm"],
            max_carousel_steps=16,
            timeout=0.5,
        )

        assert len(controller.actions) == 5
        assert all(isinstance(action, live_dev.ClickAction) for action in controller.actions)

    import asyncio

    asyncio.run(scenario())


def test_authored_start_ambiguity_emits_no_start_selection_input() -> None:
    async def scenario() -> None:
        controller = LaunchController()
        new_game = semantic_snapshot(1, label="New Game")
        ambiguous = carousel_snapshot(
            4,
            label="Wanderer",
            duplicate_left=True,
        )
        reader = LaunchTelemetry(new_game, new_game, new_game, ambiguous)

        with pytest.raises(TimeoutError, match="exact authored Game Start"):
            await _open_exact_authored_game_start(
                controller,
                reader,  # type: ignore[arg-type]
                new_game_control_labels=["New Game"],
                game_start_label="KAE 03 - Broke Pair",
                begin_control_labels=["Begin"],
                confirm_control_labels=["Confirm"],
                max_carousel_steps=16,
                timeout=0.01,
            )

        assert len(controller.actions) == 1

    import asyncio

    asyncio.run(scenario())


def test_authored_start_carousel_cycle_stops_before_begin() -> None:
    async def scenario() -> None:
        controller = LaunchController()
        snapshots = [
            semantic_snapshot(1, label="New Game"),
            semantic_snapshot(2, label="New Game"),
            semantic_snapshot(3, label="New Game"),
            carousel_snapshot(4, label="Wanderer"),
            carousel_snapshot(5, label="Wanderer"),
            carousel_snapshot(6, label="Wanderer"),
            carousel_snapshot(7, label="Nobodies"),
            carousel_snapshot(8, label="Nobodies"),
            carousel_snapshot(9, label="Nobodies"),
            carousel_snapshot(10, label="Nobodies"),
            carousel_snapshot(11, label="Wanderer"),
        ]
        reader = LaunchTelemetry(*snapshots)

        with pytest.raises(LaunchFailed, match="cycled"):
            await _open_exact_authored_game_start(
                controller,
                reader,  # type: ignore[arg-type]
                new_game_control_labels=["New Game"],
                game_start_label="KAE 03 - Broke Pair",
                begin_control_labels=["Begin"],
                confirm_control_labels=["Confirm"],
                max_carousel_steps=16,
                timeout=0.5,
            )

        assert len(controller.actions) == 3

    import asyncio

    asyncio.run(scenario())


def test_authored_start_carousel_requires_causally_later_label_change() -> None:
    async def scenario() -> None:
        controller = LaunchController()
        snapshots = [
            semantic_snapshot(1, label="New Game"),
            semantic_snapshot(2, label="New Game"),
            semantic_snapshot(3, label="New Game"),
            carousel_snapshot(4, label="Wanderer"),
            carousel_snapshot(5, label="Wanderer"),
            carousel_snapshot(6, label="Wanderer"),
            carousel_snapshot(6, label="KAE 03 - Broke Pair"),
        ]
        reader = LaunchTelemetry(*snapshots)

        with pytest.raises(TimeoutError, match="advance from 'Wanderer'"):
            await _open_exact_authored_game_start(
                controller,
                reader,  # type: ignore[arg-type]
                new_game_control_labels=["New Game"],
                game_start_label="KAE 03 - Broke Pair",
                begin_control_labels=["Begin"],
                confirm_control_labels=["Confirm"],
                max_carousel_steps=16,
                timeout=0.01,
            )

        assert len(controller.actions) == 2

    import asyncio

    asyncio.run(scenario())


def test_duplicate_semantic_label_is_ambiguous_and_emits_no_match() -> None:
    control = semantic_snapshot(4, label="Continue").ui.visible_controls
    assert control is not None
    snapshot = semantic_snapshot(4, label="Continue").model_copy(
        update={
            "ui": UIState(
                visible_controls=[control[0], control[0].model_copy(deep=True)]
            )
        }
    )

    assert _unique_visible_control(snapshot, ["Continue"]) is None


def _journey_args(*extra: str) -> object:
    return live_dev.build_parser().parse_args(
        ["journey", "--config", "config/live.burnin.yaml", *extra]
    )


def test_journey_defaults_to_single_step_without_continuous_flags() -> None:
    argv = _journey_argv(_journey_args(), "run-1")
    assert "--planning-mode" not in argv
    assert "--acknowledge-continuous-live" not in argv
    assert "--execute-live-actions" not in argv


def test_journey_continuous_flag_passes_planning_mode() -> None:
    argv = _journey_argv(_journey_args("--continuous"), "run-2")
    assert argv[argv.index("--planning-mode") + 1] == "continuous"


def test_journey_passes_an_explicit_campaign_to_the_core_run() -> None:
    argv = _journey_argv(
        _journey_args("--campaign", "ladle-css-01"),
        "campaign-run",
    )

    assert argv[argv.index("--campaign") + 1] == "ladle-css-01"


def test_journey_continuous_does_not_imply_the_acknowledgement() -> None:
    # --continuous alone must never silently grant the continuous-live ack; the
    # run command then refuses live-continuous execution, preserving the gate.
    argv = _journey_argv(_journey_args("--continuous"), "run-3")
    assert "--acknowledge-continuous-live" not in argv


def test_journey_full_continuous_live_invocation_passes_every_gate() -> None:
    argv = _journey_argv(
        _journey_args(
            "--continuous",
            "--execute",
            "--native-assisted",
            "--acknowledge-continuous-live",
        ),
        "run-4",
    )
    assert argv[argv.index("--planning-mode") + 1] == "continuous"
    assert "--execute-live-actions" in argv
    assert "--acknowledge-native-assisted-control" in argv
    assert "--acknowledge-continuous-live" in argv


def test_journey_passes_complete_scenario_declaration_to_core_run() -> None:
    argv = _journey_argv(
        _journey_args(
            "--scenario-id",
            "hub-outdoor-safe-day",
            "--save-id",
            "hub-start-v1",
            "--scenario-environment",
            "outdoor",
            "--scenario-danger",
            "safe",
            "--scenario-economy",
            "broke",
            "--scenario-party",
            "solo",
            "--scenario-time-of-day",
            "day",
        ),
        "scenario-run",
    )

    assert argv[argv.index("--scenario-id") + 1] == "hub-outdoor-safe-day"
    assert argv[argv.index("--save-id") + 1] == "hub-start-v1"
    assert argv[argv.index("--scenario-environment") + 1] == "outdoor"
    assert argv[argv.index("--scenario-danger") + 1] == "safe"
    assert argv[argv.index("--scenario-economy") + 1] == "broke"
    assert argv[argv.index("--scenario-party") + 1] == "solo"
    assert argv[argv.index("--scenario-time-of-day") + 1] == "day"


def test_attested_journey_forwards_only_the_attestation(
    tmp_path: Path,
) -> None:
    attestation = tmp_path / "current_attestation.json"
    argv = _journey_argv(
        _journey_args("--scenario", "hub-outdoor-safe-day"),
        "scenario-run",
        scenario_attestation=attestation,
    )

    assert argv[argv.index("--scenario-attestation") + 1] == str(attestation)
    assert "--scenario-id" not in argv
    assert "--save-id" not in argv


def test_scenario_capture_refuses_to_read_a_live_save(
    monkeypatch: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "saves" / "autosave1"
    source.mkdir(parents=True)
    (source / "quick.save").write_bytes(b"save")
    monkeypatch.setattr(  # type: ignore[attr-defined]
        live_dev,
        "_running_process_names",
        lambda: {"kenshi_x64.exe"},
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        live_dev,
        "_kenshi_save_root",
        lambda: tmp_path / "saves",
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        live_dev,
        "_scenario_store",
        lambda: tmp_path / "store",
    )
    args = live_dev.build_parser().parse_args(
        [
            "scenario",
            "--config",
            "config/live.burnin.yaml",
            "capture",
            "--source-save",
            "autosave1",
            "--scenario-id",
            "hub-outdoor-safe-day",
            "--save-id",
            "hub-start-v1",
            "--environment",
            "outdoor",
            "--danger",
            "safe",
            "--economy",
            "broke",
            "--party",
            "solo",
            "--time-of-day",
            "day",
        ]
    )

    assert live_dev._scenario_command(args) == 4
    assert "must be closed" in capsys.readouterr().err
    assert not (tmp_path / "store").exists()


def test_supported_scenario_command_captures_and_restores_reserved_slot(
    monkeypatch: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    save_root = tmp_path / "saves"
    source = save_root / "autosave1"
    source.mkdir(parents=True)
    (source / "quick.save").write_bytes(b"quick")
    (source / "platoon").mkdir()
    (source / "platoon" / "Nameless_0.platoon").write_bytes(b"platoon")
    monkeypatch.setattr(  # type: ignore[attr-defined]
        live_dev,
        "_running_process_names",
        lambda: set(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        live_dev,
        "_kenshi_save_root",
        lambda: save_root,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        live_dev,
        "_scenario_store",
        lambda: tmp_path / "store",
    )
    capture = live_dev.build_parser().parse_args(
        [
            "scenario",
            "--config",
            "config/live.burnin.yaml",
            "capture",
            "--source-save",
            "autosave1",
            "--scenario-id",
            "hub-outdoor-safe-day",
            "--save-id",
            "hub-start-v1",
            "--environment",
            "outdoor",
            "--danger",
            "safe",
            "--economy",
            "broke",
            "--party",
            "solo",
            "--time-of-day",
            "day",
        ]
    )
    restore = live_dev.build_parser().parse_args(
        [
            "scenario",
            "--config",
            "config/live.burnin.yaml",
            "restore",
            "hub-outdoor-safe-day",
        ]
    )

    assert live_dev._scenario_command(capture) == 0
    assert live_dev._scenario_command(restore) == 0

    managed = save_root / "KenshiAgentScenario"
    assert (managed / "quick.save").read_bytes() == b"quick"
    assert source.is_dir()
    output = capsys.readouterr().out
    assert "Captured" in output
    assert "Restored" in output


def test_supported_scenario_command_installs_and_verifies_authored_starts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "mods.cfg").write_bytes(b"KenshiAgentTelemetry.mod\r\n")
    monkeypatch.setattr(live_dev, "_running_process_names", lambda: set())
    monkeypatch.setattr(live_dev, "_kenshi_root", lambda: tmp_path)
    install = live_dev.build_parser().parse_args(
        [
            "scenario",
            "--config",
            "config/live.burnin.yaml",
            "install-starts",
        ]
    )
    verify = live_dev.build_parser().parse_args(
        [
            "scenario",
            "--config",
            "config/live.burnin.yaml",
            "verify-starts",
        ]
    )

    assert live_dev._scenario_command(install) == 0
    assert live_dev._scenario_command(verify) == 0
    output = capsys.readouterr().out
    assert "installed exact mod bytes" in output
    assert "installed and enabled exactly" in output


def test_scenario_install_refuses_while_fcs_is_open(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        live_dev,
        "_running_process_names",
        lambda: {"forgotten construction set.exe"},
    )
    args = live_dev.build_parser().parse_args(
        [
            "scenario",
            "--config",
            "config/live.burnin.yaml",
            "install-starts",
        ]
    )

    assert live_dev._scenario_command(args) == 4
    assert "Kenshi and FCS must be closed" in capsys.readouterr().err


def test_journey_subprocess_planner_uses_lossless_windows_argv() -> None:
    args = _journey_args(
        "--planner",
        "subprocess",
        "--planner-script",
        "scripts/live_direction_smoke_planner.py",
        "--planner-arg=--bearing",
        "--planner-arg=99.828",
        "--planner-arg=--distance",
        "--planner-arg=350",
    )

    argv = _journey_argv(args, "subprocess-run")

    command_args = [
        value.removeprefix("--command-arg=")
        for value in argv
        if value.startswith("--command-arg=")
    ]
    assert command_args[0] == live_dev.sys.executable
    assert Path(command_args[1]).name == "live_direction_smoke_planner.py"
    assert command_args[2:] == [
        "--bearing",
        "99.828",
        "--distance",
        "350",
    ]
    assert "--command" not in argv


def test_journey_acknowledgement_without_continuous_is_harmless_passthrough() -> None:
    # The ack can be present without --continuous; run stays single-step, so the
    # continuous-live gate is simply not reached.
    argv = _journey_argv(_journey_args("--acknowledge-continuous-live"), "run-5")
    assert "--planning-mode" not in argv
    assert "--acknowledge-continuous-live" in argv


def test_journey_delegates_final_safety_to_the_core_run(
    monkeypatch: object,
) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        live_dev,
        "agent_main",
        lambda argv: captured.append(argv) or 6,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        live_dev,
        "_controller",
        lambda _: (_ for _ in ()).throw(
            AssertionError("journey must not create a second cleanup owner")
        ),
    )

    result = live_dev._journey(
        _journey_args("--execute")
    )

    assert result == 6
    assert len(captured) == 1
    assert "--execute-live-actions" in captured[0]


def test_journey_ownership_overlay_is_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[list[str]] = []

    class OverlayProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise AssertionError("a successful journey leaves an opted-in overlay to auto-close")

    monkeypatch.setattr(live_dev, "agent_main", lambda _: 0)
    monkeypatch.setattr(
        live_dev.subprocess,
        "Popen",
        lambda argv, **_: opened.append(argv) or OverlayProcess(),
    )

    assert live_dev._journey(_journey_args("--execute")) == 0
    assert opened == []

    assert (
        live_dev._journey(
            _journey_args("--execute", "--ownership-overlay")
        )
        == 0
    )
    assert len(opened) == 1


def test_startup_clicks_hold_long_enough_for_mygui() -> None:
    """Kenshi ignores an instantaneous press.

    The launcher's startup click used the zero-duration default. That squeaked
    through only while relative stepping walked the cursor to its target slowly;
    once the pointer began warping, the click arrived instantly and stopped
    registering, and startup stalled silently on the title screen with the
    Continue button plainly visible.
    """

    assert MYGUI_CLICK_HOLD_SECONDS > 0.0
    # Matches the value the semantic control action uses in live gameplay.
    from kenshi_agent.config import ControlsConfig

    assert MYGUI_CLICK_HOLD_SECONDS == ControlsConfig().control_activation_hold_seconds
