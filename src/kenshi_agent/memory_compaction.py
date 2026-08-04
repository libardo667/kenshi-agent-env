"""Deterministic, lossless candidates for bounded durable-memory compaction.

This module proposes and validates. It never writes. The owning ``MemoryStore``
re-resolves every fingerprint and performs the lifecycle transition atomically.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from .core.continuity import (
    CompactionMethod,
    MemoryCompactionCandidate,
    MemoryCompactionGenerator,
    MemoryKind,
    MemoryRecord,
    MemoryStatus,
    new_memory_compaction_candidate_id,
)

_MIN_SOURCES = 2
_MAX_SOURCES = 8
_CONTENT_PREFIX = "Verbatim memory bundle: "


class MemoryCompactionError(ValueError):
    """A candidate could not conserve the exact selected source records."""


# These are standard-library serialization adapters. Mutmut treats the falsey
# ``None`` spelling as a survivor even though it is byte-identical to ``False``.
# Golden behavior tests protect both byte-level outputs instead.
# pragma: no mutate start
def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _unicode_json_array(values: Sequence[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


# pragma: no mutate end


def source_fingerprint(record: MemoryRecord) -> str:
    """Hash every durable semantic field while ignoring delivery bookkeeping."""

    # Exclusion is safer than an allowlist: a future durable field enters the
    # fingerprint automatically instead of silently becoming mutable.
    payload = record.model_dump(
        mode="json",
        exclude={"last_delivered_at"},
    )
    # Codec-name case is a standard-library alias, covered by the golden hash.
    return hashlib.sha256(
        _canonical_json(payload).encode("utf-8")  # pragma: no mutate
    ).hexdigest()


def _validated_sources(records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
    if not _MIN_SOURCES <= len(records) <= _MAX_SOURCES:
        raise MemoryCompactionError(  # mutation: reason
            "Compaction requires two to eight sources."  # mutation: reason
        )
    ordered = sorted(records, key=lambda record: record.memory_id)
    if len({record.memory_id for record in ordered}) != len(ordered):
        raise MemoryCompactionError(  # mutation: reason
            "Compaction source IDs must be unique."  # mutation: reason
        )
    if len({record.campaign_id for record in ordered}) != 1:
        raise MemoryCompactionError(  # mutation: reason
            "Compaction sources must belong to one campaign."  # mutation: reason
        )
    if any(record.status is not MemoryStatus.ACTIVE for record in ordered):
        raise MemoryCompactionError(  # mutation: reason
            "Every compaction source must still be active."  # mutation: reason
        )
    if len({record.kind for record in ordered}) != 1:
        raise MemoryCompactionError(  # mutation: reason
            "Compaction sources must have one kind."  # mutation: reason
        )
    # Every kind is equal above, so choosing another representative is an
    # equivalent mutation rather than a behavioral alternative.
    if ordered[0].kind in {  # pragma: no mutate
        MemoryKind.COMMITMENT,
        MemoryKind.HYPOTHESIS,
    }:
        raise MemoryCompactionError(  # mutation: reason
            "Open commitments and hypotheses are excluded from compaction."  # mutation: reason
        )
    if len({record.target_id for record in ordered}) != 1:
        raise MemoryCompactionError(  # mutation: reason
            "Compaction sources must share one exact target identity."  # mutation: reason
        )
    if len({record.authorship for record in ordered}) != 1:
        raise MemoryCompactionError(  # mutation: reason
            "Compaction sources must have one authorship class."  # mutation: reason
        )
    return ordered


def _lossless_content(records: Sequence[MemoryRecord]) -> str:
    content = _CONTENT_PREFIX + _unicode_json_array([record.content for record in records])
    if len(content) > 2000:
        raise MemoryCompactionError(  # mutation: reason
            "The verbatim compaction candidate exceeds 2000 characters."  # mutation: reason
        )
    return content


def build_lossless_compaction_candidate(
    records: Sequence[MemoryRecord],
) -> MemoryCompactionCandidate:
    """Build one inspectable candidate without paraphrasing any source."""

    ordered = _validated_sources(records)
    return MemoryCompactionCandidate(
        candidate_id=new_memory_compaction_candidate_id(),
        method=CompactionMethod.LOSSLESS,
        # These representative accesses are equivalent by _validated_sources;
        # the group invariants and complete candidate payload are both tested.
        campaign_id=ordered[0].campaign_id,  # pragma: no mutate
        source_memory_ids=[record.memory_id for record in ordered],
        source_fingerprints={record.memory_id: source_fingerprint(record) for record in ordered},
        kind=ordered[0].kind,  # pragma: no mutate
        content=_lossless_content(ordered),
        salience=max(record.salience for record in ordered),
        target_id=ordered[0].target_id,  # pragma: no mutate
        authorship=ordered[0].authorship,  # pragma: no mutate
        generator=MemoryCompactionGenerator(
            provider="local",
            model="lossless-v1",
            parameters={
                "content": "verbatim_json_array",
                "ordering": "memory_id",
                "salience": "maximum",
            },
        ),
    )


def validate_lossless_compaction_candidate(
    candidate: MemoryCompactionCandidate,
    records: Sequence[MemoryRecord],
) -> list[MemoryRecord]:
    """Recompute the entire deterministic candidate against current sources."""

    if candidate.method is not CompactionMethod.LOSSLESS:
        raise MemoryCompactionError(  # mutation: reason
            f"Unsupported compaction method {candidate.method.value!r}."  # mutation: reason
        )
    ordered = _validated_sources(records)
    current_ids = [record.memory_id for record in ordered]
    if candidate.source_memory_ids != current_ids:
        raise MemoryCompactionError(  # mutation: reason
            "Compaction candidate source IDs do not match current sources."  # mutation: reason
        )
    current_fingerprints = {record.memory_id: source_fingerprint(record) for record in ordered}
    if candidate.source_fingerprints != current_fingerprints:
        raise MemoryCompactionError(  # mutation: reason
            "A compaction source changed after the candidate was generated."  # mutation: reason
        )
    expected = build_lossless_compaction_candidate(ordered).model_copy(
        update={
            "candidate_id": candidate.candidate_id,
            "generated_at": candidate.generated_at,
        }
    )
    if candidate != expected:
        raise MemoryCompactionError(  # mutation: reason
            "The lossless compaction candidate does not exactly conserve "  # mutation: reason
            "its selected sources."  # mutation: reason
        )
    return ordered
