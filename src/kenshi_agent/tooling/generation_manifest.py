"""Deterministic, redacted provenance for one exact KAE generation.

The serialized top level is intentionally compatible with EvoGen's strict
``GenerationManifest`` without importing EvoGen. KAE-specific evidence lives
under ``metadata`` and remains typed here. User text, credentials, host paths,
and volatile runtime observations never cross this boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..affordances import AFFORDANCE_ADAPTERS
from ..config import AppConfig, load_config
from ..core.lifecycle import EVIDENCE_SEMANTICS_VERSION
from ..core.scenario import ScenarioAttestation
from ..core.telemetry import ScenarioIdentity
from ..memory import SCHEMA_VERSION as MEMORY_SCHEMA_VERSION
from ..operation_definitions import OPERATION_DEFINITION_LIST, OperationDefinition
from ..planners.base import render_planner_instructions
from .authored_starts import AuthoredGameStart, load_authored_starts_bundle
from .capability_manifest import (
    build_capability_manifest,
    capability_manifest_digest,
    capability_proof_digest,
)
from .native_contract_export import load_gameplay_capabilities
from .native_provenance import (
    CAPABILITY_MANIFEST,
    GENERATED_HEADER,
    declared_protocol_version,
)
from .native_provenance import SOURCE as NATIVE_SOURCE
from .research_evidence import ExecutableIdentity, ResearchCallSites

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STAGED_DLL = (
    ROOT / "staging" / "KenshiAgentTelemetry" / "KenshiAgentTelemetry.dll"
)
DEFAULT_OPERATION_DOC = ROOT / "docs" / "generated" / "OPERATION_DEFINITIONS.md"
DEFAULT_PROOF_LEDGER = ROOT / "docs" / "reconstruction" / "interaction_proof_status.json"
CAPABILITY_AUTHORITY_PATH = CAPABILITY_MANIFEST
TELEMETRY_AUTHORITY_PATH = ROOT / "src" / "kenshi_agent" / "core" / "telemetry.py"
PROTOCOL_AUTHORITY_PATH = ROOT / "src" / "kenshi_agent" / "core" / "protocol_2.py"
CONTINUITY_AUTHORITY_PATH = ROOT / "src" / "kenshi_agent" / "continuity_service.py"
OUTCOME_AUTHORITY_PATH = ROOT / "src" / "kenshi_agent" / "outcome_recorder.py"
RECOVERY_AUTHORITY_PATH = ROOT / "src" / "kenshi_agent" / "final_safe_state.py"
DEFAULT_TOPOLOGY = (
    ROOT / "game_sources" / "research" / "player_topology" / "call_sites.json"
)

# Match the opening reference only so nested defaults are scanned independently:
# ``${OUTER:-${INNER}}`` must expose both names to the allowlist.
BRACED_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")
PLAIN_ENV_PATTERN = re.compile(r"\$(?!\{)([A-Za-z_][A-Za-z0-9_]*)")
ALLOWED_ENV = frozenset(
    {
        "KENSHI_AGENT_ADVISOR_CADENCE_STEPS",
        "KENSHI_AGENT_ADVISOR_MODEL",
        "KENSHI_AGENT_ADVISOR_REASONING_EFFORT",
        "KENSHI_AGENT_MODEL",
        "KENSHI_AGENT_OPENROUTER_MODEL",
        "KENSHI_AGENT_OPENROUTER_SORT",
        "KENSHI_AGENT_REASONING_EFFORT",
        "KENSHI_AGENT_TELEMETRY_DIR",
        "LOCALAPPDATA",
    }
)
_CREDENTIAL_TOKENS = ("credential", "key", "password", "secret", "token")
_MODEL_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._-]*(?::[A-Za-z0-9._-]+)?)?$"
)


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactEvidence(ManifestModel):
    state: Literal["present", "absent", "unreadable"]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def hash_matches_state(self) -> ArtifactEvidence:
        if (self.state == "present") != (self.sha256 is not None):
            raise ValueError("present evidence requires a hash; other states forbid one")
        return self


class GitEvidence(ManifestModel):
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dirty: bool
    material_dirty_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def dirty_state_has_one_digest(self) -> GitEvidence:
        if self.dirty != (self.material_dirty_sha256 is not None):
            raise ValueError("dirty Git evidence requires exactly one material digest")
        return self


class ScenarioEvidence(ManifestModel):
    state: Literal["present", "absent"]
    authority: Literal["declared", "configured_attestation", "verified_fixture"] | None = None
    identity: ScenarioIdentity | None = None
    fixture_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    managed_save_name: str | None = None

    @model_validator(mode="after")
    def presence_is_complete(self) -> ScenarioEvidence:
        if self.state == "absent":
            if any(
                value is not None
                for value in (
                    self.authority,
                    self.identity,
                    self.fixture_sha256,
                    self.managed_save_name,
                )
            ):
                raise ValueError("absent scenario evidence cannot carry an identity")
        elif self.authority is None or self.identity is None:
            raise ValueError("present scenario evidence requires authority and identity")
        return self


class AuthoredStartEvidence(ManifestModel):
    state: Literal["present", "absent"]
    start: AuthoredGameStart | None = None
    bundle_manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    bundle_mod_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def presence_is_complete(self) -> AuthoredStartEvidence:
        present_values = (
            self.start,
            self.bundle_manifest_sha256,
            self.bundle_mod_sha256,
        )
        if self.state == "present" and any(value is None for value in present_values):
            raise ValueError("present authored-start evidence is incomplete")
        if self.state == "absent" and any(value is not None for value in present_values):
            raise ValueError("absent authored-start evidence cannot carry identity")
        return self


class KenshiVersionEvidence(ManifestModel):
    repository_target: ExecutableIdentity
    observed_executable: ArtifactEvidence
    observed_matches_repository_target: bool | None = None

    @model_validator(mode="after")
    def match_requires_observation(self) -> KenshiVersionEvidence:
        if self.observed_executable.state == "present":
            if self.observed_matches_repository_target is None:
                raise ValueError("present executable evidence requires a match disposition")
        elif self.observed_matches_repository_target is not None:
            raise ValueError("missing executable evidence cannot claim a match disposition")
        return self


class NativeEvidence(ManifestModel):
    source: ArtifactEvidence
    generated_capability_header: ArtifactEvidence
    built: ArtifactEvidence
    staged: ArtifactEvidence
    installed: ArtifactEvidence
    built_matches_installed: bool | None = None
    staged_matches_installed: bool | None = None


class ProtocolEvidence(ManifestModel):
    versions: dict[str, str]
    schema_digests: dict[str, str]


class CapabilityManifestAuthority(ManifestModel):
    kind: Literal["generated_capability_manifest"] = "generated_capability_manifest"
    scope: Literal["subject"] = "subject"
    lifecycle: Literal["permanent"] = "permanent"


class CapabilityAuthorityDigests(ManifestModel):
    native: str = Field(pattern=r"^[0-9a-f]{64}$")
    operations: str = Field(pattern=r"^[0-9a-f]{64}$")
    affordances: str = Field(pattern=r"^[0-9a-f]{64}$")
    telemetry: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol: str = Field(pattern=r"^[0-9a-f]{64}$")
    continuity: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: str = Field(pattern=r"^[0-9a-f]{64}$")
    recovery: str = Field(pattern=r"^[0-9a-f]{64}$")
    proof: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationMetadata(ManifestModel):
    manifest_schema_version: Literal[1] = 1
    config_redaction_schema_version: Literal[1] = 1
    git: GitEvidence
    protocol: ProtocolEvidence
    strategy_corpus: ArtifactEvidence
    scenario: ScenarioEvidence
    authored_start: AuthoredStartEvidence
    kenshi: KenshiVersionEvidence
    native: NativeEvidence
    memory_schema_version: int = Field(ge=1)
    operation_count: int = Field(ge=1)
    capability_authority_digests: CapabilityAuthorityDigests
    capability_manifest_authority: CapabilityManifestAuthority = Field(
        default_factory=CapabilityManifestAuthority
    )


class GenerationManifest(ManifestModel):
    """A KAE-owned model with EvoGen's exact serialized top-level fields."""

    generation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_generation_id: str | None = None
    created_at: datetime
    subject: Literal["kenshi-agent-env"] = "kenshi-agent-env"
    source_ref: str
    capability_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_digests: dict[str, str]
    models: dict[str, str]
    prompts: dict[str, str]
    config: dict[str, str]
    metadata: GenerationMetadata


