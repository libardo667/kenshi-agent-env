# KenshiAgentTelemetry native plug-in

`KenshiAgentTelemetry.dll` is the loaded-game telemetry and bounded control
bridge between Kenshi and the Python runtime. This file describes the current
source contract. Historical protocol milestones belong in Git history and named
run evidence, not in a second current protocol narrative.

## Current protocol

```text
telemetry protocol       2.0.0
native request schema    1.6
loaded-world capabilities 50
controller command records emitted by native at most one until 2026-09-20
```

The source declarations are authoritative. `KenshiAgentTelemetry.cpp` owns the
telemetry version, `NativeCommandRequest` in
`src/kenshi_agent/core/transport.py` owns the strict Python request schema, and
`GameplayCapabilities.json` owns the loaded-world capability vocabulary. The
portable consistency tests and the native conformance executable compare the
shared native-command JSON fixtures with both request implementations. The
conformance target also reads the current 2.0 player-topology/work fixture and
the full Protocol 2.0 world-model fixtures.

Protocol 2.0.0 retains the clean player-topology break introduced by 1.19 and
the independent work channels introduced by 1.20. Player characters are
serialized under `roster`; `platoons`, `active_platoon_id`,
`primary_character_id`, and the complete root `selected_character_ids` set have
separate owners. The old `squad`, per-character `selected`, and UI-owned
selection fields are gone rather than accepted as aliases. Each character's
`work` object independently exports `ordinary_orders`, `jobs`,
`permanent_jobs`, and `current_activity`. The old `task_state`, flattened
count/completeness fields, and cross-channel task-name ownership inference are
gone rather than retained as fallbacks. `controller_commands.commands` is
plural and the old `native_control`, `active_command_id`, and
`acknowledgements` wire names are rejected. The native producer temporarily
publishes at most one record from its current global owner; that cardinality
limit expires on 2026-09-20, and every consumer already treats the field as a
collection.

## Hooks and telemetry

The plug-in installs hooks on Kenshi's own game/UI thread:

- `TitleScreen::_NV_update` publishes a minimal title/control snapshot.
- `PlayerInterface::update` monitors native commands and publishes loaded-world
telemetry after the original update returns.
- `ContextMenu::showContextMenu` plus a muted `ContextMenuGUI::show` hook let the
  plug-in ask Kenshi which orders it would display without drawing a menu over
  the operator's game. Exact declarations, addresses, failed predicates,
  crashes, and withheld conclusions live in the
  [context-menu research object](../../game_sources/research/context_menu_orders/conclusion.md).
- `ShopTrader` lifecycle hooks maintain exact shop-owner identity, and
  `GameWorld::resetGame` clears session-bound registries and command records.

Loaded telemetry includes clock and location state, camera state, the complete
player roster, exact platoon membership and active platoon, exact primary and
complete selection, roster vitals/inventory/task summaries, nearby characters
and roles, bounded world targets, discovered map destinations, dialogue and
tooltip state, visible controls, every currently exported open inventory, and
keyed controller command records. Stable character IDs are derived from
validated Kenshi handles plus process/session generations. Platoon IDs use
Kenshi's platoon string ID under a `platoon-` namespace. Names and list
positions are not identities.

Ordinary order queues expose only what KenshiLib's `ActionDeque` can prove:
empty and one-item queues are complete, while larger queues retain bounded
first/second/tail samples with an unknown total and no invented tail position.
Jobs and permanent Jobs use their separately enumerable containers. Task
subjects, positions, and totals are nullable wherever the inspected source
cannot establish them. Current activity is observational and does not imply a
retained ordinary order or configured Job.

Every natural-resource target separately exports nullable exact
`operator_capacity`, the complete `current_operator_ids` set, and exact output
inventory stacks with explicit completeness flags. Admission monitoring reads
Kenshi's accepted-operator set; it never substitutes the selected recipients,
ordinary orders, Jobs, current activity, or animation. Progress-like native
floats remain unexported because their natural-resource semantic and range are
not proven. The exact source and live boundary is recorded in the
[resource-operator research object](../../game_sources/research/resource_operators/conclusion.md).

Bounded collections expose completeness or warning evidence where the model
supports it. An empty bounded result must not be generalized beyond that stated
boundary.

## Request transport

Python atomically replaces
`%LOCALAPPDATA%\KenshiAgent\native_command.request.json`. The title and player
update hooks watch the file's last-write time, wait one frame after a change so
an atomic replacement cannot be read halfway through, and then parse and
dispatch the request on Kenshi's UI thread.

Atomic replacement is the only dispatch signal. No keyboard or pointer trigger
remains in either the plug-in or the supported Python path. Emergency stop and
final host-safety fallback remain a deliberately separate input boundary when
the native side cannot be trusted to stop itself.

Every request carries:

- a strict schema version and UUID-shaped command ID;
- `native_assisted` control mode and the current identity session;
- the complete authored selected-recipient basis, except for commands that name
  their own recipient;
