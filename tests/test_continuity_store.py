"""The canonical scoped lifecycle store.

One SQLite database holds an append-only history and a projection rebuildable
from it. These tests hold the properties that make it an authority rather than
a cache: campaigns do not bleed, transitions are explicit, nothing is silently
deleted or rewritten, and a partial write is not a state.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kenshi_agent.campaign import (
    CampaignScope,
    CampaignScopeError,
    CampaignScopeOrigin,
    resolve_campaign_scope,
)
from kenshi_agent.config import MemoryConfig
from kenshi_agent.memory import (
    SCHEMA_VERSION,
    MemoryStore,
    MemoryTransitionError,
)
from kenshi_agent.models import (
    CanonicalMemoryProvenance,
    ContinuityOrigin,
    KeepMemoryOperation,
    MemoryAuthorship,
    MemoryKind,
    MemoryLifecycleEvent,
    MemoryResolutionDisposition,
    MemoryStatus,
    ReinforceMemoryOperation,
    ResolveMemoryOperation,
    RetractMemoryOperation,
    ScenarioIdentity,
    SupersedeMemoryOperation,
    WorldStateRevision,
)


def scope(campaign_id: str = "ladle-css-01") -> CampaignScope:
    return CampaignScope(
        campaign_id=campaign_id,
        origin=CampaignScopeOrigin.CONFIGURED,
    )


def store(path: Path, campaign_id: str = "ladle-css-01") -> MemoryStore:
    ids = iter(f"mem-{index:04d}" for index in range(1, 500))
    return MemoryStore(path, scope(campaign_id), memory_id_factory=lambda: next(ids))


def scenario(scenario_id: str = "hub-bar", save_id: str = "save-1") -> ScenarioIdentity:
    return ScenarioIdentity(
        scenario_id=scenario_id,
        save_id=save_id,
        environment="indoor",
        danger="safe",
        economy="broke",
        party="solo",
        time_of_day="day",
    )


def provenance(operation: Any, marker: str) -> CanonicalMemoryProvenance:
    revision = WorldStateRevision(
        telemetry_sequence=7,
        frame_sequence=4,
        capability_epoch=2,
        observed_at_monotonic=1.5,
    )
    return CanonicalMemoryProvenance(
        operation=operation,
        origin=ContinuityOrigin.PLAN,
        run_id=f"run-{marker}",
        authored_context_id="pc-1",
        authored_revision=revision,
        commit_revision=revision,
        plan_id="plan-a",
        plan_version=1,
        step_id=f"step-{marker}",
        rendered_grounding=f"evidence-{marker}",
    )


# --------------------------------------------------------------------------
# Campaign identity
# --------------------------------------------------------------------------


def test_a_live_run_without_an_explicit_campaign_fails_closed() -> None:
    """A config profile name is not a campaign. Two unrelated saves sharing a
    profile would otherwise share one memory."""

    with pytest.raises(CampaignScopeError):
        resolve_campaign_scope(
            MemoryConfig(enabled=True),
            mode="live",
            run_id="run-a",
            scenario=None,
        )


def test_an_explicit_campaign_is_used_verbatim_in_every_mode() -> None:
    for mode in ("live", "mock", "replay"):
        resolved = resolve_campaign_scope(
            MemoryConfig(enabled=True, campaign_id="ladle-css-01"),
            mode=mode,
            run_id="run-a",
            scenario=None,
        )
        assert resolved.campaign_id == "ladle-css-01"
        assert resolved.origin is CampaignScopeOrigin.CONFIGURED


def test_a_live_run_may_be_explicitly_ephemeral_instead() -> None:
    """The escape hatch is saying so, not leaving it blank."""

    resolved = resolve_campaign_scope(
        MemoryConfig(enabled=True, ephemeral=True),
        mode="live",
        run_id="run-a",
        scenario=None,
    )

    assert resolved.origin is CampaignScopeOrigin.EPHEMERAL
    assert "run-a" in resolved.campaign_id


def test_an_attested_scenario_gets_one_deterministic_campaign() -> None:
    first = resolve_campaign_scope(
        MemoryConfig(enabled=True),
        mode="mock",
        run_id="run-a",
        scenario=scenario(),
    )
    again = resolve_campaign_scope(
        MemoryConfig(enabled=True),
        mode="mock",
        run_id="run-b",
        scenario=scenario(),
    )
    other_save = resolve_campaign_scope(
        MemoryConfig(enabled=True),
        mode="mock",
        run_id="run-c",
        scenario=scenario(save_id="save-2"),
    )

    assert first.campaign_id == again.campaign_id
    assert first.origin is CampaignScopeOrigin.SCENARIO
    assert other_save.campaign_id != first.campaign_id


def test_an_unattested_mock_or_replay_run_is_ephemeral_never_global() -> None:
    for mode in ("mock", "replay"):
        resolved = resolve_campaign_scope(
            MemoryConfig(enabled=True),
            mode=mode,
            run_id="run-a",
            scenario=None,
        )
        assert resolved.origin is CampaignScopeOrigin.EPHEMERAL
        assert resolved.campaign_id != "default"
        assert "run-a" in resolved.campaign_id


def test_an_explicit_campaign_and_an_explicit_ephemeral_cannot_both_be_asked_for() -> None:
    with pytest.raises(CampaignScopeError):
        resolve_campaign_scope(
            MemoryConfig(enabled=True, campaign_id="ladle-css-01", ephemeral=True),
            mode="live",
            run_id="run-a",
            scenario=None,
        )


# --------------------------------------------------------------------------
# Lifecycle transitions
# --------------------------------------------------------------------------


def test_a_kept_memory_starts_active_with_its_authorship_recorded(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        record = memories.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver six sealed slop canisters.",
            salience=0.8,
            grounding=None,
        )

    assert record.memory_id == "mem-0001"
    assert record.status is MemoryStatus.ACTIVE
    assert record.authorship is MemoryAuthorship.AGENT_AUTHORED
    assert record.reinforcement_count == 0
    assert record.reinforced_at is None
    assert record.resolved_at is None
    assert record.last_delivered_at is None
    assert record.created_at.utcoffset() is not None


def test_keeping_the_same_thing_twice_reinforces_instead_of_duplicating(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        first = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="  The barman offers no work.  ",
            salience=0.4,
            grounding="current_observation(telemetry_sequence=1)",
        )
        second = memories.keep(
            "run-b",
            kind=MemoryKind.FACT,
            content="The barman offers no work.",
            salience=0.9,
            grounding="action_outcome(ao-1: no_op)",
        )
        active = memories.recall(limit=8)

    assert second.memory_id == first.memory_id
    assert len(active) == 1
    assert second.reinforcement_count == 1
    assert second.reinforced_at is not None
    # Reinforcement raises priority and keeps the stronger grounding, but never
    # rewrites the original record's authorship or creation.
    assert second.salience == 0.9
    assert second.created_at == first.created_at


def test_the_same_words_about_two_entities_stay_two_memories(tmp_path: Path) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        first = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="Offers no work.",
            salience=0.5,
            grounding=None,
            target_id="entity-a",
        )
        second = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="Offers no work.",
            salience=0.5,
            grounding=None,
            target_id="entity-b",
        )

    assert first.memory_id != second.memory_id


def test_exists_means_active_exact_id_in_this_campaign_only(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with store(path, "campaign-a") as memories:
        active = memories.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.5,
            grounding=None,
        )
        assert memories.exists(active.memory_id)
        assert not memories.exists("mem-missing")
        memories.resolve(
            "run-b",
            active.memory_id,
            reason="Delivered.",
            grounding=None,
        )
        assert not memories.exists(active.memory_id)

    with store(path, "campaign-b") as other_campaign:
        assert not other_campaign.exists(active.memory_id)


def test_a_commitment_is_resolved_with_a_reason_and_leaves_active_recall(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.8,
            grounding=None,
        )

        resolved = memories.resolve(
            "run-b",
            kept.memory_id,
            reason="All six were handed over.",
            grounding="action_outcome(ao-4: changed)",
        )

        assert resolved.status is MemoryStatus.RESOLVED
        assert resolved.resolved_at is not None
        assert resolved.resolution_reason == "All six were handed over."
        assert memories.recall(limit=8) == []
        # Not deleted: the record and its history are still there to audit.
        assert memories.get(kept.memory_id) is not None
        assert len(memories.history(kept.memory_id)) == 2


def test_the_store_itself_refuses_to_resolve_a_fact_or_episode(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        for kind in (MemoryKind.FACT, MemoryKind.EPISODE):
            record = memories.keep(
                "run-a",
                kind=kind,
                content=f"A {kind.value} is revised, not resolved.",
                salience=0.5,
                grounding=None,
            )
            with pytest.raises(MemoryTransitionError, match="cannot be resolved"):
                memories.resolve(
                    "run-b",
                    record.memory_id,
                    reason="Wrong lifecycle verb.",
                    grounding=None,
                )
            assert memories.get(record.memory_id).status is MemoryStatus.ACTIVE


def test_superseding_creates_the_replacement_and_links_both_atomically(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        old = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="The gate is open.",
            salience=0.6,
            grounding=None,
        )

        new = memories.supersede(
            "run-b",
            old.memory_id,
            kind=MemoryKind.FACT,
            content="The gate is closed at night.",
            salience=0.7,
            grounding="current_observation(telemetry_sequence=9)",
        )
        replaced = memories.get(old.memory_id)
        active = memories.recall(limit=8)

    assert replaced is not None
    assert replaced.status is MemoryStatus.SUPERSEDED
    assert replaced.superseded_at is not None
    assert replaced.superseded_by_id == new.memory_id
    assert new.supersedes_id == old.memory_id
    assert [record.memory_id for record in active] == [new.memory_id]


def test_a_retracted_memory_leaves_recall_but_not_history(tmp_path: Path) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.HYPOTHESIS,
            content="The trader might buy ore.",
            salience=0.5,
            grounding=None,
        )

        memories.retract("run-b", kept.memory_id, reason="Disproved by telemetry.")
        record = memories.get(kept.memory_id)

        assert memories.recall(limit=8) == []
        assert record is not None
        assert record.status is MemoryStatus.RETRACTED
        assert [event.event for event in memories.history(kept.memory_id)] == [
            MemoryLifecycleEvent.KEEP,
            MemoryLifecycleEvent.RETRACT,
        ]


@pytest.mark.parametrize(
    "closing",
    ["resolve", "supersede", "retract"],
)
def test_a_closed_memory_refuses_every_further_transition(
    closing: str,
    tmp_path: Path,
) -> None:
    """Skipping the status check would let a retracted belief come back."""

    with store(tmp_path / "memory.sqlite3") as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.8,
            grounding=None,
        )
        _close(memories, closing, kept.memory_id)
        before = memories.get(kept.memory_id)

        for attempt in ("reinforce", "resolve", "supersede", "retract"):
            with pytest.raises(MemoryTransitionError):
                _close(memories, attempt, kept.memory_id)

        assert memories.get(kept.memory_id) == before


def test_a_transition_on_an_unknown_memory_is_refused(tmp_path: Path) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        for attempt in ("reinforce", "resolve", "supersede", "retract"):
            with pytest.raises(MemoryTransitionError):
                _close(memories, attempt, "mem-9999")


def test_a_transition_cannot_reach_another_campaigns_memory(tmp_path: Path) -> None:
    """Campaigns do not bleed, and neither do their lifecycle operations."""

    path = tmp_path / "memory.sqlite3"
    with store(path, "other-campaign") as other:
        foreign = other.keep(
            "run-z",
            kind=MemoryKind.FACT,
            content="Another campaign's fact.",
            salience=0.9,
            grounding=None,
        )

    with store(path, "ladle-css-01") as memories:
        assert memories.get(foreign.memory_id) is None
        assert memories.recall(limit=8) == []
        for attempt in ("reinforce", "resolve", "supersede", "retract"):
            with pytest.raises(MemoryTransitionError):
                _close(memories, attempt, foreign.memory_id)

    with store(path, "other-campaign") as other:
        record = other.get(foreign.memory_id)
        assert record is not None
        assert record.status is MemoryStatus.ACTIVE


def _close(memories: MemoryStore, transition: str, memory_id: str) -> Any:
    if transition == "reinforce":
        return memories.reinforce("run-x", memory_id, grounding=None)
    if transition == "resolve":
        return memories.resolve("run-x", memory_id, reason="Done.", grounding=None)
    if transition == "supersede":
        return memories.supersede(
            "run-x",
            memory_id,
            kind=MemoryKind.FACT,
            content=f"Replacement for {memory_id}.",
            salience=0.5,
            grounding=None,
        )
    return memories.retract("run-x", memory_id, reason="No longer believed.")


# --------------------------------------------------------------------------
# History, projection, and transactionality
# --------------------------------------------------------------------------


def test_the_projection_can_be_rebuilt_from_history_alone(tmp_path: Path) -> None:
    """The projection is derived. If it cannot be rebuilt, it is the authority
    by accident, and a corrupted row becomes the truth."""

    path = tmp_path / "memory.sqlite3"
    with store(path) as memories:
        first = memories.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.8,
            grounding=None,
        )
        second = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="The gate is open.",
            salience=0.4,
            grounding="current_observation(telemetry_sequence=1)",
            target_id="entity-gate",
        )
        memories.keep(
            "run-b",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.9,
            grounding=None,
        )
        memories.supersede(
            "run-b",
            second.memory_id,
            kind=MemoryKind.FACT,
            content="The gate is closed at night.",
            salience=0.5,
            grounding=None,
            target_id="entity-gate",
        )
        memories.resolve(
            "run-c",
            first.memory_id,
            reason="Handed over.",
            grounding=None,
        )
        memories.record_delivery("run-c", [first.memory_id])
        before = _snapshot(memories)

        rebuilt = memories.rebuild_projection()

        assert rebuilt >= 4
        assert _snapshot(memories) == before


def test_every_lifecycle_provenance_survives_history_and_projection_replay(
    tmp_path: Path,
) -> None:
    """Anything accepted into history remains exact after every later write."""

    with store(tmp_path / "memory.sqlite3") as memories:
        fact_operation = KeepMemoryOperation(
            kind=MemoryKind.FACT,
            content="The barman offers no work.",
        )
        fact = memories.keep(
            "run-keep",
            kind=fact_operation.kind,
            content=fact_operation.content,
            salience=fact_operation.salience,
            grounding="evidence-keep",
            provenance=provenance(fact_operation, "keep"),
        )
        reinforce_operation = ReinforceMemoryOperation(memory_id=fact.memory_id)
        reinforced_provenance = provenance(reinforce_operation, "reinforce")
        reinforced = memories.keep(
            "run-reinforce",
            kind=fact_operation.kind,
            content=fact_operation.content,
            salience=0.8,
            grounding="evidence-reinforce",
            provenance=reinforced_provenance,
        )
        assert reinforced.latest_provenance == reinforced_provenance
        assert memories.history(fact.memory_id)[-1].payload["provenance"] == (
            reinforced_provenance.model_dump(mode="json")
        )

        commitment = memories.keep(
            "run-commitment",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.8,
            grounding=None,
        )
        resolve_operation = ResolveMemoryOperation(
            memory_id=commitment.memory_id,
            reason="The route is no longer viable.",
            disposition=MemoryResolutionDisposition.ABANDONED,
        )
        resolve_provenance = provenance(resolve_operation, "resolve")
        resolved = memories.resolve(
            "run-resolve",
            commitment.memory_id,
            reason=resolve_operation.reason,
            grounding="evidence-resolve",
            disposition=MemoryResolutionDisposition.ABANDONED,
            provenance=resolve_provenance,
        )
        resolve_payload = memories.history(commitment.memory_id)[-1].payload
        assert resolved.latest_provenance == resolve_provenance
        assert resolved.resolution_disposition is MemoryResolutionDisposition.ABANDONED
        assert resolve_payload["disposition"] == "abandoned"
        assert resolve_payload["provenance"] == resolve_provenance.model_dump(mode="json")

        original = memories.keep(
            "run-original",
            kind=MemoryKind.FACT,
            content="The gate is open.",
            salience=0.5,
            grounding=None,
        )
        supersede_operation = SupersedeMemoryOperation(
            memory_id=original.memory_id,
            kind=MemoryKind.FACT,
            content="The gate is closed.",
        )
        supersede_provenance = provenance(supersede_operation, "supersede")
        replacement = memories.supersede(
            "run-supersede",
            original.memory_id,
            kind=supersede_operation.kind,
            content=supersede_operation.content,
            salience=supersede_operation.salience,
            grounding="evidence-supersede",
            provenance=supersede_provenance,
        )
        superseded = memories.get(original.memory_id)
        assert superseded is not None
        assert superseded.latest_provenance == supersede_provenance
        assert replacement.latest_provenance == supersede_provenance
        assert memories.history(original.memory_id)[-1].payload["provenance"] == (
            supersede_provenance.model_dump(mode="json")
        )

        hypothesis = memories.keep(
            "run-hypothesis",
            kind=MemoryKind.HYPOTHESIS,
            content="The trader might buy ore.",
            salience=0.5,
            grounding=None,
        )
        retract_operation = RetractMemoryOperation(
            memory_id=hypothesis.memory_id,
            reason="Disproved by observation.",
        )
        retract_provenance = provenance(retract_operation, "retract")
        retracted = memories.retract(
            "run-retract",
            hypothesis.memory_id,
            reason=retract_operation.reason,
            provenance=retract_provenance,
        )
        assert retracted.latest_provenance == retract_provenance
        assert memories.history(hypothesis.memory_id)[-1].payload["provenance"] == (
            retract_provenance.model_dump(mode="json")
        )

        before = memories.all_records()
        memories.rebuild_projection()
        assert memories.all_records() == before


def test_v2_resolution_without_a_disposition_replays_as_completed(
    tmp_path: Path,
) -> None:
    """Schema-v2 events predate dispositions but still have one known meaning."""

    with store(tmp_path / "memory.sqlite3") as memories:
        commitment = memories.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.8,
            grounding=None,
        )
        memories.resolve(
            "run-b",
            commitment.memory_id,
            reason="Delivered.",
            grounding="evidence-resolve",
        )
        entry = memories.history(commitment.memory_id)[-1]
        legacy_payload = dict(entry.payload)
        legacy_payload.pop("disposition")
        with memories._connection:
            memories._connection.execute(
                "UPDATE memory_events SET payload=? WHERE memory_id=? AND event=?",
                (
                    json.dumps(legacy_payload, sort_keys=True),
                    commitment.memory_id,
                    MemoryLifecycleEvent.RESOLVE.value,
                ),
            )

        memories.rebuild_projection()
        replayed = memories.get(commitment.memory_id)
        assert replayed is not None
        assert replayed.resolution_disposition is (
            MemoryResolutionDisposition.COMPLETED
        )


class _BrokenProjectionStore(MemoryStore):
    """A store whose projection write fails after its event was appended."""

    def _insert_projection(self, record: Any) -> None:
        raise sqlite3.OperationalError("projection write failed")


def test_a_failed_write_leaves_neither_an_event_nor_a_projection_row(
    tmp_path: Path,
) -> None:
    """Append and project in one transaction, or the store lies either way."""

    path = tmp_path / "memory.sqlite3"
    with store(path) as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="The gate is open.",
            salience=0.5,
            grounding=None,
        )
        events_before = len(memories.history(kept.memory_id))

    with _BrokenProjectionStore(path, scope()) as broken:
        with pytest.raises(sqlite3.Error):
            broken.keep(
                "run-a",
                kind=MemoryKind.FACT,
                content="A fact that must not land.",
                salience=0.5,
                grounding=None,
            )

    with store(path) as reopened:
        assert [record.content for record in reopened.recall(limit=8)] == [
            "The gate is open."
        ]
        assert len(reopened.history(kept.memory_id)) == events_before
        assert reopened.event_count() == 1


def test_foreign_keys_stay_enforced(tmp_path: Path) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        enabled = memories._connection.execute("PRAGMA foreign_keys").fetchone()[0]
        assert enabled == 1
        with pytest.raises(sqlite3.IntegrityError):
            memories._connection.execute(
                "INSERT INTO memory_events "
                "(campaign_id, memory_id, event, run_id, recorded_at, payload) "
                "VALUES ('no-such-campaign', 'mem-0001', 'keep', 'run-a', ?, '{}')",
                (datetime.now(UTC).isoformat(),),
            )


def test_the_schema_version_is_recorded_and_reopening_is_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    with store(path) as memories:
        memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="The gate is open.",
            salience=0.5,
            grounding=None,
        )
        first = _snapshot(memories)
        assert memories.schema_version == SCHEMA_VERSION

    with store(path) as reopened:
        assert _snapshot(reopened) == first
        assert reopened.event_count() == 1


def _snapshot(memories: MemoryStore) -> list[tuple[Any, ...]]:
    return [
        (
            record.memory_id,
            record.campaign_id,
            record.kind,
            record.status,
            record.content,
            record.target_id,
            record.salience,
            record.grounding,
            record.authorship,
            record.created_at,
            record.reinforced_at,
            record.resolved_at,
            record.superseded_at,
            record.last_delivered_at,
            record.reinforcement_count,
            record.supersedes_id,
            record.superseded_by_id,
            record.resolution_reason,
            record.latest_provenance,
            record.resolution_disposition,
        )
        for record in memories.all_records()
    ]


# --------------------------------------------------------------------------
# Legacy migration
# --------------------------------------------------------------------------


V2_SCHEMA = """
    CREATE TABLE continuity_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    INSERT INTO continuity_meta VALUES ('schema_version', '2');
    CREATE TABLE campaigns (
        campaign_id TEXT PRIMARY KEY,
        origin TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    INSERT INTO campaigns VALUES (
        'ladle-css-01', 'configured', '2026-07-27T00:00:00+00:00'
    );
    CREATE TABLE memory_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
        memory_id TEXT NOT NULL,
        event TEXT NOT NULL,
        run_id TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE TABLE memories (
        memory_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        content TEXT NOT NULL,
        normalized_key TEXT NOT NULL,
        target_id TEXT NOT NULL DEFAULT '',
        salience REAL NOT NULL,
        grounding TEXT,
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
        resolution_reason TEXT
    );
"""


def write_v2(path: Path) -> None:
    payload = {
        "authorship": "agent_authored",
        "content": "The Hub has a bar.",
        "grounding": "current_observation(telemetry_sequence=4)",
        "kind": "fact",
        "salience": 0.5,
        "status": "active",
        "supersedes_id": None,
        "target_id": None,
    }
    connection = sqlite3.connect(path)
    connection.executescript(V2_SCHEMA)
    connection.execute(
        "INSERT INTO memory_events "
        "(campaign_id, memory_id, event, run_id, recorded_at, payload) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "ladle-css-01",
            "mem-v2",
            "keep",
            "run-v2",
            "2026-07-27T00:00:00+00:00",
            json.dumps(payload, sort_keys=True),
        ),
    )
    connection.execute(
        "INSERT INTO memories VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "mem-v2",
            "ladle-css-01",
            "fact",
            "active",
            "The Hub has a bar.",
            "fact\x1f\x1fthe hub has a bar.",
            "",
            0.5,
            "current_observation(telemetry_sequence=4)",
            "agent_authored",
            "run-v2",
            "2026-07-27T00:00:00+00:00",
            None,
            None,
            None,
            None,
            0,
            None,
            None,
            None,
        ),
    )
    connection.commit()
    connection.close()


def test_v2_migration_backs_up_and_preserves_unstructured_legacy_provenance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    write_v2(path)
    original = path.read_bytes()

    with store(path) as memories:
        record = memories.get("mem-v2")
        assert record is not None
        assert memories.schema_version == SCHEMA_VERSION
        assert record.grounding == "current_observation(telemetry_sequence=4)"
        assert record.latest_provenance is None
        assert record.resolution_disposition is None
        before = _snapshot(memories)
        memories.rebuild_projection()
        assert _snapshot(memories) == before

    backup = path.with_suffix(path.suffix + ".v2-backup")
    assert backup.read_bytes() == original


@pytest.mark.parametrize(
    "existing_column",
    ["latest_provenance", "resolution_disposition"],
)
def test_v2_migration_resumes_after_either_column_was_already_added(
    existing_column: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    write_v2(path)
    connection = sqlite3.connect(path)
    connection.execute(f"ALTER TABLE memories ADD COLUMN {existing_column} TEXT")
    connection.commit()
    connection.close()

    with store(path) as memories:
        columns = [
            row["name"]
            for row in memories._connection.execute("PRAGMA table_info(memories)")
        ]
        meta = memories._connection.execute(
            "SELECT value FROM continuity_meta WHERE key='schema_version'"
        ).fetchone()
        assert columns.count("latest_provenance") == 1
        assert columns.count("resolution_disposition") == 1
        assert meta is not None and meta["value"] == str(SCHEMA_VERSION)

    with store(path) as reopened:
        assert reopened.schema_version == SCHEMA_VERSION


LEGACY_SCHEMA = """
    CREATE TABLE memories (
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
    );
    INSERT INTO memories (
        namespace, run_id, kind, content, salience, evidence,
        created_at, last_accessed_at, active, target_id
    ) VALUES
    ('live-longform', 'old-run', 'fact', 'The Hub has a bar.', 0.5, 'seen',
     '2026-07-26T00:00:00+00:00', '2026-07-27T00:00:00+00:00', 1, ''),
    ('live-longform', 'old-run', 'fact', 'This barman offers no work.', 0.5, NULL,
     '2026-07-26T00:00:00+00:00', '2026-07-27T00:00:00+00:00', 1, 'entity-barman'),
    ('live-dialogue-chain', 'other-run', 'commitment', 'Go to Squin.', 0.7, NULL,
     '2026-07-26T00:00:00+00:00', '2026-07-27T00:00:00+00:00', 1, '');
"""


def write_legacy(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    connection.commit()
    connection.close()


def test_legacy_rows_are_preserved_under_their_own_namespace_not_a_new_campaign(
    tmp_path: Path,
) -> None:
    """Reassigning old rows to whatever campaign opened the file next would
    hand one save's memories to another."""

    path = tmp_path / "memory.sqlite3"
    write_legacy(path)

    with store(path, "ladle-css-01") as memories:
        assert memories.recall(limit=8) == []

    with store(path, "legacy:live-longform") as legacy:
        records = legacy.recall(limit=8, target_ids={"entity-barman"}, entity_limit=4)
        contents = {record.content for record in records}

    assert contents == {"The Hub has a bar.", "This barman offers no work."}
    assert all(
        record.authorship is MemoryAuthorship.LEGACY_UNVERIFIED for record in records
    )


def test_migration_backs_up_the_original_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    write_legacy(path)
    original = path.read_bytes()

    with store(path, "ladle-css-01") as memories:
        first = memories.event_count()
    with store(path, "ladle-css-01") as reopened:
        assert reopened.event_count() == first

    backups = sorted(tmp_path.glob("memory.sqlite3.v1-backup*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_legacy_history_is_replayable_like_any_other(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    write_legacy(path)

    with store(path, "legacy:live-dialogue-chain") as memories:
        before = _snapshot(memories)
        memories.rebuild_projection()

        assert _snapshot(memories) == before
        assert [record.content for record in memories.recall(limit=8)] == [
            "Go to Squin."
        ]


# --------------------------------------------------------------------------
# Read-only operator inspection
# --------------------------------------------------------------------------


def test_inspecting_a_store_creates_no_campaign_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """Looking at what an agent believes must not change what it believes —
    including inventing a campaign nobody ever played."""

    from kenshi_agent.memory import (
        read_only_campaigns,
        read_only_schema_version,
        read_only_store,
    )

    path = tmp_path / "memory.sqlite3"
    with store(path, "ladle-css-01") as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.8,
            grounding=None,
        )

    assert read_only_schema_version(path) == SCHEMA_VERSION
    assert [campaign for campaign, _, _ in read_only_campaigns(path)] == [
        "ladle-css-01"
    ]

    with read_only_store(path, "ladle-css-01") as inspected:
        assert inspected.path == path
        assert inspected.campaign_id == "ladle-css-01"
        assert [record.memory_id for record in inspected.all_records()] == [
            kept.memory_id
        ]
        assert inspected.event_count() == 1
        assert [entry.event for entry in inspected.history(kept.memory_id)] == [
            MemoryLifecycleEvent.KEEP
        ]
        with pytest.raises(sqlite3.OperationalError):
            inspected.keep(
                "run-b",
                kind=MemoryKind.FACT,
                content="An inspection must not be able to do this.",
                salience=0.5,
                grounding=None,
            )

    # Inspecting a campaign that is not there must not bring it into existence.
    with read_only_store(path, "never-played") as absent:
        assert absent.all_records() == []
    assert [campaign for campaign, _, _ in read_only_campaigns(path)] == [
        "ladle-css-01"
    ]


def test_reading_an_absent_database_reports_nothing_rather_than_failing(
    tmp_path: Path,
) -> None:
    from kenshi_agent.memory import read_only_campaigns, read_only_schema_version

    empty = tmp_path / "empty.sqlite3"
    sqlite3.connect(empty).close()

    assert read_only_campaigns(empty) == []
    assert read_only_schema_version(empty) is None


# --------------------------------------------------------------------------
# Deduplication, timestamps, and replay details
# --------------------------------------------------------------------------


def test_the_normalized_key_squashes_layout_but_not_meaning(tmp_path: Path) -> None:
    """Deduplication is mechanical on purpose: the storage boundary must not
    need a model to decide whether two sentences mean the same thing."""

    from kenshi_agent.memory import normalized_key

    with store(tmp_path / "memory.sqlite3") as memories:
        first = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="The gate\n\nis   open.",
            salience=0.5,
            grounding=None,
        )
        same = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="the GATE is open.",
            salience=0.5,
            grounding=None,
        )
        other_kind = memories.keep(
            "run-a",
            kind=MemoryKind.HYPOTHESIS,
            content="The gate is open.",
            salience=0.5,
            grounding=None,
        )
        assert same.memory_id == first.memory_id
        assert other_kind.memory_id != first.memory_id

    # The exact key, because storage identity depends on it: kind, then the
    # target (empty when unbound), then whitespace-squashed case-folded text,
    # separated by a unit separator that cannot appear in any of them.
    assert (
        normalized_key(MemoryKind.FACT, "  The  gate\n is OPEN. ", None)
        == "fact\x1f\x1fthe gate is open."
    )
    assert (
        normalized_key(MemoryKind.FACT, "The gate is open.", "entity-gate")
        == "fact\x1fentity-gate\x1fthe gate is open."
    )
    # An unbound record and a bound one must not collide through the separator.
    assert normalized_key(MemoryKind.FACT, "b", "a") != normalized_key(
        MemoryKind.FACT, "a\x1fb", None
    )


