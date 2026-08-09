# Checkpoint: real player topology producer migration

This checkpoint records the current boundary after the coherent Protocol 1.19
player-topology slice. Source-proven, test-proven, live-proven, and withheld
conclusions are separate below.

## Repository and authority

```text
parent commit          5753bcb8ca993d46964fc35349338e1f91855bce
integration branch     main
starting remote        origin/main matched parent commit
starting tree          clean
producer protocol      1.19.0
request schema         1.4
loaded capabilities    47
```

Recent `main` authority was inspected before editing. `5753bcb` accepts the
strict Protocol 2.0 world-model decision; `70f6a65` makes research evidence a
checked contract; `04855dc` owns the current documentation truth audit;
`965a495` requires this checkpoint to move with every goal; and `2686719` owns
the complete portable gate.

Exact current producer shape is owned by `TelemetrySnapshot` in
`src/kenshi_agent/core/telemetry.py`, native serialization in
`native/KenshiAgentTelemetry/KenshiAgentTelemetry.cpp`, and the generated
schema. `docs/PROTOCOL_2_WORLD_MODEL_DECISION.md` remains authority for the
future bounded-work and plural-command breaking boundary. The new
`game_sources/research/player_topology` package owns the inspected Kenshi
topology claims and their limits.

## Coherent 1.19 slice

Protocol 1.19 cleanly replaces the old player shape with:

- complete `roster`, where each player carries `platoon_id`;
- explicit `platoons` with stable Kenshi string IDs, names, and member IDs;
- separate `active_platoon_id` and `primary_character_id`;
- one complete root `selected_character_ids` set; and
- explicit roster, platoon, and selection completeness.

The strict model validates membership in both directions, prevents a character
from belonging to two platoons, requires all referenced IDs to exist, and
requires the primary character to be selected. The producer reads primary from
Kenshi's primary owner; roster order has no authority.

`TelemetrySnapshot.squad`, `CharacterState.selected`,
`UIState.selected_character_id`, and `UIState.selected_character_ids` were
deleted from producer, consumers, mocks, native fixtures, portable tests,
schemas, and generated documentation. They have no alias, fallback, or dual
reader. The still-singular native command registry is not disguised as part of
this goal and remains a Protocol 2.0 follow-on.

Live evidence found that Kenshi can change a player's handle container fields
when moving the player between platoons. The superseded container-normalization
assumption was removed in the same slice. The native producer now keeps the
first player candidate identity for one live `Character*` and `validKey` pair,
rejects pointer reuse when `validKey` changes, and clears the registry on
GameWorld reset. That makes player IDs stable for the current identity session,
not across sessions.

Portable models, mocks, scenario fixtures, native telemetry fixtures,
capability manifests, schemas, generated documentation, the proof ledger, and
the current documentation all move together with this authority.

## Source-proven

- KenshiLib 0.4.0 `PlayerInterface.h` separately declares
  `getAllPlayerCharacters()`, `getCurrentActivePlatoon()`,
  `selectedCharacter`, and `selectedCharacters`.
- KenshiLib 0.4.0 `Character.h` declares `Character::getPlatoon()`;
  `Platoon.h` declares `ActivePlatoon::getName()` and the underlying
  `Platoon::getPlatoonStringID()`.
- Current native call sites consume those structures independently and discard
  every pointer after the snapshot.
- ForgottenGUI and `SquadManagementScreen` were inspected and rejected as
  player-topology authority. They describe UI, not the authoritative roster,
  membership, primary, or selection structures.
- `game_sources/kenshi/controls.cfg` binds `change_squad=Tab`.

These declarations and calls prove the available sources and the implemented
read path. They do not by themselves prove what Kenshi changed or persisted.

## Test-proven

- The shared 1.19 fixture preserves multiple platoons, exact bidirectional
  membership, active platoon, primary not in roster position zero, and complete
  multi-selection through the strict Python model and compiled C++ conformance
  target.
- Negative portable tests reject every removed field, dangling linkage,
  duplicate membership, incomplete contradictions, and an unselected primary.
