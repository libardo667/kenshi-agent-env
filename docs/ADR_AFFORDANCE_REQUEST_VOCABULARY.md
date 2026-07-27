# ADR: affordance demand has a typed cross-run identity

Status: accepted 2026-07-27; supersedes the capability-naming and duplicate
identity portions of `ADR_RUNTIME_AFFORDANCE_REQUESTS`.

## Context

`request_affordance` originally named a missing capability with free prose.
Case-folding and whitespace normalization suppressed literal repeats inside one
run, but “operate the mine” and “work this ore node” remained unrelated. Logs
therefore could not count recurring demand across saves without guessing at
meaning.

A universal vocabulary would be equally dishonest. Kenshi has game-specific
nouns and mechanics, and one game has not established a universal action
ontology.

## Decision

Every request carries:

- one game-neutral intent class: `observe`, `move`, `interact`, `communicate`,
  or `manage`;
- the literal game namespace `kenshi`;
- a strict lower-snake-case game-specific capability slug;
- a separate human-readable description;
- its blocked goal, rationale, current evidence, workaround, and urgency.

The stable identity is `game:intent_class:capability_slug`. Runtime duplicate
suppression and offline aggregation use only that key. Prose never decides
identity, and matching prose cannot merge different slugs.

`kenshi-agent aggregate-affordances` reads one or more session logs, counts each
typed key across distinct run IDs, retains bounded grounded examples, and ranks
survival-critical demand before cross-run recurrence. Legacy or malformed
free-text events are reported as unclassified rather than guessed into a key.
Supplying no logs is an error, not a zero-demand report.

Every aggregate remains `needs_engineering_review`. Ranking selects candidates
for investigation; it never grants planner authority or promotes a capability
into the action catalog.

## Consequences

Multi-save runs can produce a countable implementation backlog while preserving
the evidence needed to judge each request. A model must reuse an existing slug
for the same intention even when it describes the gap differently.

Adding an intent class is a substrate schema decision. Adding a Kenshi slug is a
game-adapter vocabulary decision. Neither implies that another game shares the
same mechanic.

Old logs remain measurable as unclassified demand but cannot be merged
semantically without an explicit later migration or review.
