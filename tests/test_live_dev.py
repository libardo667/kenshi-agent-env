import inspect
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.config import ControlsConfig, load_config
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.core.operation import (
    HotkeyAction,
)
from kenshi_agent.core.telemetry import (
    CharacterState,
    GameState,
    KnownMapDestination,
    NativeCommandAcknowledgement,
    NativeCommandStatus,
    NativeControlState,
    NearbyEntity,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    Vec3,
    VisibleUIControl,
    WorldTarget,
)
from kenshi_agent.core.transport import ActionReceipt, NativeCommandRequest
from kenshi_agent.telemetry import TelemetryRead
from kenshi_agent.tooling import live_dev
from kenshi_agent.tooling.dev_cli import parse_args, render_reference
from kenshi_agent.tooling.live_dev import (
    LaunchFailed,
    LaunchInterrupted,
    _agent_argv,
    _disable_re_kenshi_startup_panel,
    _dispatch_native_startup_command,
    _ensure_native_launch_pause,
    _game_executable,
    _observe_loaded_paused_health,
    _plugin_ready,
    _steam_connection_state,
    _telemetry_payload,
    _validate_calibrated_client_rect,
    _validate_launch_preconditions,
    _validate_resumable_launcher_rect,
    _validate_safe_close_snapshot,
    _wait_until,
)


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
        self.dialog_commands: list[tuple[str, int]] = []

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

    def request_dialog_command(self, *, button_text: str, control_id: int) -> None:
        self.dialog_commands.append((button_text, control_id))


def test_live_dev_exposes_only_the_approved_top_level_commands() -> None:
    parser = live_dev.build_parser()
    commands: set[str] = set()
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        commands.update(action.choices)

    assert commands == {
        "doctor",
        "verify-portable",
        "launch",
        "run",
        "telemetry",
        "affordances",
        "snapshot",
        "recover",
        "stop",
        "tui",
        "scenario",
        "setup",
        "generation-manifest",
        "capability-manifest",
    }


def test_normal_dev_surface_has_no_profile_config_or_planner_switches() -> None:
    reference = render_reference()

    assert "--profile" not in reference
    assert "--config" not in reference
    assert "--planner" not in reference
    for obsolete in (
        ["run", "--profile", "dialogue"],
        ["run", "--config", "config/live.yaml"],
        ["run", "--planner", "subprocess"],
    ):
        with pytest.raises(SystemExit):
            parse_args(obsolete)


