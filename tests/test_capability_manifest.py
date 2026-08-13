"""Portable contract and freshness gates for the generated G13 manifest."""

# The fixture mutation matrix intentionally keeps compact authority payloads.
# Ruff's line-length rule remains active for implementation modules.
# ruff: noqa: E501, I001

from __future__ import annotations

import json
import subprocess
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

import kenshi_agent.tooling.capability_manifest as capability_manifest
from kenshi_agent.tooling.capability_manifest import (
    Capability,
    CapabilityEvidenceRef,
    INITIAL_IMPORT_GENERATION,
    build_capability_manifest,
    canonical_json,
    capability_manifest_bytes,
    capability_manifest_digest,
    write_capability_manifest,
)
from kenshi_agent.tooling.native_contract_export import (
    export_gameplay_capabilities_header,
    load_gameplay_capabilities,
)
from kenshi_agent.tooling.native_provenance import CAPABILITY_MANIFEST
from kenshi_agent.tooling.schema_export import schema_documents


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "docs" / "generated" / "CAPABILITY_MANIFEST.json"
SCHEMA = ROOT / "schemas" / "capability-manifest.schema.json"
G13_PARENT_COMMIT = "0560b9de6e049f0dc06fab9afbef76f76d198092"


def test_manifest_has_exact_categories_sorted_rows_and_linkage() -> None:
    manifest = build_capability_manifest("0" * 64)
    assert {row.kind for row in manifest.capabilities} == {
        "sensing", "representation", "memory", "action", "verification", "recovery"
    }
    assert [row.name for row in manifest.capabilities] == sorted(
        {row.name for row in manifest.capabilities}
    )
    assert all(row.semantic_effects for row in manifest.capabilities)
    assert Counter(row.kind for row in manifest.capabilities) == Counter(
        action=26,
        sensing=27,
        representation=13,
        memory=1,
        verification=1,
        recovery=1,
    )
    assert Counter(row.evidence_state for row in manifest.capabilities) == Counter(
        proven=23,
        unproven=45,
        unsupported=1,
    )
    unsupported = next(
        row for row in manifest.capabilities if row.evidence_state == "unsupported"
    )
    assert unsupported.name == "action.respond_to_immediate_threat"
    assert {row.introduced_generation for row in manifest.capabilities} == {
        INITIAL_IMPORT_GENERATION
    }
    assert manifest.model_dump(mode="json")["generation_id"] == "0" * 64


def test_mixed_proof_is_portable_but_refs_retain_live_subcase() -> None:
    manifest = build_capability_manifest("0" * 64)
    order = next(row for row in manifest.capabilities if row.name == "action.perform_character_order")
    context = next(row for row in manifest.capabilities if row.name == "action.perform_context_action")
    assert (order.evidence_state, order.proof_class) == ("proven", "portable")
    assert any(ref.proof_class == "live" for ref in order.evidence_refs)
    assert context.evidence_state == "unproven"


def test_proven_refs_and_models_are_coherent() -> None:
    manifest = build_capability_manifest("0" * 64)
    assert all(
        row.evidence_state != "proven" or row.evidence_refs
        for row in manifest.capabilities
    )
    with pytest.raises(ValueError):
        CapabilityEvidenceRef(
            authority_ref="x",
            content_digest="0" * 64,
            evidence_state="proven",
            proof_class=None,
        )
    with pytest.raises(ValueError, match="authority_ref must be nonblank"):
        CapabilityEvidenceRef(
            authority_ref="   ",
            content_digest="0" * 64,
            evidence_state="unproven",
            proof_class=None,
        )
    with pytest.raises(ValueError):
        Capability(
            name="x", purpose="x", kind="not-a-kind", owner_component="x",
            semantic_effects=["effect"],
            applicability="x", implementation_ref="x", evidence_state="unproven",
            proof_class=None, introduced_generation="0" * 64,
        )


def test_canonical_digest_excludes_publication_newline() -> None:
    manifest = build_capability_manifest("0" * 64)
    assert capability_manifest_bytes(manifest).endswith(b"\n")
    assert capability_manifest_digest(manifest) != capability_manifest_bytes(manifest).hex()
    assert capability_manifest_digest(manifest) == capability_manifest.digest_json(
        manifest.model_dump(mode="json")
    )


