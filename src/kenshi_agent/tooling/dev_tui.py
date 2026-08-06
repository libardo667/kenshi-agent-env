"""Low-impact terminal UI for launching a ``./dev run`` session.

The UI keeps one place for run-level configuration that is usually assembled from
many separate CLI flags and keeps launch behavior unchanged by delegating to the
existing ``./dev`` run path.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from ..config import AppConfig
from .authored_starts import load_authored_starts_bundle
from .dev_cli import LIVE_CONFIG
from .scenario_fixtures import load_scenario_fixture

if TYPE_CHECKING:
    import curses

    # The window type only has to be real for the checker; at runtime curses
    # may not be importable at all, and this module is still importable so the
    # dev CLI can report that rather than fail to load.
    TuiWindow: TypeAlias = curses.window
else:
    TuiWindow = Any

    try:
        import curses
    except ModuleNotFoundError:  # pragma: no cover - the tui path alone needs it
        curses = None


@dataclass
class _TuiState:
    control: Literal["plan-only", "live"] = "plan-only"
    start_source: Literal["loaded", "scenario", "game-start"] = "loaded"
    scenario_id: str = ""
    game_start_id: str = ""
    objective: str = ""
    campaign: str = ""
    steps: str = ""
    prompt_file: str = "[default]"
    advisor_corpus_file: str = "[default]"


@dataclass(frozen=True)
class _TuiChoices:
    scenario_ids: tuple[str, ...]
    game_start_ids: tuple[str, ...]
    prompt_files: tuple[str, ...]
    advisor_corpus_files: tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _config_path_from_args(args: argparse.Namespace) -> Path:
    config = getattr(args, "config", None)
    if config is not None:
        return Path(str(config))
    return _repo_root() / LIVE_CONFIG


def _scenario_store() -> Path:
    override = os.environ.get("KENSHI_AGENT_SCENARIO_STORE")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "KenshiAgent" / "scenarios"
    return _repo_root() / "scenarios"


def _scenario_ids() -> tuple[str, ...]:
    fixtures_root = _scenario_store() / "fixtures"
    if not fixtures_root.is_dir():
        return ()
    values: list[str] = []
    for save in sorted(fixtures_root.iterdir()):
        if not save.is_dir() or save.name.startswith("."):
            continue
        try:
            manifest = load_scenario_fixture(_scenario_store(), save.name)
        except Exception:
            continue
        values.append(manifest.scenario.scenario_id)
    return tuple(values)


def _game_start_ids() -> tuple[str, ...]:
    try:
        return tuple(
            start.start_id for start in load_authored_starts_bundle().manifest.starts
        )
    except Exception:
        return ()


def _prompt_candidates(root: Path) -> tuple[str, ...]:
    prompts_root = root / "prompts"
    if not prompts_root.is_dir():
        return ()
    return tuple(sorted(_display_path(path, root) for path in prompts_root.glob("*.md")))


def _advisor_candidates(root: Path) -> tuple[str, ...]:
    knowledge_root = root / "knowledge"
    if not knowledge_root.is_dir():
        return ()
    files: list[str] = []
    for pattern in ("*.yaml", "*.yml"):
        files.extend(_display_path(path, root) for path in knowledge_root.glob(pattern))
    return tuple(sorted(dict.fromkeys(files)))


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _build_choices(config: AppConfig, root: Path) -> _TuiChoices:
    prompt_files = set(_prompt_candidates(root))
    prompt_files.add(str(config.paths.prompt_file))
    advisor_files = set(_advisor_candidates(root))
    advisor_files.add(str(config.advisor.corpus_file))

    return _TuiChoices(
        scenario_ids=_scenario_ids(),
        game_start_ids=_game_start_ids(),
        prompt_files=tuple(sorted(("[default]", *prompt_files))),
        advisor_corpus_files=tuple(sorted(("[default]", *advisor_files))),
    )


def _draw_rows(
    stdscr: TuiWindow,
    state: _TuiState,
    _choices: _TuiChoices,
    selected: int,
    width: int,
    error: str | None,
    config_path: Path,
) -> None:
    rows = [
        f"Control        : {state.control}",
        f"Start source   : {state.start_source}",
        f"Scenario      : {state.scenario_id or '(none)'}",
        f"Game start    : {state.game_start_id or '(none)'}",
        f"Objective     : {state.objective or '(empty)'}",
        f"Campaign      : {state.campaign or '(empty)'}",
        f"Steps         : {state.steps or '(default)'}",
        f"Planner prompt: {state.prompt_file}",
        f"Advisor corpus: {state.advisor_corpus_file}",
        "Launch run",
        "Cancel",
    ]

    stdscr.erase()
    status = "./dev run TUI"
    if config_path:
        status = f"{status} ({config_path})"
    stdscr.addstr(0, 0, status[: width - 1])
    stdscr.clrtoeol()
    stdscr.move(2, 0)

    for index, row in enumerate(rows):
        prefix = "> " if index == selected else "  "
        stdscr.addstr(2 + index, 0, f"{prefix}{row}"[: width - 1])

    message_row = 2 + len(rows) + 1
    if error:
        stdscr.addstr(message_row, 0, f"Error: {error}"[: width - 1])
    else:
        stdscr.addstr(message_row, 0, "")
    stdscr.clrtoeol()
    stdscr.addstr(
        message_row + 1,
        0,
        "↑/↓ select  Enter edit/select  q quit"[: width - 1],
    )
    stdscr.clrtoeol()
    stdscr.refresh()


def _edit_text(stdscr: TuiWindow, title: str, value: str) -> str:
    height, width = stdscr.getmaxyx()
    row = height - 2
    prompt = f"{title}: "
    stdscr.move(row, 0)
    stdscr.clrtoeol()
    stdscr.addstr(row, 0, prompt[: width - 1])
    stdscr.addstr(row, len(prompt), value[: max(0, width - len(prompt) - 1)])
    stdscr.move(row, len(prompt) + min(len(value), max(0, width - len(prompt) - 1)))
    curses.echo()
    raw = stdscr.getstr(row, len(prompt), max(1, width - len(prompt) - 1))
    curses.noecho()
    return raw.decode("utf-8", errors="replace").strip()


def _select_one(
    stdscr: TuiWindow,
    title: str,
    options: tuple[str, ...],
    current: str,
) -> str | None:
    if not options:
        return current or None
    index = options.index(current) if current in options else 0

    while True:
        stdscr.erase()
        stdscr.addstr(0, 0, title)
        for offset, option in enumerate(options):
            marker = "> " if offset == index else "  "
            stdscr.addstr(2 + offset, 0, f"{marker}{option}")
        stdscr.addstr(2 + len(options) + 1, 0, "Enter: choose  q: cancel")
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(options)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(options)
        elif key in (10, 13, curses.KEY_ENTER):
            return options[index]
        elif key in (27, ord("q"), ord("Q")):
            return None


def _build_run_namespace(
    source_args: argparse.Namespace,
    state: _TuiState,
    config_path: Path,
) -> argparse.Namespace:
    steps: int | None
    if state.steps:
        try:
            steps = int(state.steps)
        except ValueError as exc:
            raise ValueError("steps must be a positive integer") from exc
        if steps <= 0:
            raise ValueError("steps must be a positive integer")
    else:
        steps = None

    prompt_file = None if state.prompt_file == "[default]" else state.prompt_file
    advisor_corpus_file = (
        None if state.advisor_corpus_file == "[default]" else state.advisor_corpus_file
    )

    return argparse.Namespace(
        timeout=getattr(source_args, "timeout", 60.0),
        continue_game=True,
        scenario=state.scenario_id if state.start_source == "scenario" else None,
        game_start=state.game_start_id if state.start_source == "game-start" else None,
        objective=state.objective or None,
        campaign=state.campaign or None,
        steps=steps,
        control=state.control,
        focus_display=bool(getattr(source_args, "focus_display", False)),
        config=str(config_path),
        run_id=None,
        resume_launcher=False,
        preflight_only=False,
        prompt_file=prompt_file,
        advisor_corpus_file=advisor_corpus_file,
    )


def _normalize_choices(state: _TuiState, choices: _TuiChoices) -> _TuiState:
    if state.start_source == "scenario" and choices.scenario_ids:
        if state.scenario_id not in choices.scenario_ids:
            state = replace(state, scenario_id=choices.scenario_ids[0])
    elif state.start_source == "scenario":
        state = replace(state, start_source="loaded", scenario_id="", game_start_id="")

    if state.start_source == "game-start" and choices.game_start_ids:
        if state.game_start_id not in choices.game_start_ids:
            state = replace(state, game_start_id=choices.game_start_ids[0])
    elif state.start_source == "game-start":
        state = replace(state, start_source="loaded", scenario_id="", game_start_id="")

    if state.prompt_file not in choices.prompt_files:
        state = replace(state, prompt_file="[default]")
    if state.advisor_corpus_file not in choices.advisor_corpus_files:
        state = replace(state, advisor_corpus_file="[default]")
    return state


def run_from_dev_args(
    args: argparse.Namespace,
    *,
    config_loader: Callable[[Path], AppConfig],
    run_command: Callable[[argparse.Namespace], int],
) -> int:
    if curses is None:
        print("The TUI requires the curses module. Install it first.")
        return 2
    if not os.isatty(0) or not os.isatty(1):
        print("The TUI requires an interactive terminal.")
        return 2

    config_path = _config_path_from_args(args)
    config = config_loader(config_path)
    choices = _build_choices(config, _repo_root())
    state = _normalize_choices(_TuiState(), choices)

    def _run(stdscreen: TuiWindow) -> int:
        nonlocal state
        selected = 0
        error: str | None = None
        curses.curs_set(0)
        while True:
            height, width = stdscreen.getmaxyx()
            if height < 16 or width < 30:
                return 2
            state = _normalize_choices(state, choices)
            _draw_rows(
                stdscreen,
                state,
                choices,
                selected,
                width=width,
                error=error,
                config_path=config_path,
            )
            key = stdscreen.getch()
            error = None

            if key in (ord("q"), ord("Q"), 27):
                return 0
            if key in (curses.KEY_UP, ord("k")):
                selected = (selected - 1) % 11
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                selected = (selected + 1) % 11
                continue

            if selected == 0:
                if key in (curses.KEY_LEFT, ord("h")):
                    state = replace(state, control="plan-only")
                    continue
                if key in (curses.KEY_RIGHT, ord("l")):
                    state = replace(state, control="live")
                    continue
            if selected == 1:
                if key in (curses.KEY_LEFT, ord("h")):
                    state = replace(
                        state,
                        start_source=(
                            "loaded"
                            if state.start_source == "scenario"
                            else (
                                "game-start"
                                if state.start_source == "loaded"
                                else "scenario"
                            )
                        ),
                    )
                    continue
                if key in (curses.KEY_RIGHT, ord("l")):
                    state = replace(
                        state,
                        start_source=(
                            "scenario"
                            if state.start_source == "loaded"
                            else (
                                "game-start"
                                if state.start_source == "scenario"
                                else "loaded"
                            )
                        ),
                    )
                    continue

            if key not in (10, 13, curses.KEY_ENTER):
                continue

            if selected == 9:
                try:
                    run_args = _build_run_namespace(
                        args,
                        state,
                        config_path,
                    )
                except ValueError as exc:
                    error = str(exc)
                else:
                    curses.endwin()
                    return run_command(run_args)
                continue
            if selected == 10:
                return 0
            if selected == 2 and state.start_source == "scenario":
                selected_id = _select_one(
                    stdscreen,
                    "Choose scenario",
                    choices.scenario_ids,
                    state.scenario_id,
                )
                if selected_id is not None:
                    state = replace(state, scenario_id=selected_id)
                continue
            if selected == 3 and state.start_source == "game-start":
                selected_id = _select_one(
                    stdscreen,
                    "Choose authored game start",
                    choices.game_start_ids,
                    state.game_start_id,
                )
                if selected_id is not None:
                    state = replace(state, game_start_id=selected_id)
                continue
            if selected == 4:
                state = replace(
                    state,
                    objective=_edit_text(stdscreen, "Objective", state.objective),
                )
                continue
            if selected == 5:
                state = replace(
                    state,
                    campaign=_edit_text(stdscreen, "Campaign", state.campaign),
                )
                continue
            if selected == 6:
                state = replace(state, steps=_edit_text(stdscreen, "Steps", state.steps))
                continue
            if selected == 7:
                prompt = _select_one(
                    stdscreen,
                    "Choose planner prompt",
                    choices.prompt_files,
                    state.prompt_file,
                )
                if prompt is not None:
                    state = replace(state, prompt_file=prompt)
                continue
            if selected == 8:
                corpus = _select_one(
                    stdscreen,
                    "Choose advisor corpus",
                    choices.advisor_corpus_files,
                    state.advisor_corpus_file,
                )
                if corpus is not None:
                    state = replace(state, advisor_corpus_file=corpus)

    stdscr = curses.initscr()
    try:
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        return _run(stdscr)
    finally:
        try:
            stdscr.keypad(False)
        except Exception:
            pass
        try:
            curses.nocbreak()
        except Exception:
            pass
        try:
            curses.echo()
        except Exception:
            pass
        try:
            curses.endwin()
        except Exception:
            pass


__all__ = ["run_from_dev_args"]
