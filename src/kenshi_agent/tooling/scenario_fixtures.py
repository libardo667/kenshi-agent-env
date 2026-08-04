from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from ..core.base import StrictModel
from ..core.scenario import (
    MANAGED_SAVE_NAME,
    ScenarioAttestation,
    ScenarioFixtureFile,
    ScenarioFixtureManifest,
)
from ..core.telemetry import ScenarioIdentity
from ..scenario_validation import (
    ScenarioFixtureError,
)


class _ManagedSlotOwnership(StrictModel):
    schema_version: Literal[1] = 1
    managed_save_name: Literal["KenshiAgentScenario"] = MANAGED_SAVE_NAME
    scenario_id: str
    fixture_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    restored_at: datetime


@dataclass(frozen=True)
class ScenarioRestoreResult:
    scenario: ScenarioIdentity
    managed_save_path: Path
    changed: bool
    recovery_path: Path | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fixture_files(save_dir: Path) -> list[ScenarioFixtureFile]:
    entries: list[ScenarioFixtureFile] = []
    for path in sorted(save_dir.rglob("*")):
        if path.is_symlink():
            raise ScenarioFixtureError(
                f"Scenario save contains an unsupported symbolic link: {path}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise ScenarioFixtureError(
                f"Scenario save contains an unsupported filesystem entry: {path}"
            )
        relative = path.relative_to(save_dir).as_posix()
        entries.append(
            ScenarioFixtureFile(
                path=relative,
                size=path.stat().st_size,
                sha256=_sha256(path),
            )
        )
    if not entries:
        raise ScenarioFixtureError("Scenario save contains no files.")
    return entries


