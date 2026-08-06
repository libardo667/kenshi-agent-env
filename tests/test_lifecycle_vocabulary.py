"""Detaching a monitor is not cancelling an order, and the report must say so.

A step timeout, a strategic replan, a human handoff, the run ending, the process
stopping, or telemetry going quiet all end Python's foreground interest. None of
them touch the order Kenshi holds: the character goes on mining.

Every one of those collapsed into `CANCELLED`. The audit behind this module
found no Python path that sends order-clearing input at all - `option.cancel`
cancels an asyncio task and nothing else, and the one native release is guarded
by ownership - so the orders were never being cleared. The report just said they
were, and a post-mortem reading "cancelled" had no way to learn otherwise.
"""

from __future__ import annotations

import pytest

from kenshi_agent.core.lifecycle import (
    EVIDENCE_SEMANTICS_VERSION,
    LEGACY_EVIDENCE_SEMANTICS_VERSION,
    LifecycleOutcome,
    MonitorDisposition,
    OrderDisposition,
    order_disposition_from_evidence,
)

MINE = "TASK_MINE"


def _order(**kwargs: object) -> OrderDisposition:
    base: dict[str, object] = {
        "issued": True,
        "telemetry_fresh": True,
        "retained_task_names": frozenset(),
        "expected_task_name": MINE,
    }
    base.update(kwargs)
    return order_disposition_from_evidence(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The two questions are answered from different evidence


def test_a_timeout_leaves_a_retained_order_retained() -> None:
    """The headline case. Python stopped watching; Kenshi did not stop mining."""

    outcome = LifecycleOutcome(
        monitor=MonitorDisposition.DETACHED_AFTER_TIMEOUT,
        order=_order(retained_task_names={MINE}),
        observed_at_sequence=412,
    )

    assert outcome.monitor.detached
    assert outcome.order is OrderDisposition.RETAINED_AT_LAST_OBSERVATION
    assert outcome.order.order_may_still_be_running
    assert "still held the order" in outcome.describe()
    assert "cancel" not in outcome.describe().lower()


def test_telemetry_loss_makes_the_order_unknown_rather_than_ended() -> None:
    """Absence of evidence is not evidence the order ended.

    The old vocabulary could not say "unknown" at all, so a monitor that lost
    telemetry reported the same word as one that watched an order complete.
    """

    outcome = LifecycleOutcome(
        monitor=MonitorDisposition.DETACHED_ON_TELEMETRY_LOSS,
        order=_order(telemetry_fresh=False, retained_task_names=frozenset()),
    )

    assert outcome.order is OrderDisposition.UNKNOWN_AFTER_TELEMETRY_LOSS
    # Unknown must behave like "possibly still running": the expensive mistake
    # is ordering the same character twice.
    assert outcome.order.order_may_still_be_running
    assert "unknown" in outcome.describe()
    assert "no supporting evidence" in outcome.describe()


def test_a_human_handoff_says_handoff_not_cancelled() -> None:
    outcome = LifecycleOutcome(
        monitor=MonitorDisposition.DETACHED_FOR_HUMAN_HANDOFF,
        order=_order(retained_task_names={MINE}),
        observed_at_sequence=9,
    )

    described = outcome.describe()

    assert "human handoff" in described
    assert "cancel" not in described.lower()


def test_shutdown_says_shutdown_and_claims_nothing_about_the_order() -> None:
    """Process exit is not an instruction to anybody."""

    outcome = LifecycleOutcome(
        monitor=MonitorDisposition.DETACHED_AT_SHUTDOWN,
        order=_order(telemetry_fresh=False),
    )

    assert "shutdown" in outcome.describe()
    assert outcome.order is OrderDisposition.UNKNOWN_AFTER_TELEMETRY_LOSS


def test_run_end_is_distinct_from_a_timeout() -> None:
    """Both detach, for entirely different reasons."""

    ended = LifecycleOutcome(
        monitor=MonitorDisposition.DETACHED_AT_RUN_END, order=_order()
    )
    timed_out = LifecycleOutcome(
        monitor=MonitorDisposition.DETACHED_AFTER_TIMEOUT, order=_order()
    )

    assert ended.describe() != timed_out.describe()
    assert ended.monitor.detached and timed_out.monitor.detached


# --------------------------------------------------------------------------
# The order's fate, read conservatively


def test_an_order_that_was_never_issued_says_so() -> None:
    assert _order(issued=False) is OrderDisposition.NEVER_ISSUED


def test_other_work_present_reads_as_replaced_not_ended() -> None:
    """A Job pulling a character away is not the order finishing."""

    assert _order(retained_task_names={"TASK_EAT"}) is OrderDisposition.EXTERNALLY_REPLACED


def test_nothing_retained_on_fresh_evidence_reads_as_ended() -> None:
    assert _order(retained_task_names=frozenset()) is OrderDisposition.NATURALLY_ENDED


def test_an_unnameable_task_is_unknown_rather_than_assumed_ended() -> None:
    """If the controller cannot say what to look for, absence proves nothing."""

    assert _order(expected_task_name=None) is OrderDisposition.UNKNOWN_AFTER_TELEMETRY_LOSS


def test_stale_evidence_never_concludes_the_order_ended() -> None:
    """The rule that keeps every other rule honest."""

    for retained in (frozenset(), frozenset({MINE}), frozenset({"TASK_EAT"})):
        assert (
            _order(telemetry_fresh=False, retained_task_names=retained)
            is OrderDisposition.UNKNOWN_AFTER_TELEMETRY_LOSS
        )


# --------------------------------------------------------------------------
# The vocabulary cannot collapse back


def test_monitor_and_order_dispositions_share_no_members() -> None:
    """One enum for both is how they got conflated in the first place."""

    assert not {member.value for member in MonitorDisposition} & {
        member.value for member in OrderDisposition
    }


@pytest.mark.parametrize("monitor", list(MonitorDisposition))
@pytest.mark.parametrize("order", list(OrderDisposition))
def test_every_pair_is_representable_and_describable(
    monitor: MonitorDisposition, order: OrderDisposition
) -> None:
    """Orthogonal means every combination, not a curated set of pairs."""

    described = LifecycleOutcome(monitor=monitor, order=order).describe()

    assert described
    assert described.endswith(".")


def test_no_disposition_is_named_cancelled() -> None:
    """The word that meant six things is not available to mean them again."""

    for member in (*MonitorDisposition, *OrderDisposition):
        assert "cancel" not in member.value


# --------------------------------------------------------------------------
# Historical bundles


def test_old_evidence_is_versioned_rather_than_reinterpreted() -> None:
    """A pre-vocabulary `cancelled` cannot be mapped onto a pair.

    It covered all six meanings, so choosing one would be inventing evidence.
    Bundles say which vocabulary they were written with instead.
    """

    assert EVIDENCE_SEMANTICS_VERSION > LEGACY_EVIDENCE_SEMANTICS_VERSION
    assert LifecycleOutcome(
        monitor=MonitorDisposition.OBSERVED_TERMINAL,
        order=OrderDisposition.NATURALLY_ENDED,
    ).evidence_semantics_version == EVIDENCE_SEMANTICS_VERSION


# --------------------------------------------------------------------------
# The audit, as executable claims


def test_no_option_cancel_path_sends_input_to_kenshi() -> None:
    """Detaching a monitor must not clear Jobs or orders as cleanup.

    `option.cancel` cancels an asyncio task. If it ever grew a primitive send,
    stopping a host task would start clearing the character's work as a side
    effect - the exact incidental cleanup this vocabulary exists to rule out.
    """

    import ast
    import inspect

    from kenshi_agent import options as options_module

    tree = ast.parse(inspect.getsource(options_module))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != "cancel":
            continue
        body = ast.dump(node)
        for forbidden in (
            "run_primitives",
            "send_primitive",
            "clearOrders",
            "removeJob",
            "clear_orders",
        ):
            if forbidden in body:
                offenders.append(f"{node.name} line {node.lineno}: {forbidden}")

    assert not offenders, f"an option cancel path sends input: {offenders}"


def test_the_native_release_is_guarded_by_command_ownership() -> None:
    """The only order-clearing in the tree, and it must stay owned.

    `removeJob` + `clearOrders` release a resource task, which is correct only
    because the command issued that task itself. Without the ownership guard it
    would clear work the agent never ordered.
    """

    from pathlib import Path

    semantics = (
        Path(__file__).resolve().parents[1]
        / "native"
        / "KenshiAgentTelemetry"
        / "ResourceProductionSemantics.h"
    ).read_text(encoding="utf-8", errors="replace")

    assert "RESOURCE_TASK_RELEASE_NOT_OWNED" in semantics
    assert "if (!issuedByCommand)" in semantics


def test_the_run_report_states_what_kenshi_still_holds() -> None:
    """Ending a run is not an instruction to anybody.

    A final report that records only that the agent stopped implies the world
    stopped with it.
    """

    from kenshi_agent.run_coordinator import _retained_work_at_exit

    absent = _retained_work_at_exit(None)

    assert absent["orders_at_exit"] is None
    assert "Nothing" in str(absent["orders_at_exit_note"])


def test_the_run_report_names_retained_work_and_its_currency() -> None:
    from kenshi_agent.core.observation import Observation
    from kenshi_agent.core.telemetry import (
        CharacterState,
        CharacterTaskState,
        GameState,
        TaskEntry,
        TelemetrySnapshot,
    )
    from kenshi_agent.run_coordinator import _retained_work_at_exit

    observation = Observation(
        run_id="r",
        step_index=0,
        mode="live",
        telemetry=TelemetrySnapshot(
            sequence=77,
            game=GameState(loaded=True, paused=False),
            squad=[
                CharacterState(
                    id="barth",
                    name="Barth",
                    selected=True,
                    task_state=CharacterTaskState(
                        has_player_orders=True,
                        orders=[TaskEntry(task_name=MINE)],
                        orders_count=1,
                    ),
                )
            ],
        ),
    )

    report = _retained_work_at_exit(observation)

    assert report["orders_at_exit"] == [
        {
            "character": "Barth",
            "orders": 1,
            "jobs": 0,
            "permajobs": 0,
            "current_activity": None,
        }
    ]
    assert report["orders_at_exit_observed_sequence"] == 77
    assert "sent no order-clearing input" in str(report["orders_at_exit_note"])


def test_a_later_order_fact_is_a_linked_event_not_a_receipt_edit() -> None:
    """The exactly-once terminal survives; later facts are appended.

    Editing the receipt would make the record of the terminal depend on how long
    anyone kept watching afterwards.
    """

    import inspect

    from kenshi_agent.execution import monitoring

    source = inspect.getsource(monitoring)

    assert '"order_disposition_observed"' in source
    assert '"command_id": transition.receipt.command_id' in source
    # The receipt itself is never rewritten with a disposition.
    assert "receipt.model_copy" not in source


# --------------------------------------------------------------------------
# Each detaching path declares its own disposition


def test_a_human_handoff_is_not_reported_as_a_supervisor_action() -> None:
    """A person taking the keyboard is a handoff, not the supervisor stopping us.

    Every safety cause used to reach the same generic answer, which is a smaller
    version of the problem this vocabulary exists to fix.
    """

    from kenshi_agent.core.lifecycle import monitor_disposition_for_safety_cause

    assert (
        monitor_disposition_for_safety_cause("human_input")
        is MonitorDisposition.DETACHED_FOR_HUMAN_HANDOFF
    )
    assert (
        monitor_disposition_for_safety_cause("emergency_stop")
        is MonitorDisposition.DETACHED_FOR_HUMAN_HANDOFF
    )


def test_telemetry_causes_report_as_telemetry_loss() -> None:
    """Stale, stalled, and a dead host window all end evidence, not the order."""

    from kenshi_agent.core.lifecycle import monitor_disposition_for_safety_cause

    for cause in ("telemetry_stale", "sequence_stalled", "host_terminal"):
        assert (
            monitor_disposition_for_safety_cause(cause)
            is MonitorDisposition.DETACHED_ON_TELEMETRY_LOSS
        )


def test_every_safety_cause_has_a_disposition() -> None:
    """Enumerated from the real cause vocabulary rather than restated."""

    from kenshi_agent.core.lifecycle import MONITOR_DISPOSITION_BY_SAFETY_CAUSE
    from kenshi_agent.safety_supervisor import SafetyCause

    missing = [
        cause.value
        for cause in SafetyCause
        if cause.value not in MONITOR_DISPOSITION_BY_SAFETY_CAUSE
    ]

    assert not missing, f"safety causes with no declared disposition: {missing}"


def test_an_unknown_cause_degrades_to_vague_rather_than_wrong() -> None:
    """A new cause must not silently claim a handoff or telemetry loss."""

    from kenshi_agent.core.lifecycle import monitor_disposition_for_safety_cause

    assert (
        monitor_disposition_for_safety_cause("a_cause_added_later")
        is MonitorDisposition.DETACHED_BY_SUPERVISOR
    )


def test_run_end_and_shutdown_are_distinguished_in_the_report() -> None:
    """One means the agent finished; the other that it was stopped mid-thought.

    A reader deciding whether to resume wants to know which, and both leave the
    order alone either way.
    """

    import inspect

    from kenshi_agent import run_coordinator

    source = inspect.getsource(run_coordinator)

    assert "MonitorDisposition.DETACHED_AT_RUN_END" in source
    assert "MonitorDisposition.DETACHED_AT_SHUTDOWN" in source
    assert "summary.terminated" in source


def test_preemption_reports_what_kenshi_still_holds() -> None:
    """Preemption is not an instruction to the characters either."""

    import inspect

    from kenshi_agent import run_coordinator

    cancelled = inspect.getsource(run_coordinator)
    block = cancelled[cancelled.index('"plan_execution_cancelled"') :][:1600]

    assert "monitor_disposition" in block
    assert "_retained_work_at_exit" in block
    assert "sent no order-clearing input" in block
