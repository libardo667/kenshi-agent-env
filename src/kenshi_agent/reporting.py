from __future__ import annotations

import sys
from datetime import datetime
from typing import TextIO, cast

from .control_ownership import ControlOwnershipEvent, ControlOwnershipEventType
from .models import (
    GAME_SPEED_MULTIPLIER_BY_GEAR,
    Action,
    ActionReceipt,
    CameraRecoveryStatus,
    ControlMode,
    PlannerDecision,
    PurchaseStatus,
    ResourceHarvestStatus,
    ResourceTransferStatus,
    SaleStatus,
    SkillAction,
)
from .speech import SpeechNarrator


def format_action(action: Action) -> str:
    if isinstance(action, SkillAction):
        arguments = ", ".join(
            f"{name}={value!r}" for name, value in action.argument_map().items()
        )
        return f"{action.name}({arguments})" if arguments else f"{action.name}()"
    values = action.model_dump(mode="json", exclude={"kind"})
    arguments = ", ".join(f"{name}={value!r}" for name, value in values.items())
    return f"{action.kind}({arguments})" if arguments else f"{action.kind}()"


# Only the planner writes for a listener. Reflex and supervisor decisions carry
# runtime diagnostics in the same field - one live run narrated "the monitored
# native option did not reach its terminal success ... 'cancelled':
# movement_stalled" aloud - so their reasoning is summarised, never read out.
MODEL_AUTHORED_DECISION_SOURCES = frozenset({"planner"})

# Reasoning is the point of listening, so it gets room that a status line does
# not. Clipping a thought at 150 characters produces a fragment ending in "…".
MAX_SPOKEN_REASONING_CHARS = 400

_RUNTIME_DECISION_SUMMARIES: tuple[tuple[str, str], ...] = (
    ("human input", "You've taken over, so I'm standing down."),
    ("hostile", "Something hostile is close, so I'm stopping."),
    ("stale", "I've lost a reliable view of the game, so I'm pausing."),
    ("terminal handoff", "That move ended without finishing, so I'm taking stock."),
    ("pause", "I'm making sure the game is safely paused."),
)


def _spoken_decision(source: str, decision: PlannerDecision) -> str:
    """What to read aloud for one decision, by who actually wrote it."""

    # `action_started` is only wired on the continuous path, so the single-step
    # path would otherwise never say what it is about to do.
    action = describe_action(decision.action)
    if source in MODEL_AUTHORED_DECISION_SOURCES:
        return " ".join(
            (
                _spoken_sentence(decision.intent, max_chars=MAX_SPOKEN_REASONING_CHARS),
                _spoken_sentence(decision.rationale, max_chars=MAX_SPOKEN_REASONING_CHARS),
                action,
            )
        )
    haystack = f"{decision.intent} {decision.rationale}".lower()
    for marker, sentence in _RUNTIME_DECISION_SUMMARIES:
        if marker in haystack:
            return f"{sentence} {action}"
    return f"I'm handling something the game did. {action}"