def test_reinforcing_through_keep_updates_the_grounding(tmp_path: Path) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="The barman offers no work.",
            salience=0.4,
            grounding=None,
        )
        reinforced = memories.keep(
            "run-b",
            kind=MemoryKind.FACT,
            content="The barman offers no work.",
            salience=0.4,
            grounding="action_outcome(ao-1: no_op)",
        )

    assert reinforced.grounding == "action_outcome(ao-1: no_op)"


@pytest.mark.parametrize(
    "transition",
    ["reinforce", "resolve", "supersede", "retract"],
)
def test_every_transition_timestamp_is_recorded_in_utc(
    transition: str,
    tmp_path: Path,
) -> None:
    """A naive local timestamp in a durable record is a silent lie about when."""

    with store(tmp_path / "memory.sqlite3") as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.COMMITMENT,
            content="Deliver the canisters.",
            salience=0.5,
            grounding=None,
        )
        _close(memories, transition, kept.memory_id)
        record = memories.get(kept.memory_id)
        assert record is not None
        stamps = [
            record.reinforced_at,
            record.resolved_at,
            record.superseded_at,
        ]
        recorded = [stamp for stamp in stamps if stamp is not None]
        assert recorded, transition
        assert all(stamp.utcoffset() == timedelta(0) for stamp in recorded)
        assert all(
            entry.recorded_at.utcoffset() == timedelta(0)
            for entry in memories.history(kept.memory_id)
        )


