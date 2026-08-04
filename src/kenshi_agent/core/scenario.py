"""Runtime-visible proof types for reproducible scenario identity."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import StrictModel
from .telemetry import ScenarioIdentity

MANAGED_SAVE_NAME: Literal["KenshiAgentScenario"] = "KenshiAgentScenario"


class ScenarioFixtureFile(StrictModel):
    path: str = Field(pattern=r"^[^/\\].*")
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScenarioFixtureManifest(StrictModel):
    schema_version: Literal[1] = 1
    scenario: ScenarioIdentity
    managed_save_name: Literal["KenshiAgentScenario"] = MANAGED_SAVE_NAME
    captured_at: datetime
    fixture_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: list[ScenarioFixtureFile] = Field(min_length=1)


class ScenarioObservedState(StrictModel):
    selected_character_id: str = Field(min_length=1)
    indoors: bool
    in_combat: bool
    money: int
    party_size: int = Field(ge=1)
    minute_of_day: int = Field(ge=0, lt=24 * 60)


class ScenarioAttestation(StrictModel):
    schema_version: Literal[1] = 1
    scenario: ScenarioIdentity
    fixture_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    managed_save_name: Literal["KenshiAgentScenario"] = MANAGED_SAVE_NAME
    identity_session_id: str = Field(min_length=1)
    loaded_sequence: int = Field(ge=0)
    verified_at: datetime
    observed: ScenarioObservedState
