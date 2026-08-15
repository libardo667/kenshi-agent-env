"""KAE-owned, synthetic-only EvoGen subject implementation.

G15 exposes a bounded conformance subject, not a second KAE runtime.  The
runner never imports or constructs live/replay environments, controllers,
native transport, saves, DLLs, or run bundles.  Its traces are intentionally
synthetic and preserve the distinction between a receipt and a later outcome
observation.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

API_VERSION = "1.1"
SUBJECT_NAME = "kenshi"
SCENARIOS = (
    "kae-g15-revealing",
    "kae-g15-variant",
    "kae-g15-regression",
    "kae-g15-long-horizon",
)
FIXTURE_SCENARIOS = SCENARIOS[:2]
CATEGORIES = {
    SCENARIOS[0]: "revealing",
    SCENARIOS[1]: "variant",
    SCENARIOS[2]: "regression",
    SCENARIOS[3]: "long_horizon",
}


def _evogen() -> dict[str, Any]:
    """Load the public API only after EvoGen explicitly asks for this plugin."""

    import importlib

    modules = {
        "subjects": importlib.import_module("evogen.adapters.subjects"),
        "enums": importlib.import_module("evogen.core.enums"),
        "ids": importlib.import_module("evogen.core.ids"),
        "models": importlib.import_module("evogen.core.models"),
    }
    api: dict[str, Any] = {"SubjectBootstrap": modules["subjects"].SubjectBootstrap}
    api.update(
        {
            name: getattr(modules["enums"], name)
            for name in (
                "CapabilityKind",
                "Completeness",
                "EventKind",
                "EvidenceState",
                "FailureLayer",
                "GateVerdict",
                "ProofClass",
                "ResolutionKind",
                "Severity",
            )
        }
    )
    api.update({name: getattr(modules["ids"], name) for name in ("sha256_bytes", "stable_digest")})
    api.update(
        {
            name: getattr(modules["models"], name)
            for name in (
                "ArtifactRef",
                "BoundedCollection",
                "CapabilityDefinition",
                "CapabilityEvidenceRef",
                "CapabilityIssue",
                "CapabilityManifest",
                "CapabilitySpec",
                "CandidateManifest",
                "EvaluationCase",
                "EvaluationOutcome",
                "EvaluationSuiteManifest",
                "EnvironmentOperation",
                "EvolutionPlan",
                "GenerationManifest",
                "GateDecision",
                "InvestigationReport",
                "IssueClassification",
                "MetricVector",
                "ProtectedPathHash",
                "ReviewFinding",
                "ReviewReport",
                "RunRecord",
                "ScenarioResult",
                "SubjectConformanceFixture",
                "SubjectMetricVector",
                "TrajectoryEvent",
            )
        }
    )
    return api


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _finish_after(started: datetime, finished: datetime) -> datetime:
    if finished > started:
        return finished
    return started + timedelta(microseconds=1)


def _authority_path() -> Path:
    """Locate the generated KAE authority without creating a second registry."""

    repository_root = Path(__file__).resolve().parents[3]
    candidates = (
        repository_root / "docs" / "generated" / "CAPABILITY_MANIFEST.json",
        Path(sys.prefix) / "share" / "kenshi-agent-env" / "evogen" / "CAPABILITY_MANIFEST.json",
    )
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            return path
    raise RuntimeError(
        "KAE generated capability authority is unavailable; install the repository "
        "authority package or run the generated-artifact gate first."
    )


def _authority_bytes() -> bytes:
    path = _authority_path()
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"KAE capability authority is corrupt: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), list):
        raise RuntimeError("KAE capability authority has an invalid generated shape")
    return raw


def _generation_authority_path() -> Path:
    repository_root = Path(__file__).resolve().parents[3]
    candidates = (
        repository_root / "src" / "kenshi_agent" / "tooling" / "generation_manifest.py",
        Path(__file__).resolve().parents[1] / "tooling" / "generation_manifest.py",
    )
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            return path
    raise RuntimeError("KAE generation-manifest authority is unavailable")


def _generation_authority_projection() -> dict[str, str]:
    authority = _generation_authority_path()
    return {
        "authority": "kenshi-agent-env.generation-manifest",
        "authority_source_sha256": hashlib.sha256(authority.read_bytes()).hexdigest(),
        "capability_authority_sha256": hashlib.sha256(_authority_bytes()).hexdigest(),
        "contract": "G11-generated-capability-projection",
    }


def _baseline_generation_id() -> str:
    projection = json.dumps(
        _generation_authority_projection(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(projection).hexdigest()


BASELINE_GENERATION: str = _baseline_generation_id()


def _capability_manifest(
    generation_id: str,
    *,
    synthetic_capability: dict[str, str] | None = None,
) -> Any:
    api = _evogen()
    payload = json.loads(_authority_bytes().decode("utf-8"))
    payload["generation_id"] = generation_id
    if synthetic_capability is not None:
        experiment_id = synthetic_capability["experiment_id"]
        experiment_digest = synthetic_capability["experiment_digest"]
        source_digest = synthetic_capability["source_digest"]
        implementation_digest = synthetic_capability["implementation_digest"]
        synthetic_definition = api["CapabilityDefinition"](
            name="kae_synthetic_observation",
            purpose="Record a later independent observation in the bounded synthetic subject.",
            kind=api["CapabilityKind"].VERIFICATION,
            semantic_effects=["later_independent_observation"],
            owner_component="kenshi_agent.evogen_subject",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            applicability="G15 synthetic conformance cases only.",
            completion_evidence=["A typed experiment records receipt and later outcome evidence."],
            evidence_refs=[
                api["CapabilityEvidenceRef"](
                    authority_ref=f"experiment:{experiment_id}",
                    content_digest=experiment_digest,
                    evidence_state=api["EvidenceState"].PROVEN,
                    proof_class=api["ProofClass"].SYNTHETIC,
                )
            ],
            implementation_ref=(
                f"candidate-source:{source_digest};plugin-artifact:{implementation_digest}"
            ),
            evidence_state=api["EvidenceState"].PROVEN,
            proof_class=api["ProofClass"].SYNTHETIC,
            introduced_generation=generation_id,
            limitations=[
                "No live control, replay ingestion, native transport, or world-effect claim."
            ],
        )
        payload["capabilities"].append(synthetic_definition.model_dump(mode="json"))
        payload["capabilities"] = sorted(payload["capabilities"], key=lambda item: item["name"])
    return api["CapabilityManifest"].model_validate(payload)


def _artifact(store: Any, value: Any) -> Any:
    return store.put_model(value)


class KAEConformanceRunner:
    """Deterministic read-only runner for the G15 conformance contract."""

    def __init__(self) -> None:
        self._run_counter = 0

    def capability_manifest(self, generation: Any) -> Any:
        return _capability_manifest(
            generation.generation_id,
            synthetic_capability=generation.metadata.get("synthetic_capability"),
        )

    def run(
        self,
        *,
        generation: Any,
        scenario_id: str,
        seed: int = 0,
        trace_directory: Path,
    ) -> tuple[Any, list[Any]]:
        api = _evogen()
        if scenario_id not in SCENARIOS:
            raise ValueError(f"KAE G15 runner rejects unsupported scenario {scenario_id!r}")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError("KAE G15 runner requires a non-negative integer seed")
        if trace_directory.exists() and trace_directory.is_symlink():
            raise ValueError("KAE G15 runner refuses a symlink trace directory")
        trace_directory.mkdir(parents=True, exist_ok=True)
        run_id = _stable_id("run", generation.generation_id, scenario_id, seed, self._run_counter)
        self._run_counter += 1
        path = trace_directory / f"{run_id}.jsonl"
        started = datetime.now(UTC)
        category = CATEGORIES[scenario_id]
        baseline = generation.metadata.get("baseline_marker") is True
        success = (
            category not in {"revealing", "variant"}
            if baseline
            else _candidate_source_is_authoritative(generation)
        )
        before_revision = _stable_id("world", scenario_id, seed)
        after_revision = _stable_id("world", scenario_id, seed, "outcome")
        target_fingerprint = hashlib.sha256(scenario_id.encode()).hexdigest()
        event_specs = [
            (
                api["EventKind"].OBSERVATION,
                {"synthetic": True, "read_only": True, "case_fingerprint": target_fingerprint},
                before_revision,
            ),
            (
                api["EventKind"].DECISION,
                {"choice": "bounded_synthetic_probe", "synthetic": True},
                before_revision,
            ),
            (
                api["EventKind"].DISPATCH,
                {"accepted": True, "proof": "none", "synthetic": True},
                before_revision,
            ),
            (
                api["EventKind"].EXECUTION_RECEIPT,
                {
                    "receipt": "accepted",
                    "world_effect_proven": False,
                    "synthetic": True,
                },
                before_revision,
            ),
            (
                api["EventKind"].OUTCOME_OBSERVATION,
                {
                    "later_independent_observation": True,
                    "world_effect_proven": success,
                    "receipt_is_not_outcome": True,
                    "synthetic": True,
                },
                after_revision,
            ),
            (
                api["EventKind"].GOAL_ACHIEVED if success else api["EventKind"].GOAL_BLOCKED,
                {"success": success, "category": category, "synthetic": True},
                after_revision,
            ),
        ]
        events: list[Any] = []
        for sequence, (kind, payload, revision) in enumerate(event_specs):
            events.append(
                api["TrajectoryEvent"](
                    envelope_version="1.0",
                    event_id=_stable_id("event", run_id, sequence),
                    run_id=run_id,
                    generation_id=generation.generation_id,
                    scenario_id=scenario_id,
                    sequence=sequence,
                    recorded_at=started,
                    kind=kind,
                    world_revision=revision,
                    source_event_type=None,
                    source_event_id=None,
                    source_sequence=None,
                    source_step_index=sequence,
                    source_world_revision=revision,
                    payload=payload,
                )
            )
        with path.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(event.model_dump_json())
                handle.write("\n")
        finished = _finish_after(started, datetime.now(UTC))
        return (
            api["RunRecord"](
                run_id=run_id,
                generation_id=generation.generation_id,
                scenario_id=scenario_id,
                started_at=started,
                finished_at=finished,
                success=success,
                termination="goal_achieved" if success else "goal_blocked",
                trace_digest=api["sha256_bytes"](path.read_bytes()),
                steps=len(events),
                interventions=0,
                invalid_actions=0,
                metadata={
                    "category": category,
                    "seed": seed,
                    "trace_path": str(path),
                    "synthetic": True,
                    "read_only": True,
                    "environment": "conformance_only",
                },
            ),
            events,
        )


class KAEInvestigator:
    def investigate(self, issue: Any) -> Any:
        api = _evogen()
        return api["InvestigationReport"](
            report_id=_stable_id("investigation", issue.issue_id),
            issue_id=issue.issue_id,
            inspected_sources=["KAE generated capability authority"],
            candidate_operations=[
                api["EnvironmentOperation"](
                    name="synthetic_conformance_observation",
                    semantic_effects=["later_independent_observation"],
                    description="Read-only synthetic observation used only by G15 conformance.",
                    source_ref="kenshi-agent-env:G15",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    constraints=["No live environment or controller access."],
                )
            ],
            rejected_operations=["live_kenshi_control", "replay_environment_mutation"],
            remaining_unknowns=["No live or replay claim is made by G15."],
            conclusion="The bounded synthetic adapter is sufficient for host conformance.",
        )


_CANDIDATE_SOURCE = (
    '"""Generic candidate payload for KAE conformance."""\n\n'
    "\n"
    "def build_plugin():\n"
    '    return {"semantic_effect": "later_independent_observation"}\n'
)
_CANDIDATE_SOURCE_DIGEST = hashlib.sha256(_CANDIDATE_SOURCE.encode("utf-8")).hexdigest()


def _candidate_source_is_authoritative(generation: Any) -> bool:
    metadata = generation.metadata
    source_path_value = metadata.get("candidate_source_path")
    declared_digest = metadata.get("candidate_source_digest")
    artifact_digest = generation.artifact_digests.get("plugin")
    if not all(
        isinstance(value, str) for value in (source_path_value, declared_digest, artifact_digest)
    ):
        return False
    source_path = Path(source_path_value)
    if source_path.is_symlink() or not source_path.is_file():
        return False
    actual_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return bool(actual_digest == declared_digest == artifact_digest == _CANDIDATE_SOURCE_DIGEST)


class KAEBuilder:
    def build(self, *, parent: Any, issue: Any, specification: Any, candidate_root: Path) -> Any:
        api = _evogen()
        if specification.capability_name != "kae_synthetic_observation":
            raise ValueError("KAE G15 builder rejects unsupported capability specifications")
        candidate_id = _stable_id(
            "candidate", parent.generation_id, issue.issue_id, specification.spec_id
        )
        workspace = candidate_root / candidate_id
        plugin_path = workspace / "plugins" / "capability.py"
        plugin_path.parent.mkdir(parents=True, exist_ok=False)
        plugin_path.write_text(_CANDIDATE_SOURCE, encoding="utf-8")
        source = plugin_path.read_bytes()
        source_digest = api["sha256_bytes"](source)
        spec_digest = api["stable_digest"](specification.model_dump(mode="json"))
        issue_digest = api["stable_digest"](issue.model_dump(mode="json"))
        return api["CandidateManifest"](
            candidate_id=candidate_id,
            parent_generation=parent.generation_id,
            issue_id=issue.issue_id,
            spec_id=specification.spec_id,
            workspace_path=str(workspace),
            source_digest=source_digest,
            artifact_digests={
                "plugin": source_digest,
                "specification": spec_digest,
                "issue": issue_digest,
            },
            changed_files=["plugins/capability.py"],
            claimed_capabilities=[specification.capability_name],
            file_digests={"plugins/capability.py": source_digest},
            workspace_file_digests={"plugins/capability.py": source_digest},
            metadata={"builder": "KAEBuilder", "implementation_mode": "synthetic_conformance"},
        )


class KAEReviewer:
    def review(self, candidate: Any, *, forbidden_literals: list[str] | None = None) -> Any:
        api = _evogen()
        root = Path(candidate.workspace_path)
        findings: list[Any] = []
        checks: dict[str, bool] = {}
        files = []
        for relative, _digest in candidate.workspace_file_digests.items():
            path = (root / relative).resolve()
            safe = not Path(relative).is_absolute() and ".." not in Path(relative).parts
            if safe and path.is_file() and root.resolve() in path.parents:
                files.append(path)
            else:
                findings.append(
                    api["ReviewFinding"](
                        severity=api["Severity"].CRITICAL, code="unsafe_path", message=relative
                    )
                )
        checks["workspace_files_present"] = bool(files) and len(files) == len(
            candidate.workspace_file_digests
        )
        checks["workspace_paths_safe"] = not any(f.code == "unsafe_path" for f in findings)
        checks["digests_match"] = all(
            api["sha256_bytes"](path.read_bytes())
            == candidate.workspace_file_digests[str(path.relative_to(root))]
            for path in files
        )
        checks["python_source_compiles"] = True
        for path in files:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except (OSError, SyntaxError) as exc:
                checks["python_source_compiles"] = False
                findings.append(
                    api["ReviewFinding"](
                        severity=api["Severity"].CRITICAL,
                        code="compile_failed",
                        message=str(exc),
                        file=str(path.relative_to(root)),
                    )
                )
            text = path.read_text(encoding="utf-8")
            for literal in forbidden_literals or []:
                if literal and literal in text:
                    findings.append(
                        api["ReviewFinding"](
                            severity=api["Severity"].HIGH,
                            code="revealing_literal",
                            message=literal,
                            file=str(path.relative_to(root)),
                        )
                    )
        checks["forbidden_literals_absent"] = not any(
            f.code == "revealing_literal" for f in findings
        )
        checks["candidate_authority_separate"] = True
        passed = all(checks.values()) and not findings
        return api["ReviewReport"](
            review_id=_stable_id("review", candidate.candidate_id),
            candidate_id=candidate.candidate_id,
            passed=passed,
            checks=checks,
            findings=findings,
            reviewed_files=[str(path.relative_to(root)) for path in files],
        )


class KAEEvaluator:
    def __init__(self, *, runner: KAEConformanceRunner, ledger: Any) -> None:
        self.runner = runner
        self.ledger = ledger

    def evaluate(
        self,
        *,
        baseline: Any,
        candidate: Any,
        evaluation_suite: Any,
        trace_directory: Path,
        review_passed: bool,
    ) -> Any:
        api = _evogen()
        candidate_path = Path(candidate.workspace_path) / "plugins" / "capability.py"
        if candidate_path.is_symlink() or not candidate_path.is_file():
            raise ValueError("KAE evaluator requires the declared candidate source file")
        actual_source_digest = api["sha256_bytes"](candidate_path.read_bytes())
        if actual_source_digest != candidate.source_digest:
            raise ValueError("KAE evaluator detected candidate source digest drift")
        if candidate.artifact_digests.get("plugin") != actual_source_digest:
            raise ValueError("KAE evaluator detected candidate plugin artifact drift")
        started = datetime.now(UTC)
        baseline_results: list[Any] = []
        candidate_results: list[Any] = []
        cases = [
            *evaluation_suite.revealing_cases,
            *evaluation_suite.structural_variants,
            *evaluation_suite.regression_suites,
            *evaluation_suite.long_horizon_suites,
        ]
        for generation, output, label in (
            (baseline, baseline_results, "baseline"),
            (
                api["GenerationManifest"](
                    generation_id=candidate.candidate_id,
                    parent_generation_id=baseline.generation_id,
                    subject=baseline.subject,
                    source_ref=f"candidate:{candidate.source_digest}",
                    capability_manifest_digest=api["stable_digest"](
                        self.runner.capability_manifest(
                            api["GenerationManifest"](
                                generation_id=candidate.candidate_id,
                                subject=baseline.subject,
                                source_ref="candidate",
                                capability_manifest_digest="pending",
                            )
                        ).model_dump(mode="json")
                    ),
                    artifact_digests=candidate.artifact_digests,
                    config={"synthetic": True},
                    metadata={
                        "candidate_source_path": str(candidate_path),
                        "candidate_source_digest": actual_source_digest,
                    },
                ),
                candidate_results,
                "candidate",
            ),
        ):
            for case in cases:
                for seed in case.seeds:
                    for repeat_index in range(case.repeat_count):
                        record, events = self.runner.run(
                            generation=generation,
                            scenario_id=case.scenario_id,
                            seed=seed,
                            trace_directory=trace_directory / label / case.scenario_id,
                        )
                        self.ledger.add_run(record, events)
                        elapsed = max(
                            (record.finished_at - record.started_at).total_seconds(), 0.000001
                        )
                        output.append(
                            api["ScenarioResult"](
                                scenario_id=case.scenario_id,
                                category=case.category,
                                seed=seed,
                                repeat_index=repeat_index,
                                elapsed_seconds=elapsed,
                                success=record.success,
                                steps=record.steps,
                                interventions=record.interventions,
                                invalid_actions=record.invalid_actions,
                                blocked=record.termination == "goal_blocked",
                                termination=record.termination,
                                run_id=record.run_id,
                                trace_digest=record.trace_digest,
                            )
                        )
        finished = datetime.now(UTC)

        def metrics(results: list[Any]) -> Any:
            def rate(category: str) -> float:
                selected = [r for r in results if r.category == category]
                return sum(r.success for r in selected) / len(selected) if selected else 1.0

            return api["MetricVector"](
                revealing_success_rate=rate("revealing"),
                variant_success_rate=rate("variant"),
                regression_success_rate=rate("regression"),
                long_horizon_success_rate=rate("long_horizon"),
                intervention_count=sum(r.interventions for r in results),
                invalid_action_count=sum(r.invalid_actions for r in results),
                blocked_run_count=sum(r.blocked for r in results),
                average_steps=sum(r.steps for r in results) / len(results),
                new_high_severity_issues=0,
            )

        baseline_metrics, candidate_metrics = metrics(baseline_results), metrics(candidate_results)
        suite_metric = [
            api["SubjectMetricVector"](
                namespace=evaluation_suite.subject_metric_namespace,
                metrics={
                    "evaluated_runs": len(baseline_results),
                    "successful_runs": sum(r.success for r in baseline_results),
                },
            )
        ]
        candidate_metric = [
            api["SubjectMetricVector"](
                namespace=evaluation_suite.subject_metric_namespace,
                metrics={
                    "evaluated_runs": len(candidate_results),
                    "successful_runs": sum(r.success for r in candidate_results),
                },
            )
        ]
        return api["EvaluationOutcome"](
            experiment_id=_stable_id("experiment", candidate.candidate_id),
            candidate_id=candidate.candidate_id,
            baseline_generation=baseline.generation_id,
            started_at=started,
            finished_at=finished,
            baseline_results=baseline_results,
            candidate_results=candidate_results,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            prediction_matched=candidate_metrics.revealing_success_rate == 1.0
            and candidate_metrics.revealing_success_rate > baseline_metrics.revealing_success_rate
            and candidate_metrics.variant_success_rate > baseline_metrics.variant_success_rate,
            review_passed=review_passed,
            baseline_subject_metrics=suite_metric,
            candidate_subject_metrics=candidate_metric,
            notes=[
                "Synthetic, read-only, conformance-only KAE subject evidence.",
                "Dispatch and receipt are not treated as proof; outcome is later and independent.",
            ],
        )


class KAEMaterializer:
    def __init__(self, *, runner: KAEConformanceRunner, artifacts: Any) -> None:
        self.runner = runner
        self.artifacts = artifacts

    def materialize(self, *, baseline: Any, candidate: Any, experiment: Any, decision: Any) -> Any:
        api = _evogen()
        if decision.verdict is not api["GateVerdict"].RETAIN:
            raise ValueError("KAE materializer refuses non-retained decisions")
        generation_id = _stable_id(
            "generation", baseline.generation_id, candidate.candidate_id, experiment.experiment_id
        )
        provisional = api["GenerationManifest"](
            generation_id=generation_id,
            parent_generation_id=baseline.generation_id,
            subject=SUBJECT_NAME,
            source_ref=f"candidate:{candidate.source_digest}",
            capability_manifest_digest="pending",
            artifact_digests={
                **candidate.artifact_digests,
                "experiment": candidate.artifact_digests["experiment_object"],
            },
            config={"synthetic": True},
            metadata={
                "retained_from_candidate": candidate.candidate_id,
                "experiment_id": experiment.experiment_id,
                "synthetic_capability": {
                    "experiment_id": experiment.experiment_id,
                    "experiment_digest": candidate.artifact_digests["experiment_object"],
                    "source_digest": candidate.source_digest,
                    "implementation_digest": candidate.artifact_digests["plugin"],
                },
            },
        )
        manifest = self.runner.capability_manifest(provisional)
        ref = _artifact(self.artifacts, manifest)
        return provisional.model_copy(
            update={
                "capability_manifest_digest": ref.digest,
                "artifact_digests": {
                    **provisional.artifact_digests,
                    "capability_manifest": ref.digest,
                },
            }
        )


class KAEDoctor:
    def __init__(self, context: Any) -> None:
        self.context = context

    def check(self) -> Any:
        api = _evogen()
        _authority_path()
        _authority_bytes()
        manifest = _capability_manifest(BASELINE_GENERATION)
        cas_digest = self.context.artifacts.put_json(manifest.model_dump(mode="json"))
        if (
            not manifest.capabilities
            or api["stable_digest"](manifest.model_dump(mode="json")) != cas_digest
        ):
            raise RuntimeError(
                "KAE generated capability authority failed typed digest verification"
            )
        return api["BoundedCollection"](
            items=[], completeness=api["Completeness"].COMPLETE, known_total=0
        )


def _bootstrap(context: Any) -> Any:
    api = _evogen()
    manifest = _capability_manifest(BASELINE_GENERATION)
    ref = _artifact(context.artifacts, manifest)
    generation_authority_digest = context.artifacts.put_json(_generation_authority_projection())
    baseline = api["GenerationManifest"](
        generation_id=BASELINE_GENERATION,
        subject=SUBJECT_NAME,
        source_ref="kenshi-agent-env:g11-generation-authority",
        capability_manifest_digest=ref.digest,
        artifact_digests={
            "capability_manifest": ref.digest,
            "capability_authority": context.artifacts.put_bytes(_authority_bytes()),
            "generation_authority": generation_authority_digest,
        },
        config={"synthetic": True, "read_only": True},
        metadata={
            "environment": "conformance_only",
            "baseline_marker": True,
            "generation_authority_digest": generation_authority_digest,
            "withheld_claims": ["live", "replay", "control"],
        },
    )
    plan = api["EvolutionPlan"](
        diagnostic_scenarios=list(SCENARIOS),
        revealing_cases=[SCENARIOS[0]],
        structural_variants=[SCENARIOS[1]],
        regression_suites=[SCENARIOS[2]],
        long_horizon_suites=[SCENARIOS[3]],
        forbidden_literals=list(SCENARIOS),
    )
    source_path = Path(__file__).resolve()
    source_ref = api["ArtifactRef"](
        digest=context.artifacts.put_bytes(source_path.read_bytes()), model="SourceArtifact"
    )
    suite = api["EvaluationSuiteManifest"](
        suite_id="kenshi-g15-suite",
        revealing_cases=[
            api["EvaluationCase"](
                scenario_id=SCENARIOS[0],
                category="revealing",
                seeds=[0],
                repeat_count=1,
                per_run_wall_clock_ceiling_seconds=10.0,
            )
        ],
        structural_variants=[
            api["EvaluationCase"](
                scenario_id=SCENARIOS[1],
                category="variant",
                seeds=[0],
                repeat_count=1,
                per_run_wall_clock_ceiling_seconds=10.0,
            )
        ],
        regression_suites=[
            api["EvaluationCase"](
                scenario_id=SCENARIOS[2],
                category="regression",
                seeds=[0],
                repeat_count=1,
                per_run_wall_clock_ceiling_seconds=10.0,
            )
        ],
        long_horizon_suites=[
            api["EvaluationCase"](
                scenario_id=SCENARIOS[3],
                category="long_horizon",
                seeds=[0],
                repeat_count=1,
                per_run_wall_clock_ceiling_seconds=10.0,
            )
        ],
        total_wall_clock_ceiling_seconds=120.0,
        evaluator_version="kenshi-g15-synthetic-1",
        evaluator=source_ref,
        evaluator_protected_path=str(source_path),
        environment_artifacts={
            "capability_authority.json": api["ArtifactRef"](
                digest=context.artifacts.put_bytes(_authority_bytes()), model="SourceArtifact"
            )
        },
        protected_paths=[
            api["ProtectedPathHash"](
                logical_name=str(source_path),
                absolute_path=str(source_path),
                sha256=api["sha256_bytes"](source_path.read_bytes()),
            )
        ],
        subject_metric_namespace=SUBJECT_NAME,
    )
    return api["SubjectBootstrap"](baseline=baseline, plan=plan, evaluation_suite=suite)


def _fixture(context: Any) -> Any:
    api = _evogen()
    if context.bootstrap is None:
        raise RuntimeError("KAE conformance fixture requires bootstrap")
    generation = context.bootstrap.baseline.generation_id
    issue = api["CapabilityIssue"](
        issue_id="issue-kae-g15-conformance",
        subject_generation=generation,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        title="Synthetic conformance capability",
        symptom_summary="A bounded synthetic case requires later outcome evidence.",
        classification=api["IssueClassification"](
            primary=api["FailureLayer"].OUTCOME_VERIFICATION,
            confidence=1.0,
            rationale="KAE G15 fixture authority.",
        ),
        supporting_evidence=[],
        required_effect="later_independent_observation",
        blocker_type="synthetic_receipt_only",
        proposed_resolution=api["ResolutionKind"].ADD_OUTCOME_EVIDENCE,
        prediction="A later observation distinguishes receipt from world outcome.",
    )
    specification = api["CapabilitySpec"](
        spec_id="spec-kae-g15-conformance",
        issue_id=issue.issue_id,
        parent_generation=generation,
        capability_name="kae_synthetic_observation",
        purpose="Represent a later independent observation in the bounded synthetic subject.",
        semantic_effects=["later_independent_observation"],
        owner_component="kenshi_agent.evogen_subject",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        applicability="Only the G15 synthetic conformance cases.",
        binding_rules=["Use the typed synthetic receipt and later observation."],
        execution_route="conformance-only runner",
        completion_evidence=["A later outcome observation records the world revision."],
        non_goals=["No live control.", "No replay ingestion."],
        prediction="Candidate evidence distinguishes a receipt from a later outcome.",
        revealing_cases=[SCENARIOS[0]],
        structural_variants=[SCENARIOS[1]],
        regression_suites=[SCENARIOS[2]],
        long_horizon_suites=[SCENARIOS[3]],
    )
    return api["SubjectConformanceFixture"](
        scenario_ids=list(FIXTURE_SCENARIOS), seeds=[0, 1], issue=issue, specification=specification
    )


@dataclass(frozen=True)
class KAESubjectPlugin:
    name: str = SUBJECT_NAME
    api_version: str = API_VERSION

    def runner_factory(self, context: Any) -> Any:
        return KAEConformanceRunner()

    def investigator_factory(self, context: Any) -> Any:
        return KAEInvestigator()

    def builder_factory(self, context: Any) -> Any:
        return KAEBuilder()

    def reviewer_factory(self, context: Any) -> Any:
        return KAEReviewer()

    def evaluator_factory(self, context: Any) -> Any:
        if not isinstance(context.runner, KAEConformanceRunner):
            raise RuntimeError("KAE evaluator requires the shared conformance runner")
        return KAEEvaluator(runner=context.runner, ledger=context.ledger)

    def materializer_factory(self, context: Any) -> Any:
        if not isinstance(context.runner, KAEConformanceRunner):
            raise RuntimeError("KAE materializer requires the shared conformance runner")
        return KAEMaterializer(runner=context.runner, artifacts=context.artifacts)

    def doctor_factory(self, context: Any) -> Any:
        return KAEDoctor(context)

    def bootstrap_factory(self, context: Any) -> Any:
        return _bootstrap(context)

    def conformance_factory(self, context: Any) -> Any:
        return _fixture(context)

    @property
    def probe_roles_factory(self) -> None:
        return None


def build_subject_plugin() -> Any:
    return KAESubjectPlugin()


__all__ = ["build_subject_plugin", "KAESubjectPlugin"]
