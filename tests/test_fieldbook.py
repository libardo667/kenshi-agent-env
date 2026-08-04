"""The private fieldbook is durable project context, never game state."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from kenshi_agent import application as cli
from kenshi_agent.campaign import CampaignScope, CampaignScopeOrigin
from kenshi_agent.config import load_config
from kenshi_agent.core.continuity import (
    AppendFieldbookEntryOperation,
    CreateFieldbookProjectOperation,
    FieldbookEntryKind,
    FieldbookLifecycleEvent,
    FieldbookProjectKind,
    FieldbookProjectStatus,
    FieldbookReadReceipt,
    FieldbookReadStatus,
    SelectFieldbookProjectOperation,
    SetFieldbookProjectStatusOperation,
    UpdateFieldbookSummaryOperation,
)
from kenshi_agent.fieldbook import (
    FieldbookNoOp,
    FieldbookTransitionError,
    render_fieldbook_markdown,
)
from kenshi_agent.memory import MemoryStore
from kenshi_agent.runtime_continuity import build_fieldbook_read_receipt


def open_store(path: Path, campaign_id: str = "campaign-a") -> MemoryStore:
    return MemoryStore(
        path,
        CampaignScope(
            campaign_id=campaign_id,
            origin=CampaignScopeOrigin.CONFIGURED,
        ),
    )


def test_fieldbook_project_lifecycle_and_entries_round_trip_across_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "continuity.sqlite3"
    with open_store(path) as store:
        assert store.fieldbook.event_count() == 0
        project = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.DELIVERY_DOCKET,
            title="Six-canister delivery",
            summary="Find, acquire, and deliver six sealed slop canisters.",
            provenance=None,
        )
        selected = store.fieldbook.select_project(
            run_id="run-a",
            project_id=project.project_id,
            provenance=None,
        )
        selected_persisted = store.fieldbook.get_project(project.project_id)
        assert selected_persisted is not None
        assert selected_persisted == selected
        entry = store.fieldbook.append_entry(
            run_id="run-a",
            project_id=project.project_id,
            kind=FieldbookEntryKind.QUESTION,
            content="Which route keeps Ladle out of the fog?",
            provenance=None,
        )
        updated = store.fieldbook.update_summary(
            run_id="run-a",
            project_id=project.project_id,
            summary="Cargo still needed; route question is open.",
            provenance=None,
        )
        updated_persisted = store.fieldbook.get_project(project.project_id)
        assert updated_persisted is not None
        assert updated_persisted == updated
        paused = store.fieldbook.set_status(
            run_id="run-a",
            project_id=project.project_id,
            status=FieldbookProjectStatus.PAUSED,
            provenance=None,
        )
        assert store.fieldbook.get_project(project.project_id) == paused

        assert project.project_id.startswith("fbp-")
        assert entry.entry_id.startswith("fbe-")
        assert selected.selected
        assert entry.project_id == project.project_id
        assert updated.summary == "Cargo still needed; route question is open."
        assert paused.status is FieldbookProjectStatus.PAUSED
        assert not paused.selected
        assert store.fieldbook.active_project() is None

    with open_store(path) as reopened:
        project = reopened.fieldbook.get_project(project.project_id)
        assert project is not None
        assert project.status is FieldbookProjectStatus.PAUSED
        assert project.entry_count == 1
        assert [item.entry_id for item in reopened.fieldbook.entries(project.project_id)] == [
            entry.entry_id
        ]
        resumed = reopened.fieldbook.set_status(
            run_id="run-b",
            project_id=project.project_id,
            status=FieldbookProjectStatus.ACTIVE,
            provenance=None,
        )
        completed = reopened.fieldbook.set_status(
            run_id="run-b",
            project_id=project.project_id,
            status=FieldbookProjectStatus.COMPLETED,
            provenance=None,
        )
        assert resumed.status is FieldbookProjectStatus.ACTIVE
        assert completed.status is FieldbookProjectStatus.COMPLETED


def test_planner_fieldbook_operations_carry_no_arbitrary_path_escape() -> None:
    operations = [
        CreateFieldbookProjectOperation(
            kind=FieldbookProjectKind.ROUTE_ATLAS,
            title="Squin route",
            summary="Known route observations.",
        ),
        AppendFieldbookEntryOperation(
            project_id="fbp-" + "1" * 32,
            kind=FieldbookEntryKind.QUESTION,
            content="Is the western gate open at night?",
        ),
        UpdateFieldbookSummaryOperation(
            project_id="fbp-" + "1" * 32,
            summary="The gate timing is still unknown.",
        ),
        SelectFieldbookProjectOperation(project_id="fbp-" + "1" * 32),
        SetFieldbookProjectStatusOperation(
            project_id="fbp-" + "1" * 32,
            status=FieldbookProjectStatus.ABANDONED,
        ),
    ]

    assert [operation.operation for operation in operations] == [
        "create_project",
        "append_entry",
        "update_summary",
        "select_project",
        "set_project_status",
    ]
    with pytest.raises(ValidationError):
        CreateFieldbookProjectOperation.model_validate(
            {
                "kind": "route_atlas",
                "title": "Escape",
                "summary": "No structured-store boundary.",
                "path": "../../outside.md",
            }
        )
    for payload in (
        {
            "operation": "create_project",
            "kind": "generic",
            "title": "   ",
            "summary": "Valid.",
        },
        {
            "operation": "append_entry",
            "project_id": "fbp-" + "1" * 32,
            "kind": "note",
            "content": "\n\t",
        },
        {
            "operation": "update_summary",
            "project_id": "fbp-" + "1" * 32,
            "summary": " ",
        },
    ):
        with pytest.raises(ValidationError, match="must not be blank"):
            if payload["operation"] == "create_project":
                CreateFieldbookProjectOperation.model_validate(payload)
            elif payload["operation"] == "append_entry":
                AppendFieldbookEntryOperation.model_validate(payload)
            else:
                UpdateFieldbookSummaryOperation.model_validate(payload)


def test_fieldbook_never_reads_a_project_from_another_campaign(
    tmp_path: Path,
) -> None:
    path = tmp_path / "continuity.sqlite3"
    with open_store(path, "campaign-a") as first:
        project = first.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.JOURNAL,
            title="Private campaign notes",
            summary="Only campaign A may see this.",
            provenance=None,
        )

    with open_store(path, "campaign-b") as second:
        assert second.fieldbook.get_project(project.project_id) is None
        assert second.fieldbook.list_projects() == []
        with pytest.raises(FieldbookTransitionError, match="campaign-b"):
            second.fieldbook.read(
                project_id=project.project_id,
                query=None,
                limit=8,
            )


def test_automatic_fieldbook_context_is_bounded_metadata_with_one_selected_summary(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        projects = [
            store.fieldbook.create_project(
                run_id="run-a",
                kind=FieldbookProjectKind.GENERIC,
                title=f"Project {number}",
                summary=f"Summary {number} " + "x" * 200,
                provenance=None,
            )
            for number in range(10)
        ]
        store.fieldbook.append_entry(
            run_id="run-a",
            project_id=projects[-1].project_id,
            kind=FieldbookEntryKind.NOTE,
            content="This full entry must not leak into the automatic index.",
            provenance=None,
        )
        store.fieldbook.select_project(
            run_id="run-a",
            project_id=projects[-1].project_id,
            provenance=None,
        )

        index = store.fieldbook.list_projects(limit=8)
        active = store.fieldbook.active_project_summary()

        assert len(index) == 8
        assert len(store.fieldbook.all_projects()) == 10
        assert store.fieldbook.all_projects()[0].project_id == projects[-1].project_id
        assert len(store.fieldbook.list_projects()) == 8
        assert index[0].project_id == projects[-1].project_id
        assert index[0].selected
        assert len(index[0].short_summary) == 160
        assert index[0].short_summary.endswith("...")
        assert "full entry" not in index[0].model_dump_json()
        assert active is not None
        assert active.project_id == projects[-1].project_id
        assert active.summary == projects[-1].summary


def test_fieldbook_index_preserves_exact_boundary_summary_and_zero_limit(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        project = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.GENERIC,
            title="Boundary",
            summary="s" * 160,
            provenance=None,
        )

        assert store.fieldbook.list_projects(limit=0) == []
        index = store.fieldbook.list_projects(limit=1)
        assert index[0].project_id == project.project_id
        assert index[0].short_summary == "s" * 160
        assert index[0].title == project.title
        assert index[0].kind is project.kind
        assert index[0].status is project.status
        assert index[0].entry_count == project.entry_count
        assert index[0].updated_at == project.updated_at
        assert index[0].selected == project.selected
        with pytest.raises(ValueError, match="non-negative"):
            store.fieldbook.list_projects(limit=-1)

        store.fieldbook.update_summary(
            run_id="run-a",
            project_id=project.project_id,
            summary="s" * 161,
            provenance=None,
        )
        assert store.fieldbook.list_projects(limit=1)[0].short_summary == (
            "s" * 157 + "..."
        )


def test_fieldbook_read_is_bounded_reports_truncation_and_treats_wildcards_literally(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        project = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.INCIDENT_LOG,
            title="Road incidents",
            summary="Incidents observed on the road.",
            provenance=None,
        )
        entries = [
            store.fieldbook.append_entry(
                run_id="run-a",
                project_id=project.project_id,
                kind=FieldbookEntryKind.QUESTION,
                content=content,
                provenance=None,
            )
            for content in (
                "ordinary event zero",
                "ordinary event one",
                "literal 100% risk marker",
                "decoy 100X risk marker",
                "literal route_name marker",
                "decoy routeXname marker",
                r"literal path\gate marker",
                "ordinary event seven",
            )
        ]

        bounded = store.fieldbook.read(
            project_id=project.project_id,
            query=None,
            limit=2,
        )
        percent = store.fieldbook.read(
            project_id=None,
            query="100%",
            limit=8,
        )
        exact_limit = store.fieldbook.read(
            project_id=None,
            query="100%",
            limit=1,
        )
        underscore = store.fieldbook.read(
            project_id=None,
            query="route_name",
            limit=8,
        )
        backslash = store.fieldbook.read(
            project_id=None,
            query=r"path\gate",
            limit=8,
        )
        scoped = store.fieldbook.read(
            project_id=project.project_id,
            query="ordinary",
            limit=2,
        )

        assert bounded.matched == 8
        assert bounded.truncated
        assert [entry.entry_id for entry in bounded.entries] == [
            entries[6].entry_id,
            entries[7].entry_id,
        ]
        assert [entry.entry_id for entry in percent.entries] == [
            entries[2].entry_id
        ]
        assert exact_limit.matched == 1
        assert not exact_limit.truncated
        assert [entry.entry_id for entry in underscore.entries] == [
            entries[4].entry_id
        ]
        assert [entry.entry_id for entry in backslash.entries] == [
            entries[6].entry_id
        ]
        assert scoped.project == store.fieldbook.get_project(project.project_id)
        assert scoped.project_id == project.project_id
        assert scoped.query == "ordinary"
        assert scoped.matched == 3
        assert scoped.truncated
        assert [entry.entry_id for entry in scoped.entries] == [
            entries[1].entry_id,
            entries[7].entry_id,
        ]
        assert scoped.reason == (
            f"3 fieldbook entries match project {project.project_id!r} "
            "and query 'ordinary'; 2 shown."
        )

        for project_id, query, limit, message in (
            (None, None, 1, "requires project_id or query"),
            (None, "ordinary", 0, "between one and eight"),
            (None, "ordinary", 9, "between one and eight"),
            (None, " \t", 1, "must not be blank"),
        ):
            with pytest.raises(ValueError, match=message):
                store.fieldbook.read(
                    project_id=project_id,
                    query=query,
                    limit=limit,
                )


def test_fieldbook_read_receipt_conserves_exact_results_and_campaign_state(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        project = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.ROUTE_ATLAS,
            title="Road atlas",
            summary="Observed route fragments.",
            provenance=None,
        )
        entry = store.fieldbook.append_entry(
            run_id="run-a",
            project_id=project.project_id,
            kind=FieldbookEntryKind.QUESTION,
            content="Does this road reach Squin?",
            provenance=None,
        )
        result = store.fieldbook.read(
            project_id=project.project_id,
            query=None,
            limit=8,
        )

    receipt = build_fieldbook_read_receipt(
        result,
        status=FieldbookReadStatus.COMPLETED,
        campaign_id="campaign-a",
        plan_id="plan-" + "1" * 32,
        plan_version=2,
        step_id="read-road-atlas",
    )

    assert receipt.project_ids == [project.project_id]
    assert receipt.entry_ids == [entry.entry_id]
    assert receipt.project == result.project

    payload = receipt.model_dump(mode="json")
    payload["entry_ids"] = []
    with pytest.raises(ValidationError, match="entry_ids must exactly match"):
        FieldbookReadReceipt.model_validate(payload)

    payload = receipt.model_dump(mode="json")
    payload["project_ids"] = []
    with pytest.raises(ValidationError, match="project_ids must exactly match"):
        FieldbookReadReceipt.model_validate(payload)

    payload = receipt.model_dump(mode="json")
    payload["status"] = FieldbookReadStatus.UNAVAILABLE
    with pytest.raises(ValidationError, match="impossible fieldbook read"):
        FieldbookReadReceipt.model_validate(payload)

    unavailable = FieldbookReadReceipt(
        receipt_id="fbr-" + "2" * 32,
        status=FieldbookReadStatus.UNAVAILABLE,
        campaign_id=None,
        project_id=None,
        query="roads",
        project=None,
        entries=[],
        matched=0,
        truncated=False,
        reason="The durable fieldbook is disabled.",
        project_ids=[],
        entry_ids=[],
        plan_id="plan-" + "2" * 32,
        plan_version=1,
        step_id="read-roads",
        recorded_at=datetime.now(UTC),
    )
    assert unavailable.status is FieldbookReadStatus.UNAVAILABLE


def test_fieldbook_selection_and_append_roll_back_as_whole_transitions(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        first = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.GENERIC,
            title="First",
            summary="First project.",
            provenance=None,
        )
        second = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.GENERIC,
            title="Second",
            summary="Second project.",
            provenance=None,
        )
        store.fieldbook.select_project(
            run_id="run-a",
            project_id=first.project_id,
            provenance=None,
        )
        events_before_selection = store.fieldbook.event_count()
        store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            f"""
            CREATE TEMP TRIGGER fail_second_selection
            BEFORE UPDATE OF selected ON fieldbook_projects
            WHEN NEW.project_id = '{second.project_id}' AND NEW.selected = 1
            BEGIN SELECT RAISE(ABORT, 'injected selection failure'); END
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected selection failure"):
            store.fieldbook.select_project(
                run_id="run-a",
                project_id=second.project_id,
                provenance=None,
            )
        assert store.fieldbook.active_project() is not None
        assert store.fieldbook.active_project().project_id == first.project_id  # type: ignore[union-attr]
        assert store.fieldbook.event_count() == events_before_selection

        events_before_append = store.fieldbook.event_count()
        store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TEMP TRIGGER fail_entry_insert
            BEFORE INSERT ON fieldbook_entries
            BEGIN SELECT RAISE(ABORT, 'injected append failure'); END
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected append failure"):
            store.fieldbook.append_entry(
                run_id="run-a",
                project_id=first.project_id,
                kind=FieldbookEntryKind.NOTE,
                content="This must roll back with its lifecycle event.",
                provenance=None,
            )
        persisted = store.fieldbook.get_project(first.project_id)
        assert persisted is not None
        assert persisted.entry_count == 0
        assert store.fieldbook.entries(first.project_id) == []
        assert store.fieldbook.event_count() == events_before_append


