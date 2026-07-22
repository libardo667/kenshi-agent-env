from pathlib import Path

import pytest

from kenshi_agent.config import load_config


def test_default_config_loads_and_resolves_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("KENSHI_AGENT_MODEL", raising=False)
    config = load_config(root / "config" / "default.yaml")
    assert config.mode == "mock"
    assert config.planner.model == "gpt-5.6-terra"
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
        "pause_game",
        "open_map",
        "open_inventory",
        "focus_selected",
        "close_overlay",
        "move_visible_terrain",
        "move_on_map",
    }
    assert config.safety.max_primitive_actions_per_step == 2
    fine_bounds = config.macros["move_visible_terrain"].normalized_pointer_bounds
    map_bounds = config.macros["move_on_map"].normalized_pointer_bounds
    assert fine_bounds is not None and fine_bounds.contains(0.5, 0.5)
    assert map_bounds is not None and map_bounds.contains(0.5, 0.5)
    assert not map_bounds.contains(0.2, 0.5)


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
