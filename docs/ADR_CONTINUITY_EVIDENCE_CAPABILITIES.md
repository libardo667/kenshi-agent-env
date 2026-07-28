# ADR: Continuity validates evidence capability before rendering

## Status

Accepted. Supersedes the evidence-admissibility and evicted-outcome portions of
[ADR_CONTINUITY_AUTHORITY](ADR_CONTINUITY_AUTHORITY.md); its authority, identity,
and commit-timing decisions remain in force.

## Context

Resolving an evidence ID proved only that the ID existed and reached the planner.
It did not preserve what that source was capable of establishing. Consequently,
advice, remembered belief, plan completion, and no-op or unknown attempts could
ground a world fact or close a delivery commitment. When an outcome left the
visible window, its assessment collapsed to the word `evicted`. Canonical
history stored only the prose rendering of its references.

## Decision

Every reference resolves first to an immutable `ResolvedEvidenceSnapshot`.
Snapshots name their source, runtime authority, authored context, run, revision,
and the bounded source-specific fields needed to interpret them. Validation uses
the typed authority; prose grounding is rendered only after acceptance.

- Facts require fresh observation, a controller-verified world effect, or a
  causally observed change.
- Episodes require an observation, action attempt, or plan lifecycle outcome.
  A failed, no-op, not-executed, or unknown attempt remains admissible only as
  that exact episode.
- Commitments and hypotheses may be opened without external evidence.
- Resolution always requires references. Only commitments and hypotheses are
  resolvable; facts and episodes are superseded or retracted.
- A commitment cannot close on advice, belief, plan disposition, no-op,
  not-executed, or unknown evidence. Hypotheses preserve whether they were
  confirmed, rejected, or left unknown.

The ledger keeps rich recent outcomes under the existing context bounds and a
compact digest for every action and plan outcome for the run lifetime.
`recall_memory` can explicitly search `working_outcomes`; only returned digests
re-enter the next planner manifest and become citable.

Accepted lifecycle events store the exact operation, origin, plan and step,
authored and commit revisions, references, resolved snapshots, and rendered
grounding as versioned canonical provenance. Schema 3 projects the latest
provenance and resolution disposition while append-only history retains every
older transition. Schema-2 databases are backed up before migration; their old
unstructured events remain explicitly unstructured rather than acquiring
invented evidence.

## Consequences

Existence is no longer authority. An outcome may scroll out of automatic
context without losing its assessment, and may return only through an explicit
bounded read. Operator inspection and projection rebuild expose the same
structured sources and human-readable grounding. The store remains the single
canonical memory authority; digests are run-scoped working continuity, not a
second durable memory.
