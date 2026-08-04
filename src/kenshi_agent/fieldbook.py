"""Campaign-scoped private project context in the canonical continuity database.

The fieldbook is structured durable context, not Kenshi state. It shares the
memory store's SQLite connection and transaction boundary; planner operations
never receive a filesystem path, and every automatic/read collection is
bounded before it reaches a planner.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .core.continuity import (
    ActiveFieldbookProject,
    CanonicalFieldbookProvenance,
    FieldbookEntry,
    FieldbookEntryKind,
    FieldbookHistoryEntry,
    FieldbookLifecycleEvent,
    FieldbookProject,
    FieldbookProjectIndex,
    FieldbookProjectKind,
    FieldbookProjectStatus,
    FieldbookReadResult,
    new_fieldbook_entry_id,
    new_fieldbook_project_id,
)

# SQL is declarative input to SQLite. The behavioral contract around these
# statements is mutation-tested through the methods below.
# pragma: no mutate start
_CREATE_PROJECTS_SQL = """
    CREATE TABLE IF NOT EXISTS fieldbook_projects (
        project_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        selected INTEGER NOT NULL DEFAULT 0,
        entry_count INTEGER NOT NULL DEFAULT 0,
        created_run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        latest_provenance TEXT
    )
"""
_CREATE_ENTRIES_SQL = """
    CREATE TABLE IF NOT EXISTS fieldbook_entries (
        entry_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES fieldbook_projects(project_id),
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
        sequence INTEGER NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        created_run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        provenance TEXT,
        UNIQUE(project_id, sequence)
    )
"""
_CREATE_EVENTS_SQL = """
    CREATE TABLE IF NOT EXISTS fieldbook_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
        project_id TEXT NOT NULL,
        entry_id TEXT,
        event TEXT NOT NULL,
        run_id TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        payload TEXT NOT NULL
    )