- the telemetry revision on which dispatch authority was based; and
- only the fields belonging to that command's wire projection.

The plug-in rejects duplicate IDs, future or stale revisions, session mismatch,
malformed command shapes, conflicting UI state, changed targets, and failed
command-specific authority. Acknowledgements are keyed and carry accepted and
terminal telemetry sequences where applicable. An acknowledgement proves what
the plug-in accepted or observed at its terminal boundary; it is not by itself
proof of an intended later world outcome.

The title surface accepts `continue_game`, `load_game` with one exact save name,
and `new_game` with one exact Game Start ID. A title transition begins a new
identity session, so its accepted record is preserved through `GameWorld::resetGame`
and becomes terminal as `world_session_loaded` in the first loaded-world frame.
The supported launcher refuses a loaded session without that explicit cross-session
acknowledgement.

## Current command surface

The loaded-world protocol currently covers:

- exact selection, regrouping, nearby movement, directional movement, building
  exit, and map travel;
- exact dialogue approach, resource context actions, generic character orders,
  resource production, and local resource survey;
- paired trade/loot/resource windows and item transfer;
- native cleanup of Prospecting, dialogue, message boxes, inventories, and
  management windows through one planner-visible interface exit;
- elective body shifting;
- pause and speed control; and
- the diagnostic body-platoon probe.

Before a world exists, the title protocol separately covers Continue, exact save
load, and exact Game Start creation. The post-load pause uses the same request-file
watcher.

`shift_body_platoon` has no planner-visible operation definition. It is a
diagnostic route, not a hidden fallback.

The wire name `approach_confirmed_vendor` is historical. Its current operation
is `approach_dialogue_target`, and it may target any exact conscious,
non-hostile character currently confirmed talkable.

The native bridge temporarily retains at most one record until 2026-09-20.
Selection is captured in the request but several native monitors still depend on
the current selection, and separate retained commands for disjoint recipient groups
are not implemented. Those open limits are tracked in the
[Protocol 2.0 world-model decision](../../docs/PROTOCOL_2_WORLD_MODEL_DECISION.md).

## Reverse-engineered subsystem authority

This README no longer restates ABI conclusions. The canonical packages record
the exact executable and library identity, symbols or RVAs, signature
confidence, probes, crashes, contradictions, and withheld claims:

- [context-menu orders](../../game_sources/research/context_menu_orders/conclusion.md);
- [inventory transfer and simplified pricing](../../game_sources/research/inventory_transfer/conclusion.md);
- [body shift](../../game_sources/research/body_shift/conclusion.md);
- [prospecting window](../../game_sources/research/prospecting_window/conclusion.md); and
- [player topology](../../game_sources/research/player_topology/conclusion.md); and
- [task channels](../../game_sources/research/task_channels/conclusion.md); and
- [resource operators](../../game_sources/research/resource_operators/conclusion.md).

Current source implements those reviewed conclusions. The packages, not this
overview, own the reverse-engineering argument. Acceptance of any native call
is not proof of later game state.

## Build and install

The plug-in targets Visual C++ 2010 SP1 x64 (`v100`) and the configured
maintained KenshiLib headers/libraries.

1. Set `KENSHILIB_DIR` to the dependency directory containing `Include` and
   `Libraries`.
2. Set `BOOST_INCLUDE_PATH` to Boost 1.60 containing `boost` and `stage\lib`.
3. Run `scripts\native_doctor.ps1` and resolve every failed check.
4. Run `scripts\build_native.ps1`. The Release x64 build also runs
   `NativeCommandProtocolTests.exe` against `tests\fixtures\native_commands`,
   canonical research fixtures, `tests\fixtures\protocol_2`, and
   `tests\fixtures\native_telemetry`.
5. Run `scripts\stage_native.ps1 -BuiltDll <path-to-built-dll>`.
6. After review, copy the staged `KenshiAgentTelemetry` folder into Kenshi's
   `mods` directory and enable it in the launcher.

The staging script includes the DLL, `RE_Kenshi.json`, third-party notices,
this README, and the 46-byte native-only `.mod` stub. It stages only; it does
not install into Kenshi.

## Output paths

By default the plug-in and supported launcher exchange/write:

```text
%LOCALAPPDATA%\KenshiAgent\telemetry.latest.json
%LOCALAPPDATA%\KenshiAgent\plugin_status.json
%LOCALAPPDATA%\KenshiAgent\native_command.request.json
%LOCALAPPDATA%\KenshiAgent\native_startup_transition.latest.json
```

Set `KENSHI_AGENT_TELEMETRY_DIR` before launching Kenshi to override the folder.
The parent of an override must already exist; the live stack creates only the
final folder component. `native_startup_transition.latest.json` is the launcher's
exact title-request/ack/loaded handoff capture, not a second protocol producer.

## Evidence classification

### Source-proven

- The hook, protocol, command, inventory-model, simplified-pricing, and body
  shift paths above are present in the current source and in the configured
  KenshiLib declarations they call.
