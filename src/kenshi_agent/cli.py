from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv

from .advisor import AdvisorSession, GuideCorpus, OpenRouterStrategyAdvisor
from .campaign import (
    CampaignScope,
    CampaignScopeError,
    CampaignScopeOrigin,
    resolve_campaign_scope,
)
from .config import AppConfig, load_config
from .control import Win32InputController
from .env import AgentEnvironment, LiveEnvironment, MockEnvironment, ReplayEnvironment
from .evals import evaluate_log
from .fieldbook import render_fieldbook_markdown
from .final_safe_state import FinalSafeStateOutcome, FinalSafeStateStatus
from .memory import (
    MemoryStore,
    read_only_campaigns,
    read_only_schema_version,
    read_only_store,
)
from .memory_compaction import (
    MemoryCompactionError,
    build_lossless_compaction_candidate,
)
from .models import (
    ControlMode,
    MemoryCompactionCandidate,
    PlanningMode,
    ScenarioIdentity,
)
from .overlay import show_overlay
from .planners import (
    HeuristicPlanner,
    OpenAIPlanner,
    OpenRouterPlanner,
    ScriptedPlanner,
    SubprocessPlanner,
)
from .planners.base import Planner
from .reflexes import ReflexEngine
from .reporting import ConsoleDecisionReporter
from .runtime import AgentRuntime
from .safety import ActionGuard
from .scenario_fixtures import (
    ScenarioFixtureError,
    load_scenario_attestation,
    load_scenario_fixture,
    validate_current_scenario,
)
from .schema_export import export_schemas
from .session_log import SessionLogger
from .skills import MacroRegistry
from .speech import SpeechUnavailableError, default_narrator
from .telemetry import TelemetryReader, write_snapshot_atomic
from .telemetry.sample import sample_snapshot


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _load_project_env() -> Path:
    env_file = Path.cwd() / ".env"
    load_dotenv(dotenv_path=env_file, override=False)
    return env_file


