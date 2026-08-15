from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_generation_manifest import _build_fixture

from kenshi_agent.tooling.capability_manifest import (
    build_capability_manifest,
    write_capability_manifest,
)
from kenshi_agent.tooling.generation_manifest import write_generation_manifest
from kenshi_agent.tooling.trajectory_export import (
    ExportManifest,
    TrajectoryEventEnvelope,
    TrajectoryExportError,
    _payload_revision,
    _validate_source,
    export_trajectory,
)

ROOT = Path(__file__).resolve().parents[1]
REAL_REPORTING_FIXTURE = (
    ROOT / "tests" / "fixtures" / "run_bundles" / "live_reporting_surface" / "events.jsonl"
)
LOCAL_SOAK_FIXTURE = (
    ROOT / "runs" / "protocol-2-native-survival-soak-20260810-r9" / "events.jsonl"
)
LOCAL_SOAK_SHA256 = "542eff1353e00b9cd4cad4c83969e4db9156776d7c55b5e51d01a0356ffb92ef"


def _linked_inputs(
    tmp_path: Path, *, include_scenario: bool = True
) -> tuple[Path, Path, Path]:
    generation_path = tmp_path / "generation.json"
    build_kwargs: dict[str, object] = {}
    if include_scenario:
        build_kwargs["scenario"] = {
            "scenario_id": "scenario-1",
            "save_id": "save-1",
            "environment": "indoor",
            "danger": "safe",
            "economy": "funded",
            "party": "solo",
            "time_of_day": "day",
        }
    generation = _build_fixture(tmp_path, generation_path, **build_kwargs)
    write_generation_manifest(generation, generation_path)
    capability_path = tmp_path / "capability.json"
    write_capability_manifest(
        build_capability_manifest(generation.generation_id), capability_path
    )
    return generation_path, capability_path, tmp_path / "events.jsonl"


def test_legacy_duplicate_records_are_lossless_and_deterministic(tmp_path: Path) -> None:
    generation, capability, events = _linked_inputs(tmp_path)
    record = (
        b'{"event_type":"action_receipt","run_id":"run-1","step_index":0,'
        b'"timestamp":"2026-08-15T00:00:00+00:00","payload":{"command_id":"cmd-1",'
        b'"completed_at_revision":{"telemetry_sequence":2},'
        b'"started_after_revision":{"telemetry_sequence":1}}}\n'
    )
    events.write_bytes(record + record)
    first = tmp_path / "bundle-a"
    second = tmp_path / "bundle-b"
    first_manifest = export_trajectory(events, generation, capability, "scenario-1", first)
    second_manifest = export_trajectory(events, generation, capability, "scenario-1", second)

    assert (first / "raw-events.jsonl").read_bytes() == events.read_bytes()
    assert (first / "trajectory.jsonl").read_bytes() == (second / "trajectory.jsonl").read_bytes()
    rows = [json.loads(line) for line in (first / "trajectory.jsonl").read_text().splitlines()]
    assert len({row["event_id"] for row in rows}) == 2
    assert rows[0]["world_revision"].startswith("kae-revision-sha256:")
    assert rows[0]["payload"]["correlation"]["completed_at_revision"]["telemetry_sequence"] == 2
    assert rows[0]["payload"]["correlation"]["command_id"] == "cmd-1"
    assert first_manifest.model_dump() == second_manifest.model_dump()
    assert first_manifest.bundle_id == second_manifest.bundle_id
    assert first_manifest.generation_linkage == "supplied_external_manifest"
    assert first_manifest.scenario_linkage == "generation_manifest"
    assert first_manifest.generation_manifest_sha256 == hashlib.sha256(
        generation.read_bytes()
    ).hexdigest()
    assert first_manifest.capability_manifest_file_sha256 == hashlib.sha256(
        capability.read_bytes()
    ).hexdigest()
    assert first_manifest.source.source_sequence_counts.missing == 2
    assert first_manifest.trajectory.withheld_projection_kinds == ["binding", "dispatch"]
    assert (
        ExportManifest.model_validate_json((first / "manifest.json").read_bytes())
        == first_manifest
    )
    typed = [
        TrajectoryEventEnvelope.model_validate_json(line)
        for line in (first / "trajectory.jsonl").read_bytes().splitlines()
    ]
    assert [event.sequence for event in typed] == [0, 1]


