"""Pin every current session logger event to one reviewed EvoGen disposition."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from kenshi_agent.tooling.session_event_dispositions import (
    GENERATED_PATH,
    REVIEWED_PATH,
    SOURCE_ROOT,
    SessionEventDispositionError,
    discover_source_inventory,
    load_reviewed_dispositions,
    render_generated_dispositions,
    validate_reviewed_dispositions,
)

EXPECTED_EVENTS = frozenset(
    {
        "action_outcome",
        "action_receipt",
        "action_rejected",
        "advisor_cancelled",
        "advisor_completed",
        "advisor_failed",
        "advisor_queued",
        "advisor_request_queued",
        "advisor_requested",
        "advisor_result",
        "advisor_suppressed",
        "advisor_task_failed",
        "affordance_receipt",
        "affordance_set",
        "agent_takeover_cancelled",
        "agent_takeover_countdown",
        "agent_takeover_ready",
        "campaign_scope",
        "concurrent_planner_discarded",
        "continuity_receipt",
        "continuity_store_failed",
        "control_handback_resume_failed",
        "control_handback_resumed",
        "control_ownership_changed",
        "decision",
        "fieldbook_read",
        "fieldbook_read_completed",
        "fieldbook_receipt",
        "input_boundary_rejected",
        "input_boundary_revalidated",
        "memory_read",
        "memory_read_completed",
        "observation",
        "observation_rejected",
        "operation_handler_error",
        "option_cancelled",
        "option_failed",
        "option_interrupted",
        "option_prepared",
        "option_progress",
        "option_started",
        "option_succeeded",
        "order_disposition_observed",
        "plan_aborted",
        "plan_accepted",
        "plan_budget_committed",
        "plan_budget_released",
        "plan_budget_reserved",
        "plan_completed",
        "plan_execution_cancelled",
        "plan_interrupt_staged",
        "plan_outcome",
        "plan_patch_rejected",
        "plan_patch_requested",
        "plan_patch_staged",
        "plan_patched",
        "plan_proposed",
        "plan_rebased",
        "plan_rejected",
        "plan_started",
        "plan_step_cancelled",
        "plan_step_failed",
        "plan_step_interrupted",
        "plan_step_progress",
        "plan_step_ready",
        "plan_step_started",
        "plan_step_succeeded",
        "planner_context_prepared",
        "planner_error",
        "planner_non_progress",
        "planner_output_rejected",
        "planner_transport",
        "replan_stalled",
        "run_finished",
        "run_finished_safety",
        "run_started",
        "safety_cleanup_completed",
        "safety_cleanup_failed",
        "safety_cleanup_started",
        "safety_pause_already_confirmed",
        "safety_preempted",
        "safety_supervisor_finished",
        "safety_supervisor_preempted",
        "safety_supervisor_replan_requested",
        "safety_supervisor_terminal",
        "strategic_planner_call",
        "strategic_planner_cancelled",
        "world_state_event",
        "world_state_finished",
        "world_state_update",
    }
)


def _copy_source(tmp_path: Path) -> Path:
    destination = tmp_path / "src" / "kenshi_agent"
    shutil.copytree(SOURCE_ROOT, destination)
    return destination


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_source_inventory_and_review_authority_are_exact() -> None:
    inventory = discover_source_inventory()
    rows = validate_reviewed_dispositions(inventory, load_reviewed_dispositions())

    assert len(inventory.event_types) == 90
    assert inventory.event_types == EXPECTED_EVENTS
    assert [row["source_event_type"] for row in rows] == sorted(EXPECTED_EVENTS)


def test_optional_conformance_sinks_are_reviewed_and_runtime_producers_are_visible(
    tmp_path: Path,
) -> None:
    source = _copy_source(tmp_path)
    inventory = discover_source_inventory(source)
    adapter_sinks = [
        sink for sink in inventory.open_sinks if "evogen_subject/adapter.py" in sink.source_file
    ]
    assert len(adapter_sinks) == 2
    assert {sink.expression for sink in adapter_sinks} == {"event.model_dump_json()", "'\\n'"}

    runtime_producer = source / "evogen_subject" / "runtime_event_producer.py"
    for import_source in (
        "",
        "import kenshi_agent.env.live as live\n",
        "from kenshi_agent import env\n",
        "from .. import env\n",
        "from ..env import live\n",
    ):
        runtime_producer.write_text(
            f"{import_source}\nlogger.write('hidden_runtime_event')\n",
            encoding="utf-8",
        )
        with pytest.raises(SessionEventDispositionError, match="do not match source"):
            render_generated_dispositions(source, REVIEWED_PATH)


def test_generated_disposition_map_is_fresh() -> None:
    assert GENERATED_PATH.read_text(encoding="utf-8") == render_generated_dispositions()


def test_new_direct_event_fails_until_it_is_reviewed(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(coordinator, '"run_started",', '"unreviewed_source_event",')

    with pytest.raises(SessionEventDispositionError, match="do not match source"):
        render_generated_dispositions(source, REVIEWED_PATH)


def test_new_control_ownership_event_fails_until_reviewed(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    ownership = source / "control_ownership.py"
    _replace_once(
        ownership,
        '    READY = "agent_takeover_ready"\n',
        '    READY = "agent_takeover_ready"\n    PAUSED = "agent_takeover_paused"\n',
    )

    with pytest.raises(SessionEventDispositionError, match="do not match source"):
        render_generated_dispositions(source, REVIEWED_PATH)


def test_annotated_control_ownership_event_fails_until_reviewed(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    ownership = source / "control_ownership.py"
    _replace_once(
        ownership,
        '    READY = "agent_takeover_ready"\n',
        '    READY = "agent_takeover_ready"\n    PAUSED: str = "agent_takeover_paused"\n',
    )

    with pytest.raises(SessionEventDispositionError, match="do not match source"):
        render_generated_dispositions(source, REVIEWED_PATH)


def test_unresolved_dynamic_producer_fails_closed(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(coordinator, '"run_started",', "unresolved_event_name,")

    with pytest.raises(SessionEventDispositionError, match="Unreviewed open event sink"):
        discover_source_inventory(source)


def test_partially_resolved_conditional_fails_closed(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(
        coordinator,
        '            "run_started",',
        '            "run_started" if max_steps > 0 else unresolved_event_name,',
    )

    with pytest.raises(SessionEventDispositionError, match="Unreviewed open event sink"):
        discover_source_inventory(source)


@pytest.mark.parametrize(
    "call",
    [
        "self.logger.write()",
        "self.logger.write(payload={})",
        'self.logger.write(**{"event_type": "splat_new_event"})',
    ],
)
def test_malformed_or_splatted_logger_call_fails_closed(
    tmp_path: Path,
    call: str,
) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(
        coordinator,
        '        self.logger.write(\n            "run_started",',
        f'        {call}\n        self.logger.write(\n            "run_started",',
    )

    with pytest.raises(SessionEventDispositionError, match="Unreviewed open event sink"):
        discover_source_inventory(source)


def test_aliased_operation_progress_cannot_evade_inventory(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    cognition = source / "execution" / "handlers" / "cognition.py"
    _replace_once(
        cognition,
        "        context.progress(\n            action.question,",
        "        ctx = context\n        ctx.progress(\n            action.question,",
    )
    _replace_once(
        cognition,
        '            event_type="advisor_requested",',
        '            event_type="aliased_progress_event",',
    )

    with pytest.raises(SessionEventDispositionError, match="do not match source"):
        render_generated_dispositions(source, REVIEWED_PATH)


def test_new_logger_alias_cannot_evade_inventory(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(
        coordinator,
        '        self.logger.write(\n            "run_started",',
        "        audit = self.logger\n"
        '        audit.write("aliased_source_event")\n'
        '        self.logger.write(\n            "run_started",',
    )

    with pytest.raises(SessionEventDispositionError, match="Unreviewed open event sink"):
        discover_source_inventory(source)


def test_keyword_logger_alias_cannot_evade_inventory(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(
        coordinator,
        '        self.logger.write(\n            "run_started",',
        "        audit = self.logger\n"
        '        audit.write(event_type="aliased_keyword_event")\n'
        '        self.logger.write(\n            "run_started",',
    )

    with pytest.raises(SessionEventDispositionError, match="Unreviewed open event sink"):
        discover_source_inventory(source)


@pytest.mark.parametrize(
    "injected",
    [
        '        emit_event = self.logger.write\n        emit_event("bound_logger_new")\n',
        '        emit_plan = self._plan_event\n        emit_plan("bound_plan_new")\n',
        '        emit_event, = (self.logger.write,)\n        emit_event("tuple_logger_new")\n',
        '        emit_plan, = (self._plan_event,)\n        emit_plan("tuple_plan_new")\n',
        '        emit_event = getattr(self.logger, "write")\n'
        '        emit_event("assigned_getattr_logger_new")\n',
    ],
)
def test_bound_event_method_alias_fails_closed(
    tmp_path: Path,
    injected: str,
) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(
        coordinator,
        '        self.logger.write(\n            "run_started",',
        injected + '        self.logger.write(\n            "run_started",',
    )

    with pytest.raises(SessionEventDispositionError, match="Unreviewed open event sink"):
        discover_source_inventory(source)


@pytest.mark.parametrize("method", ["write", "_plan_event"])
def test_getattr_event_method_alias_fails_closed(tmp_path: Path, method: str) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(
        coordinator,
        '        self.logger.write(\n            "run_started",',
        f'        getattr(self.logger, "{method}")("getattr_event_new")\n'
        '        self.logger.write(\n            "run_started",',
    )

    with pytest.raises(SessionEventDispositionError, match="Unreviewed open event sink"):
        discover_source_inventory(source)


def test_duplicate_and_extra_review_rows_are_rejected() -> None:
    inventory = discover_source_inventory()
    reviewed = load_reviewed_dispositions()

    duplicate = copy.deepcopy(reviewed)
    duplicate["events"].append(copy.deepcopy(duplicate["events"][0]))
    with pytest.raises(SessionEventDispositionError, match="Duplicate disposition"):
        validate_reviewed_dispositions(inventory, duplicate)

    extra = copy.deepcopy(reviewed)
    extra["events"].append(
        {
            "source_event_type": "not_a_source_event",
            "disposition": "intentionally_ignored",
            "evogen_kind": None,
            "rationale": "Adversarial extra row.",
        }
    )
    with pytest.raises(SessionEventDispositionError, match="do not match source"):
        validate_reviewed_dispositions(inventory, extra)


def test_duplicate_review_json_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version": 1, "schema_version": 1, "events": []}\n',
        encoding="utf-8",
    )

    with pytest.raises(SessionEventDispositionError, match="Duplicate JSON key"):
        load_reviewed_dispositions(path)


def test_session_logger_contract_change_fails_closed(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    session_log = source / "session_log.py"
    _replace_once(session_log, "    def write(self,", "    def emit(self,")

    with pytest.raises(SessionEventDispositionError, match="SessionLogger.write"):
        discover_source_inventory(source)


def test_duplicate_source_emission_changes_fingerprint(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    original = discover_source_inventory(source)
    coordinator = source / "run_coordinator.py"
    _replace_once(
        coordinator,
        '        self.logger.write(\n            "run_started",',
        '        self.logger.write("run_started")\n'
        '        self.logger.write(\n            "run_started",',
    )
    changed = discover_source_inventory(source)

    assert changed.event_types == original.event_types
    assert changed.fingerprint != original.fingerprint


def test_duplicate_open_sink_fails_freshness(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    coordinator = source / "run_coordinator.py"
    _replace_once(
        coordinator,
        "        self.logger.write(\n            event_type,",
        "        self.logger.write(event_type)\n"
        "        self.logger.write(\n"
        "            event_type,",
    )

    with pytest.raises(SessionEventDispositionError, match="Unreviewed open event sink"):
        discover_source_inventory(source)


def test_function_default_is_inventoried(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    session_log = source / "session_log.py"
    session_log.write_text(
        session_log.read_text(encoding="utf-8")
        + "\n\ndef _default_probe(logger: SessionLogger) -> None:\n"
        + '    def _inner(_marker: object = logger.write("default_event")) -> None:\n'
        + "        return None\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionEventDispositionError, match="do not match source"):
        render_generated_dispositions(source, REVIEWED_PATH)


def test_comment_only_source_change_preserves_fingerprint(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    original = discover_source_inventory(source)
    path = source / "run_coordinator.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n# Inventory ignores comments.\n",
        encoding="utf-8",
    )

    assert discover_source_inventory(source).fingerprint == original.fingerprint


def test_generated_json_contains_every_event_once() -> None:
    payload = json.loads(render_generated_dispositions())
    event_types = [row["source_event_type"] for row in payload["events"]]

    assert len(event_types) == 90
    assert len(event_types) == len(set(event_types))
    assert set(event_types) == EXPECTED_EVENTS
