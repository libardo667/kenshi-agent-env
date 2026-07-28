"""Lossless compaction is an explicit atomic lifecycle, not summary-shaped deletion."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kenshi_agent import cli
from kenshi_agent.campaign import CampaignScope, CampaignScopeOrigin
from kenshi_agent.config import load_config
from kenshi_agent.memory import MemoryStore
from kenshi_agent.memory_compaction import (
    MemoryCompactionError,
    build_lossless_compaction_candidate,
    source_fingerprint,
)
from kenshi_agent.models import (
    CanonicalMemoryProvenance,
    CompactionMethod,
    ContinuityOrigin,
    KeepMemoryOperation,
    MemoryAuthorship,
    MemoryCompactionCandidate,
    MemoryKind,
    MemoryLifecycleEvent,
    MemoryRecord,
    MemoryResolutionDisposition,
    MemoryStatus,
    WorldStateRevision,
)


def scope(campaign_id: str = "compaction-campaign") -> CampaignScope:
    return CampaignScope(
        campaign_id=campaign_id,
        origin=CampaignScopeOrigin.CONFIGURED,
    )


def store(path: Path, campaign_id: str = "compaction-campaign") -> MemoryStore:
    ids = iter(f"mem-{index:04d}" for index in range(1, 100))
    return MemoryStore(
        path,
        scope(campaign_id),
        memory_id_factory=lambda: next(ids),
    )


def keep(
    memories: MemoryStore,
    content: str,
    *,
    kind: MemoryKind = MemoryKind.EPISODE,
    target_id: str | None = "entity-a",
    salience: float = 0.5,
):
    return memories.keep(
        "source-run",
        kind=kind,
        content=content,
        salience=salience,
        grounding=f"grounding:{content}",
        target_id=target_id,
    )


def keep_with_authorship(
    memories: MemoryStore,
    content: str,
    *,
    authorship: MemoryAuthorship,
) -> MemoryRecord:
    now = datetime.now(UTC)
    record = MemoryRecord(
        memory_id=memories._new_memory_id(),
        campaign_id=memories.campaign_id,
        kind=MemoryKind.EPISODE,
        status=MemoryStatus.ACTIVE,
        content=content,
        salience=0.5,
        grounding=f"grounding:{content}",
        authorship=authorship,
        target_id="entity-a",
        created_run_id="source-run",
        created_at=now,
    )
    return memories._commit_keep(record, "source-run", now)


def test_lossless_compaction_atomically_supersedes_exact_sources_and_rebuilds(
    tmp_path: Path,
) -> None:
    path = tmp_path / "continuity.sqlite3"
    with store(path) as memories:
        first = keep(memories, "The western gate was open at dawn.", salience=0.4)
        second = keep(memories, "Fog made the western road hard to follow.", salience=0.8)
        candidate = build_lossless_compaction_candidate([second, first])

        assert candidate.method is CompactionMethod.LOSSLESS
        assert candidate.source_memory_ids == [first.memory_id, second.memory_id]
        assert candidate.content == (
            'Verbatim memory bundle: ["The western gate was open at dawn.",'
            ' "Fog made the western road hard to follow."]'
        )
        replacement = memories.compact("compaction-run", candidate)

        assert replacement.kind is MemoryKind.EPISODE
        assert replacement.status is MemoryStatus.ACTIVE
        assert replacement.authorship is MemoryAuthorship.AGENT_AUTHORED
        assert replacement.salience == 0.8
        assert replacement.target_id == "entity-a"
        assert replacement.grounding == (
            f"lossless_compaction({first.memory_id},{second.memory_id})"
        )
        assert replacement.created_run_id == "compaction-run"
        assert replacement.created_at.tzinfo is UTC
        assert replacement.latest_provenance is not None
        assert replacement.latest_provenance.provenance_kind == "compaction"
        assert replacement.latest_provenance.candidate == candidate
        assert replacement.latest_provenance.applied_run_id == "compaction-run"
        assert (
            replacement.latest_provenance.replacement_memory_id
            == replacement.memory_id
        )
        assert replacement.latest_provenance.applied_at == replacement.created_at
        before_rebuild = memories.all_records()
        closed = [memories.get(first.memory_id), memories.get(second.memory_id)]
        assert all(record is not None for record in closed)
        assert [record.status for record in closed if record is not None] == [
            MemoryStatus.SUPERSEDED,
            MemoryStatus.SUPERSEDED,
        ]
        assert {
            record.superseded_by_id for record in closed if record is not None
        } == {replacement.memory_id}
        assert [
            entry.event for entry in memories.history(first.memory_id)
        ] == [MemoryLifecycleEvent.KEEP, MemoryLifecycleEvent.SUPERSEDE]
        assert [
            entry.event for entry in memories.history(second.memory_id)
        ] == [MemoryLifecycleEvent.KEEP, MemoryLifecycleEvent.SUPERSEDE]

        event_count = memories.event_count()
        assert memories.rebuild_projection() == event_count
        assert memories.all_records() == before_rebuild


def test_lossless_compaction_preserves_unverified_authorship(tmp_path: Path) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep_with_authorship(
            memories,
            "Legacy episode one.",
            authorship=MemoryAuthorship.LEGACY_UNVERIFIED,
        )
        second = keep_with_authorship(
            memories,
            "Legacy episode two.",
            authorship=MemoryAuthorship.LEGACY_UNVERIFIED,
        )
        replacement = memories.compact(
            "compaction-run",
            build_lossless_compaction_candidate([first, second]),
        )

        assert replacement.authorship is MemoryAuthorship.LEGACY_UNVERIFIED
        assert memories.get(replacement.memory_id) == replacement


def test_lossless_compaction_refuses_an_existing_compacted_identity(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
        candidate = build_lossless_compaction_candidate([first, second])
        conflict = keep(memories, candidate.content)
        before_records = memories.all_records()
        before_events = memories.event_count()

        with pytest.raises(MemoryCompactionError, match="already has"):
            memories.compact("compaction-run", candidate)

        assert memories.all_records() == before_records
        assert memories.event_count() == before_events
        assert memories.get(conflict.memory_id) == conflict


def test_source_fingerprint_is_a_stable_contract_for_every_durable_field() -> None:
    revision = WorldStateRevision(
        telemetry_sequence=7,
        frame_sequence=3,
        capability_epoch=2,
        observed_at_monotonic=1.25,
    )
    provenance = CanonicalMemoryProvenance(
        operation=KeepMemoryOperation(
            kind=MemoryKind.HYPOTHESIS,
            content="Route Ω may be blocked.",
            salience=0.6,
            target_id="entity-Ω",
        ),
        origin=ContinuityOrigin.PLAN,
        run_id="source-run",
        authored_context_id="pc-1",
        authored_revision=revision,
        commit_revision=revision,
        plan_id="plan-a",
        plan_version=2,
        step_id="step-a",
        rendered_grounding="The attempt was inconclusive.",
    )
    record = MemoryRecord(
        memory_id="mem-contract",
        campaign_id="campaign-contract",
        kind=MemoryKind.HYPOTHESIS,
        status=MemoryStatus.SUPERSEDED,
        content="Route Ω may be blocked.",
        salience=0.6,
        grounding="The attempt was inconclusive.",
        latest_provenance=provenance,
        authorship=MemoryAuthorship.LEGACY_UNVERIFIED,
        target_id="entity-Ω",
        created_run_id="source-run",
        created_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        reinforced_at=datetime(2026, 7, 28, 12, 5, tzinfo=UTC),
        resolved_at=datetime(2026, 7, 28, 12, 10, tzinfo=UTC),
        superseded_at=datetime(2026, 7, 28, 12, 15, tzinfo=UTC),
        last_delivered_at=datetime(2026, 7, 28, 12, 20, tzinfo=UTC),
        reinforcement_count=3,
        supersedes_id="mem-earlier",
        superseded_by_id="mem-later",
        resolution_reason="Replaced after later evidence.",
        resolution_disposition=MemoryResolutionDisposition.UNKNOWN,
    )

    assert source_fingerprint(record) == (
        "c013dbe2eca960dcd790a5e2451e0ca78a2402811d90681d17bf8abdcc832dbb"
    )
    assert source_fingerprint(
        record.model_copy(
            update={
                "last_delivered_at": datetime(
                    2026,
                    7,
                    28,
                    13,
                    0,
                    tzinfo=UTC,
                )
            }
        )
    ) == source_fingerprint(record)


def test_candidate_has_one_exact_deterministic_contract(tmp_path: Path) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep(
            memories,
            "First Ω episode.",
            salience=0.4,
        )
        second = keep(
            memories,
            "Second episode.",
            salience=0.8,
        )

    candidate = build_lossless_compaction_candidate([second, first])

    assert candidate.model_dump(
        mode="json",
        exclude={"candidate_id", "generated_at"},
    ) == {
        "schema_version": 1,
        "method": "lossless",
        "campaign_id": "compaction-campaign",
        "source_memory_ids": [first.memory_id, second.memory_id],
        "source_fingerprints": {
            first.memory_id: source_fingerprint(first),
            second.memory_id: source_fingerprint(second),
        },
        "kind": "episode",
        "content": (
            'Verbatim memory bundle: ["First Ω episode.",'
            ' "Second episode."]'
        ),
        "salience": 0.8,
        "target_id": "entity-a",
        "authorship": "agent_authored",
        "generator": {
            "provider": "local",
            "model": "lossless-v1",
            "prompt_version": None,
            "prompt_sha256": None,
            "parameters": {
                "content": "verbatim_json_array",
                "ordering": "memory_id",
                "salience": "maximum",
            },
        },
    }


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda records, other: [records[0]],
            "two to eight",
        ),
        (
            lambda records, other: [
                records[0],
                other.model_copy(update={"memory_id": "mem-foreign"}),
            ],
            "one campaign",
        ),
        (
            lambda records, other: [
                records[0],
                records[1].model_copy(update={"kind": MemoryKind.FACT}),
            ],
            "one kind",
        ),
        (
            lambda records, other: [
                records[0],
                records[1].model_copy(update={"target_id": "entity-b"}),
            ],
            "one exact target",
        ),
        (
            lambda records, other: [
                records[0],
                records[1].model_copy(
                    update={"authorship": MemoryAuthorship.LEGACY_UNVERIFIED}
                ),
            ],
            "one authorship",
        ),
        (
            lambda records, other: [
                records[0],
                records[1].model_copy(update={"status": MemoryStatus.RETRACTED}),
            ],
            "active",
        ),
        (
            lambda records, other: [
                record.model_copy(update={"kind": MemoryKind.COMMITMENT})
                for record in records
            ],
            "commitments and hypotheses",
        ),
    ],
)
def test_candidate_builder_rejects_every_incompatible_source_class(
    tmp_path: Path,
    mutate,
    reason: str,
) -> None:
    with store(tmp_path / "first.sqlite3") as memories:
        records = [keep(memories, "First episode."), keep(memories, "Second episode.")]
    with store(tmp_path / "other.sqlite3", "other-campaign") as other_store:
        other = keep(other_store, "Foreign episode.")

    with pytest.raises(MemoryCompactionError, match=reason):
        build_lossless_compaction_candidate(mutate(records, other))


def test_candidate_fingerprint_refuses_later_source_change(tmp_path: Path) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
        candidate = build_lossless_compaction_candidate([first, second])
        memories.reinforce(
            "later-run",
            second.memory_id,
            salience=0.9,
            grounding="Later evidence.",
        )

        with pytest.raises(MemoryCompactionError, match="changed after"):
            memories.compact("compaction-run", candidate)
        assert [record.status for record in memories.all_records()] == [
            MemoryStatus.ACTIVE,
            MemoryStatus.ACTIVE,
        ]


@pytest.mark.parametrize(
    "tamper",
    [
        lambda candidate: candidate.model_copy(
            update={"campaign_id": "another-campaign"}
        ),
        lambda candidate: candidate.model_copy(
            update={"content": "A plausible but invented summary."}
        ),
        lambda candidate: candidate.model_copy(update={"salience": 0.99}),
        lambda candidate: candidate.model_copy(update={"kind": MemoryKind.FACT}),
        lambda candidate: candidate.model_copy(update={"target_id": "entity-b"}),
        lambda candidate: candidate.model_copy(
            update={"authorship": MemoryAuthorship.LEGACY_UNVERIFIED}
        ),
        lambda candidate: candidate.model_copy(
            update={
                "source_fingerprints": {
                    **candidate.source_fingerprints,
                    candidate.source_memory_ids[0]: "0" * 64,
                }
            }
        ),
        lambda candidate: candidate.model_copy(
            update={
                "generator": candidate.generator.model_copy(
                    update={"provider": "uninspected-provider"}
                )
            }
        ),
    ],
)
def test_applying_any_tampered_candidate_changes_nothing(
    tmp_path: Path,
    tamper,
) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
        candidate = tamper(
            build_lossless_compaction_candidate([first, second])
        )
        before_records = memories.all_records()
        before_events = memories.event_count()

        with pytest.raises(MemoryCompactionError):
            memories.compact("compaction-run", candidate)

        assert memories.all_records() == before_records
        assert memories.event_count() == before_events


def test_delivery_bookkeeping_does_not_invalidate_semantically_unchanged_sources(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
        candidate = build_lossless_compaction_candidate([first, second])

        memories.record_delivery(
            "delivery-run",
            [first.memory_id, second.memory_id],
        )
        replacement = memories.compact("compaction-run", candidate)

        assert replacement.status is MemoryStatus.ACTIVE
        assert [
            entry.event for entry in memories.history(first.memory_id)
        ] == [
            MemoryLifecycleEvent.KEEP,
            MemoryLifecycleEvent.DELIVER,
            MemoryLifecycleEvent.SUPERSEDE,
        ]


def test_an_applied_candidate_cannot_be_replayed(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
        candidate = build_lossless_compaction_candidate([first, second])
        memories.compact("first-compaction-run", candidate)
        before_records = memories.all_records()
        before_events = memories.event_count()

        with pytest.raises(MemoryCompactionError, match="active"):
            memories.compact("replay-run", candidate)

        assert memories.all_records() == before_records
        assert memories.event_count() == before_events


def test_candidate_bounds_refuse_oversized_source_sets_and_content(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        too_many = [
            keep(memories, f"Episode {index}.", target_id=None)
            for index in range(9)
        ]
        with pytest.raises(MemoryCompactionError, match="two to eight"):
            build_lossless_compaction_candidate(too_many)

    with store(tmp_path / "large.sqlite3") as memories:
        large = [
            keep(memories, "A" * 1000, target_id=None),
            keep(memories, "B" * 1000, target_id=None),
        ]
        with pytest.raises(MemoryCompactionError, match="exceeds 2000"):
            build_lossless_compaction_candidate(large)

    exact_overhead = len('Verbatim memory bundle: ["", ""]')
    with store(tmp_path / "exact-limit.sqlite3") as memories:
        at_limit = [
            keep(memories, "A", target_id=None),
            keep(
                memories,
                "B" * (2000 - exact_overhead - 1),
                target_id=None,
            ),
        ]
        assert len(
            build_lossless_compaction_candidate(at_limit).content
        ) == 2000
        one_over = [
            at_limit[0],
            at_limit[1].model_copy(
                update={"content": at_limit[1].content + "B"}
            ),
        ]
        with pytest.raises(MemoryCompactionError, match="exceeds 2000"):
            build_lossless_compaction_candidate(one_over)

    with store(tmp_path / "maximum.sqlite3") as memories:
        maximum = [
            keep(memories, f"Episode {index}.", target_id=None)
            for index in range(8)
        ]
        assert len(
            build_lossless_compaction_candidate(maximum).source_memory_ids
        ) == 8


def test_compaction_rolls_back_every_source_and_event_on_late_store_failure(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
        candidate = build_lossless_compaction_candidate([first, second])
        event_count = memories.event_count()
        memories._connection.execute(
            f"""
            CREATE TEMP TRIGGER fail_second_compaction_source
            BEFORE UPDATE OF status ON memories
            WHEN OLD.memory_id = '{second.memory_id}'
              AND NEW.status = 'superseded'
            BEGIN
                SELECT RAISE(ABORT, 'injected compaction failure');
            END
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            memories.compact("compaction-run", candidate)

        assert memories.event_count() == event_count
        assert memories.get(first.memory_id) == first
        assert memories.get(second.memory_id) == second
        assert memories.all_records() == [first, second]