@pytest.mark.parametrize("argv", [[], ["scenario"], ["setup"]])
def test_parser_rejects_incomplete_workflows(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        live_dev.build_parser().parse_args(argv)


def test_parser_preserves_typed_runtime_and_safety_defaults() -> None:
    parser = live_dev.build_parser()

    doctor = parser.parse_args(["doctor", "--timeout", "1.5"])
    launch = parser.parse_args(["launch", "--timeout", "2.5"])
    run = parser.parse_args(["run", "--timeout", "3.5", "--steps", "7"])
    telemetry = parser.parse_args(["telemetry", "--interval", "0.25"])
    recover = parser.parse_args(["recover", "--timeout", "4.5"])
    stop = parser.parse_args(["stop", "--timeout", "5.5"])

    assert doctor.timeout == 1.5
    assert doctor.resume_launcher is False
    assert doctor.preflight_only is True
    assert launch.timeout == 2.5
    assert launch.preflight_only is False
    assert run.timeout == 3.5
    assert run.steps == 7
    assert run.resume_launcher is False
    assert run.preflight_only is False
    assert telemetry.interval == 0.25
    assert recover.timeout == 4.5
    assert stop.timeout == 5.5


def test_shared_parser_entrypoint_and_reference_render() -> None:
    assert parse_args(["telemetry"]).command == "telemetry"
    assert parse_args(["verify-portable"]).command == "verify-portable"
    assert render_reference().startswith("# `./dev` command reference\n")


def test_run_control_mode_is_one_explicit_authority_choice() -> None:
    parser = live_dev.build_parser()

    assert parser.parse_args(["run"]).control == "plan-only"
    assert parser.parse_args(["run", "--control", "live"]).control == "live"


def test_launch_commands_keep_current_display_by_default_and_expose_focus() -> None:
    parser = live_dev.build_parser()

    assert parser.parse_args(["launch"]).focus_display is False
    assert parser.parse_args(["run"]).focus_display is False
    assert parser.parse_args(["launch", "--focus-display"]).focus_display is True
    assert parser.parse_args(["run", "--focus-display"]).focus_display is True
    with pytest.raises(SystemExit):
        parser.parse_args(["doctor", "--focus-display"])


def test_telemetry_watch_and_snapshot_are_first_class_commands() -> None:
    parser = live_dev.build_parser()

    telemetry = parser.parse_args(["telemetry", "--watch"])
    snapshot = parser.parse_args(["snapshot", "--label", "trade-check"])

    assert telemetry.watch is True
    assert snapshot.label == "trade-check"


def test_telemetry_watch_emits_fresh_ndjson_until_interrupted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reads = iter(
        [
            TelemetryRead(
                snapshot=launch_snapshot(sequence, paused=True),
                age_seconds=0.1,
                stale=False,
                path=Path("telemetry.latest.json"),
            )
            for sequence in (10, 11)
        ]
    )
    sleeps = 0

    class Reader:
        def read(self) -> TelemetryRead:
            return next(reads)

    def sleep(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(live_dev, "load_config", lambda _: object())
    monkeypatch.setattr(live_dev, "_telemetry_read", lambda _: Reader())
    monkeypatch.setattr(live_dev.time, "sleep", sleep)
    args = live_dev.build_parser().parse_args(
        ["telemetry", "--watch", "--interval", "0.01"]
    )

    assert live_dev._telemetry(args) == 0
    payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [payload["sequence"] for payload in payloads] == [10, 11]


def test_snapshot_pairs_the_frame_with_full_telemetry_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    base = load_config(root / "config" / "live.yaml")
    config = base.model_copy(
        update={
            "paths": base.paths.model_copy(update={"runs_dir": tmp_path}),
        }
    )
    result = TelemetryRead(
        snapshot=launch_snapshot(42, paused=True),
        age_seconds=0.1,
        stale=False,
        path=Path("telemetry.latest.json"),
    )

    class Reader:
        def read(self) -> TelemetryRead:
            return result

    class Capture:
        def __init__(self, _controller: object, run_dir: Path, **_: object) -> None:
            self.run_dir = run_dir

        def capture(self, _sequence: int) -> object:
            self.run_dir.mkdir(parents=True)
            path = self.run_dir / "frame.png"
            path.write_bytes(b"png")
            return type("Frame", (), {"path": path})()

    monkeypatch.setattr(live_dev, "load_config", lambda _: config)
    monkeypatch.setattr(live_dev, "_controller", lambda _: object())
    monkeypatch.setattr(live_dev, "_telemetry_read", lambda _: Reader())
    monkeypatch.setattr(live_dev, "WindowCapture", Capture)
    args = live_dev.build_parser().parse_args(
        ["snapshot", "--label", "paired-proof"]
    )

    assert live_dev._snapshot(args) == 0
    evidence_dir = Path(capsys.readouterr().out.strip())
    telemetry = json.loads((evidence_dir / "telemetry.json").read_text())
    manifest = json.loads((evidence_dir / "manifest.json").read_text())
    assert telemetry["snapshot"]["sequence"] == 42
    assert Path(manifest["frame"]).name == "frame.png"
    assert manifest["telemetry"] == "telemetry.json"


def test_setup_has_only_the_explicit_graphics_repair() -> None:
    args = live_dev.build_parser().parse_args(["setup", "graphics"])

    assert args.command == "setup"
    assert args.setup_action == "graphics"


def test_run_start_is_state_aware_and_fails_closed_on_ambiguous_state() -> None:
    choose = live_dev._choose_run_path
    loaded = launch_snapshot(7, paused=True)
    fresh = TelemetryRead(
        snapshot=loaded,
        age_seconds=0.1,
        stale=False,
        path=Path("telemetry.latest.json"),
    )

    assert choose(process_names=set(), telemetry=None, terminal_window_title=None) == (
        "launch"
    )
    assert choose(
        process_names={"kenshi_x64.exe"},
        telemetry=fresh,
        terminal_window_title=None,
    ) == "loaded"

    stale = TelemetryRead(
        snapshot=loaded,
        age_seconds=30.0,
        stale=True,
        path=Path("telemetry.latest.json"),
    )
    with pytest.raises(LaunchFailed, match=r"stale.*\./dev recover"):
        choose(
            process_names={"kenshi_x64.exe"},
            telemetry=stale,
            terminal_window_title=None,
        )
    with pytest.raises(LaunchFailed, match=r"terminal.*\./dev recover"):
        choose(
            process_names={"kenshi_x64.exe"},
            telemetry=fresh,
            terminal_window_title="RE_Kenshi Crash Reporter",
        )


def test_run_control_mode_translates_to_the_core_authority_gates() -> None:
    build_agent_argv = live_dev._agent_argv
    parser = live_dev.build_parser()

    plan_only = build_agent_argv(parser.parse_args(["run"]), "plan")
    live = build_agent_argv(parser.parse_args(["run", "--control", "live"]), "live")

    assert "--execute-live-actions" not in plan_only
    assert "--exclusive-input-session" not in plan_only
    # A live run owns input outright. The removed middle mode restored the host
    # cursor and foreground around every action, which the configured relative
    # pointer mode could not survive - live actions refused to start under it.
    assert "--execute-live-actions" in live
    assert "--acknowledge-native-assisted-control" in live
    assert "--acknowledge-continuous-live" in live
    assert "--exclusive-input-session" in live


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
        "controller_commands": {"commands": []},
    }

    _validate_safe_close_snapshot(
        payload,
        max_age_seconds=3.0,
        now=observed_at,
    )

    for path, value in (
        (("game", "loaded"), False),
        (("game", "paused"), False),
        (("controller_commands", "commands"), [{"status": "accepted"}]),
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


def test_supported_close_pauses_natively_before_requesting_wm_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = _native_launch_config(tmp_path)
        controller = LaunchController()
        command_id = "cmd-" + "1" * 32
        monkeypatch.setattr(live_dev, "new_command_id", lambda: command_id)

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

        running = native_cleanup_snapshot(idle_snapshot(40, paused=False))
        paused = native_cleanup_snapshot(
            idle_snapshot(41, paused=True),
            command_id=command_id,
            based_on_sequence=40,
        )
        telemetry = LaunchTelemetry(running, running, paused, paused)

        await live_dev._close_kenshi_safely(
            config,
            controller,
            telemetry,
            timeout_seconds=0.1,
            process_names=lambda: (
                set() if controller.close_requested else {"kenshi_x64.exe"}
            ),
        )

        request = NativeCommandRequest.model_validate_json(
            (tmp_path / "native_command.request.json").read_bytes()
        )
        assert request.command == "pause"
        assert request.paused is True
        assert controller.actions == []
        assert controller.safety_actions == []
        assert controller.lease_entries == 0
        assert controller.close_requested is True

    import asyncio

    asyncio.run(scenario())


def test_interrupted_recovery_pauses_and_closes_interface_natively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = _native_launch_config(tmp_path)
        controller = LaunchController()
        pause_id = "cmd-" + "2" * 32
        close_id = "cmd-" + "3" * 32
        command_ids = iter((pause_id, close_id))
        monkeypatch.setattr(live_dev, "new_command_id", lambda: next(command_ids))
        inventory_capabilities = [
            *resource_inventory_snapshot(40).capabilities,
            "game.pause",
            "control.close_active_interface",
        ]
        unpaused = native_cleanup_snapshot(
            resource_inventory_snapshot(40).model_copy(
                update={
                    "capabilities": inventory_capabilities,
                    "game": GameState(loaded=True, paused=False),
                },
                deep=True,
            )
        )
        paused = native_cleanup_snapshot(
            resource_inventory_snapshot(41).model_copy(
                update={"capabilities": inventory_capabilities},
                deep=True,
            ),
            command_id=pause_id,
            based_on_sequence=40,
        )
        world = native_cleanup_snapshot(
            paused.model_copy(
                update={
                    "sequence": 42,
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                        open_inventory_windows=0,
                        visible_controls_complete=True,
                        visible_controls=[],
                    )
                }
            ),
            command_id=close_id,
            command="close_active_interface",
            based_on_sequence=41,
        )
        telemetry = LaunchTelemetry(
            unpaused,
            unpaused,
            paused,
            paused,
            paused,
            world,
            world,
        )

        safe_state = await live_dev._recover_kenshi_safe_state(
            config,
            controller,
            telemetry,
            timeout_seconds=0.1,
            process_names=lambda: {"kenshi_x64.exe"},
        )

        assert safe_state == "loaded_paused"
        request = NativeCommandRequest.model_validate_json(
            (tmp_path / "native_command.request.json").read_bytes()
        )
        assert request.command == "close_active_interface"
        assert controller.safety_actions == []
        assert controller.actions == []
        assert controller.lease_entries == 0
        assert controller.close_requested is False

    import asyncio

    asyncio.run(scenario())


def test_interrupted_recovery_refuses_pause_without_native_identity() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "live.yaml")
        controller = LaunchController()
        command_id = "cmd-" + "a" * 32
        acknowledgement = NativeCommandAcknowledgement(
            command_id=command_id,
            command="produce_resource_output",
            status=NativeCommandStatus.ACCEPTED,
            reason="Resource production remains active.",
            target_id="entity-iron",
            minimum_output_quantity=3,
            selected_character_ids=["entity-hep"],
            based_on_telemetry_sequence=38,
            acknowledged_at_telemetry_sequence=39,
            accepted_at_telemetry_sequence=40,
        )

        def active_snapshot(sequence: int, *, paused: bool) -> TelemetrySnapshot:
            return launch_snapshot(sequence, paused=paused).model_copy(
                update={
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                    ),
                    "controller_commands": NativeControlState(
                        available=True,
                        commands=[acknowledgement],
                    )
                }
            )

        telemetry = LaunchTelemetry(
            active_snapshot(40, paused=False),
            active_snapshot(40, paused=False),
                active_snapshot(41, paused=True),
                active_snapshot(41, paused=True),
                active_snapshot(42, paused=True),
                active_snapshot(43, paused=True).model_copy(
                    update={"controller_commands": NativeControlState(available=True)}
                ),
                active_snapshot(43, paused=True).model_copy(
                    update={"controller_commands": NativeControlState(available=True)}
                ),
            )

        with pytest.raises(LaunchFailed, match="authoritative session identity"):
            await live_dev._recover_kenshi_safe_state(
                config,
                controller,
                telemetry,
                timeout_seconds=0.1,
                process_names=lambda: {"kenshi_x64.exe"},
            )

        assert controller.safety_actions == []
        assert controller.actions == []
        assert controller.close_requested is False

    import asyncio

    asyncio.run(scenario())


