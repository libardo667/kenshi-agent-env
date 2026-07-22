import json
from pathlib import Path

from kenshi_agent.evals import evaluate_log


def test_evaluate_log_counts_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        {
            "event_type": "decision",
            "payload": {"source": "reflex"},
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
    assert metrics.decisions == 1
    assert metrics.reflex_decisions == 1
    assert metrics.primitive_actions == 2
    assert metrics.stale_observations == 1
    assert metrics.success is True
