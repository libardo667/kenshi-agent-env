from __future__ import annotations

import json
from pathlib import Path

from kenshi_agent.affordance_requests import aggregate_affordance_requests


def request_event(
    *,
    run_id: str,
    capability_slug: str,
    capability_description: str,
    intent_class: str = "interact",
    urgency: str = "blocks_current_goal",
    status: str = "retained",
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
                "blocked_goal": "Earn money by mining.",
                "why_needed": "No advertised action expresses the exact intention.",
                "evidence": "A current Copper Resource is visible in world targets.",
                "available_workaround": "Sell carried items.",
                "urgency": urgency,
            },
        },
    }


def write_log(path: Path, *events: dict[str, object]) -> None:
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


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
