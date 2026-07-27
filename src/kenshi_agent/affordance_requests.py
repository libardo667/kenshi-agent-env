from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .models import (
    AffordanceRequestStatus,
    AffordanceUrgency,
    RequestAffordanceAction,
    affordance_aggregation_key,
)

MAX_GROUNDED_EXAMPLES_PER_CANDIDATE = 5
MAX_UNCLASSIFIED_SAMPLES = 20


@dataclass(frozen=True, slots=True)
class GroundedAffordanceExample:
    run_id: str
    capability_description: str
    blocked_goal: str
    why_needed: str
    evidence: str
    available_workaround: str | None
    urgency: str


@dataclass(frozen=True, slots=True)
class AffordanceReviewCandidate:
    aggregation_key: str
    game: str
    intent_class: str
    capability_slug: str
    capability_descriptions: list[str]
    distinct_run_count: int
    request_event_count: int
    retained_event_count: int
    duplicate_event_count: int
    urgency_run_counts: dict[str, int]
    grounded_examples: list[GroundedAffordanceExample]
    review_status: str = "needs_engineering_review"


@dataclass(frozen=True, slots=True)
class UnclassifiedAffordanceSample:
    source_log: str
    run_id: str | None
    reason: str
    legacy_capability: str | None


@dataclass(frozen=True, slots=True)
class AffordanceAggregationReport:
    source_logs: list[str]
    request_events: int
    classified_events: int
    unclassified_events: int
    candidates: list[AffordanceReviewCandidate]
    unclassified_samples: list[UnclassifiedAffordanceSample]


@dataclass(slots=True)
class _RunDemand:
    action: RequestAffordanceAction
    strongest_urgency: AffordanceUrgency
    retained: bool


@dataclass(slots=True)
class _Candidate:
    action: RequestAffordanceAction
    request_events: int = 0
    retained_events: int = 0
    duplicate_events: int = 0
    descriptions: set[str] = field(default_factory=set)
    runs: dict[str, _RunDemand] = field(default_factory=dict)


_URGENCY_STRENGTH = {
    AffordanceUrgency.IMPROVES_FIDELITY: 1,
    AffordanceUrgency.BLOCKS_CURRENT_GOAL: 2,
    AffordanceUrgency.SURVIVAL_CRITICAL: 3,
}


def _unclassified_sample(
    *,
    path: Path,
    record: object,
    reason: str,
) -> UnclassifiedAffordanceSample:
    run_id: str | None = None
    legacy_capability: str | None = None
    if isinstance(record, dict):
        raw_run_id = record.get("run_id")
        if isinstance(raw_run_id, str):
            run_id = raw_run_id
        payload = record.get("payload")
        if isinstance(payload, dict):
            request = payload.get("request")
            if isinstance(request, dict):
                raw_capability = request.get("capability")
                if isinstance(raw_capability, str):
                    legacy_capability = raw_capability
    return UnclassifiedAffordanceSample(
        source_log=str(path),
        run_id=run_id,
        reason=reason,
        legacy_capability=legacy_capability,
    )


def _parse_classified_event(
    record: object,
) -> tuple[str, RequestAffordanceAction, AffordanceRequestStatus]:
    if not isinstance(record, dict):
        raise ValueError("event is not a JSON object")
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("event has no non-empty run_id")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("event payload is not an object")
    request = RequestAffordanceAction.model_validate(payload.get("request"))
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("event evidence is not an object")
    raw_status = evidence.get("status")
    if not isinstance(raw_status, str):
        raise ValueError("event evidence has no string status")
    status = AffordanceRequestStatus(raw_status)
    expected_key = affordance_aggregation_key(request)
    if evidence.get("aggregation_key") != expected_key:
        raise ValueError("event evidence does not match the typed request key")
    return run_id, request, status


