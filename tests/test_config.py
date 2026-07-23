from pathlib import Path

import pytest

from kenshi_agent.config import load_config
from kenshi_agent.models import ClickAction, SkillAction
from kenshi_agent.skills import MacroRegistry


def test_default_config_loads_and_resolves_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("KENSHI_AGENT_MODEL", raising=False)
    config = load_config(root / "config" / "default.yaml")
    assert config.mode == "mock"
    assert config.planner.model == "gpt-5.6-luna"
    assert config.planner.reasoning_effort == "low"
    assert config.planner.openrouter_model == "openai/gpt-5.6-luna"
    assert config.paths.runs_dir == (root / "runs").resolve()
    assert config.paths.prompt_file.exists()
    assert config.telemetry.file == (root / "examples" / "telemetry.latest.json").resolve()


def test_live_example_uses_windows_local_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("KENSHI_AGENT_TELEMETRY_DIR", raising=False)

    config = load_config(root / "config" / "live.example.yaml")

    assert config.telemetry.file == (tmp_path / "KenshiAgent" / "telemetry.latest.json")
    assert config.paths.memory_db == (tmp_path / "KenshiAgent" / "state" / "live-memory.sqlite3")
    assert config.capture.window_title_contains == "Kenshi 1.0."


def test_live_example_accepts_telemetry_directory_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    override = tmp_path / "custom-telemetry"
    monkeypatch.setenv("KENSHI_AGENT_TELEMETRY_DIR", str(override))

    config = load_config(root / "config" / "live.example.yaml")

    assert config.telemetry.file == override / "telemetry.latest.json"


