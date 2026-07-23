import json
from pathlib import Path

from kenshi_agent.evals import evaluate_log


def test_evaluate_log_counts_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        {
            "event_type": "run_started",
            "payload": {"control_mode": "native_assisted"},
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
