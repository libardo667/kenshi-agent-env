from pathlib import Path

import pytest

from kenshi_agent.config import (
    ControlsConfig,
    LaunchConfig,
    MemoryConfig,
    load_config,
)
from kenshi_agent.models import (
    ClickAction,
    ControlMode,
    MemoryRetrievalPolicy,
    PlanningMode,
    SkillAction,
)
from kenshi_agent.skills import MacroRegistry


def test_default_config_loads_and_resolves_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("KENSHI_AGENT_MODEL", raising=False)
    config = load_config(root / "config" / "default.yaml")
    assert config.mode == "mock"
    assert config.control.mode == ControlMode.INTERFACE_ONLY
    assert not config.control.native_assisted_actions_enabled
    assert config.planning.mode == PlanningMode.SINGLE_STEP
    assert config.planning.max_plan_steps == 4
    assert config.planning.max_actions_per_plan == 8
    assert config.planning.max_native_assisted_actions_per_plan == 0
    assert config.planning.stateful_movement_options_enabled
    assert config.planning.concurrent_option_planning_enabled
    assert config.planning.concurrent_option_planning_delay_seconds == 20.0
    assert config.memory.retrieval_policy is MemoryRetrievalPolicy.DETERMINISTIC
    assert config.safety.supervisor_enabled
    assert config.safety.supervisor_max_sequence_stalls == 3
    assert config.safety.supervisor_pause_timeout_seconds == 2.0
    assert config.planner.model == "gpt-5.6-luna"
    assert config.planner.reasoning_effort == "low"
    assert config.planner.openrouter_model == "openai/gpt-5.6-luna"
    assert config.paths.runs_dir == (root / "runs").resolve()
    assert config.paths.prompt_file.exists()
    assert config.telemetry.file == (root / "examples" / "telemetry.latest.json").resolve()


def test_unimplemented_semantic_retrieval_cannot_be_enabled_by_config() -> None:
    with pytest.raises(ValueError):
        MemoryConfig(retrieval_policy="semantic_mmr")  # type: ignore[arg-type]


def test_calibrated_client_dimensions_must_be_configured_together() -> None:
    with pytest.raises(ValueError, match="must be set together"):
        ControlsConfig(calibrated_client_width=1920)


def test_canonical_live_config_uses_windows_local_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("KENSHI_AGENT_TELEMETRY_DIR", raising=False)

    config = load_config(root / "config" / "live.yaml")

    assert config.telemetry.file == (tmp_path / "KenshiAgent" / "telemetry.latest.json")
    assert config.paths.memory_db == (tmp_path / "KenshiAgent" / "state" / "live-memory.sqlite3")
    assert config.capture.window_title_contains == "Kenshi 1.0."
    assert config.control.mode == ControlMode.NATIVE_ASSISTED


def test_canonical_live_config_accepts_telemetry_directory_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    override = tmp_path / "custom-telemetry"
    monkeypatch.setenv("KENSHI_AGENT_TELEMETRY_DIR", str(override))

    config = load_config(root / "config" / "live.yaml")

    assert config.telemetry.file == override / "telemetry.latest.json"


def test_canonical_live_config_has_no_selectable_execution_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    config = load_config(root / "config" / "live.yaml")

    assert config.planning.mode is PlanningMode.CONTINUOUS
    assert config.planner.kind == "openrouter"
    assert not hasattr(config.planning, "live_execution_policy")


def test_canonical_live_config_keeps_calibrated_host_and_input_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    config = load_config(root / "config" / "live.yaml")

    assert config.safety.live_actions_enabled
    assert config.control.mode == ControlMode.NATIVE_ASSISTED
    assert config.planning.mode is PlanningMode.CONTINUOUS
    assert config.control.native_assisted_actions_enabled
    assert config.safety.require_cli_execute_flag
    assert not set(config.safety.allow_action_kinds) & {
        "click",
        "key",
        "hotkey",
        "move_cursor",
        "scroll",
    }
    assert set(config.safety.allow_skills) == {
        "pause_game",
        "recenter_camera",
        "close_overlay",
        "approach_confirmed_vendor",
    }
    assert config.runtime.max_steps == 30
    assert config.planner.reasoning_effort == "low"
    assert config.planner.max_output_tokens_base == 4096
    assert config.planner.max_output_tokens_per_plan_step == 2048
    assert config.planner.max_output_tokens_ceiling == 12288
    assert config.planner.max_output_continuations == 2
    assert config.planner.model == "gpt-5.6-luna"
    assert config.planner.openrouter_provider_sort == "latency"
    assert config.controls.alt_tab_after_input
    assert config.controls.pause_skill == "pause_game"
    assert config.controls.unpause_skill == "unpause_game"
    assert config.controls.speed_keys == {1: "f2", 2: "f3", 3: "f4"}
    assert config.controls.pointer_mode == "relative"
    assert config.controls.relative_pointer_max_step_pixels == 12
    assert config.controls.relative_pointer_tolerance_pixels == 1
    assert config.controls.calibrated_client_width == 1920
    assert config.controls.calibrated_client_height == 1080
    assert config.launch.require_steam_logged_on
    assert config.launch.require_graphics_profile
    assert config.launch.graphics_profile_file == (
        root / "config" / "graphics" / "iris-xe-stability-v3.yaml"
    ).resolve()
    assert config.launch.require_dual_display_topology
    assert config.launch.monitor_gpu_tdr
    assert config.launch.min_free_physical_memory_mib == 4096
    assert config.launch.reclaim_wsl_cache_on_low_memory
    assert config.launch.wsl_cache_reclaim_settle_timeout_seconds == 45
    assert config.launch.wsl_cache_reclaim_poll_seconds == 1
    assert config.launch.post_load_health_seconds == 45
    assert config.runtime.objective is not None
    assert config.safety.max_primitive_actions_per_step == 4
    assert config.safety.allow_live_unpause_actions
    assert config.safety.supervisor_enabled
    assert config.safety.supervisor_max_sequence_stalls == 3
    assert config.safety.supervisor_sequence_stall_min_age_seconds == 1.0
    assert config.safety.supervisor_pause_timeout_seconds == 2.0
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
    assert config.macros["approach_confirmed_vendor"].requires_native_assisted
    approach_vendor = config.macros["approach_confirmed_vendor"].parsed_actions()
    assert len(approach_vendor) == 1
    assert approach_vendor[0].kind == "hotkey"
    assert approach_vendor[0].keys == ["ctrl", "shift", "f10"]
    continue_vendor = config.macros["continue_confirmed_vendor_approach"]
    assert continue_vendor.requires_native_assisted
    assert continue_vendor.movement_pulse_max_seconds == 8.0
    assert continue_vendor.parsed_actions() == []
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


