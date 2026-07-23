from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from statistics import fmean, median
from typing import TypedDict


class _MetricValues(TypedDict):
    control_mode: str | None
    decisions: int
    strategic_planner_calls: int
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
    plans_proposed: int
    plans_accepted: int
    plans_rejected: int
    plans_completed: int
    plans_aborted: int
    plan_steps_started: int
    plan_steps_succeeded: int
    plan_steps_failed: int
    plan_steps_cancelled: int
    budget_reservations: int
    budget_commits: int
    budget_releases: int
    sequence_stall_incidents: int
    transient_events_retained: int
    transient_events_lost: int
    subscriber_update_drops: int
    observation_pump_errors: int
    revision_regressions: int
    revision_conflicts: int
    entity_lifetimes_started: int
    entity_lifetimes_ended: int
    command_mismatches: int
    command_receipts: int
    command_receipts_with_post_revision: int
    safety_supervisor_preemptions: int
    strategic_planner_cancellations: int
    plan_execution_cancellations: int
    safety_cleanups_started: int
    safety_cleanups_completed: int
    safety_cleanups_failed: int
    safety_supervisor_terminals: int
    safety_supervisor_safe_paused: int
    success: bool | None
    steps_completed: int | None
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class LogMetrics:
    control_mode: str | None = None
    decisions: int = 0
    strategic_planner_calls: int = 0
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
    plans_proposed: int = 0
    plans_accepted: int = 0
    plans_rejected: int = 0
    plans_completed: int = 0
    plans_aborted: int = 0
    plan_steps_started: int = 0
    plan_steps_succeeded: int = 0
    plan_steps_failed: int = 0
    plan_steps_cancelled: int = 0
    budget_reservations: int = 0
    budget_commits: int = 0
    budget_releases: int = 0
    sequence_stall_incidents: int = 0
    transient_events_retained: int = 0
    transient_events_lost: int = 0
    subscriber_update_drops: int = 0
    observation_pump_errors: int = 0
    revision_regressions: int = 0
    revision_conflicts: int = 0
    entity_lifetimes_started: int = 0
    entity_lifetimes_ended: int = 0
    command_mismatches: int = 0
    command_receipts: int = 0
    command_receipts_with_post_revision: int = 0
    safety_supervisor_preemptions: int = 0
    strategic_planner_cancellations: int = 0
    plan_execution_cancellations: int = 0
    safety_cleanups_started: int = 0
    safety_cleanups_completed: int = 0
    safety_cleanups_failed: int = 0
    safety_supervisor_terminals: int = 0
    safety_supervisor_safe_paused: int = 0
    success: bool | None = None
    steps_completed: int | None = None
    stop_reason: str | None = None
    mean_planner_latency_seconds: float | None = None
    p50_planner_latency_seconds: float | None = None
    p95_planner_latency_seconds: float | None = None
    actions_per_strategic_planner_call: float | None = None
    receipts_with_post_command_revision_percentage: float | None = None
    safety_cleanup_success_percentage: float | None = None


