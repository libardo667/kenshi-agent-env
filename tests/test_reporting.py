from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO

from kenshi_agent.models import (
    ActionReceipt,
    ControlMode,
    PauseAction,
    PlannerDecision,
    PurchaseEvidence,
    PurchaseItemAction,
    PurchaseStatus,
    SemanticActionReceipt,
    SkillAction,
)
from kenshi_agent.reporting import (
    ConsoleDecisionReporter,
    describe_action,
    describe_receipt,
    format_action,
)


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


def test_free_vendor_acquisition_is_narrated_as_pickup_not_spending() -> None:
    action = PurchaseItemAction(
        cell_label="wanted-poster",
        item_name="WANTED: The Red Bandit",
        expected_price=0,
        window="BARMAN",
        seller_id="entity-barman",
    )
    started = datetime.now(UTC)
    receipt = ActionReceipt(
        action=action,
        accepted=True,
        executed=True,
        dry_run=False,
        started_at=started,
        finished_at=started,
        primitive_actions=2,
        semantic=SemanticActionReceipt(
            action_kind="purchase_item",
            contract_version="2.1",
            revalidation="Rebound the exact free seller-owned cell.",
            purchase=PurchaseEvidence(
                status=PurchaseStatus.PURCHASED,
                seller_id="entity-barman",
                selected_character_id="entity-player",
                item_name="WANTED: The Red Bandit",
                expected_price=0,
                requested_quantity=1,
                purchased_quantity=1,
                money_before=100,
                money_after=100,
                inventory_quantity_before=0,
                inventory_quantity_after=1,
                observed_after_sequence=2,
                reason="Conserved the free acquisition.",
            ),
        ),
    )

    assert describe_action(action) == "Picking up WANTED: The Red Bandit."
    assert describe_receipt(receipt) == "Picked up 1 WANTED: The Red Bandit."


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


def test_console_reporter_makes_plan_failures_immediate_and_summarizes_them() -> None:
    stream = StringIO()
    narrator = RecordingNarrator()
    reporter = ConsoleDecisionReporter(
        run_id="failure-run",
        planner_name="openrouter",
        model_name="reasoning-model",
        narrator=narrator,
        stream=stream,
    )

    reporter.plan_failure(
        event_type="plan_rejected",
        step_index=7,
        plan_id="plan-pc-8",
        plan_version=1,
        step_id=None,
        reason="The plan requested direct live unpause.",
    )
    reporter.plan_failure(
        event_type="plan_patch_rejected",
        step_index=8,
        plan_id="plan-pc-9",
        plan_version=2,
        step_id="find-shop",
        reason="The patch pointed at a stale character.",
    )
    reporter.plan_failure(
        event_type="concurrent_planner_discarded",
        step_index=8,
        plan_id="plan-pc-9",
        plan_version=2,
        step_id="find-shop",
        reason="Concurrent option planning returned a fresh plan instead of a patch.",
    )
    reporter.plan_failure(
        event_type="plan_aborted",
        step_index=9,
        plan_id="plan-pc-9",
        plan_version=2,
        step_id="find-shop",
        reason="Kenshi cancelled the movement as movement_stalled.",
    )
    reporter.run_finished(steps_completed=9, stop_reason="Stopped for review.")

    output = stream.getvalue()
    assert "!!! PLAN REJECTED !!!" in output
    assert "step 07 | plan-pc-8 v1" in output
    assert "The plan requested direct live unpause." in output
    assert "!!! PLAN PATCH REJECTED !!!" in output
    assert "step find-shop" in output
    assert "!!! PATCH ADVISORY DISCARDED !!!" in output
    assert "!!! PLAN ABORTED !!!" in output
    assert "movement_stalled" in output
    assert (
        "PLAN FAILURE SUMMARY | 1 rejected | 1 aborted | "
        "1 patch rejected | 1 advisory discarded"
    ) in output
    assert "Last plan rejection | plan-pc-8 v1" in output
    assert "Last plan abort | plan-pc-9 v2 | step find-shop" in output

    spoken = [text for text, _ in narrator.utterances]
    assert "The plan was rejected. I'm replanning." in spoken
    assert "The plan patch was rejected. I'm keeping the current plan." in spoken
    assert "The plan stopped early. I'm replanning." in spoken
    assert all("movement_stalled" not in text for text in spoken)


