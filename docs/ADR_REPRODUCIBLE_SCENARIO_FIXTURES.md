# ADR: scenario evidence is fixture-attested

Status: accepted 2026-07-27; supersedes the unverified recurrence rule in
`ADR_SCENARIO_EVIDENCE`.

## Context

Command-line scenario labels could claim any save and situation. Aggregation
therefore treated operator prose as cross-scenario evidence even when the
launcher had loaded a different save. Repeating one incidental autosave under
new labels could manufacture both scenario and save diversity.

Kenshi's Forgotten Construction Set has first-class Game Start records for
money, squad templates, location, relations, research, equipment, health, and
skills. Some useful situations still require a dynamic save: indoors, in active
combat, or at a particular time and position.

## Decision

Custom Game Starts are the authored source of repeatable situations. A complete
closed save may then be captured as a fixture. Capture copies rather than edits
the source, hashes every relative path and byte, and rejects duplicate
`scenario_id`, reused `save_id`, or the same bytes under another save identity.

Restore writes only the reserved `KenshiAgentScenario` slot. An existing slot
without project ownership fails closed. Replacing a previously managed slot
moves its current state into the recovery store first.

Scenario launch uses the current semantic **Load Game** control and then the
exact reserved save row. It never uses auto-Continue. After a fresh loaded
paused observation, the launcher verifies:

- `indoor`/`outdoor`: selected character's resolved building membership;
- `hostile`/`safe`: selected character is/is not in active combat;
- `broke`: at most 1,000 cats; `funded`: at least 10,000 cats;
- `solo`: exactly one squad member; `squad`: at least two;
- `day`: 06:00–19:59; `night`: 20:00–05:59.

The intentional economy gap is neither category. A missing capability, value,
selection, native session, or freshness proof rejects attestation.

The attestation binds the scenario and fixture digest to the native identity
session, telemetry revision, and observed predicates. Journey revalidates it
against current fresh paused telemetry. Offline aggregation counts only valid
fixture-attested run starts. Manual labels remain visible as unverified runs and
contribute no recurrence; digest/save relabeling across logs invalidates every
conflicting attestation.

## Consequences

The fixture store is host-local generated evidence, not Git history. Game Start
mods may be versioned separately, while dynamic saves remain hashed artifacts.
No fixture is captured, restored, or loaded implicitly.

This proves initial observable conditions, not that a situation remains safe or
unchanged after play begins. Runtime safety and causal action proof still own
that boundary.