def test_cli_dry_run_is_read_only_and_applies_only_the_inspected_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "continuity.sqlite3"
    with store(path) as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
    config = load_config(
        Path(__file__).resolve().parents[1] / "config" / "default.yaml"
    )
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"memory_db": path})}
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    before = path.read_bytes()

    assert (
        cli.main(
            [
                "compact-memory",
                "--config",
                "unused",
                "--campaign",
                "compaction-campaign",
                "--source",
                second.memory_id,
                "--source",
                first.memory_id,
            ]
        )
        == 0
    )
    candidate_document = capsys.readouterr().out
    candidate = json.loads(candidate_document)
    assert candidate["source_memory_ids"] == [first.memory_id, second.memory_id]
    assert path.read_bytes() == before

    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(candidate_document, encoding="utf-8")
    assert (
        cli.main(
            [
                "compact-memory",
                "--config",
                "unused",
                "--campaign",
                "compaction-campaign",
                "--apply-candidate",
                str(candidate_path),
            ]
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["candidate"] == candidate
    assert applied["replacement"]["latest_provenance"]["candidate"] == candidate
    replacement_id = applied["replacement"]["memory_id"]
    with MemoryStore(path, scope()) as reopened:
        assert reopened.get(first.memory_id).superseded_by_id == replacement_id  # type: ignore[union-attr]
        assert reopened.get(second.memory_id).superseded_by_id == replacement_id  # type: ignore[union-attr]
        assert reopened.get(replacement_id).status is MemoryStatus.ACTIVE  # type: ignore[union-attr]


def test_cli_refuses_malformed_and_foreign_candidates_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "continuity.sqlite3"
    with store(path) as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
        candidate = build_lossless_compaction_candidate([first, second])
    config = load_config(
        Path(__file__).resolve().parents[1] / "config" / "default.yaml"
    )
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"memory_db": path})}
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    before = path.read_bytes()

    for document in (
        "{",
        candidate.model_copy(
            update={"campaign_id": "foreign-campaign"}
        ).model_dump_json(),
    ):
        candidate_path = tmp_path / "candidate.json"
        candidate_path.write_text(document, encoding="utf-8")
        assert (
            cli.main(
                [
                    "compact-memory",
                    "--config",
                    "unused",
                    "--campaign",
                    "compaction-campaign",
                    "--apply-candidate",
                    str(candidate_path),
                ]
            )
            == 1
        )
        assert "Compaction refused:" in capsys.readouterr().err
        assert path.read_bytes() == before


