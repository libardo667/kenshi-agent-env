"""Join what the agent sees on screen to what Kenshi declares it should be.

A visible control arrives as a caption, a MyGUI name, and a type. That is what
is *rendered*. Kenshi separately ships what every window *is* - each widget's
declared name, type, and authored caption - and until now the two were never
joined, so the controller answered structural questions by matching caption
strings and walking widget trees hoping to recognise something.

Resolution turns a rendered control into a declared identity: which layout it
came from, what the game calls it, and what type it is. Three things follow
that were not previously expressible:

*Addressability.* A control can be named by what it is rather than by what it
currently reads. Captions are localised, duplicated, and often rendered
content - 56 of 91 observed labels were values like '16 mph' rather than any
control name.

*Expectation.* A window's declaration lists what it contains, so the agent can
tell "this window has no sell button" from "the sell button is not currently
visible to me". Absence stops being uniform.

What the declaration does *not* give is expected text. Authored captions are
placeholders - measured live, 'Day: 12345', 'c. 100.000', 'Unobtainium', 'This
is a tip', and a button rendering TEC declared as TCH. They identify a widget's
role and are never evidence about what the UI should currently say.

*Provenance.* A widget in no layout is a widget built in code or added by a
mod. That is worth knowing rather than silently treating as ordinary.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .gui_declaration import DeclaredWidget, GuiVocabulary, load_gui_vocabulary


@lru_cache(maxsize=1)
def _vocabulary() -> GuiVocabulary:
    """The captured declaration, read once.

    Loaded lazily so an environment without the capture - a mock run, a machine
    with no Kenshi - degrades to unresolved controls rather than failing.
    """

    try:
        return load_gui_vocabulary()
    except (OSError, ValueError):
        return GuiVocabulary(layouts=())


@dataclass(frozen=True, slots=True)
class ResolvedControl:
    """One rendered control matched against Kenshi's declaration."""

    layout_widget_name: str
    layouts: tuple[str, ...]
    # Reported only when exactly one layout declares this name. `Root` is
    # declared in nearly every layout, so picking the first match reported the
    # Prospecting window's caption as 'Fog Volumes' - a guess wearing the
    # clothes of a fact.
    widget_type: str
    # The caption the layout was *authored* with, which is a placeholder, not
    # an expectation. Measured live: 'Day: 12345', 'c. 100.000', 'Unobtainium',
    # 'This is a tip', and a TEC button declared 'TCH'. It identifies a
    # widget's role and must never be compared against rendered text to judge
    # whether the UI is correct.
    declared_caption: str

    @property
    def resolved(self) -> bool:
        return bool(self.layouts)

    @property
    def ambiguous(self) -> bool:
        """Declared in more than one layout, so the layout is not implied."""

        return len(self.layouts) > 1

    @property
    def provenance(self) -> str:
        if not self.layouts:
            # Built in code, added by a mod, or anonymous rendered text. Not a
            # defect; a fact about where the widget came from.
            return "undeclared"
        return "ambiguous" if self.ambiguous else "declared"


def resolve_control(layout_widget_name: str) -> ResolvedControl:
    """Match one layout-relative widget name against the declaration."""

    if not layout_widget_name:
        return ResolvedControl("", (), "", "")
    found = _vocabulary().widgets_named(layout_widget_name)
    if not found:
        return ResolvedControl(layout_widget_name, (), "", "")
    layouts = tuple(layout for layout, _ in found)
    if len(found) > 1:
        # Several layouts declare this name and nothing here says which one is
        # on screen. Report the ambiguity and withhold the details rather than
        # taking the first and presenting it as this widget's.
        types = {widget.widget_type for _, widget in found}
        return ResolvedControl(
            layout_widget_name=layout_widget_name,
            layouts=layouts,
            # A type shared by every candidate is still known.
            widget_type=types.pop() if len(types) == 1 else "",
            declared_caption="",
        )
    widget = found[0][1]
    return ResolvedControl(
        layout_widget_name=layout_widget_name,
        layouts=layouts,
        widget_type=widget.widget_type,
        declared_caption=widget.caption,
    )


@dataclass(frozen=True, slots=True)
class WindowExpectation:
    """What a layout says a window contains, beside what is currently seen."""

    layout: str
    declared: tuple[str, ...]
    observed: tuple[str, ...]

    @property
    def missing(self) -> tuple[str, ...]:
        """Declared and not currently visible.

        Not an error. A tab that is not selected, a button hidden by state, or
        a control the export did not reach all land here. It is the difference
        between "absent" and "unknown", which the agent could not previously
        distinguish.
        """

        return tuple(name for name in self.declared if name not in set(self.observed))

    @property
    def coverage(self) -> float:
        if not self.declared:
            return 0.0
        seen = len(set(self.declared) & set(self.observed))
        return seen / len(self.declared)


def expectation_for(layout: str, observed_names: tuple[str, ...]) -> WindowExpectation:
    """Compare one layout's declaration against the controls actually seen."""

    entry = _vocabulary().layout(layout)
    declared = tuple(widget.name for widget in entry.named()) if entry else ()
    return WindowExpectation(
        layout=layout,
        declared=declared,
        observed=observed_names,
    )


def declared_siblings(layout: str, widget_name: str) -> tuple[DeclaredWidget, ...]:
    """What the declaration says sits beside a widget.

    The question that motivated all of this: the prospecting reading lives in a
    `ValueText` beside the resource-name button, which the layout states and no
    amount of caption inspection reveals.
    """

    entry = _vocabulary().layout(layout)
    return entry.siblings_of(widget_name) if entry else ()
