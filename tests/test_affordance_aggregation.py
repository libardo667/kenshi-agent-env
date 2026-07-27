from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from kenshi_agent.affordance_requests import (
    _merge_demand,
    _parse_classified_event,
    _RunDemand,
    aggregate_affordance_requests,
)
from kenshi_agent.models import (
    AffordanceUrgency,
    RequestAffordanceAction,
)


def request_event(
    *,
    run_id: str,
    capability_slug: str,
    capability_description: str,
    intent_class: str = "interact",
    urgency: str = "blocks_current_goal",
    status: str = "retained",
    blocked_goal: str = "Earn money by mining.",
    why_needed: str = "No advertised action expresses the exact intention.",
    evidence: str = "A current Copper Resource is visible in world targets.",
    available_workaround: str | None = "Sell carried items.",
) -> dict[str, object]:
    aggregation_key = f"kenshi:{intent_class}:{capability_slug}"
    return {
        "event_type": "affordance_request",
        "run_id": run_id,
        "step_index": 3,
        "payload": {
            "evidence": {
                "status": status,
                "reason": "Recorded for engineering review.",
                "request_number": 1,
                "aggregation_key": aggregation_key,
            },
            "request": {
                "kind": "request_affordance",
                "game": "kenshi",
                "intent_class": intent_class,
                "capability_slug": capability_slug,
                "capability_description": capability_description,
                "blocked_goal": blocked_goal,
                "why_needed": why_needed,
                "evidence": evidence,
                "available_workaround": available_workaround,
                "urgency": urgency,
            },
        },
    }


def run_started(
    *,
    run_id: str,
    scenario_id: str,
    save_id: str,
    environment: str = "outdoor",
    danger: str = "safe",
    economy: str = "broke",
    party: str = "solo",
    time_of_day: str = "day",
    fixture_verified: bool = True,
    fixture_digest: str | None = None,
) -> dict[str, object]:
    scenario = {
        "scenario_id": scenario_id,
        "save_id": save_id,
        "environment": environment,
        "danger": danger,
        "economy": economy,
        "party": party,
        "time_of_day": time_of_day,
    }
    return {
        "event_type": "run_started",
        "run_id": run_id,
        "step_index": None,
        "payload": {
            "scenario": scenario,
            "scenario_attestation": (
                {
                    "schema_version": 1,
                    "scenario": scenario,
                    "fixture_digest": (
                        fixture_digest
                        or hashlib.sha256(save_id.encode("utf-8")).hexdigest()
                    ),
                    "managed_save_name": "KenshiAgentScenario",
                    "identity_session_id": f"session-{run_id}",
                    "loaded_sequence": 10,
                    "verified_at": datetime.now(UTC).isoformat(),
                    "observed": {
                        "selected_character_id": "character-0",
                        "indoors": environment == "indoor",
                        "in_combat": danger == "hostile",
                        "money": 20 if economy == "broke" else 10_000,
                        "party_size": 1 if party == "solo" else 2,
                        "minute_of_day": 720 if time_of_day == "day" else 1_320,
                    },
                }
                if fixture_verified
                else None
            ),
        },
    }


def write_log(path: Path, *events: dict[str, object]) -> None:
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


def typed_action(
    *,
    capability_description: str,
    urgency: AffordanceUrgency,
) -> RequestAffordanceAction:
    event = request_event(
        run_id="typed",
        capability_slug="typed_gap",
        capability_description=capability_description,
        urgency=urgency.value,
    )
    payload = event["payload"]
    assert isinstance(payload, dict)
    return RequestAffordanceAction.model_validate(payload["request"])


