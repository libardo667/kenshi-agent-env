from pathlib import Path

from kenshi_agent.config import load_config


def test_default_config_loads_and_resolves_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")
    assert config.mode == "mock"
    assert config.paths.runs_dir == (root / "runs").resolve()
    assert config.paths.prompt_file.exists()
    assert config.telemetry.file == (root / "examples" / "telemetry.latest.json").resolve()
