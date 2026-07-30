from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from kenshi_agent import cli
from kenshi_agent.config import load_config
from kenshi_agent.final_safe_state import (
    FinalSafeStateOutcome,
    FinalSafeStateStatus,
)
from kenshi_agent.models import (
    ControlMode,
    LiveContinuousPolicy,
    PlanningMode,
    TelemetrySnapshot,
)
from kenshi_agent.telemetry import write_snapshot_atomic


def test_project_env_loads_openai_key_from_current_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "Path", SimpleNamespace(cwd=lambda: tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-from-file\n", encoding="utf-8")

    loaded_path = cli._load_project_env()

    assert loaded_path == tmp_path / ".env"
    assert os.environ["OPENAI_API_KEY"] == "test-from-file"


def test_project_env_does_not_override_explicit_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "Path", SimpleNamespace(cwd=lambda: tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-from-process")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-from-file\n", encoding="utf-8")

    cli._load_project_env()

    assert os.environ["OPENAI_API_KEY"] == "test-from-process"


def test_cli_loads_project_env_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fake_load_project_env() -> Path:
        monkeypatch.setenv("OPENAI_API_KEY", "test-before-dispatch")
        return tmp_path / ".env"

    def fake_doctor(_: object) -> int:
        assert os.environ["OPENAI_API_KEY"] == "test-before-dispatch"
        return 0

    monkeypatch.setattr(cli, "_load_project_env", fake_load_project_env)
    monkeypatch.setattr(cli, "_doctor", fake_doctor)

    assert cli.main(["doctor"]) == 0


def test_console_safe_escapes_characters_missing_from_stdout_encoding(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdout", SimpleNamespace(encoding="cp1252"))

    assert cli._console_safe("spinner ⠸") == r"spinner \u2838"


def test_run_parser_accepts_optional_tts_mode() -> None:
    args = cli.build_parser().parse_args(["run", "--tts"])

    assert args.tts is True


def test_subprocess_planner_preserves_explicit_argv_without_shell_reparsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_subprocess_planner(
        command: str | list[str],
        *,
        timeout_seconds: float,
    ) -> object:
        captured["command"] = command
        captured["timeout_seconds"] = timeout_seconds
        return sentinel

    monkeypatch.setattr(cli, "SubprocessPlanner", fake_subprocess_planner)
    config = load_config(Path(__file__).resolve().parents[1] / "config" / "default.yaml")
    command = [
        r"C:\Users\levib\AppData\Local\KenshiAgent\python.exe",
        r"\\wsl.localhost\Ubuntu-22.04\home\levib\planner.py",
        "--bearing",
        "99.828",
    ]

    planner = cli._build_planner(
        config,
        SimpleNamespace(
            planner="subprocess",
            command=None,
            command_args=command,
        ),
    )

    assert planner is sentinel
    assert captured == {
        "command": command,
        "timeout_seconds": config.planner.timeout_seconds,
    }


@pytest.mark.skipif(os.name == "nt", reason="exercises the WSL/Linux rejection path")
def test_live_run_rejects_unsupported_platform_before_persisting_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")
    runs_dir = tmp_path / "runs"
    memory_db = tmp_path / "state" / "live-memory.sqlite3"
    config = config.model_copy(
        update={
            "paths": config.paths.model_copy(
                update={"runs_dir": runs_dir, "memory_db": memory_db}
            )
        }
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    args = cli.build_parser().parse_args(
        [
            "run",
            "--config",
            "unused",
            "--mode",
            "live",
            "--run-id",
            "unsupported-platform",
        ]
    )

    with pytest.raises(SystemExit, match="requires Windows"):
        asyncio.run(cli._run_command(args))

    assert not runs_dir.exists()
    assert not memory_db.exists()


@pytest.mark.parametrize("summary_success", [None, False, True])
def test_unverified_final_pause_dominates_episode_exit_code(
    summary_success: bool | None,
) -> None:
    outcome = FinalSafeStateOutcome(
        status=FinalSafeStateStatus.PAUSE_UNVERIFIED,
        reason="Causal pause confirmation was unavailable.",
    )

    assert cli._run_exit_code(summary_success, outcome) == 6


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


def test_run_without_any_override_returns_the_original_config() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")

    assert cli._apply_run_overrides(config, SimpleNamespace()) is config


def test_run_campaign_override_is_explicit_and_ephemeral() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.longform.yaml")
    assert config.memory.campaign_id is None

    overridden = cli._apply_run_overrides(
        config,
        SimpleNamespace(
            objective=None,
            planning_mode=None,
            campaign="ladle-css-01",
        ),
    )

    assert overridden.memory.campaign_id == "ladle-css-01"
    assert config.memory.campaign_id is None


def test_run_planning_mode_override_is_ephemeral() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")

    overridden = cli._apply_run_overrides(
        config,
        SimpleNamespace(objective=None, planning_mode="continuous"),
    )

    assert overridden.planning.mode is PlanningMode.CONTINUOUS
    assert config.planning.mode is PlanningMode.SINGLE_STEP


def test_run_scenario_override_is_typed_and_ephemeral() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")

    overridden = cli._apply_run_overrides(
        config,
        SimpleNamespace(
            objective=None,
            planning_mode=None,
            scenario_id="hub-outdoor-safe-day",
            save_id="hub-start-v1",
            scenario_environment="outdoor",
            scenario_danger="safe",
            scenario_economy="broke",
            scenario_party="solo",
            scenario_time_of_day="day",
        ),
    )

    assert overridden.runtime.scenario is not None
    assert overridden.runtime.scenario.model_dump(mode="json") == {
        "scenario_id": "hub-outdoor-safe-day",
        "save_id": "hub-start-v1",
        "environment": "outdoor",
        "danger": "safe",
        "economy": "broke",
        "party": "solo",
        "time_of_day": "day",
    }
    assert config.runtime.scenario is None
    assert overridden.runtime.scenario_attestation is None


def test_run_uses_fixture_attestation_as_scenario_source(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")
    attestation = tmp_path / "attestation.json"
    attestation.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scenario": {
                    "scenario_id": "hub-outdoor-safe-day",
                    "save_id": "hub-start-v1",
                    "environment": "outdoor",
                    "danger": "safe",
                    "economy": "broke",
                    "party": "solo",
                    "time_of_day": "day",
                },
                "fixture_digest": "a" * 64,
                "managed_save_name": "KenshiAgentScenario",
                "identity_session_id": "native-session-a",
                "loaded_sequence": 10,
                "verified_at": "2026-07-27T12:00:00+00:00",
                "observed": {
                    "selected_character_id": "character-0",
                    "indoors": False,
                    "in_combat": False,
                    "money": 20,
                    "party_size": 1,
                    "minute_of_day": 720,
                },
            }
        ),
        encoding="utf-8",
    )

    overridden = cli._apply_run_overrides(
        config,
        SimpleNamespace(
            objective=None,
            planning_mode=None,
            scenario_attestation=str(attestation),
        ),
    )

    assert overridden.runtime.scenario is not None
    assert overridden.runtime.scenario.scenario_id == "hub-outdoor-safe-day"
    assert overridden.runtime.scenario_attestation is not None
    assert overridden.runtime.scenario_attestation.fixture_digest == "a" * 64


