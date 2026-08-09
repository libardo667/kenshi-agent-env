from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from math import inf
from typing import Any

JsonObject = dict[str, Any]

_ROOT_COLLECTION_PATHS = (
    "events",
    "recent_action_outcomes",
    "recent_plan_outcomes",
    "recent_continuity_receipts",
    "recent_fieldbook_receipts",
    "memories",
    "fieldbook_projects",
)
_TELEMETRY_COLLECTION_PATHS = (
    "telemetry.capabilities",
    "telemetry.ui.dialogue_options",
    "telemetry.ui.visible_controls",
    "telemetry.selected_character_ids",
    "telemetry.controller_commands.commands",
    "telemetry.roster",
    "telemetry.nearby_entities",
    "telemetry.world_targets",
    "telemetry.known_map_destinations",
    "telemetry.warnings",
)
_COLLECTION_PATHS = _ROOT_COLLECTION_PATHS + _TELEMETRY_COLLECTION_PATHS
_UI_DEFERRED_FIELDS = {
    "dialogue_options",
    "tooltip_text",
    "tooltip_source_bounds",
    "visible_controls",
}


class PlannerPayloadContextError(ValueError):
    """Raised when irreducible state cannot fit the hard request envelope."""

    def __init__(
        self,
        *,
        measurement: str,
        hard_max_units: int,
        required_units: int,
    ) -> None:
        self.measurement = measurement
        self.hard_max_units = hard_max_units
        self.required_units = required_units
        super().__init__(
            "Planner observation cannot fit the hard request envelope: "
            f"measurement={measurement}, hard_max_units={hard_max_units}, "
            f"required_units={required_units}"
        )


