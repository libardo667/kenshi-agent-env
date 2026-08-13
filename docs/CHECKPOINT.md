# Checkpoint: Exact planner affordance-set evidence

Goal 12 records one typed `affordance_set` event for every planner context. The
event is the exact semantic choice authority delivered to that planner call,
not a reconstruction from prompt text and not evidence that any choice was
selected, dispatched, completed, or changed Kenshi.

This slice changes planner evidence and replay only. It does not launch Kenshi,
dispatch input, modify a save, install a DLL, create an EvoGen capability
manifest, or export a production trajectory.

## Repository and authority

```text
parent commit          bfaa4d55ae10a34d33e7a06ee3959fc6659eceb4
integration branch     main
starting tree          clean
EvoGen counterpart     c37147b3120c38c9a979ca8671fcc11c5ab62c6c
source plan revision   2026-08-10T21:25:08.835Z
affordance schema      1.0
producer protocol      2.0.0
```

The parent commit is the completed G11 generation-manifest slice. G12 changes
only the Python planner/evidence plane and its generated schemas, documentation,
fixtures, and tests. Native code, controller protocol, telemetry schema,
environment behavior, operation definitions, and evaluation rules are
unchanged.

## One enumeration and one delivery boundary

`enumerate_affordance_set` is now the single immutable enumeration used to
build the planner projection, delivery evidence, and read-only affordance
watch. Hosted planners enumerate once before budgeting and reuse that snapshot.
Scripted and direct-action in-process planners record that no semantic menu was
delivered, rather than converting available runtime operations into false
planner-delivery evidence. Budgeted planner input is rejected unless its
complete `affordances` array equals the projection from the same enumeration.

`planner_context_prepared` remains delivery accounting: context identity,
revision, included continuity, payload size, and budget information. The new
`affordance_set` record is written after that accounting and immediately before
`decide_prepared`. A planner failure therefore preserves the exact delivered
choice set. A failed affordance-set log write prevents delivery rather than
creating an unrecorded planner call.

## Typed semantic evidence

Each offer records:

- its opaque affordance identity;
- operation kind and issuing source adapter;
- semantic name and typed parameter constraints;
- the opaque target disambiguator the planner must copy, when required;
- applicable engine-owned target identities and semantic roles; and
- the authored world revision and identity session for the containing set.

The set separately records every adapter's completeness status and typed
withholding categories. Missing telemetry, stale telemetry, a truncated source,
an unknown source, unprobed targets, invalid semantic values, authoring or
binding refusal, interface scope, and intentional non-delivery remain distinct.
An empty complete source therefore does not mean the same thing as an absent or
incomplete source.

## Replay and confidentiality boundary

`load_affordance_sets` validates committed JSONL events as the typed schema and
rejects logs with no such event; older runs remain readable elsewhere, but they
cannot claim exact choice reconstruction. `reconstruct_choice` resolves a
planner selection only from semantic identity, the emitted target
disambiguator, and typed parameter constraints. It does not parse prompts,
descriptions, or labels.

The event excludes presentation labels, descriptions, operation arguments,
keys, screen coordinates, inventory section/slot coordinates, and lists of
unoffered native commands. Inventory transfers now use an opaque planner target
while private runtime operation arguments retain the exact section and slot
needed for binding. The event records source and destination inventory owners
as semantic participants without publishing the private mechanical address.

## Independent review and withheld claims

Cicero mapped the current planner and enumeration authorities, Kuhn designed
failure-oriented falsifiers, and Hypatia audited semantic/mechanical and
runtime/evolution boundaries before implementation. Their final read-only
review found contradictory typed parameter/completeness contracts and unknown
adapter/operation pairs were too easy to load. The repaired models now reject
those cases, event models are frozen, and replay can cross-check retained offer,
operation, and adapter identities. The final focused suites were rerun after
those repairs.

Cicero also confirmed that the generated reporting-surface report still shows
no affordance-set event in its real pre-G12 live fixture. That gap is preserved
intentionally: the new synthetic fixture proves typed replay, but it is not
inserted into an older live capture or presented as live evidence.

The following remain explicitly withheld:

- an offered affordance is not a planner selection or an accepted operation;
- dispatch, acceptance, completion, and world effect still require their own
  later independent evidence;
- source completeness describes the adapter's observed denominator, not every
  possible action in Kenshi;
- old logs without `affordance_set` events cannot be upgraded by inference;
- G12 does not create the G13 KAE subject adapter or permanent EvoGen
  capability-manifest authority;
- G12 does not perform the G14 production trajectory export; and
- no live process, save, DLL installation, or game state was changed.

## Completion boundary and next goal

G12 stops after independent review, generated-artifact freshness, the complete
portable gate, a clean commit on `main`, public push, hosted Python matrix, and
the central EvoGen plan/checkpoint/cockpit ratchet. Only then may G13 begin.

G13 owns the real KAE subject adapter and permanent content-addressed
capability-manifest authority. G14 still owns trajectory export. G28 begins
supervised live observation, and G29 remains the first end-to-end live candidate
evolution proof.

## Verification

```bash
PYTEST_ADDOPTS='-p no:cacheprovider' UV_CACHE_DIR=/tmp/kae-uv-cache \
  uv run --frozen --no-sync pytest -q tests/test_affordance_set_event.py \
  tests/test_item_transfer_offers.py tests/test_affordance_watch.py \
  tests/test_continuity.py tests/test_planner_base_mutation.py \
  tests/test_reporting_surface.py tests/test_session_event_dispositions.py \
  tests/test_checkpoint_freshness.py
RUFF_CACHE_DIR=/tmp/kae-ruff-g12 UV_CACHE_DIR=/tmp/kae-uv-cache \
  uv run --frozen --no-sync ruff check .
UV_CACHE_DIR=/tmp/kae-uv-cache MYPY_CACHE_DIR=/tmp/kae-mypy-g12 \
  uv run --frozen --no-sync mypy src
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
git diff --check
```

The focused tests, Ruff, strict mypy over 153 source files, checkpoint
freshness, generated-artifact checks, the full pytest suite, and the complete
portable gate pass on the independently reviewed candidate. Hosted CI remains
the post-push completion authority.
