"""The canonical durable-continuity store.

One SQLite database, one authority. Inside it, two things with different jobs:

- `memory_events` is append-only history. Every keep, reinforce, resolve,
  supersede, retract, and delivery is a row that is never rewritten and never
  deleted. It is what the store actually knows.
- `memories` is a projection of that history, kept current inside the same
  transaction that appends to it, and rebuildable from scratch. It exists so
  recall is a query rather than a replay.

If the projection ever disagrees with the history, the history wins, and
`rebuild_projection()` is how you say so. Nothing else may write either table.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import TypeAdapter

from .campaign import CampaignScope, CampaignScopeOrigin, legacy_campaign_id
from .core.continuity import (
    CanonicalCompactionProvenance,
    CanonicalMemoryProvenance,
    MemoryAuthorship,
    MemoryCompactionCandidate,
    MemoryHistoryEntry,
    MemoryKind,
    MemoryLifecycleEvent,
    MemoryProvenance,
    MemoryRecord,
    MemoryResolutionDisposition,
    MemorySearchResult,
    MemoryStatus,
    RecallTier,
    new_memory_id,
)
from .fieldbook import FieldbookStore, create_fieldbook_schema
from .memory_compaction import (
    MemoryCompactionError,
    validate_lossless_compaction_candidate,
)

SCHEMA_VERSION = 4
_MEMORY_PROVENANCE_ADAPTER: TypeAdapter[MemoryProvenance] = TypeAdapter(
    MemoryProvenance
)

# Mutmut understands Python expressions, not SQL. Its SQL-string mutations are
# either SQLite-equivalent case changes or deliberately invalid statements.
# Keep SQL declarative here and test its observable storage contract below.
# pragma: no mutate start
_JOURNAL_MODE_SQL = "PRAGMA journal_mode=WAL"
_BEGIN_IMMEDIATE_SQL = "BEGIN IMMEDIATE"
_FOREIGN_KEYS_SQL = "PRAGMA foreign_keys=ON"
_FOREIGN_KEYS_STATE_SQL = "PRAGMA foreign_keys"
_TABLE_INFO_SQL = "PRAGMA table_info(memories)"
_LEGACY_NAMESPACES_SQL = "SELECT DISTINCT namespace FROM memories"
_LEGACY_ROWS_SQL = """
    SELECT namespace, run_id, kind, content, salience, evidence,
           created_at, active, target_id
    FROM memories
    ORDER BY id
"""
_LEGACY_ROWS_UNSCOPED_SQL = """
    SELECT namespace, run_id, kind, content, salience, evidence,
           created_at, active, '' AS target_id
    FROM memories
    ORDER BY id
"""
_DROP_LEGACY_TABLE_SQL = "DROP TABLE memories"

_CREATE_META_SQL = """
    CREATE TABLE IF NOT EXISTS continuity_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
"""
_CREATE_CAMPAIGNS_SQL = """
    CREATE TABLE IF NOT EXISTS campaigns (
        campaign_id TEXT PRIMARY KEY,
        origin TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
"""
_CREATE_EVENTS_SQL = """
    CREATE TABLE IF NOT EXISTS memory_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
        memory_id TEXT NOT NULL,
        event TEXT NOT NULL,
        run_id TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        payload TEXT NOT NULL
    )
"""
_CREATE_MEMORIES_SQL = """
    CREATE TABLE IF NOT EXISTS memories (
        memory_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        content TEXT NOT NULL,
        normalized_key TEXT NOT NULL,
        target_id TEXT NOT NULL DEFAULT '',
        salience REAL NOT NULL,
        grounding TEXT,
        latest_provenance TEXT,
        authorship TEXT NOT NULL,
        created_run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reinforced_at TEXT,
        resolved_at TEXT,
        superseded_at TEXT,
        last_delivered_at TEXT,
        reinforcement_count INTEGER NOT NULL DEFAULT 0,
        supersedes_id TEXT,
        superseded_by_id TEXT,
        resolution_reason TEXT,
        resolution_disposition TEXT
    )
"""
_MIGRATE_V2_ADD_PROVENANCE_SQL = (
    "ALTER TABLE memories ADD COLUMN latest_provenance TEXT"
)
_MIGRATE_V2_ADD_DISPOSITION_SQL = (
    "ALTER TABLE memories ADD COLUMN resolution_disposition TEXT"
)
_INDEX_SQL = (
    # Only *active* records compete for an identity. A retracted belief and its
    # later restatement are two records with the same words, and both are real.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS memories_campaign_active_key
    ON memories (campaign_id, normalized_key) WHERE status = 'active'
    """,
    """
    CREATE INDEX IF NOT EXISTS memories_campaign_rank
    ON memories (campaign_id, status, target_id, salience DESC, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS memories_campaign_target
    ON memories (campaign_id, status, target_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS memory_events_campaign_order
    ON memory_events (campaign_id, event_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS memory_events_memory_order
    ON memory_events (memory_id, event_id)
    """,
)
_SET_META_SQL = "INSERT OR REPLACE INTO continuity_meta (key, value) VALUES (?, ?)"
_GET_META_SQL = "SELECT value FROM continuity_meta WHERE key=?"
_CONTINUITY_META_EXISTS_SQL = (
    "SELECT 1 FROM sqlite_master "
    "WHERE type='table' AND name='continuity_meta'"
)
_UPSERT_CAMPAIGN_SQL = """
    INSERT INTO campaigns (campaign_id, origin, created_at) VALUES (?, ?, ?)
    ON CONFLICT(campaign_id) DO NOTHING
"""
_APPEND_EVENT_SQL = """
    INSERT INTO memory_events
        (campaign_id, memory_id, event, run_id, recorded_at, payload)
    VALUES (?, ?, ?, ?, ?, ?)
"""
_INSERT_MEMORY_SQL = """
    INSERT INTO memories (
        memory_id, campaign_id, kind, status, content, normalized_key, target_id,
        salience, grounding, latest_provenance, authorship, created_run_id, created_at,
        reinforced_at, resolved_at, superseded_at, last_delivered_at,
        reinforcement_count, supersedes_id, superseded_by_id, resolution_reason,
        resolution_disposition
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_MEMORY_COLUMNS = """
    memory_id, campaign_id, kind, status, content, target_id, salience,
    grounding, latest_provenance, authorship, created_run_id, created_at,
    reinforced_at, resolved_at, superseded_at, last_delivered_at,
    reinforcement_count, supersedes_id, superseded_by_id, resolution_reason,
    resolution_disposition
