"""Observation domain types."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
)

from .advisor import AdvisorAvailability
from .base import StrictModel
from .continuity import (
    ActiveFieldbookProject,
    ContinuityReceiptDigest,
    FieldbookProjectIndex,
    FieldbookReadReceipt,
    FieldbookReceiptDigest,
    MemoryReadReceipt,
    MemoryRecord,
    RecallSummary,
)
from .evidence import (
    ActionOutcome,
    PlanOutcome,
    StateChange,
)
from .operation import (
    ControlMode,
)
from .planning import ActivePlanContext
from .telemetry import (
    MAX_DIGESTED_VISIBLE_CONTROLS,
    CharacterState,
    NearbyEntity,
    TelemetrySnapshot,
    budgeted_visible_controls,
    dialogue_targets,
    is_runtime_owned_visible_control,
    map_destination_travel_available,
    normalize_control_label,
)
from .world import WorldStateRevision


def _json_model(value: BaseModel) -> dict[str, Any]:
    """Project a model through its canonical JSON representation."""

    result: dict[str, Any] = json.loads(value.model_dump_json())
    return result


class Observation(StrictModel):
    run_id: str
    step_index: int = Field(ge=0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mode: Literal["mock", "live", "replay"]
    control_mode: ControlMode = ControlMode.INTERFACE_ONLY
    world_revision: WorldStateRevision = Field(default_factory=WorldStateRevision)
    telemetry: TelemetrySnapshot | None = None
    telemetry_stale: bool = False
    telemetry_age_seconds: float | None = None
    screenshot_path: Path | None = None
    screenshot_sha256: str | None = None
    events: list[str] = Field(default_factory=list)
    objective: str | None = Field(default=None, max_length=1000)
    active_plan: ActivePlanContext | None = None
    recent_action_outcomes: list[ActionOutcome] = Field(default_factory=list, max_length=100)
    # Why previous plans ended, in terms of what they set out to do. Working
    # history, not durable memory: it is runtime-owned and dies with the run.
    recent_plan_outcomes: list[PlanOutcome] = Field(default_factory=list, max_length=8)
    # Why the last continuity operations were accepted or refused. Without this
    # a planner that makes one deterministic mistake remakes it every plan.
    recent_continuity_receipts: list[ContinuityReceiptDigest] = Field(
        default_factory=list,
        max_length=8,
    )
    # A diagnostic store write can fail without being a planner-authored
    # operation, so it has no operation receipt. Keep the quarantined state
    # explicit until the run ends instead of making absence look healthy.
    continuity_writes_degraded_reason: str | None = Field(
        default=None,
        max_length=1000,
    )
    continuity_reads_degraded_reason: str | None = Field(
        default=None,
        max_length=1000,
    )
    # Why the previous planner response could not be used. Without it a planner
    # that makes one deterministic mistake remakes it on every retry: a live run
    # ended after 21 identical validation failures, each replanned from an
    # observation that said nothing about the previous twenty.
    planner_feedback: str | None = Field(default=None, max_length=1200)
    # What actually moved since the previous observation, as path/before/after.
    # The agent's hardest question is whether the thing it just did had any
    # effect, and a full snapshot answers it only by comparison against a
    # snapshot it no longer has. Both stalled live runs failed here: steps that
    # "completed" while changing nothing, replanned from observations that
    # looked identical to the ones before them.
    recent_changes: list[StateChange] = Field(default_factory=list, max_length=40)
    memories: list[MemoryRecord] = Field(default_factory=list)
    # What automatic recall left out, so "nothing else" and "more, not shown"
    # are distinguishable.
    memory_recall: RecallSummary = Field(default_factory=RecallSummary)
    # The typed result of an elective `recall_memory`, carried to exactly the
    # next planner call that asked for it.
    memory_search: MemoryReadReceipt | None = None
    # Automatic context carries only a bounded project index and the selected
    # active summary. Full entries arrive through one bounded elective read.
    fieldbook_projects: list[FieldbookProjectIndex] = Field(
        default_factory=list,
        max_length=8,
    )
    active_fieldbook_project: ActiveFieldbookProject | None = None
    recent_fieldbook_receipts: list[FieldbookReceiptDigest] = Field(
        default_factory=list,
        max_length=8,
    )
    fieldbook_read: FieldbookReadReceipt | None = None
    advisor: AdvisorAvailability = Field(default_factory=AdvisorAvailability)

    def current_memory_target_ids(self) -> set[str]:
        """Return exact identities safe to use for entity-scoped recall.

        A stale snapshot is not evidence that an entity is still current. The
        lookup deliberately ignores display names and historical native target
        fields: only an entity in this snapshot, or the dialogue currently
        open in this snapshot, can reactivate a bound memory.
        """

        if self.telemetry is None or self.telemetry_stale:
            return set()
        telemetry = self.telemetry
        target_ids = {
            item.id
            for collection in (
                telemetry.squad,
                telemetry.nearby_entities,
                telemetry.world_targets,
                telemetry.known_map_destinations,
            )
            for item in collection
            if item.id
        }
        if telemetry.ui.dialogue_target_id:
            target_ids.add(telemetry.ui.dialogue_target_id)
        return target_ids

    def travel_destination_digest(self) -> list[dict[str, Any]]:
        """Somewhere to walk that is not already somewhere to talk.

        `dialogue_targets` already carries the talkable people and survives
        budgeting whole. Movement destinations lived only in
        `telemetry.nearby_entities`, a budgeted collection trimmed before
        anything else: eighteen nearby characters became one in the payload, and
        that one was in the room the agent was already standing in. It was given
        a way to leave and shown nowhere to go.

        Only the characters absent from `dialogue_targets` are listed, because
        the rest would be a second copy, and furthest first, because the near
        ones are the ones already covered. Kept short for the same reason: this
        is preserved through budgeting, so every entry is charged against the
        envelope that must always fit.
        """

        if self.telemetry is None:
            return []
        talkable = {target["id"] for target in self.dialogue_target_digest()}
        elsewhere = [
            entity
            for entity in self.telemetry.nearby_entities
            if entity.id and entity.name and entity.id not in talkable
        ]
        elsewhere.sort(
            key=lambda entity: entity.distance if entity.distance is not None else 0.0,
            reverse=True,
        )
        return [
            {
                "id": entity.id,
                "name": entity.name,
                "distance": entity.distance,
            }
            for entity in elsewhere[:8]
        ]

    def known_map_destination_digest(self) -> list[dict[str, Any]]:
        """Discovered settlement markers available for semantic long travel."""

        if self.telemetry is None or self.telemetry_stale:
            return []
        # Budgeted/replay observations can omit the squad while retaining the
        # primary character's authoritative game location. Preserve the
        # historical single-character interpretation in that case.
        #
        # One selected character at the destination means everyone travelling is
        # there. A group can be scattered, so arrival is not proven and travel
        # stays available.
        whole_group_present = (
            max(1, sum(character.selected for character in self.telemetry.squad)) == 1
        )
        return [
            destination.model_dump(mode="json", exclude_none=True)
            | {
                "travel_available": map_destination_travel_available(
                    destination,
                    current_location_id=self.telemetry.game.location_id,
                    inside_town_walls=self.telemetry.game.inside_town_walls,
                    location_authoritative=(
                        "game.location.identity" in self.telemetry.capabilities
                    ),
                    whole_group_present=whole_group_present,
                ),
            }
            for destination in sorted(
                self.telemetry.known_map_destinations,
                key=lambda destination: (destination.distance, destination.id),
            )
        ]

    def dialogue_target_digest(self) -> list[dict[str, Any]]:
        """Deterministic, authoritative interaction affordances for the planner.

        The planner must not re-derive who is talkable from raw entity flags —
        that judgment flaked live. This is the pre-validated answer: every
        non-hostile person the agent could approach and talk to, nearest first,
        with the vendor subset marked. `is_vendor` distinguishes a talk target
        the agent can also trade with.
        """

        if self.telemetry is None:
            return []
        return [
            {
                "id": target.id,
                "name": target.name,
                "distance": target.distance,
                "visible": target.visible,
                "camera_bearing_degrees": target.camera_bearing_degrees,
                "is_vendor": target.is_confirmed_vendor(),
            }
            for target in dialogue_targets(self.telemetry.nearby_entities)
        ]

    def context_target_digest(self) -> list[dict[str, Any]]:
        """Exact reviewed object/task pairs that can be dispatched right now."""

        if self.telemetry is None:
            return []
        targets = sorted(
            (target for target in self.telemetry.world_targets if target.context_actions),
            key=lambda target: (target.distance, target.name, target.id),
        )
        return [
            {
                "id": target.id,
                "name": target.name,
                "kind": target.kind,
                "distance": target.distance,
                "context_actions": [action.value for action in target.context_actions],
                "mining_resource_level": target.mining_resource_level,
                **(
                    {"screen_position": target.screen_position.model_dump(mode="json")}
                    if target.screen_position is not None
                    else {}
                ),
            }
            for target in targets[:16]
        ]

    def open_window_captions(self) -> list[str]:
        """Captions of the windows currently advertising controls, in order."""

        telemetry = self.telemetry
        if telemetry is None or telemetry.ui.visible_controls is None:
            return []
        seen: list[str] = []
        for control in telemetry.ui.visible_controls:
            if control.window and control.window not in seen:
                seen.append(control.window)
        return seen

    def window_owners(self) -> dict[str, dict[str, Any]]:
        """Whose inventory each open window is, keyed by normalized caption.

        Kenshi captions an inventory window with its owner's name, upper-cased,
        so the only thing standing between the planner and "is this the shop's
        stock or mine" is a case-insensitive name match. Doing it here means the
        answer arrives as a fact rather than as a string comparison the planner
        has to think to make - and gets wrong in the direction that sells your
        own coat.
        """

        telemetry = self.telemetry
        if telemetry is None:
            return {}
        owners: dict[str, dict[str, Any]] = {}
        vendors_by_caption: dict[str, list[NearbyEntity]] = {}
        for entity in telemetry.nearby_entities:
            if entity.shop_inventory_owner is True and entity.name:
                vendors_by_caption.setdefault(normalize_control_label(entity.name), []).append(
                    entity
                )
        for caption, vendors in vendors_by_caption.items():
            if len(vendors) == 1:
                owners[caption] = {
                    "belongs_to": "vendor",
                    "seller_id": vendors[0].id,
                }
            else:
                owners[caption] = {"belongs_to": "ambiguous"}
        # Squad last: a window naming one of your own characters is yours, even
        # if something nearby shares the name.
        squad_by_caption: dict[str, list[CharacterState]] = {}
        for character in telemetry.squad:
            if character.name:
                squad_by_caption.setdefault(normalize_control_label(character.name), []).append(
                    character
                )
        for caption, characters in squad_by_caption.items():
            if len(characters) == 1:
                owners[caption] = {
                    "belongs_to": "you",
                    "owner_id": characters[0].id,
                }
            else:
                owners[caption] = {"belongs_to": "ambiguous"}
        return owners

    def vendor_inventory_windows(self) -> list[str]:
        """Open windows that are a registered shop owner's own inventory."""

        owners = self.window_owners()
        return [
            caption
            for caption in self.open_window_captions()
            if owners.get(normalize_control_label(caption), {}).get("belongs_to") == "vendor"
        ]

    def trade_screen_open(self) -> bool:
        """Whether a shop's stock is open beside ours.

        Kenshi runs a trade as two inventory windows - the player's and the
        trader's - and every buy or sell is a right-click inside one of them.
        `ui.active_screen` collapses that to one label and reports 'inventory'
        whenever it cannot resolve the trader behind the window, so gating on
        the label alone refuses real trades: live run
        live-shop-ownership-regression-20260729-r2 lost four planner calls to
        "the trade screen is not open" while the operator was looking at it.

        Ownership decides it, exactly as it already decides which cell a
        purchase may bind to. Neither the label nor a count of item cells is
        evidence, and `active_shop_trader_count` counts traders loaded in the
        world rather than windows open on screen.
        """

        telemetry = self.telemetry
        if telemetry is None:
            return False
        if telemetry.ui.active_screen == "trade":
            return True
        open_inventories = telemetry.ui.open_inventory_windows
        if open_inventories is None or open_inventories < 2:
            return False
        return bool(self.vendor_inventory_windows())

    def visible_control_digest(
        self,
        limit: int = MAX_DIGESTED_VISIBLE_CONTROLS,
    ) -> list[dict[str, Any]]:
        """Exact controls the interface currently advertises, unambiguous only.

        The bounded argument source for `activate_visible_control`. A label that
        currently appears more than once is marked ambiguous rather than
        silently resolved, because a duplicate reference must fail closed rather
        than pick one. Bounds stay in telemetry; the adapter offers a semantic
        control and the controller re-resolves its coordinate.

        `limit` is normally derived from the room left in the payload rather
        than passed, so a screen with few controls surfaces all of them and a
        crowded one surfaces as many as actually fit.
        """

        telemetry = self.telemetry
        if telemetry is None or telemetry.ui.visible_controls is None:
            return []
        if "ui.visible_controls" not in telemetry.capabilities:
            return []
        controls = [
            control
            for control in telemetry.ui.visible_controls
            if not is_runtime_owned_visible_control(control)
        ]
        # Ambiguity is judged the way the binder judges it, or the advice is
        # stricter than the rule it describes. The binder resolves duplicate item
        # cells that are interchangeable - same window, same item, same price -
        # and only fails closed when they differ. Counting bare labels instead
        # flagged two identical Greenfruit as ambiguous, and since the prompt
        # forbids authoring an ambiguous entry, a stack of anything became
        # unsellable: the agent refused its own duplicate stock, correctly, on
        # our own advice.
        variants: dict[tuple[str, str, str], set[tuple[Any, ...]]] = {}
        for control in controls:
            key = (normalize_control_label(control.label), control.role, control.window)
            # Two buttons sharing a caption are two different buttons and stay
            # ambiguous; only stock has a notion of being interchangeable.
            distinguishing: tuple[Any, ...] = (id(control),)
            if control.role == "item":
                # A cell with no name cannot be shown interchangeable with
                # anything, so each stays its own variant and fails closed.
                distinguishing = (
                    (control.item_name, control.item_base_value)
                    if control.item_name is not None
                    else (id(control),)
                )
            variants.setdefault(key, set()).add(distinguishing)
        counts = {key: len(seen) for key, seen in variants.items()}
        # Bounded so this digest cannot overflow the irreducible planner
        # envelope. Truncation is fail-closed: an unlisted control is one the
        # planner will not author, never one it may author blindly.
        digest = []
        for control in budgeted_visible_controls(controls, limit):
            entry = {
                "exact_label": control.label,
                "role": control.role,
                "window": control.window,
                "ambiguous": (
                    counts[(normalize_control_label(control.label), control.role, control.window)]
                    > 1
                ),
            }
            # An item cell's label is a bare ordinal from the export walk, so
            # without these the planner sees "cell 37" and has to hover to learn
            # what it is - a round trip per cell, to recover facts the telemetry
            # already carries. The plugin walks the inventory structure
            # precisely so nobody has to hover; dropping them here threw that
            # away and left the agent unable to read a price it was looking at.
            if control.role == "item":
                entry["item_name"] = control.item_name
                # Named for the direction of the trade rather than for the
                # game's own "Value"/"Sell value" pair. Those two only
                # disambiguate each other when read together, and a planner
                # that sees one of them alone reads it as "the price" - which
                # is the mistake the neutral name `item_value` produced here.
                entry["buy_price"] = control.item_base_value
                entry["sell_price"] = control.item_sell_value
                entry["item_quantity"] = control.item_quantity
                entry["selected_inventory_accepts_item"] = control.selected_inventory_accepts_item
                entry["section"] = control.section
            digest.append(entry)
        return digest

    def with_world_facts(self, observed: Observation) -> Observation:
        """Take `observed`'s fresh world facts onto this runtime context.

        The one supported way to build an observation when you have current
        telemetry but no authority over what the run knows. Constructing a whole
        `Observation` from a snapshot instead leaves every runtime-owned field at
        its default, which reads downstream as a deliberate answer.
        """

        return self.model_copy(
            update={name: getattr(observed, name) for name in sorted(WORLD_FACT_FIELDS)},
            deep=True,
        )

    def log_digest(self) -> dict[str, Any]:
        """A compact record of this observation for the session log.

        Writing the whole observation every pump tick produced a 112 MB log in
        ten minutes - unworkable for an agent meant to run continuously. Only
        the replay environment ever needed the full payload; the evaluator reads
        a handful of fields, and a human reading the log wants orientation, not
        two hundred control bounds. This keeps every field either consumer uses
        plus enough state to diagnose a run, at roughly a hundredth of the size.
        """

        telemetry = self.telemetry
        digest: dict[str, Any] = {
            "run_id": self.run_id,
            "step_index": self.step_index,
            "mode": self.mode,
            "control_mode": self.control_mode.value,
            "world_revision": _json_model(self.world_revision),
            "telemetry_stale": self.telemetry_stale,
            "telemetry_age_seconds": self.telemetry_age_seconds,
            "events": list(self.events),
            "objective": self.objective,
            "advisor": _json_model(self.advisor),
            "digest": True,
        }
        if telemetry is None:
            digest["telemetry"] = None
            return digest

        selected = next(
            (character for character in telemetry.squad if character.selected),
            None,
        )
        digest["telemetry"] = {
            "sequence": telemetry.sequence,
            "source": telemetry.source,
            "identity_session_id": telemetry.identity_session_id,
            "capabilities": list(telemetry.capabilities),
            "game": {
                "loaded": telemetry.game.loaded,
                "paused": telemetry.game.paused,
                "money": telemetry.game.money,
                "elapsed_minutes": telemetry.game.elapsed_minutes,
                "location_name": telemetry.game.location_name,
            }
            | (
                {
                    "location_id": telemetry.game.location_id,
                    "inside_town_walls": telemetry.game.inside_town_walls,
                }
                if "game.location.identity" in telemetry.capabilities
                else {}
            ),
            "ui": {
                "active_screen": telemetry.ui.active_screen,
                "modal_open": telemetry.ui.modal_open,
                "dialogue_open": telemetry.ui.dialogue_open,
                "dialogue_target_id": telemetry.ui.dialogue_target_id,
                "tooltip_visible": telemetry.ui.tooltip_visible,
                "open_inventory_windows": telemetry.ui.open_inventory_windows,
                "management_screen_open": telemetry.ui.management_screen_open,
                "management_tab": telemetry.ui.management_tab,
                "selected_character_id": telemetry.ui.selected_character_id,
                # These are not bulk UI detail: together they are the authority
                # boundary for binding an output cell to one exact resource.
                "context_inventory_target_id": telemetry.ui.context_inventory_target_id,
                "visible_controls_complete": telemetry.ui.visible_controls_complete,
                "visible_control_count": (
                    len(telemetry.ui.visible_controls)
                    if telemetry.ui.visible_controls is not None
                    else None
                ),
                # Buttons and captions are the bulk of the controls and are not
                # worth keeping, but the item cells are the shelf: without them
                # a post-mortem cannot say what was for sale, at what price, or
                # whether the thing the agent kept reaching for was ever there.
                # They are the minority of controls, so this stays cheap.
                "item_cells": [
                    {
                        "label": control.label,
                        "window": control.window,
                        "section": control.section,
                        "item_name": control.item_name,
                        "item_base_value": control.item_base_value,
                        "item_sell_value": control.item_sell_value,
                        "item_quantity": control.item_quantity,
                        "selected_inventory_accepts_item": (
                            control.selected_inventory_accepts_item
                        ),
                    }
                    for control in (telemetry.ui.visible_controls or [])
                    if control.role == "item"
                ][:60],
                "open_windows": self.open_window_captions(),
                # Who owns each open inventory and how much is in it. The count
                # beside this says two windows are open; it cannot say whose, or
                # whether the export saw inside them. A live trade reached two
                # windows and the agent reported no way to move anything, and
                # the bundle could not distinguish "the export was empty" from
                # "the offer was filtered" - so the next step was another run
                # rather than a query.
                "open_inventories": [
                    {
                        "owner_id": inventory.owner_id,
                        "owner_name": inventory.owner_name,
                        "owner_kind": inventory.owner_kind,
                        "player_owned": inventory.player_owned,
                        "money": inventory.money,
                        "item_count": sum(
                            len(section.items) for section in inventory.sections
                        ),
                        "sections": [
                            section.name
                            for section in inventory.sections
                            if section.items
                        ][:8],
                    }
                    for inventory in telemetry.ui.open_inventories
                ],
                "open_inventories_complete": telemetry.ui.open_inventories_complete,
            },
            # The evaluator reconstructs native command causality from these, so
            # they are kept whole rather than counted.
            "native_control": _json_model(telemetry.native_control),
            "active_shop_trader_count": telemetry.active_shop_trader_count,
            "nearby_entity_count": len(telemetry.nearby_entities),
            "dialogue_target_count": len(dialogue_targets(telemetry.nearby_entities)),
            "world_target_count": len(telemetry.world_targets),
            "context_targets": self.context_target_digest(),
            "selected": (
                {
                    "id": selected.id,
                    "name": selected.name,
                    "hunger": selected.hunger,
                    "food_items": selected.food_items,
                    "in_combat": selected.in_combat,
                    "indoors": selected.indoors,
                    "inventory_complete": selected.inventory_complete,
                    # Kept so a post-mortem can tell a healthy run from one
                    # where the character was quietly being beaten.
                    "blood": selected.blood,
                    "bleeding_rate": selected.bleeding_rate,
                    "position": (
                        _json_model(selected.position) if selected.position is not None else None
                    ),
                }
                if selected is not None
                else None
            ),
            # What Kenshi is holding for every selected character, not just the
            # first. Retained work is per-character and is the thing that makes
            # an accepted order look like a failure and a move look stalled; a
            # digest that drops it cannot explain either after the fact.
            #
            # Counts and the leading entries only. The full lists are bounded in
            # telemetry already, and a post-mortem needs to know that work was
            # retained and roughly what, not every queued step.
            "retained_work": [
                {
                    "id": character.id,
                    "name": character.name,
                    "has_player_orders": character.task_state.has_player_orders,
                    "orders_count": character.task_state.orders_count,
                    "jobs_enabled": character.task_state.jobs_enabled,
                    "jobs_count": character.task_state.jobs_count,
                    "permajobs_count": character.task_state.permajobs_count,
                    "orders": [
                        entry.task_name for entry in character.task_state.orders[:3]
                    ],
                    "jobs": [entry.task_name for entry in character.task_state.jobs[:3]],
                    "current_activity": (
                        character.task_state.current_activity.task_name
                        if character.task_state.current_activity is not None
                        else None
                    ),
                    # Channels whose count is a lower bound rather than a
                    # total. Kenshi exports no size() for the order queue, so
                    # a deep queue arrives proven-but-partial; reporting the
                    # count bare would present a floor as a ceiling.
                    "bounded_counts": [
                        name
                        for name, complete in (
                            ("orders", character.task_state.orders_complete),
                            ("jobs", character.task_state.jobs_complete),
                            ("permajobs", character.task_state.permajobs_complete),
                        )
                        if not complete
                    ],
                }
                for character in telemetry.squad
                if character.selected and character.task_state is not None
            ],
        }
        return digest


