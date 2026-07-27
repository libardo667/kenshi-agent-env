import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from kenshi_agent.config import PlanningConfig
from kenshi_agent.evals import evaluate_log
from kenshi_agent.memory import MemoryStore, _partition_target_ids
from kenshi_agent.models import (
    ActionReceipt,
    ApproachDialogueTargetAction,
    MemoryKind,
    MemoryWrite,
    NearbyEntity,
    Observation,
    StopAction,
    TelemetrySnapshot,
)
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.session_log import SessionLogger


def test_memory_store_creates_every_missing_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "memory" / "memory.sqlite3"

    with MemoryStore(path, "test") as store:
        store.add(
            "run-a",
            MemoryWrite(kind=MemoryKind.FACT, content="Nested storage works."),
        )

    assert path.is_file()


def test_recall_defaults_bound_general_context_and_disable_entity_context(
    tmp_path: Path,
) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        for index in range(13):
            store.add(
                "run-a",
                MemoryWrite(
                    kind=MemoryKind.FACT,
                    content=f"General fact {index}.",
                ),
            )
        store.add(
            "run-a",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="Entity-only fact.",
                target_id="entity-a",
            ),
        )

        assert len(store.recall()) == 12
        assert store.recall(limit=0, target_ids={"entity-a"}) == []


def test_recall_rejects_each_negative_budget_independently(tmp_path: Path) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        for limit, entity_limit in ((-1, 0), (0, -1), (-1, -1)):
            with pytest.raises(ValueError, match="non-negative"):
                store.recall(limit=limit, entity_limit=entity_limit)


def test_recall_round_trips_owned_fields_without_crossing_namespaces(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    with (
        MemoryStore(path, "alpha") as alpha,
        MemoryStore(path, "beta") as beta,
    ):
        alpha_id = alpha.add(
            "run-alpha",
            MemoryWrite(
                kind=MemoryKind.EPISODE,
                content="  Alpha observed the gate.  ",
                salience=0.75,
                evidence="telemetry sequence 4",
            ),
        )
        beta.add(
            "run-beta",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="Beta observed the gate.",
                salience=1.0,
            ),
        )

        records = alpha.recall(limit=12)

    assert len(records) == 1
    record = records[0]
    assert record.id == alpha_id
    assert record.namespace == "alpha"
    assert record.run_id == "run-alpha"
    assert record.kind is MemoryKind.EPISODE
    assert record.content == "Alpha observed the gate."
    assert record.salience == 0.75
    assert record.evidence == "telemetry sequence 4"
    assert record.target_id is None
    assert record.created_at.utcoffset() == timedelta(0)
    assert record.last_accessed_at.utcoffset() == timedelta(0)


def test_query_filters_general_and_exact_entity_recall(tmp_path: Path) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        for target_id in (None, "entity-a"):
            for content in ("Iron deposit is depleted.", "Copper remains available."):
                store.add(
                    "run-a",
                    MemoryWrite(
                        kind=MemoryKind.FACT,
                        content=content,
                        target_id=target_id,
                    ),
                )

        records = store.recall(
            limit=4,
            query="iron",
            target_ids=["", "entity-a", "entity-a"],
            entity_limit=4,
        )

    assert [(record.content, record.target_id) for record in records] == [
        ("Iron deposit is depleted.", "entity-a"),
        ("Iron deposit is depleted.", None),
    ]


def test_entity_recall_orders_globally_by_salience_then_recent_access(
    tmp_path: Path,
) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        store.add(
            "run-a",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="Older low-salience fact.",
                salience=0.25,
                target_id="entity-low",
            ),
        )
        store.add(
            "run-a",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="Highest-salience fact.",
                salience=1.0,
                target_id="entity-high",
            ),
        )
        store.add(
            "run-a",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="Older tied fact.",
                salience=0.5,
                target_id="entity-older",
            ),
        )
        store.add(
            "run-a",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="Newer tied fact.",
                salience=0.5,
                target_id="entity-newer",
            ),
        )
        store.recall(
            limit=0,
            target_ids={"entity-older"},
            entity_limit=1,
        )

        records = store.recall(
            limit=0,
            target_ids={
                "entity-low",
                "entity-high",
                "entity-newer",
                "entity-older",
            },
            entity_limit=3,
        )

    assert [record.target_id for record in records] == [
        "entity-high",
        "entity-older",
        "entity-newer",
    ]


