# ADR: Authoritative continuous world-state stream

Status: accepted

Date: 2026-07-23

## Context

The first continuous executor could carry out a bounded plan, but it polled the
environment itself while waiting for postconditions. A future safety
supervisor, overlay, planner snapshotter, and option monitor would therefore
race or duplicate transport reads. The latest telemetry snapshot also could not
retain transient events, express bounded deltas, own pending command causality,
or preserve observed entity lifetimes through ordinal reordering.

## Decision

Feature-flagged continuous mode owns one `ObservationPump` and one bounded
`WorldStateStore` per run.

- The pump uses telemetry-only observation by default and captures a new visual
  frame only on an explicit request.
- The store validates world revisions, rejects regressions and conflicts,
  detects duplicate telemetry sequences, and carries forward the last validated
  visual revision.
- Snapshot history, semantic deltas, transient events, command history, and
  subscriber queues all have configured hard bounds.
- Consumers subscribe to isolated update copies or use a causal
  `wait_for(..., after_revision=R)` API.
- The executor records active plan/step state and deterministic command IDs in
  the store. Receipts bind each command to its start revision and canonical
  completion revision.
- Planner context may decorate only the current canonical revision; it cannot
  manufacture a world-state advance.
- Nearby ordinal source IDs are normalized into process-local lifetime IDs
  using observed fingerprint and position evidence. Ambiguity is recorded, and
  capability loss does not become a false disappearance.

The existing native plugin transport remains an atomically replaced latest
snapshot. This decision creates an authoritative event stream inside the
Python runtime; it does not claim that the plugin emits native events.

## Consequences

- Executor waits and future safety consumers share one ordered observation
  source and cannot accidentally confirm an action from its starting revision.
- Transient events and entity disappearance remain inspectable after the latest
  snapshot changes.
- Slow consumers lose bounded queued updates visibly through metrics rather
  than growing memory without limit.
- The portable identity registry alone cannot prove Kenshi object identity.
  Later P5 contracts added stable native handle generations and keyed
  bridge-level acknowledgements while retaining this portable fallback.
- `single_step` remains unchanged, and continuous live execution remains
  blocked until independent supervision and cancellable option cleanup exist.
