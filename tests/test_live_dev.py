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
    _plugin_ready,
    _steam_connection_state,
    _unique_visible_control,
    _validate_calibrated_client_rect,
    _validate_launch_preconditions,
    _wait_until,
)
from kenshi_agent.models import (
    ActionReceipt,
    CharacterState,
    GameState,
    HotkeyAction,
    KeyAction,
    NormalizedPointerBounds,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
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
