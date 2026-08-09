"""The reverse-engineering record is a typed repository object, not prose drift."""

from __future__ import annotations

import json

import pytest
import yaml
from pydantic import ValidationError

from kenshi_agent.tooling.research_evidence import (
    PROOF_SECTION_HEADINGS,
    REQUIRED_FILES,
    RESEARCH_ROOT,
    DynamicObservations,
    ResearchCallSites,
    ResearchConclusion,
    load_research_packages,
    render_research_index,
    validate_research_tree,
)


def test_every_research_package_has_the_complete_validated_shape() -> None:
    packages = load_research_packages()

    assert packages
    assert not validate_research_tree()
    for package in packages:
        files = {path.name for path in package.path.iterdir() if path.is_file()}
        assert REQUIRED_FILES <= files
        assert package.call_sites.subsystem == package.path.name
        assert package.observations.subsystem == package.path.name
        assert package.conclusion.subsystem == package.path.name
        if package.conclusion.proof_status == "live_proven":
            for probe_id in package.conclusion.live_probe_ids:
                probe = next(
                    item
                    for item in package.observations.observations
                    if item.id == probe_id
                )
                assert probe.durable_reduced_evidence is not None


def test_repository_call_sites_use_stable_source_anchors_not_line_authority() -> None:
    project_sites = [
        project_site
        for package in load_research_packages()
        for site in package.call_sites.sites
        for project_site in site.project_call_sites
    ]

    assert project_sites
    assert all(len(site.source_sha256) == 64 for site in project_sites)
    assert all(site.enclosing_function for site in project_sites)
    shifted = project_sites[0].model_copy(update={"line": project_sites[0].line + 10_000})
    assert shifted.line > project_sites[0].line


def test_conclusions_carry_binary_abi_probe_crash_and_uncertainty_fields() -> None:
    conclusion = next(
        item.conclusion
        for item in load_research_packages()
        if item.path.name == "context_menu_orders"
    )

    assert conclusion.executable.version
    assert len(conclusion.executable.sha256) == 64
    assert conclusion.libraries
    assert conclusion.source_refs
    assert conclusion.inferred_signature_confidence in {"low", "medium", "high"}
    assert conclusion.live_probe_ids
    assert conclusion.crash_ids
    assert conclusion.contradiction_ids
    assert conclusion.remaining_uncertainty


def test_context_menu_record_withholds_undurable_historical_live_claim() -> None:
    package = next(
        item for item in load_research_packages() if item.path.name == "context_menu_orders"
    )
    live = next(
        item
        for item in package.observations.observations
        if item.id == "character_order_goal_adoption"
    )

    assert package.conclusion.proof_status == "source_proven"
    assert live.run_bundle is None
    assert live.executable_sha256_at_probe is None
    assert "withheld" in live.final_disposition


def test_research_json_models_reject_unversioned_or_unfingerprinted_claims() -> None:
    call_sites = json.loads(
        (RESEARCH_ROOT / "context_menu_orders" / "call_sites.json").read_text(
            encoding="utf-8"
        )
    )
    del call_sites["executable"]["sha256"]
    with pytest.raises(ValidationError):
        ResearchCallSites.model_validate(call_sites)

    observations = json.loads(
        (
            RESEARCH_ROOT
            / "context_menu_orders"
            / "dynamic_observations.json"
        ).read_text(encoding="utf-8")
    )
    del observations["observations"][0]["final_disposition"]
    with pytest.raises(ValidationError):
        DynamicObservations.model_validate(observations)


def test_conclusion_schema_rejects_missing_uncertainty() -> None:
    package = load_research_packages()[0]
    payload = package.conclusion.model_dump()
    payload["remaining_uncertainty"] = []

    with pytest.raises(ValidationError):
        ResearchConclusion.model_validate(payload)


def test_conclusion_keeps_all_four_proof_classes_visibly_separate() -> None:
    body = (RESEARCH_ROOT / "context_menu_orders" / "conclusion.md").read_text(
        encoding="utf-8"
    )

    for heading in PROOF_SECTION_HEADINGS:
        assert heading in body


def test_generated_index_points_to_canonical_packages() -> None:
    rendered = render_research_index()

    assert "context_menu_orders" in rendered
    assert "../../game_sources/research/context_menu_orders/conclusion.md" in rendered
    assert "`source_proven`" in rendered
    assert "1.0.65" in rendered


def test_template_exposes_the_same_six_file_boundary() -> None:
    template = RESEARCH_ROOT / "_template"

    assert {path.name for path in template.iterdir() if path.is_file()} == REQUIRED_FILES


def test_issue_form_asks_the_six_research_boundary_questions() -> None:
    issue_form = (
        RESEARCH_ROOT.parents[1]
        / ".github"
        / "ISSUE_TEMPLATE"
        / "reverse-engineering-evidence.yml"
    )
    payload = yaml.safe_load(issue_form.read_text(encoding="utf-8"))
    labels = {
        item["attributes"]["label"]
        for item in payload["body"]
        if item["type"] == "textarea"
    }

    assert labels == {
        "What engine question was investigated?",
        "What did KenshiLib/ForgottenGUI claim?",
        "What did the binary show?",
        "What did live state show?",
        "What framework authority is being deleted?",
        "What remains withheld?",
    }
    assert all(
        item.get("validations", {}).get("required") is True
        for item in payload["body"]
        if item["type"] == "textarea"
    )


def test_compiled_native_fixture_reads_canonical_research_call_sites() -> None:
    root = RESEARCH_ROOT.parents[1]
    native_test = (
        root / "native" / "KenshiAgentTelemetry" / "NativeCommandProtocolTests.cpp"
    ).read_text(encoding="utf-8")
    build_script = (root / "scripts" / "build_native.ps1").read_text(
        encoding="utf-8"
    )

    for subsystem in {
        "context_menu_orders",
        "inventory_transfer",
        "body_shift",
        "prospecting_window",
    }:
        assert f'"{subsystem}"' in native_test
    assert "call_sites.json" in native_test
    assert 'Join-Path $repo "game_sources\\research"' in build_script
    assert "& $protocolTests $fixtures $research" in build_script