def test_demand_merge_obeys_the_full_urgency_and_retention_truth_table() -> None:
    strength = {
        AffordanceUrgency.IMPROVES_FIDELITY: 1,
        AffordanceUrgency.BLOCKS_CURRENT_GOAL: 2,
        AffordanceUrgency.SURVIVAL_CRITICAL: 3,
    }
    for current_urgency in AffordanceUrgency:
        for incoming_urgency in AffordanceUrgency:
            for current_retained in (False, True):
                for incoming_retained in (False, True):
                    current_action = typed_action(
                        capability_description="Current demand.",
                        urgency=current_urgency,
                    )
                    incoming_action = typed_action(
                        capability_description="Incoming demand.",
                        urgency=incoming_urgency,
                    )
                    demands = {
                        "gap": _RunDemand(
                            action=current_action,
                            strongest_urgency=current_urgency,
                            retained=current_retained,
                        )
                    }
                    incoming = _RunDemand(
                        action=incoming_action,
                        strongest_urgency=incoming_urgency,
                        retained=incoming_retained,
                    )

                    _merge_demand(demands, "gap", incoming)

                    merged = demands["gap"]
                    stronger = (
                        strength[incoming_urgency] > strength[current_urgency]
                    )
                    replaces = stronger or (
                        incoming_retained and not current_retained
                    )
                    assert merged.action is (
                        incoming_action if replaces else current_action
                    )
                    assert merged.strongest_urgency is (
                        incoming_urgency if stronger else current_urgency
                    )
                    assert merged.retained is (
                        current_retained or incoming_retained
                    )


