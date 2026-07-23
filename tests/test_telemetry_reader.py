import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kenshi_agent.models import TelemetrySnapshot
from kenshi_agent.telemetry import TelemetryReader, TelemetryReadError, write_snapshot_atomic


def test_atomic_writer_and_reader(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    snapshot = TelemetrySnapshot(sequence=4, captured_at=datetime.now(UTC), source="test")
    write_snapshot_atomic(path, snapshot)
    result = TelemetryReader(path, max_age_seconds=5, retries=1).read()
    assert result.snapshot.sequence == 4
    assert not result.stale


def test_stale_snapshot_is_marked(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    snapshot = TelemetrySnapshot(captured_at=datetime.now(UTC) - timedelta(seconds=30))
    write_snapshot_atomic(path, snapshot)
    result = TelemetryReader(path, max_age_seconds=1, retries=1).read()
    assert result.stale
    assert result.age_seconds >= 29


def test_invalid_protocol_raises(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    write_snapshot_atomic(path, TelemetrySnapshot(protocol_version="1.0.0"))
    with pytest.raises(TelemetryReadError):
        TelemetryReader(path, require_protocol_major=0, retries=1).read()


def test_reader_accepts_native_nearby_character_and_ui_signals(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    payload = TelemetrySnapshot(
        captured_at=datetime.now(UTC),
        source="kenshilib-plugin",
    ).model_dump(mode="json")
    payload["capabilities"] = ["ui.inventory", "ui.dialogue", "nearby.characters"]
    payload["ui"] = {
        "active_screen": "trade",
        "modal_open": True,
        "dialogue_open": False,
    }
    payload["nearby_entities"] = [
        {
            "id": "nearby:0",
            "name": "Bar Trader",
            "kind": "character",
            "is_animal": False,
            "trader_squad": True,
            "has_vendor_list": True,
            "is_squad_leader": True,
            "has_dialogue": True,
            "shop_inventory_owner": True,
            "talk_task_available": True,
            "talk_task_probability": 1.0,
            "faction": "Holy Nation Outlaws",
            "disposition": "neutral",
            "distance": 12.5,
            "position": {"x": -100.0, "y": 25.0, "z": 80.0},
            "camera_bearing_degrees": -18.5,
            "screen_position": {"x": 0.45, "y": 0.35},
            "conscious": True,
        }
    ]
    payload["active_shop_trader_count"] = 1
    payload["native_control"] = {
        "available": True,
        "last_command_sequence": 2,
        "last_command": "approach_confirmed_vendor",
        "last_result": "issued",
        "last_target": "Bar Trader",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = TelemetryReader(path, max_age_seconds=5, retries=1).read()

    assert result.snapshot.ui.active_screen == "trade"
    assert result.snapshot.nearby_entities[0].kind == "character"
    assert result.snapshot.nearby_entities[0].shop_inventory_owner is True
    assert result.snapshot.nearby_entities[0].is_squad_leader is True
    assert result.snapshot.nearby_entities[0].talk_task_available is True
    assert result.snapshot.active_shop_trader_count == 1
    assert result.snapshot.native_control.last_result == "issued"
    assert result.snapshot.native_control.last_target == "Bar Trader"
    assert result.snapshot.nearby_entities[0].position is not None
    assert result.snapshot.nearby_entities[0].position.x == -100.0
    assert result.snapshot.nearby_entities[0].camera_bearing_degrees == -18.5
    assert result.snapshot.nearby_entities[0].screen_position is not None
    assert result.snapshot.nearby_entities[0].screen_position.x == 0.45
    assert result.snapshot.nearby_entities[0].visible is None