def test_supported_close_never_closes_modal_without_native_cleanup_authority() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "live.yaml")
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

        with pytest.raises(LaunchFailed, match="native close_active_interface cleanup"):
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


def test_supported_close_closes_resource_inventory_natively_before_wm_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = _native_launch_config(tmp_path)
        controller = LaunchController()
        command_id = "cmd-" + "4" * 32
        monkeypatch.setattr(live_dev, "new_command_id", lambda: command_id)
        resource_inventory = native_cleanup_snapshot(
            resource_inventory_snapshot(
                60,
                loaded_shop_trader_count=2,
            )
        )
        world = native_cleanup_snapshot(
            resource_inventory.model_copy(
                update={
                    "sequence": 61,
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                        open_inventory_windows=0,
                        visible_controls_complete=True,
                        visible_controls=[],
                    )
                }
            ),
            command_id=command_id,
            command="close_active_interface",
            based_on_sequence=60,
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

        request = NativeCommandRequest.model_validate_json(
            (tmp_path / "native_command.request.json").read_bytes()
        )
        assert request.command == "close_active_interface"
        assert controller.actions == []
        assert controller.safety_actions == []
        assert controller.lease_entries == 0
        assert controller.close_requested is True

    import asyncio

    asyncio.run(scenario())


def test_supported_close_never_uses_layout_as_cleanup_authority() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "live.yaml")
        controller = LaunchController()
        incomplete = resource_inventory_snapshot(70).model_copy(
            update={
                "ui": resource_inventory_snapshot(70).ui.model_copy(
                    update={"visible_controls_complete": False}
                )
            },
            deep=True,
        )

        with pytest.raises(LaunchFailed, match="native close_active_interface cleanup"):
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


def test_supported_close_closes_both_inventories_with_one_native_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = _native_launch_config(tmp_path)
        controller = LaunchController()
        command_id = "cmd-" + "5" * 32
        monkeypatch.setattr(live_dev, "new_command_id", lambda: command_id)
        both = native_cleanup_snapshot(
            resource_inventory_snapshot(
                80,
                destination_open=True,
                loaded_shop_trader_count=5,
            )
        )
        world = native_cleanup_snapshot(
            both.model_copy(
                update={
                    "sequence": 81,
                    "ui": UIState(
                        active_screen="world",
                        modal_open=False,
                        dialogue_open=False,
                        open_inventory_windows=0,
                        visible_controls_complete=True,
                        visible_controls=[],
                    )
                }
            ),
            command_id=command_id,
            command="close_active_interface",
            based_on_sequence=80,
        )
        telemetry = LaunchTelemetry(both, both, both, world, world)

        await live_dev._close_kenshi_safely(
            config,
            controller,
            telemetry,
            timeout_seconds=0.1,
            process_names=lambda: (
                set() if controller.close_requested else {"kenshi_x64.exe"}
            ),
        )

        request = NativeCommandRequest.model_validate_json(
            (tmp_path / "native_command.request.json").read_bytes()
        )
        assert request.command == "close_active_interface"
        assert controller.actions == []
        assert controller.safety_actions == []
        assert controller.close_requested is True

    import asyncio

    asyncio.run(scenario())


