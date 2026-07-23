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
            "payload": {"primitive_actions": 2, "dry_run": False, "executed": True},
        },
        {
            "event_type": "observation",
            "payload": {"telemetry_stale": True},
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
