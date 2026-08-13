# Checkpoint: G13 capability-manifest candidate

G13 projects KAE's existing operation, native protocol, continuity, outcome,
recovery, and proof authorities into one exact EvoGen-compatible capability
manifest. Proof-ledger subcases are retained as evidence; the weakest subcase
controls the exported evidence state.

This candidate changes only the offline projection and generic contract boundary.
It does not launch Kenshi, dispatch input, modify a save, install a DLL, change
native behavior, or export a production trajectory.

## Repository and authority

```text
parent commit          0560b9de6e049f0dc06fab9afbef76f76d198092
integration branch     main
starting tree          clean
EvoGen counterpart     4270e8332f8a03757b39a306b2e936ac8a618cc3
source plan revision   G13 frozen packet
capability schema      EvoGen CapabilityDefinition with typed evidence_state
producer protocol      2.0.0
```

The parent commit is the completed G12 affordance-set slice. EvoGen commit
`4270e8332f8a03757b39a306b2e936ac8a618cc3` is the G13 prerequisite contract
on public main. This candidate
derives rows from existing authorities and leaves native code, controller
protocol, telemetry behavior, environment behavior, and evaluation rules
unchanged.

## One generated authority and one freshness owner

`kenshi_agent.tooling.capability_manifest` is the sole generated capability
projection. Operation rows derive from `OPERATION_DEFINITION_LIST`; telemetry
rows derive from the native gameplay-capability authority; continuity,
representation, outcome, and recovery descriptors live beside their owning
modules. The proof ledger contributes status and references only; incident scope
and limits remain in the referenced artifacts, while generated limitations
describe projection boundaries such as missing producer support. It supplies
no semantic operation rows. The public read-only command is:
`./dev capability-manifest --generation-id <id> --output <path>`.

Generation identity is computed from canonical underlying authorities while
excluding the final capability digest and per-entry introduced generation. The
generation ID is injected into the exact manifest bytes and its digest is then
linked into `GenerationManifest`, avoiding a fixed-point cycle.

## Typed semantic evidence

Each exported capability records:

- its deterministic semantic name and owning component;
- its category: sensing, representation, memory, action, verification, or recovery;
- one owner-derived semantic effect used by downstream diagnosis;
- its typed evidence state and optional proof class; and
- retained evidence references and known limitations.

Unproven, withheld, unknown, and unsupported capabilities carry no proof class;
source/unit proof maps to proven portable and live proof maps to proven live.

## Semantic-versus-evidence separation

The public manifest keeps semantic rows free of scenario identifiers, target
names, expected answers, and incident-specific mechanics. Detailed evidence
remains public and reconstructable in the referenced repository artifacts.

Operation definitions and native capability declarations remain the only
semantic authorities; aliases do not create duplicate capability rows.
Semantic effects are derived from those operation kinds, native names, and
owner descriptors rather than from a hand-maintained sibling registry.

The retained manifest has 69 rows: 26 action, 27 sensing, 13 representation,
and one each memory, verification, and recovery. Evidence states are 23 proven,
45 unproven, and one unsupported. The unsupported row is
`action.respond_to_immediate_threat`: its operation requires the absent
`nearby.visible_entities` producer capability while native authority advertises
`nearby.characters` and `nearby.roles`. This is a retained authority boundary,
not a reason to change native behavior or protocol in G13.

## Independent review and withheld claims

Focused diagnostics cover exact serialization, duplicate/malformed authority
rejection, stale schemas/docs, and generation linkage. The manifest is not live
evidence: dispatch, acceptance, completion, and world effect still require
later independent evidence.

The following remain explicitly withheld:

- an offered affordance is not a planner selection or an accepted operation;
- dispatch, acceptance, completion, and world effect still require their own
  later independent evidence;
- the candidate does not register a G15 plugin or change native behavior;
- the candidate does not perform the G14 production trajectory export; and
- no live process, save, DLL installation, or game state was changed.

## Completion boundary and next goal

G13 requires the full portable gate, generated-artifact freshness, and this
checkpoint to travel with the G13 candidate commit. Exact identity is
established by Git. Public push, hosted CI, and the parent EvoGen ratchet
remain required before G13 completes; G14 owns trajectory export.

## Verification

```bash
PYTEST_ADDOPTS='-p no:cacheprovider' UV_CACHE_DIR=/tmp/kae-uv-cache \
  uv run --frozen --no-sync pytest -q tests/test_generation_manifest.py \
  tests/test_capability_manifest.py tests/test_capability_consistency.py \
  tests/test_docs_hygiene.py
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev capability-manifest \
  --generation-id 0000000000000000000000000000000000000000000000000000000000000000 \
  --output /tmp/kae-capability-manifest.json
git diff --check
```

The repaired exact tree passed the full `./dev verify-portable` gate, including
generated-artifact freshness. Faraday, the independent authority reviewer, and
Averroes, the independent acceptance reviewer, each recorded a final PASS.
Public push, hosted CI, and the parent EvoGen ratchet remain required, so G13 is
not yet complete and final publication is not claimed here.