def test_checked_in_zero_generation_fixture_is_fresh() -> None:
    expected = capability_manifest_bytes(build_capability_manifest("0" * 64))
    assert FIXTURE.read_bytes() == expected


def test_capability_schema_is_owned_and_fresh() -> None:
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == schema_documents()[
        "capability-manifest.schema.json"
    ]


def test_native_authority_rejects_bare_or_unknown_entries(tmp_path: Path) -> None:
    assert load_gameplay_capabilities(CAPABILITY_MANIFEST).categories
    for index, value in enumerate((
        {"schema_version": 2, "always": ["telemetry.fake"], "conditional": []},
        {"schema_version": 2, "always": [{"name": "telemetry.fake", "category": "bogus"}], "conditional": []},
    )):
        path = tmp_path / f"native-{index}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError):
            load_gameplay_capabilities(path)


def test_proof_loader_rejects_missing_refs_for_proven_rows(tmp_path: Path) -> None:
    ledger = {
        "entries": [{
            "operation_kind": "noop", "proof_status": "source_proven", "evidence": ["x"],
            "artifact_refs": [],
        }]
    }
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="requires artifact_refs"):
        capability_manifest._proof_rows(path)


@pytest.mark.parametrize("ref", ["/etc/passwd", "../outside", "does/not/exist"])
def test_proof_loader_rejects_path_attacks(tmp_path: Path, ref: str) -> None:
    ledger = {"entries": [{"operation_kind": "noop", "proof_status": "source_proven", "evidence": ["x"], "artifact_refs": [ref]}]}
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError):
        capability_manifest._proof_rows(path)


def test_proof_loader_rejects_unknown_status_operation_and_duplicate_ref(
    tmp_path: Path,
) -> None:
    for field, value in (("operation_kind", "not_registered"), ("proof_status", "bogus")):
        ledger = {"entries": [{"operation_kind": "noop", "proof_status": "unproven", "evidence": [], "artifact_refs": []}]}
        ledger["entries"][0][field] = value
        path = tmp_path / f"{field}.json"
        path.write_text(json.dumps(ledger), encoding="utf-8")
        with pytest.raises(ValueError):
            capability_manifest._proof_rows(path)
    ledger = {"entries": [{"operation_kind": "noop", "proof_status": "source_proven", "evidence": ["x"], "artifact_refs": ["src/kenshi_agent/operation_definitions.py"] * 2}]}
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicates"):
        capability_manifest._proof_rows(path)


def test_top_level_proof_blocks_are_projected_and_fail_closed(tmp_path: Path) -> None:
    assert {row.name for row in build_capability_manifest("0" * 64).capabilities} >= {
        "representation.planner_output_contract", "representation.protocol_2_world_model",
        "representation.player_topology", "representation.task_channels",
    }
    raw = json.loads(capability_manifest.PROOF_LEDGER.read_text(encoding="utf-8"))
    raw["player_topology"]["artifact_refs"] = []
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="requires artifact_refs"):
        capability_manifest.build_capability_manifest("0" * 64, proof_ledger_path=path)


def test_native_removal_changes_affected_operation_to_unsupported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = json.loads(CAPABILITY_MANIFEST.read_text(encoding="utf-8"))
    payload["always"] = [entry for entry in payload["always"] if entry["name"] != "control.approach_vendor"]
    path = tmp_path / "native.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(capability_manifest, "CAPABILITY_MANIFEST", path)
    row = next(row for row in capability_manifest.build_capability_manifest("0" * 64).capabilities if row.name == "action.approach_dialogue_target")
    assert row.evidence_state == "unsupported"
    assert row.limitations


