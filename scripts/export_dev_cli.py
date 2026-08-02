#!/usr/bin/env python3
"""Export the parser-owned ``./dev`` command reference."""

from __future__ import annotations

from pathlib import Path

from kenshi_agent.dev_cli import export_reference

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    print(export_reference(ROOT / "docs" / "generated" / "DEV_CLI.md"))