class LaunchTelemetry:
    def __init__(self, *snapshots: TelemetrySnapshot) -> None:
        self.snapshots = list(snapshots)
        self.index = 0
        self.stale_reads = 0

    def read(self) -> TelemetryRead:
        read_index = self.index
        snapshot = self.snapshots[min(read_index, len(self.snapshots) - 1)]
        self.index += 1
        return TelemetryRead(
            snapshot=snapshot,
            age_seconds=0.0,
            stale=read_index < self.stale_reads,
            path=Path("telemetry.latest.json"),
        )


def launch_snapshot(sequence: int, *, paused: bool) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=sequence,
        captured_at=datetime.now(UTC),
        capabilities=["game.pause"],
        game=GameState(loaded=True, paused=paused),
        roster=[CharacterState(id="entity-hep", name="Hep")],
    )


def native_cleanup_snapshot(
    snapshot: TelemetrySnapshot,
    *,
    command_id: str | None = None,
    command: str = "pause",
    based_on_sequence: int | None = None,
    reason: str | None = None,
) -> TelemetrySnapshot:
    capabilities = list(snapshot.capabilities)
    for capability in ("game.pause", "control.close_active_interface"):
        if capability not in capabilities:
            capabilities.append(capability)
    commands: list[NativeCommandAcknowledgement] = []
    if command_id is not None:
        basis = based_on_sequence if based_on_sequence is not None else snapshot.sequence - 1
        commands.append(
            NativeCommandAcknowledgement(
                command_id=command_id,
                command=command,  # type: ignore[arg-type]
                status=NativeCommandStatus.COMPLETED,
                reason=(
                    reason
                    or ("world_paused" if command == "pause" else "active_interface_closed")
                ),
                selected_character_ids=list(snapshot.selected_character_ids),
                based_on_telemetry_sequence=basis,
                acknowledged_at_telemetry_sequence=snapshot.sequence,
                accepted_at_telemetry_sequence=snapshot.sequence,
                terminal_at_telemetry_sequence=snapshot.sequence,
            )
        )
    return snapshot.model_copy(
        update={
            "identity_session_id": "session-world",
            "capabilities": capabilities,
            "controller_commands": NativeControlState(
                available=True,
                commands=commands,
            ),
        },
        deep=True,
    )