def canonical_json(value: Any) -> bytes:
    """Return the one compact, key-sorted UTF-8 encoding used for identity."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_evidence(path: Path) -> ArtifactEvidence:
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return ArtifactEvidence(state="absent")
    except OSError:
        return ArtifactEvidence(state="unreadable")
    if not stat.S_ISREG(metadata.st_mode):
        return ArtifactEvidence(state="absent")
    try:
        return ArtifactEvidence(state="present", sha256=_hash_file(path))
    except FileNotFoundError:
        # The file disappeared between stat and read. Its bytes are absent;
        # callers can repeat after host state settles.
        return ArtifactEvidence(state="absent")
    except OSError:
        return ArtifactEvidence(state="unreadable")


def _lexical_absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def _validate_output_path(path: Path) -> None:
    """Forbid symlink traversal before either fingerprinting or publication."""

    for component in (path, *path.parents):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError("manifest output path cannot be inspected safely") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("manifest output path cannot contain a symlink")
        if component == path and not stat.S_ISREG(metadata.st_mode):
            raise ValueError("existing manifest output must be a regular file")
        if component == component.parent:
            break


def _required_file_digest(path: Path, label: str) -> str:
    evidence = _file_evidence(path)
    if evidence.state != "present" or evidence.sha256 is None:
        raise ValueError(f"required {label} is {evidence.state}")
    return evidence.sha256


def _git_text(*args: str) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _git_bytes(*args: str) -> bytes:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _output_repo_path(output: Path) -> str | None:
    try:
        return output.relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _untracked_content(path: Path) -> bytes:
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode):
            return b"symlink\0" + os.readlink(path).encode("utf-8", errors="surrogateescape")
        if stat.S_ISREG(metadata.st_mode):
            return path.read_bytes()
        return b"<non-regular>"
    except FileNotFoundError:
        return b"<missing>"
    except OSError:
        return b"<unreadable>"


def _git_evidence(output: Path) -> GitEvidence:
    """Fingerprint final tracked and untracked material without emitting paths."""

    commit = _git_text("rev-parse", "HEAD").strip()
    output_relative = _output_repo_path(output)

    diff_args = ["diff", "--binary", "--no-ext-diff", "HEAD", "--", "."]
    if output_relative is not None:
        diff_args.append(f":(exclude,literal){output_relative}")
    tracked_patch = _git_bytes(*diff_args)

    untracked_records: list[dict[str, str]] = []
    untracked = _git_bytes("ls-files", "--others", "--exclude-standard", "-z")
    for raw_name in filter(None, untracked.split(b"\0")):
        name = raw_name.decode("utf-8", errors="surrogateescape")
        if name == output_relative:
            continue
        untracked_records.append(
            {
                "content_sha256": digest_bytes(_untracked_content(ROOT / name)),
                "path_sha256": digest_bytes(raw_name),
            }
        )
    untracked_records.sort(key=lambda record: record["path_sha256"])

    dirty_material: dict[str, Any] = {}
    if tracked_patch:
        dirty_material["tracked_patch_sha256"] = digest_bytes(tracked_patch)
    if untracked_records:
        dirty_material["untracked"] = untracked_records
    dirty_digest = digest_json(dirty_material) if dirty_material else None
    return GitEvidence(
        commit=commit,
        dirty=dirty_digest is not None,
        material_dirty_sha256=dirty_digest,
    )


def _validate_env_references(raw: Any) -> None:
    """Reject secret-bearing or unreviewed config interpolation before expansion."""

    references: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            references.update(match.group(1) for match in BRACED_ENV_PATTERN.finditer(value))
            references.update(match.group(1) for match in PLAIN_ENV_PATTERN.finditer(value))
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)

    walk(raw)
    credentials = {
        name
        for name in references
        if any(token in name.casefold() for token in _CREDENTIAL_TOKENS)
    }
    if credentials:
        raise ValueError(
            "credential environment references are forbidden: "
            + ", ".join(sorted(credentials))
        )
    unknown = references - ALLOWED_ENV
    if unknown:
        raise ValueError(
            "unknown environment references are forbidden: " + ", ".join(sorted(unknown))
        )


def _sanitized_config_value(value: Any) -> Any:
    if isinstance(value, Path):
        # Mutable telemetry, memory, and output locations are run state, not a
        # generation. Prompt and corpus bytes have their own evidence channels.
        return {"kind": "path"}
    if isinstance(value, BaseModel):
        return _sanitized_config_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {
            str(key): _sanitized_config_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_sanitized_config_value(item) for item in value),
            key=canonical_json,
        )
    if isinstance(value, (list, tuple)):
        return [_sanitized_config_value(item) for item in value]
    if isinstance(value, Enum):
        return _sanitized_config_value(value.value)
    if isinstance(value, str):
        # Hash all free-form strings. Selected model IDs are disclosed only by
        # the reviewed ``models`` projection below.
        return {
            "kind": "text",
            "length": len(value),
            "sha256": digest_bytes(value.encode("utf-8")),
        }
    return value


def effective_config_digest(config: AppConfig) -> str:
    projection = config.model_dump(mode="python")
    runtime = projection.get("runtime")
    if isinstance(runtime, dict):
        # Stable scenario identity has its own typed channel. An attestation's
        # timestamp, session ID, sequence, and observed state are not generation
        # identity and must not leak back through the config digest.
        runtime["scenario"] = None
        runtime["scenario_attestation"] = None
    return digest_json(_sanitized_config_value(projection))


def _callable_identity(value: Callable[..., Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "module": getattr(value, "__module__", type(value).__module__),
        "qualname": getattr(value, "__qualname__", type(value).__qualname__),
    }
    defaults = getattr(value, "__defaults__", None)
    if defaults:
        identity["defaults"] = _normalize_operation_value(defaults)
    keyword_defaults = getattr(value, "__kwdefaults__", None)
    if keyword_defaults:
        identity["keyword_defaults"] = _normalize_operation_value(keyword_defaults)
    closure = getattr(value, "__closure__", None)
    freevars = getattr(getattr(value, "__code__", None), "co_freevars", ())
    if closure and freevars:
        identity["closure"] = {
            name: _normalize_operation_value(cell.cell_contents)
            for name, cell in zip(freevars, closure, strict=True)
        }
    return identity


def _normalize_operation_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, type) and issubclass(value, BaseModel):
        return {
            "model": f"{value.__module__}.{value.__qualname__}",
            "schema": _normalize_operation_value(value.model_json_schema()),
        }
    if isinstance(value, Enum):
        return _normalize_operation_value(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_operation_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_normalize_operation_value(item) for item in value),
            key=canonical_json,
        )
    if isinstance(value, (list, tuple)):
        return [_normalize_operation_value(item) for item in value]
    if isinstance(value, BaseModel):
        return _normalize_operation_value(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _normalize_operation_value(getattr(value, item.name))
            for item in fields(value)
        }
    if callable(value):
        return _callable_identity(value)
    raise TypeError(f"operation definition contains unsupported value {type(value).__name__}")


def operation_projection(definition: OperationDefinition) -> dict[str, Any]:
    return {
        item.name: _normalize_operation_value(getattr(definition, item.name))
        for item in fields(definition)
    }


def operation_registry_digest(
    definitions: Iterable[OperationDefinition] | None = None,
) -> str:
    selected = OPERATION_DEFINITION_LIST if definitions is None else definitions
    rows = sorted(
        (operation_projection(definition) for definition in selected),
        key=lambda row: cast(str, row["kind"]),
    )
    return digest_json(rows)


def capability_native_digest(path: Path | None = None) -> str:
    authority = load_gameplay_capabilities(CAPABILITY_AUTHORITY_PATH if path is None else path)
    return digest_json(
        sorted(
            (availability, name, category)
            for availability, names in (
                ("always", authority.always),
                ("conditional", authority.conditional),
            )
            for name in names
            for category, category_names in authority.categories.items()
            if name in category_names
        )
    )


def _scenario_evidence(
    attestation_path: Path | None,
    config: AppConfig,
) -> ScenarioEvidence:
    if attestation_path is not None:
        from .scenario_fixtures import load_verified_scenario_attestation

        try:
            attestation = load_verified_scenario_attestation(attestation_path)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("scenario attestation or exact fixture is invalid") from exc
        return ScenarioEvidence(
            state="present",
            authority="verified_fixture",
            identity=attestation.scenario,
            fixture_sha256=attestation.fixture_digest,
            managed_save_name=attestation.managed_save_name,
        )

    configured_attestation: ScenarioAttestation | None = config.runtime.scenario_attestation
    if configured_attestation is not None:
        return ScenarioEvidence(
            state="present",
            authority="configured_attestation",
            identity=configured_attestation.scenario,
            fixture_sha256=configured_attestation.fixture_digest,
            managed_save_name=configured_attestation.managed_save_name,
        )
    if config.runtime.scenario is not None:
        return ScenarioEvidence(
            state="present",
            authority="declared",
            identity=config.runtime.scenario,
        )
    return ScenarioEvidence(state="absent")


def _authored_start_evidence(start_id: str | None) -> AuthoredStartEvidence:
    if start_id is None:
        return AuthoredStartEvidence(state="absent")
    bundle = load_authored_starts_bundle()
    start = next(
        (candidate for candidate in bundle.manifest.starts if candidate.start_id == start_id),
        None,
    )
    if start is None:
        raise ValueError(f"unknown authored Game Start ID: {start_id!r}")
    return AuthoredStartEvidence(
        state="present",
        start=start,
        bundle_manifest_sha256=digest_json(bundle.manifest.model_dump(mode="json")),
        bundle_mod_sha256=bundle.manifest.mod.sha256,
    )


def _kenshi_evidence(executable: Path | None) -> KenshiVersionEvidence:
    try:
        call_sites = ResearchCallSites.model_validate_json(
            DEFAULT_TOPOLOGY.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError("Kenshi version authority is unreadable or invalid") from exc
    observed = (
        _file_evidence(executable)
        if executable is not None
        else ArtifactEvidence(state="absent")
    )
    matches = (
        observed.sha256 == call_sites.executable.sha256
        if observed.state == "present"
        else None
    )
    return KenshiVersionEvidence(
        repository_target=call_sites.executable,
        observed_executable=observed,
        observed_matches_repository_target=matches,
    )


def _native_evidence(
    *,
    built: Path | None,
    staged: Path | None,
    installed: Path | None,
) -> NativeEvidence:
    built_evidence = _file_evidence(built) if built else ArtifactEvidence(state="absent")
    staged_evidence = _file_evidence(staged or DEFAULT_STAGED_DLL)
    installed_evidence = (
        _file_evidence(installed) if installed else ArtifactEvidence(state="absent")
    )

    def matches(left: ArtifactEvidence, right: ArtifactEvidence) -> bool | None:
        if left.state != "present" or right.state != "present":
            return None
        return left.sha256 == right.sha256

    return NativeEvidence(
        source=_file_evidence(NATIVE_SOURCE),
        generated_capability_header=_file_evidence(GENERATED_HEADER),
        built=built_evidence,
        staged=staged_evidence,
        installed=installed_evidence,
        built_matches_installed=matches(built_evidence, installed_evidence),
        staged_matches_installed=matches(staged_evidence, installed_evidence),
    )


def _schema_constant(
    schemas: Mapping[str, dict[str, Any]],
    schema_name: str,
    field_name: str,
) -> str:
    try:
        field = schemas[schema_name]["properties"][field_name]
        value = field["const"] if "const" in field else field["default"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"schema {schema_name!r} has no typed {field_name!r} constant"
        ) from exc
    return str(value)


def _protocol_evidence(capability_manifest: dict[str, Any]) -> ProtocolEvidence:
    from .schema_documents import base_schema_documents

    schemas = base_schema_documents()
    schemas["generation_manifest.schema.json"] = GenerationManifest.model_json_schema()
    schema_digests = {
        name.removesuffix(".schema.json"): digest_json(document)
        for name, document in sorted(schemas.items())
    }
    capability_schema = capability_manifest.get("schema_version")
    if not isinstance(capability_schema, int):
        raise ValueError("native capability manifest has no integer schema_version")
    versions = {
        "evidence_semantics": str(EVIDENCE_SEMANTICS_VERSION),
        "generation_manifest": "1",
        "memory": str(MEMORY_SCHEMA_VERSION),
        "native_command_request": _schema_constant(
            schemas,
            "native_command_request.schema.json",
            "schema_version",
        ),
        "native_gameplay_capabilities": str(capability_schema),
        "native_source_protocol": declared_protocol_version(),
        "protocol_2_world_model": _schema_constant(
            schemas,
            "protocol_2_world_model.schema.json",
            "protocol_version",
        ),
        "runtime_plan": _schema_constant(
            schemas,
            "runtime_plan.schema.json",
            "schema_version",
        ),
        "runtime_plan_patch": _schema_constant(
            schemas,
            "runtime_plan_patch.schema.json",
            "schema_version",
        ),
        "telemetry": _schema_constant(
            schemas,
            "telemetry.schema.json",
            "protocol_version",
        ),
    }
    return ProtocolEvidence(versions=versions, schema_digests=schema_digests)


def _model_identities(config: AppConfig) -> dict[str, str]:
    def validated(value: str, role: str) -> str:
        if len(value) > 160 or _MODEL_ID_PATTERN.fullmatch(value) is None:
            raise ValueError(f"{role} model ID is not a safe provider/model identifier")
        if value.casefold().startswith(("sk-", "token-", "secret-")):
            raise ValueError(f"{role} model ID resembles a credential")
        return value

    if config.planner.kind == "openai":
        planner = f"openai:{validated(config.planner.model, 'planner')}"
    elif config.planner.kind == "openrouter":
        planner = f"openrouter:{validated(config.planner.openrouter_model, 'planner')}"
    else:
        planner = config.planner.kind
    advisor_prefix = "enabled" if config.advisor.enabled else "disabled"
    advisor = (
        f"{advisor_prefix}:{config.advisor.provider}:"
        f"{validated(config.advisor.model, 'advisor')}"
    )
    return {"advisor": advisor, "planner": planner}


def build_generation_manifest(
    *,
    output: str | Path,
    proof_ledger_path: str | Path | None = None,
    config_path: str | Path = "config/live.yaml",
    prompt_file: str | Path | None = None,
    advisor_corpus_file: str | Path | None = None,
    scenario_attestation: str | Path | None = None,
    game_start: str | None = None,
    script_file: str | Path | None = None,
    kenshi_executable: str | Path | None = None,
    built_dll: str | Path | None = None,
    staged_dll: str | Path | None = None,
    installed_dll: str | Path | None = None,
) -> GenerationManifest:
    """Build one manifest without launching, contacting, or mutating Kenshi."""

    config_path = Path(config_path).expanduser().resolve()
    selected_proof_ledger = (
        DEFAULT_PROOF_LEDGER
        if proof_ledger_path is None
        else Path(proof_ledger_path).expanduser().resolve()
    )
    output_path = _lexical_absolute(output)
    _validate_output_path(output_path)
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        raise ValueError("configuration is unreadable or invalid") from exc
    _validate_env_references(raw_config)
    config = load_config(config_path)

    prompt_path = (
        Path(prompt_file).expanduser().resolve()
        if prompt_file is not None
        else config.paths.prompt_file
    )
    try:
        prompt_bytes = prompt_path.read_bytes()
        prompt_template = prompt_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("planner prompt is unreadable or not UTF-8") from exc
    rendered_prompt = render_planner_instructions(
        prompt_template,
        config.planning.planner_output_policy,
    )

    corpus_path = (
        Path(advisor_corpus_file).expanduser().resolve()
        if advisor_corpus_file is not None
        else config.advisor.corpus_file
    )
    corpus_evidence = _file_evidence(corpus_path)

    scenario = _scenario_evidence(
        Path(scenario_attestation).expanduser().resolve()
        if scenario_attestation is not None
        else None,
        config,
    )
    authored_start = _authored_start_evidence(game_start)

    artifact_digests = {
        "generated_operation_definitions": _required_file_digest(
            DEFAULT_OPERATION_DOC,
            "generated operation definitions",
        ),
        "operation_registry_semantics": operation_registry_digest(),
        "proof_ledger": capability_proof_digest(selected_proof_ledger),
        "uv_lock": _required_file_digest(ROOT / "uv.lock", "Python lock"),
    }
    if config.planner.kind == "scripted":
        if script_file is None:
            raise ValueError("scripted planner generation requires --script-file")
        artifact_digests["scripted_planner"] = _required_file_digest(
            Path(script_file).expanduser().resolve(),
            "scripted planner input",
        )
    elif script_file is not None:
        raise ValueError("--script-file is valid only for the scripted planner")

    try:
        capability_manifest = json.loads(
            CAPABILITY_AUTHORITY_PATH.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("native capability manifest is unreadable or invalid") from exc
    if not isinstance(capability_manifest, dict):
        raise ValueError("native capability manifest must be a JSON object")
    git = _git_evidence(output_path)
    source_ref = git.commit
    if git.material_dirty_sha256 is not None:
        source_ref += f"+dirty:{git.material_dirty_sha256}"
    try:
        created_at = datetime.fromisoformat(
            _git_text("show", "-s", "--format=%cI", git.commit).strip()
        ).astimezone(UTC)
    except ValueError as exc:
        raise ValueError("Git commit time is unavailable") from exc

    native = _native_evidence(
        built=Path(built_dll).expanduser().resolve() if built_dll else None,
        staged=Path(staged_dll).expanduser().resolve() if staged_dll else None,
        installed=Path(installed_dll).expanduser().resolve() if installed_dll else None,
    )
    protocol = _protocol_evidence(capability_manifest)
    prompts = {
        "planner_effective": digest_bytes(rendered_prompt.encode("utf-8")),
        "planner_template": digest_bytes(prompt_bytes),
    }
    if corpus_evidence.sha256 is not None:
        prompts["advisor_strategy_corpus"] = corpus_evidence.sha256

    metadata = GenerationMetadata(
        git=git,
        protocol=protocol,
        strategy_corpus=corpus_evidence,
        scenario=scenario,
        authored_start=authored_start,
        kenshi=_kenshi_evidence(
            Path(kenshi_executable).expanduser().resolve()
            if kenshi_executable
            else None
        ),
        native=native,
        memory_schema_version=MEMORY_SCHEMA_VERSION,
        operation_count=len(OPERATION_DEFINITION_LIST),
        capability_authority_digests=CapabilityAuthorityDigests(
            native=capability_native_digest(),
            operations=operation_registry_digest(),
            affordances=digest_json(
                [
                    {
                        "name": adapter.name,
                        "sources": sorted(str(source) for source in adapter.sources),
                        "operation_kinds": sorted(adapter.operation_kinds),
                        "denominator": adapter.denominator,
                        "completeness_boundary": adapter.completeness_boundary,
                    }
                    for adapter in sorted(AFFORDANCE_ADAPTERS, key=lambda item: item.name)
                ]
            ),
            telemetry=_required_file_digest(
                TELEMETRY_AUTHORITY_PATH,
                "telemetry capability authority",
            ),
            protocol=_required_file_digest(
                PROTOCOL_AUTHORITY_PATH,
                "protocol capability authority",
            ),
            continuity=_required_file_digest(
                CONTINUITY_AUTHORITY_PATH,
                "continuity capability authority",
            ),
            outcome=_required_file_digest(
                OUTCOME_AUTHORITY_PATH,
                "outcome capability authority",
            ),
            recovery=_required_file_digest(
                RECOVERY_AUTHORITY_PATH,
                "recovery capability authority",
            ),
            proof=capability_proof_digest(selected_proof_ledger),
        ),
    )
    manifest_values: dict[str, Any] = {
        "parent_generation_id": None,
        "created_at": created_at,
        "subject": "kenshi-agent-env",
        "source_ref": source_ref,
        # Filled after the generation identity is computed.  The underlying
        # authorities above are the identity inputs; capability bytes are the
        # generated linked artifact, not an identity input.
        "capability_manifest_digest": "0" * 64,
        "artifact_digests": artifact_digests,
        "models": _model_identities(config),
        "prompts": prompts,
        "config": {
            "effective_sha256": effective_config_digest(config),
            "redaction_schema_version": "1",
        },
        "metadata": metadata,
    }
    identity_projection = GenerationManifest.model_validate(
        {"generation_id": "0" * 64, **manifest_values}
    ).model_dump(mode="json", exclude={"generation_id", "capability_manifest_digest"})
    generation_id = digest_json(identity_projection)
    generated_capabilities = build_capability_manifest(
        generation_id,
        native_authority_path=CAPABILITY_AUTHORITY_PATH,
        proof_ledger_path=selected_proof_ledger,
    )
    manifest_values["capability_manifest_digest"] = capability_manifest_digest(
        generated_capabilities
    )
    return GenerationManifest(generation_id=generation_id, **manifest_values)


def write_generation_manifest(manifest: GenerationManifest, output: str | Path) -> None:
    """Atomically replace exactly one caller-selected output file."""

    path = _lexical_absolute(output)
    _validate_output_path(path)
    if not path.parent.is_dir():
        raise ValueError("manifest output directory does not exist")
    payload = canonical_json(manifest.model_dump(mode="json")) + b"\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Run the local ``./dev generation-manifest`` implementation."""

    from .dev_cli import build_parser

    parser = build_parser(prog="./dev")
    args = parser.parse_args(
        ["generation-manifest", *(sys.argv[1:] if argv is None else argv)]
    )
    try:
        manifest = build_generation_manifest(
            config_path=args.config,
            output=args.output,
            prompt_file=args.prompt_file,
            advisor_corpus_file=args.advisor_corpus_file,
            scenario_attestation=args.scenario_attestation,
            game_start=args.game_start,
            script_file=args.script_file,
            kenshi_executable=args.kenshi_executable,
            built_dll=args.built_dll,
            staged_dll=args.staged_dll,
            installed_dll=args.installed_dll,
        )
        write_generation_manifest(manifest, args.output)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Could not write generation manifest: {exc}", file=sys.stderr)
        return 1
    print(manifest.generation_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
