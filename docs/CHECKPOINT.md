# Checkpoint: Run-local session event sequence

Goal 10 gives every newly written KAE session-log record a logger-owned,
one-based `event_sequence`. The number records serialized log order only. It is
not an environment step, telemetry revision, charged planner turn, dispatch
acknowledgement, or proof that the world changed.

This is a logger, compatibility, and test checkpoint. It changes no gameplay,
environment, evaluator, native-controller, or overlay behavior and makes no new
live-game claim.

## Repository and authority

```text
parent commit          b8544b88b8c610f2859298308b0adaca290c9ddc
integration branch     main
starting tree          clean
EvoGen counterpart     cf70589f91add9c9dbe6affd11866b20d2690642
source plan revision   2026-08-10T21:25:08.835Z
producer protocol      2.0.0
disposition schema     1
```

The parent commit is the completed G09 source-event inventory. It remains the
authority for which session events exist and how they map toward EvoGen. G10
adds record identity without changing that 89-event semantic denominator.

## Serialized order contract

`SessionLogger` owns the next sequence for one run. A fresh log starts at 1.
Sequence allocation, JSON serialization, the append, and the immediate flush
share one lock, so the order in `events.jsonl` is the sequence order even when
many producer threads write at the same gameplay step.

The logger retires a sequence before attempting I/O. This matters when a write
or flush failure is ambiguous about whether bytes reached the file: a later
write cannot reuse an identity that may already be present. Normal successful
writes remain contiguous; an uncertain failed write may leave a gap rather
than a duplicate.

The existing append API is continuation-safe for one `SessionLogger` lifecycle
at a time:

- existing records for the same run seed the next value;
- legacy records without `event_sequence` retain their positions when the next
  value is chosen;
- a complete final JSON object without a newline is preserved and terminated;
- a provably invalid unterminated crash tail is removed before appending; and
- records belonging to another run do not advance this run's local counter.

Production already creates a new run directory with `exist_ok=False` and shares
one logger object throughout the runtime. G10 does not claim coordination
between two logger objects or processes writing the same path, nor `fsync`
power-loss durability.

## Separate gameplay and telemetry fields

`step_index` remains a distinct nullable gameplay field. Several session events
can share one step, and run-level events can have no step. Nested telemetry
sequence values remain subject evidence and can repeat across several log
records. The overlay continues to derive human-facing charged turns from
budget reserve/commit/release events; it does not display `event_sequence` as a
turn or world revision.

The focused concurrency proof writes 1,024 events from eight threads. Every
event uses `step_index=7` and the same nested telemetry revision. The test
requires all producer identities to survive, and requires file-order sequences
to be exactly `1..1024`. A separate committed fixture records sequences `1..4`
across a nullable run record and three events sharing step 7 and telemetry
revision 42.

## Backward readability

`SessionEvent.event_sequence` is optional and positive when present. That lets
the typed outer-record contract accept the new field while still validating
legacy dictionary-payload records that omit it. The 45-record
`live_reporting_surface` fixture deliberately remains unchanged and contains
no `event_sequence`; replay, metrics, reporting, overlay, and context-menu
readers continue to consume outer records as ordinary mappings and ignore
unknown or absent sequence metadata.

The new fixture lives at
`tests/fixtures/session_logs/event_sequence.jsonl`. Historical ignored
`runs/*/events.jsonl` files were not rewritten.

## G09 freshness after the logger migration

The source-derived denominator remains **89 event types** and **127 producer
records**. The logger's crash-tail separator is a new reviewed non-event write,
so the generated artifact now records **17 open boundaries**: seven event-type
pass-throughs and ten reviewed non-event writes. Its source fingerprint is
`232a934c595657d6189d9ab39dc347b4af711d86814fc071ec263e51da3ad60b`.

No disposition row changed. The generated artifact was refreshed through the
checked-in exporter, and the G09 freshness tests continue to fail closed on
unreviewed events, aliases, or writer boundaries.

## Adversarial review and withheld claims

Lamport, Hoare, and Dijkstra independently audited writer lifecycle, consumer
compatibility, and the concurrency falsifier before implementation. Their
candidate review found two real defects: sequence reuse after an ambiguous
flush failure and retention of an invalid final crash fragment. Both received
dedicated regression tests and narrow logger repairs. Lamport reran the exact
failure probes and passed the repaired candidate; the full portable gate also
passed afterward.

The following remain explicitly withheld:

- event sequence does not certify dispatch, acceptance, completion, a world
  effect, goal progress, or goal achievement;
- it is not a global, cross-run, cross-process, telemetry, or gameplay clock;
- G10 does not add an EvoGen exporter or trajectory envelope;
- G11 generation-manifest and provenance work remains unstarted; and
- no live process, save, DLL, or game state was changed to prove this slice.

## Completion boundary and next goal

G10 stops after the logger migration is independently reviewed, the complete
portable gate passes, this checkpoint is committed on `main`, the public push
is clean, and hosted CI is green. Only then may the central EvoGen plan ratchet
mark G10 complete and name G11 as the sole next goal.

G11 owns generation provenance, effective configuration, secrets exclusion,
and the generation manifest. G12 still owns the authoritative planner-visible
affordance-set event, and G14 owns the production KAE exporter.

## Verification

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache uv run --frozen --extra dev pytest -p no:cacheprovider -q \
  tests/test_session_log.py tests/test_session_event_dispositions.py \
  tests/test_checkpoint_freshness.py tests/test_overlay.py tests/test_replay.py \
  tests/test_metrics.py
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
git diff --check
```

The focused logger, compatibility, disposition, replay, overlay, and evaluator
tests pass on the repaired candidate. The complete portable gate also passes:
locked dependency sync, Ruff, strict mypy over 150 source files, research
validation, event-map/schema/document freshness, the full pytest suite, and
whitespace checks. Hosted CI remains the post-push completion authority.
