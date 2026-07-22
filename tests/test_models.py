from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.models import ClickAction, Observation, TelemetrySnapshot, parse_action


def test_action_discriminator_parses_click() -> None:
    action = parse_action(
        {
            "kind": "click",
            "x": 0.25,
            "y": 0.75,
            "space": "normalized",
            "button": "left",
        }
    )
    assert isinstance(action, ClickAction)
    assert action.x == 0.25


def test_unknown_action_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_action({"kind": "wait", "seconds": 1, "surprise": True})


def test_observation_planner_payload_omits_screenshot_path() -> None:
    observation = Observation(
        run_id="run",
        step_index=0,
        mode="mock",
        telemetry=TelemetrySnapshot(),
        screenshot_path=Path("secret-frame.png"),
    )
    payload = observation.planner_payload()
    assert "secret-frame.png" not in payload
    assert '"run_id": "run"' in payload