"""
_INDEX_SQL = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS fieldbook_one_selected_project
    ON fieldbook_projects (campaign_id) WHERE selected = 1
    """,
    """
    CREATE INDEX IF NOT EXISTS fieldbook_projects_campaign_status
    ON fieldbook_projects (campaign_id, status, selected DESC, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS fieldbook_entries_project_order
    ON fieldbook_entries (campaign_id, project_id, sequence)
    """,
    """
    CREATE INDEX IF NOT EXISTS fieldbook_entries_campaign_order
    ON fieldbook_entries (campaign_id, created_at DESC, entry_id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS fieldbook_events_campaign_order
    ON fieldbook_events (campaign_id, event_id)
    """,
)
_PROJECT_COLUMNS = """
    project_id, campaign_id, kind, status, title, summary, selected,
    entry_count, created_run_id, created_at, updated_at, latest_provenance
"""
_ENTRY_COLUMNS = """
    entry_id, project_id, campaign_id, sequence, kind, content,
    created_run_id, created_at, provenance
"""
_INSERT_PROJECT_SQL = """
    INSERT INTO fieldbook_projects (
        project_id, campaign_id, kind, status, title, summary, selected,
        entry_count, created_run_id, created_at, updated_at, latest_provenance
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_INSERT_ENTRY_SQL = """
    INSERT INTO fieldbook_entries (
        entry_id, project_id, campaign_id, sequence, kind, content,
        created_run_id, created_at, provenance
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_APPEND_EVENT_SQL = """
    INSERT INTO fieldbook_events (
        campaign_id, project_id, entry_id, event, run_id, recorded_at, payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_PROJECT_SQL = f"""
    SELECT {_PROJECT_COLUMNS} FROM fieldbook_projects
    WHERE campaign_id=? AND project_id=?
"""
_SELECT_PROJECTS_SQL = f"""
    SELECT {_PROJECT_COLUMNS} FROM fieldbook_projects
    WHERE campaign_id=?
    ORDER BY selected DESC, updated_at DESC, project_id DESC
    LIMIT ?
"""
_SELECT_ALL_PROJECTS_SQL = f"""
    SELECT {_PROJECT_COLUMNS} FROM fieldbook_projects
    WHERE campaign_id=?
    ORDER BY selected DESC, updated_at DESC, project_id DESC
"""
_SELECT_ACTIVE_PROJECT_SQL = f"""
    SELECT {_PROJECT_COLUMNS} FROM fieldbook_projects
    WHERE campaign_id=? AND selected=1 AND status='active'
"""
_SELECT_ENTRIES_SQL = f"""
    SELECT {_ENTRY_COLUMNS} FROM fieldbook_entries
    WHERE campaign_id=? AND project_id=?
    ORDER BY sequence
"""
_SELECT_PROJECT_READ_SQL = f"""
    SELECT {_ENTRY_COLUMNS} FROM fieldbook_entries
    WHERE campaign_id=? AND project_id=?
    ORDER BY sequence DESC
"""
_SELECT_PROJECT_SEARCH_SQL = f"""
    SELECT {_ENTRY_COLUMNS} FROM fieldbook_entries
    WHERE campaign_id=? AND project_id=? AND content LIKE ? ESCAPE '\\'
    ORDER BY sequence DESC
"""
_SELECT_CAMPAIGN_SEARCH_SQL = f"""
    SELECT {_ENTRY_COLUMNS} FROM fieldbook_entries
    WHERE campaign_id=? AND content LIKE ? ESCAPE '\\'
    ORDER BY created_at DESC, entry_id DESC
"""
_SELECT_EVENTS_SQL = """
    SELECT event_id, campaign_id, project_id, entry_id, event, run_id,
           recorded_at, payload
    FROM fieldbook_events WHERE campaign_id=? AND project_id=? ORDER BY event_id
"""
_COUNT_EVENTS_SQL = "SELECT COUNT(*) AS total FROM fieldbook_events WHERE campaign_id=?"
_UPDATE_PROJECT_AFTER_ENTRY_SQL = """
    UPDATE fieldbook_projects
    SET entry_count=?, updated_at=?, latest_provenance=?
    WHERE campaign_id=? AND project_id=?
"""
_UPDATE_SUMMARY_SQL = """
    UPDATE fieldbook_projects SET summary=?, updated_at=?, latest_provenance=?
    WHERE campaign_id=? AND project_id=?
"""
_UPDATE_PROJECT_SELECTION_SQL = """
    UPDATE fieldbook_projects
    SET selected=?, updated_at=?, latest_provenance=?
    WHERE campaign_id=? AND project_id=?
"""
_UPDATE_STATUS_SQL = """
    UPDATE fieldbook_projects
    SET status=?, selected=?, updated_at=?, latest_provenance=?
    WHERE campaign_id=? AND project_id=?
"""
# pragma: no mutate end


class FieldbookTransitionError(ValueError):
    """A fieldbook operation referenced foreign, closed, or invalid state."""


class FieldbookNoOp(ValueError):
    """A valid fieldbook operation would make no canonical change."""


def create_fieldbook_schema(connection: sqlite3.Connection) -> None:
    connection.execute(_CREATE_PROJECTS_SQL)
    connection.execute(_CREATE_ENTRIES_SQL)
    connection.execute(_CREATE_EVENTS_SQL)
    for statement in _INDEX_SQL:
        connection.execute(statement)


# SQLite row lookup and canonical JSON encoding are representation adapters.
# Their public round trips are tested; mutating codec spelling mostly produces
# SQLite's case-insensitive aliases or JSON key-order equivalents.
# pragma: no mutate start
def _text(row: sqlite3.Row, name: str) -> str:
    value = row[name]
    if not isinstance(value, str):
        raise TypeError(f"fieldbook column {name!r} is not text")
    return value


def _optional_text(row: sqlite3.Row, name: str) -> str | None:
    value = row[name]
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"fieldbook column {name!r} is neither text nor null")


def _integer(row: sqlite3.Row, name: str) -> int:
    value = row[name]
    if value is None:
        raise TypeError(f"fieldbook column {name!r} is unexpectedly null")
    return int(value)


def _provenance_text(
    provenance: CanonicalFieldbookProvenance | None,
) -> str | None:
    payload = _provenance_payload(provenance)
    return None if payload is None else json.dumps(payload, sort_keys=True)


def _provenance_payload(
    provenance: CanonicalFieldbookProvenance | None,
) -> dict[str, Any] | None:
    return None if provenance is None else provenance.model_dump(mode="json")
# pragma: no mutate end


def _escape_like(query: str) -> str:
    escaped = query.replace("\\", "\\\\")
    return escaped.replace("%", "\\%").replace("_", "\\_")


def _short_summary(summary: str) -> str:
    return summary if len(summary) <= 160 else summary[:157] + "..."


class FieldbookStore:
    """Structured fieldbook operations over the owning MemoryStore connection."""

    __slots__ = (
        "_connection",
        "campaign_id",
        "_new_project_id",
        "_new_entry_id",
    )

    def __init__(
        self,
        connection: sqlite3.Connection,
        campaign_id: str,
        *,
        project_id_factory: Callable[[], str] = new_fieldbook_project_id,
        entry_id_factory: Callable[[], str] = new_fieldbook_entry_id,
    ) -> None:
        self._connection = connection
        self.campaign_id = campaign_id
        self._new_project_id = project_id_factory
        self._new_entry_id = entry_id_factory

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _append_event(
        self,
        *,
        project_id: str,
        entry_id: str | None,
        event: FieldbookLifecycleEvent,
        run_id: str,
        recorded_at: datetime,
        payload: dict[str, Any],
    ) -> None:
        self._connection.execute(
            _APPEND_EVENT_SQL,
            (
                self.campaign_id,
                project_id,
                entry_id,
                event.value,
                run_id,
                recorded_at.isoformat(),
                json.dumps(payload, sort_keys=True),  # pragma: no mutate
            ),
        )

    # The SQLite/Pydantic field map is a representation adapter. Store
    # round-trip tests assert every model surface and lifecycle invariant.
    # pragma: no mutate start
    @staticmethod
    def _project(row: sqlite3.Row) -> FieldbookProject:
        raw_provenance = _optional_text(row, "latest_provenance")
        return FieldbookProject(
            project_id=_text(row, "project_id"),
            campaign_id=_text(row, "campaign_id"),
            kind=FieldbookProjectKind(_text(row, "kind")),
            status=FieldbookProjectStatus(_text(row, "status")),
            title=_text(row, "title"),
            summary=_text(row, "summary"),
            selected=bool(_integer(row, "selected")),
            entry_count=_integer(row, "entry_count"),
            created_run_id=_text(row, "created_run_id"),
            created_at=datetime.fromisoformat(_text(row, "created_at")),
            updated_at=datetime.fromisoformat(_text(row, "updated_at")),
            latest_provenance=(
                None
                if raw_provenance is None
                else CanonicalFieldbookProvenance.model_validate_json(
                    raw_provenance
                )
            ),
        )

    @staticmethod
    def _entry(row: sqlite3.Row) -> FieldbookEntry:
        raw_provenance = _optional_text(row, "provenance")
        return FieldbookEntry(
            entry_id=_text(row, "entry_id"),
            project_id=_text(row, "project_id"),
            campaign_id=_text(row, "campaign_id"),
            sequence=_integer(row, "sequence"),
            kind=FieldbookEntryKind(_text(row, "kind")),
            content=_text(row, "content"),
            created_run_id=_text(row, "created_run_id"),
            created_at=datetime.fromisoformat(_text(row, "created_at")),
            provenance=(
                None
                if raw_provenance is None
                else CanonicalFieldbookProvenance.model_validate_json(
                    raw_provenance
                )
            ),
        )

    @staticmethod
    def _history_entry(row: sqlite3.Row) -> FieldbookHistoryEntry:
        return FieldbookHistoryEntry(
            event_id=_integer(row, "event_id"),
            campaign_id=_text(row, "campaign_id"),
            project_id=_text(row, "project_id"),
            entry_id=_optional_text(row, "entry_id"),
            event=FieldbookLifecycleEvent(_text(row, "event")),
            run_id=_text(row, "run_id"),
            recorded_at=datetime.fromisoformat(_text(row, "recorded_at")),
            payload=json.loads(_text(row, "payload")),
        )
    # pragma: no mutate end

    def create_project(
        self,
        *,
        run_id: str,
        kind: FieldbookProjectKind,
        title: str,
        summary: str,
        provenance: CanonicalFieldbookProvenance | None,
    ) -> FieldbookProject:
        now = self._now()
        project = FieldbookProject(
            project_id=self._new_project_id(),
            campaign_id=self.campaign_id,
            kind=kind,
            status=FieldbookProjectStatus.ACTIVE,
            title=title.strip(),
            summary=summary.strip(),
            created_run_id=run_id,
            created_at=now,
            updated_at=now,
            latest_provenance=provenance,
        )
        payload = {
            "project": project.model_dump(mode="json"),
            "provenance": _provenance_payload(provenance),
        }
        with self._connection:
            self._append_event(
                project_id=project.project_id,
                entry_id=None,
                event=FieldbookLifecycleEvent.CREATE_PROJECT,
                run_id=run_id,
                recorded_at=now,
                payload=payload,
            )
            self._connection.execute(
                _INSERT_PROJECT_SQL,
                (
                    project.project_id,
                    project.campaign_id,
                    project.kind.value,
                    project.status.value,
                    project.title,
                    project.summary,
                    0,
                    0,
                    project.created_run_id,
                    now.isoformat(),
                    now.isoformat(),
                    _provenance_text(provenance),
                ),
            )
        return project

    def get_project(self, project_id: str) -> FieldbookProject | None:
        row = self._connection.execute(
            _SELECT_PROJECT_SQL,
            (self.campaign_id, project_id),
        ).fetchone()
        return None if row is None else self._project(row)

    def _require_project(self, project_id: str) -> FieldbookProject:
        project = self.get_project(project_id)
        if project is None:
            # pragma: no mutate start - diagnostic text, not transition authority
            raise FieldbookTransitionError(
                f"No fieldbook project {project_id!r} exists in "
                f"campaign {self.campaign_id!r}."
            )
            # pragma: no mutate end
        return project

    @staticmethod
    def _require_open(project: FieldbookProject) -> None:
        if project.status in {
            FieldbookProjectStatus.COMPLETED,
            FieldbookProjectStatus.ABANDONED,
        }:
            # pragma: no mutate start - diagnostic text, not transition authority
            raise FieldbookTransitionError(
                f"Fieldbook project {project.project_id!r} is "
                f"{project.status.value} and cannot be changed."
            )
            # pragma: no mutate end

    def append_entry(
        self,
        *,
        run_id: str,
        project_id: str,
        kind: FieldbookEntryKind,
        content: str,
        provenance: CanonicalFieldbookProvenance | None,
    ) -> FieldbookEntry:
        project = self._require_project(project_id)
        if project.status is not FieldbookProjectStatus.ACTIVE:
            # pragma: no mutate start - diagnostic text, not transition authority
            raise FieldbookTransitionError(
                f"Fieldbook project {project_id!r} is {project.status.value}; "
                "resume it before appending."
            )
            # pragma: no mutate end
        now = self._now()
        entry = FieldbookEntry(
            entry_id=self._new_entry_id(),
            project_id=project_id,
            campaign_id=self.campaign_id,
            sequence=project.entry_count + 1,
            kind=kind,
            content=content.strip(),
            created_run_id=run_id,
            created_at=now,
            provenance=provenance,
        )
        with self._connection:
            self._append_event(
                project_id=project_id,
                entry_id=entry.entry_id,
                event=FieldbookLifecycleEvent.APPEND_ENTRY,
                run_id=run_id,
                recorded_at=now,
                payload={"entry": entry.model_dump(mode="json")},
            )
            self._connection.execute(
                _INSERT_ENTRY_SQL,
                (
                    entry.entry_id,
                    entry.project_id,
                    entry.campaign_id,
                    entry.sequence,
                    entry.kind.value,
                    entry.content,
                    entry.created_run_id,
                    now.isoformat(),
                    _provenance_text(provenance),
                ),
            )
            self._connection.execute(
                _UPDATE_PROJECT_AFTER_ENTRY_SQL,
                (
                    entry.sequence,
                    now.isoformat(),
                    _provenance_text(provenance),
                    self.campaign_id,
                    project_id,
                ),
            )
        return entry

    def update_summary(
        self,
        *,
        run_id: str,
        project_id: str,
        summary: str,
        provenance: CanonicalFieldbookProvenance | None,
    ) -> FieldbookProject:
        project = self._require_project(project_id)
        self._require_open(project)
        summary = summary.strip()
        if summary == project.summary:
            # pragma: no mutate start - diagnostic text, not transition authority
            raise FieldbookNoOp("The fieldbook project already has that summary.")
            # pragma: no mutate end
        now = self._now()
        with self._connection:
            self._append_event(
                project_id=project_id,
                entry_id=None,
                event=FieldbookLifecycleEvent.UPDATE_SUMMARY,
                run_id=run_id,
                recorded_at=now,
                payload={
                    "summary": summary,
                    "provenance": _provenance_payload(provenance),
                },
            )
            self._connection.execute(
                _UPDATE_SUMMARY_SQL,
                (
                    summary,
                    now.isoformat(),
                    _provenance_text(provenance),
                    self.campaign_id,
                    project_id,
                ),
            )
        return self._require_project(project_id)

    def select_project(
        self,
        *,
        run_id: str,
        project_id: str | None,
        provenance: CanonicalFieldbookProvenance | None,
    ) -> FieldbookProject | None:
        active = self.active_project()
        if project_id is None:
            if active is None:
                # pragma: no mutate start - diagnostic text
                raise FieldbookNoOp("No fieldbook project is selected.")
                # pragma: no mutate end
            now = self._now()
            with self._connection:
                self._append_event(
                    project_id=active.project_id,
                    entry_id=None,
                    event=FieldbookLifecycleEvent.CLEAR_SELECTION,
                    run_id=run_id,
                    recorded_at=now,
                    payload={"provenance": _provenance_payload(provenance)},
                )
                self._connection.execute(
                    _UPDATE_PROJECT_SELECTION_SQL,
                    (
                        0,
                        now.isoformat(),
                        _provenance_text(provenance),
                        self.campaign_id,
                        active.project_id,
                    ),
                )
            return None

        project = self._require_project(project_id)
        if project.status is not FieldbookProjectStatus.ACTIVE:
            # pragma: no mutate start - diagnostic text, not transition authority
            raise FieldbookTransitionError(
                "Only an active fieldbook project can be selected."
            )
            # pragma: no mutate end
        if project.selected:
            # pragma: no mutate start - diagnostic text, not transition authority
            raise FieldbookNoOp(
                f"Fieldbook project {project_id!r} is already selected."
            )
            # pragma: no mutate end
        now = self._now()
        with self._connection:
            if active is not None:
                self._append_event(
                    project_id=active.project_id,
                    entry_id=None,
                    event=FieldbookLifecycleEvent.CLEAR_SELECTION,
                    run_id=run_id,
                    recorded_at=now,
                    payload={"provenance": _provenance_payload(provenance)},
                )
                self._connection.execute(
                    _UPDATE_PROJECT_SELECTION_SQL,
                    (
                        0,
                        now.isoformat(),
                        _provenance_text(provenance),
                        self.campaign_id,
                        active.project_id,
                    ),
                )
            self._append_event(
                project_id=project_id,
                entry_id=None,
                event=FieldbookLifecycleEvent.SELECT_PROJECT,
                run_id=run_id,
                recorded_at=now,
                payload={"provenance": _provenance_payload(provenance)},
            )
            self._connection.execute(
                _UPDATE_PROJECT_SELECTION_SQL,
                (
                    1,
                    now.isoformat(),
                    _provenance_text(provenance),
                    self.campaign_id,
                    project_id,
                ),
            )
        return self._require_project(project_id)

    def set_status(
        self,
        *,
        run_id: str,
        project_id: str,
        status: FieldbookProjectStatus,
        provenance: CanonicalFieldbookProvenance | None,
    ) -> FieldbookProject:
        project = self._require_project(project_id)
        self._require_open(project)
        if status is project.status:
            # pragma: no mutate start - diagnostic text, not transition authority
            raise FieldbookNoOp(
                f"Fieldbook project {project_id!r} is already {status.value}."
            )
            # pragma: no mutate end
        now = self._now()
        selected = project.selected and status is FieldbookProjectStatus.ACTIVE
        with self._connection:
            self._append_event(
                project_id=project_id,
                entry_id=None,
                event=FieldbookLifecycleEvent.SET_PROJECT_STATUS,
                run_id=run_id,
                recorded_at=now,
                payload={
                    "status": status.value,
                    "provenance": _provenance_payload(provenance),
                },
            )
            self._connection.execute(
                _UPDATE_STATUS_SQL,
                (
                    status.value,
                    int(selected),
                    now.isoformat(),
                    _provenance_text(provenance),
                    self.campaign_id,
                    project_id,
                ),
            )
        return self._require_project(project_id)

    def list_projects(self, *, limit: int = 8) -> list[FieldbookProjectIndex]:
        if limit < 0:
            # pragma: no mutate start - diagnostic text
            raise ValueError("fieldbook project index limit must be non-negative")
            # pragma: no mutate end
        rows = self._connection.execute(
            _SELECT_PROJECTS_SQL,
            (self.campaign_id, limit),
        ).fetchall()
        return [
            FieldbookProjectIndex(
                project_id=project.project_id,
                title=project.title,
                kind=project.kind,
                status=project.status,
                short_summary=_short_summary(project.summary),
                entry_count=project.entry_count,
                updated_at=project.updated_at,
                selected=project.selected,
            )
            for project in (self._project(row) for row in rows)
        ]

    def all_projects(self) -> list[FieldbookProject]:
        """Return the full campaign projection for read-only operator export."""

        return [
            self._project(row)
            for row in self._connection.execute(
                _SELECT_ALL_PROJECTS_SQL,
                (self.campaign_id,),
            ).fetchall()
        ]

    def active_project(self) -> FieldbookProject | None:
        row = self._connection.execute(
            _SELECT_ACTIVE_PROJECT_SQL,
            (self.campaign_id,),
        ).fetchone()
        return None if row is None else self._project(row)

    def active_project_summary(self) -> ActiveFieldbookProject | None:
        project = self.active_project()
        if project is None:
            return None
        return ActiveFieldbookProject(
            project_id=project.project_id,
            title=project.title,
            kind=project.kind,
            status=FieldbookProjectStatus.ACTIVE,
            summary=project.summary,
            entry_count=project.entry_count,
            updated_at=project.updated_at,
        )

    def entries(self, project_id: str) -> list[FieldbookEntry]:
        self._require_project(project_id)
        return [
            self._entry(row)
            for row in self._connection.execute(
                _SELECT_ENTRIES_SQL,
                (self.campaign_id, project_id),
            ).fetchall()
        ]

    def read(
        self,
        *,
        project_id: str | None,
        query: str | None,
        limit: int,
    ) -> FieldbookReadResult:
        if project_id is None and query is None:
            # pragma: no mutate start - diagnostic text
            raise ValueError("fieldbook read requires project_id or query")
            # pragma: no mutate end
        if limit < 1 or limit > 8:
            # pragma: no mutate start - diagnostic text
            raise ValueError("fieldbook read limit must be between one and eight")
            # pragma: no mutate end
        if query is not None:
            query = query.strip()
            if not query:
                # pragma: no mutate start - diagnostic text
                raise ValueError("fieldbook query must not be blank")
                # pragma: no mutate end

        project = (
            None if project_id is None else self._require_project(project_id)
        )
        if project_id is not None and query is None:
            rows = self._connection.execute(
                _SELECT_PROJECT_READ_SQL,
                (self.campaign_id, project_id),
            ).fetchall()
        elif project_id is not None:
            assert query is not None
            rows = self._connection.execute(
                _SELECT_PROJECT_SEARCH_SQL,
                (
                    self.campaign_id,
                    project_id,
                    f"%{_escape_like(query)}%",
                ),
            ).fetchall()
        else:
            assert query is not None
            rows = self._connection.execute(
                _SELECT_CAMPAIGN_SEARCH_SQL,
                (self.campaign_id, f"%{_escape_like(query)}%"),
            ).fetchall()
        shown = [self._entry(row) for row in rows[:limit]]
        if project_id is not None:
            shown.reverse()
        selector = (
            f"project {project_id!r}"
            if query is None
            else f"query {query!r}"
            if project_id is None
            else f"project {project_id!r} and query {query!r}"
        )
        return FieldbookReadResult(
            project_id=project_id,
            query=query,
            project=project,
            entries=shown,
            matched=len(rows),
            truncated=len(rows) > limit,
            reason=(
                f"{len(rows)} fieldbook entries match {selector}; "
                f"{min(len(rows), limit)} shown."
            ),
        )

    def history(self, project_id: str) -> list[FieldbookHistoryEntry]:
        self._require_project(project_id)
        return [
            self._history_entry(row)
            for row in self._connection.execute(
                _SELECT_EVENTS_SQL,
                (self.campaign_id, project_id),
            ).fetchall()
        ]

    def event_count(self) -> int:
        row = self._connection.execute(
            _COUNT_EVENTS_SQL,
            (self.campaign_id,),
        ).fetchone()
        assert row is not None  # SELECT COUNT always yields exactly one row.
        return int(row[0])


def render_fieldbook_markdown(store: FieldbookStore) -> str:
    """Disposable human-readable projection; never an input to the store."""

    lines = [f"# Fieldbook: {store.campaign_id}", ""]
    for project in store.all_projects():
        lines.extend(
            [
                f"## {project.title}",
                "",
                (
                    f"`{project.project_id}` · {project.kind.value} · "
                    f"{project.status.value}"
                ),
                "",
                project.summary,
                "",
            ]
        )
        for entry in store.entries(project.project_id):
            lines.extend(
                [
                    f"- `{entry.entry_id}` {entry.kind.value}: {entry.content}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