- Protocol 2.0.0 and the 52-entry loaded-world capability manifest are embedded
  into the native build inputs.
- Atomic request replacement is the sole dispatch signal; neither plug-in nor
  supported Python path contains a trigger hotkey.

### Test-proven

- Portable tests validate the strict Python request/acknowledgement models,
  shared command vocabulary, generated schemas, capability manifest/header
  parity, and golden fixture set.
- A Windows native build runs the production C++ parser/serializer against the
  same golden request fixtures, checks the current multi-platoon and independent
  work-channel telemetry fixture, and checks the 2.0 specification fixtures
  keep their breaking plural-command topology.
- `scripts/check_native_provenance.py` compares protocol and capability strings
  in the installed binary and records built/installed hashes. See the current
  [checkpoint](../../docs/CHECKPOINT.md) for the measured result.

### Live-proven

`native-launch-20260810T024659Z` proves the public fresh-launch path against
built and installed DLL SHA-256
`c8e3da7572b2074db55c941acd1ff26bdc4d302a6b8c8f62bd20b10e9b55e083`.
It preserves title sequence 2, the exact `load_game("KenshiAgentScenario")`
request, the terminal cross-session `world_session_loaded` acknowledgement in
loaded sequence 37, the native pause acknowledgement, plural-selection scenario
attestation, and advancing loaded-paused health through sequence 350. The reduced
artifact is
[`docs/reconstruction/native_launch_20260810.json`](../../docs/reconstruction/native_launch_20260810.json).

`player-topology-20260809T161112Z` proves the current producer against built and
installed DLL SHA-256
`2dfee3ca27a3a2494b31386cff06e9db2ad02e38e7d3d6079fec0fb2234436bc`.
Its later raw snapshots prove two authored nonempty platoons and linkage, Tab
changing active independently, exact primary and complete-selection changes,
and one full session-scoped character ID surviving a move between platoons and
back. Changed quicksave files plus a later GameWorld session advance prove that
load restored both platoons, membership, primary Paste, and selected `[Paste]`.
Kenshi reset the active tab to `Nameless_0` instead of restoring saved
`Nameless_1`; that observed result is not hidden behind a persistence claim.

`task-channels-20260809T172100Z`, using matching built and installed 1.20 DLL
SHA-256 `3746e1dfd1feb9564c4a539388790b7d3f7f19e3971d965c08b223e8766b0d2f`,
records empty pre-dispatch work channels, exact request and acknowledgement, and
later engine sequence 329. That later frame shows Paste retaining one ordinary
`OPERATE_MACHINERY` order at position 0 beside separate current activity with
null position while Jobs and permanent Jobs remain complete and empty. Final
sequence 383 is loaded, paused, modal-free, and has no active native command.

`resource-operators-20260809T201826Z`, using matching built and installed 1.21
DLL SHA-256
`91526b828e44035b0cb6de5a22b7cc5ad0c2e392b66a7b8adcbf9ae9403d8db8`,
proves the new loaded layout and causal acceptance terminal. Two exact
characters were selected and both received an ordinary order against a
capacity-one target, but complete `current_operator_ids` contained only Ribs.
Final sequence 878 is loaded, paused, modal-free, and has no active command.

Other named live evidence remains operation-specific. Reverse-engineering
conclusions and their exact limitations belong under `game_sources/research/`;
the interaction proof ledger links those conclusions to operations. Every live
conclusion depends on later engine telemetry in its named bundle, not merely on
request delivery or an acknowledgement.

### Withheld and open

- The plug-in does not support plural simultaneously active native command
  records.
- Exact command-to-ordinary-order ownership remains withheld. A task observed in
  an ordinary queue is unattributed unless causal command evidence establishes
  ownership; Jobs, permanent Jobs, and activity are never substitutes.
- No live multi-entry ordinary queue or invalid task subject was observed, so
  unknown totals, sampled tail positions, and null subjects remain source- and
  test-proven only.
- Resource-specific assigned-worker identities remain unknown when a character
  task subject is unresolved. Work progress remains withheld because no
  progress-like native scalar has a proven natural-resource semantic, range,
  and rollover contract.
- Character identity is session-scoped. The topology bundle does not claim the
  same character ID survives GameWorld reset, that an empty management row is
  a player platoon, or that one bounded run proves arbitrary roster churn.
- Active-platoon persistence is specifically withheld: the recorded quickload
  restored membership, primary, and selection but reset the active tab.
- Several group-recipient, delayed-continuation, session-reset, and retained
  order lifecycle conclusions remain unproven.
- Body shift lacks a complete named operation bundle even though a manual live
  dispatch informed the implementation.
- Alternate host configurations and long-duration stability require their own
  evidence; an installed hash match does not prove live behavior.

Exact current source, test, live, and withheld classifications are maintained in
the [checkpoint](../../docs/CHECKPOINT.md) and
[proof ledger](../../docs/reconstruction/interaction_proof_status.json).