def budget_observation_payload(
    payload: JsonObject,
    *,
    full_text: str,
    max_chars: int,
    hard_max_chars: int | None = None,
    measure: Callable[[str], int] = len,
    measurement: str = "characters",
) -> str:
    """Return full observation JSON or a deterministic semantic reduction.

    ``max_chars`` is the proactive compaction target. ``hard_max_chars`` is
    the request envelope that may actually reject an irreducible payload. A
    decision-critical envelope may expand past the target, but never past the
    hard limit.
    """

    hard_max_chars = max_chars if hard_max_chars is None else hard_max_chars
    if max_chars > hard_max_chars:
        raise ValueError("max_chars cannot exceed hard_max_chars")
    original_units = measure(full_text)
    if original_units <= max_chars:
        return full_text

    original = deepcopy(payload)
    retained = irreducible_payload(original)
    text = _serialize_budgeted(
        original,
        retained,
        max_chars=max_chars,
        hard_max_chars=hard_max_chars,
        original_chars=original_units,
        measurement=measurement,
    )
    required_units = measure(text)
    effective_max_chars = max(max_chars, required_units)
    if effective_max_chars > hard_max_chars:
        raise PlannerPayloadContextError(
            measurement=measurement,
            hard_max_units=hard_max_chars,
            required_units=required_units,
        )

    def attempt(mutator: Callable[[JsonObject], None]) -> None:
        nonlocal retained, text
        candidate = deepcopy(retained)
        mutator(candidate)
        candidate_text = _serialize_budgeted(
            original,
            candidate,
            max_chars=max_chars,
            hard_max_chars=hard_max_chars,
            original_chars=original_units,
            measurement=measurement,
        )
        if measure(candidate_text) <= effective_max_chars:
            retained = candidate
            text = candidate_text

    telemetry = original.get("telemetry")
    if isinstance(telemetry, dict):
        native = telemetry["controller_commands"]
        retained_acknowledgement_ids = {
            item["command_id"]
            for item in retained["telemetry"]["controller_commands"]["commands"]
        }
        for acknowledgement in sorted(
            native["commands"],
            key=_acknowledgement_sort_key,
        ):
            if acknowledgement["command_id"] in retained_acknowledgement_ids:
                continue
            attempt(
                _append_mutator(
                    "telemetry.controller_commands.commands",
                    acknowledgement,
                )
            )

        for capability in sorted(telemetry["capabilities"]):
            attempt(
                _append_mutator(
                    "telemetry.capabilities",
                    capability,
                )
            )

        ui = telemetry["ui"]
        for field_name in ("tooltip_text", "tooltip_source_bounds"):
            if ui[field_name] is not None:
                attempt(
                    _set_mutator(
                        f"telemetry.ui.{field_name}",
                        ui[field_name],
                    )
                )
        if ui["dialogue_options"] is not None:
            for option in ui["dialogue_options"]:
                attempt(
                    _append_mutator(
                        "telemetry.ui.dialogue_options",
                        option,
                    )
                )
        if ui["visible_controls"] is not None:
            for control in sorted(ui["visible_controls"], key=_canonical_json):
                attempt(
                    _append_mutator(
                        "telemetry.ui.visible_controls",
                        control,
                    )
                )

        camera = telemetry["camera"]
        if _has_meaningful_value(camera):
            attempt(
                _set_mutator(
                    "telemetry.camera",
                    camera,
                )
            )

        retained_roster_ids = {
            character["id"] for character in retained["telemetry"]["roster"]
        }
        for character in sorted(telemetry["roster"], key=_entity_sort_key):
            if character["id"] in retained_roster_ids:
                continue
            attempt(
                _append_mutator(
                    "telemetry.roster",
                    character,
                )
            )

        for warning in sorted(telemetry["warnings"]):
            attempt(
                _append_mutator(
                    "telemetry.warnings",
                    warning,
                )
            )

    for event in sorted(original["events"]):
        attempt(
            _append_mutator(
                "events",
                event,
            )
        )

    older_outcomes = original["recent_action_outcomes"][:-1]
    for outcome in reversed(older_outcomes):
        attempt(
            _prepend_mutator(
                "recent_action_outcomes",
                outcome,
            )
        )

    for plan_outcome in reversed(original["recent_plan_outcomes"][:-1]):
        attempt(
            _prepend_mutator(
                "recent_plan_outcomes",
                plan_outcome,
            )
        )

    retained_receipt_ids = {
        str(receipt["receipt_id"])
        for receipt in retained["recent_continuity_receipts"]
    }
    for receipt in reversed(original["recent_continuity_receipts"]):
        receipt_id = str(receipt["receipt_id"])
        if receipt_id in retained_receipt_ids:
            continue
        wanted_ids = retained_receipt_ids | {receipt_id}
        attempt(
            _set_mutator(
                "recent_continuity_receipts",
                [
                    item
                    for item in original["recent_continuity_receipts"]
                    if str(item["receipt_id"]) in wanted_ids
                ],
            )
        )
        retained_receipt_ids = {
            str(item["receipt_id"])
            for item in retained["recent_continuity_receipts"]
        }

    retained_fieldbook_receipt_ids = {
        str(receipt["receipt_id"])
        for receipt in retained["recent_fieldbook_receipts"]
    }
    fieldbook_receipts = original.get("recent_fieldbook_receipts", [])
    for receipt in reversed(fieldbook_receipts):
        receipt_id = str(receipt["receipt_id"])
        if receipt_id in retained_fieldbook_receipt_ids:
            continue
        wanted_ids = retained_fieldbook_receipt_ids | {receipt_id}
        attempt(
            _set_mutator(
                "recent_fieldbook_receipts",
                [
                    item
                    for item in fieldbook_receipts
                    if str(item["receipt_id"]) in wanted_ids
                ],
            )
        )
        retained_fieldbook_receipt_ids = {
            str(item["receipt_id"])
            for item in retained["recent_fieldbook_receipts"]
        }

    retained_memory_ids = {str(item["memory_id"]) for item in retained["memories"]}
    for memory in sorted(original["memories"], key=_memory_sort_key, reverse=True):
        if str(memory["memory_id"]) in retained_memory_ids:
            continue
        attempt(
            _append_mutator(
                "memories",
                memory,
            )
        )

    for project in original.get("fieldbook_projects", []):
        attempt(_append_mutator("fieldbook_projects", project))

    if isinstance(telemetry, dict):
        retained_nearby_ids = {
            entity["id"] for entity in retained["telemetry"]["nearby_entities"]
        }
        for entity in sorted(telemetry["nearby_entities"], key=_nearby_sort_key):
            if entity["id"] in retained_nearby_ids:
                continue
            attempt(
                _append_mutator(
                    "telemetry.nearby_entities",
                    entity,
                )
            )
        retained_world_target_ids = {
            target["id"] for target in retained["telemetry"]["world_targets"]
        }
        for target in sorted(telemetry["world_targets"], key=_world_target_sort_key):
            if target["id"] in retained_world_target_ids:
                continue
            attempt(
                _append_mutator(
                    "telemetry.world_targets",
                    target,
                )
            )

    return text


