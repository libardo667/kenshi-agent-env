import argparse
import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kenshi_agent.dev_cli import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
INVOCATION_END = "__END_WINDOWS_INVOCATION__"


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fake_dev_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_local_app_data = tmp_path / "LocalAppData"
    invocation_log = tmp_path / "windows-python-argv.txt"
    fake_windows_python = (
        fake_local_app_data
        / "KenshiAgent"
        / "venvs"
        / "kenshi-agent-env"
        / "Scripts"
        / "python.exe"
    )

    _write_executable(
        fake_bin / "powershell.exe",
        """#!/usr/bin/env bash
printf '%s\n' "$FAKE_LOCAL_APP_DATA"
""",
    )
    _write_executable(
        fake_bin / "wslpath",
        """#!/usr/bin/env bash
if [[ "${1:-}" == "-w" ]]; then
  shift
fi
printf '%s\n' "$1"
""",
    )
    _write_executable(
        fake_windows_python,
        """#!/usr/bin/env bash
if [[ -t 0 || -t 1 || -t 2 ]]; then
  printf '\033[6n'
  read -r _
fi
if [[ -n "${FAKE_WINDOWS_ENV_LOG:-}" ]]; then
  printf '%s\n' "${WSLENV:-}" >> "$FAKE_WINDOWS_ENV_LOG"
fi
{
  printf '%s\n' "$@"
  printf '%s\n' "__END_WINDOWS_INVOCATION__"
} >> "$FAKE_WINDOWS_PYTHON_LOG"
for argument in "$@"; do
  if [[ "${FAKE_FAIL_COMMAND:-}" == "$argument" ]]; then
    exit 23
  fi
  if [[ "${FAKE_PAUSE_COMMAND:-}" == "$argument" ]]; then
    if [[ -n "${FAKE_WINDOWS_RUN_PID_FILE:-}" ]]; then
      printf '%s\n' "$$" > "$FAKE_WINDOWS_RUN_PID_FILE"
    fi
    trap 'exit 143' TERM
    sleep 5
  fi
done
if [[ "${4:-}" == "recover" \
  && -n "${FAKE_WINDOWS_RUN_PID_FILE:-}" \
  && -f "$FAKE_WINDOWS_RUN_PID_FILE" ]]; then
  if kill -0 "$(cat "$FAKE_WINDOWS_RUN_PID_FILE")" 2>/dev/null; then
    printf '%s\n' "run child still alive" > "$FAKE_WINDOWS_LIFECYCLE_ERROR"
  fi
fi
""",
    )

    env = {
        **os.environ,
        "PATH": (
            f"{fake_bin}{os.pathsep}{Path(sys.executable).parent}"
            f"{os.pathsep}{os.environ['PATH']}"
        ),
        "FAKE_LOCAL_APP_DATA": str(fake_local_app_data),
        "FAKE_WINDOWS_PYTHON_LOG": str(invocation_log),
    }
    return env, invocation_log


def _run_dev_in_pty(env: dict[str, str], *args: str) -> int:
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        [str(REPO_ROOT / "dev"), *args],
        cwd=REPO_ROOT,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)

    try:
        try:
            return_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
            pytest.fail(
                "the Windows process inherited the PTY and waited for a cursor "
                "position response"
            )
    finally:
        os.close(master_fd)

    return return_code


def _read_invocations(path: Path) -> list[list[str]]:
    invocations: list[list[str]] = []
    current: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == INVOCATION_END:
            invocations.append(current)
            current = []
        else:
            current.append(line)
    assert not current
    return invocations


def test_shared_dev_parser_preserves_the_launch_start_contract() -> None:
    parser = build_parser(include_transport=True)

    launch_default = parser.parse_args(["launch"])
    launch_title = parser.parse_args(["launch", "--title"])

    assert launch_default.continue_game is True
    assert launch_title.continue_game is False