def test_selection_history_conserves_every_project_state_transition(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        first = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.GENERIC,
            title="First",
            summary="First project.",
            provenance=None,
        )
        second = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.JOURNAL,
            title="Second",
            summary="Second project.",
            provenance=None,
        )
        store.fieldbook.select_project(
            run_id="run-select-first",
            project_id=first.project_id,
            provenance=None,
        )
        store.fieldbook.select_project(
            run_id="run-select-second",
            project_id=second.project_id,
            provenance=None,
        )

        first_after = store.fieldbook.get_project(first.project_id)
        second_after = store.fieldbook.get_project(second.project_id)
        assert first_after is not None
        assert second_after is not None
        assert not first_after.selected
        assert second_after.selected
        assert first_after.updated_at == second_after.updated_at
        assert [event.event for event in store.fieldbook.history(first.project_id)] == [
            FieldbookLifecycleEvent.CREATE_PROJECT,
            FieldbookLifecycleEvent.SELECT_PROJECT,
            FieldbookLifecycleEvent.CLEAR_SELECTION,
        ]
        assert [
            event.run_id for event in store.fieldbook.history(first.project_id)
        ] == ["run-a", "run-select-first", "run-select-second"]
        assert [event.event for event in store.fieldbook.history(second.project_id)] == [
            FieldbookLifecycleEvent.CREATE_PROJECT,
            FieldbookLifecycleEvent.SELECT_PROJECT,
        ]

        store.fieldbook.select_project(
            run_id="run-clear",
            project_id=None,
            provenance=None,
        )
        assert store.fieldbook.active_project() is None
        second_history = store.fieldbook.history(second.project_id)
        assert [event.event for event in second_history] == [
            FieldbookLifecycleEvent.CREATE_PROJECT,
            FieldbookLifecycleEvent.SELECT_PROJECT,
            FieldbookLifecycleEvent.CLEAR_SELECTION,
        ]
        assert second_history[-1].run_id == "run-clear"
        assert second_history[-1].payload == {"provenance": None}
        assert second_history[-1].entry_id is None
        cleared = store.fieldbook.get_project(second.project_id)
        assert cleared is not None
        assert not cleared.selected
        assert cleared.updated_at == second_history[-1].recorded_at
        assert store.fieldbook.event_count() == 6

        with pytest.raises(FieldbookNoOp, match="No fieldbook project"):
            store.fieldbook.select_project(
                run_id="run-clear-again",
                project_id=None,
                provenance=None,
            )
        assert store.fieldbook.event_count() == 6