"""
_SELECT_MEMORY_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories WHERE campaign_id=? AND memory_id=?
"""
_SELECT_ACTIVE_BY_KEY_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories
    WHERE campaign_id=? AND normalized_key=? AND status='active'
"""
_SELECT_ALL_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories WHERE campaign_id=? ORDER BY memory_id
"""
# Ordered by what the agent declared, how often it explicitly reinforced, and
# when the record was made - never by when it was last read. Ties break on the
# runtime-owned ID so repeated recalls of identical records cannot reorder.
_RANK_SQL = """
    ORDER BY salience DESC, reinforcement_count DESC, created_at DESC,
             memory_id DESC
"""
_GENERAL_WHERE_SQL = (
    "campaign_id=? AND status='active' AND salience>=? AND target_id=''"
)
_QUERY_FILTER_SQL = " AND content LIKE ?"
_GENERAL_RECALL_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories
    WHERE {{where}}
    {_RANK_SQL}
    LIMIT ?
"""
_ENTITY_WHERE_SQL = (
    "campaign_id=? AND status='active' AND target_id IN ({placeholders})"
)
_ENTITY_RECALL_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories
    WHERE {{where}}
    {_RANK_SQL}
    LIMIT ?
"""
# Tiers select without a LIMIT so the omitted count is the truth rather than
# "at least the budget". Each tier is bounded in Python, after exclusion.
_COMMITMENT_TIER_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories
    WHERE campaign_id=? AND status='active' AND kind='commitment'
    {_RANK_SQL}
"""
_TARGET_TIER_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories
    WHERE campaign_id=? AND status='active' AND target_id IN ({{placeholders}})
    {_RANK_SQL}
"""
_HYPOTHESIS_TIER_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories
    WHERE campaign_id=? AND status='active' AND kind='hypothesis'
    {_RANK_SQL}
"""
# Commitments and hypotheses have their own tiers. Letting an overflowing
# commitment tier spill into the general budget would mean the loudest tier
# quietly eats the others, which is what tiering exists to prevent.
_GENERAL_TIER_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories
    WHERE campaign_id=? AND status='active' AND salience>=? AND target_id=''
      AND kind NOT IN ('commitment', 'hypothesis')
    {_RANK_SQL}
"""
_SEARCH_SQL = f"""
    SELECT {_MEMORY_COLUMNS} FROM memories
    WHERE campaign_id=? AND status='active' AND content LIKE ? ESCAPE '\\'
    {_RANK_SQL}
"""
_UPDATE_REINFORCE_SQL = """
    UPDATE memories
    SET salience=?, grounding=COALESCE(?, grounding), reinforced_at=?,
        latest_provenance=COALESCE(?, latest_provenance),
        reinforcement_count=reinforcement_count + 1
    WHERE campaign_id=? AND memory_id=?
"""
_UPDATE_RESOLVE_SQL = """
    UPDATE memories SET status=?, resolved_at=?, resolution_reason=?,
        resolution_disposition=?, grounding=COALESCE(?, grounding),
        latest_provenance=COALESCE(?, latest_provenance)
    WHERE campaign_id=? AND memory_id=?
"""
_UPDATE_SUPERSEDE_SQL = """
    UPDATE memories SET status=?, superseded_at=?, superseded_by_id=?,
        latest_provenance=COALESCE(?, latest_provenance)
    WHERE campaign_id=? AND memory_id=?
"""
_UPDATE_COMPACTION_SOURCE_SQL = """
    UPDATE memories SET status=?, superseded_at=?, superseded_by_id=?,
        latest_provenance=?
    WHERE campaign_id=? AND memory_id=? AND status='active'
"""
_UPDATE_RETRACT_SQL = """
    UPDATE memories SET status=?, resolved_at=?, resolution_reason=?,
        latest_provenance=COALESCE(?, latest_provenance)
    WHERE campaign_id=? AND memory_id=?
"""
_RECORD_DELIVERY_SQL = """
    UPDATE memories SET last_delivered_at=?
    WHERE campaign_id=? AND memory_id IN ({placeholders})
"""
_SELECT_HISTORY_SQL = """
    SELECT event_id, campaign_id, memory_id, event, run_id, recorded_at, payload
    FROM memory_events WHERE campaign_id=? AND memory_id=? ORDER BY event_id
"""
_SELECT_CAMPAIGN_EVENTS_SQL = """
    SELECT event_id, campaign_id, memory_id, event, run_id, recorded_at, payload
    FROM memory_events WHERE campaign_id=? ORDER BY event_id
"""
_COUNT_EVENTS_SQL = "SELECT COUNT(*) AS total FROM memory_events WHERE campaign_id=?"
_DELETE_PROJECTION_SQL = "DELETE FROM memories WHERE campaign_id=?"
_SELECT_CAMPAIGNS_SQL = """
    SELECT campaign_id, origin, created_at FROM campaigns ORDER BY campaign_id
"""
# pragma: no mutate end

_SQLiteValue = bytes | float | int | str | None
_Identity = TypeVar("_Identity", int, str)

_CLOSED_STATUSES = frozenset(
    {MemoryStatus.RESOLVED, MemoryStatus.SUPERSEDED, MemoryStatus.RETRACTED}
)


@dataclass(frozen=True, slots=True)
class RecallBudget:
    """How much of a bounded context window each tier may spend.

    Separate numbers rather than one total, because the point of tiering is
    that an open commitment does not compete with general knowledge for the
    same slot.
    """

    commitments: int
    current_target: int
    open_hypotheses: int
    general: int
    minimum_salience: float = 0.0

    def of(self, tier: RecallTier) -> int:
        return {
            RecallTier.COMMITMENT: self.commitments,
            RecallTier.CURRENT_TARGET: self.current_target,
            RecallTier.OPEN_HYPOTHESIS: self.open_hypotheses,
            RecallTier.GENERAL: self.general,
        }[tier]


@dataclass(frozen=True, slots=True)
class TieredRecall:
    """What recall chose, why, and what it left behind."""

    records: list[MemoryRecord]
    tiers: dict[str, RecallTier]
    omitted: dict[RecallTier, int]

    def tier_of(self, memory_id: str) -> RecallTier:
        return self.tiers[memory_id]

    @property
    def total_omitted(self) -> int:
        return sum(self.omitted.values())


class MemoryTransitionError(ValueError):
    """A lifecycle transition was refused: unknown, foreign, or already closed."""


class MemoryStoreError(RuntimeError):
    """The store could not be opened or migrated safely."""


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


def _row_time(row: sqlite3.Row, column: str) -> datetime | None:
    value = _row_optional_text(row, column)
    return None if value is None else datetime.fromisoformat(value)


