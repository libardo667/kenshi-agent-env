from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LogMetrics:
    decisions: int = 0
    reflex_decisions: int = 0
    planner_errors: int = 0
    action_receipts: int = 0
    rejected_actions: int = 0
    dry_run_actions: int = 0
    executed_actions: int = 0
    primitive_actions: int = 0
    observations: int = 0
    stale_observations: int = 0
    memory_writes: int = 0
    success: bool | None = None
    steps_completed: int | None = None
    stop_reason: str | None = None


def evaluate_log(path: Path) -> LogMetrics:
    values = {
        "decisions": 0,
        "reflex_decisions": 0,
        "planner_errors": 0,
        "action_receipts": 0,
        "rejected_actions": 0,
        "dry_run_actions": 0,
        "executed_actions": 0,
        "primitive_actions": 0,
        "observations": 0,
        "stale_observations": 0,
        "memory_writes": 0,
        "success": None,
        "steps_completed": None,
        "stop_reason": None,
    }
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            event_type = record.get("event_type")
            payload = record.get("payload") or {}
            if event_type == "decision":
                values["decisions"] += 1
                source = payload.get("source")
                if source == "reflex":
                    values["reflex_decisions"] += 1
                if source == "planner_error":
                    values["planner_errors"] += 1
            elif event_type == "action_receipt":
                values["action_receipts"] += 1
                values["primitive_actions"] += int(payload.get("primitive_actions", 0))
                if payload.get("dry_run"):
                    values["dry_run_actions"] += 1
                if payload.get("executed"):
                    values["executed_actions"] += 1
            elif event_type == "action_rejected":
                values["rejected_actions"] += 1
            elif event_type == "observation":
                values["observations"] += 1
                if payload.get("telemetry_stale"):
                    values["stale_observations"] += 1
            elif event_type == "memory_written":
                values["memory_writes"] += 1
            elif event_type == "run_finished":
                values["success"] = payload.get("success")
                values["steps_completed"] = payload.get("steps_completed")
                values["stop_reason"] = payload.get("stop_reason")
    return LogMetrics(**values)
