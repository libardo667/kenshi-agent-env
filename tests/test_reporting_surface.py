"""The reporting-surface probes must name fields the code actually emits.

Three questions were reported as unanswerable gaps in a bundle that answered
all three. The probes said `offers`, `withheld` and `task_state`; the emitters
write `offered`, `withheld_unauthorable` and `character_work`. Because the
probes were resolved by substring-searching the rendered JSON, a name that
matched nothing was indistinguishable from evidence that was never recorded -
so the tool built to measure the reporting surface manufactured gaps in it, and
the three channels had been built and were working the whole time.

A measuring instrument that can silently read zero is worse than no instrument.
These tests resolve every probe against a payload produced by the real emitter,
so a probe naming a field that does not exist fails here rather than being
reported as missing evidence.
"""

from __future__ import annotations

import pytest

from kenshi_agent.core.observation import Observation
from kenshi_agent.core.telemetry import (
    CharacterState,
    CharacterWorkState,
    GameState,
    NativeControlState,
    NormalizedPointerBounds,
    TaskCollection,
    TaskCollectionCompleteness,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
)
from kenshi_agent.tooling.reporting_surface import (
    POST_MORTEM_QUESTIONS,
    resolve_probe,
)


def _observation_payload() -> dict[str, object]:
    """A digest from an observation carrying every channel a probe names."""

    observation = Observation(
        run_id="r",
        step_index=0,
        mode="mock",
        telemetry=TelemetrySnapshot(
            sequence=1,
            game=GameState(loaded=True, paused=True, money=4000),
            native_control=NativeControlState(),
            ui=UIState(
                active_screen="trade",
                visible_controls=[
                    VisibleUIControl(
                        label="Dried Meat",
                        role="item",
                        item_name="Dried Meat",
                        bounds=NormalizedPointerBounds(
                            min_x=0.1, max_x=0.2, min_y=0.1, max_y=0.2
                        ),
                    )
                ],
            ),
            roster=[
                CharacterState(
                    id="barth",
                    name="Barth",
                    work=CharacterWorkState(
                        has_player_orders=True,
                        ordinary_orders=TaskCollection(
                            items=[],
                            completeness=TaskCollectionCompleteness.COMPLETE,
                            known_total=0,
                        ),
                        jobs_enabled=False,
                        jobs=TaskCollection(
                            items=[],
                            completeness=TaskCollectionCompleteness.COMPLETE,
                            known_total=0,
                        ),
                        permanent_jobs=TaskCollection(
                            items=[],
                            completeness=TaskCollectionCompleteness.COMPLETE,
                            known_total=0,
                        ),
                        current_activity=None,
                    ),
                )
            ],
        ),
    )
    return observation.log_digest()


def _manifest_fields() -> set[str]:
    from kenshi_agent.core.planner_context import PlannerContextManifest

    return set(PlannerContextManifest.model_fields)


def _receipt_fields() -> set[str]:
    from kenshi_agent.core.affordance import AffordanceReceipt

    return set(AffordanceReceipt.model_fields)


@pytest.mark.parametrize(
    "question",
    POST_MORTEM_QUESTIONS,
    ids=lambda question: question.probe,
)
def test_every_probe_names_a_field_the_emitter_writes(question) -> None:  # type: ignore[no-untyped-def]
    if question.event_type == "observation":
        telemetry = _observation_payload()["telemetry"]
        within = question.probe.removeprefix("telemetry.")
        assert resolve_probe(telemetry, within), (
            f"{question.probe} resolves in no observation digest"
        )
    elif question.event_type == "planner_context_prepared":
        assert question.probe.split(".")[0] in _manifest_fields()
    elif question.event_type == "affordance_receipt":
        head, _, rest = question.probe.partition(".")
        # The emitter wraps the receipt model under a "receipt" key.
        assert head == "receipt"
        assert rest.split(".")[0] in _receipt_fields()
    else:  # pragma: no cover - a new event type needs a resolver here
        pytest.fail(f"no schema resolver for event type {question.event_type}")


def test_a_probe_that_names_nothing_is_not_silently_a_gap() -> None:
    """The exact failure mode: a wrong name must not read as absent evidence."""

    payload = {"offered": ["a"], "nested": {"deep": 1}}

    assert resolve_probe(payload, "offered")
    assert resolve_probe(payload, "nested.deep")
    assert not resolve_probe(payload, "offers")


def test_presence_is_measured_rather_than_truthiness() -> None:
    """A recorded zero answers the question as well as a recorded four thousand."""

    assert resolve_probe({"money": 0}, "money")
    assert resolve_probe({"orders": []}, "orders")


def test_probes_descend_into_lists() -> None:
    payload = {
        "roster": [
            {
                "work": {
                    "ordinary_orders": {
                        "items": [],
                        "completeness": "complete",
                        "known_total": 0,
                    }
                }
            }
        ]
    }

    assert resolve_probe(payload, "roster.work.ordinary_orders.known_total")
