import sqlite3
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from kenshi_agent.advisor import disabled_advisor_availability
from kenshi_agent.campaign import CampaignScope, CampaignScopeOrigin
from kenshi_agent.config import PlanningConfig
from kenshi_agent.continuity import ContinuityLedger
from kenshi_agent.continuity_service import ContinuityService
from kenshi_agent.evals import evaluate_log
from kenshi_agent.memory import MemoryStore, RecallBudget, _partition_target_ids
from kenshi_agent.models import (
    ActionReceipt,
    ApproachDialogueTargetAction,
    ControlMode,
    MemoryKind,
    MemoryRecord,
    NearbyEntity,
    Observation,
    StopAction,
    TelemetrySnapshot,
)
from kenshi_agent.planner_context import PlannerContextAssembler
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.session_log import SessionLogger


def open_store(path: Path, campaign_id: str = "test") -> MemoryStore:
    return MemoryStore(
        path,
        CampaignScope(campaign_id=campaign_id, origin=CampaignScopeOrigin.CONFIGURED),
    )


def keep(
    store: MemoryStore,
    content: str,
    *,
    kind: MemoryKind = MemoryKind.FACT,
    salience: float = 0.5,
    target_id: str | None = None,
    grounding: str | None = None,
    run_id: str = "run-a",
) -> MemoryRecord:
    return store.keep(
        run_id,
        kind=kind,
        content=content,
        salience=salience,
        grounding=grounding,
        target_id=target_id,
    )


def attach_continuity(
    runner: AgentRuntime,
    store: MemoryStore,
    ledger: ContinuityLedger,
) -> None:
    runner.continuity = ContinuityService(
        run_id=ledger.run_id,
        store=store,
        ledger=ledger,
        logger=SimpleNamespace(write=lambda *args, **kwargs: None),
        control_mode=ControlMode.INTERFACE_ONLY,
        recall_budget=getattr(
            runner,
            "_recall_budget",
            RecallBudget(commitments=4, current_target=4, open_hypotheses=2, general=8),
        ),
        fieldbook_project_limit=8,
        advisor_brief_ids=set,
    )
    runner.planner_context = PlannerContextAssembler(
        continuity=runner.continuity,
        ledger=ledger,
        planning_config=getattr(runner, "planning_config", None) or PlanningConfig(),
        advisor_availability=lambda _observation: disabled_advisor_availability(),
    )


def test_memory_store_creates_every_missing_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "memory" / "memory.sqlite3"

    with open_store(path, "test") as store:
        keep(store, "")

    assert path.is_file()


def test_recall_defaults_bound_general_context_and_disable_entity_context(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        for index in range(13):
            keep(store, f"General fact {index}.")
        keep(store, "Entity-only fact.", target_id="entity-a")

        assert len(store.recall()) == 12
        assert store.recall(limit=0, target_ids={"entity-a"}) == []


def test_recall_rejects_each_negative_budget_independently(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        for limit, entity_limit in ((-1, 0), (0, -1), (-1, -1)):
            with pytest.raises(ValueError, match="non-negative"):
                store.recall(limit=limit, entity_limit=entity_limit)


def test_recall_round_trips_owned_fields_without_crossing_namespaces(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    with (
        open_store(path, "alpha") as alpha,
        open_store(path, "beta") as beta,
    ):
        kept = keep(
            alpha,
            "  Alpha observed the gate.  ",
            kind=MemoryKind.EPISODE,
            salience=0.75,
            grounding="telemetry sequence 4",
            run_id="run-alpha",
        )
        keep(beta, "Beta observed the gate.", salience=1.0, run_id="run-beta")

        records = alpha.recall(limit=12)

    assert len(records) == 1
    record = records[0]
    assert record.memory_id == kept.memory_id
    assert record.campaign_id == "alpha"
    assert record.created_run_id == "run-alpha"
    assert record.kind is MemoryKind.EPISODE
    assert record.content == "Alpha observed the gate."
    assert record.salience == 0.75
    assert record.grounding == "telemetry sequence 4"
    assert record.target_id is None
    assert record.created_at.utcoffset() == timedelta(0)
    assert record.last_delivered_at is None


def test_query_filters_general_and_exact_entity_recall(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        for target_id in (None, "entity-a"):
            for content in ("Iron deposit is depleted.", "Copper remains available."):
                keep(store, content, target_id=target_id)

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


def test_entity_recall_orders_globally_by_salience_then_creation(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, "Older low-salience fact.", salience=0.25, target_id="entity-low")
        keep(store, "Highest-salience fact.", salience=1.0, target_id="entity-high")
        keep(store, "Older tied fact.", salience=0.5, target_id="entity-older")
        keep(store, "Newer tied fact.", salience=0.5, target_id="entity-newer")
        # Recalling one of the tied records used to promote it above the other,
        # because the ordering read the timestamp that recall itself had just
        # rewritten. Reading is not reinforcement, so this must change nothing.
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
        "entity-newer",
        "entity-older",
    ]



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



def test_current_target_memory_survives_general_recall_overflow(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "memory.sqlite3")
    try:
        target_id = "entity-barman"
        keep(store, "This barman has no affordable work.", salience=0.0, target_id=target_id)
        for index in range(6):
            keep(store, f"Later general fact {index}.", salience=1.0, run_id="run-b")

        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner._recall_budget = RecallBudget(
            commitments=2,
            current_target=2,
            open_hypotheses=2,
            general=2,
            minimum_salience=0.5,
        )
        runner.action_outcome_limit = 0
        runner._ledger = ContinuityLedger(run_id="run-b", action_outcome_limit=0)
        attach_continuity(runner, store, runner._ledger)
        runner.advisor = None
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
        decorated = runner.planner_context.decorate(observation)

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
    store = open_store(tmp_path / "memory.sqlite3")
    try:
        keep(store, "This barman rejected the proposal.", target_id="entity-old-barman")

        runner = object.__new__(AgentRuntime)
        runner.memory = store
        runner._recall_budget = RecallBudget(
            commitments=2,
            current_target=2,
            open_hypotheses=2,
            general=2,
        )
        runner.action_outcome_limit = 0
        runner._ledger = ContinuityLedger(run_id="run-b", action_outcome_limit=0)
        attach_continuity(runner, store, runner._ledger)
        runner.advisor = None
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

        assert runner.planner_context.decorate(same_name_new_identity).memories == []
        assert runner.planner_context.decorate(stale_old_identity).memories == []
    finally:
        store.close()



def test_opening_a_scoped_memory_store_does_not_rebuild_its_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    with open_store(path, "test") as store:
        keep(store, "Persisted fact.")
    connection = sqlite3.connect(path)
    schema_version_before = connection.execute("PRAGMA schema_version").fetchone()[0]
    connection.close()

    with open_store(path, "test") as reopened:
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
    store = open_store(tmp_path / "memory.sqlite3")
    keep(store, "This barman has no useful work.", target_id=target_id)
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
        runner._recall_budget = RecallBudget(
            commitments=0,
            current_target=entity_limit,
            open_hypotheses=0,
            general=0,
        )
        runner.action_outcome_limit = 0
        runner._ledger = ContinuityLedger(run_id="run-b", action_outcome_limit=0)
        attach_continuity(runner, store, runner._ledger)
        runner.advisor = None
        runner.planning_config = PlanningConfig()
        context = runner.planner_context.decorate(observation)

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