def test_a_supersede_carries_its_grounding_onto_the_replacement(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="The gate is open.",
            salience=0.5,
            grounding=None,
        )
        replacement = memories.supersede(
            "run-b",
            kept.memory_id,
            kind=MemoryKind.FACT,
            content="The gate is closed at night.",
            salience=0.6,
            grounding="current_observation(telemetry_sequence=9)",
            target_id="entity-gate",
        )

    assert replacement.grounding == "current_observation(telemetry_sequence=9)"
    assert replacement.target_id == "entity-gate"


def test_history_payloads_are_stored_with_stable_key_order(tmp_path: Path) -> None:
    """Append-only history is an audit artifact; two identical events must not
    differ only in how a dict happened to be ordered."""

    with store(tmp_path / "memory.sqlite3") as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="The gate is open.",
            salience=0.5,
            grounding=None,
        )
        raw = memories._connection.execute(
            "SELECT payload FROM memory_events WHERE memory_id=?",
            (kept.memory_id,),
        ).fetchone()[0]

    assert list(json.loads(raw)) == sorted(json.loads(raw))


def test_provenance_serialization_is_canonical_json_and_round_trips() -> None:
    operation = KeepMemoryOperation(
        kind=MemoryKind.FACT,
        content="The gate is open.",
    )
    accepted = provenance(operation, "serialize")
    payload = MemoryStore._provenance_payload(accepted)
    expected = accepted.model_dump(mode="json")

    assert payload == expected
    assert payload is not None
    assert type(payload["origin"]) is str
    assert type(payload["operation"]["kind"]) is str
    assert MemoryStore._provenance_from_payload({"provenance": payload}) == accepted
    assert MemoryStore._provenance_from_payload({}) is None
    assert MemoryStore._payload_with_provenance({"reason": "kept"}, accepted) == {
        "reason": "kept",
        "provenance": expected,
    }
    assert MemoryStore._provenance_text(accepted) == json.dumps(
        expected,
        sort_keys=True,
    )


