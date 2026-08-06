"""What became of the monitor, and what became of the order, said separately.

Python's foreground interest in an order and the order Kenshi actually holds are
different things with different lifetimes. A step timeout, a strategic replan, a
human taking the keyboard, the run ending, the process stopping, or telemetry
going quiet all end the first. None of them touch the second: the character goes
on mining.

Every one of those collapsed into `CANCELLED`, which reads as "the order was
cancelled" and is almost always false. The audit that produced this module found
no Python path that sends order-clearing input at all - `option.cancel` cancels
an asyncio task and nothing else, and the single native release is guarded by
`RESOURCE_TASK_RELEASE_NOT_OWNED` unless the command issued the task itself. So
the orders were never being cleared; the report just said they were, and a
post-mortem reading "cancelled" had no way to learn otherwise.

The two dispositions are orthogonal on purpose. Do not add a combined member
like `TIMED_OUT_BUT_RETAINED`: the point is that each question is answered from
its own evidence, and the order's answer is often simply unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Bundles written before this vocabulary existed recorded a single `cancelled`
# status covering all of the below. Reading them as any particular pair would be
# inventing evidence, so they are reported as the version they are rather than
# reinterpreted. Bump when a member's *meaning* changes, not when one is added.
EVIDENCE_SEMANTICS_VERSION = 2

# What version 1 could express: one status, no separation.
LEGACY_EVIDENCE_SEMANTICS_VERSION = 1


class MonitorDisposition(StrEnum):
    """What became of Python's foreground interest in the operation."""

    OBSERVED_TERMINAL = "observed_terminal"
    """The monitor watched the operation reach its own terminal."""

    DETACHED_AFTER_TIMEOUT = "detached_after_timeout"
    """The step's monitoring budget ran out. The order was not touched."""

    DETACHED_FOR_REPLAN = "detached_for_replan"
    """A strategic revision superseded the plan that was watching."""

    DETACHED_FOR_HUMAN_HANDOFF = "detached_for_human_handoff"
    """A person took the input, so the agent stopped watching on purpose."""

    DETACHED_AT_RUN_END = "detached_at_run_end"
    """The run reached its step ceiling or objective while work was in flight."""

    DETACHED_AT_SHUTDOWN = "detached_at_shutdown"
    """The process stopped. Nothing was told to Kenshi on the way out."""

    DETACHED_ON_TELEMETRY_LOSS = "detached_on_telemetry_loss"
    """Evidence stopped arriving, so monitoring could no longer mean anything."""

    DETACHED_BY_SUPERVISOR = "detached_by_supervisor"
    """Independent safety supervision preempted the operation."""

    @property
    def detached(self) -> bool:
        """Whether Python stopped watching without seeing a terminal."""

        return self is not MonitorDisposition.OBSERVED_TERMINAL


class OrderDisposition(StrEnum):
    """What became of the order Kenshi holds for the character."""

    NEVER_ISSUED = "never_issued"
    """No order reached the game, so there is nothing to have become of."""

    RETAINED_AT_LAST_OBSERVATION = "retained_at_last_observation"
    """The character still held the order the last time evidence arrived.

    Not "still holds it" - the claim is exactly as old as the evidence, and
    saying more would be inventing currency the bundle does not have.
    """

    UNKNOWN_AFTER_TELEMETRY_LOSS = "unknown_after_telemetry_loss"
    """Evidence stopped before the order's fate was observed.

    The honest terminal for a detached monitor with no fresh telemetry, and the
    one the old vocabulary could not say at all.
    """

    EXPLICITLY_CLEARED = "explicitly_cleared"
    """The controller cleared an order it had issued and owned."""

    EXTERNALLY_REPLACED = "externally_replaced"
    """Something else - the game's AI, a Job, a person - replaced it."""

    NATURALLY_ENDED = "naturally_ended"
    """The character finished the work, or it ceased to be possible."""

    @property
    def order_may_still_be_running(self) -> bool:
        """Whether a later reader should expect the character to still be busy.

        Deliberately true for the unknown case. A caller deciding whether to
        re-issue work should treat "we do not know" like "possibly still
        running", because the expensive mistake is ordering the same character
        twice, not checking once too often.
        """

        return self in {
            OrderDisposition.RETAINED_AT_LAST_OBSERVATION,
            OrderDisposition.UNKNOWN_AFTER_TELEMETRY_LOSS,
        }


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """One operation's two answers, with the evidence they rest on."""

    monitor: MonitorDisposition
    order: OrderDisposition
    detail: str = ""
    # The telemetry sequence the order claim was read from. None when no
    # evidence supported it, which is itself worth recording.
    observed_at_sequence: int | None = None
    evidence_semantics_version: int = EVIDENCE_SEMANTICS_VERSION

    def describe(self) -> str:
        """One sentence a post-mortem can act on.

        Says both things, and says which is a guess.
        """

        monitor_text = {
            MonitorDisposition.OBSERVED_TERMINAL: "the monitor saw this through",
            MonitorDisposition.DETACHED_AFTER_TIMEOUT: (
                "the monitor detached after its step timeout"
            ),
            MonitorDisposition.DETACHED_FOR_REPLAN: "the monitor detached to replan",
            MonitorDisposition.DETACHED_FOR_HUMAN_HANDOFF: (
                "the monitor detached for a human handoff"
            ),
            MonitorDisposition.DETACHED_AT_RUN_END: "the monitor detached at run end",
            MonitorDisposition.DETACHED_AT_SHUTDOWN: "the monitor detached at shutdown",
            MonitorDisposition.DETACHED_ON_TELEMETRY_LOSS: (
                "the monitor detached when telemetry stopped"
            ),
            MonitorDisposition.DETACHED_BY_SUPERVISOR: (
                "safety supervision detached the monitor"
            ),
        }[self.monitor]
        order_text = {
            OrderDisposition.NEVER_ISSUED: "no order ever reached the game",
            OrderDisposition.RETAINED_AT_LAST_OBSERVATION: (
                "Kenshi still held the order at the last observation"
            ),
            OrderDisposition.UNKNOWN_AFTER_TELEMETRY_LOSS: (
                "what Kenshi did with the order is unknown"
            ),
            OrderDisposition.EXPLICITLY_CLEARED: "the controller cleared its own order",
            OrderDisposition.EXTERNALLY_REPLACED: "something else replaced the order",
            OrderDisposition.NATURALLY_ENDED: "the order ended on its own",
        }[self.order]
        sequence = (
            f" (as of telemetry {self.observed_at_sequence})"
            if self.observed_at_sequence is not None
            else " (no supporting evidence)"
        )
        detail = f" {self.detail}" if self.detail else ""
        return f"{monitor_text}; {order_text}{sequence}.{detail}"