def resource_inventory_snapshot(
    sequence: int,
    *,
    source_open: bool = True,
    destination_open: bool = False,
    loaded_shop_trader_count: int = 0,
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
        active_shop_trader_count=loaded_shop_trader_count,
        ui=UIState(
            active_screen="trade" if open_count == 2 else "inventory",
            modal_open=open_count > 0,
            dialogue_open=False,
            open_inventory_windows=open_count,
                context_inventory_target_id=(
                    "entity-iron" if source_open else None
                ),
                visible_controls_complete=True,
                visible_controls=controls,
            ),
            primary_character_id="entity-hep",
            selected_character_ids=["entity-hep"],
        roster=[
            CharacterState(
                id="entity-hep",
                name="Hep",
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
    config = load_config(Path(__file__).resolve().parents[1] / "config" / "live.yaml")

    assert live_dev._controller(config) is sentinel
    assert captured["polite_input_enabled"] is True
    assert captured["idle_seconds_before_input"] == 0.0
    assert captured["max_wait_for_input_turn_seconds"] == 1.0
    assert captured["restore_foreground_after_input"] is True
    assert captured["restore_cursor_after_input"] is True
    assert captured["alt_tab_after_input"] is False


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
    config = load_config(root / "config" / "live.yaml")
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
    config = load_config(root / "config" / "live.yaml")
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
    config = load_config(root / "config" / "live.yaml")

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
    config = load_config(root / "config" / "live.yaml")
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


def test_resume_launcher_accepts_small_or_full_native_window() -> None:
    _validate_resumable_launcher_rect(WindowRect(0, 0, 1920, 1080))
    _validate_resumable_launcher_rect(WindowRect(0, 0, 900, 700))

    with pytest.raises(LaunchFailed, match="measurable"):
        _validate_resumable_launcher_rect(WindowRect(0, 0, 0, 0))


def test_launch_preflight_prioritizes_terminal_crash_over_duplicate_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.yaml")

    with pytest.raises(
        LaunchFailed,
        match=r"RE_Kenshi Crash Reporter.*\./dev recover",
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
    config = load_config(root / "config" / "live.yaml")
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


def test_recovery_requires_explicit_crash_dismissal_flag() -> None:
    inspect_args = live_dev.build_parser().parse_args(
        ["recover", "--config", "config/live.yaml"]
    )
    dismiss_args = live_dev.build_parser().parse_args(
        ["recover", "--config", "config/live.yaml", "--dismiss-crash"]
    )

    assert inspect_args.dismiss_crash is False
    assert dismiss_args.dismiss_crash is True


def test_doctor_archives_a_terminal_crash_without_dismissing_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[bool] = []

    async def archive(args: object) -> int:
        calls.append(args.dismiss)  # type: ignore[attr-defined]
        return 0

    monkeypatch.setattr(live_dev, "load_config", lambda _: object())
    monkeypatch.setattr(live_dev, "_windows_runtime", lambda: True)
    monkeypatch.setattr(live_dev, "_controller", lambda _: object())
    monkeypatch.setattr(
        live_dev,
        "_terminal_window_title",
        lambda _: "RE_Kenshi Crash Reporter",
    )
    monkeypatch.setattr(live_dev, "_crash", archive)
    monkeypatch.setattr(
        live_dev,
        "_launch",
        lambda _: (_ for _ in ()).throw(AssertionError("doctor must not continue")),
    )
    args = live_dev.build_parser().parse_args(["doctor"])

    import asyncio

    assert asyncio.run(live_dev._doctor(args)) == 4
    assert calls == [False]
    assert "archived" in capsys.readouterr().err


def test_recover_archives_a_crash_and_requires_explicit_dismissal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[bool] = []

    async def archive(args: object) -> int:
        calls.append(args.dismiss)  # type: ignore[attr-defined]
        return 0

    monkeypatch.setattr(live_dev, "load_config", lambda _: object())
    monkeypatch.setattr(live_dev, "_windows_runtime", lambda: True)
    monkeypatch.setattr(live_dev, "_controller", lambda _: object())
    monkeypatch.setattr(
        live_dev,
        "_terminal_window_title",
        lambda _: "RE_Kenshi Crash Reporter",
    )
    monkeypatch.setattr(live_dev, "_crash", archive)
    monkeypatch.setattr(
        live_dev,
        "_recover_kenshi_safe_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the crash must remain untouched")
        ),
    )
    args = live_dev.build_parser().parse_args(["recover"])

    import asyncio

    assert asyncio.run(live_dev._recover(args)) == 3
    assert calls == [False]
    assert "--dismiss-crash" in capsys.readouterr().err


def test_launch_preflight_rejects_low_memory_before_profile_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.yaml")
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


def test_low_launch_memory_reclaims_once_then_waits_for_page_reporting() -> None:
    memory_readings = iter([2048, 3072, 4352])
    command_calls: list[tuple[list[str], dict[str, object]]] = []
    sleeps: list[float] = []
    now = 0.0

    def run_command(command: list[str], **kwargs: object) -> object:
        command_calls.append((command, kwargs))
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    result = live_dev._recover_low_launch_memory(
        threshold_mib=4096,
        distribution="Ubuntu-22.04",
        available_memory_mib=lambda: next(memory_readings),
        run_command=run_command,
        sleep=sleep,
        monotonic=lambda: now,
        settle_timeout_seconds=10.0,
        poll_seconds=2.0,
    )

    assert result == (2048, 4352)
    assert sleeps == [2.0]
    assert command_calls == [
        (
            [
                "wsl.exe",
                "--distribution",
                "Ubuntu-22.04",
                "--user",
                "root",
                "--exec",
                "sh",
                "-c",
                "sync; echo 3 > /proc/sys/vm/drop_caches",
            ],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": 15.0,
            },
        )
    ]


def test_low_launch_memory_reclaim_fails_closed_after_bounded_settle() -> None:
    readings = iter([2048, 2500, 2600, 2700])
    calls = 0
    now = 0.0

    def run_command(command: list[str], **kwargs: object) -> object:
        nonlocal calls
        del command, kwargs
        calls += 1
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    with pytest.raises(
        LaunchFailed,
        match=r"completed.*2048 MiB to 2700 MiB.*requires 4096 MiB",
    ):
        live_dev._recover_low_launch_memory(
            threshold_mib=4096,
            distribution="Ubuntu-22.04",
            available_memory_mib=lambda: next(readings),
            run_command=run_command,
            sleep=sleep,
            monotonic=lambda: now,
            settle_timeout_seconds=2.0,
            poll_seconds=1.0,
        )

    assert calls == 1


def test_low_launch_memory_reclaim_rejects_failed_root_command() -> None:
    calls = 0

    def run_command(command: list[str], **kwargs: object) -> object:
        nonlocal calls
        del command, kwargs
        calls += 1
        return type(
            "Completed",
            (),
            {"returncode": 1, "stderr": "distribution unavailable"},
        )()

    with pytest.raises(
        LaunchFailed,
        match=r"reclaim failed: distribution unavailable.*No launch input",
    ):
        live_dev._recover_low_launch_memory(
            threshold_mib=4096,
            distribution="Ubuntu-22.04",
            available_memory_mib=lambda: 2048,
            run_command=run_command,
        )

    assert calls == 1


def test_launch_memory_recovery_never_runs_for_sufficient_headroom() -> None:
    def unexpected_command(command: list[str], **kwargs: object) -> object:
        del command, kwargs
        raise AssertionError("root recovery must not run above the threshold")

    assert live_dev._recover_low_launch_memory(
        threshold_mib=4096,
        distribution="Ubuntu-22.04",
        available_memory_mib=lambda: 4096,
        run_command=unexpected_command,
    ) == (4096, 4096)


def test_launch_preflight_rejects_profile_drift_with_recovery_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = load_config(root / "config" / "live.yaml")
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

    with pytest.raises(LaunchFailed, match=r"view distance.*setup graphics"):
        _validate_launch_preconditions(
            config,
            process_names={"steam.exe"},
            available_physical_memory_mib=8192,
            settings_path=settings,
            renderer_path=renderer,
            steam_connection_log_path=steam_log,
        )


def test_doctor_is_the_only_non_launching_preflight_surface() -> None:
    args = live_dev.build_parser().parse_args(
        [
            "doctor",
            "--config",
            "config/live.yaml",
        ]
    )

    assert args.preflight_only is True


def test_launch_parser_accepts_explicit_existing_launcher_resume() -> None:
    args = live_dev.build_parser().parse_args(
        [
            "launch",
            "--config",
            "config/live.yaml",
            "--resume-launcher",
        ]
    )

    assert args.resume_launcher is True


def test_launch_parser_accepts_only_one_exact_startup_source() -> None:
    args = live_dev.build_parser().parse_args(
        [
            "launch",
            "--config",
            "config/live.yaml",
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
                "config/live.yaml",
                "--scenario",
                "fixture-a",
                "--game-start",
                "kae-03-broke-pair",
            ]
        )


def test_run_parser_combines_start_agent_and_control_options() -> None:
    args = live_dev.build_parser().parse_args(
        [
            "run",
            "--config",
            "config/live.yaml",
            "--game-start",
            "kae-02-funded-solo",
            "--campaign",
            "fresh-funded-solo",
            "--steps",
            "80",
            "--control",
            "live",
        ]
    )

    assert args.game_start == "kae-02-funded-solo"
    assert args.campaign == "fresh-funded-solo"
    assert args.continue_game is True
    assert args.preflight_only is False
    assert args.tts is True
    argv = _agent_argv(args, "combined-run")
    assert argv[argv.index("--steps") + 1] == "80"
    assert "--planning-mode" not in argv
    assert "--execute-live-actions" in argv
    assert "--acknowledge-native-assisted-control" in argv
    assert "--acknowledge-continuous-live" in argv
    assert "--tts" in argv


def test_run_parser_has_no_silent_live_run_mode() -> None:
    with pytest.raises(SystemExit):
        live_dev.build_parser().parse_args(
            [
                "run",
                "--config",
                "config/live.yaml",
                "--no-tts",
            ]
        )


def test_launch_and_run_starts_agent_only_after_launch_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def launch(_: object, *, manage_display_lease: bool = True) -> int:
        assert manage_display_lease is False
        calls.append("launch")
        return 0

    def run_agent(_: object, *, manage_display_lease: bool = True) -> int:
        assert manage_display_lease is False
        calls.append("agent")
        return 7

    monkeypatch.setattr(live_dev, "_launch", launch)
    monkeypatch.setattr(live_dev, "_run_agent", run_agent)
    monkeypatch.setattr(
        live_dev,
        "load_config",
        lambda _: type(
            "Config",
            (),
            {
                "launch": type(
                    "Launch",
                    (),
                    {"require_dual_display_topology": False},
                )()
            },
        )(),
    )
    args = type("Args", (), {"config": "config/live.yaml"})()

    assert live_dev._launch_and_run(args) == 7
    assert calls == ["launch", "agent"]

    calls.clear()

    async def failed_launch(
        _: object,
        *,
        manage_display_lease: bool = True,
    ) -> int:
        assert manage_display_lease is False
        calls.append("launch")
        return 4

    monkeypatch.setattr(live_dev, "_launch", failed_launch)
    assert live_dev._launch_and_run(args) == 4
    assert calls == ["launch"]


def test_launch_and_run_focus_display_owns_one_display_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class DisplayController:
        def validate_ready(self) -> None:
            calls.append("display-ready")

    class DisplayLease:
        def __enter__(self) -> None:
            calls.append("lease-enter")

        def __exit__(self, *_: object) -> None:
            calls.append("lease-exit")

    class Config:
        class launch:
            require_dual_display_topology = True

    async def launch(_: object, *, manage_display_lease: bool = True) -> int:
        calls.append(f"launch:{manage_display_lease}")
        return 0

    def run_agent(_: object, *, manage_display_lease: bool = True) -> int:
        calls.append(f"agent:{manage_display_lease}")
        return 0

    monkeypatch.setattr(live_dev, "load_config", lambda _: Config())
    monkeypatch.setattr(
        live_dev,
        "DisplayTopologyController",
        DisplayController,
    )
    monkeypatch.setattr(
        live_dev,
        "external_display_lease",
        lambda _: DisplayLease(),
    )
    monkeypatch.setattr(live_dev, "_launch", launch)
    monkeypatch.setattr(live_dev, "_run_agent", run_agent)

    args = type(
        "Args",
        (),
        {"config": "config/live.yaml", "focus_display": True},
    )()
    assert live_dev._launch_and_run(args) == 0
    assert calls == [
        "display-ready",
        "lease-enter",
        "launch:False",
        "agent:False",
        "lease-exit",
    ]


def test_launch_and_run_keeps_current_display_without_switching_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class DisplayController:
        def validate_ready(self) -> None:
            calls.append("display-ready")

    class Config:
        class launch:
            require_dual_display_topology = True

    async def launch(_: object, *, manage_display_lease: bool = True) -> int:
        calls.append(f"launch:{manage_display_lease}")
        return 0

    def run_agent(_: object, *, manage_display_lease: bool = True) -> int:
        calls.append(f"agent:{manage_display_lease}")
        return 0

    monkeypatch.setattr(live_dev, "load_config", lambda _: Config())
    monkeypatch.setattr(live_dev, "DisplayTopologyController", DisplayController)
    monkeypatch.setattr(
        live_dev,
        "external_display_lease",
        lambda _: (_ for _ in ()).throw(
            AssertionError("the default must not switch display topology")
        ),
    )
    monkeypatch.setattr(live_dev, "_launch", launch)
    monkeypatch.setattr(live_dev, "_run_agent", run_agent)

    args = live_dev.build_parser().parse_args(
        ["run", "--config", "config/live.yaml"]
    )
    assert live_dev._launch_and_run(args) == 0
    assert calls == [
        "display-ready",
        "launch:False",
        "agent:False",
    ]


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
            "nearby_entities": [
                NearbyEntity(
                    id=f"entity-{index}",
                    name="Barman" if index == 12 else f"Character {index}",
                    distance=float(index),
                )
                for index in range(13)
            ],
            "known_map_destinations": [
                KnownMapDestination(
                    id="town-squin",
                    name="Squin",
                    distance=4800.0,
                )
            ],
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

    assert "barman" not in payload
    assert payload["nearby_entity_count"] == 13
    assert len(payload["nearest_nearby_entities"]) == 12  # type: ignore[arg-type]
    assert "entity-12" not in {
        entity["id"]  # type: ignore[index]
        for entity in payload["nearest_nearby_entities"]  # type: ignore[union-attr]
    }
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
    assert payload["known_map_destinations"] == [
        {
            "id": "town-squin",
            "name": "Squin",
            "distance": 4800.0,
        }
    ]


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



def _run_args(*extra: str) -> object:
    return live_dev.build_parser().parse_args(
        ["run", "--config", "config/live.yaml", *extra]
    )


def test_run_defaults_to_plan_only_until_execution_is_asked_for() -> None:
    argv = _agent_argv(_run_args(), "run-1")
    assert "--acknowledge-continuous-live" not in argv
    assert "--execute-live-actions" not in argv


def test_run_omits_unspecified_config_overrides() -> None:
    argv = _agent_argv(_run_args(), "config-owned-run")

    assert "--planner" not in argv
    assert "--steps" not in argv


def test_run_passes_prompt_and_advisor_overrides() -> None:
    argv = _agent_argv(
        _run_args(
            "--prompt-file",
            "prompts/experimental/planning.md",
            "--advisor-corpus-file",
            "knowledge/experimental.yaml",
        ),
        "prompt-advisor-run",
    )

    assert (
        argv[argv.index("--prompt-file") + 1] == "prompts/experimental/planning.md"
    )
    assert (
        argv[argv.index("--advisor-corpus-file") + 1]
        == "knowledge/experimental.yaml"
    )


def test_dev_run_does_not_invent_a_scheduling_flag() -> None:
    """There is one schedule. `./dev` must not imply it can be chosen."""

    with pytest.raises(SystemExit):
        _run_args("--continuous")


def test_every_run_passes_tts_to_the_core_run() -> None:
    argv = _agent_argv(_run_args(), "spoken-run")

    assert "--tts" in argv


def test_run_passes_an_explicit_campaign_to_the_core_run() -> None:
    argv = _agent_argv(
        _run_args("--campaign", "ladle-css-01"),
        "campaign-run",
    )

    assert argv[argv.index("--campaign") + 1] == "ladle-css-01"


def test_run_live_control_passes_every_core_authority_gate() -> None:
    argv = _agent_argv(
        _run_args(
            "--control",
            "live",
        ),
        "run-4",
    )
    assert "--execute-live-actions" in argv
    assert "--acknowledge-native-assisted-control" in argv
    assert "--acknowledge-continuous-live" in argv


def test_run_rejects_unattested_manual_scenario_labels() -> None:
    with pytest.raises(SystemExit):
        _run_args("--scenario-id", "unattested-label")


def test_attested_run_forwards_only_the_attestation(
    tmp_path: Path,
) -> None:
    attestation = tmp_path / "current_attestation.json"
    argv = _agent_argv(
        _run_args("--scenario", "hub-outdoor-safe-day"),
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
            "config/live.yaml",
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
            "config/live.yaml",
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
            "config/live.yaml",
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
            "config/live.yaml",
            "install-starts",
        ]
    )
    assert live_dev._scenario_command(install) == 0
    output = capsys.readouterr().out
    assert "installed exact mod bytes" in output
    assert "verified exact" in output


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
            "config/live.yaml",
            "install-starts",
        ]
    )

    assert live_dev._scenario_command(args) == 4
    assert "Kenshi and FCS must be closed" in capsys.readouterr().err