def _partition_target_ids(
    target_ids: Sequence[_Identity],
    chunk_size: int,
) -> list[Sequence[_Identity]]:
    """Partition exact identities once, preserving every value and order."""

    if chunk_size <= 0:
        raise ValueError("target ID chunk size must be positive")  # mutation: diagnostic-only
    return [
        target_ids[offset : offset + chunk_size]
        for offset in range(0, len(target_ids), chunk_size)
    ]


def _rank_key(row: sqlite3.Row) -> tuple[float, int, str, str]:
    """The Python mirror of `_RANK_SQL`, for rows merged across queries."""

    return (
        _row_float(row, "salience"),
        _row_int(row, "reinforcement_count"),
        _row_text(row, "created_at"),
        _row_text(row, "memory_id"),
    )


def _escape_like(query: str) -> str:
    """Make a planner's literal query safe for `LIKE ... ESCAPE`."""

    escaped = query.replace("\\", "\\\\")
    return escaped.replace("%", "\\%").replace("_", "\\_")


def normalized_key(kind: MemoryKind, content: str, target_id: str | None) -> str:
    """The deterministic identity of "the same thing said again".

    Deliberately mechanical - kind, squashed whitespace, case, and exact target.
    Anything cleverer would be a provider-dependent similarity judgment at the
    storage boundary, which is the one place that must not have opinions.
    """

    squashed = " ".join(content.split()).casefold()
    return f"{kind.value}\x1f{target_id or ''}\x1f{squashed}"