def _tree_digest(files: list[ScenarioFixtureFile]) -> str:
    digest = hashlib.sha256()
    for entry in files:
        digest.update(entry.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry.size).encode("ascii"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(payload + "\n", encoding="utf-8")
    temporary.replace(path)


def _fixture_dir(store: Path, scenario_id: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", scenario_id) is None:
        raise ScenarioFixtureError(
            f"Invalid scenario ID {scenario_id!r}; path-shaped identities are forbidden."
        )
    return store / "fixtures" / scenario_id


def _existing_manifests(store: Path) -> list[ScenarioFixtureManifest]:
    fixtures_root = store / "fixtures"
    if not fixtures_root.is_dir():
        return []
    return [
        load_scenario_fixture(store, path.name)
        for path in sorted(fixtures_root.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]


def capture_scenario_fixture(
    source_save: Path,
    store: Path,
    scenario: ScenarioIdentity,
    *,
    captured_at: datetime | None = None,
) -> ScenarioFixtureManifest:
    """Copy one closed Kenshi save into an immutable hashed fixture."""

    source = source_save.expanduser().resolve()
    if not source.is_dir():
        raise ScenarioFixtureError(f"Scenario source save does not exist: {source}")
    if not (source / "quick.save").is_file():
        raise ScenarioFixtureError(
            f"Scenario source is not a complete Kenshi save; quick.save is missing: {source}"
        )

    destination = _fixture_dir(store, scenario.scenario_id)
    if destination.exists():
        raise ScenarioFixtureError(
            f"Scenario fixture {scenario.scenario_id!r} already exists."
        )
    fixtures_root = destination.parent
    fixtures_root.mkdir(parents=True, exist_ok=True)
    temporary = fixtures_root / f".capture-{uuid4().hex}"
    try:
        copied_save = temporary / "save"
        shutil.copytree(source, copied_save)
        files = _fixture_files(copied_save)
        fixture_digest = _tree_digest(files)
        for existing in _existing_manifests(store):
            if existing.scenario.save_id == scenario.save_id:
                raise ScenarioFixtureError(
                    f"save_id {scenario.save_id!r} already belongs to scenario "
                    f"{existing.scenario.scenario_id!r}; one exact snapshot cannot "
                    "be relabeled as a different situation."
                )
            if existing.fixture_digest == fixture_digest:
                raise ScenarioFixtureError(
                    f"Scenario {existing.scenario.scenario_id!r} already contains "
                    "the same snapshot bytes under a different save_id."
                )
        manifest = ScenarioFixtureManifest(
            scenario=scenario,
            captured_at=captured_at or datetime.now(UTC),
            fixture_digest=fixture_digest,
            files=files,
        )
        (temporary / "manifest.json").write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def load_scenario_fixture(
    store: Path,
    scenario_id: str,
) -> ScenarioFixtureManifest:
    fixture_dir = _fixture_dir(store, scenario_id)
    manifest_path = fixture_dir / "manifest.json"
    save_dir = fixture_dir / "save"
    try:
        manifest = ScenarioFixtureManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError) as exc:
        raise ScenarioFixtureError(
            f"Scenario fixture {scenario_id!r} has no valid manifest."
        ) from exc
    if manifest.scenario.scenario_id != scenario_id:
        raise ScenarioFixtureError(
            f"Scenario manifest identity does not match directory {scenario_id!r}."
        )
    if not (save_dir / "quick.save").is_file():
        raise ScenarioFixtureError(
            f"Scenario fixture {scenario_id!r} is missing quick.save."
        )
    actual_files = _fixture_files(save_dir)
    actual_digest = _tree_digest(actual_files)
    if actual_files != manifest.files or actual_digest != manifest.fixture_digest:
        raise ScenarioFixtureError(
            f"Scenario fixture {scenario_id!r} digest does not match its manifest."
        )
    return manifest


def _ownership_path(store: Path) -> Path:
    return store / "managed_slot.json"


def _load_ownership(store: Path) -> _ManagedSlotOwnership | None:
    path = _ownership_path(store)
    if not path.exists():
        return None
    try:
        return _ManagedSlotOwnership.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise ScenarioFixtureError(
            "The managed scenario slot ownership record is invalid."
        ) from exc


def _save_matches_manifest(
    save_dir: Path,
    manifest: ScenarioFixtureManifest,
) -> bool:
    try:
        files = _fixture_files(save_dir)
    except ScenarioFixtureError:
        return False
    return files == manifest.files and _tree_digest(files) == manifest.fixture_digest


def restore_scenario_fixture(
    store: Path,
    scenario_id: str,
    save_root: Path,
    *,
    restored_at: datetime | None = None,
) -> ScenarioRestoreResult:
    """Restore into the reserved slot without ever overwriting an unowned save."""

    manifest = load_scenario_fixture(store, scenario_id)
    root = save_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    managed = root / MANAGED_SAVE_NAME
    ownership = _load_ownership(store)
    now = restored_at or datetime.now(UTC)
    ownership_record = _ManagedSlotOwnership(
        scenario_id=scenario_id,
        fixture_digest=manifest.fixture_digest,
        restored_at=now,
    )
    if managed.exists() and ownership is None:
        raise ScenarioFixtureError(
            f"{managed} exists but is not owned by this scenario store; refusing "
            "to overwrite it."
        )
    if managed.exists() and ownership is not None and _save_matches_manifest(
        managed, manifest
    ):
        if (
            ownership.scenario_id != scenario_id
            or ownership.fixture_digest != manifest.fixture_digest
        ):
            _write_json_atomic(
                _ownership_path(store),
                ownership_record.model_dump_json(indent=2),
            )
        return ScenarioRestoreResult(
            scenario=manifest.scenario,
            managed_save_path=managed,
            changed=False,
            recovery_path=None,
        )

    temporary = root / f".{MANAGED_SAVE_NAME}.restore-{uuid4().hex}"
    recovery: Path | None = None
    try:
        shutil.copytree(_fixture_dir(store, scenario_id) / "save", temporary)
        if not _save_matches_manifest(temporary, manifest):
            raise ScenarioFixtureError(
                "The restored temporary save did not match the fixture digest."
            )
        if managed.exists():
            recovery_root = store / "recovery"
            recovery_root.mkdir(parents=True, exist_ok=True)
            stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
            recovery = recovery_root / f"{stamp}-{uuid4().hex[:8]}"
            managed.replace(recovery)
        temporary.replace(managed)
        store.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            _ownership_path(store),
            ownership_record.model_dump_json(indent=2),
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if recovery is not None and recovery.exists():
            if managed.exists():
                shutil.rmtree(managed)
            recovery.replace(managed)
            recovery = None
        elif managed.exists() and _save_matches_manifest(managed, manifest):
            shutil.rmtree(managed)
        raise

    return ScenarioRestoreResult(
        scenario=manifest.scenario,
        managed_save_path=managed,
        changed=True,
        recovery_path=recovery,
    )


def verify_staged_scenario(
    store: Path,
    scenario_id: str,
    save_root: Path,
) -> ScenarioFixtureManifest:
    manifest = load_scenario_fixture(store, scenario_id)
    managed = save_root.expanduser().resolve() / MANAGED_SAVE_NAME
    ownership = _load_ownership(store)
    if ownership is None:
        raise ScenarioFixtureError(
            "No project-owned scenario has been restored into the managed save slot."
        )
    if (
        ownership.scenario_id != scenario_id
        or ownership.fixture_digest != manifest.fixture_digest
    ):
        raise ScenarioFixtureError(
            f"The managed save slot is not staged for scenario {scenario_id!r}."
        )
    if not managed.is_dir() or not _save_matches_manifest(managed, manifest):
        raise ScenarioFixtureError(
            f"The managed save slot for scenario {scenario_id!r} has changed; "
            "restore the fixture again before launch."
        )
    return manifest


def write_scenario_attestation(
    path: Path,
    attestation: ScenarioAttestation,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, attestation.model_dump_json(indent=2))


def load_scenario_attestation(path: Path) -> ScenarioAttestation:
    try:
        return ScenarioAttestation.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError) as exc:
        raise ScenarioFixtureError(
            "No valid loaded-scenario attestation is available."
        ) from exc


def load_verified_scenario_attestation(path: Path) -> ScenarioAttestation:
    """Load one attestation only while its exact fixture still verifies."""

    attestation = load_scenario_attestation(path)
    manifest = load_scenario_fixture(path.parent, attestation.scenario.scenario_id)
    if attestation.scenario != manifest.scenario:
        raise ScenarioFixtureError(
            "Current scenario attestation does not match the fixture identity."
        )
    if attestation.fixture_digest != manifest.fixture_digest:
        raise ScenarioFixtureError(
            "Current scenario attestation has a different fixture digest."
        )
    return attestation


def current_attestation_path(store: Path) -> Path:
    return store / "current_attestation.json"
