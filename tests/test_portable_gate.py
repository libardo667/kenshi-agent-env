from __future__ import annotations

import subprocess
from pathlib import Path

from kenshi_agent.tooling import portable_gate


def test_portable_gate_runs_the_complete_check_sequence(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(portable_gate.subprocess, "run", run)  # type: ignore[attr-defined]

    assert portable_gate.run_portable_gate(tmp_path) == 0
    assert calls == [command for _label, command in portable_gate.CHECKS]


def test_portable_gate_fails_when_generation_changes_checked_in_bytes(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    generated = tmp_path / "schemas" / "example.json"
    generated.parent.mkdir(parents=True)
    generated.write_text("old\n", encoding="utf-8")

    def run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "scripts/export_docs.py":
            generated.write_text("new\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(portable_gate.subprocess, "run", run)  # type: ignore[attr-defined]

    assert portable_gate.run_portable_gate(tmp_path) == 1