# Two owners share this one model, and confusing them is a silent-loss bug.
#
# The environment observes the world: it holds a telemetry snapshot and can fill
# these in from it alone. The runtime knows everything else - what the run is
# for, what it remembers, what it has already tried - and no amount of telemetry
# can reconstruct any of it.
#
# The distinction stayed implicit while every field carried a default, so code
# that legitimately had only world facts could build a whole `Observation` and
# silently assert defaults for the rest. `planning_mode` was the field that made
# this expensive: the live input boundary builds its observation from a snapshot,
# so it always claimed the default mode, and affordance enumeration read that as
# authority and withheld every monitored operation. Mining could not be
# authorized at all, and said "Affordance is absent from the current observation"
# while the deposit sat there in every publication.
#
# Fresh world facts belong on retained runtime context, never beside invented
# context - which is what `with_world_facts` is for.
WORLD_FACT_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "step_index",
        "observed_at",
        "mode",
        "control_mode",
        "world_revision",
        "telemetry",
        "telemetry_stale",
        "telemetry_age_seconds",
        "screenshot_path",
        "screenshot_sha256",
        "events",
        "recent_changes",
    }
)

RUNTIME_CONTEXT_FIELDS: frozenset[str] = frozenset(
    {
        "objective",
        "active_plan",
        "recent_action_outcomes",
        "recent_plan_outcomes",
        "recent_continuity_receipts",
        "continuity_writes_degraded_reason",
        "continuity_reads_degraded_reason",
        "planner_feedback",
        "memories",
        "memory_recall",
        "memory_search",
        "fieldbook_projects",
        "active_fieldbook_project",
        "recent_fieldbook_receipts",
        "fieldbook_read",
        "advisor",
    }
)

