# Checkpoint: G14 exact KAE trajectory exporter candidate

This checkpoint covers only the KAE-owned half of Goal 14. The candidate adds
one read-only production boundary, `./dev trajectory-export`, that projects an
exact closed SessionLogger JSONL file into EvoGen's current trajectory envelope.
It does not complete G14 until the KAE commit is public and EvoGen has serially
retired its provisional normalizer in favor of this contract.

## Repository and authority

```text
parent commit             a8584554e30bb793f5b60ef57e3d1500de5aaa12
integration branch        main
starting tree             clean at public main
EvoGen counterpart        6954f8bc1e0ad95a9ccd9486fe58999dce5cf885
source plan revision      G14 proof-first frozen packet
producer protocol         2.0.0
reviewed event types      90
source producer records   128
```

The parent is the completed G13 capability-manifest slice. The EvoGen
counterpart is the public, hosted-green proof-first route and cockpit authority;
it still contains the provisional reader that must be removed only after this
KAE half is committed and public.

The reviewed disposition source remains
`docs/reconstruction/session_event_dispositions.json`. Its generated projection
binds every current producer to one of four meanings: 23 exact EvoGen events,
54 subject-only raw evidence events, 9 derived summaries, and 4 intentionally
ignored events. Export validates that reviewed map against the current source
inventory before accepting a log. The new file writer is explicitly recorded
as a non-event sink, so it cannot evade the source-inventory freshness gate.

## Exact input and output contract

The public command requires an exact events file, linked generation and
capability manifests, an explicit scenario ID, and a new output directory:

```bash
./dev trajectory-export \
  --events <events.jsonl> \
  --generation-manifest <generation.json> \
  --capability-manifest <capability.json> \
  --scenario-id <scenario-id> \
  --output <new-bundle-directory>
```

Publication is atomic and refuses overwrite or symlink traversal. The bundle
contains:

- `raw-events.jsonl`, byte-identical to every source record;
- `trajectory.jsonl`, strict current-envelope events in eligible source
  encounter order; and
- `manifest.json`, a strict typed record of bundle identity, exact input and
  output digests, generation/capability/scenario/run identities, raw and
  normalized counts, source-identity coverage, source-inventory identity, and
  reviewed-disposition counts.

The manifest labels generation linkage as `supplied_external_manifest`: G14
validates the supplied generation/capability pair and binds its exact bytes,
but the SessionEvent envelope itself has no generation field. Scenario linkage
is separately `generation_manifest` when the manifest contains matching
scenario evidence and `declared_external` otherwise. G16 owns the later
run-bundle and RunRecord authority that can make stronger contemporaneous
provenance claims.

The trajectory event and export-manifest JSON schemas are generated and owned
by the repository freshness gate. KAE imports no EvoGen package.

## Ordering, correlation, and evidence semantics

`event_sequence` is optional only for one legacy prefix. Once a sequenced
suffix begins it must be canonical and contiguous, and it may not return to
null. Normalized `sequence` is always the zero-based encounter order of exact
events. Timestamp and `step_index` never reorder evidence.

Legacy records do not invent source sequence or event identity. The manifest
reports present and missing counts, while deterministic exporter-owned event
IDs use the source ordinal so duplicate legacy records remain distinct.

Structured KAE revisions remain intact in `payload.correlation` alongside
available command, outcome, plan, step, and identity-session IDs. The generic
envelope receives only an opaque `kae-revision-sha256:<digest>` identity, using
completed revision before general world revision before starting revision.
Missing IDs are never joined or inferred.

The reviewed exact projection includes:

- `action_receipt` as `execution_receipt`;
- `action_outcome` and `order_disposition_observed` as
  `outcome_observation`;
- `world_state_update` as `observation_delta`;
- `observation` as `observation`;
- `affordance_set` as `affordance_set`;
- `decision` and `plan_proposed` as `decision`;
- exact run start/finish; and
- reviewed error and recovery events.

Binding and dispatch remain explicitly withheld because KAE has no exact
reviewed event authority for either. A receipt or native acknowledgement is not
promoted into a dispatch, binding, completion, success, or world effect.

## Real-bundle evidence

The current retained local soak file
`runs/protocol-2-native-survival-soak-20260810-r9/events.jsonl` has SHA-256
`542eff1353e00b9cd4cad4c83969e4db9156776d7c55b5e51d01a0356ffb92ef`.
The public command processed all 38,293 byte-retained records under one run ID
with zero unreviewed types. All 38,293 truthfully lack source sequence and event
ID. The disposition totals are 22,995 exact, 14,667 subject-only raw, 390
derived, and 241 ignored.

The 22,995 normalized records contain 11,308 observations, 11,308 observation
deltas, 126 outcome observations, 121 decisions, 119 execution receipts, 11
errors, one run start, and one run finish. Every record round-tripped through
EvoGen `TrajectoryEvent` and `parse_trajectory_event_record`; sequences were
exactly `0..22994`, event IDs were unique, and run/generation identity was
singular. This is source, portable, and replay-preparation evidence, not live
world-effect evidence. The historical soak predates G11 and has no
contemporaneous generation manifest in its run directory, so this acceptance
run proves exporter compatibility and exact supplied-manifest binding, not the
identity of the generation that originally produced the soak.

The checked-in 45-record reporting fixture is the portable analogue. It
projects 9 exact events while retaining all 45 raw records and is exercised in
clean clones. The larger ignored soak is exercised when available and skipped
honestly when absent.

## Withheld claims and completion boundary

This candidate does not:

- register the G15 subject plugin or run the G16 observer/replay path;
- create a G16 `RunRecord` or G17 metric mapping;
- launch Kenshi, dispatch input, modify a save, build or install a DLL, or
  change native/runtime/environment/evaluator behavior;
- claim that acceptance, execution, acknowledgement, or process exit proves a
  later world effect; or
- prove that EvoGen has consumed this contract yet.

The KAE half requires root review, the full portable gate, an exact commit,
public push, and hosted CI. Only then may the EvoGen half begin. G15 remains
unstarted even after KAE publication; root must close and ratchet the complete
cross-repository G14 first.

## Verification

```bash
PYTEST_ADDOPTS='-p no:cacheprovider' UV_CACHE_DIR=/tmp/kae-g14-cache \
  uv run --frozen --no-sync pytest -q tests/test_trajectory_export.py \
  tests/test_dev_entrypoint.py tests/test_session_event_dispositions.py \
  tests/test_docs_hygiene.py --color=no
UV_CACHE_DIR=/tmp/kae-g14-cache ./dev verify-portable
git diff --check
```

Faraday authored the isolated first candidate. Curie independently mapped the
current contracts, exact large-bundle counts, and adversarial falsifiers. Root
rejected the two-test handback as insufficient, repaired the typed/schema and
correlation boundaries, expanded fail-closed coverage, and reproduced the
large-bundle projection plus exact EvoGen schema round-trip. Curie then passed
the repaired candidate against her acceptance matrix. Averroes independently
passed the current tree after verifying the public fixture and actual EvoGen
reader, and identified two pre-publication hardening opportunities: null-aware
revision precedence and byte-exact linked-manifest participation in bundle
identity. Root implemented both with regression tests and reran the complete
portable gate successfully. Commit, publication, and hosted CI remain pending
in this uncommitted checkpoint.
