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
    # --- the world screen -------------------------------------------------
    Affordance(
        Interface.WORLD,
        Operation.ENTER,
        "Return to the world with no window in the way.",
        Mechanism.CONTROL,
        "use_game_binding / dismiss_screen",
        native_entry_point="ForgottenGUI::closeAllWindows @ 0x6E5660",
        gap=(
            "Screens are closed one at a time by re-pressing their own toggle. "
            "A single closeAllWindows would still be a better escape hatch."
        ),
    ),
    Affordance(
        Interface.WORLD,
        Operation.NAVIGATE,
        "Pan and zoom the camera to bring somewhere into view.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="controls.cfg camera_* / camera_zoom_*",
    ),
    Affordance(
        Interface.WORLD,
        Operation.INTERACT,
        "Walk to a person and open dialogue.",
        Mechanism.NATIVE,
        "approach_dialogue_target",
        native_entry_point="native approach_confirmed_vendor",
    ),
    Affordance(
        Interface.WORLD,
        Operation.INTERACT,
        "Start, stop, or speed up time.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="controls.cfg pause=Space, speed_1..3=F2..F4",
    ),
    Affordance(
        Interface.WORLD,
        Operation.INTERACT,
        "Select which squad member is being commanded.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="controls.cfg character_next/prev, select_all, change_squad",
    ),
    Affordance(
        Interface.WORLD,
        Operation.EXIT,
        "The world screen is the base state; there is nothing to exit to.",
        Mechanism.NATIVE,
        "",
    ),
    # --- dialogue ---------------------------------------------------------
    Affordance(
        Interface.DIALOGUE,
        Operation.ENTER,
        "Open a conversation with a specific person.",
        Mechanism.NATIVE,
        "approach_dialogue_target",
    ),
    Affordance(
        Interface.DIALOGUE,
        Operation.NAVIGATE,
        "Read the available replies.",
        Mechanism.NATIVE,
        "",
        native_entry_point="ui.dialogue.options export",
    ),
    Affordance(
        Interface.DIALOGUE,
        Operation.INTERACT,
        "Choose a reply.",
        Mechanism.CONTROL,
        "activate_visible_control",
    ),
    Affordance(
        Interface.DIALOGUE,
        Operation.EXIT,
        "Leave the conversation.",
        Mechanism.CONTROL,
        "activate_visible_control",
        gap=(
            "Choose the exact visible closing reply. Escape is deliberately not "
            "used because it opens the ESC menu without ending dialogue."
        ),
    ),
    # --- inventory --------------------------------------------------------
    Affordance(
        Interface.INVENTORY,
        Operation.ENTER,
        "Open the squad member's own inventory.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="controls.cfg toggle_inventory=I",
        gap=(
            "Use open_inventory_windows as the exact count. active_screen is a "
            "collapsed label: inventory means no observed shop-owner window, "
            "while trade means one exact registered shop-owner window is open."
        ),
    ),
    Affordance(
        Interface.INVENTORY,
        Operation.NAVIGATE,
        "Reach cells that are scrolled out of view or in another section.",
        Mechanism.CONTROL,
        "scroll_screen",
        native_entry_point="InventorySectionGUI::refreshIcons @ 0x7106B0",
    ),
    Affordance(
        Interface.INVENTORY,
        Operation.INTERACT,
        "Read what an item actually is.",
        # Served by telemetry, not by a gesture: every item cell arrives already
        # carrying its name, value and quantity. This was a hover that waited on
        # a tooltip Kenshi never reports as visible, so it could only time out.
        Mechanism.NATIVE,
        "telemetry.ui.visible_controls[item].item_name",
        native_entry_point="InventoryItem::getName",
    ),
    Affordance(
        Interface.INVENTORY,
        Operation.INTERACT,
        "Equip an item from the selected character's own inventory.",
        Mechanism.CONTROL,
        "equip_item",
        native_entry_point="InventoryGUI::rightClickAutoEquipping @ 0x7137B0",
        gap=(
            "The equip route is contracted and portable-tested from observed live "
            "semantics; moving between sections and dropping still need drag-and-drop."
        ),
    ),
    Affordance(
        Interface.INVENTORY,
        Operation.EXIT,
        "Close the inventory window.",
        Mechanism.CONTROL,
        "dismiss_screen",
    ),
    # --- trade ------------------------------------------------------------
    Affordance(
        Interface.TRADE,
        Operation.ENTER,
        "Open a shop's stock for trading.",
        Mechanism.CONTROL,
        "activate_visible_control",
        native_entry_point="ForgottenGUI::showTradeWindow @ 0x7905D0",
    ),
    Affordance(
        Interface.TRADE,
        Operation.NAVIGATE,
        "Reach stock beyond the first screenful.",
        Mechanism.CONTROL,
        "scroll_screen",
    ),
    Affordance(
        Interface.TRADE,
        Operation.INTERACT,
        "Buy an item.",
        Mechanism.CONTROL,
        "purchase_item",
        native_entry_point="InventoryGUI::RClickAutoTrade @ 0x712AB0",
    ),
    Affordance(
        Interface.TRADE,
        Operation.INTERACT,
        "Sell an item.",
        Mechanism.CONTROL,
        "sell_item",
        native_entry_point="InventoryGUI::RClickAutoTrade @ 0x712AB0",
    ),
    Affordance(
        Interface.TRADE,
        Operation.EXIT,
        "Close the trade window.",
        Mechanism.CONTROL,
        "dismiss_screen",
        native_entry_point="ForgottenGUI::closeTradeWindow @ 0x790630",
    ),
    # --- character stats --------------------------------------------------
    Affordance(
        Interface.CHARACTER_STATS,
        Operation.ENTER,
        "Open the stats window to read skills and injuries.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="controls.cfg toggle_stats=C",
    ),
    Affordance(
        Interface.CHARACTER_STATS,
        Operation.EXIT,
        "Close the stats window.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="re-press toggle_stats",
        gap=(
            "Verified live. Same as the map: active_screen stays 'world', so "
            "`dismiss_screen` has nothing to bind to."
        ),
    ),
    # --- map --------------------------------------------------------------
    Affordance(
        Interface.MAP,
        Operation.ENTER,
        "Open the world map to decide where to travel.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="controls.cfg toggle_map=M",
        gap=(
            "Verified live. The map is a ManagementScreen tab and leaves "
            "active_screen on 'world'; management_screen_open is the signal."
        ),
    ),
    Affordance(
        Interface.MAP,
        Operation.NAVIGATE,
        "Pan and zoom to find a destination.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="controls.cfg camera_* while the map is open",
    ),
    Affordance(
        Interface.MAP,
        Operation.INTERACT,
        "Travel to a discovered settlement, an observed character, or along "
        "a bounded local bearing.",
        Mechanism.NATIVE,
        "travel_to_map_destination / move_to_character / move_in_direction",
        native_entry_point="PlayerInterface::newPlayerTaskSelectedCharacters MOVE_CUS_ORDERED",
        gap=(
            "Only player-discovered settlement markers are exported; exact "
            "world coordinates and undiscovered markers remain unavailable. "
            "The controller owns long-travel speed, safety monitoring, trailing "
            "camera, and terminal pause."
        ),
    ),
    Affordance(
        Interface.MAP,
        Operation.EXIT,
        "Close the map.",
        Mechanism.CONTROL,
        "use_game_binding",
        native_entry_point="re-press toggle_map",
        gap=(
            "Verified live. `dismiss_screen` cannot close it: it binds on "
            "active_screen, which the map leaves on 'world'."
        ),
    ),
    # --- message boxes ----------------------------------------------------
    Affordance(
        Interface.MESSAGE_BOX,
        Operation.ENTER,
        "Kenshi opens these itself to report a refusal.",
        Mechanism.NATIVE,
        "",
        native_entry_point="ForgottenGUI::messageBox @ 0x740F60",
    ),
    Affordance(
        Interface.MESSAGE_BOX,
        Operation.INTERACT,
        "Read why Kenshi refused an action.",
        Mechanism.CONTROL,
        "visible_controls role 'text'",
        native_entry_point="InventoryGUI::TradeResult::showMessage @ 0x70E570",
    ),
    Affordance(
        Interface.MESSAGE_BOX,
        Operation.EXIT,
        "Acknowledge the box so the interface is usable again.",
        Mechanism.CONTROL,
        "activate_visible_control",
    ),
    # --- the escape menu --------------------------------------------------
    Affordance(
        Interface.ESC_MENU,
        Operation.ENTER,
        "Open the game menu (mainly a hazard: Escape lands here by accident).",
        Mechanism.PIXEL,
        "",
        gap="Reached only as a side effect of pressing Escape with nothing else open.",
    ),
    Affordance(
        Interface.ESC_MENU,
        Operation.EXIT,
        "Get back out of the menu without quitting.",
        Mechanism.CONTROL,
        "activate_visible_control",
        gap=(
            "Use the exact current Resume control. The route is catalogued but "
            "has not yet been measured live."
        ),
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
