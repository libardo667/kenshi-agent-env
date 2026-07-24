from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GraphicsProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    profile_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = Field(default="", max_length=1000)
    settings: dict[str, str] = Field(min_length=1)

    @field_validator("settings")
    @classmethod
    def settings_are_plain_key_values(cls, value: dict[str, str]) -> dict[str, str]:
        for key, setting in value.items():
            if not key.strip() or key != key.strip() or "=" in key or "\n" in key:
                raise ValueError(f"invalid graphics setting key: {key!r}")
            if "\n" in setting or "\r" in setting:
                raise ValueError(f"graphics setting {key!r} contains a line break")
        return value

    @model_validator(mode="after")
    def setting_keys_are_case_insensitively_unique(self) -> GraphicsProfile:
        folded = [key.casefold() for key in self.settings]
        if len(set(folded)) != len(folded):
            raise ValueError("graphics profile contains case-insensitively duplicate keys")
        return self


@dataclass(frozen=True, slots=True)
class GraphicsMismatch:
    key: str
    expected: str
    actual: str | None


@dataclass(frozen=True, slots=True)
class GraphicsVerification:
    profile_id: str
    settings_path: Path
    mismatches: tuple[GraphicsMismatch, ...]

    @property
    def matches(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True, slots=True)
class GraphicsApplyResult:
    verification: GraphicsVerification
    changed: bool
    backup_path: Path | None


@dataclass(frozen=True, slots=True)
class _ParsedLine:
    raw: str
    newline: str
    key: str | None = None
    key_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class _SettingsDocument:
    lines: tuple[_ParsedLine, ...]
    values: dict[str, tuple[str, str]]
    newline: str


def load_graphics_profile(path: Path) -> GraphicsProfile:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return GraphicsProfile.model_validate(payload)


def _read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def _split_newline(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def _parse_settings(payload: str) -> _SettingsDocument:
    lines: list[_ParsedLine] = []
    values: dict[str, tuple[str, str]] = {}
    detected_newline = "\n"
    for line_number, full_line in enumerate(payload.splitlines(keepends=True), start=1):
        line, newline = _split_newline(full_line)
        if newline and detected_newline == "\n":
            detected_newline = newline
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            lines.append(_ParsedLine(raw=line, newline=newline))
            continue
        if "=" not in line:
            raise ValueError(
                f"Malformed Kenshi settings line {line_number}: expected key=value."
            )
        key_prefix, raw_value = line.split("=", 1)
        key = key_prefix.strip()
        if not key:
            raise ValueError(f"Malformed Kenshi settings line {line_number}: empty key.")
        folded = key.casefold()
        if folded in values:
            prior = values[folded][0]
            raise ValueError(
                "Kenshi settings contain case-insensitively duplicate keys: "
                f"{prior!r} and {key!r}."
            )
        values[folded] = (key, raw_value.strip())
        lines.append(
            _ParsedLine(
                raw=line,
                newline=newline,
                key=key,
                key_prefix=key_prefix,
            )
        )
    return _SettingsDocument(
        lines=tuple(lines),
        values=values,
        newline=detected_newline,
    )


def _expected_settings(profile: GraphicsProfile) -> dict[str, tuple[str, str]]:
    return {
        key.casefold(): (key, expected)
        for key, expected in profile.settings.items()
    }


def verify_graphics_profile(
    settings_path: Path,
    profile: GraphicsProfile,
) -> GraphicsVerification:
    document = _parse_settings(_read_text_exact(settings_path))
    mismatches: list[GraphicsMismatch] = []
    for folded, (profile_key, expected) in _expected_settings(profile).items():
        actual_entry = document.values.get(folded)
        actual = actual_entry[1] if actual_entry is not None else None
        if actual != expected:
            mismatches.append(
                GraphicsMismatch(
                    key=profile_key,
                    expected=expected,
                    actual=actual,
                )
            )
    return GraphicsVerification(
        profile_id=profile.profile_id,
        settings_path=settings_path,
        mismatches=tuple(mismatches),
    )


def _render_settings(document: _SettingsDocument, profile: GraphicsProfile) -> str:
    expected = _expected_settings(profile)
    rendered: list[str] = []
    seen: set[str] = set()
    for line in document.lines:
        if line.key is None:
            rendered.append(line.raw + line.newline)
            continue
        folded = line.key.casefold()
        replacement = expected.get(folded)
        if replacement is None:
            rendered.append(line.raw + line.newline)
            continue
        seen.add(folded)
        assert line.key_prefix is not None
        rendered.append(f"{line.key_prefix}={replacement[1]}{line.newline}")

    missing = [
        (folded, key, value)
        for folded, (key, value) in expected.items()
        if folded not in seen
    ]
    if missing:
        if rendered and not rendered[-1].endswith(("\n", "\r")):
            rendered[-1] += document.newline
        rendered.extend(
            f"{key}={value}{document.newline}"
            for _, key, value in missing
        )
    return "".join(rendered)


def apply_graphics_profile(
    settings_path: Path,
    profile: GraphicsProfile,
    *,
    now: datetime | None = None,
) -> GraphicsApplyResult:
    before = verify_graphics_profile(settings_path, profile)
    if before.matches:
        return GraphicsApplyResult(
            verification=before,
            changed=False,
            backup_path=None,
        )

    original = _read_text_exact(settings_path)
    document = _parse_settings(original)
    rendered = _render_settings(document, profile)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = settings_path.with_name(
        f"{settings_path.name}.kenshi-agent-pre-{profile.profile_id}-{timestamp}.bak"
    )
    shutil.copy2(settings_path, backup_path)

    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{settings_path.name}.kenshi-agent-",
            suffix=".tmp",
            dir=settings_path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copystat(settings_path, temporary_path)
        os.replace(temporary_path, settings_path)
        temporary_path = None
        after = verify_graphics_profile(settings_path, profile)
        if not after.matches:
            shutil.copy2(backup_path, settings_path)
            raise RuntimeError("Graphics profile verification failed; restored backup.")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return GraphicsApplyResult(
        verification=after,
        changed=True,
        backup_path=backup_path,
    )
