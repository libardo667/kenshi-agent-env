import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

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
done
""",
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
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
            str(REPO_ROOT / "config" / "live.burnin.yaml"),
        ]
    ]


@pytest.mark.skipif(os.name == "nt", reason="the ./dev wrapper is WSL-only")
def test_dev_recovery_also_detaches_from_an_inherited_pty(
    tmp_path: Path,
) -> None:
    env, invocation_log = _fake_dev_environment(tmp_path)
    env["FAKE_FAIL_COMMAND"] = "journey"

    assert _run_dev_in_pty(env, "journey") == 23
    assert _read_invocations(invocation_log) == [
        [
            "-u",
            "-m",
            "kenshi_agent.live_dev",
            "journey",
            "--config",
            str(REPO_ROOT / "config" / "live.longform.yaml"),
        ],
        [
            "-u",
            "-m",
            "kenshi_agent.live_dev",
            "recover",
            "--config",
            str(REPO_ROOT / "config" / "live.longform.yaml"),
        ],
    ]


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
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
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
