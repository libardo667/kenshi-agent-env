from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from kenshi_agent import cli
from kenshi_agent.config import load_config
from kenshi_agent.models import ControlMode, PlanningMode, TelemetrySnapshot
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


def test_run_objective_override_is_ephemeral() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.burnin.yaml")
    original = config.runtime.objective

    overridden = cli._apply_run_overrides(
        config,
        SimpleNamespace(objective="Inspect the bar entrance."),
    )

    assert overridden.runtime.objective == "Inspect the bar entrance."
    assert config.runtime.objective == original


def test_run_planning_mode_override_is_ephemeral() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")

    overridden = cli._apply_run_overrides(
        config,
        SimpleNamespace(objective=None, planning_mode="continuous"),
    )

    assert overridden.planning.mode is PlanningMode.CONTINUOUS
    assert config.planning.mode is PlanningMode.SINGLE_STEP


def test_exclusive_input_session_keeps_kenshi_foreground() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.burnin.yaml")
    args = SimpleNamespace(exclusive_input_session=True, execute_live_actions=True)

    options = cli._controller_kwargs(config, args)

    assert options["polite_input_enabled"] is False
    assert options["restore_foreground_after_input"] is False
    assert options["restore_cursor_after_input"] is False
    assert options["alt_tab_after_input"] is False
    assert options["pointer_mode"] == "relative"


def test_exclusive_input_session_requires_live_execution_gate() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.burnin.yaml")
    args = SimpleNamespace(exclusive_input_session=True, execute_live_actions=False)

    with pytest.raises(SystemExit, match="requires --execute-live-actions"):
        cli._controller_kwargs(config, args)


def test_shared_input_session_preserves_configured_polite_controls() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.burnin.yaml")
    config = config.model_copy(
        update={"controls": config.controls.model_copy(update={"pointer_mode": "absolute"})}
    )
    args = SimpleNamespace(exclusive_input_session=False, execute_live_actions=True)

    options = cli._controller_kwargs(config, args)

    assert options["polite_input_enabled"] is True
    assert options["restore_foreground_after_input"] is True
    assert options["restore_cursor_after_input"] is True
    assert options["alt_tab_after_input"] is True


def test_relative_pointer_requires_exclusive_live_session() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.burnin.yaml")
    args = SimpleNamespace(exclusive_input_session=False, execute_live_actions=True)

    with pytest.raises(SystemExit, match="relative requires --exclusive-input-session"):
        cli._controller_kwargs(config, args)


def test_native_assisted_execution_requires_dedicated_cli_acknowledgement() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.burnin.yaml")
    args = SimpleNamespace(
        execute_live_actions=True,
        acknowledge_native_assisted_control=False,
    )

    with pytest.raises(SystemExit, match="acknowledge-native-assisted-control"):
        cli._live_actions_enabled(config, args)

    args.acknowledge_native_assisted_control = True
    assert cli._live_actions_enabled(config, args)


def test_interface_only_execution_never_requires_native_acknowledgement() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.example.yaml")
    config = config.model_copy(
        update={"safety": config.safety.model_copy(update={"live_actions_enabled": True})}
    )
    args = SimpleNamespace(
        execute_live_actions=True,
        acknowledge_native_assisted_control=False,
    )

    assert config.control.mode == ControlMode.INTERFACE_ONLY
    assert cli._live_actions_enabled(config, args)


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
