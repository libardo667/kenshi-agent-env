#!/usr/bin/env python3
"""Capture Kenshi's shipped GUI declaration into game_sources.

Reads `data/gui/layout/*.layout` from an installed Kenshi and writes the parsed
declaration - tree, types, skins, captions - to
`game_sources/kenshi/gui_layouts.json`.

Run this against a pinned install when the game updates. Everything downstream
reads the capture, never the install, so the repository stays reproducible on a
machine with no Kenshi.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kenshi_agent.core.gui_declaration import (  # noqa: E402
    GUI_LAYOUTS_SNAPSHOT,
    parse_layout_directory,
    vocabulary_payload,
)

DEFAULT_INSTALL = Path(
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/Kenshi/data/gui/layout"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout-dir", type=Path, default=DEFAULT_INSTALL)
    parser.add_argument("--output", type=Path, default=GUI_LAYOUTS_SNAPSHOT)
    args = parser.parse_args()

    if not args.layout_dir.is_dir():
        print(f"No layout directory at {args.layout_dir}", file=sys.stderr)
        return 1

    vocabulary = parse_layout_directory(args.layout_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(vocabulary_payload(vocabulary), indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"{args.output}: {len(vocabulary.layouts)} layouts, "
        f"{vocabulary.widget_count} widgets, "
        f"{vocabulary.named_widget_count} named"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