def test_live_burnin_profile_allows_only_audited_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    config = load_config(root / "config" / "live.burnin.yaml")

    assert config.safety.live_actions_enabled
    assert config.safety.require_cli_execute_flag
    assert set(config.safety.allow_action_kinds) == {"noop", "stop", "pause", "wait", "skill"}
    assert set(config.safety.allow_skills) == {
        "open_map",
        "zoom_map_in",
        "zoom_map_out",
        "open_inventory",
        "pause_game",
        "recenter_camera",
        "close_overlay",
        "clear_item_highlights",
        "pan_camera_forward",
        "pan_camera_backward",
        "pan_camera_left",
        "pan_camera_right",
        "orbit_camera_left",
        "orbit_camera_right",
        "move_visible_terrain",
        "move_on_map",
        "interact_visible_person",
        "approach_confirmed_vendor",
        "choose_show_goods",
        "inspect_shop_item",
        "buy_inspected_shop_item",
    }
    assert config.runtime.max_steps == 30
    assert config.planner.reasoning_effort == "xhigh"
    assert config.planner.model == "gpt-5.6-luna"
    assert config.planner.openrouter_provider_sort == "latency"
    assert config.controls.alt_tab_after_input
    assert config.controls.pause_skill == "pause_game"
    assert config.controls.unpause_skill == "unpause_game"
    assert config.controls.speed_keys == {1: "f2", 2: "f3", 3: "f4"}
    assert config.controls.pointer_mode == "relative"
    assert config.controls.relative_pointer_max_step_pixels == 12
    assert config.controls.relative_pointer_tolerance_pixels == 1
    assert config.runtime.objective is not None
    assert config.safety.max_primitive_actions_per_step == 4
    assert not config.safety.allow_live_unpause_actions
    assert config.safety.max_purchase_price == 750
    assert config.safety.min_money_after_purchase == 250
    assert config.safety.max_purchases_per_run == 1
    fine_bounds = config.macros["move_visible_terrain"].normalized_pointer_bounds
    map_bounds = config.macros["move_on_map"].normalized_pointer_bounds
    assert fine_bounds is not None and fine_bounds.contains(0.5, 0.5)
    assert map_bounds is not None and map_bounds.contains(0.5, 0.5)
    assert not map_bounds.contains(0.2, 0.5)
    assert config.macros["move_visible_terrain"].movement_pulse_seconds == 0.75
    assert config.macros["move_visible_terrain"].movement_pulse_min_seconds == 0.35
    assert config.macros["move_visible_terrain"].movement_pulse_max_seconds == 3.0
    assert config.macros["move_on_map"].movement_pulse_seconds == 2.0
    assert config.macros["move_on_map"].movement_pulse_min_seconds == 1.0
    assert config.macros["move_on_map"].movement_pulse_max_seconds == 8.0
    assert len(config.macros["move_on_map"].actions) == 2
    registry = MacroRegistry(config.macros)
    fine_move = registry.expand(
        SkillAction(name="move_visible_terrain", args={"x": 0.5, "y": 0.5})  # type: ignore[arg-type]
    )[0]
    map_move = registry.expand(
        SkillAction(name="move_on_map", args={"x": 0.5, "y": 0.5})  # type: ignore[arg-type]
    )[0]
    interact = registry.expand(
        SkillAction(name="interact_visible_person", args={"x": 0.5, "y": 0.5})  # type: ignore[arg-type]
    )[0]
    assert isinstance(fine_move, ClickAction)
    assert isinstance(map_move, ClickAction)
    assert isinstance(interact, ClickAction)
    assert fine_move.hold_seconds == map_move.hold_seconds == interact.hold_seconds == 0.12
    recenter_actions = config.macros["recenter_camera"].parsed_actions()
    assert [action.kind for action in recenter_actions] == ["key"]
    assert recenter_actions[0].key == "f"
    assert recenter_actions[0].hold_seconds == 0.04
    clear_highlights = config.macros["clear_item_highlights"].parsed_actions()
    assert len(clear_highlights) == 1
    assert clear_highlights[0].kind == "key"
    assert clear_highlights[0].key == "alt"
    assert config.macros["interact_visible_person"].movement_pulse_max_seconds == 6.0
    assert config.macros["approach_confirmed_vendor"].movement_pulse_max_seconds == 8.0
    approach_vendor = config.macros["approach_confirmed_vendor"].parsed_actions()
    assert len(approach_vendor) == 1
    assert approach_vendor[0].kind == "hotkey"
    assert approach_vendor[0].keys == ["ctrl", "shift", "f10"]
    show_goods = config.macros["choose_show_goods"].parsed_actions()
    assert len(show_goods) == 1
    assert isinstance(show_goods[0], ClickAction)
    assert show_goods[0].x == 0.50
    assert show_goods[0].y == 0.812
    inspect_item = registry.expand(
        SkillAction(name="inspect_shop_item", args={"x": 0.316, "y": 0.357})  # type: ignore[arg-type]
    )
    assert len(inspect_item) == 1
    assert inspect_item[0].kind == "move_cursor"
    buy_item = registry.expand(
        SkillAction(  # type: ignore[arg-type]
            name="buy_inspected_shop_item",
            args={"x": 0.316, "y": 0.357, "expected_price": 649},
        )
    )
    assert len(buy_item) == 1
    assert isinstance(buy_item[0], ClickAction)
    assert buy_item[0].button.value == "right"
    zoom_in = config.macros["zoom_map_in"].parsed_actions()[0]
    assert zoom_in.kind == "scroll"
    assert zoom_in.x == 0.534
    assert zoom_in.y == 0.505
    assert zoom_in.notches == 1
    pan_left = config.macros["pan_camera_left"].parsed_actions()
    assert [action.kind for action in pan_left] == ["key", "key"]
    assert pan_left[0].key == "f"
    assert pan_left[0].hold_seconds == 0.04
    assert pan_left[1].key == "a"
    assert pan_left[1].hold_seconds == 0.08
    orbit_right = config.macros["orbit_camera_right"].parsed_actions()
    assert [action.kind for action in orbit_right] == ["key", "key"]
    assert orbit_right[0].key == "f"
    assert orbit_right[0].hold_seconds == 0.04
    assert orbit_right[1].key == "e"
    assert orbit_right[1].hold_seconds == 0.25


def test_real_env_file_is_ignored_but_template_is_trackable() -> None:
    root = Path(__file__).resolve().parents[1]
    ignored_names = {
        line.strip()
        for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".env" in ignored_names
    assert ".env.example" not in ignored_names
    assert (root / ".env.example").is_file()