def _console_safe(value: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return value.encode(encoding, errors="backslashreplace").decode(encoding)


def _build_planner(config: AppConfig, args: argparse.Namespace) -> Planner:
    kind = args.planner or config.planner.kind
    if kind == "heuristic":
        return HeuristicPlanner()
    if kind == "scripted":
        if not args.script:
            raise SystemExit("--script is required for the scripted planner.")
        return ScriptedPlanner(Path(args.script).expanduser().resolve())
    if kind == "subprocess":
        command = getattr(args, "command", None)
        command_args = getattr(args, "command_args", None)
        if command and command_args:
            raise SystemExit(
                "Use either --command or repeated --command-arg, not both."
            )
        if not command and not command_args:
            raise SystemExit(
                "--command or repeated --command-arg is required for the "
                "subprocess planner."
            )
        selected_command: str | list[str]
        if command_args:
            selected_command = list(command_args)
        else:
            assert isinstance(command, str)
            selected_command = command
        return SubprocessPlanner(
            selected_command,
            timeout_seconds=config.planner.timeout_seconds,
        )
    if kind == "openai":
        return OpenAIPlanner(
            config.planner,
            config.paths.prompt_file,
            max_plan_steps=config.planning.max_plan_steps,
        )
    if kind == "openrouter":
        return OpenRouterPlanner(
            config.planner,
            config.paths.prompt_file,
            max_plan_steps=config.planning.max_plan_steps,
        )
    raise SystemExit(f"Unsupported planner kind: {kind}")


def _build_advisor(config: AppConfig) -> AdvisorSession | None:
    if not config.advisor.enabled:
        return None
    corpus = GuideCorpus.load(config.advisor.corpus_file)
    if config.advisor.provider == "openrouter":
        client = OpenRouterStrategyAdvisor(config.advisor)
    else:  # pragma: no cover - strict config validation owns this branch
        raise SystemExit(f"Unsupported advisor provider: {config.advisor.provider}")
    return AdvisorSession(config.advisor, corpus, client)


class _ControllerKwargs(TypedDict):
    focus_before_input: bool
    post_input_delay_seconds: float
    polite_input_enabled: bool
    idle_seconds_before_input: float
    max_wait_for_input_turn_seconds: float
    restore_foreground_after_input: bool
    restore_cursor_after_input: bool
    alt_tab_after_input: bool
    pointer_mode: str
    relative_pointer_max_step_pixels: int
    relative_pointer_tolerance_pixels: int
    relative_pointer_settle_seconds: float
    relative_pointer_max_attempts: int
    relative_pointer_warp_enabled: bool
    relative_pointer_warp_threshold_pixels: int
    relative_pointer_warp_offset_pixels: int


def _controller_kwargs(config: AppConfig, args: argparse.Namespace) -> _ControllerKwargs:
    exclusive = bool(args.exclusive_input_session)
    if exclusive and not args.execute_live_actions:
        raise SystemExit("--exclusive-input-session requires --execute-live-actions.")
    if args.execute_live_actions and config.controls.pointer_mode == "relative" and not exclusive:
        raise SystemExit(
            "controls.pointer_mode=relative requires --exclusive-input-session "
            "when live actions are enabled."
        )
    return {
        "focus_before_input": config.controls.focus_before_input,
        "post_input_delay_seconds": config.controls.post_input_delay_seconds,
        "polite_input_enabled": False if exclusive else config.controls.polite_input_enabled,
        "idle_seconds_before_input": config.controls.idle_seconds_before_input,
        "max_wait_for_input_turn_seconds": config.controls.max_wait_for_input_turn_seconds,
        "restore_foreground_after_input": (
            False if exclusive else config.controls.restore_foreground_after_input
        ),
        "restore_cursor_after_input": (
            False if exclusive else config.controls.restore_cursor_after_input
        ),
        "alt_tab_after_input": False if exclusive else config.controls.alt_tab_after_input,
        "pointer_mode": config.controls.pointer_mode,
        "relative_pointer_max_step_pixels": (config.controls.relative_pointer_max_step_pixels),
        "relative_pointer_tolerance_pixels": (config.controls.relative_pointer_tolerance_pixels),
        "relative_pointer_settle_seconds": (config.controls.relative_pointer_settle_seconds),
        "relative_pointer_max_attempts": config.controls.relative_pointer_max_attempts,
        "relative_pointer_warp_enabled": config.controls.relative_pointer_warp_enabled,
        "relative_pointer_warp_threshold_pixels": (
            config.controls.relative_pointer_warp_threshold_pixels
        ),
        "relative_pointer_warp_offset_pixels": (
            config.controls.relative_pointer_warp_offset_pixels
        ),
    }


def _apply_run_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    objective = getattr(args, "objective", None)
    planning_mode = getattr(args, "planning_mode", None)
    campaign = getattr(args, "campaign", None)
    scenario_values = {
        "scenario_id": getattr(args, "scenario_id", None),
        "save_id": getattr(args, "save_id", None),
        "environment": getattr(args, "scenario_environment", None),
        "danger": getattr(args, "scenario_danger", None),
        "economy": getattr(args, "scenario_economy", None),
        "party": getattr(args, "scenario_party", None),
        "time_of_day": getattr(args, "scenario_time_of_day", None),
    }
    supplied_scenario_values = [
        name for name, value in scenario_values.items() if value is not None
    ]
    attestation_path = getattr(args, "scenario_attestation", None)
    if attestation_path is not None and supplied_scenario_values:
        raise SystemExit(
            "--scenario-attestation cannot be combined with manual "  # mutation: diagnostic-only
            "scenario labels."  # mutation: diagnostic-only
        )
    if supplied_scenario_values and len(supplied_scenario_values) != len(
        scenario_values
    ):
        missing = sorted(set(scenario_values) - set(supplied_scenario_values))
        raise SystemExit(
            "A scenario declaration requires all scenario fields; "  # mutation: diagnostic-only
            "missing: "  # mutation: diagnostic-only
            + ", ".join(missing)  # mutation: diagnostic-only
        )
    scenario: ScenarioIdentity | None = None
    scenario_attestation = None
    if attestation_path is not None:
        try:
            scenario_attestation = load_scenario_attestation(
                Path(attestation_path).expanduser().resolve()
            )
        except ScenarioFixtureError as exc:
            raise SystemExit(str(exc)) from exc  # mutation: diagnostic-only
        scenario = scenario_attestation.scenario
    if supplied_scenario_values:
        try:
            scenario = ScenarioIdentity.model_validate(scenario_values)
        except ValueError as exc:
            raise SystemExit(  # mutation: diagnostic-only
                f"Invalid scenario declaration: {exc}"  # mutation: diagnostic-only
            ) from exc

    if (
        objective is None
        and planning_mode is None
        and campaign is None
        and scenario is None
    ):
        return config
    updates: dict[str, object] = {}
    runtime_updates: dict[str, object] = {}
    if objective is not None:
        runtime_updates["objective"] = objective
    if scenario is not None:
        runtime_updates["scenario"] = scenario
    if scenario_attestation is not None:
        runtime_updates["scenario_attestation"] = scenario_attestation
    if runtime_updates:
        updates["runtime"] = config.runtime.model_copy(update=runtime_updates)
    if planning_mode is not None:
        updates["planning"] = config.planning.model_copy(
            update={"mode": PlanningMode(planning_mode)}
        )
    if campaign is not None:
        updates["memory"] = type(config.memory).model_validate(
            {
                **config.memory.model_dump(),
                "campaign_id": campaign,
            }
        )
    return config.model_copy(update=updates)


def _live_actions_enabled(config: AppConfig, args: argparse.Namespace) -> bool:
    if not args.execute_live_actions:
        return False
    if not config.safety.live_actions_enabled:
        raise SystemExit(
            "--execute-live-actions was supplied, but safety.live_actions_enabled is false."
        )
    if config.control.mode == ControlMode.NATIVE_ASSISTED:
        if not config.control.native_assisted_actions_enabled:
            raise SystemExit(
                "Native-assisted execution is disabled by control.native_assisted_actions_enabled."
            )
        if not args.acknowledge_native_assisted_control:
            raise SystemExit(
                "Native-assisted live execution requires --acknowledge-native-assisted-control."
            )
    if (
        config.planning.mode == PlanningMode.CONTINUOUS
        and config.planning.live_execution_policy.value != "disabled"
        and not args.acknowledge_continuous_live
    ):
        raise SystemExit(
            "Continuous live execution requires --acknowledge-continuous-live."
        )
    return True


def _validate_run_platform(config: AppConfig, args: argparse.Namespace) -> None:
    mode = args.mode or config.mode
    if config.runtime.scenario_attestation is not None and mode != "live":
        raise SystemExit(
            "Fixture-attested scenarios are valid only for a live Kenshi run."
        )
    if mode == "live" and os.name != "nt":
        raise SystemExit(
            "Live mode requires Windows. From WSL, use the supported ./dev journey "
            "launcher."
        )


def _validate_attested_live_scenario(
    config: AppConfig,
    args: argparse.Namespace,
) -> None:
    attestation = config.runtime.scenario_attestation
    if attestation is None:
        return
    raw_path = getattr(args, "scenario_attestation", None)
    if raw_path is None:
        raise SystemExit(
            "A fixture-attested live run requires its current attestation path."
        )
    path = Path(raw_path).expanduser().resolve()
    try:
        manifest = load_scenario_fixture(
            path.parent,
            attestation.scenario.scenario_id,
        )
        result = TelemetryReader(
            config.telemetry.file,
            max_age_seconds=config.telemetry.max_age_seconds,
            retries=config.telemetry.read_retries,
            retry_delay_seconds=config.telemetry.retry_delay_seconds,
            require_protocol_major=config.telemetry.require_protocol_major,
        ).read()
        if result.stale:
            raise ScenarioFixtureError(
                "Fixture-attested run requires fresh current telemetry."
            )
        validate_current_scenario(
            attestation,
            manifest,
            result.snapshot,
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def _run_exit_code(
    summary_success: bool | None,
    final_safe_state: FinalSafeStateOutcome | None,
) -> int:
    if (
        final_safe_state is not None
        and final_safe_state.status is FinalSafeStateStatus.PAUSE_UNVERIFIED
    ):
        return 6
    return 0 if summary_success is not False else 2


def _build_environment(
    config: AppConfig,
    args: argparse.Namespace,
    *,
    run_id: str,
    run_dir: Path,
    macros: MacroRegistry,
) -> AgentEnvironment:
    mode = args.mode or config.mode
    if mode == "mock":
        return MockEnvironment(
            config.mock,
            run_dir / "frames",
            run_id,
            control_mode=config.control.mode,
        )
    if mode == "replay":
        if not args.log:
            raise SystemExit("--log is required for replay mode.")
        return ReplayEnvironment(Path(args.log).expanduser().resolve())
    if mode == "live":
        if os.name != "nt":
            raise SystemExit("Live mode requires Windows.")
        execute_actions = _live_actions_enabled(config, args)
        controller = Win32InputController(
            config.capture.window_title_contains,
            **_controller_kwargs(config, args),
        )
        telemetry = TelemetryReader(
            config.telemetry.file,
            max_age_seconds=config.telemetry.max_age_seconds,
            retries=config.telemetry.read_retries,
            retry_delay_seconds=config.telemetry.retry_delay_seconds,
            require_protocol_major=config.telemetry.require_protocol_major,
        )
        return LiveEnvironment(
            run_id=run_id,
            run_dir=run_dir,
            telemetry=telemetry,
            controller=controller,
            macros=macros,
            runtime_config=config.runtime,
            controls_config=config.controls,
            capture_config=config.capture,
            execute_actions=execute_actions,
            emergency_stop_key=config.safety.emergency_stop_key,
            final_pause_timeout_seconds=(
                config.safety.supervisor_pause_timeout_seconds
            ),
            available_skills=config.safety.allow_skills,
            control_mode=config.control.mode,
        )
    raise SystemExit(f"Unsupported environment mode: {mode}")


def _inspect_memory(args: argparse.Namespace) -> int:
    """Read the continuity store and print it. Writes nothing, ever.

    An operator auditing what an agent believes must be able to do so without
    the act of looking changing anything - including opening a campaign that
    did not previously exist.
    """

    config = load_config(args.config)
    path = config.paths.memory_db
    if not path.exists():
        print(f"No continuity database at {path}.")
        return 0

    campaigns = read_only_campaigns(path)
    if args.campaign is None:
        print(f"{path} (schema {read_only_schema_version(path)})")
        for campaign_id, origin, created_at in campaigns:
            print(f"  {campaign_id}\t{origin}\tcreated {created_at}")
        if not campaigns:
            print("  (no campaigns)")
        return 0

    known = {campaign_id for campaign_id, _, _ in campaigns}
    if args.campaign not in known:
        print(f"No campaign {args.campaign!r} in {path}.")
        return 1

    with read_only_store(path, args.campaign) as store:
        if args.memory_id is not None:
            record = store.get(args.memory_id)
            if record is None:
                print(f"No memory {args.memory_id!r} in campaign {args.campaign!r}.")
                return 1
            print(json.dumps(record.model_dump(mode="json"), indent=2))
            for entry in store.history(args.memory_id):
                print(json.dumps(entry.model_dump(mode="json"), indent=2))
            return 0
        records = store.all_records()[: args.limit]
        print(f"campaign {args.campaign}: {len(records)} shown, {store.event_count()} events")
        for record in records:
            print(
                f"  {record.memory_id}\t{record.status.value}\t{record.kind.value}"
                f"\tsalience={record.salience:.2f}"
                f"\treinforced={record.reinforcement_count}\t{record.content[:60]}"
            )
    return 0


def _compact_memory(args: argparse.Namespace) -> int:
    """Propose read-only, or apply one exact previously inspected candidate."""

    config = load_config(args.config)
    path = config.paths.memory_db
    if not path.exists():
        print(f"No continuity database at {path}.", file=sys.stderr)
        return 1
    version = read_only_schema_version(path)
    if version is None or version < 4:
        print(
            f"{path} uses schema {version}; open it once with the current "
            "runtime before compacting.",
            file=sys.stderr,
        )
        return 1
    campaign_rows = read_only_campaigns(path)
    origins = {
        campaign_id: CampaignScopeOrigin(origin)
        for campaign_id, origin, _ in campaign_rows
    }
    if args.campaign not in origins:
        print(
            f"No campaign {args.campaign!r} in {path}.",
            file=sys.stderr,
        )
        return 1

    try:
        if args.apply_candidate is None:
            with read_only_store(path, args.campaign) as memories:
                records = []
                for memory_id in args.sources:
                    record = memories.get(memory_id)
                    if record is None:
                        raise MemoryCompactionError(
                            f"No memory {memory_id!r} exists in campaign "
                            f"{args.campaign!r}."
                        )
                    records.append(record)
                candidate = build_lossless_compaction_candidate(records)
            print(candidate.model_dump_json(indent=2))
            return 0

        candidate_path = Path(args.apply_candidate).expanduser().resolve()
        candidate = MemoryCompactionCandidate.model_validate_json(
            candidate_path.read_text(encoding="utf-8")
        )
        if candidate.campaign_id != args.campaign:
            raise MemoryCompactionError(
                "The inspected candidate belongs to another campaign."
            )
        with MemoryStore(
            path,
            CampaignScope(
                campaign_id=args.campaign,
                origin=origins[args.campaign],
            ),
        ) as memories:
            replacement = memories.compact(_new_run_id(), candidate)
        print(
            json.dumps(
                {
                    "candidate": candidate.model_dump(mode="json"),
                    "replacement": replacement.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        return 0
    except (MemoryCompactionError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"Compaction refused: {exc}", file=sys.stderr)
        return 1


def _inspect_fieldbook(args: argparse.Namespace) -> int:
    """Inspect the canonical fieldbook through read-only SQLite queries."""

    config = load_config(args.config)
    path = config.paths.memory_db
    if not path.exists():
        print(f"No continuity database at {path}.")
        return 0
    version = read_only_schema_version(path)
    campaigns = read_only_campaigns(path)
    if args.campaign is None:
        print(f"{path} (schema {version})")
        for campaign_id, origin, created_at in campaigns:
            print(f"  {campaign_id}\t{origin}\tcreated {created_at}")
        if not campaigns:
            print("  (no campaigns)")
        return 0
    if version is None or version < 4:
        print(
            f"{path} uses schema {version}; open it once with the current "
            "runtime to migrate before inspecting the fieldbook."
        )
        return 1
    known = {campaign_id for campaign_id, _, _ in campaigns}
    if args.campaign not in known:
        print(f"No campaign {args.campaign!r} in {path}.")
        return 1

    with read_only_store(path, args.campaign) as store:
        if args.markdown:
            print(render_fieldbook_markdown(store.fieldbook), end="")
            return 0
        if args.project_id is not None:
            project = store.fieldbook.get_project(args.project_id)
            if project is None:
                print(
                    f"No fieldbook project {args.project_id!r} in "
                    f"campaign {args.campaign!r}."
                )
                return 1
            document = {
                "project": project.model_dump(mode="json"),
                "entries": [
                    entry.model_dump(mode="json")
                    for entry in store.fieldbook.entries(args.project_id)
                ],
                "history": [
                    entry.model_dump(mode="json")
                    for entry in store.fieldbook.history(args.project_id)
                ],
            }
            print(json.dumps(document, indent=2))
            return 0
        if args.query is not None:
            result = store.fieldbook.read(
                project_id=None,
                query=args.query,
                limit=args.limit,
            )
            print(result.model_dump_json(indent=2))
            return 0
        projects = store.fieldbook.list_projects(limit=args.limit)
        print(f"campaign {args.campaign}: {len(projects)} projects shown")
        for project_index in projects:
            marker = "*" if project_index.selected else " "
            print(
                f"{marker} {project_index.project_id}\t{project_index.status.value}"
                f"\t{project_index.kind.value}\tentries={project_index.entry_count}"
                f"\t{project_index.title}"
            )
    return 0


async def _run_command(args: argparse.Namespace) -> int:
    config = _apply_run_overrides(load_config(args.config), args)
    _validate_run_platform(config, args)
    _validate_attested_live_scenario(config, args)
    run_id = args.run_id or _new_run_id()
    run_dir = config.paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    macros = MacroRegistry(config.macros)
    logger = SessionLogger(run_dir / "events.jsonl", run_id)
    memory = None
    reporter: ConsoleDecisionReporter | None = None
    if config.memory.enabled:
        try:
            campaign = resolve_campaign_scope(
                config.memory,
                mode=args.mode or config.mode,
                run_id=run_id,
                scenario=config.runtime.scenario,
            )
        except CampaignScopeError as exc:
            raise SystemExit(str(exc)) from exc
        memory = MemoryStore(config.paths.memory_db, campaign)
        logger.write(
            "campaign_scope",
            payload={
                "campaign_id": campaign.campaign_id,
                "origin": campaign.origin.value,
                "schema_version": memory.schema_version,
            },
        )
    try:
        planner_kind = args.planner or config.planner.kind
        planner = _build_planner(config, args)
        advisor = _build_advisor(config)
        environment = _build_environment(
            config,
            args,
            run_id=run_id,
            run_dir=run_dir,
            macros=macros,
        )
        run_control_mode = (
            environment.control_mode
            if isinstance(environment, ReplayEnvironment)
            else config.control.mode
        )
        narrator = None
        if args.tts:
            try:
                narrator = default_narrator()
            except SpeechUnavailableError as exc:
                raise SystemExit(f"TTS mode is unavailable: {exc}") from exc
        if config.runtime.decision_stream or narrator is not None:
            reporter = ConsoleDecisionReporter(
                run_id=run_id,
                planner_name=planner_kind,
                model_name=(
                    config.planner.openrouter_model
                    if planner_kind == "openrouter"
                    else config.planner.model
                    if planner_kind == "openai"
                    else None
                ),
                control_mode=run_control_mode,
                narrator=narrator,
            )
        runtime = AgentRuntime(
            run_id=run_id,
            environment=environment,
            planner=planner,
            advisor=advisor,
            guard=ActionGuard(
                config.safety,
                macros,
                control_mode=run_control_mode,
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=memory,
            memory_limit=config.memory.max_recalled_memories,
            entity_memory_limit=config.memory.max_entity_recalled_memories,
            commitment_memory_limit=config.memory.max_commitment_memories,
            hypothesis_memory_limit=config.memory.max_hypothesis_memories,
            fieldbook_project_limit=config.memory.max_fieldbook_projects,
            memory_retrieval_policy=config.memory.retrieval_policy,
            minimum_memory_salience=config.memory.minimum_salience,
            action_outcome_limit=config.runtime.observation_memory_limit,
            control_mode=run_control_mode,
            planning_config=config.planning,
            log_full_observations=config.runtime.log_full_observations,
            scenario=config.runtime.scenario,
            scenario_attestation=config.runtime.scenario_attestation,
            reporter=reporter,
        )
        summary = await runtime.run(
            max_steps=args.steps or config.runtime.max_steps,
            seed=args.seed,
        )
        output = {
            "run_id": summary.run_id,
            "run_dir": str(run_dir),
            "control_mode": summary.control_mode.value,
            "planning_mode": config.planning.mode.value,
            "scenario": (
                config.runtime.scenario.model_dump(mode="json")
                if config.runtime.scenario is not None
                else None
            ),
            "steps_completed": summary.steps_completed,
            "terminated": summary.terminated,
            "success": summary.success,
            "stop_reason": summary.stop_reason,
            "final_safe_state": (
                runtime.final_safe_state.model_dump(mode="json")
                if runtime.final_safe_state is not None
                else None
            ),
        }
        print(json.dumps(output, indent=2))
        return _run_exit_code(summary.success, runtime.final_safe_state)
    finally:
        if reporter is not None:
            reporter.close()
        logger.close()
        if memory is not None:
            memory.close()


def _doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    checks: list[tuple[str, bool, str]] = []
    checks.append(("python", sys.version_info >= (3, 11), platform.python_version()))
    checks.append(("prompt", config.paths.prompt_file.exists(), str(config.paths.prompt_file)))
    checks.append(("runs_dir", True, str(config.paths.runs_dir)))
    checks.append(("mode", True, args.mode or config.mode))
    checks.append(("control_mode", True, config.control.mode.value))
    checks.append(("planning_mode", True, config.planning.mode.value))
    if config.advisor.enabled:
        checks.append(
            (
                "advisor_corpus",
                config.advisor.corpus_file.exists(),
                str(config.advisor.corpus_file),
            )
        )
        checks.append(
            (
                "advisor_openrouter_api_key",
                bool(os.environ.get("OPENROUTER_API_KEY")),
                "environment",
            )
        )
        try:
            import openai  # noqa: F401

            checks.append(("advisor_openai_package", True, "installed"))
        except ImportError:
            checks.append(
                ("advisor_openai_package", False, "pip install -e '.[openai]'")
            )
    if (args.mode or config.mode) == "live":
        checks.append(("windows", os.name == "nt", platform.platform()))
        checks.append(
            ("telemetry_file", config.telemetry.file.exists(), str(config.telemetry.file))
        )
        if config.telemetry.file.exists():
            try:
                read = TelemetryReader(
                    config.telemetry.file,
                    max_age_seconds=config.telemetry.max_age_seconds,
                    retries=1,
                    require_protocol_major=config.telemetry.require_protocol_major,
                ).read()
                checks.append(
                    (
                        "telemetry_parse",
                        True,
                        f"protocol={read.snapshot.protocol_version} "
                        f"age={read.age_seconds:.2f}s stale={read.stale}",
                    )
                )
                checks.append(
                    (
                        "telemetry_fresh",
                        not read.stale,
                        f"age={read.age_seconds:.2f}s "
                        f"maximum={config.telemetry.max_age_seconds:.2f}s",
                    )
                )
            except Exception as exc:
                checks.append(("telemetry_parse", False, f"{type(exc).__name__}: {exc}"))
        if os.name == "nt":
            try:
                controller = Win32InputController(
                    config.capture.window_title_contains,
                    focus_before_input=False,
                )
                rect = controller.client_rect()
                checks.append(("kenshi_window", True, f"{rect.width}x{rect.height}"))
            except Exception as exc:
                checks.append(("kenshi_window", False, f"{type(exc).__name__}: {exc}"))
    planner_kind = args.planner or config.planner.kind
    if planner_kind in {"openai", "openrouter"}:
        key_name = "OPENAI_API_KEY" if planner_kind == "openai" else "OPENROUTER_API_KEY"
        checks.append((key_name.lower(), bool(os.environ.get(key_name)), "environment"))
        try:
            import openai  # noqa: F401

            checks.append(("openai_package", True, "installed"))
        except ImportError:
            checks.append(("openai_package", False, "pip install -e '.[openai]'"))
    width = max(len(name) for name, _, _ in checks)
    for name, passed, detail in checks:
        line = f"{'PASS' if passed else 'FAIL'}  {name:<{width}}  {detail}"
        print(_console_safe(line))
    return 0 if all(passed for _, passed, _ in checks) else 1


def _validate_telemetry(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    read = TelemetryReader(
        Path(args.file).resolve() if args.file else config.telemetry.file,
        max_age_seconds=config.telemetry.max_age_seconds,
        retries=config.telemetry.read_retries,
        retry_delay_seconds=config.telemetry.retry_delay_seconds,
        require_protocol_major=config.telemetry.require_protocol_major,
    ).read()
    print(read.snapshot.model_dump_json(indent=2))
    print(f"age_seconds={read.age_seconds:.3f} stale={read.stale}")
    return 1 if read.stale else 0


def _summarize(args: argparse.Namespace) -> int:
    metrics = evaluate_log(Path(args.log).expanduser().resolve())
    print(json.dumps(asdict(metrics), indent=2))
    return 0


def _export_schemas(args: argparse.Namespace) -> int:
    paths = export_schemas(Path(args.output).expanduser().resolve())
    for path in paths:
        print(path)
    return 0


def _write_sample_telemetry(args: argparse.Namespace) -> int:
    path = Path(args.output).expanduser().resolve()
    write_snapshot_atomic(path, sample_snapshot())
    print(path)
    return 0


def _show_overlay(args: argparse.Namespace) -> int:
    show_overlay(
        Path(args.log).expanduser().resolve(),
        title=args.title,
        opacity=args.opacity,
        auto_close_seconds=args.auto_close_seconds,
        layout=args.layout,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kenshi-agent")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    run = subparsers.add_parser("run", help="Run an agent episode.")
    run.add_argument("--config", default="config/default.yaml")
    run.add_argument("--mode", choices=["mock", "live", "replay"])
    planner_choices = ["heuristic", "scripted", "subprocess", "openai", "openrouter"]
    run.add_argument("--planner", choices=planner_choices)
    run.add_argument(
        "--planning-mode",
        choices=[mode.value for mode in PlanningMode],
        help="Override the configured single_step or continuous scheduler.",
    )
    run.add_argument("--steps", type=int)
    run.add_argument("--seed", type=int)
    run.add_argument("--run-id")
    run.add_argument(
        "--tts",
        action="store_true",
        help=(
            "Narrate human-readable planning and action updates through "
            "offline Windows speech."
        ),
    )
    run.add_argument(
        "--objective",
        help="Override the configured objective for this run only.",
    )
    run.add_argument(
        "--campaign",
        help=(
            "Explicit save-lineage identity for durable continuity in this run. "
            "Generic live profiles intentionally do not choose one."
        ),
    )
    run.add_argument("--scenario-id")
    run.add_argument(
        "--scenario-attestation",
        help=(
            "Fixture verification receipt produced by the supported live launcher. "
            "Manual scenario fields cannot be combined with it."
        ),
    )
    run.add_argument(
        "--save-id",
        help="Stable operator label for the exact save snapshot under test.",
    )
    run.add_argument(
        "--scenario-environment",
        choices=["indoor", "outdoor"],
    )
    run.add_argument(
        "--scenario-danger",
        choices=["hostile", "safe"],
    )
    run.add_argument(
        "--scenario-economy",
        choices=["broke", "funded"],
    )
    run.add_argument(
        "--scenario-party",
        choices=["solo", "squad"],
    )
    run.add_argument(
        "--scenario-time-of-day",
        choices=["day", "night"],
    )
    run.add_argument("--script", help="JSONL decisions for scripted planner.")
    run.add_argument("--command", help="External planner command for subprocess planner.")
    run.add_argument(
        "--command-arg",
        dest="command_args",
        action="append",
        help=(
            "One exact subprocess argv item. Repeat to avoid shell or UNC path "
            "reparsing; values beginning with '-' use --command-arg=VALUE."
        ),
    )
    run.add_argument("--log", help="Session JSONL for replay mode.")
    run.add_argument(
        "--execute-live-actions",
        action="store_true",
        help="Second safety gate required before real keyboard/mouse input.",
    )
    run.add_argument(
        "--acknowledge-native-assisted-control",
        action="store_true",
        help=(
            "Required in addition to the normal live-action gates before a "
            "native_assisted run may execute internal player-order bridges."
        ),
    )
    run.add_argument(
        "--acknowledge-continuous-live",
        action="store_true",
        help=(
            "Required in addition to all normal live-action gates before an "
            "enabled continuous-live policy may execute."
        ),
    )
    run.add_argument(
        "--exclusive-input-session",
        action="store_true",
        help=(
            "Keep Kenshi foreground and do not restore host focus/cursor. Use only when "
            "the human has explicitly handed the keyboard and mouse to the agent."
        ),
    )

    doctor = subparsers.add_parser("doctor", help="Check configuration and live prerequisites.")
    doctor.add_argument("--config", default="config/default.yaml")
    doctor.add_argument("--mode", choices=["mock", "live", "replay"])
    doctor.add_argument("--planner", choices=planner_choices)

    memory = subparsers.add_parser(
        "memory",
        help="Inspect durable continuity read-only: campaigns, records, history.",
    )
    memory.add_argument("--config", default="config/default.yaml")
    memory.add_argument(
        "--campaign",
        help="Campaign to inspect. Omit to list every campaign in the database.",
    )
    memory.add_argument(
        "--memory-id",
        help="Print one record's full lifecycle history instead of the summary.",
    )
    memory.add_argument("--limit", type=int, default=20)

    compaction = subparsers.add_parser(
        "compact-memory",
        help="Propose or atomically apply bounded lossless memory compaction.",
    )
    compaction.add_argument("--config", default="config/default.yaml")
    compaction.add_argument("--campaign", required=True)
    compaction_mode = compaction.add_mutually_exclusive_group(required=True)
    compaction_mode.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Exact source memory ID; repeat two to eight times for a dry run.",
    )
    compaction_mode.add_argument(
        "--apply-candidate",
        help="Apply one exact candidate JSON document emitted by a dry run.",
    )

    fieldbook = subparsers.add_parser(
        "fieldbook",
        help="Inspect campaign fieldbook projects and entries read-only.",
    )
    fieldbook.add_argument("--config", default="config/default.yaml")
    fieldbook.add_argument(
        "--campaign",
        help="Campaign to inspect. Omit to list every campaign.",
    )
    fieldbook_view = fieldbook.add_mutually_exclusive_group()
    fieldbook_view.add_argument("--project-id")
    fieldbook_view.add_argument("--query")
    fieldbook.add_argument(
        "--limit",
        type=int,
        choices=range(1, 9),
        default=8,
    )
    fieldbook_view.add_argument(
        "--markdown",
        action="store_true",
        help="Render the disposable Markdown projection to stdout.",
    )

    validate = subparsers.add_parser("validate-telemetry", help="Validate one telemetry snapshot.")
    validate.add_argument("--config", default="config/default.yaml")
    validate.add_argument("--file")

    summarize = subparsers.add_parser("summarize", help="Summarize a session JSONL log.")
    summarize.add_argument("log")

    schemas = subparsers.add_parser("export-schemas", help="Write JSON Schemas.")
    schemas.add_argument("--output", default="schemas")

    sample = subparsers.add_parser("write-sample-telemetry")
    sample.add_argument("--output", default="examples/telemetry.latest.json")

    overlay = subparsers.add_parser("overlay", help="Show the live decision companion.")
    overlay.add_argument("--log", required=True, help="Session JSONL to follow.")
    overlay.add_argument("--title", default="Kenshi Agent")
    overlay.add_argument("--opacity", type=float, default=0.82)
    overlay.add_argument("--auto-close-seconds", type=float, default=0.0)
    overlay.add_argument(
        "--layout",
        choices=["companion", "overlay"],
        default="companion",
        help="Dock beside Windows Terminal, or explicitly use the legacy game overlay.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_project_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.subcommand == "run":
        return asyncio.run(_run_command(args))
    if args.subcommand == "doctor":
        return _doctor(args)
    if args.subcommand == "memory":
        return _inspect_memory(args)
    if args.subcommand == "compact-memory":
        return _compact_memory(args)
    if args.subcommand == "fieldbook":
        return _inspect_fieldbook(args)
    if args.subcommand == "validate-telemetry":
        return _validate_telemetry(args)
    if args.subcommand == "summarize":
        return _summarize(args)
    if args.subcommand == "export-schemas":
        return _export_schemas(args)
    if args.subcommand == "write-sample-telemetry":
        return _write_sample_telemetry(args)
    if args.subcommand == "overlay":
        return _show_overlay(args)
    parser.error(f"Unhandled command: {args.subcommand}")
    return 2
