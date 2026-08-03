"""Every surface a player acts through, including the ones nobody has looked at.

Binding parity was captured, then widget parity, and each was sound on its own
while the word "parity" quietly came to mean the thin interface shell those two
cover. The largest surface — Kenshi's content database, 11.3 MB of dialogue and
4.4 MB of game data against 72 bindings and 563 widgets — went unmentioned for a
whole session because no artefact had a row for it. An absent surface reads
exactly like a covered one.

So the surfaces themselves are enumerated here, and a surface nobody has
examined is a loud row rather than a silence.

This list is the one hand-authored inventory in the affordance work, and that is
unavoidable: no artefact Kenshi ships enumerates its own surfaces. The defence
is not automation but visibility — the list is short, every row states its own
status, and the untouched rows are printed in the generated report next to the
captured ones. Adding a surface should be rare and obvious; forgetting one
should be hard to do quietly.

`DENOMINATOR_UNKNOWN` and `UNEXAMINED` are deliberately distinct from a coverage
of zero. "We have not looked" and "there is nothing there" must never render the
same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .context_action_vocabulary import TASK_TYPES_SNAPSHOT, load_task_types


class SurfaceStatus(StrEnum):
    """How far a surface has got, from nobody-looked to measured."""

    # Nobody has examined it. Its size may be known; its contents are not.
    UNEXAMINED = "unexamined"
    # We know which artefact the game declares it in, but nothing is captured.
    SOURCE_IDENTIFIED = "source_identified"
    # A game-derived denominator lives in the repository and is verified against
    # the install.
    CAPTURED = "captured"
    # Continuous choices - a bearing, a quantity, a camera angle. Counting these
    # would be a category error: they are decisions, not affordances.
    NOT_ENUMERABLE = "not_enumerable"


@dataclass(frozen=True, slots=True)
class AffordanceSurface:
    key: str
    what_it_enumerates: str
    status: SurfaceStatus
    # Where Kenshi itself declares this surface, or why nothing does.
    source: str
    # Size of the denominator when captured; None while unknown. A count here
    # with a non-captured status would be a guess wearing a measurement's
    # clothes.
    enumerated: int | None = None
    note: str = ""
    candidate_vocabulary: CandidateVocabulary | None = None


@dataclass(frozen=True, slots=True)
class CandidateVocabulary:
    """A captured source vocabulary that is broader than the denominator."""

    path: Path
    display_path: str
    enumerated: int
    limitation: str


SURFACES: tuple[AffordanceSurface, ...] = (
    AffordanceSurface(
        key="game_binding",
        what_it_enumerates="Named controls a player can press.",
        status=SurfaceStatus.CAPTURED,
        source="data/../controls.cfg, captured to game_sources/kenshi/controls.cfg",
        enumerated=72,
        note=(
            "Enumerated is not usable: 71 are expressible by an action and 10 "
            "have an observable completion terminal. See GAME_BINDING_PARITY."
        ),
    ),
    AffordanceSurface(
        key="ui_widget",
        what_it_enumerates="Named widgets in every window the game ships.",
        status=SurfaceStatus.CAPTURED,
        source=(
            "data/gui/layout/*.layout, captured to "
            "game_sources/kenshi/gui_layout_widgets.tsv"
        ),
        enumerated=563,
        note=(
            "Denominator captured; reachability is unmeasured. Only four of 52 "
            "windows have been observed live. Excludes mod UI and widgets built "
            "in code rather than loaded from a layout."
        ),
    ),
    AffordanceSurface(
        key="world_context_action",
        what_it_enumerates="Orders a player can give a world object by right-click.",
        status=SurfaceStatus.SOURCE_IDENTIFIED,
        source=(
            "KenshiLib `enum TaskType`, filtered at runtime by "
            "`PlayerInterface::isOrderValidForSelection`, with the exact per-target "
            "list in `ContextMenu::orders`."
        ),
        note=(
            "Concrete runtime menu captures now enter an empirical parity report "
            "automatically. The current world-target export carries reviewed exact "
            "natural-resource and squad-character context targets."
        ),
        candidate_vocabulary=CandidateVocabulary(
            path=TASK_TYPES_SNAPSHOT,
            display_path="game_sources/kenshi/TaskType.h",
            enumerated=len(load_task_types().entries),
            limitation=(
                "Upper bound only: most TaskType values are internal AI tasks. "
                "The report contains witnessed per-target ContextMenu::orders, "
                "not a global denominator of every target and selection."
            ),
        ),
    ),
    AffordanceSurface(
        key="game_content",
        what_it_enumerates=(
            "The things acted upon: items, buildings, research, factions, "
            "dialogue, towns."
        ),
        status=SurfaceStatus.UNEXAMINED,
        source=(
            "data/gamedata.base (4.4 MB), data/Dialogue.mod (11.3 MB), "
            "data/Newwworld.mod (1.9 MB); reachable through FCS_extended, or as "
            "live game objects through KenshiLib."
        ),
        note=(
            "Larger than every captured surface combined and unexamined. "
            "`purchase_item` is a finished action with no enumeration of what is "
            "purchasable; `harvest_resource` works on Iron because a person "
            "reviewed Iron. Needed as a vocabulary for reasoning about what is "
            "encountered, NOT as a payload to preload into an observation."
        ),
    ),
    AffordanceSurface(
        key="continuous_control",
        what_it_enumerates="Where to walk, how much to buy, which bearing, camera angle.",
        status=SurfaceStatus.NOT_ENUMERABLE,
        source="No artefact enumerates these; they are chosen, not offered.",
        note="Listed so its absence from coverage is a decision rather than an oversight.",
    ),
)


def surfaces_by_status(status: SurfaceStatus) -> tuple[AffordanceSurface, ...]:
    return tuple(surface for surface in SURFACES if surface.status is status)


def render_surface_registry() -> list[str]:
    """The whole map, with unexamined rows as prominent as measured ones."""

    lines = [
        f"surfaces           {len(SURFACES):2d}",
        f"  captured         {len(surfaces_by_status(SurfaceStatus.CAPTURED)):2d}",
        f"  source known     {len(surfaces_by_status(SurfaceStatus.SOURCE_IDENTIFIED)):2d}",
        f"  UNEXAMINED       {len(surfaces_by_status(SurfaceStatus.UNEXAMINED)):2d}",
        f"  not enumerable   {len(surfaces_by_status(SurfaceStatus.NOT_ENUMERABLE)):2d}",
        "",
    ]
    for surface in SURFACES:
        size = f"{surface.enumerated}" if surface.enumerated is not None else "—"
        lines.append(f"{surface.key}  [{surface.status.value}]  enumerated: {size}")
        lines.append(f"    {surface.what_it_enumerates}")
        lines.append(f"    source: {surface.source}")
        if surface.note:
            lines.append(f"    note: {surface.note}")
        if surface.candidate_vocabulary is not None:
            vocabulary = surface.candidate_vocabulary
            lines.append(
                "    candidate vocabulary: "
                f"{vocabulary.enumerated} from {vocabulary.display_path}"
            )
            lines.append(f"    limitation: {vocabulary.limitation}")
        lines.append("")
    return lines
