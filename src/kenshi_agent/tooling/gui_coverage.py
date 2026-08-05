"""How much of Kenshi's declared interface the agent can currently perceive.

The same reconciliation the binding and order surfaces already get, applied to
the GUI: Kenshi ships every window it can draw, so the denominator is the
game's, not this project's. What the controller models against that is the
coverage, and what it does not is the queue.

The flat `gui_layout_widgets.tsv` capture answered "does this widget exist".
This answers "which of these can the agent see, name, and act on", which is the
question that decides whether a window is usable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.gui_declaration import DeclaredLayout, load_gui_vocabulary

# Interfaces the controller models, taken from the project's own enum rather
# than restated here, so this cannot drift from what is actually implemented.
from .ui_affordances import Interface


@dataclass(frozen=True, slots=True)
class LayoutCoverage:
    layout: str
    declared_widgets: int
    named_widgets: int
    actionable_widgets: int
    modeled: bool

    @property
    def window_caption(self) -> str:
        return self._caption

    _caption: str = ""


@dataclass(frozen=True, slots=True)
class GuiCoverage:
    layouts: tuple[LayoutCoverage, ...]
    modeled_interfaces: tuple[str, ...]
    source_sha256: str

    @property
    def total_layouts(self) -> int:
        return len(self.layouts)

    @property
    def total_named(self) -> int:
        return sum(entry.named_widgets for entry in self.layouts)

    @property
    def total_actionable(self) -> int:
        return sum(entry.actionable_widgets for entry in self.layouts)

    @property
    def modeled_layouts(self) -> int:
        return sum(1 for entry in self.layouts if entry.modeled)


# Widget types a player can act on, as opposed to read. A window's usefulness
# to an agent is bounded by these, not by its total widget count.
ACTIONABLE_WIDGET_TYPES: frozenset[str] = frozenset(
    {"Button", "EditBox", "ComboBox", "ListBox", "ItemBox", "TabControl", "ScrollBar"}
)


def _layout_caption(entry: DeclaredLayout) -> str:
    """The window's authored caption, which names it for a reader."""

    root = entry.by_name("Root") or entry.by_name("_Main")
    return root.caption if root is not None else ""


def _modeled_layout_names() -> frozenset[str]:
    """Layouts the project models an interface for.

    Matched on the interface registry's own names appearing in a layout file
    name, so adding an interface updates this without a second list.
    """

    modeled = set()
    vocabulary = load_gui_vocabulary()
    for interface in Interface:
        # `character_stats` -> `characterstats`, matched against `StatsWindow`
        # by its last word, since layout names are not the enum's names.
        token = interface.value.split("_")[-1].casefold()
        if token == "world":
            continue
        for entry in vocabulary.layouts:
            stem = entry.layout.replace("Kenshi_", "").replace(".layout", "")
            if token in stem.replace("_", "").casefold():
                modeled.add(entry.layout)
    return frozenset(modeled)


def assess_gui_coverage() -> GuiCoverage:
    """Reconcile Kenshi's declared interface against what the project models."""

    vocabulary = load_gui_vocabulary()
    modeled = _modeled_layout_names()
    layouts = tuple(
        LayoutCoverage(
            layout=entry.layout,
            declared_widgets=len(entry.widgets),
            named_widgets=len(entry.named()),
            actionable_widgets=sum(
                1
                for widget in entry.named()
                if widget.widget_type in ACTIONABLE_WIDGET_TYPES
            ),
            modeled=entry.layout in modeled,
            _caption=_layout_caption(entry),
        )
        for entry in vocabulary.layouts
    )
    return GuiCoverage(
        layouts=layouts,
        modeled_interfaces=tuple(sorted(interface.value for interface in Interface)),
        source_sha256=vocabulary.sha256,
    )


def render_gui_coverage(coverage: GuiCoverage) -> list[str]:
    """Render the declared interface beside what the project models."""

    unmodeled = [entry for entry in coverage.layouts if not entry.modeled]
    lines = [
        f"layouts declared          {coverage.total_layouts:4d}",
        f"named widgets             {coverage.total_named:4d}",
        f"actionable widgets        {coverage.total_actionable:4d}",
        f"layouts with an interface {coverage.modeled_layouts:4d}",
        f"layouts without one       {len(unmodeled):4d}",
        "",
        "Actionable means a player can press, type, pick, or scroll it. A",
        "window's usefulness to an agent is bounded by those, not by its total",
        "widget count.",
        "",
        "DECLARED INTERFACE",
        f"  {'layout':<46} {'named':>5} {'act':>4}  window",
    ]
    for entry in sorted(
        coverage.layouts,
        key=lambda item: (-item.actionable_widgets, item.layout),
    ):
        marker = "  " if entry.modeled else ">>"
        caption = entry.window_caption or "-"
        lines.append(
            f"{marker}{entry.layout:<46} {entry.named_widgets:>5} "
            f"{entry.actionable_widgets:>4}  {caption[:28]}"
        )
    lines.extend(
        (
            "",
            ">> marks a declared window the project models no interface for.",
            "   Not every one needs an agent route - the level editor and fog",
            "   editor are not gameplay - but the list is the game's own, so a",
            "   window missing from it is missing on purpose rather than by",
            "   nobody having noticed.",
        )
    )
    return lines