def order_disposition_from_evidence(
    *,
    issued: bool,
    telemetry_fresh: bool,
    retained_task_names: frozenset[str] | set[str],
    expected_task_name: str | None,
) -> OrderDisposition:
    """Read the order's fate from what the last observation actually showed.

    The rules are deliberately conservative. Absence of a retained order is only
    evidence that it ended if the evidence itself is current; otherwise the
    honest answer is that nobody knows.
    """

    if not issued:
        return OrderDisposition.NEVER_ISSUED
    if not telemetry_fresh:
        return OrderDisposition.UNKNOWN_AFTER_TELEMETRY_LOSS
    if expected_task_name is None:
        # Something was issued but the controller cannot name what to look for,
        # so a retained-work list cannot confirm or deny it.
        return OrderDisposition.UNKNOWN_AFTER_TELEMETRY_LOSS
    if expected_task_name in retained_task_names:
        return OrderDisposition.RETAINED_AT_LAST_OBSERVATION
    if retained_task_names:
        # The character is holding work, but not the work this operation
        # ordered. Somebody else - a Job, the AI, a person - is driving.
        return OrderDisposition.EXTERNALLY_REPLACED
    return OrderDisposition.NATURALLY_ENDED


# Why the monitor detached, for each way a run can stop watching. A supervisor
# preemption is not one event: a human taking the keyboard, telemetry going
# quiet, and the host window dying end monitoring for different reasons and
# leave the order in different states of knownness. Collapsing them into
# "detached by supervisor" would rebuild a smaller version of the problem this
# vocabulary exists to fix.
MONITOR_DISPOSITION_BY_SAFETY_CAUSE: dict[str, MonitorDisposition] = {
    "human_input": MonitorDisposition.DETACHED_FOR_HUMAN_HANDOFF,
    "emergency_stop": MonitorDisposition.DETACHED_FOR_HUMAN_HANDOFF,
    "telemetry_stale": MonitorDisposition.DETACHED_ON_TELEMETRY_LOSS,
    "sequence_stalled": MonitorDisposition.DETACHED_ON_TELEMETRY_LOSS,
    # The game window died. Telemetry is frozen rather than merely late, so
    # nothing further about the order can be learned.
    "host_terminal": MonitorDisposition.DETACHED_ON_TELEMETRY_LOSS,
    "reflex": MonitorDisposition.DETACHED_BY_SUPERVISOR,
    "pause_capability_withdrawn": MonitorDisposition.DETACHED_BY_SUPERVISOR,
    "unexpected_unpause": MonitorDisposition.DETACHED_BY_SUPERVISOR,
}


def monitor_disposition_for_safety_cause(cause: str) -> MonitorDisposition:
    """The disposition a safety preemption actually represents.

    Unknown causes fall to the supervisor default rather than raising: a new
    cause should degrade to a true-but-vague answer, never to a wrong one.
    """

    return MONITOR_DISPOSITION_BY_SAFETY_CAUSE.get(
        cause, MonitorDisposition.DETACHED_BY_SUPERVISOR
    )