def test_closed_fieldbook_projects_refuse_changes_and_clear_selection(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "continuity.sqlite3") as store:
        project = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.EQUIPMENT_PLAN,
            title="Equipment",
            summary="Acquire one backpack.",
            provenance=None,
        )
        store.fieldbook.select_project(
            run_id="run-a",
            project_id=project.project_id,
            provenance=None,
        )
        closed = store.fieldbook.set_status(
            run_id="run-a",
            project_id=project.project_id,
            status=FieldbookProjectStatus.ABANDONED,
            provenance=None,
        )

        assert not closed.selected
        assert store.fieldbook.active_project() is None
        with pytest.raises(FieldbookTransitionError, match="cannot be changed"):
            store.fieldbook.update_summary(
                run_id="run-a",
                project_id=project.project_id,
                summary="This cannot reopen a terminal project.",
                provenance=None,
            )
        with pytest.raises(FieldbookTransitionError, match="cannot be changed"):
            store.fieldbook.set_status(
                run_id="run-a",
                project_id=project.project_id,
                status=FieldbookProjectStatus.ACTIVE,
                provenance=None,
            )
        with pytest.raises(FieldbookNoOp, match="No fieldbook project"):
            store.fieldbook.select_project(
                run_id="run-a",
                project_id=None,
                provenance=None,
            )


