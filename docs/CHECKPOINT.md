# Checkpoint: Exact runnable-system generation manifest

Goal 11 adds a read-only `./dev generation-manifest` command that records the
stable identity and provenance of the exact runnable KAE system. The manifest
uses EvoGen's serialized `GenerationManifest` envelope while keeping all
Kenshi-specific collection logic in KAE.

This is a tooling, schema, and evidence checkpoint. It does not launch Kenshi,
dispatch a command, mutate a save, install a DLL, or claim that a running world
changed.

## Repository and authority

```text
parent commit          7e25459c992572b0f102297420f7117fbc2146d7
integration branch     main
starting tree          clean
EvoGen counterpart     303701c078ead81f1886933d10faa5d0891fac8d
source plan revision   2026-08-10T21:25:08.835Z
manifest schema        1
producer protocol      2.0.0
memory schema          4
```

The parent commit is the completed G10 session-event-sequence slice. G11 adds
generation provenance only. It does not alter the runtime plane, planner
behavior, evaluator, native controller, telemetry semantics, or proof rules.

## Stable generation identity

The command builds a strict typed manifest, serializes canonical sorted compact
JSON, and hashes every top-level field except `generation_id`. Two materially
identical invocations therefore write byte-identical manifests with the same
identity. The recorded timestamp comes from the source commit rather than the
wall clock.

Source identity includes the full Git commit and a content digest for tracked
changes and untracked files. The requested output is excluded lexically, so
writing the manifest inside the checkout does not make its own identity drift.
The writer rejects symlink outputs, symlinked parents, and existing non-files;
publication uses a flushed, fsynced, same-directory temporary file followed by
an atomic replace.

Focused falsifiers prove that each required semantic change produces a new
identity: planner or advisor model, raw prompt or strategy corpus, operation
definition, protocol schema, and installed DLL bytes. Reordered schema keys do
not change identity. Missing and unreadable artifacts remain distinct typed
states rather than being collapsed into empty content.

## Recorded provenance

The manifest records:

- Git commit, dirty state, and a redacted content fingerprint;
- the raw `uv.lock` digest;
- the active planner and advisor route identifiers, including disabled advisor
  state, plus a required script digest for the scripted planner;
- raw system-prompt and strategy-corpus evidence and the rendered output-policy
  prompt digest;
- a redacted effective-config digest;
- typed protocol, schema, evidence-semantics, memory, native-source, and
  manifest versions plus canonical exported-schema digests;
- both the generated operation-definition document digest and a semantic
  projection of the live operation registry;
- the canonical proof-ledger digest;
- independently typed scenario-fixture and authored-start identities;
- target Kenshi executable/version evidence and optional observed executable
  evidence; and
- independent source, header, built, staged, and installed native-binary
  evidence with parity conclusions only when both compared artifacts exist.

The generated JSON schema at `schemas/generation_manifest.schema.json` and the
generated development-command reference are maintained by the existing schema
and documentation exporters and participate in their freshness gates.

## Redaction and fail-closed boundaries

Configuration references are inspected before interpolation. Only the exact
reviewed KAE environment-variable allowlist may affect the effective-config
digest; credential-like and unknown references fail closed, including nested
default interpolation. Paths become role markers, arbitrary strings are
hashed, and mutable telemetry, memory, runtime scenario, and attestation
payloads are excluded from effective configuration because they have their own
typed authorities.

Model identifiers are a deliberately narrow reviewed projection. Path-like,
credential-like, malformed, or overlong values fail closed. The command does
not load `.env`, enumerate the process environment, serialize API keys, or let
unrelated variables such as `HOME` affect the identity. Tests exercise both
identity invariance and absence of secret values, credential names, and host
paths in serialized output.

Scenario and authored-start evidence are separate lineage channels and may
coexist. Scenario evidence distinguishes a manually declared identifier, a
configured attestation, and a verified CLI fixture; volatile session,
sequence, observation-time, and runtime telemetry fields do not enter stable
identity. Authored-start evidence separately hashes the selected typed start,
canonical bundle manifest, and mod payload.

## Native and game-version evidence

File hashes establish only byte identity. The manifest preserves built, staged,
and installed DLL evidence independently, including absent and unreadable
states, rather than choosing one file as authoritative or asserting parity
without a comparison. Kenshi evidence likewise separates the typed research
target from an optionally observed executable and reports a match only when
both are available.

The capability digest currently names KAE's generated native
`GameplayCapabilities.json` authority. Metadata marks this projection as
provisional until G13. Although the complete output validates against EvoGen's
serialized `GenerationManifest` model, G11 does **not** claim an EvoGen
`CapabilityManifest` CAS object or subject-conformance/bootstrap readiness.

## Independent review and withheld claims

Shannon mapped every source authority, Dwork audited configuration and secret
boundaries, and Merkle designed the identity falsifiers before implementation.
Hopper expanded the executable falsifier suite. Saltzer's security review found
and then verified repairs for model-ID leakage, symlink/path handling,
external-target mutation, and absent-versus-unreadable evidence. Knuth's
contract review verified all required authority fields, mutation behavior,
generated-artifact freshness, local CLI behavior, and exact parsing through
EvoGen's current `GenerationManifest` model.

The following remain explicitly withheld:

- a digest does not prove runtime loading, command acceptance, completion, a
  world effect, goal progress, or goal achievement;
- a matching executable or DLL hash does not identify a running process;
- G11 does not create the authoritative planner-visible affordance-set event;
- G11 does not complete the G13 subject adapter or its capability-manifest CAS;
- G11 does not export production trajectories to EvoGen; and
- no live process, save, DLL installation, or game state was changed to prove
  this slice.

## Completion boundary and next goal

G11 stops after the command and schema are independently reviewed, the complete
portable gate passes, this checkpoint is committed on `main`, the public push
is clean, and hosted CI is green. Only then may the central EvoGen plan ratchet
mark G11 complete and name G12 as the sole next goal.

G12 owns one authoritative planner-visible affordance-set event. G13 still owns
the KAE subject adapter and permanent capability-manifest authority, and G14
owns the production trajectory exporter.

## Verification

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache uv run --frozen --no-sync pytest \
  -p no:cacheprovider -q tests/test_generation_manifest.py \
  tests/test_docs_hygiene.py tests/test_dev_entrypoint.py \
  tests/test_live_dev.py tests/test_native_provenance.py \
  tests/test_checkpoint_freshness.py
RUFF_CACHE_DIR=/tmp/kae-ruff-g11 UV_CACHE_DIR=/tmp/kae-uv-cache \
  uv run --frozen --no-sync ruff check .
UV_CACHE_DIR=/tmp/kae-uv-cache MYPY_CACHE_DIR=/tmp/kae-mypy-g11 \
  uv run --frozen --no-sync mypy src
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
git diff --check
```

The focused manifest, redaction, mutation, CLI, native-provenance,
generated-artifact, and checkpoint tests pass on the independently reviewed
candidate. The complete portable gate also passes: locked environment, Ruff,
strict mypy over 152 source files, research validation, disposition/schema/doc
regeneration, the full pytest suite, architecture checks, and whitespace
checks. Hosted CI remains the post-push completion authority.
