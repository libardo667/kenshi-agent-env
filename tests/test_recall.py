"""Bounded, deterministic recall and what actually reaches a planner.

Recall is a policy, not a query: it decides what a bounded context window is
spent on. These tests hold that the decision is deterministic, that the things
a plan cannot safely proceed without are protected from crowding, that what was
left out is stated rather than hidden, and that an agent can deliberately reach
for more without that reach touching the game.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kenshi_agent.campaign import CampaignScope, CampaignScopeOrigin
from kenshi_agent.memory import MemoryStore, RecallBudget
from kenshi_agent.models import (
    MemoryKind,
    MemoryReadReceipt,
    MemoryReadStatus,
    MemoryRecord,
    RecallMemoryAction,
    RecallTier,
    is_runtime_control_action,
)


def open_store(path: Path, campaign_id: str = "test") -> MemoryStore:
    identities = iter(f"mem-{index:04d}" for index in range(1, 999))
    return MemoryStore(
        path,
        CampaignScope(campaign_id=campaign_id, origin=CampaignScopeOrigin.CONFIGURED),
        memory_id_factory=lambda: next(identities),
    )


def keep(
    store: MemoryStore,
    content: str,
    *,
    kind: MemoryKind = MemoryKind.FACT,
    salience: float = 0.5,
    target_id: str | None = None,
) -> MemoryRecord:
    return store.keep(
        "run-a",
        kind=kind,
        content=content,
        salience=salience,
        grounding=None,
        target_id=target_id,
    )


def budget(**overrides: object) -> RecallBudget:
    values: dict[str, object] = {
        "commitments": 2,
        "current_target": 2,
        "open_hypotheses": 1,
        "general": 2,
        "minimum_salience": 0.0,
    }
    values.update(overrides)
    return RecallBudget(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Tier order and protection
# --------------------------------------------------------------------------


def test_recall_leads_with_what_a_plan_cannot_safely_proceed_without(
    tmp_path: Path,
) -> None:
    """Salience alone would let twenty loud facts bury an open commitment."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        commitment = keep(
            store,
            "Deliver six sealed slop canisters.",
            kind=MemoryKind.COMMITMENT,
            salience=0.1,
        )
        bound = keep(
            store,
            "This barman offers no work.",
            salience=0.0,
            target_id="entity-barman",
        )
        hypothesis = keep(
            store,
            "The trader may buy ore.",
            kind=MemoryKind.HYPOTHESIS,
            salience=0.2,
        )
        loud = [
            keep(store, f"Loud general fact {index}.", salience=1.0)
            for index in range(6)
        ]

        result = store.recall_tiered(
            budget=budget(),
            target_ids={"entity-barman"},
        )

    assert [record.memory_id for record in result.records[:3]] == [
        commitment.memory_id,
        bound.memory_id,
        hypothesis.memory_id,
    ]
    assert [record.memory_id for record in result.records[3:]] == [
        loud[-1].memory_id,
        loud[-2].memory_id,
    ]
    assert result.tier_of(commitment.memory_id) is RecallTier.COMMITMENT
    assert result.tier_of(bound.memory_id) is RecallTier.CURRENT_TARGET
    assert result.tier_of(hypothesis.memory_id) is RecallTier.OPEN_HYPOTHESIS
    assert result.tier_of(loud[-1].memory_id) is RecallTier.GENERAL


