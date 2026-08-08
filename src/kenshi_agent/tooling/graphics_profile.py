from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_setting_map(value: dict[str, str]) -> dict[str, str]:
    for key, setting in value.items():
        if not key.strip() or key != key.strip() or "=" in key or "\n" in key:
            raise ValueError(f"invalid graphics setting key: {key!r}")
        if "\n" in setting or "\r" in setting:
            raise ValueError(f"graphics setting {key!r} contains a line break")
    folded = [key.casefold() for key in value]
    if len(set(folded)) != len(folded):
        raise ValueError("graphics profile contains case-insensitively duplicate keys")
    return value


class RendererProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str = Field(min_length=1, max_length=120)
    settings: dict[str, str] = Field(min_length=1)

    @field_validator("section")
    @classmethod
    def section_is_plain(cls, value: str) -> str:
        if value != value.strip() or any(character in value for character in "[]\r\n"):
            raise ValueError("renderer section must be a plain INI section name")
        return value

    @field_validator("settings")
    @classmethod
    def settings_are_plain_key_values(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_setting_map(value)


class GraphicsProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    profile_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = Field(default="", max_length=1000)
    settings: dict[str, str] = Field(min_length=1)
    renderer: RendererProfile | None = None

    @field_validator("settings")
    @classmethod
    def settings_are_plain_key_values(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_setting_map(value)

@dataclass(frozen=True, slots=True)
class GraphicsMismatch:
    key: str
    expected: str
    actual: str | None
    document: Literal["settings", "renderer"] = "settings"


@dataclass(frozen=True, slots=True)
class GraphicsVerification:
    profile_id: str
    settings_path: Path
    renderer_path: Path | None
    mismatches: tuple[GraphicsMismatch, ...]

    @property
    def matches(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True, slots=True)
class GraphicsApplyResult:
    verification: GraphicsVerification
    changed: bool
    backup_paths: tuple[Path, ...]

    @property
    def backup_path(self) -> Path | None:
        return self.backup_paths[0] if self.backup_paths else None


@dataclass(frozen=True, slots=True)
class _ParsedLine:
    raw: str
    newline: str
    section: str | None
    section_header: str | None = None
    key: str | None = None
    key_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class _SettingsDocument:
    lines: tuple[_ParsedLine, ...]
    values: dict[tuple[str | None, str], tuple[str, str]]
    sections: dict[str, str]
    newline: str


@dataclass(frozen=True, slots=True)
class _ManagedDocument:
    label: Literal["settings", "renderer"]
    path: Path
    expected: dict[str, str]
    section: str | None


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
    values: dict[tuple[str | None, str], tuple[str, str]] = {}
    sections: dict[str, str] = {}
    detected_newline = "\n"
    active_section: str | None = None
    for line_number, full_line in enumerate(payload.splitlines(keepends=True), start=1):
        line, newline = _split_newline(full_line)
        if newline and detected_newline == "\n":
            detected_newline = newline
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            lines.append(
                _ParsedLine(raw=line, newline=newline, section=active_section)
            )
            continue
        if stripped.startswith("["):
            if not stripped.endswith("]"):
                raise ValueError(
                    f"Malformed Kenshi settings line {line_number}: invalid section."
                )
            section = stripped[1:-1].strip()
            if not section:
                raise ValueError(
                    f"Malformed Kenshi settings line {line_number}: empty section."
                )
            folded_section = section.casefold()
            if folded_section in sections:
                raise ValueError(
                    "Kenshi settings contain a case-insensitively duplicate "
                    f"section: {section!r}."
                )
            sections[folded_section] = section
            active_section = section
            lines.append(
                _ParsedLine(
                    raw=line,
                    newline=newline,
                    section=active_section,
                    section_header=section,
                )
            )
            continue
        if "=" not in line:
            raise ValueError(
                f"Malformed Kenshi settings line {line_number}: expected key=value."
            )
        key_prefix, raw_value = line.split("=", 1)
        key = key_prefix.strip()
        if not key:
            raise ValueError(f"Malformed Kenshi settings line {line_number}: empty key.")
        location = (
            active_section.casefold() if active_section is not None else None,
            key.casefold(),
        )
        if location in values:
            prior = values[location][0]
            raise ValueError(
                "Kenshi settings contain case-insensitively duplicate keys in "
                f"one section: {prior!r} and {key!r}."
            )
        values[location] = (key, raw_value.strip())
        lines.append(
            _ParsedLine(
                raw=line,
                newline=newline,
                section=active_section,
                key=key,
                key_prefix=key_prefix,
            )
        )
    return _SettingsDocument(
        lines=tuple(lines),
        values=values,
        sections=sections,
        newline=detected_newline,
    )


def _expected_settings(settings: dict[str, str]) -> dict[str, tuple[str, str]]:
    return {
        key.casefold(): (key, expected)
        for key, expected in settings.items()
    }


def _managed_documents(
    settings_path: Path,
    profile: GraphicsProfile,
    renderer_path: Path | None,
) -> tuple[_ManagedDocument, ...]:
    documents = [
        _ManagedDocument(
            label="settings",
            path=settings_path,
            expected=profile.settings,
            section=None,
        )
    ]
    if profile.renderer is not None:
        if renderer_path is None:
            raise ValueError(
                "Graphics profile manages renderer settings but no kenshi.cfg "
                "path was supplied."
            )
        if renderer_path == settings_path:
            raise ValueError("settings.cfg and kenshi.cfg must be different files.")
        documents.append(
            _ManagedDocument(
                label="renderer",
                path=renderer_path,
                expected=profile.renderer.settings,
                section=profile.renderer.section,
            )
        )
    return tuple(documents)


def _settings_agree(expected: str, actual: str | None) -> bool:
    """Whether an installed setting is the profile's, allowing Kenshi's rounding.

    Compared as strings, `npc range` failed launch as expected '1500', found
    '1500.02' - Kenshi had written the value back through its own slider and
    changed it by 0.0013%. An exact-string check on a float-valued setting
    cannot be satisfied for long, and rewriting the file at every launch treats
    the symptom while the next write-back re-breaks it.

    Numeric settings therefore compare numerically, within one part in a
    thousand of the expected magnitude. That absorbs a write-back and nothing
    else: the ordinal settings that actually mean something are small integers,
    where the nearest wrong value differs by tens of percent - shadow quality 1
    against 2 is still a mismatch, and so is a foliage range someone halved.
    Non-numeric values keep exact comparison.
    """

    if actual is None:
        return False
    if actual == expected:
        return True
    try:
        expected_value = float(expected)
        actual_value = float(actual)
    except ValueError:
        return False
    tolerance = max(abs(expected_value) * 0.001, 1e-9)
    return abs(actual_value - expected_value) <= tolerance


def _document_mismatches(managed: _ManagedDocument) -> list[GraphicsMismatch]:
    document = _parse_settings(_read_text_exact(managed.path))
    folded_section = (
        managed.section.casefold() if managed.section is not None else None
    )
    if (
        managed.section is not None
        and folded_section not in document.sections
    ):
        raise ValueError(
            f"Kenshi renderer config has no [{managed.section}] section."
        )
    mismatches: list[GraphicsMismatch] = []
    for folded, (profile_key, expected) in _expected_settings(managed.expected).items():
        actual_entry = document.values.get((folded_section, folded))
        actual = actual_entry[1] if actual_entry is not None else None
        if not _settings_agree(expected, actual):
            mismatches.append(
                GraphicsMismatch(
                    key=profile_key,
                    expected=expected,
                    actual=actual,
                    document=managed.label,
                )
            )
    return mismatches


def verify_graphics_profile(
    settings_path: Path,
    profile: GraphicsProfile,
    *,
    renderer_path: Path | None = None,
) -> GraphicsVerification:
    managed = _managed_documents(settings_path, profile, renderer_path)
    mismatches = [
        mismatch
        for document in managed
        for mismatch in _document_mismatches(document)
    ]
    return GraphicsVerification(
        profile_id=profile.profile_id,
        settings_path=settings_path,
        renderer_path=renderer_path if profile.renderer is not None else None,
        mismatches=tuple(mismatches),
    )


def _render_settings(
    document: _SettingsDocument,
    expected_settings: dict[str, str],
    *,
    section: str | None,
) -> str:
    expected = _expected_settings(expected_settings)
    folded_section = section.casefold() if section is not None else None
    rendered: list[str] = []
    seen: set[str] = set()
    insertion_index = 0
    section_found = section is None

    for line in document.lines:
        line_section = (
            line.section.casefold() if line.section is not None else None
        )
        if line.section_header is not None and line_section == folded_section:
            section_found = True
        if line_section == folded_section:
            insertion_index = len(rendered) + 1
        if line.key is None or line_section != folded_section:
            rendered.append(line.raw + line.newline)
            continue
        folded_key = line.key.casefold()
        replacement = expected.get(folded_key)
        if replacement is None:
            rendered.append(line.raw + line.newline)
            continue
        seen.add(folded_key)
        assert line.key_prefix is not None
        rendered.append(f"{line.key_prefix}={replacement[1]}{line.newline}")

    if not section_found:
        raise ValueError(f"Kenshi renderer config has no [{section}] section.")

    missing = [
        (key, value)
        for folded, (key, value) in expected.items()
        if folded not in seen
    ]
    if missing:
        if insertion_index > 0 and not rendered[insertion_index - 1].endswith(
            ("\n", "\r")
        ):
            rendered[insertion_index - 1] += document.newline
        rendered[insertion_index:insertion_index] = [
            f"{key}={value}{document.newline}"
            for key, value in missing
        ]
    return "".join(rendered)


def _write_temporary(path: Path, rendered: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.kenshi-agent-",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copystat(path, temporary_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def apply_graphics_profile(
    settings_path: Path,
    profile: GraphicsProfile,
    *,
    renderer_path: Path | None = None,
    now: datetime | None = None,
) -> GraphicsApplyResult:
    managed = _managed_documents(settings_path, profile, renderer_path)
    before = verify_graphics_profile(
        settings_path,
        profile,
        renderer_path=renderer_path,
    )
    if before.matches:
        return GraphicsApplyResult(
            verification=before,
            changed=False,
            backup_paths=(),
        )

    changed_labels = {mismatch.document for mismatch in before.mismatches}
    changed_documents = tuple(
        document for document in managed if document.label in changed_labels
    )
    rendered = {
        document.path: _render_settings(
            _parse_settings(_read_text_exact(document.path)),
            document.expected,
            section=document.section,
        )
        for document in changed_documents
    }
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime(
        "%Y%m%dT%H%M%S.%fZ"
    )
    backups = {
        document.path: document.path.with_name(
            f"{document.path.name}.kenshi-agent-pre-"
            f"{profile.profile_id}-{timestamp}.bak"
        )
        for document in changed_documents
    }
    for path, backup in backups.items():
        shutil.copy2(path, backup)

    temporaries: dict[Path, Path] = {}
    try:
        temporaries = {
            path: _write_temporary(path, payload)
            for path, payload in rendered.items()
        }
        for path, temporary in temporaries.items():
            os.replace(temporary, path)
        after = verify_graphics_profile(
            settings_path,
            profile,
            renderer_path=renderer_path,
        )
        if not after.matches:
            raise RuntimeError("Graphics profile verification failed.")
    except BaseException as original:
        restore_errors: list[OSError] = []
        for path, backup in backups.items():
            try:
                shutil.copy2(backup, path)
            except OSError as exc:
                restore_errors.append(exc)
        if restore_errors:
            raise RuntimeError(
                "Graphics apply failed and one or more managed files could not "
                "be restored from their timestamped backups."
            ) from original
        raise
    finally:
        for temporary in temporaries.values():
            temporary.unlink(missing_ok=True)

    return GraphicsApplyResult(
        verification=after,
        changed=True,
        backup_paths=tuple(backups.values()),
    )
