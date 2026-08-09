from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.core.scenario import MANAGED_SAVE_NAME
from kenshi_agent.core.telemetry import (
    CharacterState,
    GameState,
    ScenarioIdentity,
    TelemetrySnapshot,
)
from kenshi_agent.scenario_validation import (
    ScenarioFixtureError,
    attest_loaded_scenario,
    validate_current_scenario,
    verify_scenario_snapshot,
)
from kenshi_agent.tooling.scenario_fixtures import (
    capture_scenario_fixture,
    load_scenario_fixture,
    restore_scenario_fixture,
)


def _scenario(
    scenario_id: str = "hub-outdoor-safe-broke-solo-day",
    save_id: str = "hub-start-v1",
    **updates: str,
) -> ScenarioIdentity:
    values = {
        "scenario_id": scenario_id,
        "save_id": save_id,
        "environment": "outdoor",
        "danger": "safe",
        "economy": "broke",
        "party": "solo",
        "time_of_day": "day",
    }
    values.update(updates)
    return ScenarioIdentity.model_validate(values)


def _save(path: Path, marker: bytes) -> Path:
    path.mkdir(parents=True)
    (path / "quick.save").write_bytes(b"quick:" + marker)
    platoon = path / "platoon"
    platoon.mkdir()
    (platoon / "Nameless_0.platoon").write_bytes(b"platoon:" + marker)
    return path


def _snapshot(
    *,
    session_id: str = "native-session-a",
    sequence: int = 40,
    money: int | None = 20,
    elapsed_minutes: float | None = 12 * 60,
    indoors: bool | None = False,
    in_combat: bool | None = False,
    roster_size: int = 1,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=sequence,
        captured_at=datetime.now(UTC),
        source="kenshilib-plugin",
        identity_session_id=session_id,
        capabilities=[
            "game.money",
            "game.time",
            "roster.basic",
            "roster.indoors",
            "roster.health",
        ],
        game=GameState(
            loaded=True,
            paused=True,
            money=money,
            elapsed_minutes=elapsed_minutes,
        ),
        primary_character_id="character-0" if roster_size else None,
        selected_character_ids=["character-0"] if roster_size else [],
        roster=[
            CharacterState(
                id=f"character-{index}",
                name=f"Character {index}",
                indoors=indoors if index == 0 else False,
                in_combat=in_combat if index == 0 else False,
            )
            for index in range(roster_size)
        ],
    )


def test_fixture_capture_is_reproducible_and_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    source = _save(tmp_path / "save" / "autosave1", b"source")
    store = tmp_path / "scenario-store"

    manifest = capture_scenario_fixture(source, store, _scenario())
    loaded = load_scenario_fixture(store, manifest.scenario.scenario_id)

    assert loaded == manifest
    fixture_file = (
        store
        / "fixtures"
        / manifest.scenario.scenario_id
        / "save"
        / "quick.save"
    )
    fixture_file.write_bytes(b"tampered")

    with pytest.raises(ScenarioFixtureError, match="digest"):
        load_scenario_fixture(store, manifest.scenario.scenario_id)


def test_capture_refuses_missing_quick_save_and_existing_identity(
    tmp_path: Path,
) -> None:
    store = tmp_path / "scenario-store"
    invalid = tmp_path / "not-a-save"
    invalid.mkdir()

    with pytest.raises(ScenarioFixtureError, match="quick.save"):
        capture_scenario_fixture(invalid, store, _scenario())

    source = _save(tmp_path / "save" / "autosave1", b"source")
    capture_scenario_fixture(source, store, _scenario())
    with pytest.raises(ScenarioFixtureError, match="already exists"):
        capture_scenario_fixture(source, store, _scenario())


def test_fixture_store_prevents_save_identity_relabeling(
    tmp_path: Path,
) -> None:
    store = tmp_path / "scenario-store"
    first = _save(tmp_path / "first", b"first")
    second = _save(tmp_path / "second", b"second")
    capture_scenario_fixture(first, store, _scenario())

    with pytest.raises(ScenarioFixtureError, match="save_id"):
        capture_scenario_fixture(
            second,
            store,
            _scenario(
                scenario_id="different-situation",
                save_id="hub-start-v1",
            ),
        )

    with pytest.raises(ScenarioFixtureError, match="same snapshot"):
        capture_scenario_fixture(
            first,
            store,
            _scenario(
                scenario_id="relabeled-situation",
                save_id="relabeled-save-v1",
            ),
        )


def test_fixture_lookup_rejects_path_shaped_identity(
    tmp_path: Path,
) -> None:
    with pytest.raises(ScenarioFixtureError, match="scenario ID"):
        load_scenario_fixture(tmp_path, "../outside")


