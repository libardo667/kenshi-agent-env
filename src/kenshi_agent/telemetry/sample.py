from __future__ import annotations

from datetime import UTC, datetime

from ..models import (
    CameraState,
    CharacterState,
    GameState,
    TelemetrySnapshot,
    UIState,
    Vec3,
)


def sample_snapshot() -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=1,
        captured_at=datetime.now(UTC),
        source="sample",
        capabilities=[
            "game.pause",
            "game.speed",
            "game.money",
            "game.location",
            "squad.basic",
            "squad.hunger",
        ],
        game=GameState(
            loaded=True,
            paused=True,
            speed_multiplier=1.0,
            day=1,
            hour=12,
            minute=0,
            elapsed_minutes=720.0,
            money=180,
            location_name="The Hub",
        ),
        camera=CameraState(position=Vec3(x=0, y=12, z=0), center=Vec3(x=0, y=0, z=0)),
        ui=UIState(selected_character_id="squad:0", client_width=1920, client_height=1080),
        squad=[
            CharacterState(
                id="squad:0",
                name="Wanderer",
                selected=True,
                alive=True,
                conscious=True,
                position=Vec3(x=0, y=0, z=0),
                movement_speed=18.0,
                hunger=250.0,
                food_items=1,
                first_aid_kits=1,
            )
        ],
    )