def test_v3_database_is_backed_up_before_fieldbook_schema_is_added(
    tmp_path: Path,
) -> None:
    path = tmp_path / "continuity.sqlite3"
    with open_store(path):
        pass
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        DROP TABLE fieldbook_events;
        DROP TABLE fieldbook_entries;
        DROP TABLE fieldbook_projects;
        UPDATE continuity_meta SET value='3' WHERE key='schema_version';
        """
    )
    connection.commit()
    connection.close()
    original = path.read_bytes()

    with open_store(path) as migrated:
        assert migrated.schema_version == 4
        assert migrated.fieldbook.list_projects() == []

    backup = path.with_suffix(path.suffix + ".v3-backup")
    assert backup.read_bytes() == original


def test_markdown_export_is_disposable_and_never_read_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "continuity.sqlite3"
    with open_store(path) as store:
        project = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.VENDOR_LEDGER,
            title="Vendor ledger",
            summary="Known vendors, grounded elsewhere.",
            provenance=None,
        )
        canonical = render_fieldbook_markdown(store.fieldbook)
        export = tmp_path / "fieldbook.md"
        export.write_text("Invented inventory: 999 canisters.", encoding="utf-8")

        assert render_fieldbook_markdown(store.fieldbook) == canonical
        assert "999 canisters" not in canonical
        assert project.project_id in canonical
        assert canonical == (
            "# Fieldbook: campaign-a\n\n"
            "## Vendor ledger\n\n"
            f"`{project.project_id}` · vendor_ledger · active\n\n"
            "Known vendors, grounded elsewhere.\n"
        )


def test_fieldbook_cli_inspection_is_read_only_and_can_render_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "continuity.sqlite3"
    with open_store(path) as store:
        project = store.fieldbook.create_project(
            run_id="run-a",
            kind=FieldbookProjectKind.JOURNAL,
            title="Journal",
            summary="One canonical project.",
            provenance=None,
        )
        store.fieldbook.append_entry(
            run_id="run-a",
            project_id=project.project_id,
            kind=FieldbookEntryKind.NOTE,
            content="One canonical entry.",
            provenance=None,
        )
    original = path.read_bytes()
    config = load_config(
        Path(__file__).resolve().parents[1] / "config" / "default.yaml"
    )
    config = config.model_copy(
        update={
            "paths": config.paths.model_copy(update={"memory_db": path})
        }
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)

    assert (
        cli.main(
            [
                "fieldbook",
                "--config",
                "unused",
                "--campaign",
                "campaign-a",
                "--project-id",
                project.project_id,
            ]
        )
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["project"]["project_id"] == project.project_id
    assert document["entries"][0]["content"] == "One canonical entry."
    assert path.read_bytes() == original

    assert (
        cli.main(
            [
                "fieldbook",
                "--config",
                "unused",
                "--campaign",
                "campaign-a",
                "--markdown",
            ]
        )
        == 0
    )
    assert "# Fieldbook: campaign-a" in capsys.readouterr().out
    assert path.read_bytes() == original