def _spoken_sentence(value: object, *, max_chars: int = 150) -> str:
    text = " ".join(str(value).split()).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip(" ,;:-") + "…"
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def describe_action(action: Action) -> str:
    """Render planner actions for a listener without exposing binding machinery."""

    kind = action.kind
    values = action.model_dump(mode="python")
    if kind == "noop":
        return "Waiting for the next useful change."
    if kind == "stop":
        return "Stopping the run."
    if kind == "pause":
        return "Pausing the game." if values.get("paused", True) else "Starting the game."
    if kind == "set_speed":
        multiplier = GAME_SPEED_MULTIPLIER_BY_GEAR[cast(int, values["speed"])]
        words = {1.0: "normal speed", 3.0: "three times", 5.0: "five times"}
        return f"Setting the game speed to {words[multiplier]}."
    if kind == "wait":
        return f"Waiting {cast(float, values['seconds']):g} seconds."
    if kind == "consult_advisor":
        return "Thinking through a strategy question."
    if kind == "recall_memory":
        return "Checking what I remember."
    if kind == "read_fieldbook":
        return "Checking my journal."
    if kind == "approach_dialogue_target":
        return "Starting a conversation."
    if kind == "perform_context_action":
        return "Using the selected world object."
    if kind == "produce_resource_output":
        return "Waiting for the resource to produce an item."
    if kind == "harvest_resource":
        return f"Harvesting {cast(int, values['quantity'])} units of resources."
    if kind == "open_context_inventory":
        return "Opening the resource inventory."
    if kind == "move_in_direction":
        return _spoken_sentence(
            "Moving with this goal: " + cast(str, values["expected_effect"])
        )
    if kind == "travel_to_map_destination":
        return "Traveling to the selected map destination."
    if kind == "exit_current_building":
        return "Leaving the building."
    if kind == "move_to_character":
        return "Walking toward that person."
    if kind == "purchase_item":
        quantity = cast(int, values["quantity"])
        item_name = cast(str, values["item_name"])
        return _spoken_sentence(
            f"Buying {quantity} {item_name}"
            if quantity > 1
            else f"Buying {item_name}"
        )
    if kind == "dismiss_screen":
        return "Closing the current screen."
    if kind == "activate_visible_control":
        return _spoken_sentence("Choosing " + cast(str, values["exact_label"]))
    if kind == "equip_item":
        return _spoken_sentence("Equipping " + cast(str, values["item_name"]))
    if kind == "sell_item":
        quantity = cast(int, values["quantity"])
        item_name = cast(str, values["item_name"])
        return _spoken_sentence(
            f"Selling {quantity} {item_name}"
            if quantity > 1
            else f"Selling {item_name}"
        )
    if kind == "collect_resource_output":
        return _spoken_sentence(
            "Collecting " + cast(str, values["item_name"])
        )
    if kind == "scroll_screen":
        return "Looking through more of the current screen."
    if kind == "recover_camera_view":
        return "Fixing the camera view."
    if kind == "use_game_binding":
        return _spoken_sentence(cast(str, values["expected_effect"]))
    if isinstance(action, SkillAction):
        friendly_skills = {
            "move_visible_terrain": "Moving through the visible terrain.",
        }
        if action.name in friendly_skills:
            return friendly_skills[action.name]
        return _spoken_sentence("Using " + action.name.replace("_", " "))
    return _spoken_sentence(kind.replace("_", " "))


