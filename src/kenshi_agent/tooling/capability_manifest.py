"""Generate KAE's EvoGen capability manifest from existing authorities.

This module is deliberately a projection, not another operation registry.  The
operation definitions own action semantics; native capability JSON owns the
producer surface; protocol/schema files own representation; continuity,
outcome, and recovery modules own their respective rows.  The proof ledger is
consulted only for evidence state and limits.

The output is the exact generic EvoGen shape and imports no EvoGen package, so
the runtime remains independently installable.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..affordances import AFFORDANCE_ADAPTERS, affordance_operation_kinds
from ..config import CAPABILITY_DESCRIPTOR as PLANNER_DESCRIPTOR
from ..continuity_service import CAPABILITY_DESCRIPTOR as CONTINUITY_DESCRIPTOR
from ..core.capability import CapabilityDescriptor
from ..core.protocol_2 import CAPABILITY_DESCRIPTORS
from ..final_safe_state import CAPABILITY_DESCRIPTOR as RECOVERY_DESCRIPTOR
from ..operation_definitions import OPERATION_DEFINITION_LIST, OperationDefinition
from ..outcome_recorder import CAPABILITY_DESCRIPTOR as OUTCOME_DESCRIPTOR
from .native_contract_export import load_gameplay_capabilities
from .native_provenance import CAPABILITY_MANIFEST
from .path_validation import validate_repository_file

ROOT = Path(__file__).resolve().parents[3]
INITIAL_IMPORT_GENERATION = "kae-g13-import"
PROOF_LEDGER = ROOT / "docs" / "reconstruction" / "interaction_proof_status.json"
GENERATED_PATH = ROOT / "docs" / "generated" / "CAPABILITY_MANIFEST.json"
PROOF_STATUSES = frozenset(
    {
        "source_proven", "unit_proven", "live_proven", "unproven", "withheld",
        "unknown", "unsupported",
    }
)
PROVEN_PROOF_STATUSES = frozenset({"source_proven", "unit_proven", "live_proven"})
PROOF_TOP_LEVEL_FIXED_FIELDS = frozenset(
    {"schema_version", "stage", "slice", "authority", "captured_at_utc", "note", "entries"}
)


EvidenceState = Literal[
    "proven", "absent", "unproven", "withheld", "unknown", "unsupported"
]
ProofClass = Literal["synthetic", "portable", "replay", "live"]
CapabilityKind = Literal[
    "sensing", "representation", "memory", "action", "verification", "recovery"
]

_CAPABILITY_KINDS: dict[str, CapabilityKind] = {
    "sensing": "sensing",
    "representation": "representation",
    "memory": "memory",
    "action": "action",
    "verification": "verification",
    "recovery": "recovery",
}
_EVIDENCE_STATES: dict[str, EvidenceState] = {
    "proven": "proven",
    "absent": "absent",
    "unproven": "unproven",
    "withheld": "withheld",
    "unknown": "unknown",
    "unsupported": "unsupported",
}
_PROOF_CLASSES: dict[str, ProofClass] = {
    "synthetic": "synthetic",
    "portable": "portable",
    "replay": "replay",
    "live": "live",
}


def _capability_kind(value: str) -> CapabilityKind:
    try:
        return _CAPABILITY_KINDS[value]
    except KeyError as exc:
        raise ValueError(f"unknown capability kind: {value!r}") from exc


def _evidence_state(value: str) -> EvidenceState:
    try:
        return _EVIDENCE_STATES[value]
    except KeyError as exc:
        raise ValueError(f"unknown evidence state: {value!r}") from exc


def _proof_class(value: str) -> ProofClass:
    try:
        return _PROOF_CLASSES[value]
    except KeyError as exc:
        raise ValueError(f"unknown proof class: {value!r}") from exc


class CapabilityEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_ref: str = Field(min_length=1)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_state: EvidenceState
    proof_class: ProofClass | None

    @field_validator("authority_ref")
    @classmethod
    def authority_ref_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("authority_ref must be nonblank")
        return value

    @model_validator(mode="after")
    def reference_cannot_be_absent(self) -> CapabilityEvidenceRef:
        if self.evidence_state == "absent":
            raise ValueError("absent evidence cannot carry a reference")
        if self.evidence_state == "proven" and self.proof_class is None:
            raise ValueError("proven evidence references require proof_class")
        if self.evidence_state != "proven" and self.proof_class is not None:
            raise ValueError("non-proven evidence references cannot carry proof_class")
        return self


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    kind: CapabilityKind
    semantic_effects: list[str] = Field(default_factory=list, min_length=1)
    owner_component: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    applicability: str = Field(min_length=1)
    completion_evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[CapabilityEvidenceRef] = Field(default_factory=list)
    implementation_ref: str = Field(min_length=1)
    evidence_state: EvidenceState
    proof_class: ProofClass | None
    introduced_generation: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @field_validator(
        "name", "purpose", "owner_component", "applicability", "implementation_ref",
        "introduced_generation",
    )
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("capability text fields must be nonblank")
        return value

    @field_validator("semantic_effects")
    @classmethod
    def semantic_effects_are_nonempty(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("semantic_effects must contain at least one nonblank value")
        return value

    @field_validator("completion_evidence", "limitations")
    @classmethod
    def optional_text_lists_are_nonblank(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("capability text lists must contain nonblank values")
        return value

    @model_validator(mode="after")
    def coherent_evidence(self) -> Capability:
        if self.evidence_state == "proven" and self.proof_class is None:
            raise ValueError("proven capability requires proof_class")
        if self.evidence_state != "proven" and self.proof_class is not None:
            raise ValueError("non-proven capability cannot carry proof_class")
        if self.evidence_state == "proven" and not self.evidence_refs:
            raise ValueError("proven capability requires evidence_refs")
        if self.evidence_state == "absent" and self.evidence_refs:
            raise ValueError("absent capability cannot carry evidence_refs")
        return self


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: list[Capability]

    @model_validator(mode="after")
    def unique_and_ordered(self) -> CapabilityManifest:
        names = [row.name for row in self.capabilities]
        if len(names) != len(set(names)):
            raise ValueError("capability manifest contains duplicate names")
        if names != sorted(names):
            raise ValueError("capability manifest capabilities must be sorted by name")
        return self


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    return digest_bytes(canonical_json(value))


def _resolve_authority_path(raw_ref: str, key: str) -> Path:
    return validate_repository_file(
        raw_ref,
        root=ROOT,
        label=f"proof ledger artifact ref for {key!r}",
        require_tracked=True,
    )


def _validate_artifact_refs(refs: Any, key: str, status: str) -> list[str]:
    if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
        raise ValueError(f"proof ledger artifact_refs are malformed for {key!r}")
    if status in PROVEN_PROOF_STATUSES and not refs:
        raise ValueError(f"proven proof row {key!r} requires artifact_refs")
    if len(refs) != len(set(refs)):
        raise ValueError(f"proof ledger artifact_refs contain duplicates for {key!r}")
    for raw_ref in refs:
        _resolve_authority_path(raw_ref, key)
    return sorted(refs)


def _validate_proof_block(block: Any, key: str) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise ValueError(f"proof ledger block {key!r} is malformed")
    status = block.get("proof_status")
    if status not in PROOF_STATUSES:
        raise ValueError(f"proof status is unknown for {key!r}")
    for field in ("evidence", "live_evidence", "research"):
        value = block.get(field, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise ValueError(f"proof ledger {field} is malformed for {key!r}")
    if status == "live_proven" and not block.get("evidence"):
        raise ValueError(f"live proof for {key!r} requires retained evidence refs")
    refs = _validate_artifact_refs(block.get("artifact_refs", []), key, status)
    normalized = dict(block)
    normalized["artifact_refs"] = refs
    for field in ("evidence", "live_evidence", "research"):
        normalized[field] = sorted(block.get(field, []))
    return normalized


def _load_proof_ledger(path: Path | None = None) -> dict[str, Any]:
    path = PROOF_LEDGER if path is None else path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("proof ledger is unreadable") from exc
    if not isinstance(raw, dict):
        raise ValueError("proof ledger must be an object")
    return raw


def _proof_rows(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    path = PROOF_LEDGER if path is None else path
    raw = _load_proof_ledger(path)
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ValueError("proof ledger entries must be a list")
    rows: dict[str, list[dict[str, Any]]] = {}
    subcases: dict[str, set[str | None]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("operation_kind"), str):
            raise ValueError("proof ledger contains malformed entry")
        key = entry["operation_kind"]
        if key not in {definition.kind for definition in OPERATION_DEFINITION_LIST}:
            raise ValueError(f"proof ledger contains unknown operation {key!r}")
        subcase = entry.get("subcase")
        if subcase is not None and not isinstance(subcase, str):
            raise ValueError(f"proof ledger subcase is malformed for {key!r}")
        seen_subcases = subcases.setdefault(key, set())
        if subcase in seen_subcases:
            raise ValueError(
                f"proof ledger contains duplicate operation subcase {key!r}/{subcase!r}"
            )
        seen_subcases.add(subcase)
        normalized = _validate_proof_block(entry, key)
        rows.setdefault(key, []).append(normalized)
    return rows


def _top_level_proof_rows(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    path = PROOF_LEDGER if path is None else path
    raw = _load_proof_ledger(path)
    expected_keys = {
        descriptor.proof_key
        for descriptor in (PLANNER_DESCRIPTOR, *CAPABILITY_DESCRIPTORS)
        if descriptor.proof_key is not None
    }
    allowed = set(PROOF_TOP_LEVEL_FIXED_FIELDS) | expected_keys
    if set(raw) != allowed:
        raise ValueError("proof ledger contains unknown top-level fields")
    result: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(expected_keys):
        result[key] = [_validate_proof_block(raw[key], key)]
    return result


def proof_authority_projection(path: Path | None = None) -> dict[str, Any]:
    """Return proof authority content with only presentation ordering removed."""

    path = PROOF_LEDGER if path is None else path
    raw = _load_proof_ledger(path)
    entries = [entry for rows in _proof_rows(path).values() for entry in rows]
    top = _top_level_proof_rows(path)
    fixed = {
        key: sorted(raw[key]) if key == "note" else raw[key]
        for key in PROOF_TOP_LEVEL_FIXED_FIELDS
        if key != "entries"
    }
    projected_entries = []
    for entry in entries:
        refs = entry.get("artifact_refs", [])
        projected = dict(entry)
        projected["artifact_content_digests"] = {
            ref: digest_bytes(_resolve_authority_path(ref, entry["operation_kind"]).read_bytes())
            for ref in refs
        }
        projected_entries.append(projected)
    def project_block(block: dict[str, Any], key: str) -> dict[str, Any]:
        projected = dict(block)
        projected["artifact_content_digests"] = {
            ref: digest_bytes(_resolve_authority_path(ref, key).read_bytes())
            for ref in block.get("artifact_refs", [])
        }
        return projected

    return {
        "fixed": fixed,
        "top_level": {
            key: project_block(top[key][0], key)
            for key in sorted(top)
        },
        "entries": sorted(
            projected_entries,
            key=lambda entry: (
                entry["operation_kind"],
                entry.get("subcase", ""),
                entry["proof_status"],
                tuple(entry.get("artifact_refs", [])),
            ),
        ),
    }


def capability_proof_digest(path: Path | None = None) -> str:
    return digest_json(proof_authority_projection(path))


def _evidence(
    entry: list[dict[str, Any]] | None,
) -> tuple[EvidenceState, ProofClass | None, list[CapabilityEvidenceRef], list[str]]:
    if entry is None:
        return "absent", None, [], []
    statuses = {row["proof_status"] for row in entry}
    state: EvidenceState
    proof: ProofClass | None
    # A mixed operation is offered only at the weakest shared evidence state;
    # one unproven subcase must not be certified by another live witness.
    if "withheld" in statuses:
        state, proof = "withheld", None
    elif "unproven" in statuses:
        state, proof = "unproven", None
    elif "unknown" in statuses:
        state, proof = "unknown", None
    elif "unsupported" in statuses:
        state, proof = "unsupported", None
    elif "live_proven" in statuses:
        state, proof = "proven", _proof_class(
            "live" if statuses == {"live_proven"} else "portable"
        )
    else:
        state, proof = "proven", _proof_class("portable")
    evidence: list[CapabilityEvidenceRef] = []
    for row in sorted(
        entry,
        key=lambda item: (
            item.get("subcase", ""),
            item["proof_status"],
            tuple(sorted(item.get("artifact_refs", []))),
        ),
    ):
        refs = sorted(row.get("artifact_refs", []))
        for raw_ref in refs:
            if not isinstance(raw_ref, str):
                raise ValueError("proof ledger artifact ref is not a string")
            resolved = _resolve_authority_path(raw_ref, row.get("operation_kind", "proof"))
            row_status = row["proof_status"]
            ref_state = (
                "proven"
                if row_status in {"source_proven", "unit_proven", "live_proven"}
                else row_status
            )
            ref_proof = (
                "live"
                if row_status == "live_proven"
                else "portable"
                if ref_state == "proven"
                else None
            )
            evidence.append(
                CapabilityEvidenceRef(
                    authority_ref=resolved.relative_to(ROOT).as_posix(),
                    content_digest=digest_bytes(resolved.read_bytes()),
                    evidence_state=_evidence_state(ref_state),
                    proof_class=_proof_class(ref_proof) if ref_proof is not None else None,
                )
            )
    if state == "proven" and not evidence:
        raise ValueError("proven proof row resolved without evidence refs")
    # Incident-specific notes remain in the referenced evidence artifact, not
    # in generic capability semantics.
    limits: list[str] = []
    return state, proof, evidence, limits


def _row(
    *,
    name: str,
    purpose: str,
    kind: CapabilityKind,
    owner_component: str,
    implementation_ref: str,
    generation_id: str,
    introduced_generation: str = INITIAL_IMPORT_GENERATION,
    semantic_effects: Iterable[str] = (),
    applicability: str = "Available only when its owning authority is present and fresh.",
    evidence_state: EvidenceState = "proven",
    proof_class: ProofClass | None = "portable",
    evidence_refs: Iterable[CapabilityEvidenceRef] = (),
    completion_evidence: Iterable[str] = (),
    limitations: Iterable[str] = (),
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> Capability:
    return Capability(
        name=name,
        purpose=purpose,
        kind=kind,
        owner_component=owner_component,
        semantic_effects=list(semantic_effects),
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema or {"type": "object"},
        applicability=applicability,
        completion_evidence=list(completion_evidence),
        evidence_refs=list(evidence_refs),
        implementation_ref=implementation_ref,
        evidence_state=evidence_state,
        proof_class=proof_class,
        introduced_generation=introduced_generation,
        limitations=list(limitations),
    )


def _operation_rows(
    generation_id: str,
    native_authority_path: Path,
    definitions: Iterable[OperationDefinition],
    proofs: dict[str, list[dict[str, Any]]],
) -> list[Capability]:
    introduced_generation = INITIAL_IMPORT_GENERATION
    rows: list[Capability] = []
    native = load_gameplay_capabilities(native_authority_path)
    advertised = set((*native.always, *native.conditional))
    adapter_by_operation: dict[str, list[Any]] = {}
    for adapter in AFFORDANCE_ADAPTERS:
        for kind in adapter.operation_kinds:
            adapter_by_operation.setdefault(kind, []).append(adapter)
    for definition in definitions:
        state, proof, evidence, limits = _evidence(proofs.get(definition.kind))
        missing = set(definition.missing_capabilities(advertised))
        if missing:
            state, proof = "unsupported", None
            limits = [
                "Required producer capability is unavailable; this operation is "
                "unsupported for the current authority."
            ]
        adapters = adapter_by_operation.get(definition.kind, [])
        applicability = (
            "Adapters: "
            + ", ".join(sorted(adapter.name for adapter in adapters))
            + ". "
            + " ".join(
                f"{adapter.name}: denominator={adapter.denominator} "
                f"boundary={adapter.completeness_boundary}"
                for adapter in sorted(adapters, key=lambda item: item.name)
            )
        )
        rows.append(
            _row(
                name=f"action.{definition.kind}",
                purpose=definition.summary,
                kind="action",
                owner_component="kenshi_agent.operation_definitions",
                implementation_ref=f"{definition.operation_type.__module__}.{definition.operation_type.__qualname__}",
                generation_id=generation_id,
                introduced_generation=introduced_generation,
                semantic_effects=(f"operation.{definition.kind}",),
                applicability=applicability,
                input_schema=definition.operation_type.model_json_schema(),
                output_schema={
                    "type": "object",
                    "properties": {"operation": {"const": definition.kind}},
                },
                evidence_state=state,
                proof_class=proof,
                evidence_refs=evidence,
                completion_evidence=[
                    (
                        f"Typed interaction completion milestone: "
                        f"{definition.interaction.completion_milestone.value}."
                        if definition.interaction is not None
                        else (
                            "Completion is resolved by the operation's typed "
                            "semantic interaction contract."
                        )
                    )
                ],
                limitations=limits,
            )
        )
    return rows


def _native_sensing_rows(
    generation_id: str,
    native_authority_path: Path,
) -> list[Capability]:
    introduced_generation = INITIAL_IMPORT_GENERATION
    try:
        authority = load_gameplay_capabilities(native_authority_path)
    except (OSError, ValueError) as exc:
        raise ValueError("native capability authority is unreadable") from exc
    rows: list[Capability] = []
    availability_by_name = {
        name: "always" for name in authority.always
    }
    availability_by_name.update({name: "conditional" for name in authority.conditional})
    for category, category_names in sorted(authority.categories.items()):
        if category == "action":
            continue
        for name in sorted(category_names):
            # Control names belong to action rows.  Repeating them as sensing
            # rows would create two semantic owners for one permission.
            rows.append(
                _row(
                    name=f"{category}.native.{name}",
                    purpose=f"Expose the engine capability channel {name}.",
                    kind=_capability_kind(category),
                    owner_component="native/KenshiAgentTelemetry/GameplayCapabilities.json",
                    implementation_ref="native_gameplay_capabilities.schema_version=2",
                    generation_id=generation_id,
                    introduced_generation=introduced_generation,
                    semantic_effects=(
                        f"native.{availability_by_name[name]}.{name}",
                    ),
                    applicability=(
                        "Advertised by the native producer when its condition is "
                        "satisfied."
                    ),
                    evidence_state="unproven",
                    proof_class=None,
                )
            )
    return rows


def _owner_row(
    descriptor: CapabilityDescriptor,
    generation_id: str,
    introduced_generation: str,
) -> Capability:
    """Project one descriptor owned by the module that defines it."""

    return _row(
        generation_id=generation_id,
        introduced_generation=introduced_generation,
        applicability="Available only when its owning service is present and fresh.",
        evidence_state="unproven",
        proof_class=None,
        **descriptor.as_projection(),
    )


def _top_level_rows(
    generation_id: str,
    introduced_generation: str,
    proofs: dict[str, list[dict[str, Any]]],
) -> list[Capability]:
    rows = []
    for descriptor in (PLANNER_DESCRIPTOR, *CAPABILITY_DESCRIPTORS):
        if descriptor.proof_key is None:
            raise ValueError(f"top-level descriptor {descriptor.name!r} lacks proof_key")
        key = descriptor.proof_key
        state, proof, refs, limits = _evidence(proofs[key])
        projection = descriptor.as_projection()
        projection.pop("proof_key", None)
        rows.append(_row(
            generation_id=generation_id,
            introduced_generation=introduced_generation,
            evidence_state=state,
            proof_class=proof,
            evidence_refs=refs,
            applicability="Available when the typed representation authority is present and fresh.",
            limitations=limits,
            **projection,
        ))
    return rows


def build_capability_manifest(
    generation_id: str,
    *,
    native_authority_path: Path | None = None,
    proof_ledger_path: Path | None = None,
) -> CapabilityManifest:
    if not isinstance(generation_id, str) or len(generation_id) != 64 or any(
        c not in "0123456789abcdef" for c in generation_id
    ):
        raise ValueError("generation_id must be a lowercase SHA-256 digest")
    registry_kinds = {definition.kind for definition in OPERATION_DEFINITION_LIST}
    adapter_kinds = set(affordance_operation_kinds())
    if adapter_kinds != registry_kinds:
        raise ValueError("affordance adapter operation inventory does not match operation registry")
    descriptors = (PLANNER_DESCRIPTOR, *CAPABILITY_DESCRIPTORS)
    descriptor_names = [descriptor.name for descriptor in descriptors]
    proof_keys = [descriptor.proof_key for descriptor in descriptors]
    if len(descriptor_names) != len(set(descriptor_names)):
        raise ValueError("capability descriptors contain duplicate names")
    if any(key is None for key in proof_keys) or len(proof_keys) != len(set(proof_keys)):
        raise ValueError("capability descriptors contain duplicate or missing proof keys")
    native_authority_path = (
        CAPABILITY_MANIFEST if native_authority_path is None else native_authority_path
    )
    proof_ledger_path = PROOF_LEDGER if proof_ledger_path is None else proof_ledger_path
    operation_proofs = _proof_rows(proof_ledger_path)
    top_level_proofs = _top_level_proof_rows(proof_ledger_path)
    rows = _operation_rows(
        generation_id, native_authority_path, OPERATION_DEFINITION_LIST, operation_proofs
    )
    rows.extend(_native_sensing_rows(generation_id, native_authority_path))
    rows.extend(
        _top_level_rows(generation_id, INITIAL_IMPORT_GENERATION, top_level_proofs)
    )
    rows.append(_owner_row(CONTINUITY_DESCRIPTOR, generation_id, INITIAL_IMPORT_GENERATION))
    rows.append(_owner_row(OUTCOME_DESCRIPTOR, generation_id, INITIAL_IMPORT_GENERATION))
    rows.append(_owner_row(RECOVERY_DESCRIPTOR, generation_id, INITIAL_IMPORT_GENERATION))
    return CapabilityManifest(
        generation_id=generation_id,
        capabilities=sorted(rows, key=lambda row: row.name),
    )


def capability_manifest_bytes(manifest: CapabilityManifest) -> bytes:
    return canonical_json(manifest.model_dump(mode="json")) + b"\n"


def capability_manifest_digest(manifest: CapabilityManifest) -> str:
    # EvoGen hashes the canonical JSON model bytes.  The publication newline
    # is framing, not part of the content-addressed model identity.
    return digest_bytes(canonical_json(manifest.model_dump(mode="json")))


def write_capability_manifest(
    manifest: CapabilityManifest,
    output: str | Path = GENERATED_PATH,
) -> Path:
    destination = Path(output)
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    destination = Path(os.path.abspath(destination.expanduser()))
    for component in (destination, *destination.parents):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError("capability manifest output path cannot be inspected safely") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("capability manifest output path cannot contain a symlink")
        if component == destination and not stat.S_ISREG(metadata.st_mode):
            raise ValueError("existing capability manifest output must be a regular file")
    if not destination.parent.is_dir():
        raise ValueError("capability manifest output directory does not exist")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(capability_manifest_bytes(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build_capability_manifest(args.generation_id)
        write_capability_manifest(manifest, args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(capability_manifest_digest(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
