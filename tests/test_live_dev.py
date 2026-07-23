from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from kenshi_agent import live_dev
from kenshi_agent.config import ControlsConfig, load_config
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.live_dev import (
    LaunchInterrupted,
    _click,
    _validate_calibrated_client_rect,
)
from kenshi_agent.models import ActionReceipt


class LaunchController(InputController):
    def __init__(
        self,
        *,
        rect: WindowRect | None = None,
        human_input: bool = False,
        interrupt_inside_lease: bool = False,
    ) -> None:
        self.rect = rect or WindowRect(0, 0, 1920, 1080)
        self.human_input = human_input
        self.interrupt_inside_lease = interrupt_inside_lease
        self.actions: list[PrimitiveInputAction] = []
        self.lease_entries = 0

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
        return ActionReceipt(action=action, accepted=True, executed=True)

    def emergency_stop_pressed(self, key: str) -> bool:
        del key
        return False

    def continuous_user_input_detected(self) -> bool:
        return self.human_input

    def client_rect(self) -> WindowRect:
        return self.rect


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
