"""The widget surface's denominator comes from Kenshi, not from us.

The binding surface taught this: a denominator we write can only ever report
gaps we already thought of. Kenshi ships `data/gui/layout/*.layout`, its own
enumeration of every window and named widget, and that is captured verbatim
here the way `controls.cfg` was.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

WIDGET_SNAPSHOT = (
    Path(__file__).resolve().parents[1]
    / "game_sources"
    / "kenshi"
    / "gui_layout_widgets.tsv"
)
INSTALLED_LAYOUTS = Path(
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/Kenshi/data/gui/layout"
)


def _rows() -> list[dict[str, str]]:
    lines = [
        line
        for line in WIDGET_SNAPSHOT.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    return list(csv.DictReader(lines, delimiter="\t"))


def test_the_captured_denominator_is_a_real_enumeration() -> None:
    rows = _rows()
    layouts = {row["layout"] for row in rows}
    names = {row["widget"] for row in rows}

    assert len(rows) == 679
    assert len(layouts) == 52
    assert len(names) == 563
    # Screens the agent has never driven must be present, or the capture is
    # just a record of what we already reach.
    for unreached in (
        "Kenshi_BuildWindow.layout",
        "Kenshi_InventoryResearchWindow.layout",
        "Kenshi_ProspectingWindow.layout",
        "Kenshi_OverviewWindow.layout",
    ):
        assert unreached in layouts


def test_widget_names_are_not_unique_across_layouts() -> None:
    """46 of 563 names repeat, so a name cannot identify a window on its own.

    This is why per-window layout attribution needs minimum evidence and a
    uniqueness check rather than a best-overlap guess: a lone `BorderPanel` or
    `Root` matches many layouts equally well. Parity itself is unaffected,
    because it asks whether a name was ever observed, not which window it was.
    """

    rows = _rows()
    owners: dict[str, set[str]] = {}
    for row in rows:
        owners.setdefault(row["widget"], set()).add(row["layout"])
    ambiguous = {name for name, layouts in owners.items() if len(layouts) > 1}

    assert len(ambiguous) == 46
    assert "BorderPanel" in ambiguous


def test_the_capture_declares_what_it_does_not_cover() -> None:
    """Silent absence is how `31/31 covered` happened. State the boundary."""

    header = WIDGET_SNAPSHOT.read_text(encoding="utf-8")
    assert "SCOPE: base Kenshi only" in header
    assert "RE_Kenshi" in header


@pytest.mark.skipif(
    not INSTALLED_LAYOUTS.is_dir(),
    reason="Kenshi layouts are unavailable on this host",
)
def test_the_capture_still_matches_the_installed_game() -> None:
    import xml.etree.ElementTree as ET

    installed = set()
    for path in INSTALLED_LAYOUTS.glob("*.layout"):
        for widget in ET.parse(path).getroot().iter("Widget"):
            if widget.get("name"):
                installed.add((path.name, widget.get("name")))
    captured = {(row["layout"], row["widget"]) for row in _rows()}

    assert captured == installed
