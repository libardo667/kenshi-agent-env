from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO

from kenshi_agent.models import ActionReceipt, PlannerDecision, SkillAction
from kenshi_agent.reporting import ConsoleDecisionReporter, format_action


def test_format_action_renders_skill_arguments_compactly() -> None:
    action = SkillAction(name="move_visible_terrain", args={"x": 0.4, "y": 0.5})

    assert format_action(action) == "move_visible_terrain(x=0.4, y=0.5)"


def test_console_reporter_streams_decision_and_receipt() -> None:
    stream = StringIO()
    reporter = ConsoleDecisionReporter(
        run_id="visible-run",
        planner_name="openai",
        model_name="gpt-5.6-luna",
        stream=stream,
    )
    decision = PlannerDecision(
        intent="Walk toward the open road.",
        rationale="The route looks clear and stays near The Hub.",
        action=SkillAction(name="move_visible_terrain", args={"x": 0.4, "y": 0.5}),
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
        message="Movement pulse completed; paused state confirmed.",
    )

    reporter.run_started(30)
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
    assert "step 03  OBSERVE -> thinking" in output
    assert "DECIDE  1.25s | planner" in output
    assert "Why     The route looks clear" in output
    assert "Action  move_visible_terrain(x=0.4, y=0.5)" in output
    assert "DONE    0.75s" in output
    assert "Kenshi Agent finished | 1 turns | Test complete." in output
