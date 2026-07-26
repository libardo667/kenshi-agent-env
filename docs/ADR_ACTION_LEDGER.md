# ADR: bounded action-outcome ledger

## Context

Each strategic planner call is a separate API request. A screenshot and current
telemetry say what the world looks like, not what the agent tried or whether it
helped. Without that, repeating a useless action looked reasonable on every
fresh request — the agent recovered the camera over and over because nothing it
could see said the last attempt had changed nothing.

## Decision

The runtime carries a bounded `recent_action_outcomes` ledger into every
observation. Each entry records the planner's intent and exact validated action,
whether the executor performed it and the receipt message, the selected
character's before/after position, meaningful game/UI/inventory/visibility
deltas, a downsampled frame-difference fraction that ignores pixel shimmer, an
assessment of `changed`, `no_op`, `not_executed`, or `unknown`, and explicit
feedback telling the next planner not to read a no-op as progress.

Assessment is skill-aware where evidence permits. Fine and map movement require
a measurable position delta. Person interaction requires either a position delta
during the approach or an opened dialogue/trade screen — an NPC wandering
through frame does not make a failed click count as progress.

The long-form live profile retains the latest 16 outcomes. This is working
memory: reset per journey, logged to JSONL. Cross-plan and cross-run continuity
is a separate SQLite path for facts, episodes, and commitments.

## Consequences

The ledger buys short-horizon causal continuity: a later decision can see that
one keypress changed the frame, a second changed nothing, and the character's
coordinates never moved, and choose differently.

It is not a spatial map and not proof that an action achieved its semantic
purpose. A frame can change without helping, and two similar views can be
different orientations. Longer-horizon agency still needs richer intention state
(subgoal, hypothesis, attempted method, reason for abandoning), a timestamped
spatial trail, and decisive effect checks for more domain operations. Those
layers must stay grounded in observations rather than model-authored narrative.
This ledger is the smallest useful base: it makes local failure visible without
pretending the agent understands more of Kenshi than telemetry establishes.
