from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any

JsonObject = dict[str, Any]

_ROOT_COLLECTION_PATHS = (
    "events",
    "recent_action_outcomes",
    "available_skills",
    "skill_specs",
    "memories",
)
_TELEMETRY_COLLECTION_PATHS = (
    "telemetry.capabilities",
    "telemetry.ui.dialogue_options",
    "telemetry.ui.visible_controls",
    "telemetry.ui.selected_character_ids",
    "telemetry.native_control.acknowledgements",
    "telemetry.squad",
    "telemetry.nearby_entities",
    "telemetry.warnings",
)
_COLLECTION_PATHS = _ROOT_COLLECTION_PATHS + _TELEMETRY_COLLECTION_PATHS
_UI_DEFERRED_FIELDS = {
    "dialogue_options",
    "tooltip_text",
    "tooltip_source_bounds",
    "visible_controls",
}


class PlannerPayloadBudgetError(ValueError):
    """Raised when the exact safety envelope cannot fit the configured budget."""

    def __init__(self, *, max_chars: int, required_chars: int) -> None:
        self.max_chars = max_chars
        self.required_chars = required_chars
        super().__init__(
            "Planner observation budget is too small for the irreducible "
            f"safety envelope: max_chars={max_chars}, required_chars={required_chars}"
        )


