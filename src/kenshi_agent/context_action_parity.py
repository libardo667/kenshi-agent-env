"""Empirical parity for Kenshi's runtime-filtered context-menu orders.

The pinned ``TaskType`` enum is only an upper-bound vocabulary.  Concrete
menu witnesses are the game-derived evidence that a task is actually offered
for a target and current selection.  Reviewed routes stay in a separate
registry: a witness can create an unclassified row, never execution authority.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from .context_action_vocabulary import load_task_types
from .core.telemetry import TelemetrySnapshot

ROOT = Path(__file__).resolve().parents[2]
WITNESSES_PATH = ROOT / "game_sources" / "kenshi" / "context_menu_witnesses.json"
REPORT_NAME = "CONTEXT_ACTION_PARITY.md"
DEFAULT_SCAN_LIMIT = 24
UNRESOLVED_TARGET_KIND = "unresolved"


@dataclass(frozen=True, slots=True)
class ContextMenuWitness:
    run_id: str
    identity_session_id: str
    target_id: str
    target_name: str | None
    target_kind: str
    task_type_values: tuple[int, ...]
    task_type_values_complete: bool
    selected_character_ids: tuple[str, ...]
    reviewed_context_actions: tuple[str, ...]
    reviewed_default_task: str | None


class ContextActionExemptionReason(StrEnum):
    """Why a witnessed game order deliberately has no planner route."""

    NOT_PLAYER_AFFORDANCE = "not_player_affordance"
    SUPERSEDED = "superseded"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class WiredContextAction:
    """A witnessed order with a reviewed semantic execution path."""

    adapter_routes: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class ExemptContextAction:
    """A witnessed order deliberately excluded for one typed reason."""

    reason: ContextActionExemptionReason
    rationale: str


@dataclass(frozen=True, slots=True)
class MissingContextAction:
    """A witnessed player affordance with a grounded implementation queue."""

    queue_description: str


ContextActionDecision = WiredContextAction | ExemptContextAction | MissingContextAction


@dataclass(frozen=True, slots=True)
class ContextActionCoverage:
    target_kind: str
    task_type_value: int
    task_type_name: str
    witness_count: int
    decision: ContextActionDecision | None


# These entries are deliberately narrow. The test suite requires every decision
# to have a concrete witness, so this cannot become a speculative list copied
# from TaskType.h.
CONTEXT_ACTION_DECISIONS: dict[tuple[str, int], ContextActionDecision] = {
    ("natural_resource", 26): WiredContextAction(
        adapter_routes=("native_and_composite:harvest",),
        rationale=(
            "the composite adapter opens the exact resource inventory and "
            "conserves the output transfer as runtime-owned phases"
        ),
    ),
    ("natural_resource", 87): WiredContextAction(
        adapter_routes=(
            "context_orders:operate",
            "native_and_composite:harvest",
        ),
        rationale=(
            "the context adapter owns exact task acceptance and the composite "
            "adapter owns production through conserved output"
        ),
    ),
    ("squad_character", 25): WiredContextAction(
        adapter_routes=("context_orders:first_aid",),
        rationale=(
            "the generic context adapter carries the exact squad target and "
            "first_aid semantic to a stable-ID native route whose terminal "
            "proves FIRST_AID_ORDER with that exact subject"
        ),
    ),
}


def _resolved_target_kind(
    target_kind: str | None,
    *,
    target_id: str,
    selected_character_ids: tuple[str, ...],
) -> str:
    if target_kind and target_kind != UNRESOLVED_TARGET_KIND:
        return target_kind
    if target_id in selected_character_ids:
        return "squad_character"
    return UNRESOLVED_TARGET_KIND


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def _integer_tuple(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, list) or not all(type(item) is int for item in value):
        return None
    return tuple(value)


def _witness_from_evidence(
    evidence: object,
    *,
    run_id: str,
) -> ContextMenuWitness | None:
    if not isinstance(evidence, dict):
        return None
    identity_session_id = evidence.get("identity_session_id")
    target_id = evidence.get("target_id")
    target_name = evidence.get("target_name")
    target_kind = evidence.get("target_kind")
    task_type_values = _integer_tuple(evidence.get("task_type_values"))
    complete = evidence.get("task_type_values_complete")
    selected = _string_tuple(evidence.get("selected_character_ids"))
    reviewed = _string_tuple(evidence.get("reviewed_context_actions"))
    default_task = evidence.get("reviewed_default_task")
    if (
        not isinstance(identity_session_id, str)
        or not isinstance(target_id, str)
        or target_name is not None
        and not isinstance(target_name, str)
        or target_kind is not None
        and not isinstance(target_kind, str)
        or task_type_values is None
        or type(complete) is not bool
        or selected is None
        or reviewed is None
        or default_task is not None
        and not isinstance(default_task, str)
    ):
        return None
    return ContextMenuWitness(
        run_id=run_id,
        identity_session_id=identity_session_id,
        target_id=target_id,
        target_name=target_name,
        target_kind=_resolved_target_kind(
            target_kind,
            target_id=target_id,
            selected_character_ids=selected,
        ),
        task_type_values=task_type_values,
        task_type_values_complete=complete,
        selected_character_ids=selected,
        reviewed_context_actions=reviewed,
        reviewed_default_task=default_task,
    )


def _witness_from_telemetry(
    payload: object,
    *,
    run_id: str,
) -> ContextMenuWitness | None:
    try:
        telemetry = TelemetrySnapshot.model_validate(payload)
    except ValueError:
        return None
    menu = telemetry.ui.context_menu
    if menu is None:
        return None
    # TelemetrySnapshot requires a stable identity whenever a context-menu
    # payload exists; keep that cross-model invariant out of this reader.
    identity_session_id = cast(str, telemetry.identity_session_id)  # pragma: no mutate
    target = next(
        (item for item in telemetry.world_targets if item.id == menu.target_id),
        None,
    )
    selected_character_ids = tuple(telemetry.ui.selected_character_ids)
    return ContextMenuWitness(
        run_id=run_id,
        identity_session_id=identity_session_id,
        target_id=menu.target_id,
        target_name=menu.target_name,
        target_kind=_resolved_target_kind(
            target.kind if target is not None else None,
            target_id=menu.target_id,
            selected_character_ids=selected_character_ids,
        ),
        task_type_values=tuple(menu.task_type_values),
        task_type_values_complete=menu.task_type_values_complete,
        selected_character_ids=selected_character_ids,
        reviewed_context_actions=(
            tuple(action.value for action in target.context_actions)
            if target is not None
            else ()
        ),
        reviewed_default_task=target.default_task if target is not None else None,
    )


def witnesses_from_run(run_dir: Path) -> set[ContextMenuWitness]:
    """Read standard compact events and pre-event telemetry capture artifacts."""

    witnesses: set[ContextMenuWitness] = set()
    events_path = run_dir / "events.jsonl"
    try:
        # Encoding aliases/defaults are platform plumbing, not parity behavior.
        events = events_path.open(encoding="utf-8")  # pragma: no mutate
    except OSError:
        events = None
    if events is not None:
        with events:
            for line in events:
                if "runtime_context_menu_observed" not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("event_type") != "world_state_event":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict) or payload.get("event_type") != (
                    "runtime_context_menu_observed"
                ):
                    continue
                witness = _witness_from_evidence(
                    payload.get("evidence"),
                    run_id=run_dir.name,
                )
                if witness is not None:
                    witnesses.add(witness)

    for telemetry_path in sorted(run_dir.glob("telemetry*.json")):
        try:
            # Encoding aliases/defaults are platform plumbing, not parity behavior.
            payload = json.loads(
                telemetry_path.read_text(encoding="utf-8")  # pragma: no mutate
            )
        except (OSError, ValueError):
            continue
        witness = _witness_from_telemetry(payload, run_id=run_dir.name)
        if witness is not None:
            witnesses.add(witness)
    return witnesses


def newest_run_directories(runs_dir: Path, limit: int = DEFAULT_SCAN_LIMIT) -> list[Path]:
    if not runs_dir.is_dir():
        return []
    candidates = [
        child
        for child in runs_dir.iterdir()
        if child.is_dir()
        and (
            (child / "events.jsonl").is_file()
            or any(child.glob("telemetry*.json"))
        )
    ]
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return candidates[:limit]


def witnesses_from_runs(
    runs_dir: Path,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> set[ContextMenuWitness]:
    return {
        witness
        for run_dir in newest_run_directories(runs_dir, limit)
        for witness in witnesses_from_run(run_dir)
    }


def load_witnesses(path: Path) -> set[ContextMenuWitness]:
    try:
        # Encoding aliases/defaults are platform plumbing, not parity behavior.
        payload = json.loads(path.read_text(encoding="utf-8"))  # pragma: no mutate
    except OSError:
        return set()
    if not isinstance(payload, dict) or not isinstance(payload.get("witnesses"), list):
        raise ValueError("context-menu witness file has an invalid envelope")
    return {
        ContextMenuWitness(
            run_id=item["run_id"],
            identity_session_id=item["identity_session_id"],
            target_id=item["target_id"],
            target_name=item["target_name"],
            target_kind=_resolved_target_kind(
                item["target_kind"],
                target_id=item["target_id"],
                selected_character_ids=tuple(item["selected_character_ids"]),
            ),
            task_type_values=tuple(item["task_type_values"]),
            task_type_values_complete=item["task_type_values_complete"],
            selected_character_ids=tuple(item["selected_character_ids"]),
            reviewed_context_actions=tuple(item["reviewed_context_actions"]),
            reviewed_default_task=item["reviewed_default_task"],
        )
        for item in payload["witnesses"]
        if isinstance(item, dict)
    }


def write_witnesses(path: Path, witnesses: set[ContextMenuWitness]) -> Path:
    ordered = sorted(
        witnesses,
        key=lambda item: (
            item.run_id,
            item.identity_session_id,
            item.target_id,
            item.task_type_values,
            item.selected_character_ids,
        ),
    )
    payload = {
        "generated_by": "scripts/export_context_action_witnesses.py",
        "schema_version": 1,
        "witnesses": [asdict(witness) for witness in ordered],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2) + "\n"
    # Encoding aliases/defaults are platform plumbing, not parity behavior.
    path.write_text(rendered, encoding="utf-8")  # pragma: no mutate
    return path


def classify_witnesses(
    witnesses: set[ContextMenuWitness],
) -> tuple[ContextActionCoverage, ...]:
    task_names = {entry.value: entry.name for entry in load_task_types().entries}
    counts: dict[tuple[str, int], int] = {}
    for witness in witnesses:
        for task_type_value in set(witness.task_type_values):
            key = (witness.target_kind, task_type_value)
            counts[key] = counts.get(key, 0) + 1
    return tuple(
        ContextActionCoverage(
            target_kind=target_kind,
            task_type_value=task_type_value,
            task_type_name=task_names.get(task_type_value, f"UNKNOWN_{task_type_value}"),
            witness_count=count,
            decision=CONTEXT_ACTION_DECISIONS.get((target_kind, task_type_value)),
        )
        for (target_kind, task_type_value), count in sorted(counts.items())
    )


def render_context_action_parity(witnesses: set[ContextMenuWitness]) -> str:
    coverage = classify_witnesses(witnesses)
    wired = sum(isinstance(row.decision, WiredContextAction) for row in coverage)
    exempt = sum(isinstance(row.decision, ExemptContextAction) for row in coverage)
    missing = sum(isinstance(row.decision, MissingContextAction) for row in coverage)
    unclassified = sum(row.decision is None for row in coverage)
    lines = [
        "<!-- generated by scripts/export_docs.py; edits are overwritten -->",
        "",
        "# Empirical context-action parity",
        "",
        "Concrete orders captured from Kenshi's runtime-filtered context menu.",
        "Observation never grants execution authority. Every witnessed pair must",
        "be wired to a reviewed semantic route, typed-exempt, or recorded as a",
        "grounded missing affordance. Decisions without a witness fail the suite.",
        "",
        "```text",
        f"menu witnesses   {len(witnesses)}",
        f"witnessed pairs  {len(coverage)}",
        f"wired            {wired}",
        f"exempt           {exempt}",
        f"missing          {missing}",
        f"unclassified     {unclassified}",
        "```",
        "",
        "| target kind | Kenshi task | value | captures | semantic route |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in coverage:
        decision = row.decision
        if isinstance(decision, WiredContextAction):
            rendered_decision = "wired: " + " -> ".join(decision.adapter_routes)
        elif isinstance(decision, ExemptContextAction):
            rendered_decision = (
                f"exempt[{decision.reason.value}]: {decision.rationale}"
            )
        elif isinstance(decision, MissingContextAction):
            rendered_decision = f"**MISSING**: {decision.queue_description}"
        else:
            rendered_decision = "**UNCLASSIFIED**"
        lines.append(
            f"| {row.target_kind} | `{row.task_type_name}` | "
            f"{row.task_type_value} | {row.witness_count} | {rendered_decision} |"
        )
    lines.append("")
    return "\n".join(lines)
