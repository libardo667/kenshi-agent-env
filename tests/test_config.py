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
    assert config.paths.memory_db == (
        tmp_path / "KenshiAgent" / "state" / "live-memory.sqlite3"
    )
    assert config.capture.window_title_contains == "Kenshi 1.0."


def test_live_example_accepts_telemetry_directory_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    override = tmp_path / "custom-telemetry"
    monkeypatch.setenv("KENSHI_AGENT_TELEMETRY_DIR", str(override))

    config = load_config(root / "config" / "live.example.yaml")

    assert config.telemetry.file == override / "telemetry.latest.json"


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
