import asyncio
from pathlib import Path

from operation_test_support import execute_operation

from kenshi_agent.config import MockConfig
from kenshi_agent.core.evidence import CameraRecoveryStatus
from kenshi_agent.core.operation import (
    PauseAction,
    RecoverCameraViewAction,
    SetSpeedAction,
    WaitAction,
)
from kenshi_agent.env.mock import MockEnvironment


def test_mock_environment_state_transitions(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment = MockEnvironment(MockConfig(random_events=False), tmp_path, "run")
        observation = await environment.reset()
        assert observation.telemetry is not None
        assert observation.telemetry.game.paused is True

        await execute_operation(environment, PauseAction(paused=False))
        await execute_operation(environment, SetSpeedAction(speed=3))
        transition = await execute_operation(environment, WaitAction(seconds=10))
        assert transition.observation.telemetry is not None
        assert transition.observation.telemetry.game.money == 180
        assert transition.observation.telemetry.game.elapsed_minutes == 50
        assert transition.observation.screenshot_path is not None
        assert transition.observation.screenshot_path.exists()

    asyncio.run(scenario())


def test_mock_set_speed_represents_a_running_playback_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment = MockEnvironment(MockConfig(random_events=False), tmp_path, "playback")
        observation = await environment.reset()
        assert observation.telemetry is not None
        assert observation.telemetry.game.paused is True

        transition = await execute_operation(environment, SetSpeedAction(speed=3))

        assert transition.observation.telemetry is not None
        assert transition.observation.telemetry.game.paused is False
        assert transition.observation.telemetry.game.speed_multiplier == 5.0

    asyncio.run(scenario())


def test_mock_camera_recovery_returns_the_typed_controller_receipt(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        environment = MockEnvironment(MockConfig(random_events=False), tmp_path, "camera")
        observation = await environment.reset()
        assert observation.telemetry is not None
        assert "camera.recovery" in observation.telemetry.capabilities

        transition = await execute_operation(environment, RecoverCameraViewAction())
        assert transition.receipt.primitive_actions == 0
        assert transition.receipt.semantic is not None
        evidence = transition.receipt.semantic.camera_recovery
        assert evidence is not None
        assert evidence.status in set(CameraRecoveryStatus)
        assert evidence.chosen_candidate == "mock_initial"
        assert evidence.candidates[0].screenshot_path.exists()

    asyncio.run(scenario())
