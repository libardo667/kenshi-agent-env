# Protocol 2.0 world model

Status: **accepted specification; 1.19 topology vocabulary landed, bounded work and plural command migration not started**

Protocol: `2.0.0`

Typed authority: `src/kenshi_agent/core/protocol_2.py`

Generated schema: `schemas/protocol_2_world_model.schema.json`

## Decision

Protocol 2.0 remains a breaking transition for bounded work and controller
causality. Its topology vocabulary has already landed as a clean break in the
current 1.19 producer: the player collection is `roster`, never `squad`, and
platoons, active, primary, and selection are distinct. Controller causality is
still the future `controller_commands`, partitioned into plural
`retained_commands` and `recent_terminal_commands`; current 1.19 still has the
singular `native_control.active_command_id` and does not claim 2.0 compatibility.

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
alias or dual-read path. Independently, the 1.19 model also rejects the removed
`squad`, per-character `selected`, and UI-owned selection fields.

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
- Current native source now emits 1.19 `roster`, platoons, active, primary, and
  complete selection from those distinct structures. It still emits singular
  `native_control.active_command_id`; bounded work and plural controller
  records remain the 2.0 migration target.
- `game_sources/research/player_topology` records the inspected KenshiLib,
  ForgottenGUI, current native call sites, portable contract, and live-proof
  boundary for the landed topology vocabulary.

### Test-proven

- `tests/fixtures/protocol_2/valid_multiple_platoons_and_commands.json` contains
  two platoons and two simultaneously retained commands with disjoint captured
  recipients.
- `valid_truncated_world_model.json` covers exact and unknown-total truncation.
- Invalid fixtures reject contradictory membership, contradictory completeness,
  and the old 1.x `squad` / `active_command_id` shape.
- The Release x64 native conformance executable parses the same full and old
  fixtures and pins their two-platoon/two-command versus 1.x topology.
- The current 1.19 shared native fixture separately pins roster/platoon
  membership, active, primary-not-roster-order, complete selection, and removal
  of the superseded authorities.
- Schema and generated-document freshness are part of the portable gate.

### Live-proven

The full Protocol 2.0 bounded-work and plural-command specification has not had
a live producer run. Its already-landed 1.19 topology vocabulary has.
`player-topology-20260809T161112Z`, using matching built and installed DLL
SHA-256 `2dfee3ca27a3a2494b31386cff06e9db2ad02e38e7d3d6079fec0fb2234436bc`,
proves two authored nonempty platoons and linkage, active-tab changes, primary
and complete selection, same-session character identity across platoon moves,
and save/load restoration of membership, primary, and selection. Kenshi reset
the active tab on load. The bundle records the reset rather than inferring
active from membership, primary, selection, or saved pre-state.

This live result does not prove simultaneous native commands, bounded task
channels, controller ownership, or any other not-yet-produced Protocol 2.0
field.

### Withheld and named follow-on work

- **Protocol 2.0 bounded-work producer migration:** replace the current task and
  controller shapes atomically with bounded work and plural controller records;
  do not reintroduce topology aliases during that transition.
- **Native plural command registry:** populate more than one retained record,
  preserve disjoint recipient work, prove overlap behavior, and remove the
  current global singleton.
- **Platoon lifecycle follow-on:** retain worthwhile longer-run roster mutation,
  pointer-reuse, and empty-management-row questions as named work rather than
  broadening the proven topology slice. Character identity remains explicitly
  session-scoped.
- **Task ownership correlation:** link ordinary orders or Jobs to controller
  commands only from causal evidence; otherwise retain them as observed unowned
  work.
- **Exact live bundle:** record built and installed DLL hashes, pre-dispatch
  state, requests, acknowledgements, later engine evidence, and final
  dispositions for simultaneous commands. A returning call is not proof that
  Kenshi changed.

## Superseded planning records

The former body-shift and interaction-scope plans are preserved under
`docs/archive/` as historical provenance. This record owns the live Protocol
2.0 world-model decisions; the source model owns the exact schema.
