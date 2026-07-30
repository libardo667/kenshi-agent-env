"""The action queue is derived, so it cannot be forgotten or overstated."""

from __future__ import annotations

from kenshi_agent.action_completeness import (
    SCAFFOLDED_ACTIONS,
    ActionGap,
    audit_action_completeness,
    render_action_queue,
    unfinished_actions,
)
from kenshi_agent.action_contracts import ACTION_CONTRACTS


def test_every_contracted_action_is_finished_or_declared_unfinished() -> None:
    """The failure message is the queue.

    Adding an action touches a model, two unions, a contract, a bind, a
    completion owner, an executor branch, and two generated artefacts. Nobody
    holds that list, so a missing piece surfaced later as a plan rejected
    mid-run. Here it surfaces as a named gap, in a file, before the run.
    """

    queue = unfinished_actions()
    undeclared = [
        row
        for row in queue
        if row.scaffold_reason is None
    ]

    assert not undeclared, "\n".join(
        ["actions missing a piece with no scaffold entry:"]
        + [f"  {row.kind}: {[gap.value for gap in row.gaps]}" for row in undeclared]
    )


def test_an_unfinished_action_is_never_planner_visible() -> None:
    """A half-built action the model can author is worse than no action.

    An `open_screen` the planner could name while the executor could not perform
    it would fail at dispatch, mid-run, having already spent the turn.
    """

    for row in audit_action_completeness():
        if row.scaffold_reason is not None:
            assert ActionGap.PLANNER_VISIBILITY not in row.gaps, (
                f"{row.kind} is scaffolded but planner-visible"
            )
            assert not row.planner_visible


def test_scaffold_entries_name_an_action_that_exists_and_say_what_is_missing() -> None:
    stale = sorted(set(SCAFFOLDED_ACTIONS) - set(ACTION_CONTRACTS))
    assert not stale, f"scaffold entries for actions with no contract: {stale}"
    for kind, reason in SCAFFOLDED_ACTIONS.items():
        assert reason.strip(), f"{kind} is scaffolded with no stated gap"


def test_the_checklist_follows_the_contract_rather_than_a_fixed_list() -> None:
    """Option-backed actions must not be asked for an executor branch.

    A first draft of this check demanded `derive_completion_conditions` from
    every action and produced fourteen false queue items, including
    `harvest_resource` and `purchase_item`, whose completion is owned by a
    composite option. A queue is only as honest as the rule that builds it.
    """

    rows = {row.kind: row for row in audit_action_completeness()}
    assert rows["harvest_resource"].execution == "composite_option"
    assert ActionGap.EXECUTOR_DISPATCH not in rows["harvest_resource"].gaps
    assert rows["use_game_binding"].execution == "atomic_handler"


def test_the_queue_renders_for_a_reader() -> None:
    lines = render_action_queue(audit_action_completeness())
    assert any(line.startswith("contracted") for line in lines)
