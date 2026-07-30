#!/usr/bin/env python3
"""Fold local run evidence into the committed observed-blocker ledger.

`scripts/export_docs.py` regenerates the ledger from committed inputs only, so
it never invents rows on a machine that has no bundles. This script is the one
that reads `runs/` and is therefore the only way new failure evidence enters
the record.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kenshi_agent.blocker_ledger import (  # noqa: E402
    DEFAULT_SCAN_LIMIT,
    LEDGER_NAME,
    export_blocker_ledger,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=ROOT / "runs",
        help="Directory of run bundles to read (default: <repo>/runs).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SCAN_LIMIT,
        help=(
            "How many of the newest bundles to read. The corpus is gigabytes; "
            "this bounds the evidence, never the record."
        ),
    )
    args = parser.parse_args()

    generated = ROOT / "docs" / "generated"
    path = export_blocker_ledger(
        generated,
        runs_dir=args.runs_dir,
        existing=generated / LEDGER_NAME,
        limit=args.limit,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
