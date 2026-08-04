"""Runtime verification for an already prepared scenario proof."""

from __future__ import annotations

from datetime import UTC, datetime

from .core.scenario import (
    ScenarioAttestation,
    ScenarioFixtureManifest,
    ScenarioObservedState,
)
from .core.telemetry import ScenarioIdentity, TelemetrySnapshot

BROKE_MAX_CATS = 1_000
FUNDED_MIN_CATS = 10_000
DAY_START_MINUTE = 6 * 60
NIGHT_START_MINUTE = 20 * 60


class ScenarioFixtureError(RuntimeError):
    """A fixture or its current live evidence cannot be trusted."""


def verify_scenario_snapshot(
    snapshot: TelemetrySnapshot,
    scenario: ScenarioIdentity,
) -> ScenarioObservedState:
    """Prove every scenario axis from one fresh loaded paused observation."""

    if not snapshot.game.loaded or snapshot.game.paused is not True:
        raise ScenarioFixtureError(
            "Scenario verification requires a loaded, explicitly paused game."
        )
    if not snapshot.identity_session_id:
        raise ScenarioFixtureError(
            "Scenario verification requires a native identity session."
        )
    required_capabilities = {
        "game.money",
        "game.time",
        "squad.basic",
        "squad.indoors",
        "squad.health",
    }
    missing = required_capabilities - set(snapshot.capabilities)
    if missing:
        raise ScenarioFixtureError(
            "Scenario verification is missing capabilities: "
            + ", ".join(sorted(missing))
        )

    selected = [character for character in snapshot.squad if character.selected]
    if len(selected) != 1:
        raise ScenarioFixtureError(
            "Scenario verification requires exactly one selected character."
        )
    character = selected[0]
    if character.indoors is None:
        raise ScenarioFixtureError(
            "squad.indoors is unavailable for the selected character."
        )
    if character.in_combat is None:
        raise ScenarioFixtureError(
            "squad.health cannot prove selected-character combat state."
        )
    if snapshot.game.money is None:
        raise ScenarioFixtureError("game.money is unavailable.")
    if snapshot.game.elapsed_minutes is None:
        raise ScenarioFixtureError("game.time is unavailable.")

    minute_of_day = int(snapshot.game.elapsed_minutes) % (24 * 60)
    observed = ScenarioObservedState(
        selected_character_id=character.id,
        indoors=character.indoors,
        in_combat=character.in_combat,
        money=snapshot.game.money,
        party_size=len(snapshot.squad),
        minute_of_day=minute_of_day,
    )

    expected_indoors = scenario.environment == "indoor"
    if observed.indoors != expected_indoors:
        raise ScenarioFixtureError(
            f"Scenario environment is {scenario.environment!r}, but selected.indoors "
            f"is {observed.indoors!r}."
        )
    expected_combat = scenario.danger == "hostile"
    if observed.in_combat != expected_combat:
        raise ScenarioFixtureError(
            f"Scenario danger is {scenario.danger!r}, but selected.in_combat is "
            f"{observed.in_combat!r}."
        )
    if scenario.economy == "broke" and observed.money > BROKE_MAX_CATS:
        raise ScenarioFixtureError(
            f"Scenario economy is 'broke', but money {observed.money} exceeds "
            f"{BROKE_MAX_CATS} cats."
        )
    if scenario.economy == "funded" and observed.money < FUNDED_MIN_CATS:
        raise ScenarioFixtureError(
            f"Scenario economy is 'funded', but money {observed.money} is below "
            f"{FUNDED_MIN_CATS} cats."
        )
    expected_solo = scenario.party == "solo"
    if (observed.party_size == 1) != expected_solo:
        raise ScenarioFixtureError(
            f"Scenario party is {scenario.party!r}, but the squad has "
            f"{observed.party_size} members."
        )
    is_day = DAY_START_MINUTE <= minute_of_day < NIGHT_START_MINUTE
    if (scenario.time_of_day == "day") != is_day:
        raise ScenarioFixtureError(
            f"Scenario time_of_day is {scenario.time_of_day!r}, but the observed "
            f"minute of day is {minute_of_day}."
        )
    return observed


def attest_loaded_scenario(
    manifest: ScenarioFixtureManifest,
    snapshot: TelemetrySnapshot,
    *,
    verified_at: datetime | None = None,
) -> ScenarioAttestation:
    observed = verify_scenario_snapshot(snapshot, manifest.scenario)
    assert snapshot.identity_session_id is not None
    return ScenarioAttestation(
        scenario=manifest.scenario,
        fixture_digest=manifest.fixture_digest,
        identity_session_id=snapshot.identity_session_id,
        loaded_sequence=snapshot.sequence,
        verified_at=verified_at or datetime.now(UTC),
        observed=observed,
    )


def validate_current_scenario(
    attestation: ScenarioAttestation,
    manifest: ScenarioFixtureManifest,
    snapshot: TelemetrySnapshot,
) -> None:
    if attestation.scenario != manifest.scenario:
        raise ScenarioFixtureError(
            "Current scenario attestation does not match the fixture identity."
        )
    if attestation.fixture_digest != manifest.fixture_digest:
        raise ScenarioFixtureError(
            "Current scenario attestation has a different fixture digest."
        )
    if snapshot.identity_session_id != attestation.identity_session_id:
        raise ScenarioFixtureError(
            "Current telemetry belongs to a different native session."
        )
    if snapshot.sequence < attestation.loaded_sequence:
        raise ScenarioFixtureError(
            "Current telemetry sequence is older than the scenario attestation."
        )
    verify_scenario_snapshot(snapshot, manifest.scenario)


def validate_attested_snapshot(
    attestation: ScenarioAttestation,
    snapshot: TelemetrySnapshot,
) -> None:
    """Revalidate an already repository-verified attestation against live state."""

    if snapshot.identity_session_id != attestation.identity_session_id:
        raise ScenarioFixtureError(
            "Current telemetry belongs to a different native session."
        )
    if snapshot.sequence < attestation.loaded_sequence:
        raise ScenarioFixtureError(
            "Current telemetry sequence is older than the scenario attestation."
        )
    verify_scenario_snapshot(snapshot, attestation.scenario)
