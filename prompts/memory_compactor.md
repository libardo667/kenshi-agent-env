# Reserved semantic memory compactor

Status: inactive. The current `compact-memory` operator path is deterministic
and lossless; it does not load this prompt or call a model.

A future semantic provider may propose a strict candidate but never mutate
memory directly. It must name every exact source ID, preserve causal
uncertainty and the weakest relevant confidence, retain important identities,
locations, consequences, failures, and unresolved questions, and refuse mixed
targets, kinds, or epistemic states. It must never turn an attempt, hypothesis,
or inconclusive result into success. Malformed, truncated, or refused output
changes nothing, and application must revalidate all sources atomically.