def aggregate_affordance_requests(
    log_paths: list[Path],
) -> AffordanceAggregationReport:
    """Aggregate typed demand across runs without guessing at legacy prose."""

    paths = sorted({path.expanduser().resolve() for path in log_paths})
    candidates: dict[str, _Candidate] = {}
    request_events = 0
    classified_events = 0
    unclassified_events = 0
    unclassified_samples: list[UnclassifiedAffordanceSample] = []

    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                record = json.loads(line)
                if not isinstance(record, dict) or (
                    record.get("event_type") != "affordance_request"
                ):
                    continue
                request_events += 1
                try:
                    run_id, action, status = _parse_classified_event(record)
                except (ValidationError, ValueError, TypeError) as exc:
                    unclassified_events += 1
                    if len(unclassified_samples) < MAX_UNCLASSIFIED_SAMPLES:
                        unclassified_samples.append(
                            _unclassified_sample(
                                path=path,
                                record=record,
                                reason=f"line {line_number}: {exc}",
                            )
                        )
                    continue

                classified_events += 1
                key = affordance_aggregation_key(action)
                candidate = candidates.setdefault(key, _Candidate(action=action))
                candidate.request_events += 1
                candidate.descriptions.add(action.capability_description)
                if status is AffordanceRequestStatus.RETAINED:
                    candidate.retained_events += 1
                else:
                    candidate.duplicate_events += 1

                run = candidate.runs.get(run_id)
                if run is None:
                    candidate.runs[run_id] = _RunDemand(
                        action=action,
                        strongest_urgency=action.urgency,
                        retained=status is AffordanceRequestStatus.RETAINED,
                    )
                    continue
                stronger = (
                    _URGENCY_STRENGTH[action.urgency]
                    > _URGENCY_STRENGTH[run.strongest_urgency]
                )
                newly_retained = (
                    status is AffordanceRequestStatus.RETAINED
                    and not run.retained
                )
                if stronger or newly_retained:
                    run.action = action
                if stronger:
                    run.strongest_urgency = action.urgency
                if status is AffordanceRequestStatus.RETAINED:
                    run.retained = True

    review_candidates: list[AffordanceReviewCandidate] = []
    for key, candidate in candidates.items():
        urgency_counts = {
            urgency.value: sum(
                run.strongest_urgency is urgency
                for run in candidate.runs.values()
            )
            for urgency in AffordanceUrgency
        }
        examples = [
            GroundedAffordanceExample(
                run_id=run_id,
                capability_description=run.action.capability_description,
                blocked_goal=run.action.blocked_goal,
                why_needed=run.action.why_needed,
                evidence=run.action.evidence,
                available_workaround=run.action.available_workaround,
                urgency=run.strongest_urgency.value,
            )
            for run_id, run in sorted(candidate.runs.items())
        ][:MAX_GROUNDED_EXAMPLES_PER_CANDIDATE]
        review_candidates.append(
            AffordanceReviewCandidate(
                aggregation_key=key,
                game=candidate.action.game,
                intent_class=candidate.action.intent_class.value,
                capability_slug=candidate.action.capability_slug,
                capability_descriptions=sorted(candidate.descriptions),
                distinct_run_count=len(candidate.runs),
                request_event_count=candidate.request_events,
                retained_event_count=candidate.retained_events,
                duplicate_event_count=candidate.duplicate_events,
                urgency_run_counts=urgency_counts,
                grounded_examples=examples,
            )
        )

    review_candidates.sort(
        key=lambda candidate: (
            -candidate.urgency_run_counts[
                AffordanceUrgency.SURVIVAL_CRITICAL.value
            ],
            -candidate.distinct_run_count,
            -candidate.urgency_run_counts[
                AffordanceUrgency.BLOCKS_CURRENT_GOAL.value
            ],
            candidate.aggregation_key,
        )
    )
    return AffordanceAggregationReport(
        source_logs=[str(path) for path in paths],
        request_events=request_events,
        classified_events=classified_events,
        unclassified_events=unclassified_events,
        candidates=review_candidates,
        unclassified_samples=unclassified_samples,
    )
