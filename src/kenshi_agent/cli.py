from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

from .config import AppConfig, load_config
from .control import Win32InputController
from .env import LiveEnvironment, MockEnvironment, ReplayEnvironment
from .evals import evaluate_log
from .memory import MemoryStore
from .planners import HeuristicPlanner, OpenAIPlanner, ScriptedPlanner, SubprocessPlanner
from .reflexes import ReflexEngine
from .runtime import AgentRuntime
from .safety import ActionGuard
from .schema_export import export_schemas
from .session_log import SessionLogger
from .skills import MacroRegistry
from .telemetry import TelemetryReader, write_snapshot_atomic
from .telemetry.sample import sample_snapshot


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _build_planner(config: AppConfig, args: argparse.Namespace):
    kind = args.planner or config.planner.kind
    if kind == "heuristic":
        return HeuristicPlanner()
    if kind == "scripted":
        if not args.script:
            raise SystemExit("--script is required for the scripted planner.")
        return ScriptedPlanner(Path(args.script).expanduser().resolve())
    if kind == "subprocess":
        if not args.command:
            raise SystemExit("--command is required for the subprocess planner.")
        return SubprocessPlanner(args.command, timeout_seconds=config.planner.timeout_seconds)
    if kind == "openai":
        return OpenAIPlanner(config.planner, config.paths.prompt_file)
    raise SystemExit(f"Unsupported planner kind: {kind}")


def _build_environment(
    config: AppConfig,
    args: argparse.Namespace,
    *,
    run_id: str,
    run_dir: Path,
    macros: MacroRegistry,
):
    mode = args.mode or config.mode
    if mode == "mock":
        return MockEnvironment(config.mock, run_dir / "frames", run_id)
    if mode == "replay":
        if not args.log:
            raise SystemExit("--log is required for replay mode.")
        return ReplayEnvironment(Path(args.log).expanduser().resolve())
    if mode == "live":
        if os.name != "nt":
            raise SystemExit("Live mode requires Windows.")
        if args.execute_live_actions and not config.safety.live_actions_enabled:
            raise SystemExit(
                "--execute-live-actions was supplied, but safety.live_actions_enabled is false."
            )
        execute_actions = bool(args.execute_live_actions and config.safety.live_actions_enabled)
        controller = Win32InputController(
            config.capture.window_title_contains,
            focus_before_input=config.controls.focus_before_input,
            post_input_delay_seconds=config.controls.post_input_delay_seconds,
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
        )
    raise SystemExit(f"Unsupported environment mode: {mode}")


async def _run_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_id = args.run_id or _new_run_id()
    run_dir = config.paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    macros = MacroRegistry(config.macros)
    logger = SessionLogger(run_dir / "events.jsonl", run_id)
    memory = (
        MemoryStore(config.paths.memory_db, config.memory.run_namespace)
        if config.memory.enabled
        else None
    )
    try:
        planner = _build_planner(config, args)
        environment = _build_environment(
            config,
            args,
            run_id=run_id,
            run_dir=run_dir,
            macros=macros,
        )
        runtime = AgentRuntime(
            run_id=run_id,
            environment=environment,
            planner=planner,
            guard=ActionGuard(config.safety, macros),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=memory,
            memory_limit=config.memory.max_recalled_memories,
            minimum_memory_salience=config.memory.minimum_salience,
        )
        summary = await runtime.run(
            max_steps=args.steps or config.runtime.max_steps,
            seed=args.seed,
        )
        output = {
            "run_id": summary.run_id,
            "run_dir": str(run_dir),
            "steps_completed": summary.steps_completed,
            "terminated": summary.terminated,
            "success": summary.success,
            "stop_reason": summary.stop_reason,
        }
        print(json.dumps(output, indent=2))
        return 0 if summary.success is not False else 2
    finally:
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
                        f"protocol={read.snapshot.protocol_version} age={read.age_seconds:.2f}s stale={read.stale}",
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
    if (args.planner or config.planner.kind) == "openai":
        checks.append(("openai_api_key", bool(os.environ.get("OPENAI_API_KEY")), "environment"))
        try:
            import openai  # noqa: F401

            checks.append(("openai_package", True, "installed"))
        except ImportError:
            checks.append(("openai_package", False, "pip install -e '.[openai]'"))
    width = max(len(name) for name, _, _ in checks)
    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {name:<{width}}  {detail}")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kenshi-agent")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    run = subparsers.add_parser("run", help="Run an agent episode.")
    run.add_argument("--config", default="config/default.yaml")
    run.add_argument("--mode", choices=["mock", "live", "replay"])
    run.add_argument("--planner", choices=["heuristic", "scripted", "subprocess", "openai"])
    run.add_argument("--steps", type=int)
    run.add_argument("--seed", type=int)
    run.add_argument("--run-id")
    run.add_argument("--script", help="JSONL decisions for scripted planner.")
    run.add_argument("--command", help="External planner command for subprocess planner.")
    run.add_argument("--log", help="Session JSONL for replay mode.")
    run.add_argument(
        "--execute-live-actions",
        action="store_true",
        help="Second safety gate required before real keyboard/mouse input.",
    )

    doctor = subparsers.add_parser("doctor", help="Check configuration and live prerequisites.")
    doctor.add_argument("--config", default="config/default.yaml")
    doctor.add_argument("--mode", choices=["mock", "live", "replay"])
    doctor.add_argument("--planner", choices=["heuristic", "scripted", "subprocess", "openai"])

    validate = subparsers.add_parser("validate-telemetry", help="Validate one telemetry snapshot.")
    validate.add_argument("--config", default="config/default.yaml")
    validate.add_argument("--file")

    summarize = subparsers.add_parser("summarize", help="Summarize a session JSONL log.")
    summarize.add_argument("log")

    schemas = subparsers.add_parser("export-schemas", help="Write JSON Schemas.")
    schemas.add_argument("--output", default="schemas")

    sample = subparsers.add_parser("write-sample-telemetry")
    sample.add_argument("--output", default="examples/telemetry.latest.json")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.subcommand == "run":
        return asyncio.run(_run_command(args))
    if args.subcommand == "doctor":
        return _doctor(args)
    if args.subcommand == "validate-telemetry":
        return _validate_telemetry(args)
    if args.subcommand == "summarize":
        return _summarize(args)
    if args.subcommand == "export-schemas":
        return _export_schemas(args)
    if args.subcommand == "write-sample-telemetry":
        return _write_sample_telemetry(args)
    parser.error(f"Unhandled command: {args.subcommand}")
    return 2
