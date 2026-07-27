# ADR: Bound every live dispatch by fresh authority

Status: accepted; extends `ADR_UNIFIED_INPUT_BOUNDARY_AUTHORITY.md`

## Context

A polite input lease can wait without a deadline. The unified boundary already
carries both schedulers across that wait and rejects a canonical stale flag, but
did not enforce the observation age itself. An identical revision is normal
while a roughly 2 Hz producer is sampled more frequently, so revision identity
alone says neither "fresh" nor "stalled".

The telemetry reader already owns a configured freshness ceiling and marks old
reads stale. Re-encoding another duration in the executor would let those two
answers drift.

## Decision

- Every ordinary planner-authored live dispatch in both schedulers carries an
  `ExecutionToken`. A scheduler without plan conditions still carries action,
  reference, control-mode, calibration, human-input, emergency-stop, revision,
  and freshness authority.
- `LiveEnvironment` supplies each token with the exact maximum age configured on
  its `TelemetryReader`.
- Inside the acquired lease, immediately before the first primitive, reject when
  no canonical observation exists, telemetry is marked stale, the configured
  ceiling or observed age is unknown, or the observed age exceeds that ceiling.
- Continue to reject revision regression, control-mode drift, withdrawn input
  ownership, failed action/reference revalidation, calibration drift, and any
  non-true plan condition.
- A rejection emits zero primitives and carries an attributable boundary report.
  An identical revision may pass only while its observation is still within the
  configured age ceiling.

## Consequences

The telemetry reader remains the single source of the freshness duration.
Changing the live profile changes read-time and lease-time policy together.
Missing age information cannot silently become fresh authority.

Mock and replay environments have no real input lease and do not revalidate a
token. Their default ceiling is therefore absent; any environment that does call
the real fence without supplying one fails closed.

Portable blocked-lease tests cover stale, unknown-age, over-age, regression, and
state-conflict rejection with zero primitives. Supervised conflict injection
inside a live lease remains deliberately unrun.