def test_console_reporter_makes_host_terminal_failure_impossible_to_miss() -> None:
    stream = StringIO()
    reporter = ConsoleDecisionReporter(
        run_id="crashed-run",
        planner_name="openrouter",
        model_name="playing-model",
        stream=stream,
    )

    reporter.safety_failure(
        step_index=6,
        cause="host_terminal",
        reason="Kenshi entered terminal window 'Kenshi has crashed'.",
    )

    output = stream.getvalue()
    assert "!!! KENSHI CRASH DETECTED !!!" in output
    assert "step 06 | host_terminal" in output
    assert "Kenshi has crashed" in output


def test_console_reporter_summarizes_failures_when_run_does_not_finish() -> None:
    stream = StringIO()
    reporter = ConsoleDecisionReporter(
        run_id="interrupted-run",
        planner_name="openrouter",
        model_name="reasoning-model",
        stream=stream,
    )
    reporter.plan_failure(
        event_type="plan_rejected",
        step_index=2,
        plan_id="plan-pc-2",
        plan_version=1,
        step_id=None,
        reason="The plan had no causal success condition.",
    )

    reporter.close()

    output = stream.getvalue()
    assert "RUN ENDED WITHOUT NORMAL FINISH" in output
    assert "PLAN FAILURE SUMMARY | 1 rejected" in output
    assert "no causal success condition" in output


def test_runtime_authored_reasoning_is_never_read_aloud() -> None:
    """Reflex and supervisor rationales are diagnostics, not the agent's thoughts.

    This exact string was narrated during live run
    live-trade-surface-20260729-r1, read out to the operator word for word.
    """

    leaked = (
        "The active option ended without a terminal handoff while the world "
        "was still running: The monitored native option did not reach its "
        "terminal success, so the step cannot succeed: Kenshi ended the "
        "native movement as 'cancelled': movement_stalled."
    )
    narrator = RecordingNarrator()
    reporter = ConsoleDecisionReporter(
        stream=StringIO(),
        run_id="leak",
        control_mode=ControlMode.NATIVE_ASSISTED,
        planner_name="test",
        model_name="test",
        narrator=narrator,
    )

    reporter.decision(
        step_index=7,
        source="reflex",
        decision=PlannerDecision(
            intent="Recover option ownership before replanning.",
            rationale=leaked,
            action=PauseAction(paused=True),
            confidence=1.0,
        ),
        latency_seconds=0.0,
    )

    spoken = " ".join(text for text, _ in narrator.utterances)
    assert "movement_stalled" not in spoken
    assert "terminal success" not in spoken
    assert "Pausing the game." in spoken


def test_model_authored_reasoning_survives_past_a_status_line_clip() -> None:
    """A thought clipped at 150 characters is a fragment, not reasoning."""

    rationale = (
        "The Barman turned out to sell no goods, so the recruitment dialogue "
        "was a dead end, and the bar itself is still unlocated after several "
        "directional probes that revealed no new characters or shops nearby."
    )
    assert len(rationale) > 150
    narrator = RecordingNarrator()
    reporter = ConsoleDecisionReporter(
        stream=StringIO(),
        run_id="reasoning",
        control_mode=ControlMode.NATIVE_ASSISTED,
        planner_name="test",
        model_name="test",
        narrator=narrator,
    )

    reporter.decision(
        step_index=3,
        source="planner",
        decision=PlannerDecision(
            intent="Find an actual vendor.",
            rationale=rationale,
            action=PauseAction(paused=True),
            confidence=0.6,
        ),
        latency_seconds=1.0,
    )

    spoken = " ".join(text for text, _ in narrator.utterances)
    assert rationale in spoken
    assert "…" not in spoken
