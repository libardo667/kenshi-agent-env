"""One semantic interpretation of Kenshi's backwards-named nutrition field."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Protocol

NUTRITION_RESERVE_FULL = 3.0
AUTOMATIC_EATING_RESERVE_THRESHOLD = 2.5
MALNUTRITION_RESERVE_THRESHOLD = 2.0
STARVATION_FAINTING_RESERVE_THRESHOLD = 1.0
MEANINGFUL_NUTRITION_RESERVE_DELTA = 0.1


class NutritionStatus(StrEnum):
    WELL_FED = "well_fed"
    ORDINARY_FOOD_AUTOMATIC_EATING_RANGE = (
        "ordinary_food_automatic_eating_range"
    )
    MALNOURISHED = "malnourished"
    STARVATION_FAINTING_RISK = "starvation_fainting_risk"
    UNKNOWN = "unknown"


class NutritionMember(Protocol):
    id: str
    name: str
    hunger: float | None


def nutrition_status(reserve: float | None) -> NutritionStatus:
    if reserve is None:
        return NutritionStatus.UNKNOWN
    if reserve < STARVATION_FAINTING_RESERVE_THRESHOLD:
        return NutritionStatus.STARVATION_FAINTING_RISK
    if reserve < MALNUTRITION_RESERVE_THRESHOLD:
        return NutritionStatus.MALNOURISHED
    if reserve < AUTOMATIC_EATING_RESERVE_THRESHOLD:
        return NutritionStatus.ORDINARY_FOOD_AUTOMATIC_EATING_RANGE
    return NutritionStatus.WELL_FED


def roster_nutrition_digest(
    roster: Sequence[NutritionMember],
    selected_character_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Derive direction, empirical thresholds, and status for every member."""

    if not roster:
        return {}
    selected = set(selected_character_ids)
    return {
        "scale": {
            "direction": "counts_down_from_full_to_starving",
            "full": NUTRITION_RESERVE_FULL,
            "ordinary_food_automatic_eating_below": (
                AUTOMATIC_EATING_RESERVE_THRESHOLD
            ),
            "edible_ingredient_automatic_eating_below": (
                MALNUTRITION_RESERVE_THRESHOLD
            ),
            "malnutrition_below": MALNUTRITION_RESERVE_THRESHOLD,
            "starvation_fainting_baseline_below": (
                STARVATION_FAINTING_RESERVE_THRESHOLD
            ),
            "starvation_fainting_onset_uses_knockout_point": True,
        },
        "members": [
            {
                "id": character.id,
                "name": character.name,
                "selected": character.id in selected,
                "nutrition_reserve": character.hunger,
                "status": nutrition_status(character.hunger).value,
            }
            for character in roster
        ],
    }


def _model_facing_character_payload(payload: dict[str, Any]) -> dict[str, Any]:
    projected = dict(payload)
    projected["nutrition_reserve"] = projected.pop("hunger")
    return projected


def model_facing_telemetry_payload(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Expose the native reserve under one unambiguous model-facing name."""

    if payload is None:
        return None
    projected = dict(payload)
    if "roster" in projected:
        projected["roster"] = [
            _model_facing_character_payload(character)
            for character in projected["roster"]
        ]
    # Compact observation/advisor digests may include one derived `selected`
    # character. It is resolved from the root primary/selection authority; it
    # is not a second wire field.
    if projected.get("selected") is not None:
        projected["selected"] = _model_facing_character_payload(
            projected["selected"]
        )
    return projected


def nutrition_reserve_change(
    before: float | None,
    after: float | None,
) -> str | None:
    """Name a decision-relevant reserve change without reviving the wire name."""

    if before is None or after is None:
        return None
    if abs(before - after) < MEANINGFUL_NUTRITION_RESERVE_DELTA:
        return None
    return f"nutrition reserve: {before:.2f} -> {after:.2f}"