def test_candidate_document_rejects_every_invalid_source_identity_shape(
    tmp_path: Path,
) -> None:
    with store(tmp_path / "continuity.sqlite3") as memories:
        first = keep(memories, "First episode.")
        second = keep(memories, "Second episode.")
        candidate = build_lossless_compaction_candidate([first, second])
    payload = candidate.model_dump(mode="json")

    for source_ids, fingerprints in (
        (
            [first.memory_id, first.memory_id],
            candidate.source_fingerprints,
        ),
        (
            list(reversed(candidate.source_memory_ids)),
            candidate.source_fingerprints,
        ),
        (
            candidate.source_memory_ids,
            {first.memory_id: candidate.source_fingerprints[first.memory_id]},
        ),
        (
            candidate.source_memory_ids,
            {
                **candidate.source_fingerprints,
                first.memory_id: "0" * 63,
            },
        ),
        (
            candidate.source_memory_ids,
            {
                **candidate.source_fingerprints,
                first.memory_id: "A" * 64,
            },
        ),
        (
            candidate.source_memory_ids,
            {
                **candidate.source_fingerprints,
                first.memory_id: "x" * 64,
            },
        ),
        (
            candidate.source_memory_ids,
            {
                **candidate.source_fingerprints,
                first.memory_id: "X" * 64,
            },
        ),
    ):
        document = {
            **payload,
            "source_memory_ids": source_ids,
            "source_fingerprints": fingerprints,
        }
        with pytest.raises(ValueError):
            MemoryCompactionCandidate.model_validate(document)