def test_an_entity_budget_with_no_targets_returns_only_general_recall(
    tmp_path: Path,
) -> None:
    """An empty `IN ()` is a syntax error, so the guard has to be an `and`."""

    with store(tmp_path / "memory.sqlite3") as memories:
        memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="A general fact.",
            salience=0.5,
            grounding=None,
        )
        memories.keep(
            "run-a",
            kind=MemoryKind.FACT,
            content="A bound fact.",
            salience=0.9,
            grounding=None,
            target_id="entity-a",
        )

        assert [record.content for record in memories.recall(limit=8, entity_limit=4)] == [
            "A general fact."
        ]
        assert memories.recall(limit=0, entity_limit=0, target_ids={"entity-a"}) == []


def test_a_retracted_record_survives_a_projection_rebuild(tmp_path: Path) -> None:
    with store(tmp_path / "memory.sqlite3") as memories:
        kept = memories.keep(
            "run-a",
            kind=MemoryKind.HYPOTHESIS,
            content="The trader might buy ore.",
            salience=0.5,
            grounding=None,
        )
        memories.retract("run-b", kept.memory_id, reason="Disproved by telemetry.")
        before = _snapshot(memories)

        memories.rebuild_projection()

        assert _snapshot(memories) == before
        record = memories.get(kept.memory_id)
        assert record is not None
        assert record.resolution_reason == "Disproved by telemetry."


