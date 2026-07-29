from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO

from kenshi_agent.models import (
    ActionReceipt,
    ControlMode,
    PlannerDecision,
    PurchaseItemAction,
    SkillAction,
)
from kenshi_agent.reporting import ConsoleDecisionReporter, format_action


class RecordingNarrator:
    def __init__(self) -> None:
        self.utterances: list[tuple[str, str | None]] = []
        self.closed = False

    def say(self, text: str, *, key: str | None = None) -> None:
        self.utterances.append((text, key))

    def close(self) -> None:
        self.closed = True


def test_format_action_renders_skill_arguments_compactly() -> None:
    action = SkillAction(name="move_visible_terrain", args={"x": 0.4, "y": 0.5})

    assert format_action(action) == "move_visible_terrain(x=0.4, y=0.5)"


def test_console_reporter_streams_decision_and_receipt() -> None:
    stream = StringIO()
    narrator = RecordingNarrator()
    reporter = ConsoleDecisionReporter(
        run_id="visible-run",
        planner_name="openai",
        model_name="gpt-5.6-luna",
        control_mode=ControlMode.NATIVE_ASSISTED,
        stream=stream,
        narrator=narrator,
    )
    decision = PlannerDecision(
        intent="Buy food before leaving town.",
        rationale="The squad has money but no travel rations.",
        action=PurchaseItemAction(
            cell_label="seller-cell-9",
            item_name="Dried Meat",
            expected_price=72,
            window="Trade inventory",
            seller_id="entity-technical-identifier",
        ),
        confidence=0.8,
    )
    started = datetime.now(UTC)
    receipt = ActionReceipt(
        action=decision.action,
        accepted=True,
        executed=True,
        dry_run=False,
        started_at=started,
        finished_at=started + timedelta(seconds=0.75),
        primitive_actions=1,
        message=(
            "Native acknowledgement cmd-deadbeef reached a causal future "
            "telemetry revision."
        ),
    )

    reporter.run_started(30)
    reporter.planning_started(3)
    reporter.planning_started(3)
    reporter.decision(
        step_index=3,
        source="planner",
        decision=decision,
        latency_seconds=1.25,
    )
    reporter.action_receipt(step_index=3, receipt=receipt)
    reporter.run_finished(steps_completed=1, stop_reason="Test complete.")

    output = stream.getvalue()
    assert "gpt-5.6-luna | 30 turns" in output
    assert "control=native_assisted" in output
    assert "step 03  OBSERVE -> thinking" in output
    assert "DECIDE  1.25s | planner" in output
    assert "Why     The squad has money" in output
    assert "Action  purchase_item(" in output
    assert "DONE    0.75s" in output
    assert "Kenshi Agent finished | 1 turns | Test complete." in output

    spoken = " ".join(text for text, _ in narrator.utterances)
    assert spoken.count("I'm thinking") == 1
    assert "Buy food before leaving town." in spoken
    assert "The squad has money but no travel rations." in spoken
    assert "Buying Dried Meat." in spoken
    assert "Done." in spoken
    assert "cmd-deadbeef" not in spoken
    assert "causal future" not in spoken
    assert "telemetry revision" not in spoken
    assert "entity-technical-identifier" not in spoken
    assert "seller-cell-9" not in spoken
    assert "purchase_item" not in spoken


def test_console_reporter_narrates_continuous_plan_and_each_action() -> None:
    narrator = RecordingNarrator()
    reporter = ConsoleDecisionReporter(
        run_id="continuous-run",
        planner_name="openrouter",
        model_name="reasoning-model",
        narrator=narrator,
        stream=StringIO(),
    )
    action = SkillAction(name="move_visible_terrain", args={"x": 0.4, "y": 0.5})

    reporter.plan_accepted(
        step_index=4,
        objective="Reach Squin and look for supplies.",
        latency_seconds=3.5,
    )
    reporter.action_started(step_index=4, action=action)

    spoken = [text for text, _ in narrator.utterances]
    assert spoken == [
        "My plan is to reach Squin and look for supplies.",
        "Moving through the visible terrain.",
    ]
