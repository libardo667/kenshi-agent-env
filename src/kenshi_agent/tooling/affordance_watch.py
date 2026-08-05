"""Read-only live view of the affordance menu the agent would be offered.

Affordance enumeration is a pure function of one `Observation`: it consults no
planner, acquires no input lease, and emits nothing to the game. That makes it
safe to run continuously against live telemetry while a person drives Kenshi by
hand, which is the cheapest way to learn what the controller can currently see.

The menu this produces is exactly what a planner call would receive at that
telemetry sequence. It is a window onto the existing enumeration, never a
second enumeration path.

What it cannot do: distinguish "a gate withheld a modeled affordance" from
"this surface was never modeled". Both read as absence. Enumerators withhold by
returning or continuing, so the reason evaporates at the branch. Recording
typed withholding reasons is the separate, larger change that section 9.6 of
the interaction-scope plan requires; until it lands, an empty menu is a
question rather than an answer.

What it can do cheaply is catch the opposite and more damaging failure: an
affordance that is *offered but not authorable*. Enumeration and
`OperationDefinition.is_currently_authorable` are separate authorities and they
can disagree, because enumeration never consults it. The planner is then shown
a choice that cannot bind, and nothing explains the refusal. Cross-checking the
two costs one predicate call per offered kind and needs no enumerator change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..affordances import AFFORDANCE_ADAPTERS, AffordanceOffer
from ..core.observation import Observation
from ..core.telemetry import TelemetrySnapshot
from ..operation_definitions import OPERATION_DEFINITIONS

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

    offering: list[str] = []
    silent: list[str] = []
    rows: list[OfferRow] = []
    for adapter in AFFORDANCE_ADAPTERS:
        produced = [OfferRow.from_offer(offer) for offer in adapter.enumerate(observation)]
        if produced:
            offering.append(adapter.name)
            rows.extend(produced)
        else:
            silent.append(adapter.name)

    unauthorable = sorted(
        {
            row.operation_kind
            for row in rows
            if (definition := OPERATION_DEFINITIONS.get(row.operation_kind)) is not None
            and not definition.is_currently_authorable(observation)
        }
    )

    telemetry = observation.telemetry
    ui = telemetry.ui if telemetry is not None else None
    return AffordanceMenu(
        telemetry_sequence=telemetry.sequence if telemetry is not None else 0,
        stale=observation.telemetry_stale,
        loaded=bool(telemetry is not None and telemetry.game.loaded),
        location_id=(
            telemetry.game.location_id or "" if telemetry is not None else ""
        ),
        primary_character_id=(
            ui.selected_character_id or "" if ui is not None else ""
        ),
        selected_character_ids=(
            tuple(ui.selected_character_ids) if ui is not None else ()
        ),
        roster_size=len(telemetry.squad) if telemetry is not None else 0,
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
            requirement = (
                definition.selection_requirement.value if definition is not None else "?"
            )
            header.append(f"    {kind:<30} selection_requirement={requirement}")
        header.append("")
    if menu.adapters_silent:
        header.append(f"silent adapters: {', '.join(menu.adapters_silent)}")
    return header
