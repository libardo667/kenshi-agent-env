"""The single, dependency-free command contract for ``./dev``.

Both the WSL bootstrap and the Windows runtime import this module.  Keep it on
the Python standard library so help and argument validation never depend on a
prepared Windows environment.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

LIVE_CONFIG = "config/live.yaml"
# Two modes, because there were never really three.
#
# `polite-live` saved and restored the host cursor and foreground window around
# every action, so a human could keep using the desktop mid-run. It cost more
# than it bought: with the configured relative pointer mode each restore left
# the cursor somewhere the next relative move had to resync from, which is why
# live actions refused to start under it at all. It also told a second story
# about who owns input, beside the control-ownership machine that already owns
# that question.
#
# There is no human to be polite to here - the game is a fixture driven by the
# agent - so a run either sends input or it does not.
CONTROL_MODES = ("plan-only", "live")


class _FormatterFactory(Protocol):
    def __call__(self, *, prog: str) -> argparse.HelpFormatter: ...


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    def __init__(self, prog: str, *, width: int = 100) -> None:
        super().__init__(prog, max_help_position=30, width=width)
        # Python 3.11 and 3.12 calculate a different natural help column for a
        # subparser whose longest name is ``install-starts``. A floor keeps the
        # generated command reference byte-identical across the supported matrix.
        self._action_max_length = 18  # noqa: SLF001

    def _get_help_string(self, action: argparse.Action) -> str:
        """Pin the Python 3.13 default-display rule across supported versions."""

        help_text = action.help or ""
        if (
            "%(default)" not in help_text
            and action.default is not argparse.SUPPRESS
            and not action.required
            and (
                action.option_strings
                or action.nargs in (argparse.OPTIONAL, argparse.ZERO_OR_MORE)
            )
        ):
            help_text += " (default: %(default)s)"
        return help_text


class _RootHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str, *, width: int = 100) -> None:
        # Width changes that do not cross a wrap boundary are presentation-equivalent.
        # pragma: no mutate start
        super().__init__(prog, max_help_position=30, width=width)
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
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="Planner prompt file override.",
    )
    parser.add_argument(
        "--advisor-corpus-file",
        type=Path,
        help="Advisor corpus file override.",
    )
    parser.add_argument("--steps", type=int, help="Override the configured step ceiling.")
    parser.add_argument("--run-id", help="Exact run identifier; generated when omitted.")
    parser.set_defaults(tts=True)
    _add_control_option(parser)


def _add_control_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--control",
        choices=CONTROL_MODES,
        default="plan-only",
        help=(
            "plan-only sends no gameplay actions; live takes desktop input "
            "ownership for the run."
        ),
    )


def _add_display_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--focus-display",
        action="store_true",
        help=(
            "Temporarily switch to the external 1920x1080 display only; the default "
            "keeps the internal panel and external display active."
        ),
    )


def _scenario_capture_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    formatter_class: _FormatterFactory,
) -> None:
    capture = subparsers.add_parser(
        "capture",
        help="Copy one closed save into the immutable fixture store.",
        formatter_class=formatter_class,
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
    help_width: int = 100,
) -> argparse.ArgumentParser:
    def formatter(*, prog: str) -> argparse.HelpFormatter:
        return _HelpFormatter(prog, width=help_width)

    def root_formatter(*, prog: str) -> argparse.HelpFormatter:
        return _RootHelpFormatter(prog, width=help_width)

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
            "  ./dev run --objective 'Reach Squin' --control live\n"
            "  ./dev telemetry --watch\n"
            "  ./dev recover"
        ),
        formatter_class=root_formatter,
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    commands.add_parser(
        "verify-portable",
        help="Run the complete reproducible gate without touching Windows or Kenshi.",
        description=(
            "Install the locked development environment, run every portable check, "
            "and prove checked-in schemas and documentation are current."
        ),
        formatter_class=formatter,
    )

    doctor = commands.add_parser(
        "doctor",
        parents=[common],
        help="Check every launch prerequisite without sending input.",
        description="Check Steam, memory, graphics, display, crash, and selected start state.",
        formatter_class=formatter,
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
        formatter_class=formatter,
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
        help="Resume one verified native Kenshi window left by an interruption.",
    )
    _add_display_option(launch)
    launch.set_defaults(preflight_only=False)

    run = commands.add_parser(
        "run",
        parents=[common],
        help="Use a safe loaded game or launch one, then run the agent.",
        description=(
            "Run the agent in a fresh or already-loaded world. Ambiguous live state fails closed."
        ),
        formatter_class=formatter,
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum seconds for each bounded startup wait.",
    )
    _add_start_source(run)
    _add_agent_options(run)
    _add_display_option(run)
    run.set_defaults(resume_launcher=False, preflight_only=False)

    commands.add_parser(
        "tui",
        parents=[common],
        help="Launch an interactive terminal UI for composing a run configuration.",
        description=(
            "Drive ./dev run through a compact terminal UI and launch with the same "
            "safety rules."
        ),
        formatter_class=formatter,
    )

    telemetry = commands.add_parser(
        "telemetry",
        parents=[common],
        help="Print the current player-readable telemetry as JSON.",
        formatter_class=formatter,
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

    affordances = commands.add_parser(
        "affordances",
        parents=[common],
        help="Show the affordance menu the agent would be offered right now.",
        formatter_class=formatter,
    )
    affordances.add_argument(
        "--watch",
        action="store_true",
        help="Re-render whenever the menu changes until interrupted.",
    )
    affordances.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between telemetry reads while watching.",
    )
    affordances.add_argument(
        "--json",
        action="store_true",
        help="Emit newline-delimited menu payloads instead of a rendered menu.",
    )
    affordances.add_argument(
        "--capture",
        type=Path,
        default=None,
        help="Append every distinct menu to this newline-delimited JSON file.",
    )

    snapshot = commands.add_parser(
        "snapshot",
        parents=[common],
        help="Capture one frame with its matching telemetry evidence.",
        formatter_class=formatter,
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
        formatter_class=formatter,
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
        formatter_class=formatter,
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
        formatter_class=formatter,
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
    _scenario_capture_parser(scenario_actions, formatter_class=formatter)
    restore = scenario_actions.add_parser(
        "restore",
        help="Restore a fixture into the reserved project-owned save slot.",
    )
    restore.add_argument("scenario_id", help="Exact fixture ID to restore.")

    setup = commands.add_parser(
        "setup",
        parents=[common],
        help="Apply an explicit reversible host repair.",
        formatter_class=formatter,
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

    generation = commands.add_parser(
        "generation-manifest",
        help="Write a deterministic, redacted generation provenance manifest.",
        description=(
            "Write one exact KAE generation identity without launching or contacting Kenshi."
        ),
        formatter_class=formatter,
    )
    generation.add_argument(
        "--config",
        type=Path,
        default=Path(LIVE_CONFIG),
        help=argparse.SUPPRESS,
    )
    generation.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Atomic JSON output path; its parent directory must already exist.",
    )
    generation.add_argument(
        "--prompt-file",
        type=Path,
        help="Exact planner prompt override to identify.",
    )
    generation.add_argument(
        "--advisor-corpus-file",
        type=Path,
        help="Exact advisor strategy corpus override to identify.",
    )
    generation.add_argument(
        "--scenario-attestation",
        type=Path,
        help="Verified scenario attestation whose exact fixture still exists.",
    )
    generation.add_argument(
        "--game-start",
        help=(
            "Exact checked-in authored Game Start ID; recorded separately from "
            "scenario/save identity."
        ),
    )
    generation.add_argument(
        "--script-file",
        type=Path,
        help="Required exact decision script when planner.kind is scripted.",
    )
    generation.add_argument(
        "--kenshi-executable",
        type=Path,
        help="Optional observed Kenshi executable to compare with research authority.",
    )
    generation.add_argument(
        "--built-dll",
        type=Path,
        help="Optional native build output to hash.",
    )
    generation.add_argument(
        "--staged-dll",
        type=Path,
        help="Optional staged DLL override; repository staging is checked by default.",
    )
    generation.add_argument(
        "--installed-dll",
        type=Path,
        help="Optional exact DLL loaded by Kenshi to hash.",
    )

    capability = commands.add_parser(
        "capability-manifest",
        help="Write the exact generated EvoGen capability manifest without launching Kenshi.",
        description=(
            "Project KAE's operation, protocol, continuity, outcome, recovery, and proof "
            "authorities into one canonical capability manifest."
        ),
        formatter_class=formatter,
    )
    capability.add_argument(
        "--generation-id",
        required=True,
        help="The already-computed 64-character generation identity.",
    )
    capability.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Atomic JSON output path; its parent directory must already exist.",
    )

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def render_reference() -> str:
    """Render every parser-owned help page as one generated reference."""

    sections = [
        "# `./dev` command reference",
        "",
        "Generated from `kenshi_agent.tooling.dev_cli`; do not edit by hand.",
        "Regenerate with `python scripts/export_docs.py`.",
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

    # A wide reference avoids version-specific usage wrapping in argparse while
    # runtime help retains the terminal-friendly 100-column layout.
    append(build_parser(help_width=1000))
    return "\n".join(sections).rstrip() + "\n"
