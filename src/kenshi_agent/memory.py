from __future__ import annotations

import sqlite3
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .models import MemoryKind, MemoryRecord, MemoryWrite

# Mutmut understands Python expressions, not SQL. Its SQL-string mutations are
# either SQLite-equivalent case changes or deliberately invalid statements.
# Keep SQL declarative here and test its observable storage contract below.
# pragma: no mutate start
_JOURNAL_MODE_SQL = "PRAGMA journal_mode=WAL"
_FOREIGN_KEYS_SQL = "PRAGMA foreign_keys=ON"
_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        namespace TEXT NOT NULL,
        run_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        salience REAL NOT NULL,
        evidence TEXT,
        created_at TEXT NOT NULL,
        last_accessed_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        target_id TEXT NOT NULL DEFAULT '',
        UNIQUE(namespace, kind, content, target_id)
    )
"""
_TABLE_INFO_SQL = "PRAGMA table_info(memories)"
_ALTER_LEGACY_TABLE_SQL = "ALTER TABLE memories RENAME TO memories_unscoped"
_MIGRATE_LEGACY_ROWS_SQL = """
    INSERT INTO memories (
        id, namespace, run_id, kind, content, salience, evidence,
        created_at, last_accessed_at, active, target_id
    )
    SELECT
        id, namespace, run_id, kind, content, salience, evidence,
        created_at, last_accessed_at, active, ''
    FROM memories_unscoped
