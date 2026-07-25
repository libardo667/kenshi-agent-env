"""What the agent can know, and what it costs to find out.

Every fact the agent needs is in one of three states:

* **exported** — present in a telemetry snapshot, free, no action required;
* **discoverable** — obtainable, but only by *acting* (hovering a cell, opening
  a screen), which costs a model round-trip of roughly twenty seconds and risks
  the plan going stale before the answer arrives;
* **dark** — not obtainable at all, so any goal depending on it is unreachable.

The distinction is the difference between an agent that reads a shop and one
that hovers forty cells one at a time. This module names the facts a Kenshi
agent actually needs and checks a live snapshot against them, so "what is still
missing?" is a command rather than an argument.

The registry is deliberately hand-written: it encodes what *play* requires,
which no amount of introspection over the telemetry model can tell us. A field
existing in the schema proves nothing — several are declared and never emitted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .models import TelemetrySnapshot


class FactState(StrEnum):
    EXPORTED = "exported"
    DISCOVERABLE = "discoverable"
    DARK = "dark"
    # The snapshot cannot speak to this fact: dialogue options say nothing when
    # no conversation is open. Reporting those as missing would be a lie, and a
    # lie that hides the real gaps behind noise.
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing the agent may need to know in order to play."""

    key: str
    purpose: str
    # How many agent actions it takes to learn this when it is not exported.
    # Zero means reading the snapshot is enough.
    discovery_actions: int
    # How it is obtained when not exported, or "" when it simply is not.
    discovery: str
    present: Callable[[TelemetrySnapshot], bool]
    # When this fact is meaningful at all. Defaults to always.
    applies: Callable[[TelemetrySnapshot], bool] = lambda _snapshot: True

    def state(self, snapshot: TelemetrySnapshot) -> FactState:
        if not self.applies(snapshot):
            return FactState.NOT_APPLICABLE
        if self.present(snapshot):
            return FactState.EXPORTED
        return FactState.DISCOVERABLE if self.discovery else FactState.DARK


def _selected(snapshot: TelemetrySnapshot) -> Any:
    return next(
        (character for character in snapshot.squad if character.selected),
        snapshot.squad[0] if snapshot.squad else None,
    )


def _selected_field(name: str) -> Callable[[TelemetrySnapshot], bool]:
    def check(snapshot: TelemetrySnapshot) -> bool:
        character = _selected(snapshot)
        return character is not None and getattr(character, name, None) is not None

    return check


def _trading(snapshot: TelemetrySnapshot) -> bool:
    """Whether a trading or inventory window is actually open."""

    return bool(
        snapshot.ui.active_screen in {"trade", "inventory"}
        or (snapshot.ui.open_inventory_windows or 0) > 0
    )


def _item_cells(snapshot: TelemetrySnapshot) -> list[Any]:
    return [
        control
        for control in (snapshot.ui.visible_controls or [])
        if control.role == "item"
    ]


