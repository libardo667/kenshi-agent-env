# ADR: Entity-scoped memory recall

## Status

Accepted.

## Problem

Persistent recall selected one salience-ordered top-N list. A planner could
correctly record that a particular character had no useful dialogue branch, then
lose that constraint after enough later writes. Display names cannot repair the
loss: Kenshi contains duplicate names, and native identities have bounded
lifetimes.

## Decision

`MemoryWrite.target_id` optionally binds a fact to one opaque entity ID copied
exactly from the current observation. Unbound memories remain in the general
salience-ranked recall. Bound memories never enter that general list.

For each fresh observation, runtime recall is the bounded union of:

1. up to `max_entity_recalled_memories` bound to exact IDs in the current squad,
   nearby entities, world targets, or current dialogue; and
2. up to `max_recalled_memories` unbound entries above the general salience floor.

Entity matches lead the list and ignore the general salience floor. Stale
telemetry supplies no target IDs. The planner payload budget preserves exact
current-target memories before optional general context, and the advisor keeps
the same ordering.

SQLite stores an empty target key for unbound rows and includes it in the unique
ownership key. Existing databases are rebuilt once; old rows migrate as unbound.
Two targets may therefore learn identical text without one write overwriting the
other.

## Identity boundary

Names, roles, positions, and fuzzy similarity never reactivate a bound memory.
Native IDs are session-scoped by design, so a process or game-session change
creates a different lifetime. Cross-session identity reconciliation, if ever
added, requires its own evidence-backed contract; this ADR does not authorize
name matching.

## Consequences

- Later general writes cannot evict a learned constraint for a currently
  observed exact entity.
- A memory for an absent, stale, or same-named different entity cannot leak into
  planning context.
- Entity recall is additional bounded context, so disabling all recall requires
  setting both recall limits to zero or disabling memory.
- Recall changes strategy context only. It grants no action capability and
  bypasses no current-state binding or safety check.
- Log evaluation reports dialogue-approach attempts by target, their repeated
  count, and the maximum for one target so paired runs can measure the intended
  behavioral effect.

## Evidence

Portable invariants cover overflow at zero salience, exact-ID matching,
same-name and stale non-recall, legacy migration, per-target uniqueness, planner
payload conservation, and approach-count metrics. No hosted-model or live Kenshi
improvement is claimed by this decision alone. A fixed-policy portable ablation
changes repeated approaches from two to zero when only scoped recall is enabled.
