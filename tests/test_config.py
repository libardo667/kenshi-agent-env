from pathlib import Path

import pytest

from kenshi_agent.config import load_config


def test_default_config_loads_and_resolves_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")
    assert config.mode == "mock"
    assert config.paths.runs_dir == (root / "runs").resolve()
    assert config.paths.prompt_file.exists()
    assert config.telemetry.file == (root / "examples" / "telemetry.latest.json").resolve()


def test_live_example_uses_windows_local_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    config = load_config(root / "config" / "live.example.yaml")

    assert config.telemetry.file == (tmp_path / "KenshiAgent" / "telemetry.latest.json")
    assert config.paths.memory_db == (
        tmp_path / "KenshiAgent" / "state" / "live-memory.sqlite3"
    )
    assert config.capture.window_title_contains == "Kenshi 1.0."
