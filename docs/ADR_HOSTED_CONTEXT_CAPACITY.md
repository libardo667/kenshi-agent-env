# ADR: Hosted model capacity owns planner context limits

## Decision

A hosted planner derives its request envelope from the exact selected model's
token capacity. OpenRouter capacity comes from its model metadata endpoint and
is resolved once when the planner is built. Providers without capacity metadata
may use an explicit token override.

Each call reserves its actual output allowance, rendered system text, response
schema, request wrapper, and an image allowance when a screenshot is present.
The per-call observation projection begins semantic compaction while one
additional output allowance still remains. Text is measured pessimistically as
one token per UTF-8 byte because the provider's native tokenizer is unavailable
before inference.

The compaction target is not rejection authority. An irreducible projection may
expand past it while it fits the hard observation envelope. Only the hard
envelope may reject it. Current authority, exact controls, active plan state,
explicit memory reads, open commitments, current-target memories, and the
latest adverse evidence remain decision-critical.

Context projection never writes canonical memory. It drops only material from
one planner call and records the exact omissions and capacity derivation in that
call's manifest. Lossless operator compaction remains the only operation that
can supersede durable memory records.

If provider metadata is unavailable, the planner invents no replacement limit.
It sends the full current projection and leaves any context rejection to the
provider, while the manifest records that capacity metadata was unavailable.

## Consequences

- Character-count spending preferences cannot terminate continuous play.
- A model change automatically changes the planner envelope without editing a
  profile constant.
- Provider capacity, reservations, estimator, target, and hard envelope are
  attributable in run evidence.
- Token estimation remains deliberately conservative; native provider usage
  returned after a call remains the authoritative measurement.
