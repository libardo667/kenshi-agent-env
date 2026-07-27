from __future__ import annotations

import sqlite3
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path

from .models import MemoryKind, MemoryRecord, MemoryWrite


class MemoryStore:
    _TARGET_QUERY_CHUNK_SIZE = 500

    def __init__(self, path: Path, namespace: str) -> None:
        self.path = path
        self.namespace = namespace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_table()
        self._migrate_unscoped_table()
        self._connection.commit()

    def _create_table(self) -> None:
        self._connection.execute(
            """
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
        )

    def _migrate_unscoped_table(self) -> None:
        columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "target_id" in columns:
            return

        # Adding a column would leave SQLite's old three-column UNIQUE
        # constraint in force, silently merging the same fact learned about two
        # different entities. Rebuild once so target identity becomes part of
        # ownership while every legacy row remains an unbound general memory.
        with self._connection:
            self._connection.execute("ALTER TABLE memories RENAME TO memories_unscoped")
            self._create_table()
            self._connection.execute(
                """
                INSERT INTO memories (
                    id, namespace, run_id, kind, content, salience, evidence,
                    created_at, last_accessed_at, active, target_id
                )
                SELECT
                    id, namespace, run_id, kind, content, salience, evidence,
                    created_at, last_accessed_at, active, ''
                FROM memories_unscoped
                """
            )
            self._connection.execute("DROP TABLE memories_unscoped")

    def add(self, run_id: str, write: MemoryWrite) -> int:
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            """
            INSERT INTO memories (
                namespace, run_id, kind, content, salience, evidence,
                created_at, last_accessed_at, active, target_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(namespace, kind, content, target_id) DO UPDATE SET
                salience = MAX(memories.salience, excluded.salience),
                evidence = COALESCE(excluded.evidence, memories.evidence),
                last_accessed_at = excluded.last_accessed_at,
                active = 1
            """,
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
            """
            SELECT id FROM memories
            WHERE namespace=? AND kind=? AND content=? AND target_id=?
            """,
            (
                self.namespace,
                write.kind.value,
                write.content.strip(),
                write.target_id or "",
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Memory upsert succeeded but could not resolve its id.")
        return int(row["id"])

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
            raise ValueError("memory recall limits must be non-negative")

        # General recall contains only unbound knowledge. Entity-bound facts
        # must never leak onto a different same-named or later-session entity;
        # they reappear only through an exact current target ID.
        parameters: list[object] = [self.namespace, minimum_salience]
        where = "namespace=? AND active=1 AND salience>=? AND target_id=''"
        if query:
            where += " AND content LIKE ?"
            parameters.append(f"%{query}%")
        parameters.append(limit)
        general_rows = self._connection.execute(
            f"""
            SELECT id, namespace, run_id, kind, content, salience, evidence,
                   created_at, last_accessed_at, target_id
            FROM memories
            WHERE {where}
            ORDER BY salience DESC, last_accessed_at DESC, id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

        entity_rows: list[sqlite3.Row] = []
        exact_target_ids = sorted({target_id for target_id in target_ids if target_id})
        if entity_limit > 0 and exact_target_ids:
            candidates: dict[int, sqlite3.Row] = {}
            for offset in range(0, len(exact_target_ids), self._TARGET_QUERY_CHUNK_SIZE):
                chunk = exact_target_ids[offset : offset + self._TARGET_QUERY_CHUNK_SIZE]
                placeholders = ",".join("?" for _ in chunk)
                entity_parameters: list[object] = [self.namespace, *chunk]
                entity_where = (
                    f"namespace=? AND active=1 AND target_id IN ({placeholders})"
                )
                if query:
                    entity_where += " AND content LIKE ?"
                    entity_parameters.append(f"%{query}%")
                entity_parameters.append(entity_limit)
                rows = self._connection.execute(
                    f"""
                    SELECT id, namespace, run_id, kind, content, salience, evidence,
                           created_at, last_accessed_at, target_id
                    FROM memories
                    WHERE {entity_where}
                    ORDER BY salience DESC, last_accessed_at DESC, id DESC
                    LIMIT ?
                    """,
                    entity_parameters,
                ).fetchall()
                candidates.update((int(row["id"]), row) for row in rows)
            entity_rows = sorted(
                candidates.values(),
                key=lambda row: (
                    float(row["salience"]),
                    str(row["last_accessed_at"]),
                    int(row["id"]),
                ),
                reverse=True,
            )[:entity_limit]

        # Exact target matches lead so downstream bounded consumers cannot
        # accidentally slice them away in favor of general salience.
        rows = [*entity_rows, *general_rows]
        now = datetime.now(UTC).isoformat()
        if rows:
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            self._connection.execute(
                f"UPDATE memories SET last_accessed_at=? WHERE id IN ({placeholders})",
                [now, *ids],
            )
            self._connection.commit()
        return [
            MemoryRecord(
                id=int(row["id"]),
                namespace=str(row["namespace"]),
                run_id=str(row["run_id"]),
                kind=MemoryKind(str(row["kind"])),
                content=str(row["content"]),
                salience=float(row["salience"]),
                evidence=str(row["evidence"]) if row["evidence"] is not None else None,
                target_id=str(row["target_id"]) if row["target_id"] else None,
                created_at=datetime.fromisoformat(str(row["created_at"])),
                last_accessed_at=datetime.fromisoformat(str(row["last_accessed_at"])),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