def test_canonical_live_config_authorizes_semantic_actions_not_raw_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canonical config grants composition, not a wider input surface."""

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    config = load_config(root / "config" / "live.yaml")

    assert config.control.mode == ControlMode.NATIVE_ASSISTED
    assert config.planning.mode is PlanningMode.CONTINUOUS
    # Both live gates are still required before any input is emitted.
    assert config.safety.live_actions_enabled
    assert config.safety.require_cli_execute_flag
    assert config.safety.emergency_stop_key == "f12"

    kinds = set(config.safety.allow_action_kinds)
    assert {"approach_dialogue_target", "activate_visible_control"} <= kinds
    # Raw controller primitives are never live-allowlisted.
    assert not kinds & {"click", "key", "hotkey", "move_cursor", "scroll"}

    # The approach macro survives only to supply the audited native primitives.
    assert config.controls.native_approach_skill == "approach_confirmed_vendor"
    assert "approach_confirmed_vendor" in config.safety.allow_skills
    # The calibrated Barman recipe macros are not authorized here.
    assert not {
        "choose_show_goods",
        "inspect_shop_item",
        "buy_inspected_shop_item",
    } & set(config.safety.allow_skills)


def test_canonical_live_config_allowlists_every_affordance_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every operation materialized by an adapter must pass internal safety."""

    from kenshi_agent.affordances import affordance_operation_kinds
    from kenshi_agent.operation_definitions import OPERATION_DEFINITIONS

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    afforded_operations = affordance_operation_kinds()
    assert afforded_operations, "expected at least one affordance operation"

    config = load_config(root / "config" / "live.yaml")
    allowed = set(config.safety.allow_action_kinds)
    missing = sorted(afforded_operations - allowed)
    assert not missing, f"canonical live config does not allowlist: {missing}"
    controller_verified_max = max(
        contract.max_primitive_actions
        for contract in OPERATION_DEFINITIONS.values()
        if contract.kind in allowed and contract.controller_verified
    )
    assert (
        config.safety.max_controller_verified_primitive_actions_per_step
        >= controller_verified_max
    )
    assert not allowed & {"click", "key", "hotkey", "move_cursor", "scroll"}


def test_canonical_live_config_does_not_claim_a_specific_save_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    config = load_config(root / "config" / "live.yaml")

    assert config.memory.enabled
    assert config.memory.campaign_id is None
    assert not config.memory.ephemeral


def test_canonical_live_config_uses_an_explicit_reasoning_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("KENSHI_AGENT_OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("KENSHI_AGENT_REASONING_EFFORT", raising=False)

    config = load_config(root / "config" / "live.yaml")

    assert config.planner.openrouter_model == "google/gemini-3.1-flash-lite"
    assert config.planner.reasoning_effort == "low"
    assert config.planner.openrouter_require_parameters
    assert config.planner.include_screenshot


@pytest.mark.parametrize(
    "config_name",
    [
        "default.yaml",
        "live.yaml",
    ],
)
def test_every_memory_enabled_config_allows_the_cognitive_read(
    config_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    config = load_config(root / "config" / config_name)

    assert config.memory.enabled
    assert "recall_memory" in config.safety.allow_action_kinds


def test_wsl_cache_reclaim_requires_a_real_memory_floor() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "reclaim_wsl_cache_on_low_memory needs "
            "min_free_physical_memory_mib"
        ),
    ):
        LaunchConfig(reclaim_wsl_cache_on_low_memory=True)
