#!/usr/bin/env python3
"""Fold local runtime context-menu captures into the committed witness set."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kenshi_agent.tooling.context_action_parity import (  # noqa: E402
    DEFAULT_SCAN_LIMIT,
    WITNESSES_PATH,
    load_witnesses,
    witnesses_from_runs,
    write_witnesses,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    parser.add_argument("--limit", type=int, default=DEFAULT_SCAN_LIMIT)
    args = parser.parse_args()

    witnesses = load_witnesses(WITNESSES_PATH)
    witnesses.update(witnesses_from_runs(args.runs_dir, args.limit))
    print(write_witnesses(WITNESSES_PATH, witnesses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