def test_windows_transport_is_hidden_and_available_on_every_command() -> None:
    parser = build_parser(include_transport=True)
    workflows = (
        ["doctor"],
        ["launch"],
        ["run"],
        ["telemetry"],
        ["snapshot"],
        ["recover"],
        ["stop"],
        ["scenario", "list"],
        ["setup", "graphics"],
    )

    for workflow in workflows:
        args = parser.parse_args(
            [workflow[0], "--config", "config/transport-test.yaml", *workflow[1:]]
        )
        assert args.config == "config/transport-test.yaml", workflow

    subparsers = next(
        action
        for action in parser._actions  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )
    for command in subparsers.choices.values():
        assert "--config" not in command.format_help()


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_dev_help_is_local_and_describes_the_complete_supported_surface(
    tmp_path: Path,
) -> None:
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{tmp_path}",
    }

    result = subprocess.run(
        [str(REPO_ROOT / "dev"), "--help"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "{doctor,launch,run,telemetry,snapshot,recover,stop,scenario,setup}" in (
        result.stdout
    )
    assert "Windows live Python" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_every_help_page_is_local(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{tmp_path}",
    }

    for command in (
        "doctor",
        "launch",
        "run",
        "telemetry",
        "snapshot",
        "recover",
        "stop",
        "scenario",
        "setup",
    ):
        result = subprocess.run(
            [str(REPO_ROOT / "dev"), command, "--help"],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (command, result.stderr)
        assert f"usage: ./dev {command}" in result.stdout
        assert "Windows live Python" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_removed_commands_fail_before_windows_runtime_discovery(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{tmp_path}",
    }

    for removed in (
        "play",
        "journey",
        "probe",
        "close",
        "crash",
        "shot",
        "graphics",
    ):
        result = subprocess.run(
            [str(REPO_ROOT / "dev"), removed],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "invalid choice" in result.stderr
        assert "Windows live Python" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_run_rejects_profile_config_and_planner_implementation_flags_locally(
    tmp_path: Path,
) -> None:
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{tmp_path}",
    }

    for arguments in (
        ("--profile", "dialogue"),
        ("--config", "config/live.yaml"),
        ("--planner", "subprocess"),
        ("--planner-script", "scripts/live_direction_smoke_planner.py"),
    ):
        result = subprocess.run(
            [str(REPO_ROOT / "dev"), "run", *arguments],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "unrecognized arguments" in result.stderr
        assert "Windows live Python" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_dev_detaches_windows_processes_from_an_inherited_pty(
    tmp_path: Path,
) -> None:
    env, invocation_log = _fake_dev_environment(tmp_path)

    assert _run_dev_in_pty(env, "telemetry") == 0
    assert _read_invocations(invocation_log) == [
        [
            "-u",
            "-m",
            "kenshi_agent.live_dev",
            "telemetry",
            "--config",
            str(REPO_ROOT / "config" / "live.yaml"),
        ]
    ]


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_dev_run_failure_recovers_with_the_same_canonical_config(
    tmp_path: Path,
) -> None:
    env, invocation_log = _fake_dev_environment(tmp_path)
    env["FAKE_FAIL_COMMAND"] = "run"

    assert _run_dev_in_pty(env, "run") == 23
    assert _read_invocations(invocation_log) == [
        [
            "-u",
            "-m",
            "kenshi_agent.live_dev",
            "run",
            "--config",
            str(REPO_ROOT / "config" / "live.yaml"),
        ],
        [
            "-u",
            "-m",
            "kenshi_agent.live_dev",
            "recover",
            "--config",
            str(REPO_ROOT / "config" / "live.yaml"),
        ],
    ]


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_interrupted_dev_run_still_invokes_supported_recovery(tmp_path: Path) -> None:
    env, invocation_log = _fake_dev_environment(tmp_path)
    run_pid = tmp_path / "run.pid"
    lifecycle_error = tmp_path / "lifecycle-error.txt"
    env["FAKE_PAUSE_COMMAND"] = "run"
    env["FAKE_WINDOWS_RUN_PID_FILE"] = str(run_pid)
    env["FAKE_WINDOWS_LIFECYCLE_ERROR"] = str(lifecycle_error)
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        [str(REPO_ROOT / "dev"), "run"],
        cwd=REPO_ROOT,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)
    deadline = time.monotonic() + 2.0
    while not run_pid.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert run_pid.exists()

    # A detached tmux pane delivers the signal to the wrapper process, not to
    # the Windows interop child as a shared Unix process group. The wrapper
    # must revoke that child before starting recovery.
    try:
        os.kill(process.pid, signal.SIGTERM)
        assert process.wait(timeout=5) == 143
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        os.close(master_fd)
    assert [invocation[3] for invocation in _read_invocations(invocation_log)] == [
        "run",
        "recover",
    ]
    assert not lifecycle_error.exists(), (
        "supported recovery started while the interrupted run still held authority"
    )


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_dev_forwards_the_exact_wsl_distribution_for_memory_recovery(
    tmp_path: Path,
) -> None:
    env, invocation_log = _fake_dev_environment(tmp_path)
    environment_log = tmp_path / "windows-environment.txt"
    env["FAKE_WINDOWS_ENV_LOG"] = str(environment_log)
    env["WSL_DISTRO_NAME"] = "Ubuntu-Test"
    env["WSLENV"] = "EXISTING/u"
    for variable_name in (
        "KENSHI_AGENT_OPENROUTER_MODEL",
        "KENSHI_AGENT_REASONING_EFFORT",
        "KENSHI_AGENT_ADVISOR_MODEL",
        "KENSHI_AGENT_ADVISOR_REASONING_EFFORT",
        "KENSHI_AGENT_ADVISOR_CADENCE_STEPS",
    ):
        env.pop(variable_name, None)

    assert _run_dev_in_pty(env, "telemetry") == 0
    assert _read_invocations(invocation_log)
    assert environment_log.read_text(encoding="utf-8").splitlines() == [
        "EXISTING/u:WSL_DISTRO_NAME/w"
    ]


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_dev_streams_windows_progress_before_the_process_exits(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_local_app_data = tmp_path / "LocalAppData"
    fake_windows_python = (
        fake_local_app_data
        / "KenshiAgent"
        / "venvs"
        / "kenshi-agent-env"
        / "Scripts"
        / "python.exe"
    )
    stream_probe = tmp_path / "stream_probe.py"
    stream_probe.write_text(
        """import time

print("WINDOWS_PROGRESS_READY")
time.sleep(3)
""",
        encoding="utf-8",
    )
    _write_executable(
        fake_bin / "powershell.exe",
        """#!/usr/bin/env bash
printf '%s\n' "$FAKE_LOCAL_APP_DATA"
""",
    )
    _write_executable(
        fake_bin / "wslpath",
        """#!/usr/bin/env bash
if [[ "${1:-}" == "-w" ]]; then
  shift
fi
printf '%s\n' "$1"
""",
    )
    _write_executable(
        fake_windows_python,
        """#!/usr/bin/env bash
python_flags=()
if [[ "${1:-}" == "-u" ]]; then
  python_flags+=("-u")
  shift
fi
exec "$FAKE_REAL_PYTHON" "${python_flags[@]}" "$FAKE_STREAM_PROBE"
""",
    )
    env = {
        **os.environ,
        "PATH": (
            f"{fake_bin}{os.pathsep}{Path(sys.executable).parent}"
            f"{os.pathsep}{os.environ['PATH']}"
        ),
        "FAKE_LOCAL_APP_DATA": str(fake_local_app_data),
        "FAKE_REAL_PYTHON": sys.executable,
        "FAKE_STREAM_PROBE": str(stream_probe),
    }

    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        [str(REPO_ROOT / "dev"), "telemetry"],
        cwd=REPO_ROOT,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)
    output = b""

    try:
        deadline = time.monotonic() + 1.5
        while b"WINDOWS_PROGRESS_READY" not in output:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([master_fd], [], [], remaining)
            if readable:
                output += os.read(master_fd, 4096)

        assert b"WINDOWS_PROGRESS_READY" in output
        assert process.poll() is None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        os.close(master_fd)
