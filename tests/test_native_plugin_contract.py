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
    assert "target->isATrader()" in source
    assert "AppendVector3(json, targetPosition)" in source
    assert "target->isOnScreen" in source
    assert "target->getVisible()" in source
    assert "getViewMatrix()" in source
    assert "worldToScreenRel" in source
    assert "screen_position" in source
    assert "nearby.characters" in source
    assert "gui->isAnyInventoryWindowOpen()" in source
    assert "gui->dialogue->isVisible()" in source
    assert '"trade"' in source
    assert "geometry can still" in source
