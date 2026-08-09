from __future__ import annotations

from datetime import UTC, datetime

from ..core.telemetry import (
    CameraState,
    CharacterState,
    GameState,
    PlatoonState,
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
            "roster.basic",
            "roster.hunger",
            "platoons.membership",
            "platoons.active",
            "selection.complete",
            "primary.character",
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
        ui=UIState(client_width=1920, client_height=1080),
        primary_character_id="character:0",
        selected_character_ids=["character:0"],
        roster=[
            CharacterState(
                id="character:0",
                name="Wanderer",
                platoon_id="platoon:0",
                alive=True,
                conscious=True,
                position=Vec3(x=0, y=0, z=0),
                movement_speed=18.0,
                hunger=2.5,
                food_items=1,
                first_aid_kits=1,
            )
        ],
        platoons=[
            PlatoonState(
                id="platoon:0",
                name="Nameless",
                member_ids=["character:0"],
            )
        ],
        platoons_complete=True,
        active_platoon_id="platoon:0",
    )
