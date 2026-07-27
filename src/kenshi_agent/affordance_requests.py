from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from .models import (
    AffordanceRequestStatus,
    AffordanceUrgency,
    RequestAffordanceAction,
    ScenarioIdentity,
    affordance_aggregation_key,
)
from .scenario_fixtures import ScenarioAttestation

MAX_GROUNDED_EXAMPLES_PER_CANDIDATE = 5
MAX_UNCLASSIFIED_SAMPLES = 20
_MISSING_SCENARIO = object()
_DemandKey = TypeVar("_DemandKey")


@dataclass(frozen=True, slots=True)
class GroundedAffordanceExample:
    run_id: str
    scenario: dict[str, str] | None
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
    distinct_scenario_count: int
    distinct_save_count: int
    unverified_run_count: int
    request_event_count: int
    retained_event_count: int
    duplicate_event_count: int
    urgency_run_counts: dict[str, int]
    urgency_scenario_counts: dict[str, int]
    grounded_examples: list[GroundedAffordanceExample]
    review_status: str = "needs_engineering_review"


@dataclass(frozen=True, slots=True)
class ScenarioCoverage:
    verified_run_count: int
    unverified_run_count: int
    distinct_scenario_count: int
    distinct_save_count: int
    dimension_scenario_counts: dict[str, dict[str, int]]


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
    scenario_coverage: ScenarioCoverage
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
        raise ValueError("event is not a JSON object")  # mutation: diagnostic-only
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(  # mutation: diagnostic-only
            "event has no non-empty run_id"
        )
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(  # mutation: diagnostic-only
            "event payload is not an object"
        )
    request = RequestAffordanceAction.model_validate(payload.get("request"))
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError(  # mutation: diagnostic-only
            "event evidence is not an object"
        )
    raw_status = evidence.get("status")
    if not isinstance(raw_status, str):
        raise ValueError(  # mutation: diagnostic-only
            "event evidence has no string status"
        )
    status = AffordanceRequestStatus(raw_status)
    expected_key = affordance_aggregation_key(request)
    if evidence.get("aggregation_key") != expected_key:
        raise ValueError(  # mutation: diagnostic-only
            "event evidence does not match the typed request key"
        )
    return run_id, request, status


def _incoming_demand_replaces(
    current: _RunDemand,
    incoming: _RunDemand,
) -> bool:
    return bool(
        _URGENCY_STRENGTH[incoming.strongest_urgency]
        > _URGENCY_STRENGTH[current.strongest_urgency]
        or (incoming.retained and not current.retained)
    )


def _merge_demand(
    demands: dict[_DemandKey, _RunDemand],
    key: _DemandKey,
    run: _RunDemand,
) -> None:
    current = demands.get(key)
    if current is None:
        demands[key] = _RunDemand(
            action=run.action,
            strongest_urgency=run.strongest_urgency,
            retained=run.retained,
        )
        return
    if _incoming_demand_replaces(current, run):
        current.action = run.action
    current.strongest_urgency = max(
        current.strongest_urgency,
        run.strongest_urgency,
        key=_URGENCY_STRENGTH.__getitem__,
    )
    if run.retained:
        current.retained = True


def _effective_scenarios(
    attested: dict[str, ScenarioAttestation | None],
) -> dict[str, ScenarioIdentity]:
    """Drop unattested, conflicting, or relabeled fixture identities."""

    declarations_by_key: dict[tuple[str, str], set[str]] = {}
    digests_by_key: dict[tuple[str, str], set[str]] = {}
    keys_by_save: dict[str, set[tuple[str, str]]] = {}
    keys_by_digest: dict[str, set[tuple[str, str]]] = {}
    for attestation in attested.values():
        if attestation is None:
            continue
        scenario = attestation.scenario
        key = (scenario.save_id, scenario.scenario_id)
        declarations_by_key.setdefault(key, set()).add(
            scenario.model_dump_json()
        )
        digests_by_key.setdefault(key, set()).add(attestation.fixture_digest)
        keys_by_save.setdefault(scenario.save_id, set()).add(key)
        keys_by_digest.setdefault(attestation.fixture_digest, set()).add(key)
    consistent_keys = {
        key
        for key, declarations in declarations_by_key.items()
        if len(declarations) == 1
        and len(digests_by_key[key]) == 1
        and len(keys_by_save[key[0]]) == 1
        and all(
            len(keys_by_digest[digest]) == 1
            for digest in digests_by_key[key]
        )
    }
    return {
        run_id: attestation.scenario
        for run_id, attestation in attested.items()
        if attestation is not None
        and (
            attestation.scenario.save_id,
            attestation.scenario.scenario_id,
        )
        in consistent_keys
    }


def _scenario_coverage(
    *,
    all_run_ids: set[str],
    scenarios: dict[str, ScenarioIdentity],
) -> ScenarioCoverage:
    unique_scenarios = {
        (scenario.save_id, scenario.scenario_id): scenario
        for scenario in scenarios.values()
    }
    dimension_counts: dict[str, dict[str, int]] = {}
    for field_name in (
        "environment",
        "danger",
        "economy",
        "party",
        "time_of_day",
    ):
        values: dict[str, int] = {}
        for scenario in unique_scenarios.values():
            value = str(getattr(scenario, field_name))
            values[value] = values.get(value, 0) + 1
        dimension_counts[field_name] = dict(sorted(values.items()))
    return ScenarioCoverage(
        verified_run_count=len(scenarios),
        unverified_run_count=len(all_run_ids - set(scenarios)),
        distinct_scenario_count=len(unique_scenarios),
        distinct_save_count=len(
            {scenario.save_id for scenario in unique_scenarios.values()}
        ),
        dimension_scenario_counts=dimension_counts,
    )