def irreducible_payload(
    original: JsonObject,
    *,
    preserve_current_target_memories: bool = True,
) -> JsonObject:
    retained = {
        key: deepcopy(value)
        for key, value in original.items()
        if key not in _ROOT_COLLECTION_PATHS
    }
    retained["events"] = []
    retained["recent_action_outcomes"] = (
        [deepcopy(original["recent_action_outcomes"][-1])]
        if original["recent_action_outcomes"]
        else []
    )
    # The most recent plan outcome carries the objective the agent was last
    # pursuing and why it stopped. Dropping it first would leave the planner
    # replanning against a purpose it can no longer see.
    retained["recent_plan_outcomes"] = (
        [deepcopy(original["recent_plan_outcomes"][-1])]
        if original["recent_plan_outcomes"]
        else []
    )
    receipts = original.get("recent_continuity_receipts", [])
    latest_adverse = next(
        (
            receipt
            for receipt in reversed(receipts)
            if receipt["status"] in {"rejected", "failed"}
        ),
        None,
    )
    retained["recent_continuity_receipts"] = (
        [deepcopy(latest_adverse)]
        if latest_adverse is not None
        else ([deepcopy(receipts[-1])] if receipts else [])
    )
    fieldbook_receipts = original.get("recent_fieldbook_receipts", [])
    latest_adverse_fieldbook = next(
        (
            receipt
            for receipt in reversed(fieldbook_receipts)
            if receipt["status"] in {"rejected", "failed"}
        ),
        None,
    )
    retained["recent_fieldbook_receipts"] = (
        [deepcopy(latest_adverse_fieldbook)]
        if latest_adverse_fieldbook is not None
        else (
            [deepcopy(fieldbook_receipts[-1])]
            if fieldbook_receipts
            else []
        )
    )
    current_target_ids = _current_memory_target_ids(original)
    retained["memories"] = (
        sorted(
            (
                deepcopy(memory)
                for memory in original["memories"]
                if _decision_critical(memory, current_target_ids)
            ),
            key=_memory_sort_key,
            reverse=True,
        )
        if preserve_current_target_memories
        else []
    )
    retained["fieldbook_projects"] = []

    telemetry = original.get("telemetry")
    if not isinstance(telemetry, dict):
        return retained

    ui = telemetry["ui"]
    native = telemetry["controller_commands"]
    critical_commands = _critical_commands(native)
    referenced_target_ids = {
        value
        for value in (
            ui["dialogue_target_id"],
            native["last_target_id"],
            *(item["target_id"] for item in critical_commands),
        )
        if value is not None
    }
    referenced_target_ids.update(
        _outcome_target_ids(original["recent_action_outcomes"][-1])
        if original["recent_action_outcomes"]
        else set()
    )

    retained_ui = {
        key: deepcopy(value)
        for key, value in ui.items()
        if key not in _UI_DEFERRED_FIELDS
    }
    retained_ui["dialogue_options"] = None if ui["dialogue_options"] is None else []
    retained_ui["visible_controls"] = None if ui["visible_controls"] is None else []

    retained_native = deepcopy(native)
    retained_native["commands"] = critical_commands

    retained["telemetry"] = deepcopy(telemetry)
    del retained["telemetry"]["camera"]
    retained["telemetry"].update(
        {
            "capabilities": [],
            "ui": retained_ui,
            "controller_commands": retained_native,
            "roster": sorted(
                (deepcopy(character) for character in telemetry["roster"]),
                key=_entity_sort_key,
            ),
            "nearby_entities": sorted(
                (
                    deepcopy(entity)
                    for entity in telemetry["nearby_entities"]
                    if entity["id"] in referenced_target_ids
                ),
                key=_entity_sort_key,
            ),
            "world_targets": sorted(
                (
                    deepcopy(target)
                    for target in telemetry["world_targets"]
                    if target["id"] in referenced_target_ids
                ),
                key=_entity_sort_key,
            ),
            # The complete, authority-filtered list lives in the top-level
            # `known_map_destinations` digest. Do not pay for a duplicate raw
            # telemetry copy in the irreducible envelope.
            "known_map_destinations": [],
            "warnings": [],
        }
    )
    return retained


