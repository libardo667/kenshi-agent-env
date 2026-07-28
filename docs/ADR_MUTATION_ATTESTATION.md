# ADR: mutation coverage is a derived, committed claim

## Status

Accepted 2026-07-28.

## Context

`runs/*` is gitignored, so every mutation campaign this repository has ever run lived only
on the machine that ran it. The committed claim about that work was prose — `STATUS.md`
asserted "Fifty-six mutation shards remain unattested" while the artifacts on disk said
fifty. A reader of the repository had an assertion and no evidence.

The artifacts could not have settled it anyway. Each recorded `batch`, `counts`, `total`,
and `actionable_mutants`, but nothing about *which code* it examined. A 429/429 result and
the same result taken four commits later were byte-comparable and indistinguishable, so
"is this module still covered?" had no answer short of re-running the campaign.

Two consequences showed up together. One slice added 275 lines to `runtime.py` and 100 to
`continuous_executor.py` and attended neither shard, because the rule to do so was prose in
a loop prompt and nothing checked it. Separately, a batch of `kenshi-mutate results` calls
against invalidated caches wrote eleven `total: 0` artifacts that became the most recent
record for eleven shards with real campaigns underneath — correct behavior from a tool that
fails closed on zero, but indistinguishable in the directory from having no evidence.

## Decision

Every campaign artifact records `source_sha256`, the digest of the exact file it attests.
`docs/generated/MUTATION_ATTESTATION.md` is generated from those artifacts and committed.

The ledger stores counts and the digest recorded at campaign time. It does **not** store
each shard's state. State is recomputed on every write by digesting the module again and
comparing, which is what makes the record self-invalidating: a module edited after its
campaign reads `source-changed` whether or not anyone remembered to look.

Four states, and only one is a pass: `attested`, `source-changed`, `unverified` (the
campaign predates digests and cannot name its tree), `never`.

`tests/test_docs_hygiene.py` regenerates the ledger from **committed inputs only** — the
checked-in file and the sources it digests — and fails when the result differs. Local run
artifacts are folded in by `scripts/export_mutation_ledger.py`, not by the gate, so the
check holds on a clone with no `runs/` directory.

A zero-mutant artifact is not ranked as evidence. `mutation_exit_code` already fails closed
on it; letting it displace a real campaign as a shard's newest result would let a cache
invalidation erase attested work from the committed record.

## Consequences

`runs/` stays ignored. The repository gains a coverage claim it can check rather than one
it asserts, and editing a mutated module now breaks the build until either the ledger is
regenerated — recording the honest `source-changed` — or the shard is re-run.

Every existing campaign reads `unverified`: they predate digests, and their numbers are
preserved without claiming a tree they cannot name. That state clears one shard at a time,
as each is re-run. It is deliberately not silent about being unfinished.

`attested` promises only that the module under test has not moved. A mutation result also
depends on the tests, and a changed suite is not tracked here — the ledger says the code is
unchanged, not that the numbers would reproduce.
