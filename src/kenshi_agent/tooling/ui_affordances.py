"""What the agent can do inside the interface families already modeled.

`fact_coverage` answers "what can the agent know?". This is its other half:
what can the agent *operate*? An agent that can read a shop perfectly and has
no way to leave the shop is not playing the game.

Every interface Kenshi puts on screen is broken into the four operations that
make it navigable at all:

* **ENTER** — get the screen open from wherever we are;
* **NAVIGATE** — move around inside it (scroll, page, pan, switch section);
* **INTERACT** — do the thing the screen exists for;
* **EXIT** — get back out, reliably, from any state.

A missing EXIT is the worst kind of gap, because it is the one that strands a
continuously-running agent: it cannot be recovered from by replanning, only by
a human. A missing ENTER merely makes a goal unreachable.

Each affordance also records its **mechanism**, because that is the thing that
keeps being wrong. Three mechanisms, in descending order of reliability:

* `NATIVE` — a native command calling Kenshi's own method. Deterministic; the
  game either does it or tells us why not.
* `CONTROL` — click an exported widget by its observed bounds. Reliable only
  when the widget is exported and actually hit-testable.
* `PIXEL` — click a calibrated screen coordinate. Breaks on resolution change,
  UI layout change, and anything overlapping. Always a stopgap.

This registry is hand-written and therefore cannot be the denominator for game
parity. It remains useful as a navigation audit inside the interface families
we already know, especially for detecting missing exits. Game-derived source
adapters own parity and expansion detection.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Interface(StrEnum):
    WORLD = "world"
    DIALOGUE = "dialogue"
    INVENTORY = "inventory"
    TRADE = "trade"
    PROSPECTING = "prospecting"
    CHARACTER_STATS = "character_stats"
    MAP = "map"
    MESSAGE_BOX = "message_box"
    ESC_MENU = "esc_menu"


class Operation(StrEnum):
    ENTER = "enter"
    NAVIGATE = "navigate"
    INTERACT = "interact"
    EXIT = "exit"


class Mechanism(StrEnum):
    NATIVE = "native"
    CONTROL = "control"
    PIXEL = "pixel"
    # Nothing implements this yet.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Affordance:
    interface: Interface
    operation: Operation
    # What the agent is trying to accomplish, in play terms.
    purpose: str
    mechanism: Mechanism
    # The semantic action that provides this, or "" when nothing does.
    action: str
    # Kenshi's own entry point, when we know it. Recorded even for gaps,
    # because it is the implementation route for closing them.
    native_entry_point: str = ""
    # Why this is not covered, when it is not.
    gap: str = ""

    @property
    def covered(self) -> bool:
        return self.mechanism is not Mechanism.NONE

    @property
    def strands_the_agent(self) -> bool:
        """An uncovered EXIT is what turns a bad plan into a stuck run."""

        return self.operation is Operation.EXIT and not self.covered


AFFORDANCES: tuple[Affordance, ...] = (
    Affordance(
        Interface.WORLD,
        Operation.ENTER,
        "Return to the world with no modal window in the way.",
        Mechanism.NATIVE,
        "close_active_interface",
        native_entry_point="ForgottenGUI::closeAllWindows @ 0x6E5660",
    ),
    Affordance(
        Interface.WORLD,
        Operation.NAVIGATE,
        "Travel by observed identity, discovered destination, or bounded bearing.",
        Mechanism.NATIVE,
        "move_to_character / travel_to_map_destination / move_in_direction",
    ),
    Affordance(
        Interface.WORLD,
        Operation.INTERACT,
        "Issue an exact task Kenshi advertises on a person or world object.",
        Mechanism.NATIVE,
        "perform_character_order / perform_context_action",
    ),
    Affordance(
        Interface.WORLD,
        Operation.INTERACT,
        "Pause, resume, or select a playback speed.",
        Mechanism.NATIVE,
        "pause / set_speed",
    ),
    Affordance(
        Interface.WORLD,
        Operation.INTERACT,
        "Select one exact squad member.",
        Mechanism.NATIVE,
        "select_squad_member_exact",
    ),
    Affordance(
        Interface.WORLD,
        Operation.EXIT,
        "The world is the base state; there is nothing to exit to.",
        Mechanism.NATIVE,
        "",
    ),
    Affordance(
        Interface.DIALOGUE,
        Operation.ENTER,
        "Open a conversation with a specific observed person.",
        Mechanism.NATIVE,
        "approach_dialogue_target",
    ),
    Affordance(
        Interface.DIALOGUE,
        Operation.NAVIGATE,
        "Read the available replies.",
        Mechanism.NATIVE,
        "telemetry.ui.dialogue.options",
    ),
    Affordance(
        Interface.DIALOGUE,
        Operation.INTERACT,
        "Choose one exact reply.",
        Mechanism.NONE,
        "",
        gap="The retired visible-control click has no native replacement yet.",
    ),
    Affordance(
        Interface.DIALOGUE,
        Operation.EXIT,
        "Leave the conversation.",
        Mechanism.NATIVE,
        "close_active_interface",
        native_entry_point="DialogueWindow::hide @ 0x720D50",
    ),
    Affordance(
        Interface.INVENTORY,
        Operation.ENTER,
        "Pair two observed owners so their inventories are open together.",
        Mechanism.NATIVE,
        "open_trade_window",
        native_entry_point="ForgottenGUI::showTradeWindow @ 0x7905D0",
    ),
    Affordance(
        Interface.INVENTORY,
        Operation.NAVIGATE,
        "Address an item by its exported section and slot.",
        Mechanism.NATIVE,
        "telemetry.ui.open_inventories",
    ),
    Affordance(
        Interface.INVENTORY,
        Operation.INTERACT,
        "Transfer an item between the two open inventories.",
        Mechanism.NATIVE,
        "transfer_item",
    ),
    Affordance(
        Interface.INVENTORY,
        Operation.EXIT,
        "Close the paired inventory windows.",
        Mechanism.NATIVE,
        "close_active_interface",
        native_entry_point="ForgottenGUI::closeTradeWindow @ 0x790630",
    ),
    Affordance(
        Interface.TRADE,
        Operation.ENTER,
        "Open a money-trading, looting, or automatic transfer window.",
        Mechanism.NATIVE,
        "open_trade_window",
    ),
    Affordance(
        Interface.TRADE,
        Operation.NAVIGATE,
        "Read every exported inventory section and slot.",
        Mechanism.NATIVE,
        "telemetry.ui.open_inventories",
    ),
    Affordance(
        Interface.TRADE,
        Operation.INTERACT,
        "Buy, sell, give, loot, or collect through one inventory-model transfer.",
        Mechanism.NATIVE,
        "transfer_item",
        native_entry_point=(
            "Inventory::removeItemDontDestroy_returnsItem / Inventory::tryAddItem"
        ),
    ),
    Affordance(
        Interface.TRADE,
        Operation.EXIT,
        "Close the trade window.",
        Mechanism.NATIVE,
        "close_active_interface",
        native_entry_point="ForgottenGUI::closeTradeWindow @ 0x790630",
    ),
    Affordance(
        Interface.PROSPECTING,
        Operation.ENTER,
        "Read the local resource field without leaving its source window open.",
        Mechanism.NATIVE,
        "survey_local_resources",
        native_entry_point="ProspectingWindow::showT / _show / hide",
    ),
    Affordance(
        Interface.PROSPECTING,
        Operation.NAVIGATE,
        "Read the exact published resource rows.",
        Mechanism.NATIVE,
        "telemetry.resource_survey.readings",
    ),
    Affordance(
        Interface.PROSPECTING,
        Operation.EXIT,
        "Return to the prior interface as part of the survey transaction.",
        Mechanism.NATIVE,
        "survey_local_resources / close_active_interface",
        native_entry_point="ProspectingWindow::hide @ 0x48D6A0",
    ),
    Affordance(
        Interface.CHARACTER_STATS,
        Operation.ENTER,
        "Open the character stats window.",
        Mechanism.NONE,
        "",
        gap="No current operation opens management UI.",
    ),
    Affordance(
        Interface.CHARACTER_STATS,
        Operation.INTERACT,
        "Read exported roster skills, health, and state.",
        Mechanism.NATIVE,
        "telemetry.roster",
    ),
    Affordance(
        Interface.CHARACTER_STATS,
        Operation.EXIT,
        "Close the character stats window.",
        Mechanism.NATIVE,
        "close_active_interface",
    ),
    Affordance(
        Interface.MAP,
        Operation.ENTER,
        "Open the world map.",
        Mechanism.NONE,
        "",
        gap="Travel no longer requires opening the map, but the UI itself is unreachable.",
    ),
    Affordance(
        Interface.MAP,
        Operation.NAVIGATE,
        "Pan or zoom the open map.",
        Mechanism.NONE,
        "",
        gap="No current operation manipulates map presentation.",
    ),
    Affordance(
        Interface.MAP,
        Operation.INTERACT,
        "Travel to an exact discovered settlement marker.",
        Mechanism.NATIVE,
        "travel_to_map_destination",
    ),
    Affordance(
        Interface.MAP,
        Operation.EXIT,
        "Close the map.",
        Mechanism.NATIVE,
        "close_active_interface",
    ),
    Affordance(
        Interface.MESSAGE_BOX,
        Operation.ENTER,
        "Kenshi opens a refusal message itself.",
        Mechanism.NATIVE,
        "",
        native_entry_point="ForgottenGUI::messageBox @ 0x740F60",
    ),
    Affordance(
        Interface.MESSAGE_BOX,
        Operation.INTERACT,
        "Read the refusal text.",
        Mechanism.NATIVE,
        "telemetry.ui.visible_controls[text]",
    ),
    Affordance(
        Interface.MESSAGE_BOX,
        Operation.EXIT,
        "Acknowledge the message.",
        Mechanism.NATIVE,
        "close_active_interface",
        native_entry_point="ForgottenGUI::hideMessageBox @ 0x73EA10",
    ),
    Affordance(
        Interface.ESC_MENU,
        Operation.ENTER,
        "Open the game menu.",
        Mechanism.NONE,
        "",
        gap="No current runtime operation opens the escape menu.",
    ),
    Affordance(
        Interface.ESC_MENU,
        Operation.EXIT,
        "Return from the menu without quitting.",
        Mechanism.NATIVE,
        "close_active_interface",
        native_entry_point="ForgottenGUI::closeAllWindows @ 0x6E5660",
    ),
)


@dataclass(frozen=True, slots=True)
class AffordanceReport:
    covered: tuple[Affordance, ...]
    missing: tuple[Affordance, ...]

    @property
    def stranding_gaps(self) -> tuple[Affordance, ...]:
        """Missing exits — the gaps that need a human to recover from."""

        return tuple(a for a in self.missing if a.strands_the_agent)

    @property
    def pixel_dependencies(self) -> tuple[Affordance, ...]:
        """Covered, but by the mechanism that silently stops working."""

        return tuple(a for a in self.covered if a.mechanism is Mechanism.PIXEL)

    def as_lines(self) -> list[str]:
        total = len(self.covered) + len(self.missing)
        lines = [
            f"modeled rows  {total:3d}",
            f"implemented   {len(self.covered):3d}",
            f"unimplemented {len(self.missing):3d}",
            f"stranding {len(self.stranding_gaps):3d}  (missing exits)",
            f"on pixels {len(self.pixel_dependencies):3d}  (covered, but fragile)",
            "",
        ]
        for interface in Interface:
            rows = [a for a in AFFORDANCES if a.interface is interface]
            if not rows:
                continue
            lines.append(interface.value.upper())
            for affordance in rows:
                mark = "ok " if affordance.covered else "GAP"
                detail = affordance.action or affordance.native_entry_point or "-"
                lines.append(
                    f"  {mark} {affordance.operation.value:9s} "
                    f"{affordance.purpose}  [{affordance.mechanism.value}: {detail}]"
                )
                if affordance.gap:
                    lines.append(f"        -> {affordance.gap}")
            lines.append("")
        return lines


def audit(affordances: Iterable[Affordance] = AFFORDANCES) -> AffordanceReport:
    """Split the affordance registry into what works and what does not."""

    rows = tuple(affordances)
    return AffordanceReport(
        covered=tuple(a for a in rows if a.covered),
        missing=tuple(a for a in rows if not a.covered),
    )