def test_native_header_projection_is_byte_stable(tmp_path: Path) -> None:
    fresh = export_gameplay_capabilities_header(CAPABILITY_MANIFEST, tmp_path)
    committed = ROOT / "native" / "KenshiAgentTelemetry" / "GameplayCapabilities.generated.h"
    assert fresh.read_bytes() == committed.read_bytes()
    parent = subprocess.run(
        ["git", "show", f"{G13_PARENT_COMMIT}:native/KenshiAgentTelemetry/GameplayCapabilities.generated.h"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    assert committed.read_bytes() == parent


def test_native_and_proof_semantic_reordering_is_stable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from kenshi_agent.tooling.generation_manifest import (
        capability_native_digest,
        capability_proof_digest,
    )
    baseline_native = capability_native_digest()
    baseline_proof = capability_proof_digest()
    baseline_manifest = build_capability_manifest("0" * 64)
    baseline_bytes = capability_manifest_bytes(baseline_manifest)
    native = json.loads(CAPABILITY_MANIFEST.read_text(encoding="utf-8"))
    native["always"] = list(reversed(native["always"]))
    native["conditional"] = list(reversed(native["conditional"]))
    native_path = tmp_path / "native.json"
    native_path.write_text(json.dumps(native), encoding="utf-8")
    monkeypatch.setattr(capability_manifest, "CAPABILITY_MANIFEST", native_path)
    import kenshi_agent.tooling.generation_manifest as generation_manifest
    monkeypatch.setattr(generation_manifest, "CAPABILITY_AUTHORITY_PATH", native_path)
    assert capability_native_digest() == baseline_native
    assert capability_manifest_bytes(build_capability_manifest("0" * 64)) == baseline_bytes
    proof = json.loads(capability_manifest.PROOF_LEDGER.read_text(encoding="utf-8"))
    proof["entries"] = list(reversed(proof["entries"]))
    for entry in proof["entries"]:
        entry["artifact_refs"] = list(reversed(entry.get("artifact_refs", [])))
    proof_path = tmp_path / "proof.json"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    assert capability_proof_digest(proof_path) == baseline_proof
    assert capability_manifest_bytes(
        build_capability_manifest("0" * 64, proof_ledger_path=proof_path)
    ) == baseline_bytes


def test_generation_native_authority_path_links_capability_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from kenshi_agent.tooling import generation_manifest

    native = json.loads(CAPABILITY_MANIFEST.read_text(encoding="utf-8"))
    native["always"][0]["name"] += ".linked"
    native_path = tmp_path / "native.json"
    native_path.write_text(json.dumps(native), encoding="utf-8")
    monkeypatch.setattr(generation_manifest, "CAPABILITY_AUTHORITY_PATH", native_path)
    generated = generation_manifest.build_generation_manifest(output=tmp_path / "generation.json")
    expected = build_capability_manifest(
        generated.generation_id,
        native_authority_path=native_path,
    )
    assert generated.capability_manifest_digest == capability_manifest_digest(expected)
@pytest.mark.parametrize("forbidden", [
    "Barman", "Fish", "Burn", "Raw Iron", "Copper", "The Hub", "cmd-",
    "cmd-ad044f547c894764b0506d8459545a81", "cmd-482c905e973740d8b9bf6b88331b70ce",
    "inventory slot 0", "section 13.9", "wire command cmd-",
])
def test_incident_narrative_does_not_enter_generic_semantics(forbidden: str) -> None:
    manifest = build_capability_manifest("0" * 64)
    dumped = json.dumps([
        {
            "name": row.name,
            "purpose": row.purpose,
            "applicability": row.applicability,
            "completion_evidence": row.completion_evidence,
            "limitations": row.limitations,
        }
        for row in manifest.capabilities
    ], sort_keys=True)
    assert forbidden not in dumped


def test_capability_manifest_generation_is_canonical() -> None:
    manifest = build_capability_manifest("0" * 64)
    assert canonical_json(manifest.model_dump(mode="json")) == canonical_json(
        json.loads(capability_manifest_bytes(manifest))
    )


def test_real_generation_manifest_links_exact_capability_identity(tmp_path: Path) -> None:
    from kenshi_agent.tooling.generation_manifest import build_generation_manifest
    generation = build_generation_manifest(output=tmp_path / "generation.json")
    capabilities = build_capability_manifest(generation.generation_id)
    assert generation.capability_manifest_digest == capability_manifest_digest(capabilities)
    assert {row.introduced_generation for row in capabilities.capabilities} == {
        INITIAL_IMPORT_GENERATION
    }
    assert capability_manifest_bytes(build_capability_manifest("0" * 64)) != capability_manifest_bytes(capabilities)


def test_import_lineage_is_explicit_and_not_rewritten() -> None:
    first = build_capability_manifest("f" * 64)
    second = build_capability_manifest("e" * 64)
    assert {row.introduced_generation for row in first.capabilities} == {
        INITIAL_IMPORT_GENERATION
    }
    assert {row.introduced_generation for row in second.capabilities} == {
        INITIAL_IMPORT_GENERATION
    }


def test_exact_authority_digest_inventory_is_typed(tmp_path: Path) -> None:
    from kenshi_agent.tooling.generation_manifest import build_generation_manifest

    generation = build_generation_manifest(output=tmp_path / "generation.json")
    digests = generation.metadata.capability_authority_digests.model_dump()
    assert set(digests) == {
        "native", "operations", "affordances", "telemetry", "protocol",
        "continuity", "outcome", "recovery", "proof",
    }
    assert all(isinstance(value, str) and len(value) == 64 for value in digests.values())
    assert all(all(char in "0123456789abcdef" for char in value) for value in digests.values())


def test_top_level_owner_implementation_and_evidence_refs_are_exact() -> None:
    manifest = build_capability_manifest("0" * 64)
    expected = {
        descriptor.name: descriptor
        for descriptor in (
            capability_manifest.PLANNER_DESCRIPTOR,
            *capability_manifest.CAPABILITY_DESCRIPTORS,
        )
    }
    for name, descriptor in expected.items():
        row = next(row for row in manifest.capabilities if row.name == name)
        assert row.owner_component == descriptor.owner_component
        assert row.implementation_ref == descriptor.implementation_ref
        assert descriptor.proof_key
        assert row.evidence_state != "proven" or row.evidence_refs
        assert all(len(ref.content_digest) == 64 for ref in row.evidence_refs)


def test_duplicate_descriptor_name_and_proof_key_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = capability_manifest.CAPABILITY_DESCRIPTORS[0]
    monkeypatch.setattr(capability_manifest, "CAPABILITY_DESCRIPTORS", (descriptor, descriptor))
    with pytest.raises(ValueError, match="duplicate names"):
        build_capability_manifest("0" * 64)

    unique_name_duplicate_key = replace(
        descriptor, name="representation.another", proof_key=descriptor.proof_key
    )
    monkeypatch.setattr(
        capability_manifest,
        "CAPABILITY_DESCRIPTORS",
        (descriptor, unique_name_duplicate_key),
    )
    with pytest.raises(ValueError, match="duplicate or missing proof keys"):
        build_capability_manifest("0" * 64)


def test_unknown_unsupported_and_absent_evidence_remain_distinct() -> None:
    assert capability_manifest._evidence(None)[:2] == ("absent", None)
    assert capability_manifest._evidence([
        {"proof_status": "unsupported", "artifact_refs": [], "operation_kind": "noop"}
    ])[:2] == ("unsupported", None)
    assert capability_manifest._evidence([
        {"proof_status": "unknown", "artifact_refs": [], "operation_kind": "noop"}
    ])[:2] == ("unknown", None)


def _git_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "authority-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Authority Test"], cwd=repo, check=True)
    tracked = repo / "tracked.txt"
    tracked.write_text("tracked", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    return repo


def test_proof_path_validator_rejects_directory_symlinks_and_untracked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _git_fixture_repo(tmp_path)
    monkeypatch.setattr(capability_manifest, "ROOT", repo)
    (repo / "folder").mkdir()
    (repo / "untracked.txt").write_text("untracked", encoding="utf-8")
    with pytest.raises(ValueError, match="regular file"):
        capability_manifest._resolve_authority_path("folder", "test")
    with pytest.raises(ValueError, match="untracked"):
        capability_manifest._resolve_authority_path("untracked.txt", "test")
    try:
        (repo / "final-link").symlink_to(repo / "tracked.txt")
        (repo / "link-parent").symlink_to(repo, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform lacks symlink support")
    with pytest.raises(ValueError, match="symlink"):
        capability_manifest._resolve_authority_path("final-link", "test")
    with pytest.raises(ValueError, match="symlink"):
        capability_manifest._resolve_authority_path("link-parent/tracked.txt", "test")


def test_relative_capability_output_rejects_symlinked_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "real"
    destination.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(destination, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform lacks symlink support")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        write_capability_manifest(build_capability_manifest("0" * 64), "link/output.json")


def test_proof_referenced_bytes_and_top_level_bytes_change_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "proof-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Proof Test"], cwd=repo, check=True)
    operation_artifact = repo / "operation.txt"
    top_level_artifact = repo / "top-level.txt"
    operation_artifact.write_text("operation-one", encoding="utf-8")
    top_level_artifact.write_text("top-level-one", encoding="utf-8")
    subprocess.run(["git", "add", "operation.txt", "top-level.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "proof artifacts"], cwd=repo, check=True)
    raw = json.loads(capability_manifest.PROOF_LEDGER.read_text(encoding="utf-8"))
    for entry in raw["entries"]:
        entry["artifact_refs"] = ["operation.txt"] if entry.get("artifact_refs") else []
    for key in ("planner_output_contract", "protocol_2_world_model", "player_topology", "task_channels"):
        raw[key]["artifact_refs"] = ["top-level.txt"] if raw[key].get("artifact_refs") else []
    ledger = repo / "proof.json"
    ledger.write_text(json.dumps(raw), encoding="utf-8")
    subprocess.run(["git", "add", "proof.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "proof ledger"], cwd=repo, check=True)
    monkeypatch.setattr(capability_manifest, "ROOT", repo)
    baseline = capability_manifest.capability_proof_digest(ledger)
    operation_artifact.write_text("operation-two", encoding="utf-8")
    assert capability_manifest.capability_proof_digest(ledger) != baseline
    operation_baseline = capability_manifest.capability_proof_digest(ledger)
    top_level_artifact.write_text("top-level-two", encoding="utf-8")
    assert capability_manifest.capability_proof_digest(ledger) != operation_baseline


def test_native_semantic_mutation_matrix_changes_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    baseline = capability_manifest_bytes(build_capability_manifest("0" * 64))
    from kenshi_agent.tooling.generation_manifest import capability_native_digest

    baseline_native_digest = capability_native_digest()
    payload = json.loads(CAPABILITY_MANIFEST.read_text(encoding="utf-8"))
    cases = []
    renamed = json.loads(json.dumps(payload))
    renamed["always"][0]["name"] += ".changed"
    cases.append(renamed)
    recategorized = json.loads(json.dumps(payload))
    recategorized["always"][0]["category"] = "representation"
    cases.append(recategorized)
    availability = json.loads(json.dumps(payload))
    availability["conditional"].append(availability["always"].pop())
    cases.append(availability)
    for index, mutated in enumerate(cases):
        path = tmp_path / f"native-{index}.json"
        path.write_text(json.dumps(mutated), encoding="utf-8")
        monkeypatch.setattr(capability_manifest, "CAPABILITY_MANIFEST", path)
        changed_bytes = capability_manifest_bytes(build_capability_manifest("0" * 64))
        assert changed_bytes != baseline
        assert capability_native_digest(path) != baseline_native_digest


def test_operation_affordance_and_descriptor_mutations_change_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = capability_manifest_bytes(build_capability_manifest("0" * 64))
    definition = capability_manifest.OPERATION_DEFINITION_LIST[0]
    monkeypatch.setattr(
        capability_manifest,
        "OPERATION_DEFINITION_LIST",
        tuple(replace(item, summary=item.summary + " changed") if item is definition else item
              for item in capability_manifest.OPERATION_DEFINITION_LIST),
    )
    assert capability_manifest_bytes(build_capability_manifest("0" * 64)) != baseline


@pytest.mark.parametrize("field", ["denominator", "completeness_boundary"])
def test_each_affordance_authority_field_changes_projection(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    import kenshi_agent.affordances as affordances

    baseline = capability_manifest_bytes(build_capability_manifest("0" * 64))
    adapter = affordances.AFFORDANCE_ADAPTERS[0]
    mutated = replace(adapter, **{field: getattr(adapter, field) + " changed"})
    adapters = (mutated, *affordances.AFFORDANCE_ADAPTERS[1:])
    monkeypatch.setattr(affordances, "AFFORDANCE_ADAPTERS", adapters)
    monkeypatch.setattr(capability_manifest, "AFFORDANCE_ADAPTERS", adapters)
    assert capability_manifest_bytes(build_capability_manifest("0" * 64)) != baseline


@pytest.mark.parametrize(
    "descriptor_name,descriptor_index",
    [
        ("PLANNER_DESCRIPTOR", None),
        ("CAPABILITY_DESCRIPTORS", 0),
        ("CAPABILITY_DESCRIPTORS", 1),
        ("CAPABILITY_DESCRIPTORS", 2),
        ("CONTINUITY_DESCRIPTOR", None),
        ("OUTCOME_DESCRIPTOR", None),
        ("RECOVERY_DESCRIPTOR", None),
    ],
)
@pytest.mark.parametrize("mutation", ["purpose", "semantic_effects"])
def test_each_owner_descriptor_mutation_changes_projection(
    monkeypatch: pytest.MonkeyPatch,
    descriptor_name: str,
    descriptor_index: int | None,
    mutation: str,
) -> None:
    baseline = capability_manifest_bytes(build_capability_manifest("0" * 64))
    descriptor = (
        capability_manifest.CAPABILITY_DESCRIPTORS[descriptor_index]
        if descriptor_index is not None
        else getattr(capability_manifest, descriptor_name)
    )
    if mutation == "purpose":
        changed = replace(descriptor, purpose=descriptor.purpose + " changed")
    else:
        changed = replace(
            descriptor,
            semantic_effects=descriptor.semantic_effects + ("owner.changed",),
        )
    if descriptor_name == "CAPABILITY_DESCRIPTORS":
        descriptors = list(capability_manifest.CAPABILITY_DESCRIPTORS)
        descriptors[descriptor_index] = changed
        monkeypatch.setattr(
            capability_manifest,
            descriptor_name,
            tuple(descriptors),
        )
    else:
        monkeypatch.setattr(capability_manifest, descriptor_name, changed)
    assert capability_manifest_bytes(build_capability_manifest("0" * 64)) != baseline


def test_top_level_and_operation_proof_mutations_change_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    baseline = capability_manifest_bytes(build_capability_manifest("0" * 64))
    raw = json.loads(capability_manifest.PROOF_LEDGER.read_text(encoding="utf-8"))

    top_level = json.loads(json.dumps(raw))
    top_level["planner_output_contract"]["proof_status"] = "withheld"
    top_level["planner_output_contract"]["artifact_refs"] = []
    top_level_path = tmp_path / "top-level-proof.json"
    top_level_path.write_text(json.dumps(top_level), encoding="utf-8")
    assert capability_manifest_bytes(
        build_capability_manifest("0" * 64, proof_ledger_path=top_level_path)
    ) != baseline

    operation = json.loads(json.dumps(raw))
    noop = next(entry for entry in operation["entries"] if entry["operation_kind"] == "noop")
    noop["proof_status"] = "unproven"
    noop["artifact_refs"] = []
    operation_path = tmp_path / "operation-proof.json"
    operation_path.write_text(json.dumps(operation), encoding="utf-8")
    assert capability_manifest_bytes(
        build_capability_manifest("0" * 64, proof_ledger_path=operation_path)
    ) != baseline


def test_generation_identity_changes_for_each_authority_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from kenshi_agent.tooling import generation_manifest

    baseline = generation_manifest.build_generation_manifest(output=tmp_path / "base.json")
    baseline_digests = baseline.metadata.capability_authority_digests.model_dump()
    fixed_baseline = capability_manifest_bytes(build_capability_manifest("0" * 64))

    native = json.loads(CAPABILITY_MANIFEST.read_text(encoding="utf-8"))
    native["always"][0]["name"] += ".changed"
    native_path = tmp_path / "native.json"
    native_path.write_text(json.dumps(native), encoding="utf-8")
    monkeypatch.setattr(generation_manifest, "CAPABILITY_AUTHORITY_PATH", native_path)
    mutated = generation_manifest.build_generation_manifest(output=tmp_path / "native-out.json")
    assert mutated.generation_id != baseline.generation_id
    assert mutated.metadata.capability_authority_digests.native != baseline_digests["native"]
    monkeypatch.undo()

    definition = generation_manifest.OPERATION_DEFINITION_LIST[0]
    changed_definitions = tuple(
        replace(item, summary=item.summary + " changed") if item is definition else item
        for item in generation_manifest.OPERATION_DEFINITION_LIST
    )
    monkeypatch.setattr(generation_manifest, "OPERATION_DEFINITION_LIST", changed_definitions)
    monkeypatch.setattr(capability_manifest, "OPERATION_DEFINITION_LIST", changed_definitions)
    mutated = generation_manifest.build_generation_manifest(output=tmp_path / "operations-out.json")
    assert mutated.generation_id != baseline.generation_id
    assert mutated.metadata.capability_authority_digests.operations != baseline_digests["operations"]
    monkeypatch.undo()

    import kenshi_agent.affordances as affordances
    adapter = affordances.AFFORDANCE_ADAPTERS[0]
    changed_adapters = (
        replace(
            adapter,
            denominator=adapter.denominator + " changed",
            completeness_boundary=adapter.completeness_boundary + " changed",
        ),
        *affordances.AFFORDANCE_ADAPTERS[1:],
    )
    monkeypatch.setattr(affordances, "AFFORDANCE_ADAPTERS", changed_adapters)
    monkeypatch.setattr(generation_manifest, "AFFORDANCE_ADAPTERS", changed_adapters)
    monkeypatch.setattr(capability_manifest, "AFFORDANCE_ADAPTERS", changed_adapters)
    mutated = generation_manifest.build_generation_manifest(output=tmp_path / "affordances-out.json")
    assert mutated.generation_id != baseline.generation_id
    assert mutated.metadata.capability_authority_digests.affordances != baseline_digests["affordances"]
    monkeypatch.undo()

    for field, constant in (
        ("telemetry", "TELEMETRY_AUTHORITY_PATH"),
        ("protocol", "PROTOCOL_AUTHORITY_PATH"),
        ("continuity", "CONTINUITY_AUTHORITY_PATH"),
        ("outcome", "OUTCOME_AUTHORITY_PATH"),
        ("recovery", "RECOVERY_AUTHORITY_PATH"),
    ):
        source = Path(getattr(generation_manifest, constant))
        mutated_source = tmp_path / f"{field}.py"
        mutated_source.write_bytes(source.read_bytes() + b"\n# mutation\n")
        monkeypatch.setattr(generation_manifest, constant, mutated_source)
        mutated = generation_manifest.build_generation_manifest(output=tmp_path / f"{field}-out.json")
        assert mutated.generation_id != baseline.generation_id
        assert getattr(mutated.metadata.capability_authority_digests, field) != baseline_digests[field]
        monkeypatch.undo()

    monkeypatch.undo()
    proof = json.loads(capability_manifest.PROOF_LEDGER.read_text(encoding="utf-8"))
    proof["planner_output_contract"]["proof_status"] = "withheld"
    proof["planner_output_contract"]["artifact_refs"] = []
    proof_path = tmp_path / "proof.json"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    mutated = generation_manifest.build_generation_manifest(
        output=tmp_path / "proof-out.json", proof_ledger_path=proof_path
    )
    assert mutated.generation_id != baseline.generation_id
    assert mutated.metadata.capability_authority_digests.proof != baseline_digests["proof"]

    assert capability_manifest_bytes(build_capability_manifest("0" * 64)) == fixed_baseline


def test_generation_proof_authority_path_links_capability_digest(
    tmp_path: Path,
) -> None:
    from kenshi_agent.tooling import generation_manifest

    proof = json.loads(capability_manifest.PROOF_LEDGER.read_text(encoding="utf-8"))
    proof["planner_output_contract"]["proof_status"] = "withheld"
    proof["planner_output_contract"]["artifact_refs"] = []
    proof_path = tmp_path / "proof-authority.json"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    generated = generation_manifest.build_generation_manifest(
        output=tmp_path / "generation.json",
        proof_ledger_path=proof_path,
    )
    expected = build_capability_manifest(
        generated.generation_id,
        proof_ledger_path=proof_path,
    )
    assert generated.capability_manifest_digest == capability_manifest_digest(expected)