UNSCOPED_LEGACY_SCHEMA = """
    CREATE TABLE memories (
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
    );
    INSERT INTO memories (
        namespace, run_id, kind, content, salience, evidence,
        created_at, last_accessed_at, active
    ) VALUES
    ('live', 'old-run', 'fact', 'The Hub has a bar.', 0.5, 'seen',
     '2026-07-26T00:00:00+00:00', '2026-07-27T00:00:00+00:00', 1),
    ('live', 'old-run', 'fact', 'A withdrawn belief.', 0.5, NULL,
     '2026-07-26T00:00:00+00:00', '2026-07-27T00:00:00+00:00', 0);
"""


def test_the_oldest_shape_migrates_with_no_entity_bindings_invented(
    tmp_path: Path,
) -> None:
    """The pre-target table has no identities, so nothing may acquire one."""

    path = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(UNSCOPED_LEGACY_SCHEMA)
    connection.commit()
    connection.close()

    with store(path, "legacy:live") as memories:
        records = memories.all_records()
        active = memories.recall(limit=8)

    by_content = {record.content: record for record in records}
    assert set(by_content) == {"The Hub has a bar.", "A withdrawn belief."}
    assert all(record.target_id is None for record in records)
    assert by_content["The Hub has a bar."].grounding == "seen"
    assert by_content["The Hub has a bar."].status is MemoryStatus.ACTIVE
    assert by_content["A withdrawn belief."].status is MemoryStatus.RETRACTED
    assert [record.content for record in active] == ["The Hub has a bar."]


def test_migrated_entity_bindings_and_grounding_survive(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    write_legacy(path)

    with store(path, "legacy:live-longform") as memories:
        bound = memories.recall(
            limit=0,
            target_ids={"entity-barman"},
            entity_limit=4,
        )
        general = memories.recall(limit=8)

    assert [record.target_id for record in bound] == ["entity-barman"]
    assert bound[0].content == "This barman offers no work."
    assert [record.grounding for record in general] == ["seen"]
    assert general[0].last_delivered_at is None