def describe_receipt(receipt: ActionReceipt) -> str:
    """Reduce a technical receipt to the result a human player would care about."""

    if not receipt.accepted or receipt.error_type:
        return "That didn't work. I'm reconsidering."
    if not receipt.executed and receipt.dry_run:
        return "I didn't take that action."
    semantic = receipt.semantic
    if semantic is not None and semantic.camera_recovery is not None:
        status = semantic.camera_recovery.status
        if status is CameraRecoveryStatus.ALREADY_CLEAR:
            return "The camera view was already clear."
        if status is CameraRecoveryStatus.RECOVERED:
            return "The camera view is clear again."
        return "I couldn't get a clear camera view."
    if semantic is not None and semantic.purchase is not None:
        purchase = semantic.purchase
        if purchase.status is PurchaseStatus.PURCHASED:
            return (
                f"Bought {purchase.purchased_quantity} "
                f"{purchase.item_name}."
            )
        if purchase.status is PurchaseStatus.PARTIALLY_PURCHASED:
            return (
                f"Bought {purchase.purchased_quantity} of "
                f"{purchase.requested_quantity} {purchase.item_name}."
            )
        if purchase.status is PurchaseStatus.NOT_PURCHASED:
            return "Nothing was purchased."
        return "I couldn't verify the last purchase."
    if semantic is not None and semantic.sale is not None:
        sale = semantic.sale
        if sale.status is SaleStatus.SOLD:
            return f"Sold {sale.sold_quantity} {sale.item_name}."
        if sale.status is SaleStatus.PARTIALLY_SOLD:
            return (
                f"Sold {sale.sold_quantity} of "
                f"{sale.requested_quantity} {sale.item_name}."
            )
        if sale.status is SaleStatus.NOT_SOLD:
            return "Nothing was sold."
        return "I couldn't verify the last sale."
    if semantic is not None and semantic.resource_harvest is not None:
        harvest = semantic.resource_harvest
        if harvest.status is ResourceHarvestStatus.HARVESTED:
            item = f" {harvest.item_name}" if harvest.item_name else ""
            return (
                f"Harvested {harvest.transferred_quantity}{item} "
                "and packed it away."
            )
        return "The harvest didn't produce a usable item."
    if semantic is not None and semantic.resource_transfer is not None:
        transfer = semantic.resource_transfer
        if transfer.status is ResourceTransferStatus.TRANSFERRED:
            return _spoken_sentence("Collected " + transfer.item_name)
        return "The item wasn't transferred."
    acknowledgement = receipt.native_acknowledgement
    if acknowledgement is not None:
        native_results = {
            "map_destination_reached": "I reached the map destination.",
            "walk_destination_reached": "I reached the walking destination.",
            "dialogue_started": "The conversation has started.",
            "context_task_started": "The task has started.",
        }
        if acknowledgement.reason in native_results:
            return native_results[acknowledgement.reason]
    if receipt.advisor is not None:
        return "I have some new advice to consider."
    if receipt.action.kind == "stop":
        return "The run is stopping."
    if receipt.action.kind == "pause":
        return (
            "The game is paused."
            if getattr(receipt.action, "paused", True)
            else "The game is running."
        )
    return "Done."


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
        narrator: SpeechNarrator | None = None,
    ) -> None:
        self.run_id = run_id
        self.planner_name = planner_name
        self.model_name = model_name
        self.control_mode = control_mode
        self.stream = stream or sys.stdout
        self.narrator = narrator
        self._last_spoken_planning_step: int | None = None

    def run_started(self, max_steps: int) -> None:
        model = f" | {self.model_name}" if self.model_name else ""
        self._write(
            f"Kenshi Agent | {self.planner_name}{model} | {max_steps} turns | "
            f"control={self.control_mode.value}\n"
            f"Run {self.run_id}\n"
        )
        self._say("Kenshi Agent is ready.", key="run")

    def planning_started(self, step_index: int) -> None:
        self._write(f"[{self._clock()}] step {step_index:02d}  OBSERVE -> thinking...\n")
        if step_index != self._last_spoken_planning_step:
            self._last_spoken_planning_step = step_index
            self._say("I'm thinking about what to do next.", key="state")

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
        self._say(_spoken_decision(source, decision), key="decision")

    def plan_accepted(
        self,
        *,
        step_index: int,
        objective: str,
        latency_seconds: float,
    ) -> None:
        self._write(
            f"[{self._clock()}] step {step_index:02d}  PLAN    "
            f"{latency_seconds:.2f}s | {objective}\n"
        )
        self._say(
            _spoken_sentence("My plan is to " + objective[0].lower() + objective[1:]),
            key="decision",
        )

    def action_started(self, step_index: int, action: Action) -> None:
        self._write(
            f"[{self._clock()}] step {step_index:02d}  ACT     "
            f"{format_action(action)}\n"
        )
        self._say(describe_action(action), key="action")

    def action_receipt(self, *, step_index: int, receipt: ActionReceipt) -> None:
        duration = (receipt.finished_at - receipt.started_at).total_seconds()
        status = "DONE" if receipt.accepted and not receipt.error_type else "FAILED"
        detail = receipt.message or "Action completed."
        self._write(
            f"[{self._clock()}] step {step_index:02d}  {status:<6}  "
            f"{duration:.2f}s | {detail}\n\n"
        )
        self._say(describe_receipt(receipt), key="result")

    def error(self, *, step_index: int, label: str, message: str) -> None:
        self._write(f"[{self._clock()}] step {step_index:02d}  {label} | {message}\n\n")
        self._say("That didn't work. I'm reconsidering.", key="result")

    def control_ownership(self, event: ControlOwnershipEvent) -> None:
        if event.event_type is ControlOwnershipEventType.COUNTDOWN:
            self._write(
                f"\n*** AGENT TAKEOVER IN {event.seconds_remaining}s ***\n"
                "Move the mouse or press a key to keep human control. "
                "Press F12 to disarm takeover.\n"
            )
            self._say(
                f"Agent takeover in {event.seconds_remaining} seconds.",
                key="control",
            )
            return
        self._write(
            f"\n*** CONTROL {event.state.value.upper()} ***\n"
            f"{event.reason}\n"
        )
        if event.event_type is ControlOwnershipEventType.READY:
            self._say("Agent control is active.", key="control")
        else:
            self._say("Human control is active.", key="control")

    def run_finished(self, *, steps_completed: int, stop_reason: str) -> None:
        self._write(
            f"Kenshi Agent finished | {steps_completed} turns | {stop_reason}\n"
        )
        self._say("The run is finished.", key="run")

    def close(self) -> None:
        if self.narrator is not None:
            self.narrator.close()

    def _say(self, value: str, *, key: str) -> None:
        if self.narrator is not None:
            self.narrator.say(value, key=key)

    def _write(self, value: str) -> None:
        self.stream.write(value)
        self.stream.flush()

    @staticmethod
    def _clock() -> str:
        return datetime.now().astimezone().strftime("%H:%M:%S")
