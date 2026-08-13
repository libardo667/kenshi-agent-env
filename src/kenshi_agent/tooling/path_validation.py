"""Fail-closed validation for repository-relative authority files."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


def validate_repository_file(
    raw_ref: str | Path,
    *,
    root: Path,
    label: str,
    require_tracked: bool = False,
) -> Path:
    """Resolve one regular file without crossing symlinks or repository bounds."""

    candidate = Path(raw_ref)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{label} escapes repository")
    lexical = Path(os.path.abspath(root / candidate))
    root = Path(os.path.abspath(root))
    if lexical != root and root not in lexical.parents:
        raise ValueError(f"{label} escapes repository")
    for component in (lexical, *lexical.parents):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError as exc:
            raise ValueError(f"{label} is missing") from exc
        except OSError as exc:
            raise ValueError(f"{label} cannot be inspected safely") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"{label} traverses a symlink")
        if component == root:
            break
    if not stat.S_ISREG(os.lstat(lexical).st_mode):
        raise ValueError(f"{label} is not a regular file")
    if require_tracked:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", candidate.as_posix()],
            capture_output=True,
            check=False,
        ).returncode == 0
        if not tracked:
            raise ValueError(f"{label} is untracked")
    return lexical