def _decision_critical(memory: JsonObject, current_target_ids: set[str]) -> bool:
    """Whether dropping this memory could make the next plan unsafe or amnesiac.

    Two kinds survive budgeting: what the agent is currently committed to, and
    what it knows about an entity in front of it right now. Everything else is
    context, and context is what a budget is for.
    """

    return (
        memory.get("kind") == "commitment"
        or memory.get("target_id") in current_target_ids
    )


def _current_memory_target_ids(original: JsonObject) -> set[str]:
    if original.get("telemetry_stale") is True:
        return set()
    telemetry = original.get("telemetry")
    if not isinstance(telemetry, dict):
        return set()

    target_ids: set[str] = set()
    for collection_name in (
        "roster",
        "nearby_entities",
        "world_targets",
        "known_map_destinations",
    ):
        collection = telemetry.get(collection_name)
        if not isinstance(collection, list):
            continue
        target_ids.update(
            str(item["id"])
            for item in collection
            if isinstance(item, dict) and item.get("id")
        )
    ui = telemetry.get("ui")
    if isinstance(ui, dict) and ui.get("dialogue_target_id"):
        target_ids.add(str(ui["dialogue_target_id"]))
    return target_ids


def _memory_sort_key(memory: JsonObject) -> tuple[float, str, str]:
    """Rank by what the agent declared and when it was made, never by reads."""

    return (
        float(memory["salience"]),
        str(memory["created_at"]),
        str(memory["memory_id"]),
    )


def _critical_commands(native: JsonObject) -> list[JsonObject]:
    commands = native["commands"]
    if not commands:
        return []

    critical_ids: set[str] = set()
    critical_ids.update(
        command["command_id"]
        for command in commands
        if command["status"] == "accepted"
    )
    critical_ids.add(max(commands, key=_acknowledgement_sort_key)["command_id"])
    return sorted(
        (
            deepcopy(item)
            for item in commands
            if item["command_id"] in critical_ids
        ),
        key=_acknowledgement_sort_key,
    )


def _serialize_budgeted(
    original: JsonObject,
    retained: JsonObject,
    *,
    max_chars: int,
    hard_max_chars: int,
    original_chars: int,
    measurement: str,
) -> str:
    document = deepcopy(retained)
    document["observation_budget"] = {
        "truncated": True,
        "strategy": "semantic-v1",
        "target_units": max_chars,
        "hard_max_units": hard_max_chars,
        "original_units": original_chars,
        "measurement": measurement,
        "omitted": _omission_metadata(original, retained),
    }
    return _compact_json(document)