def test_run_delegates_final_safety_to_the_core_run(
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
            AssertionError("run must not create a second cleanup owner")
        ),
    )

    result = live_dev._run_agent(
        _run_args("--control", "live")
    )

    assert result == 6
    assert len(captured) == 1
    assert "--execute-live-actions" in captured[0]


def test_run_ownership_overlay_follows_exclusive_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[list[str]] = []
    terminated: list[bool] = []

    class OverlayProcess:
        running = True

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            terminated.append(True)
            self.running = False

        def wait(self, timeout: float) -> int:
            assert timeout == 1.0
            return 0

        def kill(self) -> None:
            raise AssertionError("a responsive companion should terminate cleanly")

    monkeypatch.setattr(live_dev, "agent_main", lambda _: 0)
    monkeypatch.setattr(
        live_dev.subprocess,
        "Popen",
        lambda argv, **_: opened.append(argv) or OverlayProcess(),
    )

    # A plan-only run sends no input, so nothing owns the desktop and no
    # ownership companion is shown. The contrast used to be between two live
    # modes; there is only one now, and it always owns input.
    assert live_dev._run_agent(_run_args("--control", "plan-only")) == 0
    assert opened == []

    assert (
        live_dev._run_agent(
            _run_args("--control", "live")
        )
        == 0
    )
    assert len(opened) == 1
    assert terminated == [True]
    assert "--auto-close-seconds" not in opened[0]
    owner_pid_index = opened[0].index("--owner-pid")
    assert opened[0][owner_pid_index + 1] == str(os.getpid())


