import sqlite3
from pathlib import Path

from kenshi_agent.config import PlanningConfig
from kenshi_agent.evals import evaluate_log
from kenshi_agent.memory import MemoryStore
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