def budget_observation_payload(
    payload: JsonObject,
    *,
    full_text: str,
    max_chars: int,
) -> str:
    """Return full observation JSON or a deterministic semantic reduction."""

    if len(full_text) <= max_chars:
        return full_text

    original = deepcopy(payload)
    retained = _irreducible_payload(original)
    text = _serialize_budgeted(
        original,
        retained,
        max_chars=max_chars,
        original_chars=len(full_text),
    )
    if len(text) > max_chars:
        raise PlannerPayloadBudgetError(
            max_chars=max_chars,
            required_chars=len(text),
        )

    def attempt(mutator: Callable[[JsonObject], None]) -> None:
        nonlocal retained, text
        candidate = deepcopy(retained)
        mutator(candidate)
        candidate_text = _serialize_budgeted(
            original,
            candidate,
            max_chars=max_chars,
            original_chars=len(full_text),
        )
        if len(candidate_text) <= max_chars:
            retained = candidate
            text = candidate_text

    telemetry = original.get("telemetry")
    if isinstance(telemetry, dict):
        native = telemetry["native_control"]
        retained_acknowledgement_ids = {
            item["command_id"]
            for item in retained["telemetry"]["native_control"]["acknowledgements"]
        }
        for acknowledgement in sorted(
            native["acknowledgements"],
            key=_acknowledgement_sort_key,
        ):
            if acknowledgement["command_id"] in retained_acknowledgement_ids:
                continue
            attempt(
                _append_mutator(
                    "telemetry.native_control.acknowledgements",
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

        retained_squad_ids = {
            character["id"] for character in retained["telemetry"]["squad"]
        }
        for character in sorted(telemetry["squad"], key=_entity_sort_key):
            if character["id"] in retained_squad_ids:
                continue
            attempt(
                _append_mutator(
                    "telemetry.squad",
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

    sorted_skill_specs = sorted(
        original["skill_specs"],
        key=lambda item: (item["name"], _canonical_json(item)),
    )
    specs_by_name: dict[str, list[JsonObject]] = {}
    for skill_spec in sorted_skill_specs:
        specs_by_name.setdefault(str(skill_spec["name"]), []).append(skill_spec)
    available_names = set(original["available_skills"])
    for skill_name in sorted(available_names):
        attempt(
            _skill_contract_mutator(
                skill_name,
                specs_by_name.get(skill_name, []),
            )
        )
    for skill_spec in sorted_skill_specs:
        if skill_spec["name"] not in available_names:
            attempt(_append_mutator("skill_specs", skill_spec))

    older_outcomes = original["recent_action_outcomes"][:-1]
    for outcome in reversed(older_outcomes):
        attempt(
            _prepend_mutator(
                "recent_action_outcomes",
                outcome,
            )
        )

    for memory in sorted(
        original["memories"],
        key=lambda item: (
            float(item["salience"]),
            str(item["last_accessed_at"]),
            int(item["id"]),
        ),
        reverse=True,
    ):
        attempt(
            _append_mutator(
                "memories",
                memory,
            )
        )

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

    return text


def _irreducible_payload(original: JsonObject) -> JsonObject:
    retained = {
        key: deepcopy(value)
        for key, value in original.items()
        if key not in {*_ROOT_COLLECTION_PATHS, "telemetry"}
    }
    retained["events"] = []
    retained["recent_action_outcomes"] = (
        [deepcopy(original["recent_action_outcomes"][-1])]
        if original["recent_action_outcomes"]
        else []
    )
    retained["available_skills"] = []
    retained["skill_specs"] = []
    retained["memories"] = []

    telemetry = original.get("telemetry")
    if not isinstance(telemetry, dict):
        retained["telemetry"] = telemetry
        return retained

    ui = telemetry["ui"]
    native = telemetry["native_control"]
    critical_acknowledgements = _critical_acknowledgements(native)
    selected_ids = set(ui["selected_character_ids"])
    if ui["selected_character_id"] is not None:
        selected_ids.add(ui["selected_character_id"])
    for acknowledgement in critical_acknowledgements:
        selected_ids.update(acknowledgement["selected_character_ids"])
    selected_ids.update(
        character["id"] for character in telemetry["squad"] if character["selected"]
    )

    referenced_target_ids = {
        value
        for value in (
            ui["dialogue_target_id"],
            native["last_target_id"],
            *(item["target_id"] for item in critical_acknowledgements),
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

    retained_native = {
        key: deepcopy(value)
        for key, value in native.items()
        if key != "acknowledgements"
    }
    retained_native["acknowledgements"] = critical_acknowledgements

    retained["telemetry"] = {
        key: deepcopy(value)
        for key, value in telemetry.items()
        if key
        not in {
            "capabilities",
            "camera",
            "ui",
            "native_control",
            "squad",
            "nearby_entities",
            "warnings",
        }
    }
    retained["telemetry"].update(
        {
            "capabilities": [],
            "ui": retained_ui,
            "native_control": retained_native,
            "squad": sorted(
                (
                    deepcopy(character)
                    for character in telemetry["squad"]
                    if character["id"] in selected_ids
                ),
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
            "warnings": [],
        }
    )
    return retained


def _critical_acknowledgements(native: JsonObject) -> list[JsonObject]:
    acknowledgements = native["acknowledgements"]
    if not acknowledgements:
        return []

    critical_ids: set[str] = set()
    if native["active_command_id"] is not None:
        critical_ids.add(native["active_command_id"])
    critical_ids.add(max(acknowledgements, key=_acknowledgement_sort_key)["command_id"])
    return sorted(
        (
            deepcopy(item)
            for item in acknowledgements
            if item["command_id"] in critical_ids
        ),
        key=_acknowledgement_sort_key,
    )


def _serialize_budgeted(
    original: JsonObject,
    retained: JsonObject,
    *,
    max_chars: int,
    original_chars: int,
) -> str:
    document = deepcopy(retained)
    document["observation_budget"] = {
        "truncated": True,
        "strategy": "semantic-v1",
        "max_chars": max_chars,
        "original_chars": original_chars,
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


def _skill_contract_mutator(
    skill_name: str,
    skill_specs: list[JsonObject],
) -> Callable[[JsonObject], None]:
    def mutate(candidate: JsonObject) -> None:
        _append_path(candidate, "available_skills", skill_name)
        for skill_spec in skill_specs:
            _append_path(candidate, "skill_specs", skill_spec)

    return mutate


def _outcome_target_ids(outcome: JsonObject) -> set[str]:
    action = outcome.get("action")
    if not isinstance(action, dict) or action.get("kind") != "skill":
        return set()
    arguments = action.get("args")
    if not isinstance(arguments, list):
        return set()
    return {
        str(argument["value"])
        for argument in arguments
        if isinstance(argument, dict)
        and argument.get("name") == "target_id"
        and isinstance(argument.get("value"), str)
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _compact_json(value: Any) -> str:
    return _canonical_json(value)


def _entity_sort_key(entity: JsonObject) -> tuple[str, str]:
    return str(entity["id"]), _canonical_json(entity)


def _nearby_sort_key(entity: JsonObject) -> tuple[float, str, str]:
    distance = entity["distance"]
    return (
        float("inf") if distance is None else float(distance),
        str(entity["id"]),
        _canonical_json(entity),
    )


def _acknowledgement_sort_key(item: JsonObject) -> tuple[int, str]:
    return int(item["acknowledged_at_telemetry_sequence"]), str(item["command_id"])