def test_run_owned_overlay_escalates_to_kill_when_terminate_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class StuckOverlayProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            calls.append("terminate")

        def wait(self, timeout: float) -> int:
            assert timeout == 1.0
            calls.append("wait")
            if "kill" not in calls:
                raise live_dev.subprocess.TimeoutExpired("overlay", timeout)
            return 0

        def kill(self) -> None:
            calls.append("kill")

    monkeypatch.setattr(live_dev, "agent_main", lambda _: 0)
    monkeypatch.setattr(
        live_dev.subprocess,
        "Popen",
        lambda *_args, **_kwargs: StuckOverlayProcess(),
    )

    assert live_dev._run_agent(_run_args("--control", "live")) == 0
    assert calls == ["terminate", "wait", "kill", "wait"]


def _title_snapshot(sequence: int = 20) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=sequence,
        identity_session_id="session-title",
        capabilities=[
            "control.continue_game",
            "control.load_game",
            "control.new_game",
        ],
        game=GameState(loaded=False),
        ui=UIState(active_screen="title"),
        controller_commands=NativeControlState(available=True),
    )


def _title_transition_ack(
    command: str,
    *,
    save_name: str = "",
    game_start_id: str = "",
) -> NativeCommandAcknowledgement:
    return NativeCommandAcknowledgement(
        command_id="cmd-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        command=command,  # type: ignore[arg-type]
        status=NativeCommandStatus.COMPLETED,
        reason="world_session_loaded",
        save_name=save_name,
        game_start_id=game_start_id,
        selected_character_ids=[],
        based_on_telemetry_sequence=20,
        acknowledged_at_telemetry_sequence=21,
        accepted_at_telemetry_sequence=21,
        terminal_at_telemetry_sequence=21,
    )