# Everything the runtime owns and re-derives for each planner call. A subset,
# because `active_plan` is resolved from the store rather than supplied.
PLANNER_CONTEXT_FIELDS: frozenset[str] = RUNTIME_CONTEXT_FIELDS - {"active_plan"}

# The runtime-owned fields that must survive a telemetry publication rather than
# wait to be re-derived, because losing one between two planner calls loses the
# only copy: a hosted advisor brief, a retained memory, the run's objective.
PUBLICATION_SURVIVING_FIELDS: frozenset[str] = frozenset(
    {
        "objective",
        "recent_action_outcomes",
        "memories",
        "advisor",
    }
)


def _assert_every_field_has_an_owner() -> None:
    """A new `Observation` field must be classified, not silently assumed.

    Adding one without saying who owns it is exactly how the boundary came to
    fabricate planner context, so this fails at import rather than at runtime.
    """

    declared = set(Observation.model_fields)
    classified = WORLD_FACT_FIELDS | RUNTIME_CONTEXT_FIELDS
    unclassified = declared - classified
    if unclassified:
        raise RuntimeError(
            "Observation fields with no declared owner: "
            + ", ".join(sorted(unclassified))
            + ". Add each to WORLD_FACT_FIELDS or RUNTIME_CONTEXT_FIELDS."
        )
    unknown = classified - declared
    if unknown:
        raise RuntimeError(
            "Ownership declared for fields Observation does not have: "
            + ", ".join(sorted(unknown))
        )
    overlap = WORLD_FACT_FIELDS & RUNTIME_CONTEXT_FIELDS
    if overlap:
        raise RuntimeError("Fields claimed by both owners: " + ", ".join(sorted(overlap)))
    for name, subset in (
        ("PLANNER_CONTEXT_FIELDS", PLANNER_CONTEXT_FIELDS),
        ("PUBLICATION_SURVIVING_FIELDS", PUBLICATION_SURVIVING_FIELDS),
    ):
        stray = subset - RUNTIME_CONTEXT_FIELDS
        if stray:
            raise RuntimeError(f"{name} names non-runtime fields: " + ", ".join(sorted(stray)))


_assert_every_field_has_an_owner()
