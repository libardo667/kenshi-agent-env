"""Whether each typed action is finished, derived rather than remembered.

Adding one action touches a model, two unions, a contract, a bind function, a
completion owner, an executor branch, generated schemas and generated docs.
Nobody holds that list; it is rediscovered each time, and a piece left out
surfaces later as a plan rejected mid-run or an action the model can author and
the executor cannot perform.

The checklist is derived from the contract's own `ActionExecution`, so it cannot
drift from what the code actually requires: an atomic handler needs an executor
branch, while an option-backed action gets its terminal from the option instead.

The unfinished list is the queue. It is deliberately not a set of stubs that
pass: a generated `derive_completion_conditions` returning an empty tuple would
satisfy every structural check and verify nothing. The affordance-adapter gate
separately forbids an unfinished operation from entering the offered surface.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import StrEnum


class ActionGap(StrEnum):
    """A specific missing piece, named so a queue item is actionable."""

    EXECUTOR_DISPATCH = "executor_dispatch"
    BIND = "bind"
    COMPLETION_TERMINAL = "completion_terminal"


# Actions that exist but are not finished, and exactly what each still needs.
# Removing an entry is how an operation graduates, and the gate then requires
# every derived piece to be real before an adapter may expose it.
SCAFFOLDED_ACTIONS: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class ActionCompleteness:
    kind: str
    execution: str
    gaps: tuple[ActionGap, ...]
    scaffold_reason: str | None

    @property
    def finished(self) -> bool:
        return action_is_finished(self)


def action_is_finished(row: ActionCompleteness) -> bool:
    """Undecorated so mutation tooling can see the decision it makes."""

    return not row.gaps and row.scaffold_reason is None


def _executor_source() -> str:
    from .env import live

    return inspect.getsource(live)


def audit_action_completeness() -> tuple[ActionCompleteness, ...]:
    """Report every contracted action against what its execution kind requires."""

    from .action_contracts import ACTION_CONTRACTS, ActionExecution

    source = _executor_source()
    rows: list[ActionCompleteness] = []
    for kind, contract in sorted(ACTION_CONTRACTS.items()):
        gaps: list[ActionGap] = []
        if contract.bind is None:
            gaps.append(ActionGap.BIND)
        # Only an atomic handler is dispatched by type in the live environment.
        # A monitored or composite option owns its own terminal, so demanding a
        # branch here would invent work that does not exist.
        if contract.execution is ActionExecution.ATOMIC_HANDLER:
            model = contract.model.__name__
            if f"isinstance(action, {model})" not in source:
                gaps.append(ActionGap.EXECUTOR_DISPATCH)
        reason = SCAFFOLDED_ACTIONS.get(kind)
        rows.append(
            ActionCompleteness(
                kind=kind,
                execution=contract.execution.value,
                gaps=tuple(gaps),
                scaffold_reason=reason,
            )
        )
    return tuple(rows)


def unfinished_actions() -> tuple[ActionCompleteness, ...]:
    """The queue: every action that is not yet finished, with what it needs."""

    return tuple(row for row in audit_action_completeness() if not row.finished)


def render_action_queue(rows: tuple[ActionCompleteness, ...]) -> list[str]:
    finished = [row for row in rows if row.finished]
    unfinished = [row for row in rows if not row.finished]
    lines = [
        f"contracted   {len(rows):3d}",
        f"finished     {len(finished):3d}",
        f"unfinished   {len(unfinished):3d}",
        "",
    ]
    if not unfinished:
        lines.append("No unfinished actions.")
        return lines
    lines.append("UNFINISHED — implementation queue")
    for row in unfinished:
        lines.append(f"  {row.kind}  [{row.execution}]")
        if row.scaffold_reason:
            lines.append(f"      still needed: {row.scaffold_reason}")
        for gap in row.gaps:
            lines.append(f"      missing: {gap.value}")
    return lines