FACTS: tuple[Fact, ...] = (
    # --- what the agent is, and needs -------------------------------------
    Fact(
        "self.hunger",
        "Whether to eat at all. The core survival loop starts here.",
        1,
        "open the character's stats window and read it",
        _selected_field("hunger"),
    ),
    Fact(
        "self.inventory",
        "Whether it already owns what it is about to go buy.",
        2,
        "open the inventory window and hover each cell",
        lambda s: bool(_selected(s) is not None and _selected(s).inventory),
    ),
    Fact(
        "self.health",
        "Whether to heal, flee, or rest.",
        1,
        "open the character's stats window",
        lambda s: bool(_selected(s) is not None and _selected(s).body_parts),
    ),
    Fact(
        "self.first_aid_kits",
        "Whether healing is even possible.",
        2,
        "open the inventory and identify kits",
        _selected_field("first_aid_kits"),
    ),
    Fact(
        "self.current_goal",
        "What Kenshi itself thinks the character is doing.",
        0,
        "",
        _selected_field("current_goal"),
    ),
    # --- where and when ---------------------------------------------------
    Fact(
        "world.location_name",
        "Where the character is. Any travel goal is unreachable without it.",
        0,
        "",
        lambda s: s.game.location_name is not None,
    ),
    Fact(
        "world.clock",
        "Time of day, for anything schedule- or night-dependent.",
        0,
        "",
        lambda s: s.game.hour is not None,
    ),
    Fact(
        "world.money",
        "What it can afford.",
        0,
        "",
        lambda s: s.game.money is not None,
    ),
    # --- trading ----------------------------------------------------------
    Fact(
        "shop.item_names",
        "What is for sale. Without it every cell must be hovered blind.",
        1,
        "hover each cell and read the tooltip",
        lambda s: any(
            cell.label and not cell.label.startswith("item_") for cell in _item_cells(s)
        ),
        _trading,
    ),
    Fact(
        "shop.item_price",
        "Affordability, per item.",
        1,
        "hover the cell and parse the tooltip",
        lambda s: False,
        _trading,
    ),
    Fact(
        "shop.item_category",
        "Whether an item is food, a weapon, armour. Needed for any 'buy X' goal.",
        1,
        "hover the cell and parse the tooltip",
        lambda s: False,
        _trading,
    ),
    Fact(
        "shop.item_quantity",
        "Stack size, for how much is actually on offer.",
        1,
        "hover the cell and read the tooltip",
        lambda s: False,
        _trading,
    ),
    Fact(
        "shop.trader_money",
        "What the trader can pay when selling to them.",
        0,
        "",
        lambda s: False,
        _trading,
    ),
    # --- interaction ------------------------------------------------------
    Fact(
        "ui.dialogue_options",
        "The choices in a conversation.",
        0,
        "",
        lambda s: s.ui.dialogue_options is not None,
        lambda s: bool(s.ui.dialogue_open),
    ),
    Fact(
        "ui.visible_controls",
        "What can be clicked at all.",
        0,
        "",
        lambda s: s.ui.visible_controls is not None,
    ),
    Fact(
        "ui.screen",
        "Which interface is open, so entering and leaving are verifiable.",
        0,
        "",
        lambda s: s.ui.active_screen is not None,
    ),
    Fact(
        "nearby.dialogue_targets",
        "Who can be approached and talked to.",
        0,
        "",
        lambda s: bool(s.nearby_entities),
    ),
)


@dataclass(frozen=True, slots=True)
class CoverageReport:
    exported: tuple[Fact, ...]
    discoverable: tuple[Fact, ...]
    dark: tuple[Fact, ...]
    not_applicable: tuple[Fact, ...] = ()

    @property
    def exploration_cost(self) -> int:
        """Agent actions needed to learn everything not already exported.

        Each of these is a model round-trip, so this is the number to drive
        down: it is roughly the cost of the agent orienting itself once.
        """

        return sum(fact.discovery_actions for fact in self.discoverable)

    def as_lines(self) -> list[str]:
        lines = [
            f"exported     {len(self.exported):3d}",
            f"discoverable {len(self.discoverable):3d}  "
            f"({self.exploration_cost} agent actions to learn)",
            f"dark         {len(self.dark):3d}",
            f"n/a here     {len(self.not_applicable):3d}",
            "",
        ]
        for label, facts in (
            ("DARK (goal-blocking)", self.dark),
            ("DISCOVERABLE (costs round-trips)", self.discoverable),
            ("EXPORTED", self.exported),
        ):
            if not facts:
                continue
            lines.append(label)
            for fact in facts:
                suffix = f"  <- {fact.discovery}" if fact.discovery else ""
                lines.append(f"  {fact.key:28s} {fact.purpose}{suffix}")
            lines.append("")
        return lines


def audit(snapshot: TelemetrySnapshot) -> CoverageReport:
    """Classify every known fact against one live telemetry snapshot."""

    buckets: dict[FactState, list[Fact]] = {state: [] for state in FactState}
    for fact in FACTS:
        buckets[fact.state(snapshot)].append(fact)
    return CoverageReport(
        exported=tuple(buckets[FactState.EXPORTED]),
        discoverable=tuple(buckets[FactState.DISCOVERABLE]),
        dark=tuple(buckets[FactState.DARK]),
        not_applicable=tuple(buckets[FactState.NOT_APPLICABLE]),
    )