def _native_launch_config(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.yaml")
    return config.model_copy(
        update={
            "telemetry": config.telemetry.model_copy(
                update={"file": tmp_path / "telemetry.latest.json"}
            )
        }
    )


@pytest.mark.parametrize(
    ("command", "save_name", "game_start_id"),
    [
        ("continue_game", "", ""),
        ("load_game", "KenshiAgentScenario", ""),
        ("new_game", "", "kae-03-broke-pair"),
    ],
)
def test_title_transition_is_one_native_request_and_zero_desktop_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    save_name: str,
    game_start_id: str,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            live_dev,
            "new_command_id",
            lambda: "cmd-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        config = _native_launch_config(tmp_path)
        controller = LaunchController()
        loaded = launch_snapshot(22, paused=False).model_copy(
            update={
                "identity_session_id": "session-world",
                "controller_commands": NativeControlState(
                    available=True,
                    commands=[
                        _title_transition_ack(
                            command,
                            save_name=save_name,
                            game_start_id=game_start_id,
                        )
                    ],
                ),
            }
        )
        reader = LaunchTelemetry(_title_snapshot(), loaded)

        result = await _dispatch_native_startup_command(
            config,
            reader,  # type: ignore[arg-type]
            controller,
            command=command,  # type: ignore[arg-type]
            save_name=save_name,
            game_start_id=game_start_id,
            timeout=0.2,
        )

        request = NativeCommandRequest.model_validate_json(
            (tmp_path / "native_command.request.json").read_bytes()
        )
        evidence = json.loads(
            (tmp_path / "native_startup_transition.latest.json").read_text(
                encoding="utf-8"
            )
        )
        assert request.command == command
        assert request.save_name == save_name
        assert request.game_start_id == game_start_id
        assert request.selected_character_ids == []
        assert result.identity_session_id == "session-world"
        assert evidence["request"]["command_id"] == request.command_id
        assert evidence["acknowledgement"]["command_id"] == request.command_id
        assert evidence["title_snapshot"]["identity_session_id"] == "session-title"
        assert evidence["loaded_snapshot"]["identity_session_id"] == "session-world"
        assert controller.actions == []
        assert controller.lease_entries == 0

    import asyncio

    asyncio.run(scenario())


def test_title_transition_waits_for_fresh_native_title_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            live_dev,
            "new_command_id",
            lambda: "cmd-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        config = _native_launch_config(tmp_path)
        controller = LaunchController()
        stale = _title_snapshot(19)
        title = _title_snapshot(20)
        loaded = launch_snapshot(22, paused=False).model_copy(
            update={
                "identity_session_id": "session-world",
                "controller_commands": NativeControlState(
                    available=True,
                    commands=[
                        _title_transition_ack(
                            "load_game",
                            save_name="KenshiAgentScenario",
                        )
                    ],
                ),
            }
        )
        reader = LaunchTelemetry(stale, title, loaded)
        reader.stale_reads = 1

        result = await _dispatch_native_startup_command(
            config,
            reader,  # type: ignore[arg-type]
            controller,
            command="load_game",
            save_name="KenshiAgentScenario",
            timeout=0.5,
        )

        assert result.identity_session_id == "session-world"
        assert controller.actions == []
        assert controller.lease_entries == 0

    import asyncio

    asyncio.run(scenario())


def test_post_load_pause_is_native_and_never_acquires_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = _native_launch_config(tmp_path)
        controller = LaunchController()
        command_id = "cmd-" + "6" * 32
        monkeypatch.setattr(live_dev, "new_command_id", lambda: command_id)
        running = native_cleanup_snapshot(
            launch_snapshot(30, paused=False)
        )
        paused = native_cleanup_snapshot(
            launch_snapshot(31, paused=True),
            command_id=command_id,
            based_on_sequence=30,
        )
        reader = LaunchTelemetry(running, paused)

        result = await _ensure_native_launch_pause(
            config,
            reader,  # type: ignore[arg-type]
            controller,
            timeout=0.2,
        )

        request = NativeCommandRequest.model_validate_json(
            (tmp_path / "native_command.request.json").read_bytes()
        )
        assert request.command == "pause"
        assert request.paused is True
        assert result.game.paused is True
        assert controller.actions == []
        assert controller.lease_entries == 0

    import asyncio

    asyncio.run(scenario())


def test_supported_launch_contains_no_mouse_or_keyboard_delivery() -> None:
    source = inspect.getsource(live_dev._perform_launch)

    assert 'request_dialog_command(button_text="OK", control_id=1003)' in source

    for forbidden in (
        "ClickAction",
        "KeyAction",
        "HotkeyAction",
        "_click",
        "_execute_primitive",
        "input_lease",
    ):
        assert forbidden not in source


def test_game_executable_is_the_install_root_not_a_shortcut_or_archived_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = tmp_path / "Steam" / "steamapps" / "common" / "Kenshi"
    install.mkdir(parents=True)
    executable = install / "kenshi_x64.exe"
    executable.write_bytes(b"")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.delenv("KENSHI_AGENT_EXECUTABLE", raising=False)

    assert _game_executable() == executable


def test_game_executable_override_must_be_an_exact_exe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shortcut = tmp_path / "RE_Kenshi.lnk"
    shortcut.write_bytes(b"")
    monkeypatch.setenv("KENSHI_AGENT_EXECUTABLE", str(shortcut))

    with pytest.raises(FileNotFoundError, match="exact .exe"):
        _game_executable()
