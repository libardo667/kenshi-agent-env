import json
from dataclasses import asdict
from pathlib import Path

import pytest

from kenshi_agent.tooling.evals import (
    LogMetrics,
    PlanLifecycle,
    evaluate_log,
    replay_plan_lifecycle,
)


def test_evaluate_log_counts_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        {
            "event_type": "run_started",
            "payload": {
                "control_mode": "native_assisted",
                "memory_retrieval_policy": "deterministic",
            },
        },
        {
            "event_type": "decision",
            "payload": {"source": "reflex", "planner_latency_seconds": 0.001},
        },
        {
            "event_type": "decision",
            "payload": {"source": "planner", "planner_latency_seconds": 1.0},
        },
        {
            "event_type": "decision",
            "payload": {"source": "planner", "planner_latency_seconds": 3.0},
        },
        {
            "event_type": "action_receipt",
            "payload": {
                "primitive_actions": 2,
                "dry_run": False,
                "executed": True,
                "native_acknowledgement": {
                    "command_id": "cmd-0123456789abcdef0123456789abcdef",
                    "status": "accepted",
                    "based_on_telemetry_sequence": 10,
                    "acknowledged_at_telemetry_sequence": 11,
                },
            },
        },
        {
            "event_type": "observation",
            "payload": {
                "telemetry_stale": True,
                "telemetry": {
                    "native_control": {
                        "acknowledgements": [
                            {
                                "command_id": ("cmd-0123456789abcdef0123456789abcdef"),
                                "status": "completed",
                                "based_on_telemetry_sequence": 10,
                                "acknowledged_at_telemetry_sequence": 11,
                            },
                            {
                                "command_id": ("cmd-ffffffffffffffffffffffffffffffff"),
                                "status": "rejected",
                                "based_on_telemetry_sequence": 12,
                                "acknowledged_at_telemetry_sequence": 14,
                            },
                        ]
                    }
                },
            },
        },
        {
            "event_type": "safety_supervisor_preempted",
            "payload": {"cause": "reflex"},
        },
        {
            "event_type": "strategic_planner_cancelled",
            "payload": {"cause": "reflex"},
        },
        {
            "event_type": "safety_cleanup_started",
            "payload": {"cause": "reflex"},
        },
        {
            "event_type": "safety_cleanup_completed",
            "payload": {"cause": "reflex"},
        },
        {
            "event_type": "safety_supervisor_terminal",
            "payload": {"cause": "reflex", "status": "safe_paused"},
        },
        {
            "event_type": "plan_patch_staged",
            "payload": {"plan_id": "plan"},
        },
        {
            "event_type": "plan_patched",
            "payload": {"plan_id": "plan"},
        },
        {
            "event_type": "option_prepared",
            "payload": {"option_id": "option-1"},
        },
        {
            "event_type": "option_started",
            "payload": {"option_id": "option-1"},
        },
        {
            "event_type": "option_progress",
            "payload": {"option_id": "option-1"},
        },
        {
            "event_type": "option_succeeded",
            "payload": {"option_id": "option-1"},
        },
        {
            "event_type": "run_finished",
            "payload": {"success": True, "steps_completed": 1, "stop_reason": "done"},
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    metrics = evaluate_log(path)
    assert metrics.decisions == 3
    assert metrics.reflex_decisions == 1
    assert metrics.primitive_actions == 2
    assert metrics.stale_observations == 1
    assert metrics.success is True
    assert metrics.control_mode == "native_assisted"
    assert metrics.memory_retrieval_policies == {"deterministic": 1}
    assert metrics.mean_planner_latency_seconds == 2.0
    assert metrics.p50_planner_latency_seconds == 2.0
    assert metrics.p95_planner_latency_seconds == 3.0
    assert metrics.safety_supervisor_preemptions == 1
    assert metrics.strategic_planner_cancellations == 1
    assert metrics.safety_cleanups_started == 1
    assert metrics.safety_cleanups_completed == 1
    assert metrics.safety_cleanups_failed == 0
    assert metrics.safety_supervisor_terminals == 1
    assert metrics.safety_supervisor_safe_paused == 1
    assert metrics.safety_cleanup_success_percentage == 100.0
    assert metrics.plan_patches_staged == 1
    assert metrics.plan_patches_applied == 1
    assert metrics.options_prepared == 1
    assert metrics.options_started == 1
    assert metrics.option_progress_updates == 1
    assert metrics.options_succeeded == 1
    assert metrics.options_failed == 0
    assert metrics.options_cancelled == 0
    assert metrics.option_success_percentage == 100.0
    assert metrics.native_command_acknowledgements == 2
    assert metrics.native_commands_accepted == 1
    assert metrics.native_commands_completed == 1
    assert metrics.native_commands_rejected == 1
    assert metrics.native_commands_cancelled == 0
    assert metrics.mean_native_ack_sequence_lag == 1.5
    assert metrics.native_command_completion_percentage == 100.0


def test_continuity_metrics_count_every_typed_status_and_read_legacy_logs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "continuity.jsonl"
    records = [
        {
            "event_type": "continuity_receipt",
            "payload": {
                "status": status,
                "operation": {"operation": operation},
            },
        }
        for status, operation in (
            ("accepted", "keep"),
            ("accepted", "reinforce"),
            ("rejected", "supersede"),
            ("no_op", "keep"),
            ("failed", "resolve"),
        )
    ]
    records.append(
        {
            # Compatibility input only; current metric output deliberately
            # does not resurrect the retired memory_writes terminology.
            "event_type": "memory_written",
            "payload": {},
        }
    )
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    metrics = evaluate_log(path)

    assert metrics.continuity_operations_accepted == 2
    assert metrics.continuity_operations_rejected == 1
    assert metrics.continuity_operations_no_op == 1
    assert metrics.continuity_operations_failed == 1
    assert metrics.continuity_memories_kept == 2
    assert "memory_writes" not in asdict(metrics)


def test_evaluate_log_measures_repeated_dialogue_approaches_by_target(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        {
            "event_type": "action_receipt",
            "payload": {
                "action": {
                    "kind": "approach_dialogue_target",
                    "target_id": target_id,
                },
                "executed": executed,
            },
        }
        for target_id, executed in (
            ("entity-barman", True),
            ("entity-guard", True),
            ("entity-barman", True),
            ("entity-barman", False),
        )
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    metrics = evaluate_log(path)

    assert metrics.dialogue_approach_attempts_by_target == {
        "entity-barman": 3,
        "entity-guard": 1,
    }
    assert metrics.dialogue_approach_attempts == 4
    assert metrics.repeated_dialogue_approach_attempts == 2
    assert metrics.max_dialogue_approach_attempts_per_target == 3


def test_the_observation_digest_keeps_what_the_evaluator_reads() -> None:
    """A digest must not silently break replay metrics.

    Logging every full observation produced a 112 MB file in ten minutes, but
    the evaluator reads only a few fields from them and a human reading the log
    wants orientation, not two hundred control bounds.
    """

    from kenshi_agent.core.observation import Observation
    from kenshi_agent.core.telemetry import (
        CharacterState,
        GameState,
        NativeCommandAcknowledgement,
        NativeCommandStatus,
        NativeControlState,
        TelemetrySnapshot,
        UIState,
    )
    from kenshi_agent.core.world import WorldStateRevision

    command_id = "cmd-" + "a" * 32
    observation = Observation(
        run_id="digest-test",
        step_index=4,
        mode="live",
        world_revision=WorldStateRevision(telemetry_sequence=12, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=12,
            identity_session_id="session-digest",
            capabilities=["game.pause", "identity.stable_handles"],
            game=GameState(loaded=True, paused=True, money=1000),
            ui=UIState(
                active_screen="trade",
                context_inventory_target_id="entity-copper",
                visible_controls_complete=True,
                selected_character_id="entity-hep",
                selected_character_ids=["entity-hep"],
            ),
            squad=[
                CharacterState(
                    id="entity-hep",
                    name="Hep",
                    selected=True,
                    inventory_complete=True,
                )
            ],
            native_control=NativeControlState(
                available=True,
                acknowledgements=[
                    NativeCommandAcknowledgement(
                        command_id=command_id,
                        command="approach_confirmed_vendor",
                        status=NativeCommandStatus.ACCEPTED,
                        reason="issued",
                        target_id="entity-barman",
                        selected_character_ids=["entity-hep"],
                        based_on_telemetry_sequence=10,
                        acknowledged_at_telemetry_sequence=11,
                        accepted_at_telemetry_sequence=11,
                    )
                ],
            ),
        ),
        telemetry_stale=True,
    )

    digest = observation.log_digest()

    # Exactly the fields metrics.py reads off an observation.
    assert digest["telemetry_stale"] is True
    acknowledgements = digest["telemetry"]["native_control"]["acknowledgements"]
    assert [item["command_id"] for item in acknowledgements] == [command_id]

    # Enough to orient a human reading the log.
    assert digest["telemetry"]["ui"]["active_screen"] == "trade"
    assert digest["telemetry"]["ui"]["context_inventory_target_id"] == "entity-copper"
    assert digest["telemetry"]["ui"]["visible_controls_complete"] is True
    assert digest["telemetry"]["selected"]["inventory_complete"] is True
    assert digest["telemetry"]["game"]["money"] == 1000
    assert digest["world_revision"]["telemetry_sequence"] == 12

    # And it is marked, so replay can refuse it instead of failing obscurely.
    assert digest["digest"] is True

    import json

    full_size = len(json.dumps(observation.model_dump(mode="json"), default=str))
    assert len(json.dumps(digest, default=str)) < full_size


def test_evaluate_log_conserves_every_recognized_event_into_exact_metrics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "all-events.jsonl"
    command_1 = "cmd-" + "1" * 32
    command_2 = "cmd-" + "2" * 32
    command_3 = "cmd-" + "3" * 32
    records: list[dict[str, object]] = [
        {"event_type": "run_started", "payload": {"control_mode": "start-mode"}},
        {
            "event_type": "decision",
            "payload": {"source": "reflex", "planner_latency_seconds": 100},
        },
        {
            "event_type": "decision",
            "payload": {"source": "planner_error", "planner_latency_seconds": 4},
        },
        {
            "event_type": "decision",
            "payload": {"source": "planner", "planner_latency_seconds": 2},
        },
        *[
            {
                "event_type": "strategic_planner_call",
                "payload": {"planner_latency_seconds": latency},
            }
            for latency in range(1, 21)
        ],
        {
            "event_type": "action_receipt",
            "payload": {
                "primitive_actions": 2,
                "action": {
                    "kind": "approach_dialogue_target",
                    "target_id": "entity-z",
                },
                "dry_run": True,
                "executed": False,
                "command_id": command_1,
                "causal_revision_advanced": True,
                "native_acknowledgement": {
                    "command_id": command_1,
                    "status": "accepted",
                    "based_on_telemetry_sequence": 10,
                    "acknowledged_at_telemetry_sequence": 11,
                },
            },
        },
        {
            "event_type": "action_receipt",
            "payload": {
                "primitive_actions": 3,
                "action": {
                    "kind": "approach_dialogue_target",
                    "target_id": "entity-z",
                },
                "dry_run": False,
                "executed": True,
                "command_id": command_2,
                "causal_revision_advanced": False,
                "native_acknowledgement": {
                    "command_id": command_2,
                    "status": "cancelled",
                    "based_on_telemetry_sequence": 5,
                    "acknowledged_at_telemetry_sequence": 6,
                },
            },
        },
        {
            "event_type": "action_receipt",
            "payload": {
                "primitive_actions": 5,
                "action": {
                    "kind": "approach_dialogue_target",
                    "target_id": "entity-a",
                },
                "executed": True,
            },
        },
        {"event_type": "action_rejected", "payload": {}},
        {
            "event_type": "observation",
            "payload": {
                "telemetry_stale": True,
                "telemetry": {
                    "native_control": {
                        "acknowledgements": [
                            {
                                "command_id": command_1,
                                "status": "completed",
                                "based_on_telemetry_sequence": 10,
                                "acknowledged_at_telemetry_sequence": 12,
                            },
                            {
                                "command_id": command_3,
                                "status": "rejected",
                                "based_on_telemetry_sequence": 4,
                                "acknowledged_at_telemetry_sequence": 7,
                            },
                            {"command_id": 4, "status": "completed"},
                            "invalid",
                        ]
                    }
                },
            },
        },
        {
            "event_type": "continuity_receipt",
            "payload": {
                "status": "accepted",
                "operation": {"operation": "keep"},
            },
        },
        {
            "event_type": "continuity_receipt",
            "payload": {
                "status": "accepted",
                "operation": {"operation": "reinforce"},
            },
        },
        {
            "event_type": "continuity_receipt",
            "payload": {"status": "accepted"},
        },
        {"event_type": "continuity_receipt", "payload": {"status": "rejected"}},
        {"event_type": "continuity_receipt", "payload": {"status": "no_op"}},
        {"event_type": "continuity_receipt", "payload": {"status": "failed"}},
        {"event_type": "memory_written", "payload": {}},
        {"event_type": "plan_outcome", "payload": {}},
        {
            "event_type": "memory_read",
            "payload": {
                "result": {
                    "status": "completed",
                    "records": [{"memory_id": "mem-a"}, {"memory_id": "mem-b"}],
                    "truncated": True,
                }
            },
        },
        {"event_type": "memory_read", "payload": {"result": []}},
        {
            "event_type": "fieldbook_receipt",
            "payload": {
                "status": "accepted",
                "operation": {"operation": "create_project"},
            },
        },
        {
            "event_type": "fieldbook_receipt",
            "payload": {
                "status": "accepted",
                "operation": {"operation": "append_entry"},
            },
        },
        {
            "event_type": "fieldbook_receipt",
            "payload": {"status": "rejected", "operation": {}},
        },
        {
            "event_type": "fieldbook_receipt",
            "payload": {"status": "no_op", "operation": {}},
        },
        {
            "event_type": "fieldbook_receipt",
            "payload": {"status": "failed", "operation": {}},
        },
        {
            "event_type": "fieldbook_read",
            "payload": {
                "result": {
                    "status": "completed",
                    "entries": [{"entry_id": "fbe-a"}, {"entry_id": "fbe-b"}],
                    "truncated": True,
                }
            },
        },
        {
            "event_type": "fieldbook_read",
            "payload": {"result": {"status": "unavailable", "entries": []}},
        },
        {
            "event_type": "fieldbook_read",
            "payload": {"result": {"status": "failed", "entries": []}},
        },
        {"event_type": "plan_proposed", "payload": {}},
        {"event_type": "plan_accepted", "payload": {}},
        {"event_type": "plan_rejected", "payload": {}},
        {"event_type": "plan_completed", "payload": {}},
        {"event_type": "plan_aborted", "payload": {}},
        {"event_type": "plan_step_started", "payload": {}},
        {"event_type": "plan_step_succeeded", "payload": {}},
        {"event_type": "plan_step_failed", "payload": {}},
        {"event_type": "plan_step_cancelled", "payload": {}},
        {"event_type": "plan_step_interrupted", "payload": {}},
        {"event_type": "plan_budget_reserved", "payload": {}},
        {"event_type": "plan_budget_committed", "payload": {}},
        {"event_type": "plan_budget_released", "payload": {}},
        {"event_type": "input_boundary_revalidated", "payload": {}},
        {"event_type": "input_boundary_rejected", "payload": {}},
        {"event_type": "safety_supervisor_preempted", "payload": {}},
        {"event_type": "strategic_planner_cancelled", "payload": {}},
        {
            "event_type": "advisor_result",
            "payload": {"evidence": {"status": "answered"}},
        },
        {
            "event_type": "advisor_result",
            "payload": {"evidence": {"status": "failed"}},
        },
        {
            "event_type": "advisor_result",
            "payload": {"evidence": {"status": "cooldown"}},
        },
        {"event_type": "plan_execution_cancelled", "payload": {}},
        {"event_type": "safety_cleanup_started", "payload": {}},
        {"event_type": "safety_cleanup_started", "payload": {}},
        {"event_type": "safety_cleanup_completed", "payload": {}},
        {"event_type": "safety_cleanup_failed", "payload": {}},
        {
            "event_type": "safety_supervisor_terminal",
            "payload": {"status": "safe_paused"},
        },
        {
            "event_type": "safety_supervisor_terminal",
            "payload": {"status": "failed"},
        },
        {"event_type": "plan_patch_staged", "payload": {}},
        {"event_type": "plan_interrupt_staged", "payload": {}},
        {"event_type": "plan_patched", "payload": {}},
        {"event_type": "plan_patch_rejected", "payload": {}},
        {"event_type": "concurrent_planner_discarded", "payload": {}},
        {"event_type": "option_prepared", "payload": {}},
        {"event_type": "option_started", "payload": {}},
        {"event_type": "option_progress", "payload": {}},
        {"event_type": "option_succeeded", "payload": {}},
        {"event_type": "option_failed", "payload": {}},
        {"event_type": "option_cancelled", "payload": {}},
        {"event_type": "option_interrupted", "payload": {}},
        {
            "event_type": "world_state_update",
            "payload": {
                "sequence_status": "duplicate",
                "transient_events_lost": 2,
                "subscriber_update_drops": 3,
                "observation_pump_errors": 4,
            },
        },
        {
            "event_type": "world_state_update",
            "payload": {
                "sequence_status": "current",
                "transient_events_lost": 5,
                "subscriber_update_drops": 1,
                "observation_pump_errors": 6,
            },
        },
        {
            "event_type": "world_state_event",
            "payload": {"event_type": "observation_event"},
        },
        {
            "event_type": "world_state_event",
            "payload": {"event_type": "observation_event"},
        },
        {
            "event_type": "world_state_event",
            "payload": {"event_type": "other"},
        },
        {
            "event_type": "world_state_finished",
            "payload": {
                "sequence_stall_incidents": 4,
                "transient_events_retained": 3,
                "transient_events_lost": 6,
                "revision_regressions": 7,
                "revision_conflicts": 8,
                "entity_lifetimes_started": 9,
                "entity_lifetimes_ended": 10,
                "command_mismatches": 11,
                "subscriber_drops": 12,
                "pump_errors": 13,
            },
        },
        {
            "event_type": "run_finished",
            "payload": {
                "control_mode": "finish-mode",
                "success": False,
                "steps_completed": 42,
                "stop_reason": "finished",
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    metrics = evaluate_log(path)

    assert metrics == LogMetrics(
        control_mode="finish-mode",
        decisions=3,
        strategic_planner_calls=20,
        reflex_decisions=1,
        planner_errors=1,
        action_receipts=3,
        rejected_actions=1,
        dry_run_actions=1,
        executed_actions=2,
        primitive_actions=10,
        dialogue_approach_attempts=3,
        repeated_dialogue_approach_attempts=1,
        max_dialogue_approach_attempts_per_target=2,
        dialogue_approach_attempts_by_target={"entity-a": 1, "entity-z": 2},
        observations=1,
        stale_observations=1,
        continuity_memories_kept=2,
        continuity_operations_accepted=3,
        continuity_operations_rejected=1,
        continuity_operations_no_op=1,
        continuity_operations_failed=1,
        memory_lifecycle_transitions={"keep": 1, "reinforce": 1, "unknown": 1},
        plan_outcomes=1,
        memory_reads=2,
        memory_reads_completed=1,
        memory_reads_unavailable=0,
        memory_reads_failed=0,
        memory_read_records=2,
        memory_read_truncations=1,
        fieldbook_operations_accepted=2,
        fieldbook_operations_rejected=1,
        fieldbook_operations_no_op=1,
        fieldbook_operations_failed=1,
        fieldbook_projects_created=1,
        fieldbook_entries_appended=1,
        fieldbook_lifecycle_transitions={
            "append_entry": 1,
            "create_project": 1,
        },
        fieldbook_reads=3,
        fieldbook_reads_completed=1,
        fieldbook_reads_unavailable=1,
        fieldbook_reads_failed=1,
        fieldbook_read_entries=2,
        fieldbook_read_truncations=1,
        plans_proposed=1,
        plans_accepted=1,
        plans_rejected=1,
        plans_completed=1,
        plans_aborted=1,
        plan_steps_started=1,
        plan_steps_succeeded=1,
        plan_steps_failed=1,
        plan_steps_cancelled=2,
        budget_reservations=1,
        budget_commits=1,
        budget_releases=1,
        input_boundary_revalidations=1,
        input_boundary_rejections=1,
        sequence_stall_incidents=4,
        transient_events_retained=3,
        transient_events_lost=6,
        subscriber_update_drops=12,
        observation_pump_errors=13,
        revision_regressions=7,
        revision_conflicts=8,
        entity_lifetimes_started=9,
        entity_lifetimes_ended=10,
        command_mismatches=11,
        command_receipts=2,
        command_receipts_with_post_revision=1,
        native_command_acknowledgements=3,
        native_commands_accepted=2,
        native_commands_completed=1,
        native_commands_rejected=1,
        native_commands_cancelled=1,
        safety_supervisor_preemptions=1,
        strategic_planner_cancellations=1,
        advisor_requests=3,
        advisor_hosted_calls=2,
        advisor_answers=1,
        advisor_suppressions=1,
        advisor_failures=1,
        plan_execution_cancellations=1,
        safety_cleanups_started=2,
        safety_cleanups_completed=1,
        safety_cleanups_failed=1,
        safety_supervisor_terminals=2,
        safety_supervisor_safe_paused=1,
        plan_patches_staged=2,
        plan_patches_applied=1,
        plan_patches_rejected=1,
        concurrent_planner_discards=1,
        options_prepared=1,
        options_started=1,
        option_progress_updates=1,
        options_succeeded=1,
        options_failed=1,
        options_cancelled=2,
        success=False,
        steps_completed=42,
        stop_reason="finished",
        mean_planner_latency_seconds=10.5,
        p50_planner_latency_seconds=10.5,
        p95_planner_latency_seconds=19.0,
        actions_per_strategic_planner_call=0.15,
        receipts_with_post_command_revision_percentage=50.0,
        mean_native_ack_sequence_lag=2.0,
        native_command_completion_percentage=50.0,
        safety_cleanup_success_percentage=50.0,
        option_success_percentage=25.0,
    )


def test_empty_and_legacy_logs_have_exact_closed_defaults(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert evaluate_log(empty) == LogMetrics()

    fallback = tmp_path / "fallback.jsonl"
    fallback.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {"event_type": "decision", "payload": {"source": "reflex"}},
                {"event_type": "decision", "payload": {"source": "planner"}},
                {"event_type": "decision", "payload": {"source": "planner_error"}},
                {"event_type": "action_receipt", "payload": {}},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    metrics = evaluate_log(fallback)
    assert metrics.strategic_planner_calls == 2
    assert metrics.actions_per_strategic_planner_call == 0.5
    assert metrics.mean_planner_latency_seconds is None


@pytest.mark.parametrize(
    ("field_name", "event_type", "payload", "increment"),
    [
        ("reflex_decisions", "decision", {"source": "reflex"}, 1),
        ("planner_errors", "decision", {"source": "planner_error"}, 1),
        ("dry_run_actions", "action_receipt", {"dry_run": True}, 1),
        ("rejected_actions", "action_rejected", {}, 1),
        ("observations", "observation", {}, 1),
        ("stale_observations", "observation", {"telemetry_stale": True}, 1),
        (
            "continuity_memories_kept",
            "continuity_receipt",
            {"status": "accepted", "operation": {"operation": "keep"}},
            1,
        ),
        (
            "continuity_operations_rejected",
            "continuity_receipt",
            {"status": "rejected"},
            1,
        ),
        (
            "continuity_operations_no_op",
            "continuity_receipt",
            {"status": "no_op"},
            1,
        ),
        (
            "continuity_operations_failed",
            "continuity_receipt",
            {"status": "failed"},
            1,
        ),
        ("plan_outcomes", "plan_outcome", {}, 1),
        (
            "memory_reads_completed",
            "memory_read",
            {"result": {"status": "completed"}},
            1,
        ),
        (
            "memory_reads_unavailable",
            "memory_read",
            {"result": {"status": "unavailable"}},
            1,
        ),
        (
            "memory_reads_failed",
            "memory_read",
            {"result": {"status": "failed"}},
            1,
        ),
        (
            "memory_read_records",
            "memory_read",
            {"result": {"records": [{}, {}]}},
            2,
        ),
        (
            "memory_read_truncations",
            "memory_read",
            {"result": {"truncated": True}},
            1,
        ),
        ("plans_proposed", "plan_proposed", {}, 1),
        ("plans_accepted", "plan_accepted", {}, 1),
        ("plans_rejected", "plan_rejected", {}, 1),
        ("plans_completed", "plan_completed", {}, 1),
        ("plans_aborted", "plan_aborted", {}, 1),
        ("plan_steps_started", "plan_step_started", {}, 1),
        ("plan_steps_failed", "plan_step_failed", {}, 1),
        ("budget_reservations", "plan_budget_reserved", {}, 1),
        ("budget_commits", "plan_budget_committed", {}, 1),
        ("budget_releases", "plan_budget_released", {}, 1),
        ("input_boundary_revalidations", "input_boundary_revalidated", {}, 1),
        ("input_boundary_rejections", "input_boundary_rejected", {}, 1),
        ("safety_supervisor_preemptions", "safety_supervisor_preempted", {}, 1),
        (
            "strategic_planner_cancellations",
            "strategic_planner_cancelled",
            {},
            1,
        ),
        (
            "advisor_answers",
            "advisor_result",
            {"evidence": {"status": "answered"}},
            1,
        ),
        (
            "advisor_failures",
            "advisor_result",
            {"evidence": {"status": "failed"}},
            1,
        ),
        (
            "advisor_suppressions",
            "advisor_result",
            {"evidence": {"status": "cooldown"}},
            1,
        ),
        ("plan_execution_cancellations", "plan_execution_cancelled", {}, 1),
        ("safety_cleanups_completed", "safety_cleanup_completed", {}, 1),
        ("safety_cleanups_failed", "safety_cleanup_failed", {}, 1),
        (
            "safety_supervisor_safe_paused",
            "safety_supervisor_terminal",
            {"status": "safe_paused"},
            1,
        ),
        ("plan_patches_applied", "plan_patched", {}, 1),
        ("plan_patches_rejected", "plan_patch_rejected", {}, 1),
        ("concurrent_planner_discards", "concurrent_planner_discarded", {}, 1),
        ("options_prepared", "option_prepared", {}, 1),
        ("options_started", "option_started", {}, 1),
        ("option_progress_updates", "option_progress", {}, 1),
        ("options_succeeded", "option_succeeded", {}, 1),
        ("options_failed", "option_failed", {}, 1),
    ],
)
def test_repeated_events_are_accumulated_instead_of_saturating_at_one(
    tmp_path: Path,
    field_name: str,
    event_type: str,
    payload: dict[str, object],
    increment: int,
) -> None:
    path = tmp_path / f"{field_name}.jsonl"
    record = {"event_type": event_type, "payload": payload}
    path.write_text(
        f"{json.dumps(record)}\n{json.dumps(record)}\n",
        encoding="utf-8",
    )

    metrics = evaluate_log(path)

    assert getattr(metrics, field_name) == 2 * increment


def test_world_state_sources_are_independently_counted_and_missing_totals_are_zero(
    tmp_path: Path,
) -> None:
    empty_update_path = tmp_path / "empty-update.jsonl"
    empty_update_path.write_text(
        f"{json.dumps({'event_type': 'world_state_update', 'payload': {}})}\n",
        encoding="utf-8",
    )
    assert evaluate_log(empty_update_path) == LogMetrics()

    update_path = tmp_path / "updates.jsonl"
    update_records = [
        {
            "event_type": "world_state_update",
            "payload": {
                "sequence_status": "duplicate",
                "transient_events_lost": 5,
                "subscriber_update_drops": 3,
                "observation_pump_errors": 4,
            },
        },
        {
            "event_type": "world_state_update",
            "payload": {"sequence_status": "duplicate"},
        },
    ]
    update_path.write_text(
        "\n".join(json.dumps(record) for record in update_records) + "\n",
        encoding="utf-8",
    )
    assert evaluate_log(update_path) == LogMetrics(
        sequence_stall_incidents=2,
        transient_events_lost=5,
        subscriber_update_drops=3,
        observation_pump_errors=4,
    )

    event_path = tmp_path / "events.jsonl"
    event_records = [
        {"event_type": "world_state_event", "payload": {"event_type": event_type}}
        for event_type in ("observation_event", "observation_event", "other")
    ]
    event_path.write_text(
        "\n".join(json.dumps(record) for record in event_records) + "\n",
        encoding="utf-8",
    )
    assert evaluate_log(event_path) == LogMetrics(transient_events_retained=2)

    finished_path = tmp_path / "finished.jsonl"
    finished_path.write_text(
        f"{json.dumps({'event_type': 'world_state_finished', 'payload': {}})}\n",
        encoding="utf-8",
    )
    assert evaluate_log(finished_path) == LogMetrics()


def test_missing_primitive_count_is_zero_and_native_terminal_statuses_accumulate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native-statuses.jsonl"
    records: list[dict[str, object]] = [
        {"event_type": "action_receipt", "payload": {}},
    ]
    for status, marker in (
        ("completed", "c"),
        ("completed", "d"),
        ("rejected", "r"),
        ("rejected", "s"),
        ("cancelled", "x"),
        ("cancelled", "y"),
    ):
        records.append(
            {
                "event_type": "action_receipt",
                "payload": {
                    "native_acknowledgement": {
                        "command_id": "cmd-" + marker * 32,
                        "status": status,
                    }
                },
            }
        )
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    metrics = evaluate_log(path)

    assert metrics.primitive_actions == 0
    assert metrics.native_command_acknowledgements == 6
    assert metrics.native_commands_accepted == 4
    assert metrics.native_commands_completed == 2
    assert metrics.native_commands_rejected == 2
    assert metrics.native_commands_cancelled == 2


def test_no_latency_return_preserves_every_derived_metric_and_transition(
    tmp_path: Path,
) -> None:
    path = tmp_path / "derived-without-latency.jsonl"
    command_accepted = "cmd-" + "a" * 32
    command_completed = "cmd-" + "b" * 32
    records = [
        {
            "event_type": "action_receipt",
            "payload": {
                "command_id": command_accepted,
                "causal_revision_advanced": True,
                "native_acknowledgement": {
                    "command_id": command_accepted,
                    "status": "accepted",
                    "based_on_telemetry_sequence": 10,
                    "acknowledged_at_telemetry_sequence": 11,
                },
            },
        },
        {
            "event_type": "action_receipt",
            "payload": {
                "command_id": command_completed,
                "causal_revision_advanced": False,
                "native_acknowledgement": {
                    "command_id": command_completed,
                    "status": "completed",
                    "based_on_telemetry_sequence": 20,
                    "acknowledged_at_telemetry_sequence": 23,
                },
            },
        },
        {
            "event_type": "observation",
            "payload": {
                "telemetry": {
                    "native_control": {
                        "acknowledgements": [
                            {
                                "command_id": "cmd-" + "c" * 32,
                                "status": "cancelled",
                                "based_on_telemetry_sequence": 30,
                                "acknowledged_at_telemetry_sequence": "unknown",
                            }
                        ]
                    }
                }
            },
        },
        {
            "event_type": "continuity_receipt",
            "payload": {
                "status": "accepted",
                "operation": {"operation": "reinforce"},
            },
        },
        {
            "event_type": "continuity_receipt",
            "payload": {
                "status": "accepted",
                "operation": {"operation": "reinforce"},
            },
        },
        {"event_type": "safety_cleanup_started", "payload": {}},
        {"event_type": "safety_cleanup_started", "payload": {}},
        {"event_type": "safety_cleanup_completed", "payload": {}},
        {"event_type": "option_succeeded", "payload": {}},
        {"event_type": "option_failed", "payload": {}},
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    metrics = evaluate_log(path)

    assert metrics.memory_lifecycle_transitions == {"reinforce": 2}
    assert metrics.receipts_with_post_command_revision_percentage == 50.0
    assert metrics.native_command_acknowledgements == 3
    assert metrics.native_commands_accepted == 3
    assert metrics.native_commands_completed == 1
    assert metrics.native_commands_cancelled == 1
    assert metrics.mean_native_ack_sequence_lag == 2.0
    assert metrics.native_command_completion_percentage == pytest.approx(100 / 3)
    assert metrics.safety_cleanup_success_percentage == 50.0
    assert metrics.option_success_percentage == 50.0
    assert metrics.mean_planner_latency_seconds is None


def test_lifecycle_replay_maps_every_event_and_deduplicates_step_terminals(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lifecycles.jsonl"
    status_by_event = {
        "plan_proposed": "proposed",
        "plan_accepted": "accepted",
        "plan_rejected": "rejected",
        "plan_started": "started",
        "plan_step_ready": "running",
        "plan_step_started": "running",
        "plan_step_progress": "running",
        "plan_step_succeeded": "running",
        "plan_step_failed": "running",
        "plan_step_cancelled": "running",
        "plan_step_interrupted": "running",
        "plan_patch_requested": "needs_replan",
        "plan_patched": "running",
        "plan_completed": "completed",
        "plan_aborted": "aborted",
    }
    records: list[dict[str, object]] = [
        {
            "event_type": event_type,
            "payload": {
                "plan_id": f"plan-{index}",
                "plan_version": index,
                "step_id": f"step-{index}",
            },
        }
        for index, event_type in enumerate(status_by_event, start=1)
    ]
    records.extend(
        [
            {"event_type": "unknown", "payload": {"plan_id": "ignored"}},
            {"event_type": "plan_started", "payload": {"plan_id": 5}},
            {"event_type": "plan_started", "payload": None},
            {
                "event_type": "plan_proposed",
                "payload": {"plan_id": "plan-sequence", "plan_version": 1},
            },
            *[
                {
                    "event_type": event_type,
                    "payload": {
                        "plan_id": "plan-sequence",
                        "plan_version": 2,
                        "step_id": step_id,
                    },
                }
                for event_type, step_id in (
                    ("plan_step_succeeded", "success-a"),
                    ("plan_step_succeeded", "success-a"),
                    ("plan_step_succeeded", "success-b"),
                    ("plan_step_failed", "failure-a"),
                    ("plan_step_failed", "failure-a"),
                    ("plan_step_cancelled", "cancel-a"),
                    ("plan_step_cancelled", "cancel-a"),
                    ("plan_step_interrupted", "interrupt-a"),
                    ("plan_step_interrupted", "interrupt-a"),
                    ("plan_completed", "terminal"),
                )
            ],
        ]
    )
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    replayed = replay_plan_lifecycle(path)

    expected = {
        f"plan-{index}": PlanLifecycle(
            plan_id=f"plan-{index}",
            plan_version=index,
            status=status,
            active_step_id=(
                None
                if event_type in {"plan_rejected", "plan_completed", "plan_aborted"}
                else f"step-{index}"
            ),
            succeeded_step_ids=([f"step-{index}"] if event_type == "plan_step_succeeded" else []),
            failed_step_ids=([f"step-{index}"] if event_type == "plan_step_failed" else []),
            cancelled_step_ids=(
                [f"step-{index}"]
                if event_type in {"plan_step_cancelled", "plan_step_interrupted"}
                else []
            ),
        )
        for index, (event_type, status) in enumerate(
            status_by_event.items(),
            start=1,
        )
    }
    expected["plan-sequence"] = PlanLifecycle(
        plan_id="plan-sequence",
        plan_version=2,
        status="completed",
        active_step_id=None,
        succeeded_step_ids=["success-a", "success-b"],
        failed_step_ids=["failure-a"],
        cancelled_step_ids=["cancel-a", "interrupt-a"],
    )
    assert replayed == expected
