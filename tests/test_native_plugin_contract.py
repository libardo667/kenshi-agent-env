from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPO_ROOT / "native" / "KenshiAgentTelemetry" / "KenshiAgentTelemetry.cpp"


def test_native_plugin_does_not_export_unvalidated_raw_getting_eaten_byte() -> None:
    source = PLUGIN_SOURCE.read_text(encoding="utf-8")

    assert "character->isGettingEaten" not in source
    assert "getting-eaten state" in source


def test_native_plugin_exports_nearby_character_and_ui_signals() -> None:
    source = PLUGIN_SOURCE.read_text(encoding="utf-8")

    assert "getCharactersWithinSphere" in source
    assert "ShopTraderConstructorHook" in source
    assert "ShopTraderDestructorHook" in source
    assert "GameWorldResetHook" in source
    assert "GetRealAddress(&GameWorld::resetGame)" in source
    assert "ResetSessionState();" in source
    assert "NEARBY_CHARACTER_RADIUS = 400.0f" in source
    assert source.count("NEARBY_CHARACTER_RADIUS") == 3
    assert "IsTrackedShopOwner(target)" in source
    assert "platoon->getIsTrader()" in source
    assert "platoon->getHasVendorList()" in source
    assert "platoon->getSquadLeader() == target" in source
    assert "target->hasDialogue()" in source
    assert "getPlayerTaskProbability" in source
    assert "PLAYER_TALK_TO" in source
    assert "IssueApproachConfirmedVendor" in source
    assert "newPlayerTaskSelectedCharacters" in source
    assert "control.approach_vendor" in source
    assert "VK_F10" in source
    assert "nearby.roles" in source
    assert "AppendVector3(json, targetPosition)" in source
    assert "target->isOnScreen" in source
    assert "target->getVisible()" in source
    assert "getViewMatrix()" in source
    assert "worldToScreenRel" in source
    assert "camera_bearing_degrees" in source
    assert "std::atan2(cameraX, -cameraZ)" in source
    assert "screen_position" in source
    assert "nearby.characters" in source
    assert "gui->isAnyInventoryWindowOpen()" in source
    assert "gui->dialogue->isVisible()" in source
    assert '"trade"' in source
    assert "geometry can still" in source
