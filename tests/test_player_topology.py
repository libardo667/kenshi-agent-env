"""Clean-break player roster, platoon, active, primary, and selection authority."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.core.telemetry import TelemetrySnapshot

FIXTURES = Path(__file__).parent / "fixtures" / "native_telemetry"
ROOT = Path(__file__).parents[1]


def topology_fixture() -> TelemetrySnapshot:
    return TelemetrySnapshot.model_validate_json(
        (FIXTURES / "valid_player_topology.json").read_bytes()
    )


def test_shared_native_fixture_distinguishes_every_player_topology_role() -> None:
    snapshot = topology_fixture()

    assert [character.id for character in snapshot.roster] == [
        "character-alpha",
        "character-beta-primary",
        "character-beta-second",
    ]
    assert snapshot.active_platoon_id == "platoon-beta"
    assert snapshot.primary_character_id == "character-beta-primary"
    assert snapshot.primary_character() is snapshot.roster[1]
    assert snapshot.primary_character() is not snapshot.roster[0]
    assert [character.id for character in snapshot.selected_characters()] == [
        "character-beta-primary",
        "character-beta-second",
    ]
    assert snapshot.selected_character_ids_complete is True
    assert snapshot.roster_complete is True
    assert snapshot.platoons_complete is True
    assert {platoon.id: platoon.member_ids for platoon in snapshot.platoons} == {
        "platoon-alpha": ["character-alpha"],
        "platoon-beta": ["character-beta-primary", "character-beta-second"],
    }
    assert all("selected" not in row.model_fields_set for row in snapshot.roster)
    assert snapshot.roster[0].work is not None
    assert snapshot.roster[0].work.ordinary_orders.known_total == 1
    assert snapshot.roster[0].work.jobs.known_total == 0
    assert snapshot.roster[0].work.current_activity is not None
    assert snapshot.roster[0].work.current_activity.position is None
    assert "selected_character_id" not in type(snapshot.ui).model_fields
    assert "selected_character_ids" not in type(snapshot.ui).model_fields


def test_superseded_squad_and_ui_selection_authority_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetrySnapshot.model_validate_json(
            (FIXTURES / "invalid_superseded_squad_authority.json").read_bytes()
        )


def test_planner_and_manual_dispatch_use_root_topology_authority() -> None:
    prompt = (ROOT / "prompts" / "planner_system.md").read_text(encoding="utf-8")
    dispatch = (ROOT / "scripts" / "dispatch_native_command.py").read_text(
        encoding="utf-8"
    )

    assert "`telemetry.roster`" in prompt
    assert "telemetry.squad" not in prompt
    assert "snapshot.selected_character_ids" in dispatch
    assert "snapshot.ui.selected_character_ids" not in dispatch


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"active_platoon_id": "platoon-missing"}, "active_platoon_id"),
        ({"primary_character_id": "character-alpha"}, "must also appear"),
        ({"selected_character_ids": ["character-missing"]}, "current roster"),
    ],
)
def test_topology_rejects_unresolved_active_primary_and_selection(
    update: dict[str, object],
    message: str,
) -> None:
    payload = topology_fixture().model_dump(mode="json") | update

    with pytest.raises(ValidationError, match=message):
        TelemetrySnapshot.model_validate(payload)


def test_complete_topology_requires_one_platoon_owner_per_roster_member() -> None:
    payload = topology_fixture().model_dump(mode="json")
    payload["roster"][0]["platoon_id"] = None

    with pytest.raises(ValidationError, match="every roster member"):
        TelemetrySnapshot.model_validate(payload)


def test_roster_and_platoon_membership_must_agree_bidirectionally() -> None:
    payload = topology_fixture().model_dump(mode="json")
    payload["platoons"][1]["member_ids"] = ["character-beta-primary"]

    with pytest.raises(ValidationError, match="membership disagree"):
        TelemetrySnapshot.model_validate(payload)