def test_a_record_appears_in_exactly_one_tier(tmp_path: Path) -> None:
    """A bound commitment is both. Counting it twice spends the budget twice."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        both = keep(
            store,
            "Trade with this one when the money is there.",
            kind=MemoryKind.COMMITMENT,
            salience=0.5,
            target_id="entity-trader",
        )

        result = store.recall_tiered(
            budget=budget(),
            target_ids={"entity-trader"},
        )

    assert [record.memory_id for record in result.records] == [both.memory_id]
    assert result.tier_of(both.memory_id) is RecallTier.COMMITMENT


def test_each_tier_is_bounded_independently(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        for index in range(5):
            keep(
                store,
                f"Commitment {index}.",
                kind=MemoryKind.COMMITMENT,
                salience=index / 10,
            )
            keep(store, f"Bound {index}.", target_id="entity-a", salience=index / 10)
            keep(store, f"General {index}.", salience=index / 10)

        result = store.recall_tiered(
            budget=budget(commitments=2, current_target=3, general=1),
            target_ids={"entity-a"},
        )

    counts = {tier: 0 for tier in RecallTier}
    for record in result.records:
        counts[result.tier_of(record.memory_id)] += 1
    assert counts[RecallTier.COMMITMENT] == 2
    assert counts[RecallTier.CURRENT_TARGET] == 3
    assert counts[RecallTier.GENERAL] == 1


def test_omissions_are_stated_rather_than_hidden(tmp_path: Path) -> None:
    """A planner that cannot tell "nothing else" from "more, not shown" will
    conclude the first and stop looking."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        for index in range(5):
            keep(store, f"Commitment {index}.", kind=MemoryKind.COMMITMENT)
            keep(store, f"General {index}.")

        result = store.recall_tiered(budget=budget(commitments=2, general=2))

    assert result.omitted[RecallTier.COMMITMENT] == 3
    assert result.omitted[RecallTier.GENERAL] == 3
    assert result.omitted[RecallTier.CURRENT_TARGET] == 0
    assert result.total_omitted == 6


def test_a_zero_budget_omits_everything_and_says_so(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, "A commitment.", kind=MemoryKind.COMMITMENT)
        keep(store, "A general fact.")

        result = store.recall_tiered(
            budget=budget(commitments=0, current_target=0, open_hypotheses=0, general=0)
        )

    assert result.records == []
    assert result.total_omitted == 2


def test_only_general_recall_respects_the_salience_floor(tmp_path: Path) -> None:
    """A survival constraint is not less important for being unexciting."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        quiet_commitment = keep(
            store,
            "Do not go north without food.",
            kind=MemoryKind.COMMITMENT,
            salience=0.0,
        )
        keep(store, "A quiet general fact.", salience=0.0)

        result = store.recall_tiered(budget=budget(minimum_salience=0.5))

    assert [record.memory_id for record in result.records] == [
        quiet_commitment.memory_id
    ]


def test_closed_records_never_reach_any_tier(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        resolved = keep(store, "Delivered.", kind=MemoryKind.COMMITMENT)
        store.resolve("run-b", resolved.memory_id, reason="Done.", grounding=None)
        retracted = keep(store, "A doubted thing.", kind=MemoryKind.HYPOTHESIS)
        store.retract("run-b", retracted.memory_id, reason="Disproved.")

        result = store.recall_tiered(budget=budget())

    assert result.records == []
    assert result.total_omitted == 0


def test_recall_is_deterministic_across_repeated_calls(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        for index in range(6):
            keep(store, f"Tied fact {index}.", salience=0.5)

        first = store.recall_tiered(budget=budget(general=3))
        again = store.recall_tiered(budget=budget(general=3))

    assert [record.memory_id for record in first.records] == [
        record.memory_id for record in again.records
    ]


def test_reinforcement_outranks_an_equally_salient_unreinforced_record(
    tmp_path: Path,
) -> None:
    """Explicit reinforcement is the agent saying so. Reading is not."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        quiet = keep(store, "An unreinforced fact.", salience=0.5)
        insisted = keep(store, "A fact worth repeating.", salience=0.5)
        store.reinforce("run-b", insisted.memory_id, grounding=None)
        store.record_delivery("run-b", [quiet.memory_id, quiet.memory_id])

        result = store.recall_tiered(budget=budget(general=2))

    assert [record.memory_id for record in result.records] == [
        insisted.memory_id,
        quiet.memory_id,
    ]


def test_tiered_recall_writes_nothing(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, "A commitment.", kind=MemoryKind.COMMITMENT)
        keep(store, "A bound fact.", target_id="entity-a")
        before = store._connection.total_changes

        for _ in range(20):
            store.recall_tiered(budget=budget(), target_ids={"entity-a"})

        assert store._connection.total_changes == before


