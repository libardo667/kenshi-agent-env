from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from kenshi_agent import cli
from kenshi_agent.config import load_config
from kenshi_agent.models import TelemetrySnapshot
from kenshi_agent.telemetry import write_snapshot_atomic


def test_project_env_loads_openai_key_from_current_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-from-file\n", encoding="utf-8")

    loaded_path = cli._load_project_env()

    assert loaded_path == tmp_path / ".env"
    assert os.environ["OPENAI_API_KEY"] == "test-from-file"


def test_project_env_does_not_override_explicit_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-from-process")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-from-file\n", encoding="utf-8")

    cli._load_project_env()

    assert os.environ["OPENAI_API_KEY"] == "test-from-process"


def test_cli_loads_project_env_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-before-dispatch\n", encoding="utf-8")

    def fake_doctor(_: object) -> int:
        assert os.environ["OPENAI_API_KEY"] == "test-before-dispatch"
        return 0

    monkeypatch.setattr(cli, "_doctor", fake_doctor)

    assert cli.main(["doctor"]) == 0


def test_console_safe_escapes_characters_missing_from_stdout_encoding(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(encoding="cp1252"))

    assert cli._console_safe("spinner ⠸") == r"spinner \u2838"


def test_live_doctor_rejects_stale_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).resolve().parents[1]
    telemetry_path = tmp_path / "telemetry.json"
    write_snapshot_atomic(
        telemetry_path,
        TelemetrySnapshot(captured_at=datetime.now(UTC) - timedelta(seconds=30)),
    )
    config = load_config(root / "config" / "default.yaml")
    config = config.model_copy(
        update={
            "telemetry": config.telemetry.model_copy(
                update={"file": telemetry_path, "max_age_seconds": 1.0}
            )
        }
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    args = SimpleNamespace(config="unused", mode="live", planner="heuristic")

    assert cli._doctor(args) == 1
    output = capsys.readouterr().out
    assert "FAIL  telemetry_fresh" in output
