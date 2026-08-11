from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

import kenshi_agent.tooling.generation_manifest as generation_manifest
from kenshi_agent.core.scenario import ScenarioAttestation, ScenarioObservedState
from kenshi_agent.core.telemetry import ScenarioIdentity
from kenshi_agent.tooling.generation_manifest import (
    _validate_env_references,
    build_generation_manifest,
    canonical_json,
    write_generation_manifest,
)


def _config_fixture(
    root: Path,
    *,
    planner_kind: str = "openrouter",
    planner_model: str = "planner-v1",
    advisor_model: str = "advisor-v1",
    advisor_enabled: bool = True,
    objective: str | None = None,
    scenario: dict[str, str] | None = None,
) -> tuple[Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    prompt = root / "prompt.md"
    prompt.write_text("planner prompt v1 {{PLANNER_OUTPUT_POLICY}}\n", encoding="utf-8")
    corpus = root / "corpus.yaml"
    corpus.write_text("strategy: v1\n", encoding="utf-8")
    scenario_yaml = ""
    if scenario is not None:
        scenario_yaml = "\n  scenario:\n" + "".join(
            f"    {key}: {value}\n" for key, value in scenario.items()
        )
    objective_yaml = f"\n  objective: {objective!r}" if objective is not None else ""
    runtime_header = (
        "runtime:"
        if (objective is not None or scenario is not None)
        else "runtime: {}"
    )
    config = root / "config.yaml"
    config.write_text(
        f"""version: 1
mode: mock
paths:
  runs_dir: runs
  prompt_file: prompt.md
  memory_db: memory.sqlite3
{runtime_header}{objective_yaml}{scenario_yaml}
planner:
  kind: {planner_kind}
  model: {planner_model}
  openrouter_model: {planner_model}
advisor:
  enabled: {str(advisor_enabled).lower()}
  model: {advisor_model}
  corpus_file: corpus.yaml
telemetry:
  file: telemetry.json
""",
        encoding="utf-8",
    )
    return config, prompt, corpus


def _build_fixture(root: Path, output: Path, **kwargs: object):
    build_kwargs = {
        name: kwargs.pop(name)
        for name in (
            "scenario_attestation",
            "game_start",
            "kenshi_executable",
            "built_dll",
            "staged_dll",
            "installed_dll",
        )
        if name in kwargs
    }
    config, prompt, corpus = _config_fixture(root, **kwargs)
    return build_generation_manifest(
        config_path=config,
        output=output,
        prompt_file=prompt,
        advisor_corpus_file=corpus,
        **build_kwargs,
    )


def test_generation_manifest_is_repeatable_and_atomic(tmp_path: Path) -> None:
    first = build_generation_manifest(output=tmp_path / "one.json")
    second = build_generation_manifest(output=tmp_path / "two.json")
    write_generation_manifest(first, tmp_path / "one.json")
    write_generation_manifest(second, tmp_path / "two.json")
    assert first.generation_id == second.generation_id
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()
    assert set(json.loads((tmp_path / "one.json").read_text())) == {
        "generation_id", "parent_generation_id", "created_at", "subject", "source_ref",
        "capability_manifest_digest", "artifact_digests", "models", "prompts", "config", "metadata",
    }


def test_env_references_fail_closed() -> None:
    _validate_env_references({"value": "${LOCALAPPDATA:-$LOCALAPPDATA/cache}"})
    with pytest.raises(ValueError, match="credential"):
        _validate_env_references({"value": "${OPENAI_API_KEY:-missing}"})
    with pytest.raises(ValueError, match="unknown"):
        _validate_env_references({"value": "${NOT_ALLOWED:-missing}"})


def test_canonical_json_ignores_mapping_presentation() -> None:
    assert canonical_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_manifest_carries_every_goal_11_authority(tmp_path: Path) -> None:
    manifest = _build_fixture(tmp_path / "fixture", tmp_path / "manifest.json")
    metadata = manifest.metadata.model_dump(mode="json")

    assert set(manifest.artifact_digests) == {
        "generated_operation_definitions",
        "operation_registry_semantics",
        "proof_ledger",
        "uv_lock",
    }
    assert set(manifest.models) == {"advisor", "planner"}
    assert set(manifest.prompts) == {
        "advisor_strategy_corpus",
        "planner_effective",
        "planner_template",
    }
    assert manifest.config["redaction_schema_version"] == "1"
    assert metadata["manifest_schema_version"] == 1
    assert metadata["memory_schema_version"] == generation_manifest.MEMORY_SCHEMA_VERSION
    assert metadata["operation_count"] == len(
        generation_manifest.OPERATION_DEFINITION_LIST
    )
    assert metadata["protocol"]["versions"] == {
        "evidence_semantics": "2",
        "generation_manifest": "1",
        "memory": "4",
        "native_command_request": "1.6",
        "native_gameplay_capabilities": "1",
        "native_source_protocol": "2.0.0",
        "protocol_2_world_model": "2.0.0",
        "runtime_plan": "1.0",
        "runtime_plan_patch": "1.0",
        "telemetry": "2.0.0",
    }
    assert "generation_manifest" in metadata["protocol"]["schema_digests"]
    assert metadata["strategy_corpus"]["state"] == "present"
    assert set(metadata["native"]) >= {
        "built",
        "generated_capability_header",
        "installed",
        "source",
        "staged",
    }


def test_repeated_git_evidence_excludes_an_in_repo_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Generation Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    monkeypatch.setattr(generation_manifest, "ROOT", repo)

    output = repo / "manifest.json"
    first = generation_manifest._git_evidence(output)
    output.write_bytes(b"generation one")
    second = generation_manifest._git_evidence(output)
    output.write_bytes(b"generation two")
    third = generation_manifest._git_evidence(output)
    assert first == second == third


def test_api_key_and_unrelated_environment_do_not_change_identity_or_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _build_fixture(tmp_path / "first", tmp_path / "one.json")
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-api-key")
    monkeypatch.setenv("UNRELATED_TEST_VALUE", "unrelated-secret")
    monkeypatch.setenv("HOME", "/tmp/secret-home")
    second = _build_fixture(tmp_path / "second", tmp_path / "two.json")
    assert first.generation_id == second.generation_id
    first_bytes = canonical_json(first.model_dump(mode="json"))
    second_bytes = canonical_json(second.model_dump(mode="json"))
    assert first_bytes == second_bytes
    assert b"super-secret-api-key" not in second_bytes
    assert b"unrelated-secret" not in second_bytes
    assert b"/tmp/secret-home" not in second_bytes


def test_serialized_manifest_redacts_free_text_and_host_path_values(
    tmp_path: Path,
) -> None:
    manifest = _build_fixture(
        tmp_path / "redacted",
        tmp_path / "manifest.json",
        objective="super-secret objective at /home/levib/private\\Windows\\secret.txt",
    )
    payload = canonical_json(manifest.model_dump(mode="json"))
    assert b"super-secret" not in payload
    assert b"/home/levib/private" not in payload
    assert b"Windows\\secret.txt" not in payload
    assert str(tmp_path).encode() not in payload


@pytest.mark.parametrize(
    "model_id",
    ["/tmp/provider/model", "../provider/model", "C:\\Users\\secret\\model", "sk-live-secret"],
)
def test_path_like_and_credential_like_model_ids_fail_closed(
    tmp_path: Path, model_id: str
) -> None:
    with pytest.raises(ValueError, match="model ID"):
        _build_fixture(
            tmp_path / "invalid",
            tmp_path / "manifest.json",
            planner_model=model_id,
            advisor_model=model_id,
        )


def test_output_symlink_and_symlink_parent_are_rejected(
    tmp_path: Path,
) -> None:
    manifest = _build_fixture(tmp_path / "fixture", tmp_path / "baseline.json")
    target = tmp_path / "target.json"
    target.write_text("existing", encoding="utf-8")
    output_link = tmp_path / "output.json"
    output_link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        write_generation_manifest(manifest, output_link)

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_generation_manifest(manifest, parent_link / "output.json")


def test_nested_unknown_and_credential_interpolation_fail_closed() -> None:
    with pytest.raises(ValueError, match="credential"):
        _validate_env_references({"value": "${OPENAI_API_KEY:-${LOCALAPPDATA}}"})
    with pytest.raises(ValueError, match="unknown"):
        _validate_env_references({"value": "${NOT_ALLOWED:-${LOCALAPPDATA}}"})
    with pytest.raises(ValueError, match="unknown"):
        _validate_env_references({"value": "${LOCALAPPDATA:-${NOT_ALLOWED}}"})


def test_planner_and_advisor_model_changes_change_identity(tmp_path: Path) -> None:
    baseline = _build_fixture(tmp_path / "base", tmp_path / "base.json")
    planner = _build_fixture(
        tmp_path / "planner", tmp_path / "planner.json", planner_model="planner-v2"
    )
    advisor = _build_fixture(
        tmp_path / "advisor", tmp_path / "advisor.json", advisor_model="advisor-v2"
    )
    assert planner.generation_id != baseline.generation_id
    assert advisor.generation_id != baseline.generation_id


def test_prompt_and_corpus_bytes_change_identity(tmp_path: Path) -> None:
    baseline_root = tmp_path / "base"
    baseline = _build_fixture(baseline_root, tmp_path / "base.json")
    (baseline_root / "prompt.md").write_text(
        "prompt changed {{PLANNER_OUTPUT_POLICY}}\n", encoding="utf-8"
    )
    changed_prompt = build_generation_manifest(
        config_path=baseline_root / "config.yaml",
        output=tmp_path / "prompt.json",
        prompt_file=baseline_root / "prompt.md",
        advisor_corpus_file=baseline_root / "corpus.yaml",
    )
    (baseline_root / "corpus.yaml").write_text("strategy: changed\n", encoding="utf-8")
    changed_corpus = build_generation_manifest(
        config_path=baseline_root / "config.yaml",
        output=tmp_path / "corpus.json",
        prompt_file=baseline_root / "prompt.md",
        advisor_corpus_file=baseline_root / "corpus.yaml",
    )
    assert changed_prompt.generation_id != baseline.generation_id
    assert changed_corpus.generation_id != changed_prompt.generation_id


def test_operation_doc_and_semantic_operation_changes_change_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _build_fixture(tmp_path / "base", tmp_path / "base.json")
    operation_doc = tmp_path / "operation-definitions.md"
    operation_doc.write_bytes(b"operation documentation v1")
    monkeypatch.setattr(generation_manifest, "DEFAULT_OPERATION_DOC", operation_doc)
    changed_doc = _build_fixture(tmp_path / "doc", tmp_path / "doc.json")
    assert changed_doc.generation_id != baseline.generation_id

    definition = generation_manifest.OPERATION_DEFINITION_LIST[0]
    monkeypatch.setattr(
        generation_manifest,
        "OPERATION_DEFINITION_LIST",
        (replace(definition, summary=definition.summary + " changed"),),
    )
    changed_field = _build_fixture(tmp_path / "field", tmp_path / "field.json")
    assert changed_field.generation_id != changed_doc.generation_id
    assert (
        changed_field.artifact_digests["operation_registry_semantics"]
        != changed_doc.artifact_digests["operation_registry_semantics"]
    )

    def altered_bind(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return None

    monkeypatch.setattr(
        generation_manifest,
        "OPERATION_DEFINITION_LIST",
        (replace(definition, bind=altered_bind),),
    )
    changed_callable = _build_fixture(
        tmp_path / "callable", tmp_path / "callable.json"
    )
    assert changed_callable.generation_id != changed_doc.generation_id
    assert (
        changed_callable.artifact_digests["operation_registry_semantics"]
        != changed_doc.artifact_digests["operation_registry_semantics"]
    )


def test_schema_semantics_change_identity_but_key_order_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _build_fixture(tmp_path / "base", tmp_path / "base.json")
    from kenshi_agent.tooling import schema_documents

    original = schema_documents.base_schema_documents
    docs = original()

    def reordered() -> dict[str, dict[str, object]]:
        return {
            name: dict(reversed(list(document.items())))
            for name, document in reversed(list(docs.items()))
        }

    monkeypatch.setattr(schema_documents, "base_schema_documents", reordered)
    same_schema = _build_fixture(tmp_path / "reordered", tmp_path / "reordered.json")
    assert same_schema.generation_id == baseline.generation_id

    changed = {name: dict(document) for name, document in docs.items()}
    changed["telemetry.schema.json"] = {
        **changed["telemetry.schema.json"],
        "title": "ChangedTelemetrySchema",
    }
    monkeypatch.setattr(schema_documents, "base_schema_documents", lambda: changed)
    changed_schema = _build_fixture(tmp_path / "changed", tmp_path / "changed.json")
    assert changed_schema.generation_id != baseline.generation_id


def test_installed_dll_bytes_change_identity_and_missing_artifacts_are_explicit(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing-artifact.bin"
    missing = _build_fixture(
        tmp_path / "missing",
        tmp_path / "missing.json",
        built_dll=missing_path,
        staged_dll=missing_path,
        installed_dll=missing_path,
    )
    native = missing.metadata.model_dump(mode="json")["native"]
    assert native["built"]["state"] == "absent"
    assert native["staged"]["state"] == "absent"
    assert native["installed"]["state"] == "absent"

    installed = tmp_path / "KenshiAgentTelemetry.dll"
    installed.write_bytes(b"dll-v1")
    first = _build_fixture(tmp_path / "dll1", tmp_path / "dll1.json", installed_dll=installed)
    installed.write_bytes(b"dll-v2")
    second = _build_fixture(tmp_path / "dll2", tmp_path / "dll2.json", installed_dll=installed)
    assert first.generation_id != second.generation_id
    assert (
        first.metadata.model_dump(mode="json")["native"]["installed"]["sha256"]
        == hashlib.sha256(b"dll-v1").hexdigest()
    )


def test_kenshi_target_version_and_observed_executable_evidence(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "kenshi_x64.exe"
    executable.write_bytes(b"not-the-authoritative-kenshi")
    manifest = _build_fixture(
        tmp_path / "fixture", tmp_path / "manifest.json", kenshi_executable=executable
    )
    evidence = manifest.metadata.model_dump(mode="json")["kenshi"]
    assert evidence["repository_target"]["version"] == "1.0.65"
    assert evidence["observed_executable"]["state"] == "present"
    assert evidence["observed_matches_repository_target"] is False


def test_scenario_and_authored_start_are_distinct_and_attestation_is_not_volatile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = {
        "scenario_id": "hub-outdoor-safe-broke-solo-day",
        "save_id": "hub-start-v1",
        "environment": "outdoor",
        "danger": "safe",
        "economy": "broke",
        "party": "solo",
        "time_of_day": "day",
    }
    config, prompt, corpus = _config_fixture(tmp_path / "scenario", scenario=scenario)
    first = build_generation_manifest(
        config_path=config,
        output=tmp_path / "scenario-1.json",
        prompt_file=prompt,
        advisor_corpus_file=corpus,
    )
    authored_config, authored_prompt, authored_corpus = _config_fixture(
        tmp_path / "authored"
    )
    second = build_generation_manifest(
        config_path=authored_config,
        output=tmp_path / "scenario-2.json",
        prompt_file=authored_prompt,
        advisor_corpus_file=authored_corpus,
        game_start="kae-03-broke-pair",
    )
    first_metadata = first.metadata.model_dump(mode="json")
    second_metadata = second.metadata.model_dump(mode="json")
    assert first_metadata["scenario"]["state"] == "present"
    assert first_metadata["authored_start"]["state"] == "absent"
    assert second_metadata["scenario"]["state"] == "absent"
    assert second_metadata["authored_start"]["state"] == "present"
    assert first_metadata["scenario"] != second_metadata["authored_start"]

    assert first_metadata["scenario"]["identity"] == scenario

    combined = build_generation_manifest(
        config_path=config,
        output=tmp_path / "scenario-and-start.json",
        prompt_file=prompt,
        advisor_corpus_file=corpus,
        game_start="kae-03-broke-pair",
    )
    combined_metadata = combined.metadata.model_dump(mode="json")
    assert combined_metadata["scenario"]["state"] == "present"
    assert combined_metadata["authored_start"]["state"] == "present"

    attestation_scenario = ScenarioIdentity.model_validate(scenario)
    attestation_one = ScenarioAttestation(
        scenario=attestation_scenario,
        fixture_digest="a" * 64,
        identity_session_id="session-one",
        loaded_sequence=4,
        verified_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed=ScenarioObservedState(
            selected_character_id="character-0",
            indoors=False,
            in_combat=False,
            money=20,
            party_size=1,
            minute_of_day=600,
        ),
    )
    attestation_two = attestation_one.model_copy(
        update={
            "identity_session_id": "session-two",
            "loaded_sequence": 88,
            "verified_at": datetime(2026, 2, 2, tzinfo=UTC),
            "observed": attestation_one.observed.model_copy(update={"money": 99}),
        }
    )
    from kenshi_agent.tooling import scenario_fixtures

    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text("{}", encoding="utf-8")
    attestations = iter((attestation_one, attestation_two))
    monkeypatch.setattr(
        scenario_fixtures,
        "load_verified_scenario_attestation",
        lambda path: next(attestations),
    )
    attested_one = _build_fixture(
        tmp_path / "attested-one",
        tmp_path / "attested-one.json",
        scenario_attestation=attestation_path,
    )
    attested_two = _build_fixture(
        tmp_path / "attested-two",
        tmp_path / "attested-two.json",
        scenario_attestation=attestation_path,
    )
    assert attested_one.generation_id == attested_two.generation_id
    assert (
        attested_one.metadata.model_dump(mode="json")["scenario"]
        == attested_two.metadata.model_dump(mode="json")["scenario"]
    )


def test_serialized_output_has_strict_ezogen_equivalent_top_level_shape(
    tmp_path: Path,
) -> None:
    manifest = _build_fixture(tmp_path / "fixture", tmp_path / "manifest.json")

    class StrictEquivalentManifest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        generation_id: str
        parent_generation_id: str | None
        created_at: str
        subject: str
        source_ref: str
        capability_manifest_digest: str
        artifact_digests: dict[str, str]
        models: dict[str, str]
        prompts: dict[str, str]
        config: dict[str, str]
        metadata: dict[str, object]

    serialized = json.loads(canonical_json(manifest.model_dump(mode="json")))
    validated = StrictEquivalentManifest.model_validate(serialized)
    assert validated.generation_id == manifest.generation_id


def test_public_dev_generation_manifest_is_local_on_non_windows(tmp_path: Path) -> None:
    output = tmp_path / "manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            str(generation_manifest.ROOT / "dev"),
            "generation-manifest",
            "--output",
            str(output),
        ],
        cwd=generation_manifest.ROOT,
        env={
            **os.environ,
            "PATH": os.environ.get("PATH", ""),
            "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert result.stdout.strip() == json.loads(output.read_text(encoding="utf-8"))["generation_id"]