def test_classified_events_require_a_nonempty_string_run_identity() -> None:
    event = request_event(
        run_id="valid",
        capability_slug="typed_gap",
        capability_description="Typed gap.",
    )
    for invalid_run_id in (None, "", 42):
        invalid = {**event, "run_id": invalid_run_id}
        try:
            _parse_classified_event(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid run ID {invalid_run_id!r}")


def test_aggregation_preserves_exact_payloads_and_malformed_line_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    legacy = {
        "event_type": "affordance_request",
        "run_id": "legacy-run",
        "payload": {
            "request": {"capability": "Legacy free prose"},
            "evidence": {"status": "retained"},
        },
    }
    classified = [
        request_event(
            run_id="run-a",
            capability_slug="operate_world_target",
            capability_description="Initial description.",
            urgency="improves_fidelity",
            status="retained",
        ),
        request_event(
            run_id="run-a",
            capability_slug="operate_world_target",
            capability_description="Strongest description.",
            urgency="survival_critical",
            status="duplicate",
            blocked_goal="Escape starvation.",
            why_needed="The exact machine has no typed operation.",
            evidence="Fresh telemetry exposes the exact machine.",
            available_workaround=None,
        ),
        request_event(
            run_id="run-a",
            capability_slug="operate_world_target",
            capability_description="Weaker later description.",
            urgency="blocks_current_goal",
            status="duplicate",
        ),
        request_event(
            run_id="run-b",
            capability_slug="operate_world_target",
            capability_description="Second run description.",
            urgency="blocks_current_goal",
            status="duplicate",
        ),
    ]
    records: list[object] = [
        [],
        {"event_type": "ignored", "run_id": 42},
        {
            **run_started(
                run_id="invalid",
                scenario_id="invalid",
                save_id="invalid",
            ),
            "run_id": "",
        },
        legacy,
        {"event_type": "affordance_request"},
        *classified,
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    report = aggregate_affordance_requests([path, path])

    assert report.source_logs == [str(path.resolve())]
    assert report.request_events == 6
    assert report.classified_events == 4
    assert report.unclassified_events == 2
    assert report.scenario_coverage.verified_run_count == 0
    assert report.scenario_coverage.unverified_run_count == 3
    assert report.scenario_coverage.distinct_scenario_count == 0
    assert report.scenario_coverage.distinct_save_count == 0
    assert len(report.unclassified_samples) == 2
    assert report.unclassified_samples[0].source_log == str(path.resolve())
    assert report.unclassified_samples[0].run_id == "legacy-run"
    assert report.unclassified_samples[0].reason.startswith("line 4:")
    assert report.unclassified_samples[0].legacy_capability == "Legacy free prose"
    assert report.unclassified_samples[1].run_id is None
    assert report.unclassified_samples[1].reason.startswith("line 5:")
    assert report.unclassified_samples[1].legacy_capability is None

    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.aggregation_key == "kenshi:interact:operate_world_target"
    assert candidate.game == "kenshi"
    assert candidate.intent_class == "interact"
    assert candidate.capability_slug == "operate_world_target"
    assert candidate.capability_descriptions == [
        "Initial description.",
        "Second run description.",
        "Strongest description.",
        "Weaker later description.",
    ]
    assert candidate.distinct_run_count == 2
    assert candidate.unverified_run_count == 2
    assert candidate.request_event_count == 4
    assert candidate.retained_event_count == 1
    assert candidate.duplicate_event_count == 3
    assert candidate.urgency_run_counts == {
        "survival_critical": 1,
        "blocks_current_goal": 1,
        "improves_fidelity": 0,
    }
    assert len(candidate.grounded_examples) == 2
    strongest = candidate.grounded_examples[0]
    assert strongest.run_id == "run-a"
    assert strongest.scenario is None
    assert strongest.capability_description == "Strongest description."
    assert strongest.blocked_goal == "Escape starvation."
    assert strongest.why_needed == "The exact machine has no typed operation."
    assert strongest.evidence == "Fresh telemetry exposes the exact machine."
    assert strongest.available_workaround is None
    assert strongest.urgency == "survival_critical"
    assert candidate.grounded_examples[1].available_workaround == (
        "Sell carried items."
    )


def test_scenario_evidence_fails_closed_without_poisoning_later_runs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    repeated = run_started(
        run_id="run-repeated",
        scenario_id="repeated-valid",
        save_id="repeated-save",
    )
    conflict_first = run_started(
        run_id="run-conflict",
        scenario_id="conflict",
        save_id="conflict-save",
        danger="safe",
    )
    conflict_second = run_started(
        run_id="run-conflict",
        scenario_id="conflict",
        save_id="conflict-save",
        danger="hostile",
    )
    events = [
        {
            **run_started(
                run_id="run-malformed",
                scenario_id="malformed",
                save_id="malformed-save",
            ),
            "payload": {
                "scenario": {"scenario_id": 42},
                "scenario_attestation": {"schema_version": "invalid"},
            },
        },
        request_event(
            run_id="run-malformed",
            capability_slug="scenario_gap",
            capability_description="Malformed declaration.",
        ),
        run_started(
            run_id="a-unattested",
            scenario_id="unattested",
            save_id="unattested-save",
            fixture_verified=False,
        ),
        request_event(
            run_id="a-unattested",
            capability_slug="scenario_gap",
            capability_description="Unattested demand.",
        ),
        run_started(
            run_id="b-valid",
            scenario_id="first-valid",
            save_id="first-save",
        ),
        request_event(
            run_id="b-valid",
            capability_slug="scenario_gap",
            capability_description="First valid demand.",
        ),
        run_started(
            run_id="run-save-a",
            scenario_id="save-conflict-a",
            save_id="shared-save",
            fixture_digest="a" * 64,
        ),
        request_event(
            run_id="run-save-a",
            capability_slug="scenario_gap",
            capability_description="Save conflict A.",
        ),
        run_started(
            run_id="run-save-b",
            scenario_id="save-conflict-b",
            save_id="shared-save",
            fixture_digest="b" * 64,
        ),
        request_event(
            run_id="run-save-b",
            capability_slug="scenario_gap",
            capability_description="Save conflict B.",
        ),
        repeated,
        repeated,
        request_event(
            run_id="run-repeated",
            capability_slug="scenario_gap",
            capability_description="Repeated valid declaration.",
        ),
        conflict_first,
        conflict_second,
        request_event(
            run_id="run-conflict",
            capability_slug="scenario_gap",
            capability_description="Conflicting declaration.",
        ),
    ]
    write_log(path, *events)

    report = aggregate_affordance_requests([path])

    assert report.scenario_coverage.verified_run_count == 2
    assert report.scenario_coverage.unverified_run_count == 5
    assert report.scenario_coverage.distinct_scenario_count == 2
    assert report.scenario_coverage.distinct_save_count == 2
    assert report.scenario_coverage.dimension_scenario_counts == {
        "environment": {"outdoor": 2},
        "danger": {"safe": 2},
        "economy": {"broke": 2},
        "party": {"solo": 2},
        "time_of_day": {"day": 2},
    }
    candidate = report.candidates[0]
    assert candidate.distinct_run_count == 7
    assert candidate.distinct_scenario_count == 2
    assert candidate.distinct_save_count == 2
    assert candidate.unverified_run_count == 5
    verified_scenario_ids = {
        example.scenario["scenario_id"]
        for example in candidate.grounded_examples
        if example.scenario is not None
    }
    assert verified_scenario_ids == {"first-valid", "repeated-valid"}


def test_representative_and_ranking_prefer_retained_urgent_scenario_demand(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    events: list[dict[str, object]] = []
    for run_id, urgency, status in (
        ("run-a", "improves_fidelity", "retained"),
        ("run-b", "survival_critical", "duplicate"),
        ("run-c", "improves_fidelity", "duplicate"),
        ("run-y", "survival_critical", "retained"),
        ("run-z", "improves_fidelity", "duplicate"),
    ):
        events.extend(
            [
                run_started(
                    run_id=run_id,
                    scenario_id="shared-scenario",
                    save_id="shared-save",
                ),
                request_event(
                    run_id=run_id,
                    capability_slug="representative_gap",
                    capability_description=f"Description from {run_id}.",
                    urgency=urgency,
                    status=status,
                ),
            ]
        )
    events.extend(
        [
            request_event(
                run_id="run-a",
                capability_slug="a_fidelity_gap",
                capability_description="Lower-priority gap.",
                urgency="improves_fidelity",
            ),
            request_event(
                run_id="run-a",
                capability_slug="z_blocking_gap",
                capability_description="Higher-priority gap.",
                urgency="blocks_current_goal",
            ),
        ]
    )
    write_log(path, *events)

    report = aggregate_affordance_requests([path])

    assert [candidate.capability_slug for candidate in report.candidates] == [
        "representative_gap",
        "z_blocking_gap",
        "a_fidelity_gap",
    ]
    representative = report.candidates[0]
    assert len(representative.grounded_examples) == 1
    assert representative.grounded_examples[0].run_id == "run-y"
    assert representative.grounded_examples[0].scenario is not None
    assert representative.grounded_examples[0].scenario["scenario_id"] == (
        "shared-scenario"
    )


def test_unclassified_samples_obey_the_exact_report_cap(tmp_path: Path) -> None:
    path = tmp_path / "malformed.jsonl"
    events = [
        {
            "event_type": "affordance_request",
            "run_id": f"malformed-{index}",
        }
        for index in range(21)
    ]
    write_log(path, *events)

    report = aggregate_affordance_requests([path])

    assert report.request_events == 21
    assert report.unclassified_events == 21
    assert len(report.unclassified_samples) == 20


def test_same_typed_gap_across_runs_forms_one_grounded_review_candidate(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    write_log(
        first,
        request_event(
            run_id="run-a",
            capability_slug="operate_world_target",
            capability_description="Operate the selected mine.",
        ),
        request_event(
            run_id="run-a",
            capability_slug="operate_world_target",
            capability_description="Work an exact ore node.",
            status="duplicate",
        ),
    )
    write_log(
        second,
        request_event(
            run_id="run-b",
            capability_slug="operate_world_target",
            capability_description="Use a contextual world task.",
            urgency="survival_critical",
        ),
    )

    report = aggregate_affordance_requests([first, second])

    assert report.request_events == 3
    assert report.classified_events == 3
    assert report.unclassified_events == 0
    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.aggregation_key == "kenshi:interact:operate_world_target"
    assert candidate.distinct_run_count == 2
    assert candidate.request_event_count == 3
    assert candidate.retained_event_count == 2
    assert candidate.duplicate_event_count == 1
    assert candidate.urgency_run_counts == {
        "survival_critical": 1,
        "blocks_current_goal": 1,
        "improves_fidelity": 0,
    }
    assert [example.run_id for example in candidate.grounded_examples] == [
        "run-a",
        "run-b",
    ]
    assert candidate.review_status == "needs_engineering_review"


def test_same_save_scenario_reruns_do_not_inflate_cross_scenario_demand(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    events: list[dict[str, object]] = []
    for run_id in ("run-a", "run-b"):
        events.extend(
            [
                run_started(
                    run_id=run_id,
                    scenario_id="hub-outdoor-safe-day",
                    save_id="hub-start-v1",
                ),
                request_event(
                    run_id=run_id,
                    capability_slug="travel_to_map_destination",
                    capability_description="Travel to a chosen map destination.",
                    intent_class="move",
                ),
            ]
        )
    events.extend(
        [
            run_started(
                run_id="run-c",
                scenario_id="squin-indoor-hostile-night",
                save_id="squin-captured-v1",
                environment="indoor",
                danger="hostile",
                economy="funded",
                party="squad",
                time_of_day="night",
            ),
            request_event(
                run_id="run-c",
                capability_slug="travel_to_map_destination",
                capability_description="Travel to a chosen map destination.",
                intent_class="move",
                urgency="survival_critical",
            ),
        ]
    )
    write_log(path, *events)

    report = aggregate_affordance_requests([path])

    candidate = report.candidates[0]
    assert candidate.distinct_run_count == 3
    assert candidate.distinct_scenario_count == 2
    assert candidate.distinct_save_count == 2
    assert candidate.unverified_run_count == 0
    assert candidate.urgency_scenario_counts == {
        "survival_critical": 1,
        "blocks_current_goal": 1,
        "improves_fidelity": 0,
    }
    assert len(candidate.grounded_examples) == 2
    assert {
        example.scenario["scenario_id"]
        for example in candidate.grounded_examples
        if example.scenario is not None
    } == {
        "hub-outdoor-safe-day",
        "squin-indoor-hostile-night",
    }
    assert report.scenario_coverage.verified_run_count == 3
    assert report.scenario_coverage.distinct_scenario_count == 2
    assert report.scenario_coverage.distinct_save_count == 2
    assert report.scenario_coverage.dimension_scenario_counts == {
        "environment": {"indoor": 1, "outdoor": 1},
        "danger": {"hostile": 1, "safe": 1},
        "economy": {"broke": 1, "funded": 1},
        "party": {"solo": 1, "squad": 1},
        "time_of_day": {"day": 1, "night": 1},
    }


def test_undeclared_run_stays_visible_without_becoming_scenario_recurrence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    write_log(
        path,
        request_event(
            run_id="historical-run",
            capability_slug="travel_to_map_destination",
            capability_description="Travel to a chosen map destination.",
            intent_class="move",
        ),
    )

    report = aggregate_affordance_requests([path])

    candidate = report.candidates[0]
    assert candidate.distinct_run_count == 1
    assert candidate.distinct_scenario_count == 0
    assert candidate.distinct_save_count == 0
    assert candidate.unverified_run_count == 1
    assert report.scenario_coverage.unverified_run_count == 1


def test_unattested_scenario_labels_do_not_become_recurrence_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unverified.jsonl"
    write_log(
        path,
        run_started(
            run_id="run-unverified",
            scenario_id="claimed-scenario",
            save_id="claimed-save",
            fixture_verified=False,
        ),
        request_event(
            run_id="run-unverified",
            capability_slug="remote_map_travel",
            capability_description="Travel to a remote map location.",
        ),
    )

    report = aggregate_affordance_requests([path])

    assert report.scenario_coverage.verified_run_count == 0
    assert report.scenario_coverage.unverified_run_count == 1
    assert report.candidates[0].distinct_scenario_count == 0


def test_same_fixture_digest_cannot_masquerade_as_distinct_saves(
    tmp_path: Path,
) -> None:
    shared_digest = "d" * 64
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    write_log(
        first,
        run_started(
            run_id="run-a",
            scenario_id="scenario-a",
            save_id="save-a",
            fixture_digest=shared_digest,
        ),
        request_event(
            run_id="run-a",
            capability_slug="remote_map_travel",
            capability_description="Travel remotely.",
        ),
    )
    write_log(
        second,
        run_started(
            run_id="run-b",
            scenario_id="scenario-b",
            save_id="save-b",
            fixture_digest=shared_digest,
        ),
        request_event(
            run_id="run-b",
            capability_slug="remote_map_travel",
            capability_description="Travel remotely.",
        ),
    )

    report = aggregate_affordance_requests([first, second])

    assert report.scenario_coverage.verified_run_count == 0
    assert report.scenario_coverage.unverified_run_count == 2
    assert report.candidates[0].distinct_save_count == 0


def test_reused_scenario_identity_with_conflicting_axes_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    write_log(
        path,
        run_started(
            run_id="run-a",
            scenario_id="hub-test",
            save_id="hub-start-v1",
            danger="safe",
        ),
        request_event(
            run_id="run-a",
            capability_slug="travel_to_map_destination",
            capability_description="Travel to a chosen map destination.",
            intent_class="move",
        ),
        run_started(
            run_id="run-b",
            scenario_id="hub-test",
            save_id="hub-start-v1",
            danger="hostile",
        ),
        request_event(
            run_id="run-b",
            capability_slug="travel_to_map_destination",
            capability_description="Travel to a chosen map destination.",
            intent_class="move",
        ),
    )

    report = aggregate_affordance_requests([path])

    assert report.scenario_coverage.verified_run_count == 0
    assert report.scenario_coverage.unverified_run_count == 2
    assert report.candidates[0].distinct_scenario_count == 0
    assert report.candidates[0].unverified_run_count == 2


def test_declared_diversity_outranks_more_reruns_of_one_scenario(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    events: list[dict[str, object]] = []
    for run_id in ("repeat-a", "repeat-b", "repeat-c"):
        events.extend(
            [
                run_started(
                    run_id=run_id,
                    scenario_id="hub-repeat",
                    save_id="hub-start-v1",
                ),
                request_event(
                    run_id=run_id,
                    capability_slug="repeat_only_gap",
                    capability_description="A gap repeated in one scenario.",
                ),
            ]
        )
    for index, run_id in enumerate(("diverse-a", "diverse-b"), start=1):
        events.extend(
            [
                run_started(
                    run_id=run_id,
                    scenario_id=f"diverse-{index}",
                    save_id=f"save-{index}",
                ),
                request_event(
                    run_id=run_id,
                    capability_slug="diverse_gap",
                    capability_description="A gap seen in distinct scenarios.",
                ),
            ]
        )
    write_log(path, *events)

    report = aggregate_affordance_requests([path])

    assert [candidate.capability_slug for candidate in report.candidates] == [
        "diverse_gap",
        "repeat_only_gap",
    ]
    assert report.candidates[0].distinct_run_count == 2
    assert report.candidates[1].distinct_run_count == 3


def test_matching_prose_never_merges_distinct_game_specific_slugs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    write_log(
        path,
        request_event(
            run_id="run-a",
            capability_slug="operate_world_target",
            capability_description="Act on the target.",
        ),
        request_event(
            run_id="run-a",
            capability_slug="attack_world_target",
            capability_description="Act on the target.",
        ),
    )

    report = aggregate_affordance_requests([path])

    assert [candidate.aggregation_key for candidate in report.candidates] == [
        "kenshi:interact:attack_world_target",
        "kenshi:interact:operate_world_target",
    ]


def test_survival_critical_then_cross_run_recurrence_ranks_review_queue(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    write_log(
        path,
        request_event(
            run_id="run-a",
            capability_slug="withdraw_from_threat",
            capability_description="Retreat from an immediate threat.",
            intent_class="move",
            urgency="survival_critical",
        ),
        request_event(
            run_id="run-a",
            capability_slug="travel_to_map_destination",
            capability_description="Travel to a chosen map destination.",
            intent_class="move",
        ),
        request_event(
            run_id="run-b",
            capability_slug="travel_to_map_destination",
            capability_description="Travel to a chosen map destination.",
            intent_class="move",
        ),
    )

    report = aggregate_affordance_requests([path])

    assert [candidate.capability_slug for candidate in report.candidates] == [
        "withdraw_from_threat",
        "travel_to_map_destination",
    ]
    assert all(
        candidate.review_status == "needs_engineering_review"
        for candidate in report.candidates
    )


def test_legacy_free_text_is_reported_unclassified_instead_of_guessed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.jsonl"
    write_log(
        path,
        {
            "event_type": "affordance_request",
            "run_id": "legacy-run",
            "payload": {
                "evidence": {
                    "status": "retained",
                    "reason": "Old wire shape.",
                    "request_number": 1,
                    "normalized_capability": "operate the target",
                },
                "request": {
                    "kind": "request_affordance",
                    "capability": "Operate the target",
                    "blocked_goal": "Mine ore.",
                    "why_needed": "No control exists.",
                    "evidence": "A mine is nearby.",
                    "urgency": "blocks_current_goal",
                },
            },
        },
    )

    report = aggregate_affordance_requests([path])

    assert report.request_events == 1
    assert report.classified_events == 0
    assert report.unclassified_events == 1
    assert report.candidates == []
    assert report.unclassified_samples[0].run_id == "legacy-run"
    assert report.unclassified_samples[0].legacy_capability == "Operate the target"