class MemoryStore:
    _TARGET_QUERY_CHUNK_SIZE = 500

    def __init__(
        self,
        path: Path,
        scope: CampaignScope,
        *,
        memory_id_factory: Callable[[], str] = new_memory_id,
    ) -> None:
        self.path = path
        self.campaign_id = scope.campaign_id
        self._new_memory_id = memory_id_factory
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(_FOREIGN_KEYS_SQL)
        # Back up before the first write of any kind, including the journal-mode
        # switch: an operator rolling back wants the exact file they had.
        legacy_rows = self._legacy_rows_to_migrate()
        existing_version = (
            None if legacy_rows is not None else self._existing_schema_version()
        )
        if existing_version is not None and existing_version > SCHEMA_VERSION:
            raise MemoryStoreError(  # mutation: reason
                f"{self.path} uses continuity schema {existing_version}, "  # mutation: reason
                f"newer than this build's schema {SCHEMA_VERSION}; refusing "  # mutation: reason
                "to open it with an older writer."  # mutation: reason
            )
        if existing_version in {2, 3}:
            self._backup_version(existing_version)
        self._connection.execute(_JOURNAL_MODE_SQL)
        if legacy_rows is not None:
            self._migrate_v1(legacy_rows)
        elif existing_version == 2:
            self._migrate_v2()
        self._create_schema()
        self._register_campaign(scope.campaign_id, scope.origin)
        self.fieldbook = FieldbookStore(self._connection, self.campaign_id)
        self._connection.commit()

    # -- schema ---------------------------------------------------------

    @property
    def schema_version(self) -> int:
        row = self._connection.execute(_GET_META_SQL, ("schema_version",)).fetchone()
        return SCHEMA_VERSION if row is None else int(_row_text(row, "value"))

    def _create_schema(self) -> None:
        self._connection.execute(_CREATE_META_SQL)
        self._connection.execute(_CREATE_CAMPAIGNS_SQL)
        self._connection.execute(_CREATE_EVENTS_SQL)
        self._connection.execute(_CREATE_MEMORIES_SQL)
        create_fieldbook_schema(self._connection)
        for statement in _INDEX_SQL:
            self._connection.execute(statement)
        self._connection.execute(_SET_META_SQL, ("schema_version", str(SCHEMA_VERSION)))

    def _existing_schema_version(self) -> int | None:
        table = self._connection.execute(
            _CONTINUITY_META_EXISTS_SQL
        ).fetchone()
        if table is None:
            return None
        row = self._connection.execute(
            _GET_META_SQL,
            ("schema_version",),
        ).fetchone()
        return None if row is None else int(_row_text(row, "value"))

    def _backup_version(self, version: int) -> Path:
        backup = self.path.with_suffix(
            self.path.suffix + f".v{version}-backup"
        )
        if not backup.exists():
            shutil.copy2(self.path, backup)
        return backup

    def _register_campaign(self, campaign_id: str, origin: CampaignScopeOrigin) -> None:
        self._connection.execute(
            _UPSERT_CAMPAIGN_SQL,
            (campaign_id, origin.value, self._now()),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # -- migration ------------------------------------------------------

    def _legacy_rows_to_migrate(self) -> list[sqlite3.Row] | None:
        """Read a pre-campaign `memories` table, and back the file up first.

        Returns `None` when there is nothing to migrate. Performs no write, so
        the backup it takes is the file exactly as the operator left it.
        """

        columns = {
            _row_text(row, "name")
            for row in self._connection.execute(_TABLE_INFO_SQL).fetchall()
        }
        if not columns or "memory_id" in columns:
            return None
        if "namespace" not in columns:
            raise MemoryStoreError(  # mutation: reason
                f"{self.path} holds an unrecognized `memories` "  # mutation: reason
                "table; refusing to migrate a shape this build "  # mutation: reason
                "does not know."  # mutation: reason
            )

        self._backup_version(1)
        return list(
            self._connection.execute(
                _LEGACY_ROWS_SQL if "target_id" in columns else _LEGACY_ROWS_UNSCOPED_SQL
            ).fetchall()
        )

    def _migrate_v1(self, rows: Sequence[sqlite3.Row]) -> None:
        """Fold pre-campaign rows into history, once, in one transaction.

        The old rows are real user data with no grounding and no campaign, so
        they keep both facts: `legacy_unverified` authorship, and a campaign
        named after the profile that wrote them rather than whichever save
        happens to open the file next. Assigning them to a live campaign would
        hand one playthrough's beliefs to another.
        """

        with self._connection:
            self._connection.execute(_DROP_LEGACY_TABLE_SQL)
            self._create_schema()
            for namespace in sorted({_row_text(row, "namespace") for row in rows}):
                self._register_campaign(
                    legacy_campaign_id(namespace),
                    CampaignScopeOrigin.LEGACY,
                )
            for row in rows:
                self._append_legacy_row(row)

    def _migrate_v2(self) -> None:
        """Add structured provenance without rewriting append-only v2 events."""

        columns = {
            _row_text(row, "name")
            for row in self._connection.execute(_TABLE_INFO_SQL).fetchall()
        }
        with self._connection:
            if "latest_provenance" not in columns:
                self._connection.execute(_MIGRATE_V2_ADD_PROVENANCE_SQL)
            if "resolution_disposition" not in columns:
                self._connection.execute(_MIGRATE_V2_ADD_DISPOSITION_SQL)

    def _append_legacy_row(self, row: sqlite3.Row) -> None:
        campaign_id = legacy_campaign_id(_row_text(row, "namespace"))
        record = MemoryRecord(
            memory_id=self._new_memory_id(),
            campaign_id=campaign_id,
            kind=MemoryKind(_row_text(row, "kind")),
            status=(
                MemoryStatus.ACTIVE
                if _row_int(row, "active")
                else MemoryStatus.RETRACTED
            ),
            content=_row_text(row, "content"),
            salience=_row_float(row, "salience"),
            grounding=_row_optional_text(row, "evidence"),
            authorship=MemoryAuthorship.LEGACY_UNVERIFIED,
            target_id=_row_optional_text(row, "target_id") or None,
            created_run_id=_row_text(row, "run_id"),
            created_at=datetime.fromisoformat(_row_text(row, "created_at")),
        )
        self._append_event(
            campaign_id,
            record.memory_id,
            MemoryLifecycleEvent.KEEP,
            record.created_run_id,
            record.created_at.isoformat(),
            self._keep_payload(record),
        )
        self._insert_projection(record)

    # -- events and projection -----------------------------------------

    def _append_event(
        self,
        campaign_id: str,
        memory_id: str,
        event: MemoryLifecycleEvent,
        run_id: str,
        recorded_at: str,
        payload: dict[str, Any],
    ) -> None:
        self._connection.execute(
            _APPEND_EVENT_SQL,
            (
                campaign_id,
                memory_id,
                event.value,
                run_id,
                recorded_at,
                json.dumps(payload, sort_keys=True),
            ),
        )

    @staticmethod
    def _keep_payload(record: MemoryRecord) -> dict[str, Any]:
        return {
            "kind": record.kind.value,
            "content": record.content,
            "salience": record.salience,
            "target_id": record.target_id,
            "grounding": record.grounding,
            "provenance": MemoryStore._provenance_payload(
                record.latest_provenance
            ),
            "authorship": record.authorship.value,
            "status": record.status.value,
            "supersedes_id": record.supersedes_id,
        }

    @staticmethod
    def _provenance_payload(
        provenance: MemoryProvenance | None,
    ) -> dict[str, Any] | None:
        return (
            None
            if provenance is None
            else provenance.model_dump(mode="json")
        )

    @staticmethod
    def _provenance_text(
        provenance: MemoryProvenance | None,
    ) -> str | None:
        payload = MemoryStore._provenance_payload(provenance)
        return None if payload is None else json.dumps(payload, sort_keys=True)

    @staticmethod
    def _provenance_from_payload(
        payload: dict[str, Any],
    ) -> MemoryProvenance | None:
        provenance = payload.get("provenance")
        return (
            None
            if provenance is None
            else _MEMORY_PROVENANCE_ADAPTER.validate_python(provenance)
        )

    @staticmethod
    def _payload_with_provenance(
        payload: dict[str, Any],
        provenance: MemoryProvenance | None,
    ) -> dict[str, Any]:
        return {
            **payload,
            "provenance": MemoryStore._provenance_payload(provenance),
        }

    def _insert_projection(self, record: MemoryRecord) -> None:
        self._connection.execute(
            _INSERT_MEMORY_SQL,
            (
                record.memory_id,
                record.campaign_id,
                record.kind.value,
                record.status.value,
                record.content,
                normalized_key(record.kind, record.content, record.target_id),
                record.target_id or "",
                record.salience,
                record.grounding,
                self._provenance_text(record.latest_provenance),
                record.authorship.value,
                record.created_run_id,
                record.created_at.isoformat(),
                None if record.reinforced_at is None else record.reinforced_at.isoformat(),
                None if record.resolved_at is None else record.resolved_at.isoformat(),
                None if record.superseded_at is None else record.superseded_at.isoformat(),
                None
                if record.last_delivered_at is None
                else record.last_delivered_at.isoformat(),
                record.reinforcement_count,
                record.supersedes_id,
                record.superseded_by_id,
                record.resolution_reason,
                (
                    None
                    if record.resolution_disposition is None
                    else record.resolution_disposition.value
                ),
            ),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=_row_text(row, "memory_id"),
            campaign_id=_row_text(row, "campaign_id"),
            kind=MemoryKind(_row_text(row, "kind")),
            status=MemoryStatus(_row_text(row, "status")),
            content=_row_text(row, "content"),
            salience=_row_float(row, "salience"),
            grounding=_row_optional_text(row, "grounding"),
            latest_provenance=(
                None
                if (raw_provenance := _row_optional_text(row, "latest_provenance"))
                is None
                else _MEMORY_PROVENANCE_ADAPTER.validate_json(raw_provenance)
            ),
            authorship=MemoryAuthorship(_row_text(row, "authorship")),
            target_id=_row_optional_text(row, "target_id") or None,
            created_run_id=_row_text(row, "created_run_id"),
            created_at=datetime.fromisoformat(_row_text(row, "created_at")),
            reinforced_at=_row_time(row, "reinforced_at"),
            resolved_at=_row_time(row, "resolved_at"),
            superseded_at=_row_time(row, "superseded_at"),
            last_delivered_at=_row_time(row, "last_delivered_at"),
            reinforcement_count=_row_int(row, "reinforcement_count"),
            supersedes_id=_row_optional_text(row, "supersedes_id"),
            superseded_by_id=_row_optional_text(row, "superseded_by_id"),
            resolution_reason=_row_optional_text(row, "resolution_reason"),
            resolution_disposition=(
                None
                if (
                    raw_disposition := _row_optional_text(
                        row,
                        "resolution_disposition",
                    )
                )
                is None
                else MemoryResolutionDisposition(raw_disposition)
            ),
        )

    # -- lifecycle ------------------------------------------------------

    def keep(
        self,
        run_id: str,
        *,
        kind: MemoryKind,
        content: str,
        salience: float,
        grounding: str | None,
        target_id: str | None = None,
        provenance: CanonicalMemoryProvenance | None = None,
    ) -> MemoryRecord:
        """Create a record, or reinforce the one that already says this.

        Exact restatement is deduplicated by a deterministic normalized key, not
        by similarity: the storage boundary must not need a model to decide
        whether two sentences mean the same thing.
        """

        text = content.strip()
        existing = self._active_by_key(normalized_key(kind, text, target_id))
        if existing is not None:
            return self.reinforce(
                run_id,
                existing.memory_id,
                grounding=grounding,
                salience=max(existing.salience, salience),
                provenance=provenance,
            )

        now = datetime.now(UTC)
        record = MemoryRecord(
            memory_id=self._new_memory_id(),
            campaign_id=self.campaign_id,
            kind=kind,
            status=MemoryStatus.ACTIVE,
            content=text,
            salience=salience,
            grounding=grounding,
            latest_provenance=provenance,
            target_id=target_id,
            created_run_id=run_id,
            created_at=now,
        )
        return self._commit_keep(record, run_id, now)

    def _commit_keep(
        self,
        record: MemoryRecord,
        run_id: str,
        now: datetime,
    ) -> MemoryRecord:
        try:
            with self._connection:
                self._append_event(
                    record.campaign_id,
                    record.memory_id,
                    MemoryLifecycleEvent.KEEP,
                    run_id,
                    now.isoformat(),
                    self._keep_payload(record),
                )
                self._insert_projection(record)
        except sqlite3.IntegrityError as exc:
            self._raise_expected_integrity_conflict(exc)
        return record

    def reinforce(
        self,
        run_id: str,
        memory_id: str,
        *,
        grounding: str | None,
        salience: float | None = None,
        provenance: CanonicalMemoryProvenance | None = None,
    ) -> MemoryRecord:
        current = self._require_open(memory_id, MemoryLifecycleEvent.REINFORCE)
        now = datetime.now(UTC)
        raised = current.salience if salience is None else max(current.salience, salience)
        with self._connection:
            self._append_event(
                self.campaign_id,
                memory_id,
                MemoryLifecycleEvent.REINFORCE,
                run_id,
                now.isoformat(),
                self._payload_with_provenance(
                    {"salience": raised, "grounding": grounding},
                    provenance,
                ),
            )
            self._connection.execute(
                _UPDATE_REINFORCE_SQL,
                (
                    raised,
                    grounding,
                    now.isoformat(),
                    self._provenance_text(provenance),
                    self.campaign_id,
                    memory_id,
                ),
            )
        return current.model_copy(
            update={
                "salience": raised,
                "grounding": (
                    current.grounding if grounding is None else grounding
                ),
                "latest_provenance": (
                    current.latest_provenance
                    if provenance is None
                    else provenance
                ),
                "reinforced_at": now,
                "reinforcement_count": current.reinforcement_count + 1,
            }
        )

    def resolve(
        self,
        run_id: str,
        memory_id: str,
        *,
        reason: str,
        grounding: str | None,
        disposition: MemoryResolutionDisposition = (
            MemoryResolutionDisposition.COMPLETED
        ),
        provenance: CanonicalMemoryProvenance | None = None,
    ) -> MemoryRecord:
        current = self._require_open(memory_id, MemoryLifecycleEvent.RESOLVE)
        if current.kind not in {MemoryKind.COMMITMENT, MemoryKind.HYPOTHESIS}:
            raise MemoryTransitionError(  # mutation: reason
                f"A {current.kind.value} cannot be resolved; supersede or "  # mutation: reason
                "retract it instead."  # mutation: reason
            )
        if current.kind is MemoryKind.COMMITMENT and disposition not in {
            MemoryResolutionDisposition.COMPLETED,
            MemoryResolutionDisposition.ABANDONED,
        }:
            raise MemoryTransitionError(  # mutation: reason
                "A commitment resolves only as completed or abandoned."  # mutation: reason
            )
        if current.kind is MemoryKind.HYPOTHESIS and disposition not in {
            MemoryResolutionDisposition.CONFIRMED,
            MemoryResolutionDisposition.REJECTED,
            MemoryResolutionDisposition.UNKNOWN,
        }:
            raise MemoryTransitionError(  # mutation: reason
                "A hypothesis resolves only as confirmed, rejected, or unknown."  # mutation: reason
            )
        now = datetime.now(UTC)
        with self._connection:
            self._append_event(
                self.campaign_id,
                memory_id,
                MemoryLifecycleEvent.RESOLVE,
                run_id,
                now.isoformat(),
                self._payload_with_provenance(
                    {
                        "reason": reason,
                        "grounding": grounding,
                        "disposition": disposition.value,
                    },
                    provenance,
                ),
            )
            self._connection.execute(
                _UPDATE_RESOLVE_SQL,
                (
                    MemoryStatus.RESOLVED.value,
                    now.isoformat(),
                    reason,
                    disposition.value,
                    grounding,
                    self._provenance_text(provenance),
                    self.campaign_id,
                    memory_id,
                ),
            )
        return current.model_copy(
            update={
                "status": MemoryStatus.RESOLVED,
                "resolved_at": now,
                "resolution_reason": reason,
                "resolution_disposition": disposition,
                "grounding": (
                    current.grounding if grounding is None else grounding
                ),
                "latest_provenance": (
                    current.latest_provenance
                    if provenance is None
                    else provenance
                ),
            }
        )

    def supersede(
        self,
        run_id: str,
        memory_id: str,
        *,
        kind: MemoryKind,
        content: str,
        salience: float,
        grounding: str | None,
        target_id: str | None = None,
        provenance: CanonicalMemoryProvenance | None = None,
    ) -> MemoryRecord:
        """Create the replacement and close the original in one transaction.

        Two calls would leave a window where both are active, or where the old
        one is closed and nothing replaced it. Neither is a state this store
        admits.
        """

        self._require_open(memory_id, MemoryLifecycleEvent.SUPERSEDE)
        replacement_key = normalized_key(kind, content, target_id)
        conflicting = self._active_by_key(replacement_key)
        if conflicting is not None and conflicting.memory_id != memory_id:
            raise MemoryTransitionError(  # mutation: reason
                f"Campaign {self.campaign_id!r} already has an active memory "  # mutation: reason
                "with that normalized identity; supersede or retract the "  # mutation: reason
                f"conflicting memory {conflicting.memory_id!r} first."  # mutation: reason
            )
        now = datetime.now(UTC)
        replacement = MemoryRecord(
            memory_id=self._new_memory_id(),
            campaign_id=self.campaign_id,
            kind=kind,
            status=MemoryStatus.ACTIVE,
            content=content.strip(),
            salience=salience,
            grounding=grounding,
            latest_provenance=provenance,
            target_id=target_id,
            created_run_id=run_id,
            created_at=now,
            supersedes_id=memory_id,
        )
        try:
            with self._connection:
                self._append_event(
                    self.campaign_id,
                    memory_id,
                    MemoryLifecycleEvent.SUPERSEDE,
                    run_id,
                    now.isoformat(),
                    self._payload_with_provenance(
                        {"superseded_by_id": replacement.memory_id},
                        provenance,
                    ),
                )
                self._append_event(
                    self.campaign_id,
                    replacement.memory_id,
                    MemoryLifecycleEvent.KEEP,
                    run_id,
                    now.isoformat(),
                    self._keep_payload(replacement),
                )
                self._connection.execute(
                    _UPDATE_SUPERSEDE_SQL,
                    (
                        MemoryStatus.SUPERSEDED.value,
                        now.isoformat(),
                        replacement.memory_id,
                        self._provenance_text(provenance),
                        self.campaign_id,
                        memory_id,
                    ),
                )
                self._insert_projection(replacement)
        except sqlite3.IntegrityError as exc:
            self._raise_expected_integrity_conflict(exc)
        return replacement

    def compact(
        self,
        run_id: str,
        candidate: MemoryCompactionCandidate,
    ) -> MemoryRecord:
        """Atomically replace every exact source in one lossless candidate.

        Candidate construction has no write authority. Application takes an
        immediate SQLite write lock, re-reads every source, recomputes the
        candidate, then appends all lifecycle events and projection changes in
        one transaction. Any drift or late failure leaves every source open.
        """

        try:
            with self._connection:
                self._connection.execute(_BEGIN_IMMEDIATE_SQL)
                if candidate.campaign_id != self.campaign_id:
                    raise MemoryCompactionError(  # mutation: reason
                        "The compaction candidate belongs to another campaign."  # mutation: reason
                    )
                records: list[MemoryRecord] = []
                for memory_id in candidate.source_memory_ids:
                    record = self.get(memory_id)
                    if record is None:
                        raise MemoryCompactionError(  # mutation: reason
                            f"No compaction source {memory_id!r} exists in "  # mutation: reason
                            f"campaign {self.campaign_id!r}."  # mutation: reason
                        )
                    records.append(record)
                ordered = validate_lossless_compaction_candidate(
                    candidate,
                    records,
                )
                conflicting = self._active_by_key(
                    normalized_key(
                        candidate.kind,
                        candidate.content,
                        candidate.target_id,
                    )
                )
                if conflicting is not None:
                    raise MemoryCompactionError(  # mutation: reason
                        "An active memory already has the compacted identity."  # mutation: reason
                    )
                replacement_id = self._new_memory_id()
                now = datetime.now(UTC)
                provenance = CanonicalCompactionProvenance(
                    candidate=candidate,
                    applied_run_id=run_id,
                    replacement_memory_id=replacement_id,
                    applied_at=now,
                )
                replacement = MemoryRecord(
                    memory_id=replacement_id,
                    campaign_id=self.campaign_id,
                    kind=candidate.kind,
                    status=MemoryStatus.ACTIVE,
                    content=candidate.content,
                    salience=candidate.salience,
                    grounding=(
                        "lossless_compaction("
                        + ",".join(candidate.source_memory_ids)
                        + ")"
                    ),
                    latest_provenance=provenance,
                    authorship=candidate.authorship,
                    target_id=candidate.target_id,
                    created_run_id=run_id,
                    created_at=now,
                )
                for source in ordered:
                    self._append_event(
                        self.campaign_id,
                        source.memory_id,
                        MemoryLifecycleEvent.SUPERSEDE,
                        run_id,
                        now.isoformat(),
                        self._payload_with_provenance(
                            {"superseded_by_id": replacement_id},
                            provenance,
                        ),
                    )
                    updated = self._connection.execute(
                        _UPDATE_COMPACTION_SOURCE_SQL,
                        (
                            MemoryStatus.SUPERSEDED.value,
                            now.isoformat(),
                            replacement_id,
                            self._provenance_text(provenance),
                            self.campaign_id,
                            source.memory_id,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise MemoryCompactionError(  # mutation: reason
                            f"Compaction source {source.memory_id!r} stopped "  # mutation: reason
                            "being active during application."  # mutation: reason
                        )
                self._append_event(
                    self.campaign_id,
                    replacement.memory_id,
                    MemoryLifecycleEvent.KEEP,
                    run_id,
                    now.isoformat(),
                    self._keep_payload(replacement),
                )
                self._insert_projection(replacement)
        except sqlite3.IntegrityError as exc:
            self._raise_expected_integrity_conflict(exc)
        return replacement

    def retract(
        self,
        run_id: str,
        memory_id: str,
        *,
        reason: str,
        provenance: CanonicalMemoryProvenance | None = None,
    ) -> MemoryRecord:
        current = self._require_open(memory_id, MemoryLifecycleEvent.RETRACT)
        now = datetime.now(UTC)
        with self._connection:
            self._append_event(
                self.campaign_id,
                memory_id,
                MemoryLifecycleEvent.RETRACT,
                run_id,
                now.isoformat(),
                self._payload_with_provenance(
                    {"reason": reason},
                    provenance,
                ),
            )
            self._connection.execute(
                _UPDATE_RETRACT_SQL,
                (
                    MemoryStatus.RETRACTED.value,
                    now.isoformat(),
                    reason,
                    self._provenance_text(provenance),
                    self.campaign_id,
                    memory_id,
                ),
            )
        return current.model_copy(
            update={
                "status": MemoryStatus.RETRACTED,
                "resolved_at": now,
                "resolution_reason": reason,
                "latest_provenance": (
                    current.latest_provenance
                    if provenance is None
                    else provenance
                ),
            }
        )

    def _require_open(
        self,
        memory_id: str,
        event: MemoryLifecycleEvent,
    ) -> MemoryRecord:
        record = self.get(memory_id)
        if record is None:
            raise MemoryTransitionError(  # mutation: reason
                f"No memory {memory_id!r} exists in "  # mutation: reason
                f"campaign {self.campaign_id!r}."  # mutation: reason
            )
        if record.status in _CLOSED_STATUSES:
            raise MemoryTransitionError(  # mutation: reason
                f"Memory {memory_id!r} is {record.status.value} and "  # mutation: reason
                f"cannot be {event.value}d."  # mutation: reason
            )
        return record

    @staticmethod
    def _raise_expected_integrity_conflict(exc: sqlite3.IntegrityError) -> None:
        """Translate uniqueness races without disguising other store failures."""

        if exc.sqlite_errorcode in {
            sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
            sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        }:
            raise MemoryTransitionError(  # mutation: reason
                "The continuity transition conflicts with an existing "  # mutation: reason
                "runtime or normalized memory identity."  # mutation: reason
            ) from exc
        raise exc

    # -- reads ----------------------------------------------------------

    def get(self, memory_id: str) -> MemoryRecord | None:
        row = self._connection.execute(
            _SELECT_MEMORY_SQL,
            (self.campaign_id, memory_id),
        ).fetchone()
        return None if row is None else self._record(row)

    def exists(self, memory_id: str) -> bool:
        """Whether this campaign owns an active record with this exact ID."""

        record = self.get(memory_id)
        return record is not None and record.status is MemoryStatus.ACTIVE

    def _active_by_key(self, key: str) -> MemoryRecord | None:
        row = self._connection.execute(
            _SELECT_ACTIVE_BY_KEY_SQL,
            (self.campaign_id, key),
        ).fetchone()
        return None if row is None else self._record(row)

    def all_records(self) -> list[MemoryRecord]:
        return [
            self._record(row)
            for row in self._connection.execute(
                _SELECT_ALL_SQL,
                (self.campaign_id,),
            ).fetchall()
        ]

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
        parameters: list[object] = [self.campaign_id, minimum_salience]
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
        # Guarded on the identities, not the budget: an empty `IN ()` is a
        # syntax error, while a zero budget is already enforced by `LIMIT`.
        if exact_target_ids:
            candidates: dict[str, sqlite3.Row] = {}
            for chunk in _partition_target_ids(
                exact_target_ids,
                self._TARGET_QUERY_CHUNK_SIZE,
            ):
                placeholders = ",".join("?" for _ in chunk)
                entity_parameters: list[object] = [self.campaign_id, *chunk]
                entity_where = _ENTITY_WHERE_SQL.format(placeholders=placeholders)
                if query:
                    entity_where += _QUERY_FILTER_SQL
                    entity_parameters.append(f"%{query}%")
                entity_parameters.append(entity_limit)
                rows = self._connection.execute(
                    _ENTITY_RECALL_SQL.format(where=entity_where),
                    entity_parameters,
                ).fetchall()
                candidates.update((_row_text(row, "memory_id"), row) for row in rows)
            entity_rows = sorted(
                candidates.values(),
                key=lambda row: (
                    _row_float(row, "salience"),
                    _row_text(row, "created_at"),
                    _row_text(row, "memory_id"),
                ),
                reverse=True,
            )[:entity_limit]

        # Exact target matches lead so downstream bounded consumers cannot
        # accidentally slice them away in favor of general salience.
        #
        # No write here, at any rate. `_with_memories` decorates every pumped
        # observation - around ten a second in a live run - and this used to
        # open a write transaction each time, refreshing the very timestamp the
        # ordering above then read back. Reading is not reinforcement.
        return [self._record(row) for row in (*entity_rows, *general_rows)]

    def recall_tiered(
        self,
        *,
        budget: RecallBudget,
        target_ids: Collection[str] = (),
    ) -> TieredRecall:
        """Spend a bounded context window in a fixed, deterministic order.

        Tiers are selected whole and bounded afterwards, so the omitted count is
        the real number rather than "at least the budget". A record belongs to
        exactly one tier — a commitment about the entity in front of you is a
        commitment, and counting it twice would spend the budget twice.

        Only the general tier honours the salience floor. A survival constraint
        is not less important for being unexciting, and neither is an open
        commitment.
        """

        exact_target_ids = sorted({target_id for target_id in target_ids if target_id})
        selections: list[tuple[RecallTier, list[sqlite3.Row]]] = [
            (
                RecallTier.COMMITMENT,
                self._rows(_COMMITMENT_TIER_SQL, (self.campaign_id,)),
            ),
            (RecallTier.CURRENT_TARGET, self._target_rows(exact_target_ids)),
            (
                RecallTier.OPEN_HYPOTHESIS,
                self._rows(_HYPOTHESIS_TIER_SQL, (self.campaign_id,)),
            ),
            (
                RecallTier.GENERAL,
                self._rows(
                    _GENERAL_TIER_SQL,
                    (self.campaign_id, budget.minimum_salience),
                ),
            ),
        ]

        records: list[MemoryRecord] = []
        tiers: dict[str, RecallTier] = {}
        omitted: dict[RecallTier, int] = {}
        for tier, rows in selections:
            allowance = budget.of(tier)
            taken = 0
            skipped = 0
            for row in rows:
                memory_id = _row_text(row, "memory_id")
                if memory_id in tiers:
                    continue
                if taken < allowance:
                    records.append(self._record(row))
                    tiers[memory_id] = tier
                    taken += 1
                else:
                    skipped += 1
            omitted[tier] = skipped
        return TieredRecall(records=records, tiers=tiers, omitted=omitted)

    def _rows(self, sql: str, parameters: Sequence[object]) -> list[sqlite3.Row]:
        return list(self._connection.execute(sql, parameters).fetchall())

    def _target_rows(self, exact_target_ids: Sequence[str]) -> list[sqlite3.Row]:
        if not exact_target_ids:
            return []
        candidates: dict[str, sqlite3.Row] = {}
        for chunk in _partition_target_ids(
            exact_target_ids,
            self._TARGET_QUERY_CHUNK_SIZE,
        ):
            placeholders = ",".join("?" for _ in chunk)
            candidates.update(
                (_row_text(row, "memory_id"), row)
                for row in self._connection.execute(
                    _TARGET_TIER_SQL.format(placeholders=placeholders),
                    (self.campaign_id, *chunk),
                ).fetchall()
            )
        return sorted(candidates.values(), key=_rank_key, reverse=True)

    def search(self, *, query: str, limit: int) -> MemorySearchResult:
        """One deliberate, bounded read of material outside automatic recall.

        Emits no game input and spends no risk budget: this is the agent
        choosing to look something up, not choosing to do something. The query
        is matched literally — `%` and `_` are SQL wildcards, and a planner's
        query is not SQL.
        """

        if limit < 1:
            raise ValueError(  # mutation: reason
                "memory search limit must be at least one"  # mutation: reason
            )
        rows = self._rows(
            _SEARCH_SQL,
            (self.campaign_id, f"%{_escape_like(query)}%"),
        )
        return MemorySearchResult(
            query=query,
            records=[self._record(row) for row in rows[:limit]],
            matched=len(rows),
            truncated=len(rows) > limit,
            reason=(  # mutation: reason
                f"{len(rows)} active records match {query!r}; "  # mutation: reason
                f"{min(len(rows), limit)} shown."  # mutation: reason
            ),
        )

    def record_delivery(self, run_id: str, memory_ids: Sequence[str]) -> None:
        """Note that these records reached an assembled planner payload.

        A diagnostic, and only that: no ordering reads `last_delivered_at`, so
        being read often cannot make a record look important.
        """

        ids = list(memory_ids)
        if not ids:
            return
        now = self._now()
        with self._connection:
            for memory_id in ids:
                self._append_event(
                    self.campaign_id,
                    memory_id,
                    MemoryLifecycleEvent.DELIVER,
                    run_id,
                    now,
                    {},
                )
            for chunk in _partition_target_ids(ids, self._TARGET_QUERY_CHUNK_SIZE):
                placeholders = ",".join("?" for _ in chunk)
                self._connection.execute(
                    _RECORD_DELIVERY_SQL.format(placeholders=placeholders),
                    [now, self.campaign_id, *chunk],
                )

    def history(self, memory_id: str) -> list[MemoryHistoryEntry]:
        return [
            self._history_entry(row)
            for row in self._connection.execute(
                _SELECT_HISTORY_SQL,
                (self.campaign_id, memory_id),
            ).fetchall()
        ]

    def event_count(self) -> int:
        row = self._connection.execute(
            _COUNT_EVENTS_SQL,
            (self.campaign_id,),
        ).fetchone()
        return _row_int(row, "total")


    @staticmethod
    def _history_entry(row: sqlite3.Row) -> MemoryHistoryEntry:
        payload = json.loads(_row_text(row, "payload"))
        return MemoryHistoryEntry(
            event_id=_row_int(row, "event_id"),
            campaign_id=_row_text(row, "campaign_id"),
            memory_id=_row_text(row, "memory_id"),
            event=MemoryLifecycleEvent(_row_text(row, "event")),
            run_id=_row_text(row, "run_id"),
            recorded_at=datetime.fromisoformat(_row_text(row, "recorded_at")),
            payload=payload,
        )

    # -- projection rebuild ---------------------------------------------

    def rebuild_projection(self) -> int:
        """Discard the projection and replay this campaign's history over it.

        The only supported repair. It is also the proof that the projection is
        derived rather than authoritative: if replay does not reproduce the
        current rows exactly, something wrote state that history never saw.
        """

        events = [
            self._history_entry(row)
            for row in self._connection.execute(
                _SELECT_CAMPAIGN_EVENTS_SQL,
                (self.campaign_id,),
            ).fetchall()
        ]
        with self._connection:
            self._connection.execute(_DELETE_PROJECTION_SQL, (self.campaign_id,))
            for entry in events:
                self._replay(entry)
        return len(events)

    def _replay(self, entry: MemoryHistoryEntry) -> None:
        payload = entry.payload
        if entry.event is MemoryLifecycleEvent.KEEP:
            self._insert_projection(
                MemoryRecord(
                    memory_id=entry.memory_id,
                    campaign_id=entry.campaign_id,
                    kind=MemoryKind(payload["kind"]),
                    status=MemoryStatus(payload["status"]),
                    content=payload["content"],
                    salience=payload["salience"],
                    grounding=payload["grounding"],
                    latest_provenance=self._provenance_from_payload(payload),
                    authorship=MemoryAuthorship(payload["authorship"]),
                    target_id=payload["target_id"],
                    created_run_id=entry.run_id,
                    created_at=entry.recorded_at,
                    supersedes_id=payload["supersedes_id"],
                )
            )
            return
        if entry.event is MemoryLifecycleEvent.REINFORCE:
            self._connection.execute(
                _UPDATE_REINFORCE_SQL,
                (
                    payload["salience"],
                    payload["grounding"],
                    entry.recorded_at.isoformat(),
                    self._provenance_text(self._provenance_from_payload(payload)),
                    entry.campaign_id,
                    entry.memory_id,
                ),
            )
            return
        if entry.event is MemoryLifecycleEvent.RESOLVE:
            disposition = payload.get(
                "disposition",
                MemoryResolutionDisposition.COMPLETED.value,
            )
            self._connection.execute(
                _UPDATE_RESOLVE_SQL,
                (
                    MemoryStatus.RESOLVED.value,
                    entry.recorded_at.isoformat(),
                    payload["reason"],
                    disposition,
                    payload["grounding"],
                    self._provenance_text(self._provenance_from_payload(payload)),
                    entry.campaign_id,
                    entry.memory_id,
                ),
            )
            return
        if entry.event is MemoryLifecycleEvent.SUPERSEDE:
            self._connection.execute(
                _UPDATE_SUPERSEDE_SQL,
                (
                    MemoryStatus.SUPERSEDED.value,
                    entry.recorded_at.isoformat(),
                    payload["superseded_by_id"],
                    self._provenance_text(self._provenance_from_payload(payload)),
                    entry.campaign_id,
                    entry.memory_id,
                ),
            )
            return
        if entry.event is MemoryLifecycleEvent.RETRACT:
            self._connection.execute(
                _UPDATE_RETRACT_SQL,
                (
                    MemoryStatus.RETRACTED.value,
                    entry.recorded_at.isoformat(),
                    payload["reason"],
                    self._provenance_text(self._provenance_from_payload(payload)),
                    entry.campaign_id,
                    entry.memory_id,
                ),
            )
            return
        self._connection.execute(
            _RECORD_DELIVERY_SQL.format(placeholders="?"),
            [entry.recorded_at.isoformat(), entry.campaign_id, entry.memory_id],
        )

    # -- lifecycle of the handle ----------------------------------------

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


# -- read-only operator inspection --------------------------------------


def read_only_campaigns(path: Path) -> list[tuple[str, str, str]]:
    """Every campaign in a database, without opening or creating one.

    `MemoryStore.__init__` registers its campaign, which is right for a run and
    wrong for an audit: looking must not invent a campaign that was never
    played.
    """

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [
            (
                _row_text(row, "campaign_id"),
                _row_text(row, "origin"),
                _row_text(row, "created_at"),
            )
            for row in connection.execute(_SELECT_CAMPAIGNS_SQL).fetchall()
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        connection.close()


def read_only_schema_version(path: Path) -> int | None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(_GET_META_SQL, ("schema_version",)).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()
    return None if row is None else int(_row_text(row, "value"))


class ReadOnlyMemoryStore(MemoryStore):
    """A store handle that cannot write, for operator inspection.

    It reuses every read path rather than reimplementing the queries, because a
    second set of queries is a second answer waiting to disagree.
    """

    def __init__(self, path: Path, campaign_id: str) -> None:
        self.path = path
        self.campaign_id = campaign_id
        self._new_memory_id = new_memory_id
        self._connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._connection.row_factory = sqlite3.Row
        self.fieldbook = FieldbookStore(self._connection, self.campaign_id)


def read_only_store(path: Path, campaign_id: str) -> ReadOnlyMemoryStore:
    return ReadOnlyMemoryStore(path, campaign_id)