def evaluate_log(path: Path) -> LogMetrics:
    decision_planner_latencies: list[float] = []
    strategic_planner_latencies: list[float] = []
    values: _MetricValues = {
        "control_mode": None,
        "decisions": 0,
        "strategic_planner_calls": 0,
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
        "plans_proposed": 0,
        "plans_accepted": 0,
        "plans_rejected": 0,
        "plans_completed": 0,
        "plans_aborted": 0,
        "plan_steps_started": 0,
        "plan_steps_succeeded": 0,
        "plan_steps_failed": 0,
        "plan_steps_cancelled": 0,
        "budget_reservations": 0,
        "budget_commits": 0,
        "budget_releases": 0,
        "sequence_stall_incidents": 0,
        "transient_events_retained": 0,
        "transient_events_lost": 0,
        "subscriber_update_drops": 0,
        "observation_pump_errors": 0,
        "revision_regressions": 0,
        "revision_conflicts": 0,
        "entity_lifetimes_started": 0,
        "entity_lifetimes_ended": 0,
        "command_mismatches": 0,
        "command_receipts": 0,
        "command_receipts_with_post_revision": 0,
        "safety_supervisor_preemptions": 0,
        "strategic_planner_cancellations": 0,
        "plan_execution_cancellations": 0,
        "safety_cleanups_started": 0,
        "safety_cleanups_completed": 0,
        "safety_cleanups_failed": 0,
        "safety_supervisor_terminals": 0,
        "safety_supervisor_safe_paused": 0,
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
                values["control_mode"] = str(control_mode) if control_mode is not None else None
            elif event_type == "decision":
                values["decisions"] += 1
                source = payload.get("source")
                if source == "reflex":
                    values["reflex_decisions"] += 1
                if source == "planner_error":
                    values["planner_errors"] += 1
                latency = payload.get("planner_latency_seconds")
                if latency is not None and source != "reflex":
                    decision_planner_latencies.append(float(latency))
            elif event_type == "strategic_planner_call":
                values["strategic_planner_calls"] += 1
                latency = payload.get("planner_latency_seconds")
                if latency is not None:
                    strategic_planner_latencies.append(float(latency))
            elif event_type == "action_receipt":
                values["action_receipts"] += 1
                values["primitive_actions"] += int(payload.get("primitive_actions", 0))
                if payload.get("dry_run"):
                    values["dry_run_actions"] += 1
                if payload.get("executed"):
                    values["executed_actions"] += 1
                if payload.get("command_id") is not None:
                    values["command_receipts"] += 1
                    if payload.get("causal_revision_advanced") is True:
                        values["command_receipts_with_post_revision"] += 1
            elif event_type == "action_rejected":
                values["rejected_actions"] += 1
            elif event_type == "observation":
                values["observations"] += 1
                if payload.get("telemetry_stale"):
                    values["stale_observations"] += 1
            elif event_type == "memory_written":
                values["memory_writes"] += 1
            elif event_type == "plan_proposed":
                values["plans_proposed"] += 1
            elif event_type == "plan_accepted":
                values["plans_accepted"] += 1
            elif event_type == "plan_rejected":
                values["plans_rejected"] += 1
            elif event_type == "plan_completed":
                values["plans_completed"] += 1
            elif event_type == "plan_aborted":
                values["plans_aborted"] += 1
            elif event_type == "plan_step_started":
                values["plan_steps_started"] += 1
            elif event_type == "plan_step_succeeded":
                values["plan_steps_succeeded"] += 1
            elif event_type == "plan_step_failed":
                values["plan_steps_failed"] += 1
            elif event_type == "plan_step_cancelled":
                values["plan_steps_cancelled"] += 1
            elif event_type == "plan_budget_reserved":
                values["budget_reservations"] += 1
            elif event_type == "plan_budget_committed":
                values["budget_commits"] += 1
            elif event_type == "plan_budget_released":
                values["budget_releases"] += 1
            elif event_type == "safety_supervisor_preempted":
                values["safety_supervisor_preemptions"] += 1
            elif event_type == "strategic_planner_cancelled":
                values["strategic_planner_cancellations"] += 1
            elif event_type == "plan_execution_cancelled":
                values["plan_execution_cancellations"] += 1
            elif event_type == "safety_cleanup_started":
                values["safety_cleanups_started"] += 1
            elif event_type == "safety_cleanup_completed":
                values["safety_cleanups_completed"] += 1
            elif event_type == "safety_cleanup_failed":
                values["safety_cleanups_failed"] += 1
            elif event_type == "safety_supervisor_terminal":
                values["safety_supervisor_terminals"] += 1
                if payload.get("status") == "safe_paused":
                    values["safety_supervisor_safe_paused"] += 1
            elif event_type == "world_state_update":
                if payload.get("sequence_status") == "duplicate":
                    values["sequence_stall_incidents"] += 1
                values["transient_events_lost"] = max(
                    values["transient_events_lost"],
                    int(payload.get("transient_events_lost", 0)),
                )
                values["subscriber_update_drops"] = max(
                    values["subscriber_update_drops"],
                    int(payload.get("subscriber_update_drops", 0)),
                )
                values["observation_pump_errors"] = max(
                    values["observation_pump_errors"],
                    int(payload.get("observation_pump_errors", 0)),
                )
            elif event_type == "world_state_event":
                if payload.get("event_type") == "observation_event":
                    values["transient_events_retained"] += 1
            elif event_type == "world_state_finished":
                values["sequence_stall_incidents"] = max(
                    values["sequence_stall_incidents"],
                    int(payload.get("sequence_stall_incidents", 0)),
                )
                values["transient_events_retained"] = max(
                    values["transient_events_retained"],
                    int(payload.get("transient_events_retained", 0)),
                )
                for field_name in (
                    "transient_events_lost",
                    "revision_regressions",
                    "revision_conflicts",
                    "entity_lifetimes_started",
                    "entity_lifetimes_ended",
                    "command_mismatches",
                ):
                    values[field_name] = max(
                        values[field_name],
                        int(payload.get(field_name, 0)),
                    )
                values["subscriber_update_drops"] = max(
                    values["subscriber_update_drops"],
                    int(payload.get("subscriber_drops", 0)),
                )
                values["observation_pump_errors"] = max(
                    values["observation_pump_errors"],
                    int(payload.get("pump_errors", 0)),
                )
            elif event_type == "run_finished":
                control_mode = payload.get("control_mode")
                if control_mode is not None:
                    values["control_mode"] = str(control_mode)
                values["success"] = payload.get("success")
                values["steps_completed"] = payload.get("steps_completed")
                values["stop_reason"] = payload.get("stop_reason")
    if values["strategic_planner_calls"] == 0:
        values["strategic_planner_calls"] = max(
            0,
            values["decisions"] - values["reflex_decisions"],
        )
    actions_per_call = (
        values["action_receipts"] / values["strategic_planner_calls"]
        if values["strategic_planner_calls"]
        else None
    )
    causal_receipt_percentage = (
        100.0 * values["command_receipts_with_post_revision"] / values["command_receipts"]
        if values["command_receipts"]
        else None
    )
    safety_cleanup_success_percentage = (
        100.0 * values["safety_cleanups_completed"] / values["safety_cleanups_started"]
        if values["safety_cleanups_started"]
        else None
    )
    planner_latencies = strategic_planner_latencies or decision_planner_latencies
    if not planner_latencies:
        return LogMetrics(
            **values,
            actions_per_strategic_planner_call=actions_per_call,
            receipts_with_post_command_revision_percentage=(causal_receipt_percentage),
            safety_cleanup_success_percentage=safety_cleanup_success_percentage,
        )
    ordered = sorted(planner_latencies)
    p95_index = max(0, ceil(len(ordered) * 0.95) - 1)
    return LogMetrics(
        **values,
        mean_planner_latency_seconds=fmean(ordered),
        p50_planner_latency_seconds=median(ordered),
        p95_planner_latency_seconds=ordered[p95_index],
        actions_per_strategic_planner_call=actions_per_call,
        receipts_with_post_command_revision_percentage=causal_receipt_percentage,
        safety_cleanup_success_percentage=safety_cleanup_success_percentage,
    )


@dataclass(slots=True)
class PlanLifecycle:
    plan_id: str
    plan_version: int | None = None
    status: str = "proposed"
    active_step_id: str | None = None
    succeeded_step_ids: list[str] = field(default_factory=list)
    failed_step_ids: list[str] = field(default_factory=list)
    cancelled_step_ids: list[str] = field(default_factory=list)


def replay_plan_lifecycle(path: Path) -> dict[str, PlanLifecycle]:
    """Rebuild each plan's executor-owned lifecycle from append-only events."""

    plans: dict[str, PlanLifecycle] = {}
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
        "plan_patch_requested": "needs_replan",
        "plan_patched": "running",
        "plan_completed": "completed",
        "plan_aborted": "aborted",
    }
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            event_type = record.get("event_type")
            if event_type not in status_by_event:
                continue
            payload = record.get("payload") or {}
            plan_id = payload.get("plan_id")
            if not isinstance(plan_id, str):
                continue
            lifecycle = plans.setdefault(plan_id, PlanLifecycle(plan_id=plan_id))
            version = payload.get("plan_version")
            if isinstance(version, int):
                lifecycle.plan_version = version
            step_id = payload.get("step_id")
            if isinstance(step_id, str):
                lifecycle.active_step_id = step_id
                if (
                    event_type == "plan_step_succeeded"
                    and step_id not in lifecycle.succeeded_step_ids
                ):
                    lifecycle.succeeded_step_ids.append(step_id)
                elif event_type == "plan_step_failed" and step_id not in lifecycle.failed_step_ids:
                    lifecycle.failed_step_ids.append(step_id)
                elif (
                    event_type == "plan_step_cancelled"
                    and step_id not in lifecycle.cancelled_step_ids
                ):
                    lifecycle.cancelled_step_ids.append(step_id)
            lifecycle.status = status_by_event[event_type]
            if event_type in {"plan_rejected", "plan_completed", "plan_aborted"}:
                lifecycle.active_step_id = None
    return plans
