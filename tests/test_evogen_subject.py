from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from kenshi_agent.evogen_subject import build_subject_plugin
from kenshi_agent.evogen_subject.adapter import (
    _CANDIDATE_SOURCE_DIGEST,
    SCENARIOS,
    KAEConformanceRunner,
    _finish_after,
)


def test_ordinary_kenshi_imports_do_not_load_evogen() -> None:
    script = """
import builtins, sys
real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'evogen' or name.startswith('evogen.'):
        raise AssertionError('ordinary KAE import reached EvoGen')
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked
import kenshi_agent
import kenshi_agent.core
import kenshi_agent.application
import kenshi_agent.control
import kenshi_agent.env.live
import kenshi_agent.env.mock
import kenshi_agent.env.replay
import kenshi_agent.native_commands
import kenshi_agent.tooling.trajectory_export
assert not any(name == 'evogen' or name.startswith('evogen.') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_metadata_declares_g15_entrypoint_and_exact_optional_pin() -> None:
    metadata = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '[project.entry-points."evogen.subjects"]' in metadata
    assert 'kenshi = "kenshi_agent.evogen_subject:build_subject_plugin"' in metadata
    assert "5e72ca364f0a1b2c5b23d41c9af5a2a15099b946" in metadata
    assert "python_version < '3.14'" in metadata


def test_runner_is_strict_synthetic_and_preserves_receipt_outcome_boundary(tmp_path: Path) -> None:
    pytest.importorskip("evogen")
    from evogen.core.models import GenerationManifest
    from evogen.trace.io import read_jsonl_events

    runner = KAEConformanceRunner()
    generation = GenerationManifest(
        generation_id="0" * 64,
        subject="kenshi",
        source_ref="test",
        capability_manifest_digest="pending",
    )
    with pytest.raises(ValueError, match="unsupported scenario"):
        runner.run(generation=generation, scenario_id="arbitrary", seed=0, trace_directory=tmp_path)
    record, events = runner.run(
        generation=generation,
        scenario_id=SCENARIOS[0],
        seed=0,
        trace_directory=tmp_path,
    )
    assert [event.sequence for event in events] == list(range(len(events)))
    assert events[2].kind.value == "dispatch"
    assert events[3].kind.value == "execution_receipt"
    assert events[3].payload["world_effect_proven"] is False
    assert events[4].kind.value == "outcome_observation"
    assert events[4].payload["later_independent_observation"] is True
    assert events[4].payload["receipt_is_not_outcome"] is True
    assert read_jsonl_events(Path(record.metadata["trace_path"])) == events


def test_runner_timestamp_guard_handles_microsecond_rollover() -> None:
    started = datetime(2026, 1, 1, 0, 0, 0, 999999)
    assert _finish_after(started, started) == datetime(2026, 1, 1, 0, 0, 1)


def test_wrong_candidate_bytes_do_not_improve(tmp_path: Path) -> None:
    pytest.importorskip("evogen")
    from evogen.core.models import GenerationManifest

    candidate_path = tmp_path / "candidate.py"
    candidate_path.write_text("def build_plugin():\n    return {}\n", encoding="utf-8")
    generation = GenerationManifest(
        generation_id="1" * 64,
        subject="kenshi",
        source_ref="candidate",
        capability_manifest_digest="pending",
        artifact_digests={"plugin": _CANDIDATE_SOURCE_DIGEST},
        metadata={
            "candidate_source_path": str(candidate_path),
            "candidate_source_digest": _CANDIDATE_SOURCE_DIGEST,
        },
    )
    record, _events = KAEConformanceRunner().run(
        generation=generation,
        scenario_id=SCENARIOS[0],
        trace_directory=tmp_path / "trace",
    )
    assert record.success is False
    assert record.termination == "goal_blocked"


def test_child_manifest_contains_experiment_bound_synthetic_capability() -> None:
    pytest.importorskip("evogen")
    from evogen.core.models import GenerationManifest

    experiment_digest = "2" * 64
    generation = GenerationManifest(
        generation_id="3" * 64,
        subject="kenshi",
        source_ref="candidate",
        capability_manifest_digest="pending",
        metadata={
            "synthetic_capability": {
                "experiment_id": "experiment-test",
                "experiment_digest": experiment_digest,
                "source_digest": _CANDIDATE_SOURCE_DIGEST,
                "implementation_digest": _CANDIDATE_SOURCE_DIGEST,
            }
        },
    )
    capability = KAEConformanceRunner().capability_manifest(generation).by_name(
        "kae_synthetic_observation"
    )
    assert capability is not None
    assert capability.evidence_state.value == "proven"
    assert capability.proof_class.value == "synthetic"
    assert capability.evidence_refs[0].content_digest == experiment_digest
    assert "candidate-source:" + _CANDIDATE_SOURCE_DIGEST in capability.implementation_ref
    assert "plugin-artifact:" + _CANDIDATE_SOURCE_DIGEST in capability.implementation_ref
    assert "live" in " ".join(capability.limitations)


def test_plugin_exposes_all_api_11_factories_without_probe_roles() -> None:
    plugin = build_subject_plugin()
    assert plugin.name == "kenshi"
    assert plugin.api_version == "1.1"
    for name in (
        "runner_factory",
        "investigator_factory",
        "builder_factory",
        "reviewer_factory",
        "evaluator_factory",
        "materializer_factory",
        "doctor_factory",
        "bootstrap_factory",
        "conformance_factory",
    ):
        assert callable(getattr(plugin, name))
    assert plugin.probe_roles_factory is None


def test_adapter_has_no_runtime_environment_or_control_imports() -> None:
    source = Path("src/kenshi_agent/evogen_subject/adapter.py").read_text(encoding="utf-8")
    assert "from kenshi_agent.env" not in source
    assert "from kenshi_agent.control" not in source
    assert "from kenshi_agent.native_commands" not in source
    assert "ReplayEnvironment(" not in source
    assert "LiveEnvironment(" not in source


def test_host_conformance_passes_all_boundaries_without_live_kenshi(
    tmp_path: Path,
) -> None:
    pytest.importorskip("evogen")
    import evogen.adapters.conformance as conformance
    from evogen.adapters.subjects import discover_subject_entry_points, load_subject_plugin

    entries = discover_subject_entry_points()
    matches = [entry for entry in entries if entry.name == "kenshi"]
    assert len(matches) == 1
    assert matches[0].value == "kenshi_agent.evogen_subject:build_subject_plugin"
    assert load_subject_plugin("kenshi").name == "kenshi"
    report = conformance.run_subject_conformance("kenshi", workspace=tmp_path / "doctor")
    assert report.passed is True
    assert [check.status for check in report.checks] == ["pass"] * 7
    assert report.diagnostics.items == []


def test_doctor_and_conformance_do_not_touch_runtime_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("evogen")
    import ctypes
    import os

    import evogen.adapters.conformance as conformance

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"forbidden runtime operation: {args!r} {kwargs!r}")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(ctypes, "CDLL", forbidden)
    watched = (
        Path("config/default.yaml"),
        Path("docs/generated/CAPABILITY_MANIFEST.json"),
        Path("docs/generated/SESSION_EVENT_DISPOSITIONS.json"),
    )
    before = {path: path.read_bytes() for path in watched}
    report = conformance.run_subject_conformance("kenshi", workspace=tmp_path / "doctor")
    assert report.passed is True
    assert {path: path.read_bytes() for path in watched} == before
