"""Export the exact KAE session log into EvoGen's current trajectory envelope.

This boundary is intentionally small and conservative.  KAE owns the source
event authority and the reviewed disposition map; EvoGen consumes the current
envelope written here.  The raw event log is copied byte-for-byte so the
projection never becomes the only evidence left for a run.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, cast, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from ..core.transport import SessionEvent
from .capability_manifest import CapabilityManifest, capability_manifest_digest
from .generation_manifest import GenerationManifest, digest_bytes
from .session_event_dispositions import (
    discover_source_inventory,
    load_reviewed_dispositions,
    validate_reviewed_dispositions,
)

ROOT = Path(__file__).resolve().parents[3]
REVIEWED_PATH = ROOT / "docs" / "reconstruction" / "session_event_dispositions.json"


class TrajectoryExportError(ValueError):
    """The source or linked provenance cannot produce a sound trajectory."""


class FieldCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    present: int = Field(ge=0)
    missing: int = Field(ge=0)


EventKind = Literal[
    "run_started",
    "observation",
    "observation_delta",
    "affordance_set",
    "decision",
    "binding",
    "dispatch",
    "execution_receipt",
    "outcome_observation",
    "memory_update",
    "human_intervention",
    "recovery",
    "error",
    "goal_blocked",
    "goal_achieved",
    "run_finished",
]


class TrajectoryEventEnvelope(BaseModel):
    """KAE-owned mirror of EvoGen's current strict trajectory envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_version: Literal["1.0"]
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_id: str = Field(min_length=1)
    sequence: StrictInt = Field(ge=0)
    recorded_at: datetime
    kind: EventKind
    world_revision: str | None
    source_event_type: str | None
    source_event_id: str | None
    source_sequence: StrictInt | None
    source_step_index: StrictInt | None
    source_world_revision: str | None
    payload: dict[str, Any]


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file: Literal["raw-events.jsonl"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_count: int = Field(ge=1)
    event_types: list[str]
    sequence_mode: Literal["legacy_prefix", "legacy_prefix_and_sequenced_suffix", "sequenced"]
    source_sequence_counts: FieldCounts
    source_event_id_counts: FieldCounts

    @model_validator(mode="after")
    def counts_cover_source(self) -> SourceManifest:
        sequence_total = (
            self.source_sequence_counts.present + self.source_sequence_counts.missing
        )
        if sequence_total != self.record_count:
            raise ValueError("source sequence counts must cover every record")
        event_id_total = (
            self.source_event_id_counts.present + self.source_event_id_counts.missing
        )
        if event_id_total != self.record_count:
            raise ValueError("source event ID counts must cover every record")
        if self.event_types != sorted(set(self.event_types)):
            raise ValueError("source event types must be unique and sorted")
        if any(not event_type.strip() for event_type in self.event_types):
            raise ValueError("source event types must be nonblank")
        return self


class TrajectoryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file: Literal["trajectory.jsonl"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_count: int = Field(ge=0)
    kind_counts: dict[str, int]
    withheld_projection_kinds: list[Literal["binding", "dispatch"]]

    @model_validator(mode="after")
    def counts_cover_trajectory(self) -> TrajectoryManifest:
        if not set(self.kind_counts).issubset(get_args(EventKind)):
            raise ValueError("trajectory kind counts contain an unknown event kind")
        if any(count < 0 for count in self.kind_counts.values()):
            raise ValueError("trajectory kind counts cannot be negative")
        if sum(self.kind_counts.values()) != self.event_count:
            raise ValueError("trajectory kind counts must cover every normalized event")
        if self.kind_counts != dict(sorted(self.kind_counts.items())):
            raise ValueError("trajectory kind counts must be sorted")
        if self.withheld_projection_kinds != ["binding", "dispatch"]:
            raise ValueError("binding and dispatch must remain explicitly withheld")
        return self


class ReviewedDispositionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority: Literal["docs/reconstruction/session_event_dispositions.json"]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_inventory_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_type_count: int = Field(ge=1)
    source_record_count: int = Field(ge=1)
    disposition_counts: dict[str, int]

    @field_validator("disposition_counts")
    @classmethod
    def disposition_counts_are_exact(cls, value: dict[str, int]) -> dict[str, int]:
        expected = {
            "derived_summary",
            "exact_evogen_event",
            "intentionally_ignored",
            "subject_only_raw_evidence",
        }
        if set(value) != expected or any(count < 0 for count in value.values()):
            raise ValueError("disposition counts must name every reviewed disposition")
        return value


class ExportManifest(BaseModel):
    """The deterministic, typed manifest published beside one trajectory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    bundle_kind: Literal["kenshi-agent-env-trajectory"] = "kenshi-agent-env-trajectory"
    bundle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_manifest_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_linkage: Literal["supplied_external_manifest"]
    scenario_linkage: Literal["generation_manifest", "declared_external"]
    scenario_id: str
    run_id: str
    source: SourceManifest
    trajectory: TrajectoryManifest
    reviewed_dispositions: ReviewedDispositionManifest

    @field_validator("scenario_id", "run_id")
    @classmethod
    def identity_is_nonblank(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("trajectory identities must be nonblank and unpadded")
        return value

    @model_validator(mode="after")
    def linked_counts_are_coherent(self) -> ExportManifest:
        if self.capability_generation_id != self.generation_id:
            raise ValueError("capability generation identity must match generation")
        if sum(self.reviewed_dispositions.disposition_counts.values()) != self.source.record_count:
            raise ValueError("disposition counts must cover every source record")
        exact_count = self.reviewed_dispositions.disposition_counts["exact_evogen_event"]
        if exact_count != self.trajectory.event_count:
            raise ValueError("exact disposition count must match normalized event count")
        return self


_REQUIRED_SOURCE_KEYS = frozenset(
    {"event_type", "run_id", "step_index", "timestamp", "payload"}
)
_OPTIONAL_SOURCE_KEYS = frozenset({"event_sequence"})
SequenceMode = Literal["legacy_prefix", "legacy_prefix_and_sequenced_suffix", "sequenced"]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TrajectoryExportError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> NoReturn:
    raise TrajectoryExportError(f"Non-finite JSON constant {value!r}")


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrajectoryExportError(f"Invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise TrajectoryExportError(f"{label} must be a JSON object")
    return value, raw


def _validate_file_path(path: Path, label: str) -> Path:
    resolved = Path(os.path.abspath(path.expanduser()))
    try:
        metadata = os.lstat(resolved)
    except OSError as exc:
        raise TrajectoryExportError(f"{label} cannot be inspected") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise TrajectoryExportError(f"{label} must be a regular file")
    return resolved


def _validate_source(path: Path) -> tuple[bytes, list[SessionEvent], list[dict[str, Any]]]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise TrajectoryExportError(f"Cannot read source events: {exc}") from exc
    if not raw_bytes:
        raise TrajectoryExportError("Source events are empty")
    if not raw_bytes.endswith(b"\n"):
        raise TrajectoryExportError("Source events have a truncated unterminated tail")

    events: list[SessionEvent] = []
    raw_records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw_bytes.splitlines(keepends=True), start=1):
        if line in {b"\n", b"\r\n"}:
            raise TrajectoryExportError(f"Blank source record at line {line_number}")
        if not line.endswith((b"\n", b"\r\n")):
            raise TrajectoryExportError(f"Truncated source record at line {line_number}")
        try:
            record = json.loads(
                line.rstrip(b"\r\n").decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_nonfinite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TrajectoryExportError) as exc:
            raise TrajectoryExportError(
                f"Malformed source JSON at line {line_number}: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise TrajectoryExportError(f"Source record at line {line_number} is not an object")
        if not _REQUIRED_SOURCE_KEYS.issubset(record) or (
            set(record) - _REQUIRED_SOURCE_KEYS - _OPTIONAL_SOURCE_KEYS
        ):
            missing = sorted(_REQUIRED_SOURCE_KEYS - set(record))
            extra = sorted(set(record) - _REQUIRED_SOURCE_KEYS - _OPTIONAL_SOURCE_KEYS)
            raise TrajectoryExportError(
                f"Source record at line {line_number} has invalid fields; "
                f"missing={missing}, extra={extra}"
            )
        for field in ("event_sequence", "step_index"):
            value = record.get(field)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                raise TrajectoryExportError(
                    f"Source record at line {line_number} {field} must be an integer or null"
                )
        if not isinstance(record["timestamp"], str):
            raise TrajectoryExportError(
                f"Source record at line {line_number} timestamp must be an ISO string"
            )
        try:
            event = SessionEvent.model_validate(record)
        except ValidationError as exc:
            raise TrajectoryExportError(
                f"Invalid source record at line {line_number}: {exc}"
            ) from exc
        events.append(event)
        raw_records.append(record)
    return raw_bytes, events, raw_records


def _revision_identity(value: Any) -> str | None:
    """Give structured revisions an opaque stable identity without stringifying them."""

    if value is None:
        return None
    if isinstance(value, (str, int, float, dict, list, bool)):
        return "kae-revision-sha256:" + hashlib.sha256(_canonical(value)).hexdigest()
    raise TrajectoryExportError("Structured world revision must be JSON data")


def _payload_revision(record: dict[str, Any]) -> Any:
    payload = record["payload"]
    if not isinstance(payload, dict):
        return None
    for key in ("completed_at_revision", "world_revision", "started_after_revision"):
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _payload_correlation(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload[key]
        for key in (
            "started_after_revision",
            "completed_at_revision",
            "world_revision",
            "command_id",
            "outcome_id",
            "plan_id",
            "plan_version",
            "step_id",
            "identity_session_id",
        )
        if key in payload
    }


def _source_event_id(record: dict[str, Any]) -> str | None:
    # Only an actual source event identity is promoted.  command_id and
    # outcome_id remain in payload.raw and are never joined or relabeled.
    for key in ("event_id", "id"):
        if key in record:
            value = record[key]
            if value is None:
                return None
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value)
            raise TrajectoryExportError(f"source {key} must be a string, integer, or null")
    return None


def _normal_event(
    event: SessionEvent,
    raw: dict[str, Any],
    *,
    generation_id: str,
    scenario_id: str,
    normalized_sequence: int,
    kind: str,
    source_ordinal: int,
) -> TrajectoryEventEnvelope:
    revision = _revision_identity(_payload_revision(raw))
    payload = raw["payload"]
    if not isinstance(payload, dict):  # SessionEvent's type makes this unreachable.
        payload = {}
    # Keep the source payload and complete source record explicit.  No source
    # object is flattened into a lossy string representation.
    normalized_payload = {
        "source_payload": payload,
        "correlation": _payload_correlation(payload),
        "raw": raw,
    }
    event_id = "kae-evt-" + hashlib.sha256(
        _canonical(
            {
                "run_id": event.run_id,
                "event_sequence": event.event_sequence,
                "source_ordinal": source_ordinal,
                "record": raw,
            }
        )
    ).hexdigest()
    try:
        return TrajectoryEventEnvelope(
            envelope_version="1.0",
            event_id=event_id,
            run_id=event.run_id,
            generation_id=generation_id,
            scenario_id=scenario_id,
            sequence=normalized_sequence,
            recorded_at=event.timestamp,
            kind=cast(EventKind, kind),
            world_revision=revision,
            source_event_type=event.event_type,
            source_event_id=_source_event_id(raw),
            source_sequence=event.event_sequence,
            source_step_index=event.step_index,
            source_world_revision=revision,
            payload=normalized_payload,
        )
    except ValidationError as exc:
        raise TrajectoryExportError(f"Cannot construct current trajectory envelope: {exc}") from exc


def _safe_output(output: Path) -> tuple[Path, Path]:
    destination = Path(os.path.abspath(output.expanduser()))
    for component in (destination, *destination.parents):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise TrajectoryExportError("Output path cannot be inspected safely") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise TrajectoryExportError("Output path cannot contain a symlink")
        if component == destination and metadata:
            raise TrajectoryExportError("Output bundle already exists")
        if component == component.parent:
            break
    if not destination.parent.is_dir():
        raise TrajectoryExportError("Output bundle parent must already exist")
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    return destination, temp


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def export_trajectory(
    events_path: str | Path,
    generation_manifest_path: str | Path,
    capability_manifest_path: str | Path,
    scenario_id: str,
    output: str | Path,
) -> ExportManifest:
    """Validate and atomically publish one exact trajectory bundle."""

    source = _validate_file_path(Path(events_path), "source events")
    generation_path = _validate_file_path(Path(generation_manifest_path), "generation manifest")
    capability_path = _validate_file_path(Path(capability_manifest_path), "capability manifest")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise TrajectoryExportError("scenario_id must be non-empty")

    generation_raw, generation_bytes = _load_json(generation_path, "generation manifest")
    capability_raw, capability_bytes = _load_json(capability_path, "capability manifest")
    try:
        generation = GenerationManifest.model_validate(generation_raw)
        capability = CapabilityManifest.model_validate(capability_raw)
    except ValidationError as exc:
        raise TrajectoryExportError(f"Invalid linked manifest: {exc}") from exc
    capability_digest = capability_manifest_digest(capability)
    if generation.subject != "kenshi-agent-env":
        raise TrajectoryExportError("Generation manifest subject is not kenshi-agent-env")
    if generation.generation_id != capability.generation_id:
        raise TrajectoryExportError("Generation and capability manifest identities differ")
    if generation.capability_manifest_digest != capability_digest:
        raise TrajectoryExportError(
            "Generation manifest capability digest does not match capability manifest"
        )
    scenario_evidence = generation.metadata.scenario
    scenario_linkage: Literal["generation_manifest", "declared_external"]
    if scenario_evidence.state == "present":
        if (
            scenario_evidence.identity is None
            or scenario_evidence.identity.scenario_id != scenario_id
        ):
            raise TrajectoryExportError(
                "Explicit scenario_id does not match generation manifest scenario"
            )
        scenario_linkage = "generation_manifest"
    else:
        scenario_linkage = "declared_external"

    source_bytes, events, raw_records = _validate_source(source)
    reviewed = load_reviewed_dispositions(REVIEWED_PATH)
    try:
        inventory = discover_source_inventory()
        validated_rows = validate_reviewed_dispositions(inventory, reviewed)
    except ValueError as exc:
        raise TrajectoryExportError(
            f"Reviewed disposition authority is stale or invalid: {exc}"
        ) from exc
    rows = validated_rows
    dispositions: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("source_event_type"), str):
            raise TrajectoryExportError("Reviewed disposition authority is malformed")
        event_type = row["source_event_type"]
        if event_type in dispositions:
            raise TrajectoryExportError(f"Duplicate reviewed disposition for {event_type!r}")
        dispositions[event_type] = row

    run_ids = {event.run_id for event in events}
    if len(run_ids) != 1:
        raise TrajectoryExportError(f"Source events contain mixed run IDs: {sorted(run_ids)}")
    run_id = next(iter(run_ids))
    for event in events:
        if event.event_type not in dispositions:
            raise TrajectoryExportError(f"Unreviewed outer event type {event.event_type!r}")

    sequence_values = [event.event_sequence for event in events]
    sequence_mode: SequenceMode
    first_sequenced = next(
        (index for index, value in enumerate(sequence_values) if value is not None),
        None,
    )
    if first_sequenced is not None:
        if any(value is None for value in sequence_values[first_sequenced:]):
            raise TrajectoryExportError(
                "Sequenced source suffix cannot return to nullable event_sequence"
            )
        suffix = [value for value in sequence_values[first_sequenced:] if value is not None]
        expected_first = first_sequenced + 1
        if suffix[0] != expected_first or any(
            right != left + 1 for left, right in zip(suffix, suffix[1:], strict=False)
        ):
            raise TrajectoryExportError(
                "Source event_sequence suffix is not canonical and contiguous"
            )
        sequence_mode = "legacy_prefix_and_sequenced_suffix" if first_sequenced else "sequenced"
    else:
        sequence_mode = "legacy_prefix"

    normalized: list[TrajectoryEventEnvelope] = []
    disposition_counts: dict[str, int] = {
        "derived_summary": 0,
        "exact_evogen_event": 0,
        "intentionally_ignored": 0,
        "subject_only_raw_evidence": 0,
    }
    kind_counts: dict[str, int] = {}
    source_sequence_present = sum(event.event_sequence is not None for event in events)
    source_event_id_present = sum(_source_event_id(raw) is not None for raw in raw_records)
    for source_ordinal, (event, raw) in enumerate(zip(events, raw_records, strict=True)):
        row = dispositions[event.event_type]
        disposition = row.get("disposition")
        if not isinstance(disposition, str):
            raise TrajectoryExportError(
                f"Reviewed disposition for {event.event_type!r} is not a string"
            )
        disposition_counts[disposition] = disposition_counts.get(disposition, 0) + 1
        kind = row.get("evogen_kind")
        if disposition != "exact_evogen_event":
            continue
        if not isinstance(kind, str):
            raise TrajectoryExportError(f"Exact reviewed event {event.event_type!r} has no kind")
        normalized.append(
            _normal_event(
                event,
                raw,
                generation_id=generation.generation_id,
                scenario_id=scenario_id,
                normalized_sequence=len(normalized),
                kind=kind,
                source_ordinal=source_ordinal,
            )
        )
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    raw_digest = digest_bytes(source_bytes)
    trajectory_bytes = b"".join(
        _canonical(event.model_dump(mode="json")) + b"\n" for event in normalized
    )
    trajectory_digest = digest_bytes(trajectory_bytes)
    reviewed_digest = digest_bytes(_canonical(reviewed))
    generation_file_digest = digest_bytes(generation_bytes)
    capability_file_digest = digest_bytes(capability_bytes)
    bundle_id = digest_bytes(
        _canonical(
            {
                "capability_manifest_file_sha256": capability_file_digest,
                "generation_id": generation.generation_id,
                "generation_manifest_sha256": generation_file_digest,
                "scenario_id": scenario_id,
                "run_id": run_id,
                "source_sha256": raw_digest,
                "trajectory_sha256": trajectory_digest,
                "reviewed_disposition_sha256": reviewed_digest,
                "source_inventory_fingerprint": inventory.fingerprint,
            }
        )
    )
    manifest = ExportManifest(
        bundle_id=bundle_id,
        generation_id=generation.generation_id,
        capability_generation_id=capability.generation_id,
        capability_manifest_digest=capability_digest,
        generation_manifest_sha256=generation_file_digest,
        capability_manifest_file_sha256=capability_file_digest,
        generation_linkage="supplied_external_manifest",
        scenario_linkage=scenario_linkage,
        scenario_id=scenario_id,
        run_id=run_id,
        source=SourceManifest(
            file="raw-events.jsonl",
            sha256=raw_digest,
            record_count=len(events),
            event_types=sorted({event.event_type for event in events}),
            sequence_mode=sequence_mode,
            source_sequence_counts=FieldCounts(
                present=source_sequence_present,
                missing=len(events) - source_sequence_present,
            ),
            source_event_id_counts=FieldCounts(
                present=source_event_id_present,
                missing=len(events) - source_event_id_present,
            ),
        ),
        trajectory=TrajectoryManifest(
            file="trajectory.jsonl",
            sha256=trajectory_digest,
            event_count=len(normalized),
            kind_counts=dict(sorted(kind_counts.items())),
            withheld_projection_kinds=["binding", "dispatch"],
        ),
        reviewed_dispositions=ReviewedDispositionManifest(
            authority="docs/reconstruction/session_event_dispositions.json",
            fingerprint=reviewed_digest,
            source_inventory_fingerprint=inventory.fingerprint,
            source_event_type_count=len(inventory.event_types),
            source_record_count=len(inventory.records),
            disposition_counts=dict(sorted(disposition_counts.items())),
        ),
    )
    destination, temp = _safe_output(Path(output))
    try:
        _write_bytes(temp / "raw-events.jsonl", source_bytes)
        _write_bytes(temp / "trajectory.jsonl", trajectory_bytes)
        _write_bytes(temp / "manifest.json", _canonical(manifest.model_dump(mode="json")) + b"\n")
        directory_fd = os.open(temp, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.replace(temp, destination)
        parent_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except (OSError, ValueError) as exc:
        try:
            for child in temp.iterdir():
                child.unlink()
            temp.rmdir()
        except OSError:
            pass
        raise TrajectoryExportError(f"Cannot publish trajectory bundle: {exc}") from exc
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument(
        "--generation-manifest", dest="generation", type=Path, required=True
    )
    parser.add_argument(
        "--capability-manifest", dest="capability", type=Path, required=True
    )
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        manifest = export_trajectory(
            args.events, args.generation, args.capability, args.scenario_id, args.output
        )
    except (OSError, TrajectoryExportError) as exc:
        parser.error(str(exc))
    print(manifest.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
