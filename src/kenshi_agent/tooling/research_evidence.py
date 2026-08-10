"""Typed, repository-owned reverse-engineering evidence packages.

The package under ``game_sources/research/<subsystem>`` is the authority.  The
proof ledger may classify an operation and point here, but it may not restate
the reverse-engineering argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = ROOT / "game_sources" / "research"
REQUIRED_FILES = frozenset(
    {
        "question.md",
        "static_evidence.md",
        "call_sites.json",
        "dynamic_observations.json",
        "abi_notes.md",
        "conclusion.md",
    }
)
PROOF_SECTION_HEADINGS = (
    "## Source-proven",
    "## Test-proven",
    "## Live-proven",
    "## Withheld",
)

Sha256 = str
Confidence = Literal["low", "medium", "high"]
ProofStatus = Literal[
    "source_proven", "test_proven", "live_proven", "withheld", "inconclusive"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutableIdentity(StrictModel):
    product: str = Field(min_length=1)
    version: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    steam_build_id: str = Field(pattern=r"^[0-9]+$")
    sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")


class LibraryIdentity(StrictModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    repository_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    headers_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    working_tree_state: str = Field(min_length=1)


class RepositoryCallSite(StrictModel):
    path: str = Field(min_length=1)
    source_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    enclosing_function: str = Field(min_length=1)
    # Informational only. Stable function and expression anchors plus the exact
    # source blob own validation, so unrelated insertions do not create churn.
    line: int = Field(gt=0)
    contains: str = Field(min_length=1)


class NativeCallSite(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    component: Literal["Kenshi", "KenshiLib", "ForgottenGUI", "project_native"]
    symbol: str = Field(min_length=1)
    rva: str | None = Field(default=None, pattern=r"^0x[0-9A-Fa-f]+$")
    declared_signature: str = Field(min_length=1)
    inferred_signature_confidence: Confidence
    declaration_source: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    project_call_sites: list[RepositoryCallSite] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_address_or_symbol(self) -> NativeCallSite:
        if self.rva is None and not self.symbol:
            raise ValueError("a call site requires an RVA or symbol")
        return self


class ResearchCallSites(StrictModel):
    schema_version: Literal[1]
    subsystem: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    executable: ExecutableIdentity
    libraries: list[LibraryIdentity] = Field(min_length=1)
    sites: list[NativeCallSite] = Field(min_length=1)


class DynamicObservation(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: Literal["live_probe", "crash", "contradiction", "portable_test"]
    observed_on: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    kenshi_version: str = Field(min_length=1)
    executable_sha256_at_probe: Sha256 | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    installed_dll_sha256_at_probe: Sha256 | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    run_bundle: str | None = None
    durable_reduced_evidence: str | None = None
    pre_dispatch_state: str = Field(min_length=1)
    request: str = Field(min_length=1)
    acknowledgement: str = Field(min_length=1)
    later_engine_evidence: str = Field(min_length=1)
    final_disposition: str = Field(min_length=1)
    evidence_source: str = Field(min_length=1)
    remaining_uncertainty: list[str]


class DynamicObservations(StrictModel):
    schema_version: Literal[1]
    subsystem: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    observations: list[DynamicObservation]


class ResearchConclusion(StrictModel):
    schema_version: Literal[1]
    subsystem: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    proof_status: ProofStatus
    executable: ExecutableIdentity
    libraries: list[LibraryIdentity] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    inferred_signature_confidence: Confidence
    portable_test_refs: list[str]
    live_probe_ids: list[str]
    crash_ids: list[str]
    contradiction_ids: list[str]
    remaining_uncertainty: list[str] = Field(min_length=1)
    supersedes: list[str] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ResearchPackage:
    path: Path
    call_sites: ResearchCallSites
    observations: DynamicObservations
    conclusion: ResearchConclusion


def _markdown_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path}: conclusion.md must start with YAML front matter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"{path}: conclusion.md front matter is not closed") from exc
    payload = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: conclusion.md front matter must be a mapping")
    return payload, "\n".join(lines[end + 1 :])


def _validate_repository_call_site(site: RepositoryCallSite) -> str | None:
    path = ROOT / site.path
    if not path.is_file():
        return f"call site path does not exist: {site.path}"
    raw = path.read_bytes()
    # `source_sha256` identifies the exact source blob that was inspected when
    # the evidence package was authored. It is provenance, not a demand that
    # today's mutable checkout remain byte-identical forever. Current-source
    # continuity is instead proved by the stable enclosing-function and
    # contained-expression anchors below; line numbers remain informational.
    source = raw.decode("utf-8")
    function_index = source.find(site.enclosing_function)
    if function_index < 0:
        return (
            f"call site enclosing function {site.enclosing_function!r} is absent "
            f"from {site.path}"
        )
    contains_index = source.find(site.contains, function_index)
    if contains_index < 0:
        return (
            f"call site expression {site.contains!r} is absent after "
            f"{site.enclosing_function!r} in {site.path}"
        )
    return None


def load_research_package(path: Path) -> ResearchPackage:
    missing = REQUIRED_FILES - {item.name for item in path.iterdir() if item.is_file()}
    if missing:
        raise ValueError(f"{path}: missing required files: {', '.join(sorted(missing))}")
    call_sites = ResearchCallSites.model_validate_json(
        (path / "call_sites.json").read_text(encoding="utf-8")
    )
    observations = DynamicObservations.model_validate_json(
        (path / "dynamic_observations.json").read_text(encoding="utf-8")
    )
    metadata, body = _markdown_front_matter(path / "conclusion.md")
    conclusion = ResearchConclusion.model_validate(metadata)

    if {call_sites.subsystem, observations.subsystem, conclusion.subsystem} != {
        path.name
    }:
        raise ValueError(f"{path}: subsystem names must match the directory")
    if call_sites.executable != conclusion.executable:
        raise ValueError(f"{path}: conclusion and call sites name different executables")
    if call_sites.libraries != conclusion.libraries:
        raise ValueError(f"{path}: conclusion and call sites name different libraries")

    site_ids = {site.id for site in call_sites.sites}
    if len(site_ids) != len(call_sites.sites):
        raise ValueError(f"{path}: call-site ids must be unique")
    unknown_sites = set(conclusion.source_refs) - site_ids
    if unknown_sites:
        raise ValueError(f"{path}: unknown source refs: {', '.join(sorted(unknown_sites))}")

    observations_by_id = {item.id: item for item in observations.observations}
    if len(observations_by_id) != len(observations.observations):
        raise ValueError(f"{path}: observation ids must be unique")
    expected_kinds = {
        "portable_test_refs": (conclusion.portable_test_refs, "portable_test"),
        "live_probe_ids": (conclusion.live_probe_ids, "live_probe"),
        "crash_ids": (conclusion.crash_ids, "crash"),
        "contradiction_ids": (conclusion.contradiction_ids, "contradiction"),
    }
    for field, (identifiers, kind) in expected_kinds.items():
        for identifier in identifiers:
            observation = observations_by_id.get(identifier)
            if observation is None:
                raise ValueError(f"{path}: {field} references unknown id {identifier}")
            if observation.kind != kind:
                raise ValueError(
                    f"{path}: {field} references {identifier} with kind {observation.kind}"
                )
    if conclusion.proof_status == "live_proven" and not conclusion.live_probe_ids:
        raise ValueError(f"{path}: live_proven conclusion has no live probe")
    if conclusion.proof_status == "live_proven":
        for identifier in conclusion.live_probe_ids:
            observation = observations_by_id[identifier]
            if observation.run_bundle is None:
                raise ValueError(
                    f"{path}: live_proven probe {identifier} has no exact run bundle"
                )
            if observation.durable_reduced_evidence is None:
                raise ValueError(
                    f"{path}: live_proven probe {identifier} has no durable reduced evidence"
                )
            durable_path = ROOT / observation.durable_reduced_evidence
            if not durable_path.is_file():
                raise ValueError(
                    f"{path}: durable reduced evidence does not exist: "
                    f"{observation.durable_reduced_evidence}"
                )
    for heading in PROOF_SECTION_HEADINGS:
        if heading not in body:
            raise ValueError(f"{path}: conclusion.md is missing {heading}")
    for site in call_sites.sites:
        for project_call_site in site.project_call_sites:
            problem = _validate_repository_call_site(project_call_site)
            if problem:
                raise ValueError(f"{path}: {problem}")
    return ResearchPackage(path, call_sites, observations, conclusion)


def load_research_packages(root: Path = RESEARCH_ROOT) -> tuple[ResearchPackage, ...]:
    return tuple(
        load_research_package(path)
        for path in sorted(root.iterdir())
        if path.is_dir() and not path.name.startswith("_")
    )


def validate_research_tree(root: Path = RESEARCH_ROOT) -> list[str]:
    errors: list[str] = []
    try:
        packages = load_research_packages(root)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    if not packages:
        errors.append(f"{root}: no research evidence packages")
    return errors


def render_research_index(root: Path = RESEARCH_ROOT) -> str:
    packages = load_research_packages(root)
    lines = [
        "<!-- generated by scripts/export_docs.py; edits are overwritten -->",
        "",
        "# Reverse-engineering evidence index",
        "",
        "Each row is loaded from the validated canonical package under",
        "`game_sources/research/`. Proof ledgers link to these packages instead",
        "of restating their reverse-engineering conclusions.",
        "",
        "| subsystem | status | Kenshi binary | signature confidence | live probes "
        "| crashes | open uncertainties |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for package in packages:
        conclusion = package.conclusion
        executable = conclusion.executable
        lines.append(
            f"| [`{conclusion.subsystem}`](../../game_sources/research/"
            f"{conclusion.subsystem}/conclusion.md) | `{conclusion.proof_status}` | "
            f"{executable.version} `{executable.sha256[:12]}...` | "
            f"`{conclusion.inferred_signature_confidence}` | "
            f"{len(conclusion.live_probe_ids)} | {len(conclusion.crash_ids)} | "
            f"{len(conclusion.remaining_uncertainty)} |"
        )
    lines.extend(["", "Regenerate with `python scripts/export_docs.py`.", ""])
    return "\n".join(lines)
