# Checkpoint: G15 optional EvoGen subject adapter candidate

This checkpoint records the KAE-owned G15 candidate only. It registers KAE as
an optional EvoGen subject and supplies a bounded synthetic conformance
surface. It does not extend KAE runtime control, replay, native transport, or
live-world evidence.

## Repository and authority

```text
parent commit             548658cbcef35037252e63be40248fa6a94b5ec1
integration branch        main
candidate state           root-accepted locally; publication pending
EvoGen API source         public commit 5e72ca364f0a1b2c5b23d41c9af5a2a15099b946
current public ratchet    ee89104
entry-point group         evogen.subjects
entry-point name          kenshi
Python compatibility      KAE 3.11-3.14; EvoGen extra marker <3.14
next goal                 G16, locked pending separate authorization
```

The generated KAE capability manifest remains the sole capability authority.
The optional package projects that authority and KAE's G11 generation-manifest
authority into typed EvoGen models and content-addressed artifacts. The
optional dependency is not imported by ordinary KAE modules. G14 exporter
evidence remains preserved by repository history; this G15 checkpoint does not
restate or reclassify the earlier G14 body.

## Candidate contract

The KAE-owned `kenshi` subject exposes API 1.1 bootstrap, runner, investigator,
builder, reviewer, evaluator, materializer, doctor, and conformance factories.
There is no probe-role factory.

The runner is deterministic, synthetic, read-only, and conformance-only. It
accepts only four opaque G15 scenarios, emits typed ordered events, writes
isolated JSONL traces, and distinguishes an accepted execution receipt from a
later independent outcome observation. It never launches or controls Kenshi,
instantiates live or replay environments, dispatches input, accesses saves or
DLLs, or ingests G16 run bundles.

Builder, reviewer, evaluator, and materializer are separate KAE authorities.
Candidate work is isolated, digest-checked, forbidden-literal checked, and
evaluated symmetrically across revealing, variant, regression, and long-horizon
cases before typed content-addressed materialization. Candidate improvement is
bound to recomputed SHA-256 of the generated source bytes, matching both the
candidate plugin artifact and the expected generated implementation. Tampered
or unrelated bytes remain blocked. A retained child adds only the
synthetic-only `kae_synthetic_observation` capability, with experiment evidence
and implementation references bound to exact content digests.

The wheel ships the existing generated capability manifest as a read-only
authority file. The doctor checks that local or wheel authority, validates its
typed digest and CAS linkage, and only then returns a complete-empty
diagnostic collection. Missing or corrupt authority raises a diagnostic
failure.

## Verification evidence

The following commands are the candidate verification sequence. They do not
launch or control Kenshi:

```bash
UV_CACHE_DIR=/tmp/kae-g15-full-uv-cache \
  uv sync --frozen --extra dev --extra evogen
UV_CACHE_DIR=/tmp/kae-g15-full-uv-cache \
  uv run --frozen --no-sync pytest -q \
  tests/test_evogen_subject.py tests/test_session_event_dispositions.py \
  --color=no
UV_CACHE_DIR=/tmp/kae-g15-full-uv-cache ./dev verify-portable
UV_CACHE_DIR=/tmp/kae-g15-full-uv-cache uv lock --check --offline
git diff --check
```

The portable gate installs both development and optional EvoGen extras. On
Python 3.11-3.13 this runs the G15 tests and host conformance checks. On Python
3.14 the honest `<3.14` marker leaves ordinary KAE development installed and
skips only tests guarded by the unavailable EvoGen extra.

The installed-wheel proof is:

```bash
UV_CACHE_DIR=/tmp/kae-g15-full-uv-cache \
  uv build --wheel --out-dir /tmp/kae-g15-wheel
UV_CACHE_DIR=/tmp/kae-g15-full-uv-cache \
  uv venv /tmp/kae-g15-wheel-venv --python 3.13
UV_CACHE_DIR=/tmp/kae-g15-full-uv-cache \
  uv pip install --python /tmp/kae-g15-wheel-venv/bin/python \
  /tmp/kae-g15-wheel/kenshi_agent_env-0.1.0-py3-none-any.whl
UV_CACHE_DIR=/tmp/kae-g15-full-uv-cache \
  uv pip install --python /tmp/kae-g15-wheel-venv/bin/python \
  'evogen @ git+https://github.com/libardo667/evogen.git@5e72ca364f0a1b2c5b23d41c9af5a2a15099b946'
(cd /tmp && /tmp/kae-g15-wheel-venv/bin/evogen subject doctor kenshi)
```

Before the exact EvoGen pin is added, the fresh wheel environment must import
ordinary KAE modules with EvoGen absent. After the pin is added, the doctor must
report `Status: pass`, all seven host boundaries as `pass`, and
`Diagnostics: 0` from the arbitrary `/tmp` working directory.

## Withheld claims and review state

This candidate does not claim live Kenshi control, replay consumption, native
or DLL compatibility, save mutation, observer behavior, G16 run-bundle
ingestion, or a live world effect. Synthetic outcome evidence is not live or
replay evidence, and dispatch or receipt is not proof of a world effect.

Noether authored the isolated candidate. Curie independently reproduced the
causal-digest falsifiers, event-inventory failures, installed-wheel discovery,
and retained synthetic evidence before returning PASS. Root byte-compared the
receiving checkout, reran the full portable gate, and reproduced the isolated
wheel doctor with launch-sensitive boundaries disabled. Commit, publication,
hosted CI, and any G16 authorization remain pending.
