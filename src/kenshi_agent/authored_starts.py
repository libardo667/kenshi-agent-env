from __future__ import annotations

import base64
import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import Field, model_validator

from .core.base import StrictModel
from .core.telemetry import TelemetrySnapshot

_BUNDLE_DIR = (
    Path(__file__).resolve().parents[2]
    / "scenarios"
    / "KenshiAgentScenarios"
)


class AuthoredStartsError(RuntimeError):
    """The authored Game Start artifact or its live proof cannot be trusted."""


class BundledModIdentity(StrictModel):
    directory: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.mod$")
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuthoredGameStart(StrictModel):
    start_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    label: str = Field(min_length=1, max_length=80)
    money: int = Field(ge=0)
    party_size: int = Field(ge=1, le=30)


class AuthoredStartsManifest(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    mod: BundledModIdentity
    starts: list[AuthoredGameStart] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def identities_are_unique(self) -> AuthoredStartsManifest:
        ids = [start.start_id for start in self.starts]
        labels = [start.label.casefold() for start in self.starts]
        if len(ids) != len(set(ids)):
            raise ValueError("authored Game Start IDs must be unique")
        if len(labels) != len(set(labels)):
            raise ValueError("authored Game Start labels must be unique")
        return self


@dataclass(frozen=True)
class AuthoredStartsBundle:
    manifest: AuthoredStartsManifest
    payload: bytes


@dataclass(frozen=True)
class AuthoredStartsInstallResult:
    mod_path: Path
    mod_changed: bool
    enabled_changed: bool
    backup_path: Path | None


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def load_authored_starts_bundle(
    bundle_dir: Path | None = None,
) -> AuthoredStartsBundle:
    root = (bundle_dir or _BUNDLE_DIR).resolve()
    try:
        manifest = AuthoredStartsManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        encoded = "".join(
            (root / f"{manifest.mod.filename}.base64")
            .read_text(encoding="ascii")
            .split()
        )
        payload = base64.b64decode(encoded, validate=True)
    except (OSError, ValueError) as exc:
        raise AuthoredStartsError(
            f"Authored Game Start bundle is unreadable or invalid: {root}"
        ) from exc
    if len(payload) != manifest.mod.size_bytes:
        raise AuthoredStartsError(
            "Authored Game Start bundle size does not match its manifest."
        )
    if _sha256_bytes(payload) != manifest.mod.sha256:
        raise AuthoredStartsError(
            "Authored Game Start bundle digest does not match its manifest."
        )
    return AuthoredStartsBundle(manifest=manifest, payload=payload)


def _target_mod_path(bundle: AuthoredStartsBundle, kenshi_root: Path) -> Path:
    return (
        kenshi_root
        / "mods"
        / bundle.manifest.mod.directory
        / bundle.manifest.mod.filename
    )


def _enabled_mod_names(path: Path) -> tuple[list[str], bytes]:
    try:
        payload = path.read_bytes()
        text = payload.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise AuthoredStartsError(f"Kenshi enabled-mod list is unreadable: {path}") from exc
    names = [line.strip() for line in text.splitlines() if line.strip()]
    return names, payload


def verify_installed_authored_starts(
    bundle: AuthoredStartsBundle,
    kenshi_root: Path,
) -> Path:
    target = _target_mod_path(bundle, kenshi_root)
    if target.is_symlink() or not target.is_file():
        raise AuthoredStartsError(
            f"Exact authored Game Start mod is not installed: {target}"
        )
    identity = bundle.manifest.mod
    if target.stat().st_size != identity.size_bytes:
        raise AuthoredStartsError(
            "Installed authored Game Start mod size does not match the bundle."
        )
    if _sha256_file(target) != identity.sha256:
        raise AuthoredStartsError(
            "Installed authored Game Start mod digest does not match the bundle."
        )

    names, _ = _enabled_mod_names(kenshi_root / "data" / "mods.cfg")
    count = sum(name.casefold() == identity.filename.casefold() for name in names)
    if count != 1:
        raise AuthoredStartsError(
            f"{identity.filename} must be enabled exactly once in Kenshi data/mods.cfg."
        )
    return target


def install_authored_starts(
    bundle: AuthoredStartsBundle,
    kenshi_root: Path,
    *,
    installed_at: datetime | None = None,
) -> AuthoredStartsInstallResult:
    """Install exact bytes and enable them without overwriting another artifact."""

    root = kenshi_root.resolve()
    mods_cfg = root / "data" / "mods.cfg"
    names, mods_payload = _enabled_mod_names(mods_cfg)
    identity = bundle.manifest.mod
    enabled_count = sum(
        name.casefold() == identity.filename.casefold() for name in names
    )
    if enabled_count > 1:
        raise AuthoredStartsError(
            f"{identity.filename} appears more than once in Kenshi data/mods.cfg."
        )

    backup_path: Path | None = None
    enabled_changed = enabled_count == 0
    if enabled_changed:
        observed_at = installed_at or datetime.now(UTC)
        stamp = observed_at.strftime("%Y%m%dT%H%M%S.%fZ")
        backup_path = mods_cfg.with_name(
            f"{mods_cfg.name}.kenshi-agent-pre-scenarios-{stamp}.bak"
        )
        if backup_path.exists():
            raise AuthoredStartsError(
                f"Refusing to replace an existing enabled-mod backup: {backup_path}"
            )

    target = _target_mod_path(bundle, root)
    if target.is_symlink():
        raise AuthoredStartsError(
            f"Authored Game Start install target is a symbolic link: {target}"
        )
    if target.exists():
        if not target.is_file() or _sha256_file(target) != identity.sha256:
            raise AuthoredStartsError(
                "Authored Game Start install refuses to overwrite a different artifact."
            )
        mod_changed = False
    else:
        parent = target.parent
        if parent.exists() and (
            not parent.is_dir() or parent.is_symlink() or any(parent.iterdir())
        ):
            raise AuthoredStartsError(
                "Authored Game Start install target directory contains unexpected files."
            )
        parent.mkdir(parents=True, exist_ok=True)
        _write_bytes_atomic(target, bundle.payload)
        mod_changed = True

    if enabled_changed:
        assert backup_path is not None
        shutil.copy2(mods_cfg, backup_path)
        newline = b"\r\n" if b"\r\n" in mods_payload else b"\n"
        appended = mods_payload
        if appended and not appended.endswith((b"\r", b"\n")):
            appended += newline
        appended += identity.filename.encode("utf-8") + newline
        _write_bytes_atomic(mods_cfg, appended)

    verify_installed_authored_starts(bundle, root)
    return AuthoredStartsInstallResult(
        mod_path=target,
        mod_changed=mod_changed,
        enabled_changed=enabled_changed,
        backup_path=backup_path,
    )


def resolve_authored_game_start(
    bundle: AuthoredStartsBundle,
    start_id: str,
) -> AuthoredGameStart:
    matches = [start for start in bundle.manifest.starts if start.start_id == start_id]
    if len(matches) != 1:
        raise AuthoredStartsError(f"Unknown authored Game Start ID: {start_id!r}")
    return matches[0]


def verify_authored_game_start_snapshot(
    snapshot: TelemetrySnapshot,
    start: AuthoredGameStart,
) -> None:
    if not snapshot.game.loaded or snapshot.game.paused is not True:
        raise AuthoredStartsError(
            "Authored Game Start proof requires a loaded, explicitly paused game."
        )
    if not snapshot.identity_session_id:
        raise AuthoredStartsError(
            "Authored Game Start proof requires a native identity session."
        )
    required = {"game.money", "squad.basic"}
    missing = required - set(snapshot.capabilities)
    if missing:
        raise AuthoredStartsError(
            "Authored Game Start proof is missing capabilities: "
            + ", ".join(sorted(missing))
        )
    if snapshot.game.money != start.money:
        raise AuthoredStartsError(
            f"Authored Game Start expected money {start.money}, but observed "
            f"{snapshot.game.money!r}."
        )
    if len(snapshot.squad) != start.party_size:
        raise AuthoredStartsError(
            f"Authored Game Start expected party size {start.party_size}, but "
            f"observed {len(snapshot.squad)}."
        )
