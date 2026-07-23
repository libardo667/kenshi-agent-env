from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from statistics import fmean, median
from typing import TypedDict


class _MetricValues(TypedDict):
    control_mode: str | None
    decisions: int
    reflex_decisions: int
    planner_errors: int
    action_receipts: int
    rejected_actions: int
    dry_run_actions: int
    executed_actions: int
    primitive_actions: int
    observations: int
    stale_observations: int
    memory_writes: int
    success: bool | None
    steps_completed: int | None
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class LogMetrics:
    control_mode: str | None = None
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
    mean_planner_latency_seconds: float | None = None
    p50_planner_latency_seconds: float | None = None
    p95_planner_latency_seconds: float | None = None


def evaluate_log(path: Path) -> LogMetrics:
    planner_latencies: list[float] = []
    values: _MetricValues = {
        "control_mode": None,
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
            if event_type == "run_started":
                control_mode = payload.get("control_mode")
                values["control_mode"] = (
                    str(control_mode) if control_mode is not None else None
                )
            elif event_type == "decision":
                values["decisions"] += 1
                source = payload.get("source")
                if source == "reflex":
                    values["reflex_decisions"] += 1
                if source == "planner_error":
                    values["planner_errors"] += 1
                latency = payload.get("planner_latency_seconds")
                if latency is not None and source != "reflex":
                    planner_latencies.append(float(latency))
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
                control_mode = payload.get("control_mode")
                if control_mode is not None:
                    values["control_mode"] = str(control_mode)
                values["success"] = payload.get("success")
                values["steps_completed"] = payload.get("steps_completed")
                values["stop_reason"] = payload.get("stop_reason")
    if not planner_latencies:
        return LogMetrics(**values)
    ordered = sorted(planner_latencies)
    p95_index = max(0, ceil(len(ordered) * 0.95) - 1)
    return LogMetrics(
        **values,
        mean_planner_latency_seconds=fmean(ordered),
        p50_planner_latency_seconds=median(ordered),
        p95_planner_latency_seconds=ordered[p95_index],
    )
