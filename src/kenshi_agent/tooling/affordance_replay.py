"""Typed reconstruction of the semantic choices delivered to a planner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..affordances import (
    AFFORDANCE_ADAPTERS,
    AffordanceSelection,
)
from ..core.affordance import (
    AffordanceSetEvent,
    AffordanceSetOffer,
    AffordanceSetParameter,
)


class AffordanceSetReplayUnavailable(ValueError):
    """The log cannot prove the planner's delivered choice set."""


def load_affordance_sets(path: Path) -> tuple[AffordanceSetEvent, ...]:
    """Load typed affordance-set evidence without inferring from old prompts."""

    events: list[AffordanceSetEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise AffordanceSetReplayUnavailable(
                    f"Invalid JSON on line {line_number} of {path}."
                ) from exc
            if not isinstance(record, dict) or record.get("event_type") != "affordance_set":
                continue
            try:
                event = AffordanceSetEvent.model_validate(record.get("payload"))
            except ValueError as exc:
                raise AffordanceSetReplayUnavailable(
                    f"Invalid affordance_set payload on line {line_number} of {path}."
                ) from exc
            adapter_operations = {
                adapter.name: adapter.operation_kinds for adapter in AFFORDANCE_ADAPTERS
            }
            recorded_sources = {
                source.source_adapter for source in event.source_completeness
            }
            if recorded_sources != adapter_operations.keys():
                raise AffordanceSetReplayUnavailable(
                    f"Affordance source completeness inventory does not match the "
                    f"registered adapters on line {line_number} of {path}."
                )
            if any(
                offer.source_adapter not in adapter_operations
                or offer.operation_kind not in adapter_operations[offer.source_adapter]
                for offer in event.offers
            ):
                raise AffordanceSetReplayUnavailable(
                    f"Unknown affordance adapter/operation pair on line {line_number} "
                    f"of {path}."
                )
            events.append(event)
    if not events:
        raise AffordanceSetReplayUnavailable(
            "No typed affordance_set events exist; exact choice reconstruction is unavailable."
        )
    context_ids = [event.context_id for event in events]
    if len(context_ids) != len(set(context_ids)):
        raise AffordanceSetReplayUnavailable(
            "Duplicate affordance_set context identities prevent exact reconstruction."
        )
    return tuple(events)


def _parameter_value_is_valid(spec: AffordanceSetParameter, value: Any) -> bool:
    if spec.kind == "integer":
        valid_kind = isinstance(value, int) and not isinstance(value, bool)
    elif spec.kind == "number":
        valid_kind = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif spec.kind in {"text", "choice"}:
        valid_kind = isinstance(value, str)
    else:
        return False
    if not valid_kind:
        return False
    if spec.choices and value not in spec.choices:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if spec.minimum is not None and value < spec.minimum:
            return False
        if spec.maximum is not None and value > spec.maximum:
            return False
    return True


def reconstruct_choice(
    event: AffordanceSetEvent,
    selection: AffordanceSelection,
    *,
    expected_affordance_id: str | None = None,
    expected_operation_kind: str | None = None,
    expected_source_adapter: str | None = None,
) -> AffordanceSetOffer:
    """Resolve one planner selection solely against typed delivered evidence."""

    supplied = selection.parameter_map()
    if len(supplied) != len(selection.parameters):
        raise ValueError("affordance selection parameter names must be unique")
    matches: list[AffordanceSetOffer] = []
    for offer in event.offers:
        if offer.semantic != selection.semantic:
            continue
        if offer.selection_target_id != selection.target_id:
            continue
        if expected_affordance_id is not None:
            if offer.affordance_id != expected_affordance_id:
                continue
        if expected_operation_kind is not None:
            if offer.operation_kind != expected_operation_kind:
                continue
        if expected_source_adapter is not None:
            if offer.source_adapter != expected_source_adapter:
                continue
        specs = {spec.name: spec for spec in offer.semantic_parameters}
        if supplied.keys() - specs.keys():
            continue
        if {name for name, spec in specs.items() if spec.required} - supplied.keys():
            continue
        if not all(
            _parameter_value_is_valid(specs[name], value)
            for name, value in supplied.items()
        ):
            continue
        matches.append(offer)
    if len(matches) != 1:
        raise ValueError(
            "planner selection does not resolve to exactly one delivered affordance"
        )
    return matches[0]
