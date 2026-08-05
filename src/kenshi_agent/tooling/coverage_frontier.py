"""Why the world-target affordance surface is the size it is.

The project already derives denominators from game source: `controls.cfg` bounds
the binding surface, `TaskType.h` bounds the order vocabulary. Context actions
were supposed to work the same way, reconciling witnessed runtime menus against
wired semantic routes.

They do not, and running the agent longer will not fix it. The plug-in does not
ask Kenshi what a target affords. It emits two hardcoded answers:

    natural_resource -> ["operate"]        WorldTargetProtocol.cpp
    squad_character  -> ["first_aid"]      KenshiAgentTelemetry.cpp

Those literals are the entire world-target surface. Three wired pairs out of a
291-entry vocabulary is not evidence of a young witness set; it is the ceiling
of what the current export can ever produce. An agent can walk past a hundred
distinguishable objects and learn nothing, because nothing asks.

The mechanism to lift the ceiling already exists in the plug-in and is already
called: `PlayerInterface::getPlayerTaskProbability(TaskType, target, out)`.
Kenshi itself answers whether the player can issue one exact task to one exact
target. It is currently invoked with a single hardcoded `FIRST_AID_ORDER`.
Iterating a bounded task vocabulary against each nearby target turns discovery
from something a human notices into something the export states.

This module derives the ceiling rather than asserting it, so it stops being
true the moment the plug-in stops hardcoding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .context_action_parity import CONTEXT_ACTION_DECISIONS, WITNESSES_PATH, load_witnesses
from .context_action_vocabulary import load_task_types

NATIVE_ROOT = Path(__file__).resolve().parents[3] / "native" / "KenshiAgentTelemetry"

# Every source file that may emit a world target's advertised context actions.
NATIVE_TARGET_SOURCES = (
    NATIVE_ROOT / "KenshiAgentTelemetry.cpp",
    NATIVE_ROOT / "WorldTargetProtocol.cpp",
)

# `"kind":"<kind>"` ... `"context_actions":["a","b"]` emitted as adjacent
# literals in one serialized object. Both current emitters write the kind
# before the actions, close together, with no intervening object.
_HARDCODED_SURFACE = re.compile(
    r'"kind\\":\\"(?P<kind>[a-z_]+)\\",(?P<between>(?:[^{}]|\{[^{}]*\})*?)'
    r'"context_actions\\":\[(?P<actions>[^\]]*)\]',
    re.DOTALL,
)
_ACTION_LITERAL = re.compile(r'\\"([a-z_]+)\\"')

# The API that can replace the hardcoding, and where it is already used.
TASK_PROBABILITY_API = "getPlayerTaskProbability"


@dataclass(frozen=True, slots=True)
class HardcodedSurface:
    """One target kind and the context actions the plug-in always claims for it."""

    target_kind: str
    context_actions: tuple[str, ...]
    source: str


@dataclass(frozen=True, slots=True)
class CoverageFrontier:
    vocabulary_size: int
    hardcoded_surfaces: tuple[HardcodedSurface, ...]
    witnessed_pairs: tuple[tuple[str, int], ...]
    wired_pairs: tuple[tuple[str, int], ...]
    witnessed_target_kinds: tuple[str, ...]
    task_probability_call_sites: int

    @property
    def emittable_target_kinds(self) -> tuple[str, ...]:
        return tuple(sorted({surface.target_kind for surface in self.hardcoded_surfaces}))

    @property
    def emittable_semantics(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    action
                    for surface in self.hardcoded_surfaces
                    for action in surface.context_actions
                }
            )
        )

    @property
    def surface_is_hardcoded(self) -> bool:
        """Whether the world-target surface is fixed literals rather than a query."""

        return bool(self.hardcoded_surfaces)

    @property
    def unreachable_vocabulary(self) -> int:
        """Task types no amount of agent runtime can currently surface."""

        return self.vocabulary_size - len(self.emittable_semantics)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _hardcoded_surfaces() -> tuple[HardcodedSurface, ...]:
    """Parse the context actions the plug-in writes as fixed literals."""

    found: list[HardcodedSurface] = []
    for path in NATIVE_TARGET_SOURCES:
        text = _read(path)
        for match in _HARDCODED_SURFACE.finditer(text):
            actions = tuple(_ACTION_LITERAL.findall(match.group("actions")))
            if not actions:
                continue
            found.append(
                HardcodedSurface(
                    target_kind=match.group("kind"),
                    context_actions=actions,
                    source=path.name,
                )
            )
    return tuple(sorted(found, key=lambda item: (item.target_kind, item.source)))


def _task_probability_call_sites() -> int:
    return sum(_read(path).count(TASK_PROBABILITY_API) for path in NATIVE_TARGET_SOURCES)


def assess_coverage_frontier() -> CoverageFrontier:
    """Derive the current world-target ceiling from game source and plug-in source."""

    witnesses = load_witnesses(WITNESSES_PATH)
    witnessed_pairs = sorted(
        {
            (witness.target_kind, value)
            for witness in witnesses
            for value in witness.task_type_values
        }
    )
    return CoverageFrontier(
        vocabulary_size=len(load_task_types().entries),
        hardcoded_surfaces=_hardcoded_surfaces(),
        witnessed_pairs=tuple(witnessed_pairs),
        wired_pairs=tuple(sorted(CONTEXT_ACTION_DECISIONS)),
        witnessed_target_kinds=tuple(sorted({witness.target_kind for witness in witnesses})),
        task_probability_call_sites=_task_probability_call_sites(),
    )


@dataclass(frozen=True, slots=True)
class DiscoveredPair:
    """One (category, task) pair Kenshi advertised, and whether it is routed."""

    category: str
    task_name: str
    task_value: int
    example_object: str
    routed: bool


def reconcile_discovered_objects(
    snapshot: object,
    *,
    wired_task_values: frozenset[int] | None = None,
) -> tuple[DiscoveredPair, ...]:
    """Turn one snapshot's discovery data into a routed/unrouted queue.

    The frontier report says how big the ceiling is. This says what is actually
    behind it: pairs Kenshi advertised, marked against the semantic routes the
    controller has. Unrouted pairs are the implementation queue, derived from
    the game rather than from anyone noticing an absence.

    Routing is judged by task value only. A pair being unrouted means the
    controller has no operation for that task at all - it is deliberately not a
    claim about whether the existing route would work on that category.
    """

    if wired_task_values is None:
        wired_task_values = frozenset(
            task_value for _, task_value in CONTEXT_ACTION_DECISIONS
        )
    objects = getattr(snapshot, "discovered_objects", None) or []
    seen: dict[tuple[str, str], DiscoveredPair] = {}
    for entry in objects:
        category = getattr(entry, "category", "")
        for task in getattr(entry, "advertised_tasks", None) or []:
            key = (category, task.name)
            if key in seen:
                continue
            seen[key] = DiscoveredPair(
                category=category,
                task_name=task.name,
                task_value=task.value,
                example_object=getattr(entry, "name", "") or getattr(entry, "id", ""),
                routed=task.value in wired_task_values,
            )
    return tuple(sorted(seen.values(), key=lambda pair: (pair.category, pair.task_name)))


def render_discovered_pairs(pairs: tuple[DiscoveredPair, ...]) -> list[str]:
    """Render the discovery queue: what exists, and what has nowhere to go."""

    if not pairs:
        return [
            "no advertised tasks in this snapshot",
            "",
            "  Either nothing nearby affords anything to the current selection,",
            "  or the probe reached nothing. Absence here is not evidence that",
            "  the world is empty of affordances.",
        ]
    unrouted = [pair for pair in pairs if not pair.routed]
    lines = [
        f"advertised pairs   {len(pairs):3d}",
        f"routed             {len(pairs) - len(unrouted):3d}",
        f"unrouted           {len(unrouted):3d}   <- implementation queue",
        "",
        f"  {'category':<18} {'task':<26} {'value':>5}  example",
    ]
    for pair in pairs:
        marker = "   " if pair.routed else ">> "
        lines.append(
            f"{marker}{pair.category:<18} {pair.task_name:<26} "
            f"{pair.task_value:>5}  {pair.example_object[:28]}"
        )
    if unrouted:
        lines.extend(
            (
                "",
                ">> marks a task Kenshi advertises that the controller cannot",
                "   express. Each is a lever the agent can see and not pull.",
            )
        )
    return lines


def render_coverage_frontier(frontier: CoverageFrontier) -> list[str]:
    """Render the ceiling and the mechanism that would lift it."""

    lines = [
        f"task vocabulary (TaskType.h)      {frontier.vocabulary_size:4d}",
        f"semantics the plug-in can emit    {len(frontier.emittable_semantics):4d}",
        f"target kinds the plug-in can emit {len(frontier.emittable_target_kinds):4d}",
        f"witnessed (kind, task) pairs      {len(frontier.witnessed_pairs):4d}",
        f"wired (kind, task) pairs          {len(frontier.wired_pairs):4d}",
        f"vocabulary currently unreachable  {frontier.unreachable_vocabulary:4d}",
        "",
        "HARDCODED WORLD-TARGET SURFACE",
    ]
    if not frontier.hardcoded_surfaces:
        lines.append("  none - the plug-in queries the game for advertised actions")
    else:
        for surface in frontier.hardcoded_surfaces:
            actions = ", ".join(surface.context_actions)
            lines.append(f"  {surface.target_kind:<20} -> [{actions}]   {surface.source}")
        lines.extend(
            (
                "",
                "  These literals are the whole surface. A longer run cannot find a",
                "  third semantic or a third target kind, because the export never",
                "  asks Kenshi what a target affords - it states what it already",
                "  decided. Coverage is bounded by the plug-in, not by exploration.",
            )
        )
    lines.extend(
        (
            "",
            "MECHANISM AVAILABLE",
            f"  {TASK_PROBABILITY_API} call sites: {frontier.task_probability_call_sites}",
            "  Kenshi answers, per exact task and exact target, whether the player",
            "  may issue it. Iterating a bounded task vocabulary against each",
            "  nearby target replaces the literals above with the game's own",
            "  answer, and makes the frontier fill in as the agent moves.",
            "",
            "WITNESSED TARGET KINDS",
        )
    )
    for kind in frontier.witnessed_target_kinds:
        lines.append(f"  {kind}")
    return lines
