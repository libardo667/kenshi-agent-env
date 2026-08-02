"""The single, dependency-free command contract for ``./dev``.

Both the WSL bootstrap and the Windows runtime import this module.  Keep it on
the Python standard library so help and argument validation never depend on a
prepared Windows environment.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

LIVE_CONFIG = "config/live.yaml"
CONTROL_MODES = ("plan-only", "polite-live", "exclusive-live")


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=30, width=100)


class _RootHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        # Width changes that do not cross a wrap boundary are presentation-equivalent.
        # pragma: no mutate start
        super().__init__(prog, max_help_position=30, width=100)
        # pragma: no mutate end


def _common_parser(*, include_transport: bool) -> argparse.ArgumentParser:
    # argparse intentionally treats False and None alike here. The resulting
    # no-help parent contract is tested directly, so that equivalent is excluded.
    # pragma: no mutate start
    common = argparse.ArgumentParser(add_help=False)
    # pragma: no mutate end
    if include_transport:
        common.add_argument("--config", help=argparse.SUPPRESS)
    return common


def _add_start_source(
    parser: argparse.ArgumentParser,
) -> argparse._MutuallyExclusiveGroup:  # noqa: SLF001
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--scenario",
        help="Use this exact restored and attested scenario fixture.",
    )
    source.add_argument(
        "--game-start",
        help="Start this exact bundled authored start and prove its initial state.",
    )
    parser.set_defaults(continue_game=True)
    return source


def _add_agent_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--objective", help="Override the configured objective for this run.")
    parser.add_argument(
        "--campaign",
        help="Save-lineage identity used for durable memory continuity.",
    )
    parser.add_argument("--steps", type=int, help="Override the configured step ceiling.")
    parser.add_argument("--run-id", help="Exact run identifier; generated when omitted.")
    narration = parser.add_mutually_exclusive_group()
    narration.add_argument(
        "--tts",
        action="store_true",
        help="Narrate planning and action updates.",
    )
    narration.add_argument(
        "--no-tts",
        dest="tts",
        action="store_false",
        help="Disable spoken planning and action updates.",
    )
    parser.set_defaults(tts=True)
    _add_control_option(parser)


def _add_control_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--control",
        choices=CONTROL_MODES,
        default="plan-only",
        help=(
            "plan-only sends no gameplay actions; polite-live restores host focus and cursor; "
            "exclusive-live retains desktop ownership."
        ),
    )


def _scenario_capture_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    capture = subparsers.add_parser(
        "capture",
        help="Copy one closed save into the immutable fixture store.",
        formatter_class=_HelpFormatter,
    )
    capture.add_argument(
        "--source-save",
        required=True,
        help="Closed Kenshi save directory to copy.",
    )
    capture.add_argument("--scenario-id", required=True, help="New immutable fixture ID.")
    capture.add_argument("--save-id", required=True, help="Stable source-save identity.")
    capture.add_argument(
        "--environment",
        choices=("indoor", "outdoor"),
        required=True,
        help="Observable environment axis.",
    )
    capture.add_argument(
        "--danger",
        choices=("hostile", "safe"),
        required=True,
        help="Observable danger axis.",
    )
    capture.add_argument(
        "--economy",
        choices=("broke", "funded"),
        required=True,
        help="Observable economy axis.",
    )
    capture.add_argument(
        "--party",
        choices=("solo", "squad"),
        required=True,
        help="Observable party axis.",
    )
    capture.add_argument(
        "--time-of-day",
        choices=("day", "night"),
        required=True,
        help="Observable time axis.",
    )


def build_parser(
    *,
    prog: str = "./dev",
    include_transport: bool = False,
) -> argparse.ArgumentParser:
    common = _common_parser(include_transport=include_transport)
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Safe, state-aware Kenshi live development. Use 'run' for the normal "
            "launch-and-agent workflow."
        ),
        epilog=(
            "Examples:\n"
            "  ./dev doctor\n"
            "  ./dev run --objective 'Reach Squin' --control polite-live\n"
            "  ./dev telemetry --watch\n"
            "  ./dev recover"
        ),
        formatter_class=_RootHelpFormatter,
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    doctor = commands.add_parser(
        "doctor",
        parents=[common],
        help="Check every launch prerequisite without sending input.",
        description="Check Steam, memory, graphics, display, crash, and selected start state.",
        formatter_class=_HelpFormatter,
    )
    doctor.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum seconds for bounded readiness checks.",
    )
    _add_start_source(doctor)
    doctor.set_defaults(resume_launcher=False, preflight_only=True)

    launch = commands.add_parser(
        "launch",
        parents=[common],
        help="Launch Kenshi without starting an agent.",
        description="Launch Kenshi and optionally load one exact start source.",
        formatter_class=_HelpFormatter,
    )
    launch.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum seconds for each bounded startup wait.",
    )
    launch_source = _add_start_source(launch)
    launch_source.add_argument(
        "--title",
        dest="continue_game",
        action="store_false",
        help="Stop at the title screen instead of loading a world.",
    )
    launch.add_argument(
        "--resume-launcher",
        action="store_true",
        help="Resume one verified pre-game launcher left by an interruption.",
    )
    launch.set_defaults(preflight_only=False)

    run = commands.add_parser(
        "run",
        parents=[common],
        help="Use a safe loaded game or launch one, then run the agent.",
        description=(
            "Run the agent in a fresh or already-loaded world. Ambiguous live state fails closed."
        ),
        formatter_class=_HelpFormatter,
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum seconds for each bounded startup wait.",
    )
    _add_start_source(run)
    _add_agent_options(run)
    run.set_defaults(resume_launcher=False, preflight_only=False)

    telemetry = commands.add_parser(
        "telemetry",
        parents=[common],
        help="Print the current player-readable telemetry as JSON.",
        formatter_class=_HelpFormatter,
    )
    telemetry.add_argument(
        "--watch",
        action="store_true",
        help="Emit newline-delimited snapshots until interrupted.",
    )
    telemetry.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between watched snapshots.",
    )

    snapshot = commands.add_parser(
        "snapshot",
        parents=[common],
        help="Capture one frame with its matching telemetry evidence.",
        formatter_class=_HelpFormatter,
    )
    snapshot.add_argument(
        "--label",
        default="snapshot",
        help="Filesystem-safe evidence label.",
    )

    recover = commands.add_parser(
        "recover",
        parents=[common],
        help="Leave Kenshi safely paused and release stranded display ownership.",
        formatter_class=_HelpFormatter,
    )
    recover.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Maximum seconds for each bounded recovery wait.",
    )
    recover.add_argument(
        "--dismiss-crash",
        action="store_true",
        help="After archiving a visible crash, explicitly dismiss its unsent reporter.",
    )

    stop = commands.add_parser(
        "stop",
        parents=[common],
        help="Safely pause and close Kenshi.",
        formatter_class=_HelpFormatter,
    )
    stop.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Maximum seconds for safe pause and close confirmation.",
    )

    scenario = commands.add_parser(
        "scenario",
        parents=[common],
        help="Manage reproducible starts and immutable save fixtures.",
        formatter_class=_HelpFormatter,
    )
    scenario_actions = scenario.add_subparsers(
        dest="scenario_action",
        required=True,
        metavar="ACTION",
    )
    scenario_actions.add_parser("list", help="List and verify captured fixtures.")
    scenario_actions.add_parser(
        "install-starts",
        help="Install and verify the exact bundled authored starts.",
    )
    _scenario_capture_parser(scenario_actions)
    restore = scenario_actions.add_parser(
        "restore",
        help="Restore a fixture into the reserved project-owned save slot.",
    )
    restore.add_argument("scenario_id", help="Exact fixture ID to restore.")

    setup = commands.add_parser(
        "setup",
        parents=[common],
        help="Apply an explicit reversible host repair.",
        formatter_class=_HelpFormatter,
    )
    setup_actions = setup.add_subparsers(
        dest="setup_action",
        required=True,
        metavar="ACTION",
    )
    setup_actions.add_parser(
        "graphics",
        help="Install the canonical live configuration's reversible graphics settings.",
    )

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def render_reference() -> str:
    """Render every parser-owned help page as one generated reference."""

    sections = [
        "# `./dev` command reference",
        "",
        "Generated from `kenshi_agent.dev_cli`; do not edit by hand.",
        "Regenerate with `python scripts/export_dev_cli.py`.",
        "",
    ]

    def append(parser: argparse.ArgumentParser) -> None:
        sections.extend(
            [
                f"## `{parser.prog}`",
                "",
                "```text",
                parser.format_help().rstrip(),
                "```",
                "",
            ]
        )
        for action in parser._actions:  # noqa: SLF001
            if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
                continue
            for child in action.choices.values():
                append(child)

    append(build_parser())
    return "\n".join(sections).rstrip() + "\n"


def export_reference(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8's canonical spelling, alias, and the supported platform default
    # produce identical generated bytes; the exact output is gated separately.
    # pragma: no mutate start
    path.write_text(render_reference(), encoding="utf-8")
    # pragma: no mutate end
    return path
