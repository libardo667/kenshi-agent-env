# Protocol 2.0 world model

Status: **accepted specification; producer migration not started**

Protocol: `2.0.0`

Typed authority: `src/kenshi_agent/core/protocol_2.py`

Generated schema: `schemas/protocol_2_world_model.schema.json`

## Decision

Protocol 2.0 makes one breaking transition. The player collection is `roster`,
never `squad`. Controller causality is `controller_commands`, partitioned into
plural `retained_commands` and `recent_terminal_commands`; there is no singular
`active_command_id`.

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

All objects reject extra fields. Protocol `1.x`, `squad`, `native_control`, and
`active_command_id` therefore fail validation rather than entering an alias or
dual-read path.

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
- Current native source reads those four task channels separately but still
  emits 1.x `squad` and singular `active_command_id`. That contradiction is the
  migration target, not evidence that the producer already implements 2.0.

### Test-proven

- `tests/fixtures/protocol_2/valid_multiple_platoons_and_commands.json` contains
  two platoons and two simultaneously retained commands with disjoint captured
  recipients.
- `valid_truncated_world_model.json` covers exact and unknown-total truncation.
- Invalid fixtures reject contradictory membership, contradictory completeness,
  and the old 1.x `squad` / `active_command_id` shape.
- The Release x64 native conformance executable parses the same full and old
  fixtures and pins their two-platoon/two-command versus 1.x topology.
- Schema and generated-document freshness are part of the portable gate.

### Live-proven

No live gameplay was performed for this specification. The native conformance
build was not installed and dispatched no command. This record makes no new claim
about stable platoon identity, multi-platoon enumeration, simultaneous native
commands, task ownership, or world change.

### Withheld and named follow-on work

- **Protocol 2.0 producer migration:** replace the 1.x producer and consumer
  atomically; delete the old fields and 1.x fixtures with no dual-read fallback.
- **Native plural command registry:** populate more than one retained record,
  preserve disjoint recipient work, prove overlap behavior, and remove the
  current global singleton.
- **Platoon identity proof:** verify stable IDs and complete membership across
  tab changes and save/load before claiming that behavior live.
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