def _representative_example_runs(
    candidate: _Candidate,
    scenarios: dict[str, ScenarioIdentity],
) -> list[tuple[str, _RunDemand]]:
    """Retain one strongest example per verified scenario before raw reruns."""

    verified: dict[tuple[str, str], tuple[str, _RunDemand]] = {}
    unverified: list[tuple[str, _RunDemand]] = []
    for run_id, run in sorted(candidate.runs.items()):
        scenario = scenarios.get(run_id)
        if scenario is None:
            unverified.append((run_id, run))
            continue
        key = (scenario.save_id, scenario.scenario_id)
        current = verified.get(key)
        if current is None:
            verified[key] = (run_id, run)
            continue
        _, current_run = current
        if _incoming_demand_replaces(current_run, run):
            verified[key] = (run_id, run)
    representatives = [
        example for _, example in sorted(verified.items())
    ]
    return [*representatives, *unverified]


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
    all_run_ids: set[str] = set()
    verified_attestations: dict[str, ScenarioAttestation | None] = {}

    for path in paths:
        with path.open("r", encoding="utf-8") as handle:  # pragma: no mutate
            for line_number, line in enumerate(handle, start=1):
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                raw_run_id = record.get("run_id")
                if isinstance(raw_run_id, str) and raw_run_id:
                    all_run_ids.add(raw_run_id)
                if record.get("event_type") == "run_started":
                    if not isinstance(raw_run_id, str) or not raw_run_id:
                        continue
                    payload = record.get("payload")
                    raw_scenario = (
                        payload.get("scenario") if isinstance(payload, dict) else None
                    )
                    raw_attestation = (
                        payload.get("scenario_attestation")
                        if isinstance(payload, dict)
                        else None
                    )
                    try:
                        scenario = (
                            ScenarioIdentity.model_validate(raw_scenario)
                            if raw_scenario is not None
                            else None
                        )
                        attestation = (
                            ScenarioAttestation.model_validate(raw_attestation)
                            if raw_attestation is not None
                            else None
                        )
                        if (
                            scenario is None
                            or attestation is None
                            or attestation.scenario != scenario
                        ):
                            attestation = None
                    except ValidationError:
                        attestation = None
                    existing = verified_attestations.get(
                        raw_run_id,
                        _MISSING_SCENARIO,
                    )
                    if existing is _MISSING_SCENARIO:
                        verified_attestations[raw_run_id] = attestation
                    elif attestation != existing:
                        verified_attestations[raw_run_id] = None
                    continue
                if record.get("event_type") != "affordance_request":
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

                _merge_demand(
                    candidate.runs,
                    run_id,
                    _RunDemand(
                        action=action,
                        strongest_urgency=action.urgency,
                        retained=status is AffordanceRequestStatus.RETAINED,
                    ),
                )

    effective_scenarios = _effective_scenarios(verified_attestations)
    review_candidates: list[AffordanceReviewCandidate] = []
    for key, candidate in candidates.items():
        urgency_run_counts = {
            urgency.value: sum(
                run.strongest_urgency is urgency
                for run in candidate.runs.values()
            )
            for urgency in AffordanceUrgency
        }
        scenario_demands: dict[tuple[str, str], _RunDemand] = {}
        for run_id, run in candidate.runs.items():
            scenario = effective_scenarios.get(run_id)
            if scenario is None:
                continue
            _merge_demand(
                scenario_demands,
                (scenario.save_id, scenario.scenario_id),
                run,
            )
        urgency_scenario_counts = {
            urgency.value: sum(
                demand.strongest_urgency is urgency
                for demand in scenario_demands.values()
            )
            for urgency in AffordanceUrgency
        }
        examples = [
            GroundedAffordanceExample(
                run_id=run_id,
                scenario=(
                    effective_scenarios[run_id].model_dump()
                    if run_id in effective_scenarios
                    else None
                ),
                capability_description=run.action.capability_description,
                blocked_goal=run.action.blocked_goal,
                why_needed=run.action.why_needed,
                evidence=run.action.evidence,
                available_workaround=run.action.available_workaround,
                urgency=run.strongest_urgency.value,
            )
            for run_id, run in _representative_example_runs(
                candidate,
                effective_scenarios,
            )
        ][:MAX_GROUNDED_EXAMPLES_PER_CANDIDATE]
        review_candidates.append(
            AffordanceReviewCandidate(
                aggregation_key=key,
                game=candidate.action.game,
                intent_class=candidate.action.intent_class.value,
                capability_slug=candidate.action.capability_slug,
                capability_descriptions=sorted(candidate.descriptions),
                distinct_run_count=len(candidate.runs),
                distinct_scenario_count=len(scenario_demands),
                distinct_save_count=len(
                    {save_id for save_id, _ in scenario_demands}
                ),
                unverified_run_count=(
                    len(candidate.runs)
                    - sum(run_id in effective_scenarios for run_id in candidate.runs)
                ),
                request_event_count=candidate.request_events,
                retained_event_count=candidate.retained_events,
                duplicate_event_count=candidate.duplicate_events,
                urgency_run_counts=urgency_run_counts,
                urgency_scenario_counts=urgency_scenario_counts,
                grounded_examples=examples,
            )
        )

    review_candidates.sort(
        key=lambda candidate: (
            -int(
                candidate.urgency_run_counts[
                    AffordanceUrgency.SURVIVAL_CRITICAL.value
                ]
                > 0
            ),
            -candidate.distinct_save_count,
            -candidate.urgency_scenario_counts[
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
        scenario_coverage=_scenario_coverage(
            all_run_ids=all_run_ids,
            scenarios=effective_scenarios,
        ),
        candidates=review_candidates,
        unclassified_samples=unclassified_samples,
    )
