# ADR: Lossless memory compaction

## Decision

Canonical memory compaction starts with a deterministic lossless treatment.
An operator selects two to eight exact active memories, inspects a strict
candidate, then separately applies that same candidate.

The candidate preserves every source sentence verbatim in stable memory-ID
order. Sources must share campaign, kind, target, and authorship. Commitments
and hypotheses are excluded. A fingerprint covers durable semantic and
lifecycle state but not delivery bookkeeping.

Application takes an immediate database write lock, re-reads and fingerprints
every source, and recomputes the entire candidate. One transaction supersedes
all exact sources and inserts the replacement with the inspected candidate,
generator metadata, source IDs, and fingerprints in canonical provenance.
Any drift, tampering, conflict, or late write failure changes nothing. Source
events remain append-only.

Deterministic recall is the only implemented retrieval treatment and is logged
on every run. Semantic rewriting and semantic MMR cannot be configured yet.
Their reserved prompt is explicitly inactive until a real bounded provider,
disposable cache, outage fallback, and comparative evaluation exist.

## Consequences

- Compaction can reduce active-record count without interpreting failed or
  uncertain attempts as success.
- Looking at a candidate is read-only; applying one is explicit and auditable.
- A future semantic treatment must meet the same conservation and atomicity
  boundary rather than gaining direct store authority.
