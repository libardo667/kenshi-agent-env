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


def test_protocol_mismatch_precedes_removed_field_validation(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    payload = TelemetrySnapshot().model_dump(mode="json")
    payload["protocol_version"] = "0.8.2"
    payload["world_targets"] = [
        {
            "id": "entity-copper",
            "name": "Copper Resource",
            "kind": "natural_resource",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "distance": 10.0,
            "context_actions": ["operate"],
            "default_task": "operate_machinery",
            "task_available": False,
            "task_probability": 0.0,
        }
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        TelemetryReadError,
        match="Telemetry protocol major 0 does not match required major 1",
    ):
        TelemetryReader(path, retries=1).read()


def test_reader_accepts_native_nearby_character_and_ui_signals(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    payload = TelemetrySnapshot(
        captured_at=datetime.now(UTC),
        source="kenshilib-plugin",
    ).model_dump(mode="json")
    payload["capabilities"] = [
        "game.location",
        "game.location.identity",
        "ui.inventory",
        "ui.dialogue",
        "nearby.characters",
    ]
    payload["game"] = {
        "loaded": True,
        "paused": True,
        "location_id": "entity-squin",
        "location_name": "Squin",
        "inside_town_walls": True,
    }
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
            "faction": "Holy Nation Outlaws",
            "disposition": "neutral",
            "distance": 12.5,
            "position": {"x": -100.0, "y": 25.0, "z": 80.0},
            "camera_bearing_degrees": -18.5,
            "screen_position": {"x": 0.45, "y": 0.35},
            "conscious": True,
        }
    ]
    payload["world_targets"] = [
        {
            "id": "entity-copper",
            "name": "Copper Resource",
            "kind": "natural_resource",
            "position": {"x": -80.0, "y": 20.0, "z": 60.0},
            "distance": 31.5,
            "context_actions": ["operate"],
            "default_task": "operate_machinery",
            "mining_resource_level": 0.8,
        }
    ]
    payload["known_map_destinations"] = [
        {
            "id": "entity-squin",
            "name": "Squin",
            "distance": 1300.0,
            "has_gates": True,
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
    assert result.snapshot.game.location_id == "entity-squin"
    assert result.snapshot.game.location_name == "Squin"
    assert result.snapshot.game.inside_town_walls is True
    assert result.snapshot.known_map_destinations[0].has_gates is True
    assert result.snapshot.nearby_entities[0].kind == "character"
    assert result.snapshot.nearby_entities[0].shop_inventory_owner is True
    assert result.snapshot.nearby_entities[0].is_squad_leader is True
    assert result.snapshot.active_shop_trader_count == 1
    assert result.snapshot.native_control.last_result == "issued"
    assert result.snapshot.native_control.last_target == "Bar Trader"
    assert result.snapshot.nearby_entities[0].position is not None
    assert result.snapshot.nearby_entities[0].position.x == -100.0
    assert result.snapshot.nearby_entities[0].camera_bearing_degrees == -18.5
    assert result.snapshot.nearby_entities[0].screen_position is not None
    assert result.snapshot.nearby_entities[0].screen_position.x == 0.45
    assert result.snapshot.nearby_entities[0].visible is None
    assert result.snapshot.world_targets[0].context_actions == ["operate"]
    assert result.snapshot.world_targets[0].mining_resource_level == 0.8
