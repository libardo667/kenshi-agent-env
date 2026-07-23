from __future__ import annotations

import sys
from datetime import datetime
from typing import TextIO

from .models import Action, ActionReceipt, ControlMode, PlannerDecision, SkillAction


def format_action(action: Action) -> str:
    if isinstance(action, SkillAction):
        arguments = ", ".join(
            f"{name}={value!r}" for name, value in action.argument_map().items()
        )
        return f"{action.name}({arguments})" if arguments else f"{action.name}()"
    values = action.model_dump(mode="json", exclude={"kind"})
    arguments = ", ".join(f"{name}={value!r}" for name, value in values.items())
    return f"{action.kind}({arguments})" if arguments else f"{action.kind}()"


class ConsoleDecisionReporter:
    """Human-readable, immediately flushed stream of the agent's visible reasoning."""

    def __init__(
        self,
        *,
        run_id: str,
        planner_name: str,
        model_name: str | None,
        control_mode: ControlMode = ControlMode.INTERFACE_ONLY,
        stream: TextIO | None = None,
    ) -> None:
        self.run_id = run_id
        self.planner_name = planner_name
        self.model_name = model_name
        self.control_mode = control_mode
        self.stream = stream or sys.stdout

    def run_started(self, max_steps: int) -> None:
        model = f" | {self.model_name}" if self.model_name else ""
        self._write(
            f"Kenshi Agent | {self.planner_name}{model} | {max_steps} turns | "
            f"control={self.control_mode.value}\n"
            f"Run {self.run_id}\n"
        )

    def planning_started(self, step_index: int) -> None:
        self._write(f"[{self._clock()}] step {step_index:02d}  OBSERVE -> thinking...\n")

    def decision(
        self,
        *,
        step_index: int,
        source: str,
        decision: PlannerDecision,
        latency_seconds: float,
    ) -> None:
        self._write(
            f"[{self._clock()}] step {step_index:02d}  DECIDE  "
            f"{latency_seconds:.2f}s | {source}\n"
            f"  Intent  {decision.intent}\n"
            f"  Why     {decision.rationale}\n"
            f"  Action  {format_action(decision.action)}\n"
            f"  Conf    {decision.confidence:.0%}\n"
        )

    def action_receipt(self, *, step_index: int, receipt: ActionReceipt) -> None:
        duration = (receipt.finished_at - receipt.started_at).total_seconds()
        status = "DONE" if receipt.accepted and not receipt.error_type else "FAILED"
        detail = receipt.message or "Action completed."
        self._write(
            f"[{self._clock()}] step {step_index:02d}  {status:<6}  "
            f"{duration:.2f}s | {detail}\n\n"
        )

    def error(self, *, step_index: int, label: str, message: str) -> None:
        self._write(f"[{self._clock()}] step {step_index:02d}  {label} | {message}\n\n")

    def run_finished(self, *, steps_completed: int, stop_reason: str) -> None:
        self._write(
            f"Kenshi Agent finished | {steps_completed} turns | {stop_reason}\n"
        )

    def _write(self, value: str) -> None:
        self.stream.write(value)
        self.stream.flush()

    @staticmethod
    def _clock() -> str:
        return datetime.now().astimezone().strftime("%H:%M:%S")
