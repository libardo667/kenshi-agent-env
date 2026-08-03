from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from kenshi_agent.context_action_parity import (
    CONTEXT_ACTION_DECISIONS,
    WITNESSES_PATH,
    ContextActionExemptionReason,
    ContextMenuWitness,
    ExemptContextAction,
    MissingContextAction,
    WiredContextAction,
    classify_witnesses,
    load_witnesses,
    newest_run_directories,
    render_context_action_parity,
    witnesses_from_run,
    witnesses_from_runs,
    write_witnesses,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = object()


def menu_evidence() -> dict[str, object]:
    return {
        "identity_session_id": "session-1",
        "target_id": "entity-iron",
        "target_name": "Iron Resource",
        "target_kind": "natural_resource",
        "task_type_values": [87, 26],
        "task_type_values_complete": True,
        "selected_character_ids": ["entity-tassilo"],
        "reviewed_context_actions": ["operate"],
        "reviewed_default_task": "operate_machinery",
    }


def menu_event(evidence: object = DEFAULT_EVIDENCE) -> dict[str, object]:
    return {
        "event_type": "world_state_event",
        "payload": {
            "event_type": "runtime_context_menu_observed",
            "evidence": menu_evidence() if evidence is DEFAULT_EVIDENCE else evidence,
        },
    }


def iron_witness(*, run_id: str = "live-menu-r1") -> ContextMenuWitness:
    return ContextMenuWitness(
        run_id=run_id,
        identity_session_id="session-1",
        target_id="entity-iron",
        target_name="Iron Resource",
        target_kind="natural_resource",
        task_type_values=(87, 26),
        task_type_values_complete=True,
        selected_character_ids=("entity-tassilo",),
        reviewed_context_actions=("operate",),
        reviewed_default_task="operate_machinery",
    )


def test_standard_world_event_becomes_an_exact_context_menu_witness(
    tmp_path: Path,
) -> None:
    run = tmp_path / "live-menu-r1"
    run.mkdir()
    event = menu_event()
    (run / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    assert witnesses_from_run(run) == {iron_witness()}


def test_selected_character_menu_is_classified_without_a_world_target_row(
    tmp_path: Path,
) -> None:
    run = tmp_path / "selected-character-menu"
    run.mkdir()
    evidence = menu_evidence()
    evidence.update(
        {
            "target_id": "entity-tassilo",
            "target_name": "Tassilo",
            "target_kind": None,
            "task_type_values": [25],
            "reviewed_context_actions": [],
            "reviewed_default_task": None,
        }
    )
    (run / "events.jsonl").write_text(
        json.dumps(menu_event(evidence)) + "\n",
        encoding="utf-8",
    )

    witness = next(iter(witnesses_from_run(run)))
    assert witness.target_kind == "squad_character"


def test_malformed_world_event_evidence_is_never_partially_accepted(
    tmp_path: Path,
) -> None:
    run = tmp_path / "malformed-menu"
    run.mkdir()
    invalid_evidence: list[object] = [None, [], "not-an-object"]
    for field, invalid_values in {
        "identity_session_id": [None],
        "target_id": [None],
        "target_name": [3],
        "target_kind": [3],
        "task_type_values": ["87,26", (87, 26), [True, 26]],
        "task_type_values_complete": [1, None],
        "selected_character_ids": ["entity-tassilo", ("entity-tassilo",), [3]],
        "reviewed_context_actions": ["operate", ("operate",), [3]],
        "reviewed_default_task": [3],
    }.items():
        for invalid in invalid_values:
            evidence = menu_evidence()
            evidence[field] = invalid
            invalid_evidence.append(evidence)
    lines = [json.dumps(menu_event(evidence)) for evidence in invalid_evidence]
    lines.extend(
        [
            "not a context-menu event",
            "{broken runtime_context_menu_observed",
            json.dumps(["runtime_context_menu_observed"]),
            json.dumps(
                {
                    "event_type": "planner_error",
                    "payload": {"event_type": "runtime_context_menu_observed"},
                }
            ),
            json.dumps(
                {
                    "event_type": "world_state_event",
                    "payload": ["runtime_context_menu_observed"],
                }
            ),
            json.dumps(
                {
                    "event_type": "world_state_event",
                    "payload": {
                        "event_type": "something_else",
                        "marker": "runtime_context_menu_observed",
                        "evidence": menu_evidence(),
                    },
                }
            ),
        ]
    )
    lines.append(json.dumps(menu_event()))
    (run / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert witnesses_from_run(run) == {iron_witness(run_id="malformed-menu")}


@pytest.mark.parametrize(
    "noise",
    [
        "not a context-menu event",
        "{broken runtime_context_menu_observed",
        json.dumps(["runtime_context_menu_observed"]),
        json.dumps(
            {
                "event_type": "planner_error",
                "payload": {"event_type": "runtime_context_menu_observed"},
            }
        ),
        json.dumps(
            {
                "event_type": "world_state_event",
                "payload": ["runtime_context_menu_observed"],
            }
        ),
        json.dumps(
            {
                "event_type": "world_state_event",
                "payload": {
                    "event_type": "something_else",
                    "marker": "runtime_context_menu_observed",
                },
            }
        ),
    ],
)
def test_event_noise_never_hides_a_later_valid_witness(
    tmp_path: Path,
    noise: str,
) -> None:
    run = tmp_path / "noisy-menu"
    run.mkdir()
    (run / "events.jsonl").write_text(
        noise + "\n" + json.dumps(menu_event()) + "\n",
        encoding="utf-8",
    )

    assert witnesses_from_run(run) == {iron_witness(run_id="noisy-menu")}


def test_legacy_live_snapshot_is_backfilled_as_a_witness(tmp_path: Path) -> None:
    run = tmp_path / "legacy-live-menu"
    run.mkdir()
    telemetry = {
        "identity_session_id": "session-legacy",
        "capabilities": ["identity.stable_handles", "ui.context_menu.orders"],
        "ui": {
            "context_menu_open": True,
            "context_menu_probe": "captured",
            "context_menu": {
                "target_id": "entity-copper",
                "target_name": "Copper Resource",
                "task_type_values": [87, 26],
                "task_type_values_complete": True,
            },
            "selected_character_ids": ["entity-fish"],
        },
        "squad": [{"id": "entity-fish", "name": "Fish", "selected": True}],
        "world_targets": [
            {
                "id": "entity-copper",
                "name": "Copper Resource",
                "kind": "natural_resource",
                "position": {"x": 1, "y": 2, "z": 3},
                "distance": 4,
                "context_actions": ["operate"],
                "default_task": "operate_machinery",
            }
        ],
    }
    (run / "telemetry.context-menu.json").write_text(
        json.dumps(telemetry), encoding="utf-8"
    )

    assert witnesses_from_run(run) == {
        ContextMenuWitness(
            run_id="legacy-live-menu",
            identity_session_id="session-legacy",
            target_id="entity-copper",
            target_name="Copper Resource",
            target_kind="natural_resource",
            task_type_values=(87, 26),
            task_type_values_complete=True,
            selected_character_ids=("entity-fish",),
            reviewed_context_actions=("operate",),
            reviewed_default_task="operate_machinery",
        )
    }


def test_legacy_selected_character_menu_resolves_without_a_world_target(
    tmp_path: Path,
) -> None:
    run = tmp_path / "legacy-selected-character"
    run.mkdir()
    telemetry = {
        "identity_session_id": "session-selected",
        "capabilities": ["identity.stable_handles", "ui.context_menu.orders"],
        "ui": {
            "context_menu_open": True,
            "context_menu_probe": "captured",
            "context_menu": {
                "target_id": "entity-fish",
                "target_name": "Fish",
                "task_type_values": [25],
                "task_type_values_complete": True,
            },
            "selected_character_ids": ["entity-fish"],
        },
        "squad": [{"id": "entity-fish", "name": "Fish", "selected": True}],
        "world_targets": [],
    }
    (run / "telemetry.context-menu.json").write_text(
        json.dumps(telemetry), encoding="utf-8"
    )

    witness = next(iter(witnesses_from_run(run)))
    assert witness.target_kind == "squad_character"


def test_legacy_snapshot_retains_unresolved_target_and_rejects_invalid_state(
    tmp_path: Path,
) -> None:
    run = tmp_path / "legacy-unresolved"
    run.mkdir()
    telemetry = {
        "identity_session_id": "session-unresolved",
        "capabilities": ["identity.stable_handles", "ui.context_menu.orders"],
        "ui": {
            "context_menu_open": True,
            "context_menu_probe": "captured",
            "context_menu": {
                "target_id": "entity-unknown",
                "target_name": None,
                "task_type_values": [9999],
                "task_type_values_complete": False,
            },
            "selected_character_ids": [],
        },
    }
    (run / "telemetry.context-menu.json").write_text(
        json.dumps(telemetry), encoding="utf-8"
    )
    (run / "telemetry.invalid.json").write_text("{}", encoding="utf-8")
    (run / "telemetry.broken.json").write_text("{broken", encoding="utf-8")
    without_menu = {
        **telemetry,
        "ui": {
            **telemetry["ui"],
            "context_menu_open": False,
            "context_menu_probe": "closed",
            "context_menu": None,
        },
    }
    (run / "telemetry.no-menu.json").write_text(
        json.dumps(without_menu), encoding="utf-8"
    )
    without_identity = {**telemetry, "identity_session_id": None}
    (run / "telemetry.no-identity.json").write_text(
        json.dumps(without_identity), encoding="utf-8"
    )

    assert witnesses_from_run(run) == {
        ContextMenuWitness(
            run_id="legacy-unresolved",
            identity_session_id="session-unresolved",
            target_id="entity-unknown",
            target_name=None,
            target_kind="unresolved",
            task_type_values=(9999,),
            task_type_values_complete=False,
            selected_character_ids=(),
            reviewed_context_actions=(),
            reviewed_default_task=None,
        )
    }


def test_witness_store_round_trips_every_field_and_has_stable_order(
    tmp_path: Path,
) -> None:
    first = iron_witness()
    second = ContextMenuWitness(
        run_id="a-earlier-sort",
        identity_session_id="session-2",
        target_id="entity-unknown",
        target_name=None,
        target_kind="unresolved",
        task_type_values=(9999,),
        task_type_values_complete=False,
        selected_character_ids=(),
        reviewed_context_actions=(),
        reviewed_default_task=None,
    )
    path = tmp_path / "nested" / "store" / "witnesses.json"

    assert write_witnesses(path, {first, second}) == path
    expected = {
        "generated_by": "scripts/export_context_action_witnesses.py",
        "schema_version": 1,
        "witnesses": json.loads(json.dumps([asdict(second), asdict(first)])),
    }
    assert path.read_text(encoding="utf-8") == json.dumps(expected, indent=2) + "\n"
    assert load_witnesses(path) == {first, second}
    assert write_witnesses(path, {first, second}) == path


def test_witness_store_upgrades_a_selected_unresolved_target_kind(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-witnesses.json"
    legacy = ContextMenuWitness(
        run_id="legacy-selected",
        identity_session_id="session-selected",
        target_id="entity-fish",
        target_name="Fish",
        target_kind="unresolved",
        task_type_values=(25,),
        task_type_values_complete=True,
        selected_character_ids=("entity-fish",),
        reviewed_context_actions=(),
        reviewed_default_task=None,
    )
    path.write_text(
        json.dumps({"schema_version": 1, "witnesses": [asdict(legacy)]}),
        encoding="utf-8",
    )

    witness = next(iter(load_witnesses(path)))
    assert witness.target_kind == "squad_character"


def test_witness_store_missing_and_invalid_envelopes_fail_predictably(
    tmp_path: Path,
) -> None:
    assert load_witnesses(tmp_path / "missing.json") == set()
    invalid = tmp_path / "invalid.json"
    for payload in ([], {}, {"witnesses": {}}):
        invalid.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(
            ValueError,
            match="^context-menu witness file has an invalid envelope$",
        ):
            load_witnesses(invalid)


def test_run_discovery_is_bounded_newest_first_and_aggregates_exactly(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old-run"
    newest = tmp_path / "new-run"
    ignored = tmp_path / "not-a-run"
    for directory in (old, newest, ignored):
        directory.mkdir()
    (old / "events.jsonl").write_text(
        json.dumps(menu_event()) + "\n", encoding="utf-8"
    )
    telemetry_only = tmp_path / "telemetry-only"
    telemetry_only.mkdir()
    (telemetry_only / "telemetry.context-menu.json").write_text(
        "{}", encoding="utf-8"
    )
    newest_evidence = menu_evidence()
    newest_evidence["identity_session_id"] = "session-new"
    (newest / "events.jsonl").write_text(
        json.dumps(menu_event(newest_evidence)) + "\n", encoding="utf-8"
    )
    os.utime(old, (10, 10))
    os.utime(newest, (20, 20))
    os.utime(telemetry_only, (5, 5))

    assert newest_run_directories(tmp_path, limit=1) == [newest]
    assert newest_run_directories(tmp_path, limit=3) == [newest, old, telemetry_only]
    discovered = witnesses_from_runs(tmp_path, limit=1)
    assert len(discovered) == 1
    assert next(iter(discovered)).identity_session_id == "session-new"


def test_every_empirical_pair_has_one_decision_and_no_decision_is_speculative(
) -> None:
    witnesses = load_witnesses(WITNESSES_PATH)
    coverage = classify_witnesses(witnesses)
    witnessed_pairs = {
        (row.target_kind, row.task_type_value) for row in coverage
    }

    assert witnesses
    assert witnessed_pairs == set(CONTEXT_ACTION_DECISIONS)
    assert all(row.decision is not None for row in coverage)
    for decision in CONTEXT_ACTION_DECISIONS.values():
        if isinstance(decision, WiredContextAction):
            from kenshi_agent.affordances import AFFORDANCE_ADAPTERS

            assert decision.adapter_routes
            adapter_names = {adapter.name for adapter in AFFORDANCE_ADAPTERS}
            assert all(
                route.partition(":")[0] in adapter_names
                for route in decision.adapter_routes
            )
        elif isinstance(decision, MissingContextAction):
            assert decision.queue_description.strip()
        else:
            assert decision.rationale.strip()


def test_classification_counts_distinct_witnesses_and_exposes_unknown_tasks() -> None:
    first = iron_witness()
    second = ContextMenuWitness(
        **{
            **asdict(first),
            "run_id": "second-run",
            "task_type_values": (87, 87, 9999),
        }
    )

    coverage = classify_witnesses({first, second})

    assert [
        (
            row.target_kind,
            row.task_type_value,
            row.task_type_name,
            row.witness_count,
            row.decision,
        )
        for row in coverage
    ] == [
        (
            "natural_resource",
            26,
            "LOOT_TARGET",
            1,
            CONTEXT_ACTION_DECISIONS[("natural_resource", 26)],
        ),
        (
            "natural_resource",
            87,
            "OPERATE_MACHINERY",
            2,
            CONTEXT_ACTION_DECISIONS[("natural_resource", 87)],
        ),
        ("natural_resource", 9999, "UNKNOWN_9999", 1, None),
    ]

    report = render_context_action_parity({first, second})
    assert "menu witnesses   2" in report
    assert "witnessed pairs  3" in report
    assert "wired            2" in report
    assert "exempt           0" in report
    assert "missing          0" in report
    assert "unclassified     1" in report
    assert "| natural_resource | `UNKNOWN_9999` | 9999 | 1 | **UNCLASSIFIED** |" in report


def test_report_keeps_observation_separate_from_execution_authority() -> None:
    report = render_context_action_parity(load_witnesses(WITNESSES_PATH))

    assert "witnessed pairs  3" in report
    assert "wired            3" in report
    assert "missing          0" in report
    assert "unclassified     0" in report
    assert "OPERATE_MACHINERY" in report
    assert "LOOT_TARGET" in report
    assert "FIRST_AID_ORDER" in report
    assert "context_orders:first_aid" in report
    assert "Observation never grants execution authority" in report


def test_report_renders_a_typed_exemption_without_granting_a_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    witness = ContextMenuWitness(
        run_id="synthetic-exemption",
        identity_session_id="session-1",
        target_id="entity-unknown",
        target_name=None,
        target_kind="natural_resource",
        task_type_values=(9999,),
        task_type_values_complete=True,
        selected_character_ids=("entity-fish",),
        reviewed_context_actions=(),
        reviewed_default_task=None,
    )
    monkeypatch.setitem(
        CONTEXT_ACTION_DECISIONS,
        ("natural_resource", 9999),
        ExemptContextAction(
            reason=ContextActionExemptionReason.UNSAFE,
            rationale="unsafe synthetic witness",
        ),
    )

    report = render_context_action_parity({witness})

    assert "exempt           1" in report
    assert "exempt[unsafe]: unsafe synthetic witness" in report


def test_committed_witnesses_include_current_local_runtime_evidence() -> None:
    discovered = witnesses_from_runs(ROOT / "runs")
    if not discovered:
        return

    assert discovered <= load_witnesses(WITNESSES_PATH), (
        "local context-menu evidence has not entered the committed witness set; "
        "run scripts/export_context_action_witnesses.py"
    )