def test_attested_scenario_refuses_parallel_manual_labels(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")
    path = tmp_path / "attestation.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="cannot be combined"):
        cli._apply_run_overrides(
            config,
            SimpleNamespace(
                objective=None,
                planning_mode=None,
                scenario_attestation=str(path),
                scenario_id="fabricated",
            ),
        )


def test_run_scenario_override_refuses_partial_declarations() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "default.yaml")

    with pytest.raises(SystemExit, match="all scenario fields"):
        cli._apply_run_overrides(
            config,
            SimpleNamespace(
                objective=None,
                planning_mode=None,
                scenario_id="hub-outdoor-safe-day",
            ),
        )


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
        acknowledge_continuous_live=True,
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
        acknowledge_continuous_live=False,
    )

    assert config.control.mode == ControlMode.INTERFACE_ONLY
    assert cli._live_actions_enabled(config, args)


def test_continuous_live_policy_requires_its_own_cli_acknowledgement() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "live.burnin.yaml")
    config = config.model_copy(
        update={
            "planning": config.planning.model_copy(
                update={
                    "mode": PlanningMode.CONTINUOUS,
                    "live_execution_policy": (
                        LiveContinuousPolicy.DIALOGUE_INTERACTION_V1
                    ),
                }
            )
        }
    )
    args = SimpleNamespace(
        execute_live_actions=True,
        acknowledge_native_assisted_control=True,
        acknowledge_continuous_live=False,
    )

    with pytest.raises(SystemExit, match="acknowledge-continuous-live"):
        cli._live_actions_enabled(config, args)

    args.acknowledge_continuous_live = True
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