"""
_DROP_LEGACY_TABLE_SQL = "DROP TABLE memories_unscoped"
_UPSERT_SQL = """
    INSERT INTO memories (
        namespace, run_id, kind, content, salience, evidence,
        created_at, last_accessed_at, active, target_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    ON CONFLICT(namespace, kind, content, target_id) DO UPDATE SET
        salience = MAX(memories.salience, excluded.salience),
        evidence = COALESCE(excluded.evidence, memories.evidence),
        last_accessed_at = excluded.last_accessed_at,
        active = 1
"""
_RESOLVE_UPSERT_SQL = """
    SELECT id FROM memories
    WHERE namespace=? AND kind=? AND content=? AND target_id=?
"""
_GENERAL_WHERE_SQL = "namespace=? AND active=1 AND salience>=? AND target_id=''"
_QUERY_FILTER_SQL = " AND content LIKE ?"
_GENERAL_RECALL_SQL = """
    SELECT id, namespace, run_id, kind, content, salience, evidence,
           created_at, last_accessed_at, target_id
    FROM memories
    WHERE {where}
    ORDER BY salience DESC, last_accessed_at DESC, id DESC
    LIMIT ?
"""
_ENTITY_WHERE_SQL = "namespace=? AND active=1 AND target_id IN ({placeholders})"
_ENTITY_RECALL_SQL = """
    SELECT id, namespace, run_id, kind, content, salience, evidence,
           created_at, last_accessed_at, target_id
    FROM memories
    WHERE {where}
    ORDER BY salience DESC, last_accessed_at DESC, id DESC
    LIMIT ?
"""
_UPDATE_ACCESS_SQL = (
    "UPDATE memories SET last_accessed_at=? WHERE id IN ({placeholders})"
)
# pragma: no mutate end

_SQLiteValue = bytes | float | int | str | None


def _row_value(row: sqlite3.Row, column: str) -> _SQLiteValue:
    """Read an exact selected column instead of SQLite Row's case-folded lookup."""

    value: object = dict(row)[column]
    if value is not None and not isinstance(value, (bytes, float, int, str)):
        raise TypeError(  # mutation: diagnostic-only
            f"unsupported SQLite value for {column!r}"
        )
    return value


def _row_int(row: sqlite3.Row, column: str) -> int:
    value = _row_value(row, column)
    if value is None:
        raise TypeError(  # mutation: diagnostic-only
            f"SQLite column {column!r} is unexpectedly null"
        )
    return int(value)


def _row_float(row: sqlite3.Row, column: str) -> float:
    value = _row_value(row, column)
    if value is None:
        raise TypeError(  # mutation: diagnostic-only
            f"SQLite column {column!r} is unexpectedly null"
        )
    return float(value)


def _row_text(row: sqlite3.Row, column: str) -> str:
    value = _row_value(row, column)
    if not isinstance(value, str):
        raise TypeError(  # mutation: diagnostic-only
            f"SQLite column {column!r} is not text"
        )
    return value


def _row_optional_text(row: sqlite3.Row, column: str) -> str | None:
    value = _row_value(row, column)
    if value is None or isinstance(value, str):
        return value
    raise TypeError(  # mutation: diagnostic-only
        f"SQLite column {column!r} is neither text nor null"
    )


def _partition_target_ids(
    target_ids: Sequence[str],
    chunk_size: int,
) -> list[Sequence[str]]:
    """Partition exact identities once, preserving every value and order."""

    if chunk_size <= 0:
        raise ValueError("target ID chunk size must be positive")  # mutation: diagnostic-only
    return [
        target_ids[offset : offset + chunk_size]
        for offset in range(0, len(target_ids), chunk_size)
    ]


class MemoryStore:
    _TARGET_QUERY_CHUNK_SIZE = 500

    def __init__(self, path: Path, namespace: str) -> None:
        self.path = path
        self.namespace = namespace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(_JOURNAL_MODE_SQL)
        self._connection.execute(_FOREIGN_KEYS_SQL)
        self._create_table()
        self._migrate_unscoped_table()
        self._connection.commit()

    def _create_table(self) -> None:
        self._connection.execute(_CREATE_TABLE_SQL)

    def _migrate_unscoped_table(self) -> None:
        columns = {
            _row_text(row, "name")
            for row in self._connection.execute(_TABLE_INFO_SQL).fetchall()
        }
        if "target_id" in columns:
            return

        # Adding a column would leave SQLite's old three-column UNIQUE
        # constraint in force, silently merging the same fact learned about two
        # different entities. Rebuild once so target identity becomes part of
        # ownership while every legacy row remains an unbound general memory.
        with self._connection:
            self._connection.execute(_ALTER_LEGACY_TABLE_SQL)
            self._create_table()
            self._connection.execute(_MIGRATE_LEGACY_ROWS_SQL)
            self._connection.execute(_DROP_LEGACY_TABLE_SQL)

    def add(self, run_id: str, write: MemoryWrite) -> int:
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            _UPSERT_SQL,
            (
                self.namespace,
                run_id,
                write.kind.value,
                write.content.strip(),
                write.salience,
                write.evidence,
                now,
                now,
                write.target_id or "",
            ),
        )
        self._connection.commit()
        # sqlite3.lastrowid is the last INSERT on the connection, not
        # necessarily the row updated by this upsert. After inserting another
        # memory, updating an older one otherwise reports the intervening ID.
        row = self._connection.execute(
            _RESOLVE_UPSERT_SQL,
            (
                self.namespace,
                write.kind.value,
                write.content.strip(),
                write.target_id or "",
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError(  # mutation: diagnostic-only
                "Memory upsert succeeded but could not resolve its id."
            )
        return _row_int(row, "id")

    def recall(
        self,
        *,
        limit: int = 12,
        minimum_salience: float = 0.0,
        query: str | None = None,
        target_ids: Collection[str] = (),
        entity_limit: int = 0,
    ) -> list[MemoryRecord]:
        if limit < 0 or entity_limit < 0:
            raise ValueError(  # mutation: diagnostic-only
                "memory recall limits must be non-negative"
            )

        # General recall contains only unbound knowledge. Entity-bound facts
        # must never leak onto a different same-named or later-session entity;
        # they reappear only through an exact current target ID.
        parameters: list[object] = [self.namespace, minimum_salience]
        where = _GENERAL_WHERE_SQL
        if query:
            where += _QUERY_FILTER_SQL
            parameters.append(f"%{query}%")
        parameters.append(limit)
        general_rows = self._connection.execute(
            _GENERAL_RECALL_SQL.format(where=where),
            parameters,
        ).fetchall()

        entity_rows: list[sqlite3.Row] = []
        exact_target_ids = sorted({target_id for target_id in target_ids if target_id})
        if entity_limit:
            if exact_target_ids:
                candidates: dict[int, sqlite3.Row] = {}
                for chunk in _partition_target_ids(
                    exact_target_ids,
                    self._TARGET_QUERY_CHUNK_SIZE,
                ):
                    placeholders = ",".join("?" for _ in chunk)
                    entity_parameters: list[object] = [self.namespace, *chunk]
                    entity_where = _ENTITY_WHERE_SQL.format(
                        placeholders=placeholders
                    )
                    if query:
                        entity_where += _QUERY_FILTER_SQL
                        entity_parameters.append(f"%{query}%")
                    entity_parameters.append(entity_limit)
                    rows = self._connection.execute(
                        _ENTITY_RECALL_SQL.format(where=entity_where),
                        entity_parameters,
                    ).fetchall()
                    candidates.update(
                        (_row_int(row, "id"), row) for row in rows
                    )
                entity_rows = sorted(
                    candidates.values(),
                    key=lambda row: (
                        _row_float(row, "salience"),
                        _row_text(row, "last_accessed_at"),
                        _row_int(row, "id"),
                    ),
                    reverse=True,
                )[:entity_limit]

        # Exact target matches lead so downstream bounded consumers cannot
        # accidentally slice them away in favor of general salience.
        rows = [*entity_rows, *general_rows]
        now = datetime.now(UTC).isoformat()
        if rows:
            ids = [_row_int(row, "id") for row in rows]
            placeholders = ",".join("?" for _ in ids)
            self._connection.execute(
                _UPDATE_ACCESS_SQL.format(placeholders=placeholders),
                [now, *ids],
            )
            self._connection.commit()
        return [
            MemoryRecord(
                id=_row_int(row, "id"),
                namespace=_row_text(row, "namespace"),
                run_id=_row_text(row, "run_id"),
                kind=MemoryKind(_row_text(row, "kind")),
                content=_row_text(row, "content"),
                salience=_row_float(row, "salience"),
                evidence=_row_optional_text(row, "evidence"),
                target_id=_row_optional_text(row, "target_id") or None,
                created_at=datetime.fromisoformat(
                    _row_text(row, "created_at")
                ),
                last_accessed_at=datetime.fromisoformat(
                    _row_text(row, "last_accessed_at")
                ),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
