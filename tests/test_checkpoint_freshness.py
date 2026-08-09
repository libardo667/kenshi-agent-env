"""Keep the durable checkpoint in the same goal commit as repository changes."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = Path("docs/CHECKPOINT.md")


def _git(*args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _checkpoint_value(label: str) -> str:
    text = (ROOT / CHECKPOINT).read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(label)}\s{{2,}}(.+)$", text, flags=re.MULTILINE)
    assert match is not None, f"docs/CHECKPOINT.md has no `{label}` repository field"
    return match.group(1).strip()


def test_checkpoint_tracks_the_current_goal_commit() -> None:
    """Accept one updated candidate before commit and the same state after commit."""

    head = _git("rev-parse", "HEAD")
    repository_status = _git("status", "--porcelain=v1", "--untracked-files=all")
    checkpoint_status = _git(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        CHECKPOINT.as_posix(),
    )

    assert _checkpoint_value("integration branch") == "main"
    recorded_parent = _checkpoint_value("parent commit")
    assert re.fullmatch(r"[0-9a-f]{40}", recorded_parent), (
        "docs/CHECKPOINT.md `parent commit` must be one full lowercase Git hash"
    )

    if repository_status:
        assert checkpoint_status, (
            "The repository has an uncommitted goal candidate but docs/CHECKPOINT.md "
            "is unchanged. Refresh the checkpoint in the same slice."
        )
        expected_parent = head
        state = "uncommitted goal candidate"
    else:
        checkpoint_commit = _git(
            "log",
            "-1",
            "--format=%H",
            "--",
            CHECKPOINT.as_posix(),
        )
        assert checkpoint_commit == head, (
            "docs/CHECKPOINT.md was not updated by HEAD. Every completed goal commit "
            "must refresh the checkpoint."
        )
        expected_parent = _git("rev-parse", "HEAD^")
        state = "committed goal"

    assert recorded_parent == expected_parent, (
        f"docs/CHECKPOINT.md records parent {recorded_parent}, but the {state} "
        f"requires {expected_parent}"
    )
