from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent.core.telemetry import (
    CharacterState,
    GameState,
    TelemetrySnapshot,
)
from kenshi_agent.tooling.authored_starts import (
    AuthoredGameStart,
    AuthoredStartsBundle,
    AuthoredStartsError,
    AuthoredStartsManifest,
    BundledModIdentity,
    install_authored_starts,
    load_authored_starts_bundle,
    resolve_authored_game_start,
    verify_authored_game_start_snapshot,
    verify_installed_authored_starts,
)


def _bundle(payload: bytes = b"exact authored scenario mod") -> AuthoredStartsBundle:
    return AuthoredStartsBundle(
        manifest=AuthoredStartsManifest(
            mod=BundledModIdentity(
                directory="KenshiAgentScenarios",
                filename="KenshiAgentScenarios.mod",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
            starts=[
                AuthoredGameStart(
                    start_id="kae-01-broke-solo",
                    label="KAE 01 - Broke Solo",
                    money=20,
                    party_size=1,
                ),
                AuthoredGameStart(
                    start_id="kae-03-broke-pair",
                    label="KAE 03 - Broke Pair",
                    money=20,
                    party_size=2,
                ),
            ],
        ),
        payload=payload,
    )


def _install_tree(root: Path, bundle: AuthoredStartsBundle) -> Path:
    target = (
        root
        / "mods"
        / bundle.manifest.mod.directory
        / bundle.manifest.mod.filename
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(bundle.payload)
    data = root / "data"
    data.mkdir()
    (data / "mods.cfg").write_text(
        "KenshiAgentTelemetry.mod\r\nKenshiAgentScenarios.mod\r\n",
        encoding="utf-8",
        newline="",
    )
    return target


def _snapshot(*, money: int = 20, party_size: int = 1) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        sequence=12,
        captured_at=datetime.now(UTC),
        identity_session_id="native-session-start",
        capabilities=["game.money", "squad.basic"],
        game=GameState(loaded=True, paused=True, money=money),
        squad=[
            CharacterState(
                id=f"entity-{index}",
                name=f"Wanderer {index}",
                selected=index == 0,
            )
            for index in range(party_size)
        ],
    )


def test_checked_in_authored_start_bundle_matches_catalog_and_bytes() -> None:
    bundle = load_authored_starts_bundle()

    assert bundle.manifest.mod.size_bytes == len(bundle.payload)
    assert bundle.manifest.mod.sha256 == hashlib.sha256(bundle.payload).hexdigest()
    assert [start.start_id for start in bundle.manifest.starts] == [
        "kae-01-broke-solo",
        "kae-02-funded-solo",
        "kae-03-broke-pair",
        "kae-04-funded-pair",
    ]
    assert [(start.money, start.party_size) for start in bundle.manifest.starts] == [
        (20, 1),
        (20_000, 1),
        (20, 2),
        (20_000, 2),
    ]


def test_installed_start_mod_requires_exact_bytes_and_one_enabled_entry(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    target = _install_tree(tmp_path, bundle)

    assert verify_installed_authored_starts(bundle, tmp_path) == target

    target.write_bytes(b"X" + bundle.payload[1:])
    with pytest.raises(AuthoredStartsError, match="digest"):
        verify_installed_authored_starts(bundle, tmp_path)

    target.write_bytes(bundle.payload)
    (tmp_path / "data" / "mods.cfg").write_text(
        "KenshiAgentTelemetry.mod\r\n",
        encoding="utf-8",
        newline="",
    )
    with pytest.raises(AuthoredStartsError, match="enabled exactly once"):
        verify_installed_authored_starts(bundle, tmp_path)


def test_authored_start_install_is_idempotent_and_preserves_mod_order(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    data = tmp_path / "data"
    data.mkdir(parents=True)
    mods_cfg = data / "mods.cfg"
    mods_cfg.write_text(
        "KenshiAgentTelemetry.mod\r\n",
        encoding="utf-8",
        newline="",
    )
    observed_at = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)

    first = install_authored_starts(bundle, tmp_path, installed_at=observed_at)
    second = install_authored_starts(bundle, tmp_path, installed_at=observed_at)

    assert first.mod_changed is True
    assert first.enabled_changed is True
    assert first.backup_path is not None and first.backup_path.read_bytes() == (
        b"KenshiAgentTelemetry.mod\r\n"
    )
    assert second.mod_changed is False
    assert second.enabled_changed is False
    assert second.backup_path is None
    assert mods_cfg.read_bytes() == (
        b"KenshiAgentTelemetry.mod\r\nKenshiAgentScenarios.mod\r\n"
    )
    verify_installed_authored_starts(bundle, tmp_path)


def test_authored_start_install_preserves_existing_mod_config_bytes(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    data = tmp_path / "data"
    data.mkdir(parents=True)
    mods_cfg = data / "mods.cfg"
    existing = b"\xef\xbb\xbfKenshiAgentTelemetry.mod\r\n\r\n"
    mods_cfg.write_bytes(existing)

    install_authored_starts(bundle, tmp_path)

    assert mods_cfg.read_bytes() == (
        existing + b"KenshiAgentScenarios.mod\r\n"
    )


def test_authored_start_install_refuses_conflicting_mod_without_config_mutation(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    target = _install_tree(tmp_path, bundle)
    target.write_bytes(b"user-authored conflicting mod")
    mods_cfg = tmp_path / "data" / "mods.cfg"
    before = mods_cfg.read_bytes()

    with pytest.raises(AuthoredStartsError, match="refuses to overwrite"):
        install_authored_starts(bundle, tmp_path)

    assert target.read_bytes() == b"user-authored conflicting mod"
    assert mods_cfg.read_bytes() == before


def test_authored_start_install_preflights_recovery_before_any_write(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    data = tmp_path / "data"
    data.mkdir(parents=True)
    mods_cfg = data / "mods.cfg"
    mods_cfg.write_bytes(b"KenshiAgentTelemetry.mod\r\n")
    observed_at = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)
    backup = data / (
        "mods.cfg.kenshi-agent-pre-scenarios-20260727T070000.000000Z.bak"
    )
    backup.write_bytes(b"pre-existing recovery")

    with pytest.raises(AuthoredStartsError, match="existing enabled-mod backup"):
        install_authored_starts(bundle, tmp_path, installed_at=observed_at)

    assert not (tmp_path / "mods" / "KenshiAgentScenarios").exists()
    assert mods_cfg.read_bytes() == b"KenshiAgentTelemetry.mod\r\n"
    assert backup.read_bytes() == b"pre-existing recovery"


def test_game_start_resolution_and_loaded_proof_are_exact() -> None:
    bundle = _bundle()
    start = resolve_authored_game_start(bundle, "kae-03-broke-pair")

    verify_authored_game_start_snapshot(_snapshot(party_size=2), start)

    with pytest.raises(AuthoredStartsError, match="money"):
        verify_authored_game_start_snapshot(
            _snapshot(money=1_000, party_size=2),
            start,
        )
    with pytest.raises(AuthoredStartsError, match="party"):
        verify_authored_game_start_snapshot(_snapshot(party_size=1), start)
    with pytest.raises(AuthoredStartsError, match="Unknown authored Game Start"):
        resolve_authored_game_start(bundle, "KAE 03 - Broke Pair")


def test_game_start_proof_fails_closed_on_missing_capability_or_identity() -> None:
    start = _bundle().manifest.starts[0]
    missing_capability = _snapshot().model_copy(update={"capabilities": ["squad.basic"]})
    missing_identity = _snapshot().model_copy(update={"identity_session_id": None})

    with pytest.raises(AuthoredStartsError, match="capabilities"):
        verify_authored_game_start_snapshot(missing_capability, start)
    with pytest.raises(AuthoredStartsError, match="identity"):
        verify_authored_game_start_snapshot(missing_identity, start)