- Compiled registry tests preserve one ID for the same pointer/key across a
  candidate-handle change, reject pointer reuse with a changed key, and clear
  the mapping on reset.
- Capability and generation tests pin the new topology vocabulary and ensure
  the native manifest/header, schemas, examples, and generated references move
  with it.

Fixtures and passing calls prove contracts and compiled behavior, not a changed
game world.

## Native build and installed provenance

The Release x64 VS2010 SP1 build completed and its native conformance executable
reported `Native protocol fixtures and semantics passed.` The built DLL was
installed before the decisive live run.

```text
built DLL path          C:\Users\levib\AppData\Local\KenshiAgent\build\native\bin\KenshiAgentTelemetry.dll
built DLL sha256        2dfee3ca27a3a2494b31386cff06e9db2ad02e38e7d3d6079fec0fb2234436bc
built DLL size          413696 bytes
installed DLL path      C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll
installed DLL sha256    2dfee3ca27a3a2494b31386cff06e9db2ad02e38e7d3d6079fec0fb2234436bc
installed DLL size      413696 bytes
conformance exe sha256  90582667c5d94adb0eee26a44cd4ff97db10ef257ec3b5474d244fd827592dc6
conformance exe size    280576 bytes
built equals installed  YES
provenance chain        consistent; generated header current; 47 capabilities present
```

## Live-proven

Exact bundle: `runs/player-topology-20260809T161112Z/manifest.json`.

The ignored local bundle indexes every raw frame-plus-telemetry snapshot under
`runs/dev-snapshots/` and records each pre-state, request, acknowledgement,
later engine observation, and disposition separately. Its decisive evidence is:

- a baseline with Paste primary despite being second in roster order;
- a later authored world with Hep in `platoon-Nameless_0` and Paste in
  `platoon-Nameless_1`;
- Tab changing active platoon while primary and complete selection remained
  independently observable;
- exact native selection requests acknowledged and then confirmed by later
  telemetry as primary/selection changes;
- Paste retaining one full session-scoped ID while moved into the other
  platoon and back under the current DLL;
- F5 input paired with changed nonempty `quick.save`, `Nameless_0`, and
  `Nameless_1` file hashes, rather than treating the input receipt as proof;
- a deliberate post-save mutation to active `Nameless_0`, primary Hep,
  selected `[Hep]`, followed by F9;
- F9 input paired with identity-session advance 2 to 3 and later restored
  platoon IDs, names, membership, primary Paste, and selected `[Paste]`; and
- final sequence 1096: loaded, paused, modal-free, idle, no active native
  command, two complete platoons, primary Paste, selected `[Paste]`.

Kenshi reset the active tab to `Nameless_0` on quickload instead of restoring
the saved `Nameless_1`. The producer reported that distinction. A returned UI
input, getter call, or native acknowledgement was never used as proof of the
later game state.

Final live disposition: **the game remains loaded, paused, modal-free, idle,
and has no active native command; built and installed DLL hashes match**.

## Withheld and named follow-on work

- **Player identity lifecycle:** character IDs are deliberately session-scoped;
  no cross-GameWorld-reset character-ID continuity is claimed. Longer-run
  roster mutation and pointer-reuse coverage remain follow-on work.
- **Empty management rows:** an empty UI-created squad row had no player member
  linkage and was not exported as a player platoon. Empty-row semantics require
  separate evidence before adding another authority.
- **Active persistence:** the recorded load reset active platoon; no active-tab
  persistence claim is made.
- **Protocol 2.0 bounded work and plural commands:** task-channel ownership,
  plural retained commands, overlap, supersession, and registry bounds remain
  the accepted next breaking boundary. They are not partially implemented here.

## Verification

The complete portable gate passed over this final candidate with:

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
```

It covers locked dependency sync, Ruff, strict mypy, research-package
validation, schema and generated-document freshness, the complete pytest suite,
and `git diff --check`. The statement above is valid only together with the
successful gate output for this checkout; if the candidate changes, the gate
must be rerun.
