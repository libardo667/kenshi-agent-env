"""Read-only live view of the affordance menu the agent would be offered.

Affordance enumeration is a pure function of one `Observation`: it consults no
planner, acquires no input lease, and emits nothing to the game. That makes it
safe to run continuously against live telemetry while a person drives Kenshi by
hand, which is the cheapest way to learn what the controller can currently see.

The menu this produces is exactly what a planner call would receive at that
telemetry sequence. It is a window onto the existing enumeration, never a
second enumeration path.

The canonical enumeration now retains typed completeness and withholding
evidence. This display remains compact, while saved `affordance_set` events
carry the exact explanation needed for replay and diagnosis.

It also cross-checks each offered kind against its own definition. Slice 1 made
`AffordanceAdapter.offers` filter unauthorable offers, so this should always
come back empty. It is kept as a live assertion rather than deleted: an adapter
that enumerates through some other path would reintroduce exactly the
disagreement that put `harvest_resource` on the menu for a pair standing at an
iron deposit they could not harvest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..affordances import AFFORDANCE_ADAPTERS, AffordanceOffer, enumerate_affordance_set
from ..core.observation import Observation
from ..core.telemetry import TelemetrySnapshot
from ..operation_definitions import OPERATION_DEFINITIONS
from .coverage_frontier import reconcile_discovered_objects, render_discovered_pairs

WATCH_RUN_ID = "affordance-watch"


@dataclass(frozen=True, slots=True)
class OfferRow:
    """One currently offered affordance, flattened for display."""

    affordance_id: str
    source: str
    operation_kind: str
    semantic: str
    description: str
    target_id: str
    target_label: str
    target_kind: str

    @classmethod
    def from_offer(cls, offer: AffordanceOffer) -> OfferRow:
        target = offer.target
        return cls(
            affordance_id=offer.affordance_id,
            source=offer.source.value,
            operation_kind=offer.operation_kind,
            semantic=offer.semantic,
            description=offer.description,
            target_id=target.target_id if target else "",
            target_label=target.label if target else "",
            target_kind=target.kind if target else "",
        )


@dataclass(frozen=True, slots=True)
class AffordanceMenu:
    """Everything the planner would be offered at one telemetry sequence."""

    telemetry_sequence: int
    stale: bool
    loaded: bool
    location_id: str
    primary_character_id: str
    selected_character_ids: tuple[str, ...]
    roster_size: int
    offers: tuple[OfferRow, ...]
    adapters_offering: tuple[str, ...]
    adapters_silent: tuple[str, ...]
    # Operation kinds this menu offers that their own definition would refuse
    # right now. Every entry is a choice the planner can pick and cannot run.
    unauthorable_offer_kinds: tuple[str, ...]

    def fingerprint(self) -> str:
        """Identity of this menu, ignoring the sequence that produced it.

        Change detection for a watch loop. Telemetry advances constantly while
        the menu does not, so re-rendering per read would bury the moments that
        matter: an affordance appearing or disappearing as the world changes.
        """

        payload = json.dumps(
            {
                "primary": self.primary_character_id,
                "selected": list(self.selected_character_ids),
                "location": self.location_id,
                "stale": self.stale,
                "loaded": self.loaded,
                "offers": sorted(
                    f"{row.source}:{row.operation_kind}:{row.semantic}:{row.target_id}"
                    for row in self.offers
                ),
                "unauthorable": list(self.unauthorable_offer_kinds),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def by_source(self) -> dict[str, tuple[OfferRow, ...]]:
        grouped: dict[str, list[OfferRow]] = {}
        for row in self.offers:
            grouped.setdefault(row.source, []).append(row)
        return {source: tuple(rows) for source, rows in sorted(grouped.items())}


def observation_from_snapshot(
    snapshot: TelemetrySnapshot,
    *,
    stale: bool = False,
    step_index: int = 0,
    run_id: str = WATCH_RUN_ID,
) -> Observation:
    """Build the minimum observation affordance enumeration actually reads.

    Enumerators consult telemetry, staleness, and capabilities. The planner and
    continuity fields an ordinary run would carry do not participate, so a
    synthesized observation produces the same menu a real one would.
    """

    return Observation(
        run_id=run_id,
        step_index=step_index,
        mode="live",
        telemetry=snapshot,
        telemetry_stale=stale,
    )


def current_menu(observation: Observation) -> AffordanceMenu:
    """Enumerate every adapter exactly as a planner call would."""

    enumeration = enumerate_affordance_set(observation)
    rows = [OfferRow.from_offer(offer) for offer in enumeration.offers]
    offering = sorted({offer.source_adapter for offer in enumeration.evidence_offers})
    silent = sorted({adapter.name for adapter in AFFORDANCE_ADAPTERS} - set(offering))

    unauthorable = sorted(
        {
            row.operation_kind
            for row in rows
            if (definition := OPERATION_DEFINITIONS.get(row.operation_kind)) is not None
            and not definition.is_currently_authorable(observation)
        }
    )

    telemetry = observation.telemetry
    return AffordanceMenu(
        telemetry_sequence=telemetry.sequence if telemetry is not None else 0,
        stale=observation.telemetry_stale,
        loaded=bool(telemetry is not None and telemetry.game.loaded),
        location_id=(
            telemetry.game.location_id or "" if telemetry is not None else ""
        ),
        primary_character_id=(
            telemetry.primary_character_id or "" if telemetry is not None else ""
        ),
        selected_character_ids=(
            tuple(telemetry.selected_character_ids) if telemetry is not None else ()
        ),
        roster_size=len(telemetry.roster) if telemetry is not None else 0,
        offers=tuple(rows),
        adapters_offering=tuple(offering),
        adapters_silent=tuple(silent),
        unauthorable_offer_kinds=tuple(unauthorable),
    )


def menu_payload(menu: AffordanceMenu) -> dict[str, object]:
    """Machine-readable menu for capture and later analysis."""

    return {
        "telemetry_sequence": menu.telemetry_sequence,
        "fingerprint": menu.fingerprint(),
        "stale": menu.stale,
        "loaded": menu.loaded,
        "location_id": menu.location_id,
        "primary_character_id": menu.primary_character_id,
        "selected_character_ids": list(menu.selected_character_ids),
        "roster_size": menu.roster_size,
        "offer_count": len(menu.offers),
        "adapters_offering": list(menu.adapters_offering),
        "adapters_silent": list(menu.adapters_silent),
        "unauthorable_offer_kinds": list(menu.unauthorable_offer_kinds),
        "offers": [
            {
                "affordance_id": row.affordance_id,
                "source": row.source,
                "operation_kind": row.operation_kind,
                "semantic": row.semantic,
                "description": row.description,
                "target_id": row.target_id,
                "target_label": row.target_label,
                "target_kind": row.target_kind,
            }
            for row in menu.offers
        ],
    }


def render_menu(menu: AffordanceMenu) -> list[str]:
    """Render one menu for a person watching the world change."""

    selection = (
        ", ".join(menu.selected_character_ids) if menu.selected_character_ids else "none"
    )
    header = [
        f"telemetry sequence {menu.telemetry_sequence}"
        f"{'  STALE' if menu.stale else ''}"
        f"{'' if menu.loaded else '  WORLD NOT LOADED'}",
        f"location {menu.location_id or 'unknown'}   roster {menu.roster_size}",
        f"primary  {menu.primary_character_id or 'none'}",
        f"selected {selection}",
        "",
    ]
    if not menu.offers:
        header.extend(
            (
                "NO AFFORDANCES OFFERED",
                "",
                "  An empty menu does not distinguish a gate withholding a modeled",
                "  affordance from a surface that was never modeled. Both look like",
                "  this. Typed withholding reasons are the separate change.",
                "",
            )
        )
    else:
        header.append(f"{len(menu.offers)} OFFERED")
        for source, rows in menu.by_source().items():
            header.append(f"  {source} ({len(rows)})")
            for row in rows:
                target = f"  -> {row.target_label}" if row.target_label else ""
                header.append(f"    {row.semantic:<28} {row.operation_kind:<28}{target}")
        header.append("")
    if menu.unauthorable_offer_kinds:
        header.extend(
            (
                "OFFERED BUT NOT AUTHORABLE",
                "",
                "  These are on the menu and their own definition would refuse them",
                "  right now. Enumeration never consults authorability, so the",
                "  planner can pick one and get a refusal it was given no way to",
                "  anticipate.",
                "",
            )
        )
        for kind in menu.unauthorable_offer_kinds:
            definition = OPERATION_DEFINITIONS.get(kind)
            scope = (
                definition.recipient_scope_for().value if definition is not None else "?"
            )
            header.append(f"    {kind:<30} recipient_scope={scope}")
        header.append("")
    if menu.adapters_silent:
        header.append(f"silent adapters: {', '.join(menu.adapters_silent)}")
    return header


def render_discovery(observation: Observation) -> list[str]:
    """What Kenshi says is available nearby, beside what the controller routes.

    The menu above is what the agent can choose. This is what the world
    affords. The difference is the part of Kenshi the agent cannot reach yet.
    """

    pairs = reconcile_discovered_objects(observation.telemetry)
    return ["", "WORLD DISCOVERY (asked of Kenshi, not declared here)", ""] + [
        f"  {line}" for line in render_discovered_pairs(pairs)
    ]
