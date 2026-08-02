from __future__ import annotations

from dataclasses import dataclass

import pytest

from kenshi_agent.nutrition import (
    NutritionStatus,
    model_facing_telemetry_payload,
    nutrition_reserve_change,
    nutrition_status,
    squad_nutrition_digest,
)


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    selected: bool
    hunger: float | None


@pytest.mark.parametrize(
    ("reserve", "expected"),
    [
        (None, NutritionStatus.UNKNOWN),
        (0.9, NutritionStatus.STARVATION_FAINTING_RISK),
        (1.0, NutritionStatus.MALNOURISHED),
        (1.9, NutritionStatus.MALNOURISHED),
        (2.0, NutritionStatus.ORDINARY_FOOD_AUTOMATIC_EATING_RANGE),
        (2.4, NutritionStatus.ORDINARY_FOOD_AUTOMATIC_EATING_RANGE),
        (2.5, NutritionStatus.WELL_FED),
        (3.0, NutritionStatus.WELL_FED),
    ],
)
def test_nutrition_status_uses_the_exact_game_derived_boundaries(
    reserve: float | None,
    expected: NutritionStatus,
) -> None:
    assert nutrition_status(reserve) is expected


def test_squad_digest_covers_every_member_and_preserves_unknown() -> None:
    assert squad_nutrition_digest([]) == {}

    digest = squad_nutrition_digest(
        [
            Member("hep", "Hep", True, 2.775886),
            Member("unknown", "Unknown", False, None),
        ]
    )

    assert digest == {
        "scale": {
            "direction": "counts_down_from_full_to_starving",
            "full": 3.0,
            "ordinary_food_automatic_eating_below": 2.5,
            "edible_ingredient_automatic_eating_below": 2.0,
            "malnutrition_below": 2.0,
            "starvation_fainting_baseline_below": 1.0,
            "starvation_fainting_onset_uses_knockout_point": True,
        },
        "members": [
            {
                "id": "hep",
                "name": "Hep",
                "selected": True,
                "nutrition_reserve": 2.775886,
                "status": "well_fed",
            },
            {
                "id": "unknown",
                "name": "Unknown",
                "selected": False,
                "nutrition_reserve": None,
                "status": "unknown",
            },
        ],
    }


def test_model_facing_telemetry_replaces_both_native_character_shapes() -> None:
    source = {
        "sequence": 8,
        "squad": [{"id": "hep", "hunger": 2.75, "alive": True}],
        "selected": {"id": "hep", "hunger": 2.75, "alive": True},
    }

    projected = model_facing_telemetry_payload(source)

    assert projected == {
        "sequence": 8,
        "squad": [
            {"id": "hep", "nutrition_reserve": 2.75, "alive": True}
        ],
        "selected": {
            "id": "hep",
            "nutrition_reserve": 2.75,
            "alive": True,
        },
    }
    assert source["squad"][0]["hunger"] == 2.75
    assert source["selected"]["hunger"] == 2.75
    assert model_facing_telemetry_payload(None) is None
    assert model_facing_telemetry_payload({"selected": None}) == {
        "selected": None
    }


def test_nutrition_change_is_bounded_and_uses_only_the_semantic_name() -> None:
    assert nutrition_reserve_change(None, 2.5) is None
    assert nutrition_reserve_change(2.5, None) is None
    assert nutrition_reserve_change(2.5, 2.45) is None
    assert nutrition_reserve_change(0.1, 0.0) == (
        "nutrition reserve: 0.10 -> 0.00"
    )
    assert nutrition_reserve_change(2.7, 2.6) == (
        "nutrition reserve: 2.70 -> 2.60"
    )
    assert nutrition_reserve_change(2.6, 2.7) == (
        "nutrition reserve: 2.60 -> 2.70"
    )
