# Action continuity ledger

Each strategic planner call is a separate API request. A continuous call may
authorize several steps, but the next call still needs to know what earlier
actions changed. A screenshot and current telemetry alone do not say what the
agent tried or whether it helped; that made repeated camera recovery look
reasonable on every fresh request.

The runtime now carries a bounded `recent_action_outcomes` ledger into every
observation. Each entry records:

- the planner's intent and exact validated action;
- whether the executor performed it and the receipt message;
- the selected character's before/after world position;
- meaningful game, UI, inventory, visibility, and movement telemetry deltas;
- a downsampled frame-difference fraction that ignores small pixel shimmer;
- an assessment of `changed`, `no_op`, `not_executed`, or `unknown`; and
- explicit feedback telling the next planner not to treat a no-op as progress.

Outcome assessment is skill-aware where evidence permits it. Fine and map
movement require a measurable Lekko position delta. Person interaction requires
either a position delta during the approach or an opened dialogue/trade screen;
an NPC moving through the frame does not make a failed click count as progress.

The long-form live profile retains the latest 16 outcomes. This is working memory:
it is reset at the beginning of a journey and logged to JSONL. Cross-plan and
cross-run continuity is now a separate SQLite path: plans and patches may write
facts, episodes, and commitments; an accepted plan objective is recorded when
the planner did not leave its own commitment; and later observations recall
salient entries.

## What this buys us

The ledger provides short-horizon causal continuity. A later decision can see,
for example, that F materially changed the first frame, a second F changed
nothing, and Lekko's coordinates never moved. That supports choosing an orbit,
pan, or grounded movement action instead of repeating F.

Movement entries retain both coordinates and a calculated world-distance delta,
so the agent can compare intended direction with actual progress. UI, money,
and named-inventory transitions make later interaction and purchase actions
auditable.

## What it does not buy us

This is not a full spatial map or a proof that an action achieved its semantic
purpose. A frame can change without helping, and two visually similar views can
represent different orientations. Longer-horizon agency still needs stronger
structure around three layers:

1. Richer intention state beyond current objective/commitment memories:
   subgoal, hypothesis, attempted method, and reason for abandoning it.
2. A spatial trail: timestamped character coordinates, nearby named entities,
   camera orientation when telemetry can expose it, and recognizable landmarks.
3. Broader outcome checks tied to actions. Exact dialogue, arrival, money
   changes, management/UI state, combat state, and camera position cover
   important cases; many domain operations still lack a decisive effect.

Those layers should remain grounded in observations rather than model-authored
narrative. The present action ledger is the smallest useful base: it makes local
failure and movement history visible without pretending the agent understands
more of Kenshi than telemetry and frames actually establish.