def test_revision_precedence_skips_null_values() -> None:
    record = {
        "payload": {
            "completed_at_revision": None,
            "world_revision": {"telemetry_sequence": 2},
            "started_after_revision": {"telemetry_sequence": 1},
        }
    }

    assert _payload_revision(record) == {"telemetry_sequence": 2}


def test_bundle_identity_includes_exact_linked_manifest_bytes(tmp_path: Path) -> None:
    generation, capability, events = _linked_inputs(tmp_path)
    _write_records(events, [_record("run_started")])
    first = export_trajectory(
        events, generation, capability, "scenario-1", tmp_path / "bundle-a"
    )

    capability.write_text(
        json.dumps(json.loads(capability.read_text()), indent=2) + "\n",
        encoding="utf-8",
    )
    second = export_trajectory(
        events, generation, capability, "scenario-1", tmp_path / "bundle-b"
    )

    assert first.capability_manifest_digest == second.capability_manifest_digest
    assert (
        first.capability_manifest_file_sha256
        != second.capability_manifest_file_sha256
    )
    assert first.bundle_id != second.bundle_id


def _record(
    event_type: str,
    *,
    run_id: str = "run-1",
    sequence: int | None | object = ...,
    step_index: int | None = None,
    timestamp: str = "2026-08-15T00:00:00+00:00",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "event_type": event_type,
        "run_id": run_id,
        "step_index": step_index,
        "timestamp": timestamp,
        "payload": payload or {},
    }
    if sequence is not ...:
        record["event_sequence"] = sequence
    return record


