# Protocol 2.0 world model

Status: **current protocol; producer and all repository consumers cut over atomically**

Protocol: `2.0.0`

Typed authority: `src/kenshi_agent/core/protocol_2.py`

Generated schema: `schemas/protocol_2_world_model.schema.json`

## Decision

Protocol 2.0 is the current breaking wire contract. The player collection is
`roster`, never `squad`; platoons, active, primary, and selection are distinct;
ordinary orders, Jobs, permanent Jobs, and activity have separate owners; and
controller causality is exposed only through plural `controller_commands`.
The operational telemetry collection is `controller_commands.commands`. The
producer-independent full world model further partitions retained and recent
terminal records where bounded-history reasoning needs that distinction.

The root world-model shape is:

```text
protocol_version = "2.0.0"
sequence
identity_session_id
roster
platoons
active_platoon_id
primary_character_id
selected_character_ids
controller_commands
observed_unowned_kenshi_work
```

`PlayerCharacterState` carries `platoon_id` and optional `work`. Selection is
not repeated as a character flag. `PlatoonState` carries `member_ids`; listed
members must exist in the roster, one character cannot appear in two platoons,
and complete membership must agree with each character's `platoon_id`.

`CharacterWorkState` names four independent Kenshi channels:

- `ordinary_orders` for the player order queue;
- `jobs` plus the independent `jobs_enabled` switch;
- `permanent_jobs` for KenshiLib's `permajobs` container;
- `current_activity` for the task system's current goal.

No channel is inferred from another. A retained Job does not prove an ordinary
order, and a current activity does not prove either.

Task evidence carries nullable `task_value`, `task_name`, `subject_id`,
`description`, and `position`. Unknown subjects and positions remain null. In
the current 1.21 producer, KenshiLib's ordinary `ActionDeque` exposes first,
second, and last accessors but no size, so a multi-entry queue has unknown total
and its sampled tail has unknown numeric position. Enumerable Jobs and permanent
Jobs retain exact totals within the shared bound.

Each controller command retains its immutable dispatch primary, selected IDs,
active platoon, exact recipient IDs, identity session, ownership generation,
delivery evidence, recipient-level evidence, supersession links, monitor
disposition, and terminal sequence. Retained records are complete and cannot be
evicted by terminal-history truncation. They describe commands issued by this
controller, not all work in Kenshi.

World work without a causal controller link appears separately in
`observed_unowned_kenshi_work`, with its character, exact channel, task,
observation sequence, and why ownership is absent. It never gains a fabricated
command ID.

Every bounded collection has:

```text
items
completeness = complete | truncated
known_total = exact integer | null
```

For `complete`, `known_total` must equal `len(items)`. For `truncated`, an exact
total must be greater than the retained length; null means the source cannot
know the total. Absence from `items` proves absence only when completeness is
`complete`. A missing source is represented by the nullable containing field,
not by an empty collection.

All 2.0 objects reject extra fields. Protocol `1.x`, `squad`, `native_control`,
and `active_command_id` therefore fail 2.0 validation rather than entering an
alias or dual-read path. Independently, the 1.21 model also rejects the removed
`squad`, per-character `selected`, UI-owned selection, and `task_state` fields.

## Evidence boundary

### Source-proven

- KenshiLib 0.4.0 `PlayerInterface.h` separately declares `selectedCharacter`,
  `selectedCharacters`, `currentPlatoon`, `playerCharacters`,
  `getCurrentActivePlatoon()`, and `getAllPlayerCharacters()`.
- KenshiLib 0.4.0 `Platoon.h` declares `ActivePlatoon`, its character handles,
  name, size, and leader accessors; current native code already exercises
  active-platoon and character-platoon APIs.
- KenshiLib 0.4.0 `AITaskSystem.h` separately declares `orders`, `jobs`,
  `permajobs`, `isJobsEnabled()`, and `getCurrentGoal()`.
- Current native source emits 2.0 `roster`, platoons, active, primary,
  complete selection, and the four independent work channels from those
  distinct structures. It emits only plural `controller_commands.commands`;
  `active_command_id` is absent.
