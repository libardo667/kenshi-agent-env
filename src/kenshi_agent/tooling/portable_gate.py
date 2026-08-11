"""One local and CI authority for the complete portable verification gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

GENERATED_ROOTS = (Path("schemas"), Path("docs/generated"))

CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("install locked development environment", ("uv", "sync", "--frozen", "--extra", "dev")),
    (
        "Ruff",
        ("uv", "run", "--frozen", "--no-sync", "ruff", "check", "."),
    ),
    (
        "mypy",
        ("uv", "run", "--frozen", "--no-sync", "mypy", "src"),
    ),
    (
        "research evidence",
        (
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "python",
            "scripts/check_research_evidence.py",
        ),
    ),
    (
        "session event disposition generation",
        (
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "python",
            "scripts/export_session_event_dispositions.py",
        ),
    ),
    (
        "schema generation",
        (
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "python",
            "scripts/export_schemas.py",
        ),
    ),
    (
        "documentation generation",
        (
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "python",
            "scripts/export_docs.py",
        ),
    ),
    (
        "tests",
        (
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "pytest",
            "-q",
            "--color=no",
        ),
    ),
    ("whitespace errors", ("git", "diff", "--check")),
)


def _snapshot_generated(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for generated_root in GENERATED_ROOTS
        for path in sorted((root / generated_root).glob("**/*"))
        if path.is_file()
    }


def run_portable_gate(root: Path) -> int:
    """Run every portable check and fail if generation changes checked-in bytes."""

    before_generation = _snapshot_generated(root)
    for label, command in CHECKS:
        print(f"\n==> {label}", flush=True)
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode != 0:
            return result.returncode
        if label == "documentation generation":
            after_generation = _snapshot_generated(root)
            if after_generation != before_generation:
                print(
                    "Generated artifacts were stale; commit the regenerated files and rerun.",
                    flush=True,
                )
                return 1
    print("\nPortable verification passed.", flush=True)
    return 0