def _write_records(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def test_checked_in_real_reporting_fixture_has_exact_reviewed_projection(tmp_path: Path) -> None:
    generation, capability, _ = _linked_inputs(tmp_path)
    output = tmp_path / "bundle"
    manifest = export_trajectory(
        REAL_REPORTING_FIXTURE,
        generation,
        capability,
        "scenario-1",
        output,
    )

    assert (output / "raw-events.jsonl").read_bytes() == REAL_REPORTING_FIXTURE.read_bytes()
    assert manifest.source.record_count == 45
    assert manifest.source.sequence_mode == "legacy_prefix"
    assert manifest.source.source_sequence_counts.model_dump() == {
        "present": 0,
        "missing": 45,
    }
    assert manifest.source.source_event_id_counts.model_dump() == {
        "present": 0,
        "missing": 45,
    }
    assert manifest.reviewed_dispositions.disposition_counts == {
        "derived_summary": 7,
        "exact_evogen_event": 9,
        "intentionally_ignored": 3,
        "subject_only_raw_evidence": 26,
    }
    assert manifest.trajectory.kind_counts == {
        "decision": 2,
        "error": 1,
        "execution_receipt": 1,
        "observation": 1,
        "observation_delta": 1,
        "outcome_observation": 1,
        "run_finished": 1,
        "run_started": 1,
    }
    rows = [
        TrajectoryEventEnvelope.model_validate_json(line)
        for line in (output / "trajectory.jsonl").read_bytes().splitlines()
    ]
    assert [row.sequence for row in rows] == list(range(9))
    assert [row.kind for row in rows].count("outcome_observation") == 1
    assert next(row for row in rows if row.source_event_type == "world_state_update").kind == (
        "observation_delta"
    )
    assert all(row.source_sequence is None and row.source_event_id is None for row in rows)
    receipt = next(row for row in rows if row.kind == "execution_receipt")
    outcome = next(row for row in rows if row.kind == "outcome_observation")
    assert receipt.payload["correlation"]["command_id"] == outcome.payload["correlation"][
        "command_id"
    ]
    assert receipt.event_id != outcome.event_id
    assert "goal_achieved" not in {row.kind for row in rows}


@pytest.mark.skipif(
    not LOCAL_SOAK_FIXTURE.is_file(),
    reason="ignored local real-run evidence is not present in a clean clone",
)
def test_local_38293_record_soak_has_zero_unknown_dispositions(tmp_path: Path) -> None:
    assert hashlib.sha256(LOCAL_SOAK_FIXTURE.read_bytes()).hexdigest() == LOCAL_SOAK_SHA256
    generation, capability, _ = _linked_inputs(tmp_path, include_scenario=False)
    manifest = export_trajectory(
        LOCAL_SOAK_FIXTURE,
        generation,
        capability,
        "protocol-2-native-survival-soak-20260810-r9",
        tmp_path / "soak-bundle",
    )
    assert manifest.source.record_count == 38_293
    assert manifest.source.source_sequence_counts.model_dump() == {
        "present": 0,
        "missing": 38_293,
    }
    assert manifest.reviewed_dispositions.disposition_counts == {
        "derived_summary": 390,
        "exact_evogen_event": 22_995,
        "intentionally_ignored": 241,
        "subject_only_raw_evidence": 14_667,
    }
    assert manifest.scenario_linkage == "declared_external"
    assert manifest.trajectory.kind_counts == {
        "decision": 121,
        "error": 11,
        "execution_receipt": 119,
        "observation": 11_308,
        "observation_delta": 11_308,
        "outcome_observation": 126,
        "run_finished": 1,
        "run_started": 1,
    }


def test_legacy_prefix_and_contiguous_sequenced_suffix_preserve_encounter_order(
    tmp_path: Path,
) -> None:
    generation, capability, events = _linked_inputs(tmp_path)
    _write_records(
        events,
        [
            _record("run_started", step_index=99, timestamp="2026-08-15T00:00:02+00:00"),
            _record("campaign_scope", sequence=2, step_index=None),
            _record(
                "run_finished",
                sequence=3,
                step_index=0,
                timestamp="2026-08-15T00:00:01+00:00",
            ),
        ],
    )
    output = tmp_path / "bundle"
    manifest = export_trajectory(events, generation, capability, "scenario-1", output)
    rows = [json.loads(line) for line in (output / "trajectory.jsonl").read_text().splitlines()]

    assert manifest.source.sequence_mode == "legacy_prefix_and_sequenced_suffix"
    assert [row["source_event_type"] for row in rows] == ["run_started", "run_finished"]
    assert [row["sequence"] for row in rows] == [0, 1]
    assert [row["source_sequence"] for row in rows] == [None, 3]


@pytest.mark.parametrize(
    "sequences, match",
    [
        ([1, None], "cannot return"),
        ([1, 3], "canonical and contiguous"),
        ([2, 3], "canonical and contiguous"),
        ([1, 1], "canonical and contiguous"),
    ],
)
def test_noncanonical_source_sequences_are_rejected(
    tmp_path: Path,
    sequences: list[int | None],
    match: str,
) -> None:
    generation, capability, events = _linked_inputs(tmp_path)
    _write_records(
        events,
        [
            _record("run_started", sequence=sequences[0]),
            _record("run_finished", sequence=sequences[1]),
        ],
    )
    with pytest.raises(TrajectoryExportError, match=match):
        export_trajectory(events, generation, capability, "scenario-1", tmp_path / "bundle")


def test_unknown_type_and_mixed_run_ids_fail_before_publication(tmp_path: Path) -> None:
    generation, capability, events = _linked_inputs(tmp_path)
    for records, match in (
        ([_record("not-reviewed")], "Unreviewed outer event type"),
        (
            [_record("run_started", run_id="one"), _record("run_finished", run_id="two")],
            "mixed run IDs",
        ),
    ):
        _write_records(events, records)
        output = tmp_path / f"bundle-{len(match)}"
        with pytest.raises(TrajectoryExportError, match=match):
            export_trajectory(events, generation, capability, "scenario-1", output)
        assert not output.exists()


def test_linked_manifests_and_scenario_are_fail_closed(tmp_path: Path) -> None:
    generation, capability, events = _linked_inputs(tmp_path)
    _write_records(events, [_record("run_started")])

    raw_generation = json.loads(generation.read_text(encoding="utf-8"))
    raw_generation["capability_manifest_digest"] = "0" * 64
    mismatched = tmp_path / "generation-mismatched.json"
    mismatched.write_text(json.dumps(raw_generation), encoding="utf-8")
    with pytest.raises(TrajectoryExportError, match="capability digest"):
        export_trajectory(events, mismatched, capability, "scenario-1", tmp_path / "digest")
    with pytest.raises(TrajectoryExportError, match="scenario_id"):
        export_trajectory(events, generation, capability, "scenario-other", tmp_path / "scenario")

    raw_generation = json.loads(generation.read_text(encoding="utf-8"))
    raw_generation["subject"] = "another-subject"
    wrong_subject = tmp_path / "generation-wrong-subject.json"
    wrong_subject.write_text(json.dumps(raw_generation), encoding="utf-8")
    with pytest.raises(TrajectoryExportError, match="subject"):
        export_trajectory(events, wrong_subject, capability, "scenario-1", tmp_path / "subject")

    with pytest.raises(TrajectoryExportError, match="non-empty"):
        export_trajectory(events, generation, capability, "   ", tmp_path / "blank-scenario")


def test_output_is_new_atomic_and_symlink_safe(tmp_path: Path) -> None:
    generation, capability, events = _linked_inputs(tmp_path)
    _write_records(events, [_record("run_started")])
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(TrajectoryExportError, match="already exists"):
        export_trajectory(events, generation, capability, "scenario-1", existing)

    symlink = tmp_path / "linked-output"
    symlink.symlink_to(existing, target_is_directory=True)
    with pytest.raises(TrajectoryExportError, match="symlink"):
        export_trajectory(events, generation, capability, "scenario-1", symlink)


def test_public_dev_command_exports_the_same_bundle(tmp_path: Path) -> None:
    generation, capability, events = _linked_inputs(tmp_path)
    _write_records(events, [_record("run_started")])
    output = tmp_path / "bundle"
    result = subprocess.run(
        (
            str(ROOT / "dev"),
            "trajectory-export",
            "--events",
            str(events),
            "--generation-manifest",
            str(generation),
            "--capability-manifest",
            str(capability),
            "--scenario-id",
            "scenario-1",
            "--output",
            str(output),
        ),
        cwd=ROOT,
        env={**os.environ, "UV_CACHE_DIR": "/tmp/kae-g14-cache"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert ExportManifest.model_validate_json((output / "manifest.json").read_bytes())


def test_malformed_duplicate_and_truncated_source_records_are_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_bytes(
        b'{"event_type":"run_started","event_type":"run_finished","run_id":"r",'
        b'"step_index":null,"timestamp":"2026-08-15T00:00:00+00:00","payload":{}}\n'
    )
    with pytest.raises(TrajectoryExportError, match="Duplicate JSON key"):
        _validate_source(duplicate)

    truncated = tmp_path / "truncated.jsonl"
    truncated.write_bytes(
        b'{"event_type":"run_started","run_id":"r","step_index":null,'
        b'"timestamp":"2026-08-15T00:00:00+00:00","payload":{}}'
    )
    with pytest.raises(TrajectoryExportError, match="truncated"):
        _validate_source(truncated)

    nonfinite = tmp_path / "nonfinite.jsonl"
    nonfinite.write_bytes(
        b'{"event_type":"run_started","run_id":"r","step_index":null,'
        b'"timestamp":"2026-08-15T00:00:00+00:00","payload":{"value":NaN}}\n'
    )
    with pytest.raises(TrajectoryExportError, match="Non-finite"):
        _validate_source(nonfinite)

    nonobject = tmp_path / "nonobject.jsonl"
    nonobject.write_bytes(b"[]\n")
    with pytest.raises(TrajectoryExportError, match="not an object"):
        _validate_source(nonobject)

    for field, value in (("event_sequence", True), ("step_index", 1.0)):
        malformed_type = tmp_path / f"malformed-{field}.jsonl"
        record = _record("run_started")
        record[field] = value
        _write_records(malformed_type, [record])
        with pytest.raises(TrajectoryExportError, match=field):
            _validate_source(malformed_type)
