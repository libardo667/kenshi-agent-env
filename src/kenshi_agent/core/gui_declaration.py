"""Kenshi's own GUI declaration, parsed from its shipped MyGUI layouts.

The game ships every window it can draw as XML in `data/gui/layout`. Each file
names its widgets, gives their types, and carries their authored captions and
nesting. That is the game's enumeration of its own interface - the same kind of
denominator `controls.cfg` provides for bindings and `TaskType.h` for orders.

The project already captured the *names* into `gui_layout_widgets.tsv`, flat:
one row per widget, no parent, no caption. Flat is enough to ask "does this
widget exist"; it cannot answer "what sits beside it", which is the question
that actually comes up. Locating the prospecting reading needed exactly that
and, lacking it here, was answered by a heuristic that collected every caption
under a line and guessed - while the layout file three directories away named
the widget outright.

So this parses the structure: tree, type, skin, caption, per layout. What a
window contains, what each part is called, and what it is expected to say.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

GUI_LAYOUTS_SNAPSHOT = (
    Path(__file__).resolve().parents[3] / "game_sources" / "kenshi" / "gui_layouts.json"
)

# MyGUI prefixes a widget's name with its layout instance prefix at load time,
# so a runtime name is `<instance><declared>`. The declared tail is what joins
# back to these files.
LAYOUT_ROOT_NAME = "Root"


@dataclass(frozen=True, slots=True)
class DeclaredWidget:
    """One widget as its layout declares it."""

    name: str
    widget_type: str
    skin: str
    caption: str
    depth: int
    parent: str
    children: tuple[str, ...]

    @property
    def is_named(self) -> bool:
        return bool(self.name)

    @property
    def declares_caption(self) -> bool:
        return bool(self.caption)


@dataclass(frozen=True, slots=True)
class DeclaredLayout:
    """One shipped layout file and every widget it declares."""

    layout: str
    widgets: tuple[DeclaredWidget, ...]

    def named(self) -> tuple[DeclaredWidget, ...]:
        return tuple(widget for widget in self.widgets if widget.is_named)

    def by_name(self, name: str) -> DeclaredWidget | None:
        return next((widget for widget in self.widgets if widget.name == name), None)

    def children_of(self, name: str) -> tuple[DeclaredWidget, ...]:
        return tuple(widget for widget in self.widgets if widget.parent == name)

    def siblings_of(self, name: str) -> tuple[DeclaredWidget, ...]:
        widget = self.by_name(name)
        if widget is None:
            return ()
        return tuple(
            other
            for other in self.widgets
            if other.parent == widget.parent and other.name != name and other.is_named
        )


@dataclass(frozen=True, slots=True)
class GuiVocabulary:
    layouts: tuple[DeclaredLayout, ...]
    sha256: str = ""
    _index: dict[str, DeclaredLayout] = field(default_factory=dict, repr=False, compare=False)

    def layout(self, name: str) -> DeclaredLayout | None:
        return next((entry for entry in self.layouts if entry.layout == name), None)

    def widgets_named(self, name: str) -> tuple[tuple[str, DeclaredWidget], ...]:
        """Every layout declaring this widget name, since names are not unique."""

        found = []
        for entry in self.layouts:
            widget = entry.by_name(name)
            if widget is not None:
                found.append((entry.layout, widget))
        return tuple(found)

    @property
    def widget_count(self) -> int:
        return sum(len(entry.widgets) for entry in self.layouts)

    @property
    def named_widget_count(self) -> int:
        return sum(len(entry.named()) for entry in self.layouts)


def _parse_widget(
    element: ElementTree.Element,
    parent: str,
    depth: int,
    out: list[DeclaredWidget],
) -> str:
    name = element.get("name", "")
    caption = ""
    for prop in element.findall("Property"):
        if prop.get("key") == "Caption":
            caption = prop.get("value", "")
            break
    child_elements = element.findall("Widget")
    child_names = []
    for child in child_elements:
        child_name = _parse_widget(child, name, depth + 1, out)
        if child_name:
            child_names.append(child_name)
    out.append(
        DeclaredWidget(
            name=name,
            widget_type=element.get("type", ""),
            skin=element.get("skin", ""),
            caption=caption,
            depth=depth,
            parent=parent,
            children=tuple(child_names),
        )
    )
    return name


def parse_layout(path: Path) -> DeclaredLayout:
    """Parse one shipped layout into its declared widget tree."""

    root = ElementTree.parse(path).getroot()
    widgets: list[DeclaredWidget] = []
    for element in root.findall("Widget"):
        _parse_widget(element, "", 0, widgets)
    return DeclaredLayout(layout=path.name, widgets=tuple(widgets))


def parse_layout_directory(directory: Path) -> GuiVocabulary:
    """Parse every layout in a shipped `data/gui/layout` directory."""

    layouts = [
        parse_layout(path) for path in sorted(directory.glob("*.layout"))
    ]
    digest = sha256()
    for path in sorted(directory.glob("*.layout")):
        digest.update(path.read_bytes())
    return GuiVocabulary(layouts=tuple(layouts), sha256=digest.hexdigest())


def vocabulary_payload(vocabulary: GuiVocabulary) -> dict[str, object]:
    """Serialize the parsed declaration for capture into game_sources."""

    return {
        "schema_version": 1,
        "generated_by": "scripts/export_gui_layouts.py",
        "source": "Kenshi data/gui/layout/*.layout, captured verbatim",
        "source_sha256": vocabulary.sha256,
        "note": [
            "The game's own declaration of its interface: every window it can",
            "draw, what each part is named, what type it is, and what caption",
            "it was authored with.",
            "Base Kenshi only. Mods add windows declared in no layout file, and",
            "widgets built in code rather than loaded from a layout are also",
            "outside this. Absence here is not proof a widget cannot appear.",
        ],
        "layouts": [
            {
                "layout": entry.layout,
                "widgets": [
                    {
                        "name": widget.name,
                        "type": widget.widget_type,
                        "skin": widget.skin,
                        "caption": widget.caption,
                        "depth": widget.depth,
                        "parent": widget.parent,
                        "children": list(widget.children),
                    }
                    for widget in entry.widgets
                ],
            }
            for entry in vocabulary.layouts
        ],
    }


def load_gui_vocabulary(path: Path = GUI_LAYOUTS_SNAPSHOT) -> GuiVocabulary:
    """Load the captured declaration."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    layouts = []
    for entry in payload["layouts"]:
        widgets = tuple(
            DeclaredWidget(
                name=widget["name"],
                widget_type=widget["type"],
                skin=widget["skin"],
                caption=widget["caption"],
                depth=widget["depth"],
                parent=widget["parent"],
                children=tuple(widget["children"]),
            )
            for widget in entry["widgets"]
        )
        layouts.append(DeclaredLayout(layout=entry["layout"], widgets=widgets))
    return GuiVocabulary(
        layouts=tuple(layouts),
        sha256=str(payload.get("source_sha256", "")),
    )