def _omission_metadata(original: JsonObject, retained: JsonObject) -> JsonObject:
    collection_counts: JsonObject = {}
    for path in _COLLECTION_PATHS:
        original_value = _get_path(original, path)
        retained_value = _get_path(retained, path)
        if not isinstance(original_value, list):
            continue
        retained_count = len(retained_value) if isinstance(retained_value, list) else 0
        if retained_count != len(original_value):
            collection_counts[path] = {
                "original": len(original_value),
                "retained": retained_count,
            }

    omitted_fields = sorted(_omitted_field_paths(original, retained))
    return {
        "collections": collection_counts,
        "fields": omitted_fields,
    }


def _omitted_field_paths(
    original: Any,
    retained: Any,
    *,
    prefix: str = "",
) -> set[str]:
    if not isinstance(original, dict):
        return set()

    paths: set[str] = set()
    retained_mapping = retained if isinstance(retained, dict) else {}
    for key, original_value in original.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(original_value, list):
            continue
        if isinstance(original_value, dict):
            paths.update(
                _omitted_field_paths(
                    original_value,
                    retained_mapping.get(key),
                    prefix=path,
                )
            )
            continue
        if key not in retained_mapping and _has_meaningful_value(original_value):
            paths.add(path)
    return paths


def _has_meaningful_value(value: Any) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    if isinstance(value, dict):
        return any(_has_meaningful_value(item) for item in value.values())
    return True


def _get_path(document: JsonObject, path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_path(document: JsonObject, path: str, value: Any) -> None:
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = deepcopy(value)


def _append_path(document: JsonObject, path: str, value: Any) -> None:
    collection = _get_path(document, path)
    if not isinstance(collection, list):
        raise TypeError(f"{path} is not a retained collection")
    collection.append(deepcopy(value))


def _prepend_path(document: JsonObject, path: str, value: Any) -> None:
    collection = _get_path(document, path)
    if not isinstance(collection, list):
        raise TypeError(f"{path} is not a retained collection")
    collection.insert(0, deepcopy(value))


def _set_mutator(path: str, value: Any) -> Callable[[JsonObject], None]:
    def mutate(candidate: JsonObject) -> None:
        _set_path(candidate, path, value)

    return mutate


def _append_mutator(path: str, value: Any) -> Callable[[JsonObject], None]:
    def mutate(candidate: JsonObject) -> None:
        _append_path(candidate, path, value)

    return mutate


def _prepend_mutator(path: str, value: Any) -> Callable[[JsonObject], None]:
    def mutate(candidate: JsonObject) -> None:
        _prepend_path(candidate, path, value)

    return mutate


def _outcome_target_ids(outcome: JsonObject) -> set[str]:
    action = outcome.get("action")
    if not isinstance(action, dict):
        return set()
    ids: set[str] = set()
    semantic_target = action.get("target_id")
    if isinstance(semantic_target, str) and semantic_target:
        ids.add(semantic_target)
    return ids


# `json.dumps` intentionally treats `ensure_ascii=False` and `None` identically.
# Exact Unicode, key-order, and separator behavior is asserted at this adapter's
# boundary; mutating inside the stdlib delegation only manufactures equivalent
# keyword-value mutants.
# pragma: no mutate start
def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
# pragma: no mutate end


def _compact_json(value: Any) -> str:
    return _canonical_json(value)


def _entity_sort_key(entity: JsonObject) -> tuple[str, str]:
    return str(entity["id"]), _canonical_json(entity)


def _nearby_sort_key(entity: JsonObject) -> tuple[float, str, str]:
    distance = entity["distance"]
    return (
        inf if distance is None else float(distance),
        str(entity["id"]),
        _canonical_json(entity),
    )


def _world_target_sort_key(target: JsonObject) -> tuple[float, str, str]:
    return (
        float(target["distance"]),
        str(target["id"]),
        _canonical_json(target),
    )


def _acknowledgement_sort_key(item: JsonObject) -> tuple[int, str]:
    return int(item["acknowledged_at_telemetry_sequence"]), str(item["command_id"])