def test_restore_never_overwrites_an_unowned_save_slot(tmp_path: Path) -> None:
    source = _save(tmp_path / "source", b"fixture")
    store = tmp_path / "scenario-store"
    save_root = tmp_path / "live-saves"
    capture_scenario_fixture(source, store, _scenario())
    _save(save_root / MANAGED_SAVE_NAME, b"user-owned")

    with pytest.raises(ScenarioFixtureError, match="not owned"):
        restore_scenario_fixture(
            store,
            _scenario().scenario_id,
            save_root,
        )

    assert (save_root / MANAGED_SAVE_NAME / "quick.save").read_bytes() == (
        b"quick:user-owned"
    )


def test_restore_is_exact_idempotent_and_recovers_prior_managed_state(
    tmp_path: Path,
) -> None:
    store = tmp_path / "scenario-store"
    save_root = tmp_path / "live-saves"
    first_source = _save(tmp_path / "first", b"first")
    second_source = _save(tmp_path / "second", b"second")
    first = _scenario()
    second = _scenario(
        scenario_id="squin-indoor-hostile-funded-squad-night",
        save_id="squin-captured-v1",
        environment="indoor",
        danger="hostile",
        economy="funded",
        party="squad",
        time_of_day="night",
    )
    capture_scenario_fixture(first_source, store, first)
    capture_scenario_fixture(second_source, store, second)

    initial = restore_scenario_fixture(store, first.scenario_id, save_root)
    repeated = restore_scenario_fixture(store, first.scenario_id, save_root)
    assert initial.changed
    assert not repeated.changed
    assert repeated.recovery_path is None

    managed_quick = save_root / MANAGED_SAVE_NAME / "quick.save"
    managed_quick.write_bytes(b"runtime-mutated")
    replaced = restore_scenario_fixture(store, second.scenario_id, save_root)

    assert replaced.changed
    assert replaced.recovery_path is not None
    assert (replaced.recovery_path / "quick.save").read_bytes() == b"runtime-mutated"
    assert managed_quick.read_bytes() == b"quick:second"


def test_snapshot_verification_covers_all_declared_axes() -> None:
    scenario = _scenario()
    observed = verify_scenario_snapshot(_snapshot(), scenario)

    assert observed.indoors is False
    assert observed.in_combat is False
    assert observed.money == 20
    assert observed.party_size == 1
    assert observed.minute_of_day == 12 * 60

    hostile = _scenario(danger="hostile")
    with pytest.raises(ScenarioFixtureError, match="danger"):
        verify_scenario_snapshot(_snapshot(), hostile)

    funded = _scenario(economy="funded")
    with pytest.raises(ScenarioFixtureError, match="economy"):
        verify_scenario_snapshot(_snapshot(money=5_000), funded)

    night = _scenario(time_of_day="night")
    with pytest.raises(ScenarioFixtureError, match="time_of_day"):
        verify_scenario_snapshot(_snapshot(), night)


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        (_snapshot(money=None), "game.money"),
        (_snapshot(elapsed_minutes=None), "game.time"),
        (_snapshot(indoors=None), "roster.indoors"),
        (_snapshot(in_combat=None), "roster.health"),
        (_snapshot(roster_size=0), "selected"),
    ],
)
def test_snapshot_verification_never_turns_missing_evidence_into_a_match(
    snapshot: TelemetrySnapshot,
    message: str,
) -> None:
    with pytest.raises(ScenarioFixtureError, match=message):
        verify_scenario_snapshot(snapshot, _scenario())


def test_attestation_is_bound_to_fixture_session_and_current_state(
    tmp_path: Path,
) -> None:
    source = _save(tmp_path / "source", b"fixture")
    store = tmp_path / "scenario-store"
    manifest = capture_scenario_fixture(source, store, _scenario())
    snapshot = _snapshot()
    attestation = attest_loaded_scenario(manifest, snapshot)

    validate_current_scenario(attestation, manifest, _snapshot(sequence=41))

    with pytest.raises(ScenarioFixtureError, match="native session"):
        validate_current_scenario(
            attestation,
            manifest,
            _snapshot(session_id="native-session-b", sequence=41),
        )

    with pytest.raises(ScenarioFixtureError, match="older"):
        validate_current_scenario(
            attestation,
            manifest,
            _snapshot(sequence=39),
        )

    altered = manifest.model_copy(
        update={"fixture_digest": "f" * 64}
    )
    with pytest.raises(ScenarioFixtureError, match="fixture digest"):
        validate_current_scenario(attestation, altered, _snapshot(sequence=41))
