import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.config import CaptureConfig, ControlsConfig, MacroConfig, RuntimeConfig
from kenshi_agent.control.base import InputController, PrimitiveInputAction, WindowRect
from kenshi_agent.env.live import LiveEnvironment
from kenshi_agent.models import (
    ActionReceipt,
    ClickAction,
    GameState,
    KeyAction,
    SkillAction,
    TelemetrySnapshot,
)
from kenshi_agent.skills import MacroRegistry
from kenshi_agent.telemetry import TelemetryRead


class PulseTelemetry:
    def __init__(self) -> None:
        self.paused = True
        self.sequence = 0

    def read(self) -> TelemetryRead:
        self.sequence += 1
        return TelemetryRead(
            snapshot=TelemetrySnapshot(
                sequence=self.sequence,
                captured_at=datetime.now(UTC),
                game=GameState(loaded=True, paused=self.paused),
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
    ) -> None:
        self.telemetry = telemetry
        self.actions: list[PrimitiveInputAction] = []
        self.emergency_after = emergency_after
        self.emergency_checks = 0
        self.user_input_after = user_input_after
        self.user_input_checks = 0

    def focus_window(self) -> None:
        return None

    async def execute(self, action: PrimitiveInputAction) -> ActionReceipt:
        self.actions.append(action)
        if isinstance(action, KeyAction) and action.key == "space":
            self.telemetry.paused = not self.telemetry.paused
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

    def client_rect(self) -> WindowRect:
        return WindowRect(left=0, top=0, right=1920, bottom=1080)


def movement_registry(
    *,
    pulse_seconds: float = 0.01,
    minimum: float | None = None,
    maximum: float | None = None,
) -> MacroRegistry:
    return MacroRegistry(
        {
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
    )


def live_environment(
    tmp_path: Path,
    telemetry: PulseTelemetry,
    controller: PulseController,
    registry: MacroRegistry,
) -> LiveEnvironment:
    return LiveEnvironment(
        run_id="pulse-test",
        run_dir=tmp_path,
        telemetry=telemetry,  # type: ignore[arg-type]
        controller=controller,
        macros=registry,
        runtime_config=RuntimeConfig(settle_seconds=0.0, objective="Explore nearby."),
        controls_config=ControlsConfig(post_input_delay_seconds=0.0),
        capture_config=CaptureConfig(enabled=False),
        execute_actions=True,
        emergency_stop_key="f12",
        available_skills=["move_visible_terrain"],
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
        assert "advanced Kenshi for 0.02s" in transition.receipt.message

    asyncio.run(scenario())


def test_emergency_stop_ends_pulse_after_repausing(tmp_path: Path) -> None:
    async def scenario() -> None:
        telemetry = PulseTelemetry()
        controller = PulseController(telemetry, emergency_after=3)
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

        with pytest.raises(RuntimeError, match="User input ended.*after re-pausing"):
            await environment.step(movement_action())

        assert telemetry.paused is True
        assert [action.kind for action in controller.actions][-2:] == ["key", "key"]

    asyncio.run(scenario())
