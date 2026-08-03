#!/usr/bin/env python3
"""Enforce Stage 0's temporary ceilings on the five demolition targets."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "reconstruction" / "demolition_ratchets.json"
DISPATCH_DISCRIMINATORS = {
    "action_kind",
    "action_type",
    "completion_owner",
    "execution",
    "kind",
    "operation_kind",
}


def _contains_dispatch_discriminator(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "isinstance"
        ):
            return True
        if isinstance(child, ast.Attribute) and child.attr in DISPATCH_DISCRIMINATORS:
            return True
        if isinstance(child, ast.Name) and child.id in DISPATCH_DISCRIMINATORS:
            return True
    return False


def _is_default_match_case(case: ast.match_case) -> bool:
    pattern = case.pattern
    return (
        isinstance(pattern, ast.MatchAs)
        and pattern.name is None
        and pattern.pattern is None
    )


def source_metrics(path: Path) -> dict[str, int]:
    """Return the two monotonic demolition metrics for one existing source."""

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    dispatch_branches = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp)):
            dispatch_branches += int(_contains_dispatch_discriminator(node.test))
        elif isinstance(node, ast.Match) and _contains_dispatch_discriminator(node.subject):
            dispatch_branches += sum(
                not _is_default_match_case(case) for case in node.cases
            )
    return {
        "lines": len(source.splitlines()),
        "semantic_dispatch_branches": dispatch_branches,
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("targets"), dict):
        raise ValueError(f"Unsupported reconstruction ratchet manifest: {path}")
    return payload


def check_ratchets(manifest_path: Path = DEFAULT_MANIFEST) -> list[str]:
    """Return every ceiling violation; deleted targets satisfy the ratchet."""

    payload = _load_manifest(manifest_path)
    violations: list[str] = []
    for relative_path, ceilings in sorted(payload["targets"].items()):
        path = ROOT / relative_path
        if not path.exists():
            continue
        metrics = source_metrics(path)
        for metric, actual in metrics.items():
            ceiling = ceilings[f"max_{metric}"]
            if actual > ceiling:
                violations.append(
                    f"{relative_path}: {metric} grew to {actual}; ceiling is {ceiling}"
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    violations = check_ratchets(args.manifest)
    if violations:
        for violation in violations:
            print(violation)
        return 1
    print("Reconstruction demolition ratchets pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