def test_another_campaigns_records_reach_no_tier(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with open_store(path, "other") as other:
        keep(other, "Another campaign's commitment.", kind=MemoryKind.COMMITMENT)
        keep(other, "Another campaign's bound fact.", target_id="entity-a")

    with open_store(path, "test") as store:
        result = store.recall_tiered(budget=budget(), target_ids={"entity-a"})

    assert result.records == []
    assert result.total_omitted == 0


# --------------------------------------------------------------------------
# The elective bounded read
# --------------------------------------------------------------------------


def test_recall_memory_is_a_cognitive_planner_control() -> None:
    action = RecallMemoryAction(query="gate")

    assert is_runtime_control_action(action)


def test_a_read_receipt_cannot_advertise_memory_ids_it_did_not_return(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        returned = keep(store, "The gate at Squin closes at night.")

    with pytest.raises(ValueError, match="record_ids"):
        MemoryReadReceipt(
            query="gate",
            records=[returned],
            receipt_id="mrr-" + "1" * 32,
            source="durable_memory",
            status=MemoryReadStatus.COMPLETED,
            campaign_id="test",
            record_ids=["mem-invented"],
            plan_id="single-step",
            plan_version=1,
            step_id="step-0",
        )


def test_an_elective_search_finds_what_automatic_recall_left_out(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, "The gate at Squin closes at night.", salience=0.0)
        for index in range(6):
            keep(store, f"Loud unrelated fact {index}.", salience=1.0)

        automatic = store.recall_tiered(budget=budget(general=2))
        found = store.search(query="squin", limit=4)

    assert "Squin" not in " ".join(record.content for record in automatic.records)
    assert [record.content for record in found.records] == [
        "The gate at Squin closes at night."
    ]
    assert found.truncated is False


def test_an_elective_search_is_bounded_and_says_when_it_truncated(
    tmp_path: Path,
) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        for index in range(10):
            keep(store, f"Gate fact {index}.", salience=index / 10)

        found = store.search(query="gate", limit=3)

    assert len(found.records) == 3
    assert found.truncated is True
    assert found.matched == 10
    # The counts have to be *said*, not only carried in fields: this string is
    # the receipt message a planner reads back.
    assert "10" in found.reason
    assert "3 shown" in found.reason


def test_an_elective_search_cannot_see_another_campaign(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with open_store(path, "other") as other:
        keep(other, "Another campaign's gate fact.")

    with open_store(path, "test") as store:
        found = store.search(query="gate", limit=4)

    assert found.records == []
    assert found.matched == 0


def test_an_elective_search_skips_closed_records(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        withdrawn = keep(store, "The gate is open.")
        store.retract("run-b", withdrawn.memory_id, reason="Disproved.")

        assert store.search(query="gate", limit=4).records == []


def test_an_elective_search_writes_nothing(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, "A gate fact.")
        before = store._connection.total_changes

        for _ in range(10):
            store.search(query="gate", limit=4)

        assert store._connection.total_changes == before


@pytest.mark.parametrize("limit", [-1, 0])
def test_a_search_budget_below_one_is_refused(limit: int, tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        with pytest.raises(ValueError):
            store.search(query="gate", limit=limit)


def test_a_search_pattern_is_matched_literally(tmp_path: Path) -> None:
    """`%` and `_` are SQL wildcards. A planner's query is not SQL."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, "Stock is 100% sold out.")
        keep(store, "Nothing to do with that.")

        found = store.search(query="100%", limit=4)
        percent = store.search(query="%", limit=4)
        underscore = store.search(query="_", limit=4)

    assert [record.content for record in found.records] == ["Stock is 100% sold out."]
    # A literal `%` matches only the record that actually contains one, rather
    # than every record the way an unescaped wildcard would.
    assert [record.content for record in percent.records] == [
        "Stock is 100% sold out."
    ]
    assert underscore.records == []


# --------------------------------------------------------------------------
# What survives the payload budget
# --------------------------------------------------------------------------


def budgeted_memories(payload: dict[str, object], max_chars: int) -> list[str]:
    import json

    from kenshi_agent.observation_budget import budget_observation_payload

    text = budget_observation_payload(
        payload,
        full_text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        max_chars=max_chars,
    )
    return [memory["content"] for memory in json.loads(text)["memories"]]


def test_an_open_commitment_survives_a_budget_that_drops_general_context() -> None:
    """An agent that loses its commitment to a character budget has amnesia,
    not brevity."""

    from datetime import UTC, datetime

    from kenshi_agent.models import (
        MemoryStatus,
        NearbyEntity,
        Observation,
        TelemetrySnapshot,
    )

    now = datetime.now(UTC)

    def record(content: str, kind: MemoryKind, target_id: str | None) -> MemoryRecord:
        return MemoryRecord(
            memory_id=f"mem-{abs(hash(content)) % 10000:04d}",
            campaign_id="test",
            kind=kind,
            status=MemoryStatus.ACTIVE,
            content=content,
            salience=0.9 if kind is MemoryKind.FACT else 0.1,
            target_id=target_id,
            created_run_id="run-a",
            created_at=now,
        )

    observation = Observation(
        run_id="budget",
        step_index=0,
        mode="mock",
        telemetry=TelemetrySnapshot(
            nearby_entities=[NearbyEntity(id="entity-barman", name="Barman")]
        ),
        memories=[
            record("Deliver six sealed slop canisters.", MemoryKind.COMMITMENT, None),
            record("This barman offers no work.", MemoryKind.FACT, "entity-barman"),
            *[
                record(f"Loud general fact {index} " + "g" * 400, MemoryKind.FACT, None)
                for index in range(8)
            ],
        ],
    )
    payload = observation.model_dump(mode="json")

    retained = budgeted_memories(payload, max_chars=4200)

    assert "Deliver six sealed slop canisters." in retained
    assert "This barman offers no work." in retained
    assert len(retained) < len(observation.memories)


def test_a_query_containing_escape_characters_is_still_literal(
    tmp_path: Path,
) -> None:
    """A backslash is a character in a sentence, not an escape in a pattern."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, r"The path is C:\Kenshi\data.")
        keep(store, "A note about under_scores.")
        keep(store, "Nothing to do with either.")

        backslash = store.search(query=r"C:\Kenshi", limit=4)
        underscore = store.search(query="under_scores", limit=4)
        near_miss = store.search(query="underXscores", limit=4)

    assert [record.content for record in backslash.records] == [
        r"The path is C:\Kenshi\data."
    ]
    assert [record.content for record in underscore.records] == [
        "A note about under_scores."
    ]
    assert near_miss.records == []


def test_a_search_limit_of_one_is_allowed(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, "A gate fact.")

        found = store.search(query="gate", limit=1)

    assert len(found.records) == 1
    assert found.truncated is False


def test_exactly_the_limit_is_not_a_truncation(tmp_path: Path) -> None:
    with open_store(tmp_path / "memory.sqlite3") as store:
        keep(store, "Gate fact one.")
        keep(store, "Gate fact two.")

        exact = store.search(query="gate", limit=2)
        over = store.search(query="gate", limit=1)

    assert exact.matched == 2
    assert exact.truncated is False
    assert over.truncated is True


def test_the_current_target_tier_ranks_across_every_entity_in_view(
    tmp_path: Path,
) -> None:
    """Two entities are one merged ranking, not two half-budgets."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        weak = keep(store, "About A, weakly.", salience=0.1, target_id="entity-a")
        strong = keep(store, "About B, strongly.", salience=0.9, target_id="entity-b")
        middling = keep(store, "About A, mildly.", salience=0.5, target_id="entity-a")

        result = store.recall_tiered(
            budget=budget(current_target=2, general=0),
            target_ids={"entity-a", "entity-b"},
        )

    assert [record.memory_id for record in result.records] == [
        strong.memory_id,
        middling.memory_id,
    ]
    assert weak.memory_id not in {record.memory_id for record in result.records}
    assert result.omitted[RecallTier.CURRENT_TARGET] == 1


def test_a_claimed_record_is_skipped_without_ending_its_tier(
    tmp_path: Path,
) -> None:
    """A bound commitment is claimed by the commitment tier. The target tier has
    to step over it and keep going, not stop there."""

    with open_store(tmp_path / "memory.sqlite3") as store:
        both = keep(
            store,
            "Trade here when the money is there.",
            kind=MemoryKind.COMMITMENT,
            salience=0.9,
            target_id="entity-trader",
        )
        bound = keep(
            store,
            "This trader pays badly for ore.",
            salience=0.5,
            target_id="entity-trader",
        )

        result = store.recall_tiered(
            budget=budget(commitments=2, current_target=2, general=0),
            target_ids={"entity-trader"},
        )

    assert [record.memory_id for record in result.records] == [
        both.memory_id,
        bound.memory_id,
    ]
    assert result.tier_of(bound.memory_id) is RecallTier.CURRENT_TARGET
    assert result.omitted[RecallTier.CURRENT_TARGET] == 0