def test_recall_persists_a_later_utc_access_time(tmp_path: Path) -> None:
    with MemoryStore(tmp_path / "memory.sqlite3", "test") as store:
        store.add(
            "run-a",
            MemoryWrite(kind=MemoryKind.FACT, content="Remember this."),
        )

        first = store.recall(limit=1)[0]
        second = store.recall(limit=1)[0]

    assert second.last_accessed_at > first.last_accessed_at
    assert second.last_accessed_at.utcoffset() == timedelta(0)


def test_target_partitions_conserve_every_identity_once() -> None:
    for chunk_size in (1, 2, 500):
        for count in (0, 1, 2, 499, 500, 501, 1001):
            target_ids = [f"entity-{index}" for index in range(count)]

            chunks = _partition_target_ids(target_ids, chunk_size)

            assert [target_id for chunk in chunks for target_id in chunk] == target_ids
            assert all(0 < len(chunk) <= chunk_size for chunk in chunks)
            assert len(chunks) == (count + chunk_size - 1) // chunk_size

    for invalid_size in (0, -1):
        with pytest.raises(ValueError, match="positive"):
            _partition_target_ids([], invalid_size)


def test_memory_upsert_and_recall(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3", "test")
    try:
        first = store.add(
            "run-a",
            MemoryWrite(kind=MemoryKind.FACT, content="The Hub has a bar.", salience=0.4),
        )
        store.add(
            "run-a",
            MemoryWrite(kind=MemoryKind.FACT, content="The Hub has a gate.", salience=0.4),
        )
        second = store.add(
            "run-b",
            MemoryWrite(kind=MemoryKind.FACT, content="The Hub has a bar.", salience=0.8),
        )
        assert first == second
        records = store.recall(limit=5, minimum_salience=0.5)
        assert len(records) == 1
        assert records[0].salience == 0.8
    finally:
        store.close()


def test_current_target_memory_survives_general_recall_overflow(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3", "test")
    try:
        target_id = "entity-barman"
        store.add(
            "run-a",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="This barman has no affordable work.",
                salience=0.0,
                target_id=target_id,
            ),
        )
        for index in range(6):
            store.add(
                "run-b",
                MemoryWrite(
                    kind=MemoryKind.FACT,
                    content=f"Later general fact {index}.",
                    salience=1.0,
                ),
            )

        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner.memory_limit = 2
        runner.entity_memory_limit = 2
        runner.minimum_memory_salience = 0.5
        runner.action_outcome_limit = 0
        runner._action_outcomes = []
        runner.advisor = None
        runner._affordance_requests = []
        runner.planning_config = PlanningConfig()

        observation = Observation(
            run_id="run-b",
            step_index=0,
            mode="mock",
            telemetry=TelemetrySnapshot(
                nearby_entities=[
                    NearbyEntity(id=target_id, name="Barman"),
                ],
            ),
        )
        decorated = runner._with_memories(observation)

        assert [memory.target_id for memory in decorated.memories] == [
            target_id,
            None,
            None,
        ]
        assert decorated.memories[0].content == "This barman has no affordable work."
    finally:
        store.close()


def test_target_memory_never_attaches_by_name_or_stale_identity(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3", "test")
    try:
        store.add(
            "run-a",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="This barman rejected the proposal.",
                target_id="entity-old-barman",
            ),
        )

        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner.memory_limit = 2
        runner.entity_memory_limit = 2
        runner.minimum_memory_salience = 0.0
        runner.action_outcome_limit = 0
        runner._action_outcomes = []
        runner.advisor = None
        runner._affordance_requests = []
        runner.planning_config = PlanningConfig()

        same_name_new_identity = Observation(
            run_id="run-b",
            step_index=0,
            mode="mock",
            telemetry=TelemetrySnapshot(
                nearby_entities=[
                    NearbyEntity(id="entity-new-barman", name="Barman"),
                ],
            ),
        )
        stale_old_identity = same_name_new_identity.model_copy(
            update={
                "telemetry": TelemetrySnapshot(
                    nearby_entities=[
                        NearbyEntity(id="entity-old-barman", name="Barman"),
                    ],
                ),
                "telemetry_stale": True,
            }
        )

        assert runner._with_memories(same_name_new_identity).memories == []
        assert runner._with_memories(stale_old_identity).memories == []
    finally:
        store.close()


def test_memory_store_migrates_legacy_rows_without_merging_target_lifetimes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
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
        ) VALUES (
            'test', 'legacy', 'fact', 'The Hub has a bar.', 0.5, NULL,
            '2026-07-26T00:00:00+00:00', '2026-07-26T00:00:00+00:00', 1
        );
        """
    )
    connection.close()

    store = MemoryStore(path, "test")
    try:
        legacy = store.recall(limit=5)
        first_target = store.add(
            "run-a",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="No work is available.",
                target_id="entity-a",
            ),
        )
        second_target = store.add(
            "run-b",
            MemoryWrite(
                kind=MemoryKind.FACT,
                content="No work is available.",
                target_id="entity-b",
            ),
        )

        assert [(record.content, record.target_id) for record in legacy] == [
            ("The Hub has a bar.", None)
        ]
        assert first_target != second_target
        assert {
            record.target_id
            for record in store.recall(
                limit=0,
                entity_limit=2,
                target_ids={"entity-a", "entity-b"},
            )
        } == {"entity-a", "entity-b"}
    finally:
        store.close()


def test_opening_a_scoped_memory_store_does_not_rebuild_its_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    with MemoryStore(path, "test") as store:
        store.add(
            "run-a",
            MemoryWrite(kind=MemoryKind.FACT, content="Persisted fact."),
        )
    connection = sqlite3.connect(path)
    schema_version_before = connection.execute("PRAGMA schema_version").fetchone()[0]
    connection.close()

    with MemoryStore(path, "test") as reopened:
        assert [record.content for record in reopened.recall(limit=1)] == [
            "Persisted fact."
        ]

    connection = sqlite3.connect(path)
    schema_version_after = connection.execute("PRAGMA schema_version").fetchone()[0]
    connection.close()
    assert schema_version_after == schema_version_before


def test_entity_recall_reduces_repeated_approaches_in_controlled_policy(
    tmp_path: Path,
) -> None:
    """Hold one deterministic policy fixed and ablate only scoped recall."""

    target_id = "entity-barman"
    store = MemoryStore(tmp_path / "memory.sqlite3", "test")
    store.add(
        "run-a",
        MemoryWrite(
            kind=MemoryKind.FACT,
            content="This barman has no useful work.",
            target_id=target_id,
        ),
    )
    observation = Observation(
        run_id="ablation",
        step_index=0,
        mode="mock",
        telemetry=TelemetrySnapshot(
            nearby_entities=[NearbyEntity(id=target_id, name="Barman")]
        ),
    )

    def run_condition(name: str, entity_limit: int) -> int:
        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner.memory_limit = 0
        runner.entity_memory_limit = entity_limit
        runner.minimum_memory_salience = 0.0
        runner.action_outcome_limit = 0
        runner._action_outcomes = []
        runner.advisor = None
        runner._affordance_requests = []
        runner.planning_config = PlanningConfig()
        context = runner._with_memories(observation)

        path = tmp_path / f"{name}.jsonl"
        with SessionLogger(path, name) as logger:
            for step_index in range(3):
                knows_constraint = any(
                    memory.target_id == target_id for memory in context.memories
                )
                action = (
                    StopAction(reason="Known branch is exhausted.")
                    if knows_constraint
                    else ApproachDialogueTargetAction(target_id=target_id)
                )
                logger.write(
                    "action_receipt",
                    step_index=step_index,
                    payload=ActionReceipt(
                        action=action,
                        accepted=True,
                        executed=True,
                        dry_run=True,
                    ),
                )
        return evaluate_log(path).repeated_dialogue_approach_attempts

    try:
        assert run_condition("without-entity-recall", 0) == 2
        assert run_condition("with-entity-recall", 2) == 0
    finally:
        store.close()
