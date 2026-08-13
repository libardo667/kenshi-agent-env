"""Exact planner-choice evidence and typed replay reconstruction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent.affordances import (
    AffordanceParameterKind,
    AffordanceSelection,
    enumerate_affordance_set,
)
from kenshi_agent.core.affordance import (
    AffordanceParameter,
    AffordanceSetEvent,
    AffordanceSetOffer,
    AffordanceSetParameter,
    AffordanceSourceCompleteness,
    AffordanceSourceCompletenessStatus,
    AffordanceWithheldCategory,
)
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.telemetry import TelemetrySnapshot
from kenshi_agent.core.world import WorldStateRevision
from kenshi_agent.planner_context import render_planner_payload
from kenshi_agent.planners.base import prepared_budgeted_input
from kenshi_agent.tooling.affordance_replay import (
    AffordanceSetReplayUnavailable,
    load_affordance_sets,
    reconstruct_choice,
)


def _observation(*, telemetry: TelemetrySnapshot | None = None, stale: bool = False) -> Observation:
    return Observation(
        run_id="affordance-evidence",
        step_index=3,
        mode="live",
        world_revision=WorldStateRevision(
            telemetry_sequence=7,
            observed_at_monotonic=12.5,
        ),
        telemetry=telemetry,
        telemetry_stale=stale,
    )


def _event() -> AffordanceSetEvent:
    base = enumerate_affordance_set(
        _observation(telemetry=TelemetrySnapshot(sequence=7))
    ).as_evidence(context_id="pc-1")
    return AffordanceSetEvent.model_validate(
        {
            **base.model_dump(mode="json"),
            "offers": (
            AffordanceSetOffer(
                affordance_id="aff-0123456789abcdef0123",
                operation_kind="wait",
                source_adapter="runtime",
                semantic="wait",
                semantic_parameters=(
                    AffordanceSetParameter(
                        name="seconds",
                        kind=AffordanceParameterKind.INTEGER,
                        required=True,
                        minimum=1,
                        maximum=10,
                    ),
                ),
                target_id_required=False,
            ),
            ),
        }
    )


def test_missing_stale_and_not_delivered_are_distinct() -> None:
    missing = enumerate_affordance_set(_observation())
    stale = enumerate_affordance_set(
        _observation(telemetry=TelemetrySnapshot(sequence=7), stale=True)
    )
    withheld = enumerate_affordance_set(_observation(), delivered=False)

    assert missing.withheld_categories == (AffordanceWithheldCategory.MISSING_TELEMETRY,)
    assert stale.withheld_categories == (AffordanceWithheldCategory.STALE_TELEMETRY,)
    assert withheld.withheld_categories == (AffordanceWithheldCategory.NOT_DELIVERED,)
    assert {source.status for source in missing.source_completeness} == {
        AffordanceSourceCompletenessStatus.UNKNOWN
    }
    assert {source.status for source in withheld.source_completeness} == {
        AffordanceSourceCompletenessStatus.NOT_DELIVERED
    }


def test_complete_empty_source_is_not_unknown_empty() -> None:
    enumeration = enumerate_affordance_set(
        _observation(telemetry=TelemetrySnapshot(sequence=7))
    )
    dialogue = next(
        source
        for source in enumeration.source_completeness
        if source.source_adapter == "dialogue_options"
    )

    assert dialogue.status is AffordanceSourceCompletenessStatus.COMPLETE
    assert dialogue.withheld_categories == ()
    assert not any(
        offer.source_adapter == "dialogue_options"
        for offer in enumeration.evidence_offers
    )


def test_budgeted_payload_must_equal_the_canonical_projection() -> None:
    observation = _observation()
    enumeration = enumerate_affordance_set(observation)
    payload = render_planner_payload(observation, affordance_set=enumeration)

    prepared = prepared_budgeted_input(
        observation,
        context_id="pc-1",
        payload=payload,
        affordance_enumeration=enumeration,
    )
    assert prepared.affordance_set == enumeration.as_evidence(context_id="pc-1")
    with pytest.raises(ValidationError, match="frozen"):
        prepared.affordance_set.offers = ()

    changed = json.loads(payload)
    changed["affordances"].append({"affordance_id": "aff-ffffffffffffffffffff"})
    with pytest.raises(ValueError, match="payload and affordance-set evidence disagree"):
        prepared_budgeted_input(
            observation,
            context_id="pc-2",
            payload=json.dumps(changed),
            affordance_enumeration=enumeration,
        )


def test_replay_loads_typed_fixture_and_refuses_old_logs(tmp_path) -> None:
    event = _event()
    fixture = Path(__file__).parent / "fixtures/session_logs/affordance_set.jsonl"
    assert load_affordance_sets(fixture) == (event,)

    old = tmp_path / "old.jsonl"
    old.write_text('{"event_type":"planner_context_prepared","payload":{}}\n')
    with pytest.raises(AffordanceSetReplayUnavailable, match="exact choice reconstruction"):
        load_affordance_sets(old)


def test_selection_reconstructs_without_prompt_or_labels() -> None:
    event = _event()
    selection = AffordanceSelection(
        semantic="wait",
        parameters=[AffordanceParameter(name="seconds", value=4)],
    )

    assert reconstruct_choice(event, selection) == event.offers[0]
    assert reconstruct_choice(
        event,
        selection,
        expected_affordance_id="aff-0123456789abcdef0123",
        expected_operation_kind="wait",
        expected_source_adapter="runtime",
    ) == event.offers[0]
    with pytest.raises(ValueError, match="exactly one delivered affordance"):
        reconstruct_choice(
            event,
            selection,
            expected_operation_kind="noop",
        )
    with pytest.raises(ValueError, match="exactly one delivered affordance"):
        reconstruct_choice(
            event,
            AffordanceSelection(
                semantic="wait",
                parameters=[AffordanceParameter(name="seconds", value=11)],
            ),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "unknown"},
        {"minimum": 10, "maximum": 1},
        {"kind": "choice", "choices": ()},
        {"kind": "choice", "choices": ("a", "a")},
        {"kind": "text", "minimum": 1},
    ],
)
def test_malformed_parameter_contracts_are_rejected(changes) -> None:
    payload = {
        "name": "value",
        "kind": "integer",
        "required": True,
    }
    payload.update(changes)

    with pytest.raises(ValidationError):
        AffordanceSetParameter.model_validate(payload)


def test_source_and_event_completeness_must_cohere() -> None:
    with pytest.raises(ValidationError, match="complete.*incomplete"):
        AffordanceSourceCompleteness(
            source_adapter="runtime",
            status=AffordanceSourceCompletenessStatus.COMPLETE,
            withheld_categories=(AffordanceWithheldCategory.SOURCE_TRUNCATED,),
        )
    payload = _event().model_dump(mode="json")
    payload["withheld_categories"] = ["not_bindable"]
    with pytest.raises(ValidationError, match="must equal source evidence"):
        AffordanceSetEvent.model_validate(payload)


def test_replay_rejects_unknown_adapter_operation_pairs(tmp_path) -> None:
    payload = _event().model_dump(mode="json")
    payload["offers"][0]["operation_kind"] = "native_shutdown"
    path = tmp_path / "forged.jsonl"
    path.write_text(
        json.dumps({"event_type": "affordance_set", "payload": payload}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AffordanceSetReplayUnavailable, match="adapter/operation"):
        load_affordance_sets(path)


def test_replay_requires_the_registered_source_completeness_inventory(tmp_path) -> None:
    observation = _observation()
    event = enumerate_affordance_set(observation).as_evidence(context_id="pc-1")
    payload = event.model_dump(mode="json")
    payload["source_completeness"][0]["source_adapter"] = "aardvark"
    path = tmp_path / "forged-source.jsonl"
    path.write_text(
        json.dumps({"event_type": "affordance_set", "payload": payload}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AffordanceSetReplayUnavailable, match="source completeness"):
        load_affordance_sets(path)
