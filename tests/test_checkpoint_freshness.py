"""Validate the structure of the durable checkpoint document."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = Path("docs/CHECKPOINT.md")


def _checkpoint_value(label: str) -> str:
    text = (ROOT / CHECKPOINT).read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(label)}\s{{2,}}(.+)$", text, flags=re.MULTILINE)
    assert match is not None, f"docs/CHECKPOINT.md has no `{label}` repository field"
    return match.group(1).strip()


def test_checkpoint_tracks_the_current_goal_commit() -> None:
    """Ensure docs/CHECKPOINT.md retains valid branch and commit metadata."""

    assert _checkpoint_value("integration branch") == "main"
    recorded_parent = _checkpoint_value("parent commit")
    assert re.fullmatch(r"[0-9a-f]{40}", recorded_parent), (
        "docs/CHECKPOINT.md `parent commit` must be one full lowercase Git hash"
    )