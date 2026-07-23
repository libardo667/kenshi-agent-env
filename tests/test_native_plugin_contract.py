from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPO_ROOT / "native" / "KenshiAgentTelemetry" / "KenshiAgentTelemetry.cpp"
ATOMIC_WRITER_SOURCE = (
    REPO_ROOT / "native" / "KenshiAgentTelemetry" / "AtomicJsonWriter.cpp"
)
PLUGIN_PROJECT = (
    REPO_ROOT
    / "native"
    / "KenshiAgentTelemetry"
    / "KenshiAgentTelemetry.vcxproj"
)


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
    assert "ProcessNativeCommandRequest" in source
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


def test_native_plugin_uses_session_scoped_validated_handle_identity() -> None:
    source = PLUGIN_SOURCE.read_text(encoding="utf-8")

    assert 'PROTOCOL_VERSION = "0.5.0"' in source
    assert "identity.stable_handles" in source
    assert "identity_session_id" in source
    assert "CreateProcessGeneration()" in source
    assert "++g_sessionGeneration;" in source
    assert "StableEntityId(const hand& handle)" in source
    assert "if (!handle.isValid())" in source
    assert "handle.containerSerial" in source
    assert "handle.serial" in source
    assert "SameHandleIdentity(*it, handle)" in source
    assert "selectedCharacters.find" not in source
    assert "selected_character_ids" in source
    assert "last_target_id" in source
    assert "squad:" not in source
    assert "nearby:" not in source


def test_native_plugin_requires_causal_exact_target_command_requests() -> None:
    source = PLUGIN_SOURCE.read_text(encoding="utf-8")

    assert 'PROTOCOL_VERSION = "0.5.0"' in source
    assert "native_command.request.json" in source
    assert "ProcessNativeCommandRequest" in source
    assert "FindExactConfirmedVendor" in source
    assert "FindNearestConfirmedVendor" not in source
    assert "based_on_revision.telemetry_sequence" in source
    assert "identity_session_id" in source
    assert "native_assisted" in source
    assert "selected_character_ids" in source
    assert "duplicate_command_id" in source
    assert "stale_revision" in source
    assert "selection_mismatch" in source
    assert "target_lifetime_changed" in source
    assert "target_role_invalid" in source
    assert "exact_dialogue_target_open" in source
    assert "acknowledgements" in source
    assert "MAX_NATIVE_ACKNOWLEDGEMENTS = 16" in source
    assert "MonitorActiveNativeCommand" in source


def test_native_plugin_exports_food_chain_authoritative_ui_and_time_sources() -> None:
    source = PLUGIN_SOURCE.read_text(encoding="utf-8")

    assert "#include <kenshi/gui/ToolTip.h>" in source
    assert "getTimeStamp_inGameHours().getTotalMinutes()" in source
    assert "game.time" in source
    assert "ui.dialogue.target" in source
    assert "ui.dialogue.options" in source
    assert "ui.tooltip" in source
    assert "TryGetDialogueTargetId" in source
    assert "replyTexts" in source
    assert "getCaption().asUTF8()" in source
    assert "gui->getToolTip()" in source
    assert "tooltip->getVisible()" in source
    assert "tooltip->caller->getAbsoluteCoord()" in source
    assert "tooltip_source_bounds" in source

    project = PLUGIN_PROJECT.read_text(encoding="utf-8")
    assert project.count("MyGUIEngine_x64.lib") == 2


def test_native_plugin_exports_bounded_visible_semantic_ui_controls() -> None:
    source = PLUGIN_SOURCE.read_text(encoding="utf-8")

    assert "#include <mygui/MyGUI_Gui.h>" in source
    assert "ui.visible_controls" in source
    assert "AppendVisibleUIControls" in source
    assert "MAX_VISIBLE_UI_CONTROLS = 64" in source
    assert "MAX_VISITED_UI_WIDGETS = 2048" in source
    assert "MAX_UI_WIDGET_DEPTH = 32" in source
    assert "getInheritedVisible()" in source
    assert "getInheritedEnabled()" in source
    assert "getAbsoluteCoord()" in source
    assert 'widget->castType<MyGUI::Button>(false)' in source


def test_native_sampler_recovers_from_transient_write_and_cpp_failures() -> None:
    plugin = PLUGIN_SOURCE.read_text(encoding="utf-8")
    writer = ATOMIC_WRITER_SOURCE.read_text(encoding="utf-8")

    assert "SamplingGuard samplingGuard(g_sampling);" in plugin
    assert "catch (const std::exception& exception)" in plugin
    assert "catch (...)" in plugin
    assert "maximumMoveAttempts = 4" in writer
    assert "ERROR_SHARING_VIOLATION" in writer
    assert "ERROR_LOCK_VIOLATION" in writer
