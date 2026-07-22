from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import MemoryKind, MemoryRecord, MemoryWrite


class MemoryStore:
    def __init__(self, path: Path, namespace: str) -> None:
        self.path = path
        self.namespace = namespace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
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
                UNIQUE(namespace, kind, content)
            )
            """
        )
        self._connection.commit()

    def add(self, run_id: str, write: MemoryWrite) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self._connection.execute(
            """
            INSERT INTO memories (
                namespace, run_id, kind, content, salience, evidence,
                created_at, last_accessed_at, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(namespace, kind, content) DO UPDATE SET
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
            ),
        )
        self._connection.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = self._connection.execute(
            "SELECT id FROM memories WHERE namespace=? AND kind=? AND content=?",
            (self.namespace, write.kind.value, write.content.strip()),
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
    ) -> list[MemoryRecord]:
        parameters: list[object] = [self.namespace, minimum_salience]
        where = "namespace=? AND active=1 AND salience>=?"
        if query:
            where += " AND content LIKE ?"
            parameters.append(f"%{query}%")
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT id, namespace, run_id, kind, content, salience, evidence,
                   created_at, last_accessed_at
            FROM memories
            WHERE {where}
            ORDER BY salience DESC, last_accessed_at DESC, id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
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