- `game_sources/research/player_topology` records the inspected KenshiLib,
  ForgottenGUI, current native call sites, portable contract, and live-proof
  boundary for the landed topology vocabulary.
- `game_sources/research/task_channels` records the inspected task-system
  declarations, executable call-site evidence, producer calls, portable
  contract, and withheld ownership boundary.

### Test-proven

- `tests/fixtures/protocol_2/valid_multiple_platoons_and_commands.json` contains
  two platoons and two simultaneously retained commands with disjoint captured
  recipients.
- `valid_truncated_world_model.json` covers exact and unknown-total truncation.
- Invalid fixtures reject contradictory membership, contradictory completeness,
  and the old 1.x `squad` / `active_command_id` shape.
- The Release x64 native conformance executable parses the same full and old
  fixtures and pins their two-platoon/two-command versus 1.x topology.
- The current 2.0 shared native fixture separately pins roster/platoon
  membership, active, primary-not-roster-order, complete selection, one retained
  ordinary order beside current activity with empty Jobs, and removal of the
  superseded authorities.
- Portable tests keep unknown ordinary totals, task subjects, and positions
  explicit, and prevent Jobs or activity from proving controller retention.
- Schema and generated-document freshness are part of the portable gate.

### Live-proven

The Protocol 2.0 producer has not had a supervised live run. The preceding
1.19-1.21 topology, bounded-work vocabulary, and exact resource-operator state
have durable reduced evidence artifacts under their research packages.
`player-topology-20260809T161112Z`, using matching built and installed DLL
SHA-256 `2dfee3ca27a3a2494b31386cff06e9db2ad02e38e7d3d6079fec0fb2234436bc`,
proves two authored nonempty platoons and linkage, active-tab changes, primary
and complete selection, same-session character identity across platoon moves,
and save/load restoration of membership, primary, and selection. Kenshi reset
the active tab on load. The bundle records the reset rather than inferring
active from membership, primary, selection, or saved pre-state.

This topology result does not prove simultaneous native commands, task-channel
behavior, controller ownership, or any other field not present in that 1.19
bundle. Task-channel behavior is separately live-proven by
`task-channels-20260809T172100Z` with matching built and installed 1.20 DLL
SHA-256 `3746e1dfd1feb9564c4a539388790b7d3f7f19e3971d965c08b223e8766b0d2f`.
Later engine sequence 329 shows a retained ordinary `OPERATE_MACHINERY` order
beside separate current activity while Jobs and permanent Jobs remain complete
and empty. That result does not establish a general command-ownership link.

Resource acceptance is separately live-proven by
`resource-operators-20260809T201826Z` with matching built and installed 1.21
DLL SHA-256
`91526b828e44035b0cb6de5a22b7cc5ad0c2e392b66a7b8adcbf9ae9403d8db8`.
Two identities were selected and both had the exact ordinary resource order,
while a capacity-one target's complete accepted set contained only Ribs. This
prevents the current singular command model from laundering selection or
queued work into accepted operation.

### Withheld and named follow-on work

- **Native plural command registry, deadline 2026-09-20:** populate more than one
  retained record, preserve disjoint recipient work, prove overlap behavior,
  and remove the current global singleton. Until that date the native producer
  emits at most one record, but no consumer may assume that cardinality.
- **Platoon lifecycle follow-on:** retain worthwhile longer-run roster mutation,
  pointer-reuse, and empty-management-row questions as named work rather than
  broadening the proven topology slice. Character identity remains explicitly
  session-scoped.
- **Task ownership correlation:** link an ordinary order to a controller command
  only from causal evidence; otherwise retain it as observed unattributed work.
  Jobs, permanent Jobs, and activity must not be adopted as controller-owned.
- **Resource progress semantics:** no progress-like native float is public until
  its natural-resource meaning, range, and rollover behavior are proven.
- **Exact live bundle:** record built and installed DLL hashes, pre-dispatch
  state, requests, acknowledgements, later engine evidence, and final
  dispositions for simultaneous commands. A returning call is not proof that
  Kenshi changed.

## Superseded planning records

The former body-shift and interaction-scope plans are preserved under
`docs/archive/` as historical provenance. This record owns the live Protocol
2.0 world-model decisions; the source model owns the exact schema.
