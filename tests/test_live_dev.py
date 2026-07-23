import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent import live_dev
from kenshi_agent.config import ControlsConfig, load_config
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.live_dev import (
    LaunchFailed,
    LaunchInterrupted,
    _click,
    _click_semantic_control,
    _disable_re_kenshi_startup_panel,
    _ensure_interrupted_safe_state,
    _plugin_ready,
    _unique_visible_control,
    _validate_calibrated_client_rect,
    _wait_until,
)
from kenshi_agent.models import (
    ActionReceipt,
    GameState,
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
    ) -> None:
        self.rect = rect or WindowRect(0, 0, 1920, 1080)
        self.human_input = human_input
        self.interrupt_inside_lease = interrupt_inside_lease
        self.actions: list[PrimitiveInputAction] = []
        self.safety_actions: list[PrimitiveInputAction] = []
        self.lease_entries = 0
        self.title = title

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

        assert outcome == "confirmed paused at telemetry sequence 11"
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

        assert outcome == "already confirmed paused at telemetry sequence 20"
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
            live_dev.ClickAction(x=0.65, y=0.3)
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
