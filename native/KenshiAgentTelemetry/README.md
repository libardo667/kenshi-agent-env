# KenshiAgentTelemetry native plugin

This DLL is the telemetry and narrowly bounded control bridge from Kenshi to the
Python environment. It hooks Kenshi's own `TitleScreen::_NV_update` and
`PlayerInterface::update` methods, samples after the corresponding original
method returns on the same game/UI thread, and atomically replaces
`telemetry.latest.json`. The title hook emits a deliberately minimal
title/control-only snapshot. The player hook emits loaded-game telemetry and
owns native-command monitoring.

It exports fields with a relatively clear KenshiLib/MyGUI surface: pause,
speed, money, elapsed game minutes, camera state, complete selection, squad
life/conscious/down/crippled/combat state, nutrition reserve, blood,
inventory/equipment, modal and management UI state, exact dialogue
target/options, tooltip evidence, and bounded visible buttons/text plus named
item cells with owner windows and current bounds. Nearby telemetry carries
stable identity, roles, disposition, distance, position, viewport state, and
camera-relative bearing. Bounded world-target telemetry separately carries
exact reviewed mining-resource identities, positions, distances, supported
context actions, and resource levels. Squad telemetry includes Kenshi's
UI-facing current goal when available.

An item cell's `item_value` is base worth, not the shop's authoritative asking
price or sale offer. Transaction effect must be established from later money
telemetry.

Nearby roles keep anatomy, platoon commerce, leadership, dialogue, and exact
`ShopTrader::getTrader()` ownership separate. Exact ownership comes from a bounded registry maintained by
`ShopTrader` constructor/destructor hooks installed before save load; Kenshi's
spatial query does not enumerate these wrappers. A `GameWorld::resetGame` hook
clears that registry and prior native command acknowledgements before Kenshi
constructs a new or loaded session, since the plugin DLL remains resident
across those transitions.
Opaque entity IDs derive from validated Kenshi handles plus process/session
generations. Character IDs also survive handle-container transitions; other IDs
retain the complete handle identity. They survive squad/nearby and
world-target list reordering and distinguish duplicate names without serializing
addresses.
`identity_session_id` changes across process or game-session lifetimes.
`selected_character_ids` reports the full player-character selection set, while
the singular ID identifies its active member.
It retains the `0.3.0` causal command envelope and `0.4.0` dialogue/tooltip
observations, and adds capability-gated visible controls, management state,
named inventory cells, squad inventory/vitals, and additional movement
commands. The former food-specific policy is retired; these observations now
serve generic semantic contracts.
The first supervised `0.5.0` load found that sampling solely from
`PlayerInterface::update` cannot publish title controls before a save exists.
Both a direct detour of MyGUI's exported frame function and a MyGUI
`eventFrameStart` subscription crashed during startup and are rejected. They
also invoked a snapshot builder whose loaded-game assumptions were not valid
before world/player initialization. The lifecycle revision keeps the `0.5.0`
wire schema, hooks the pinned Kenshi `TitleScreen` method, and separates a
minimal title snapshot from the loaded-game snapshot. No third-party MyGUI
function or delegate list is modified.
Dialogue choices remain null when the dialogue cannot be read. Tooltip text and
source-widget bounds remain null when no tooltip is visible. Visible controls
are read only after the relevant Kenshi UI-thread update and bounded to 224
results, 2,048 visited widgets per pass, and depth 32; the plugin never invokes
their callbacks.

It also recognizes a private `Ctrl+Shift+F10` request bridge. Before the
hotkey, Python atomically publishes a strict `native_command.request.json`
carrying its UUID command ID, complete world revision, `native_assisted` mode,
identity session, exactly one selected stable ID, and command-specific
arguments:

- `approach_confirmed_vendor` is the legacy wire name for approaching any exact
  conscious, non-hostile humanoid dialogue target. It uses `PLAYER_TALK_TO` and
  completes only when dialogue opens with that exact target.
- `move_to_character` walks to one exact current nearby character through
  `MOVE_CUS_ORDERED` without opening dialogue and completes on arrival.
- `move_in_direction` walks a bearing/distance from the selected character,
  capped at 2,000 units, and completes inside the fixed arrival tolerance or
  after crossing the intended destination plane. Its request and acknowledgement
  carry an empty target plus the exact bearing and distance.
- `travel_to_map_destination` resolves one exact discovered town and approaches
  its direction-dependent gate waypoint. A gated town then receives one
  controller-owned interior order. Crossing the wall predicate does not
  terminate that order. Once its native-resolved endpoint is reached, exact
  current-town identity completes the command even when that town's geometry
  never exposes selected-character inside-walls state. An ungated town requires
  exact current-location identity. A reached interior leg without exact town
  identity cancels rather than inventing arrival.
- `exit_current_building` requires the selected character's indoor handle to
  resolve to a valid building, then resolves its current unlocked exit and
  outside point without accepting model-authored geometry.
- `perform_context_action` carries one reviewed semantic. `operate` re-resolves
  an exact current natural resource and `first_aid` an exact eligible squad
  member; both complete only after the selected character's exact AI goal names
  the corresponding Kenshi task and target.
- `produce_resource_output` adopts that exact already-running task or issues it
  once, then stays active through `Operating machine` and completes only when
  the resource output section contains a positive quantity.
- `open_context_inventory` re-resolves one exact resource and invokes Kenshi's
  ordinary building-inventory UI, completing only when that same handle owns
  the open contextual inventory.

The plugin rechecks all shared and command-specific facts and never substitutes
a nearer target. `native_control` exposes a bounded ring of keyed
accepted/rejected/completed/cancelled acknowledgements. Active work cancels if
selection, uninterrupted pause beyond the bounded handoff window, target
lifetime, or required target role changes. The legacy last-command fields
remain diagnostics. While an active command still belongs to one exact selected
character, the player-update hook reasserts `CameraClass::followObject` each
frame. This keeps the camera and world streaming centered on the command owner;
inactive, ambiguous, or selection-mismatched states never claim camera
ownership.
This makes the DLL a native-assisted control bridge, not a globally read-only
plugin. The Python runtime exposes these commands only in `native_assisted`
mode; `interface_only` filters their capabilities/state and rejects the marked
actions. The bounded nearby query uses a 400-world-unit town-local radius,
which covers most of the Hub from the default Wanderer spawn without encoding
a person or coordinate. World targets combine a 400-unit local scan and a
2,000-unit outer scan, deduplicate stable IDs, retain the nearest 128 recognized
objects, and warn if either source scan reaches capacity.

It explicitly leaves body-part wounds, bleeding rate, getting-eaten state,
imprisonment/enslavement, the internal task stack, distant world state, and
geometry occlusion unavailable or unvalidated. KenshiLib's raw
`isGettingEaten` byte is not exported because live validation found it set on a
healthy new character. The `food_items` scalar remains for compatibility but
has disagreed with named inventory in live evidence; consumers must prefer the
inventory list.

## Build

See the [Windows native setup guide](../../docs/GUIDE_WINDOWS_NATIVE_SETUP.md) for exact
media hashes, Visual C++/SP1 installation, Git LFS dependency setup, and
diagnostics.

1. Install RE_Kenshi and obtain the matching maintained KenshiLib development
   dependencies.
2. Install a Visual Studio version capable of using the Visual C++ 2010 x64
   (`v100`) platform toolset.
3. Set `KENSHILIB_DIR` to the dependency directory containing `Include` and
   `Libraries`.
4. Set `BOOST_INCLUDE_PATH` to the extracted Boost 1.60 root containing both
   `boost` and `stage\lib`.
5. Run `scripts\native_doctor.ps1` and resolve every failed check.
6. Run `scripts\build_native.ps1` to build **Release | x64** with local Windows
   intermediate/output directories. The build also runs the production native
   parser/serializer against the golden JSON fixtures shared with Python.
7. Run `scripts\stage_native.ps1 -BuiltDll <path-to-built-dll>`.
8. After reviewing the staged files, copy the staged `KenshiAgentTelemetry`
   folder to `<Kenshi>\mods\KenshiAgentTelemetry` and enable the mod in the
   Kenshi launcher.

The staged layout follows the current upstream HelloWorld example: its 46-byte
native-only `.mod` stub, `RE_Kenshi.json`, and the plugin DLL in one Kenshi mod
folder. A zero-byte marker is invalid and Kenshi will reject it while loading
game data.

## Output

By default the plugin writes to:

```text
%LOCALAPPDATA%\KenshiAgent\telemetry.latest.json
%LOCALAPPDATA%\KenshiAgent\plugin_status.json
%LOCALAPPDATA%\KenshiAgent\native_command.request.json
```

Set `KENSHI_AGENT_TELEMETRY_DIR` before launching Kenshi to override the folder.
The parent of an override must already exist; the plugin creates only the final
folder component.

## Verification sequence

- Launch to the title screen and confirm `plugin_status.json` says `ready`.
- At two client resolutions, verify `ui.visible_controls` reports the same
  unique configured title/save labels with different current bounds.
- Enter a disposable save and confirm telemetry sequence numbers increase.
- Pause/unpause and verify the field changes.
- Select different squad members and verify the singular ID, complete selected
  ID set, and squad `selected` flags agree.
- Compare nutrition reserve, blood, combat state, and named inventory/equipment
  against the selected character's visible UI.
- Open inventory, trade, stats, map, tech, and squad management. Verify window
  ownership, named item cells, dedicated window/tab fields, and current control
  bounds.
- Reorder a squad and change the camera/nearby presentation; verify entity IDs
  remain attached to handles rather than list positions or names.
- Load a disposable save and verify `identity_session_id` changes without
  retaining old selection, nearby, or native target IDs.
- Publish a stale-revision request and verify its exact command ID is rejected
  without movement.
- Publish one current exact-target request and verify a later keyed acceptance,
  no substitute target, terminal completion/cancellation semantics, and final
  pause.
- Repeat for `move_to_character`, verifying bounded arrival without opening
  dialogue and explicit cancellation when the world is paused.
- Perform the same live check for `move_in_direction`. The 2026-07-25 probe
  `20260725T2223-direction-smoke-061-green` proved one exact 36.5-degree,
  30-unit order from keyed acceptance through `walk_destination_reached`, about
  30.4 units of plausible movement, a resulting frame, and safe final pause.
  Repeat across other bearings, distances, obstacles, and scenes before making
  broader movement claims.
- During native movement, verify the camera center follows the exact selected
  character without a separate portrait gesture. Confirm inactive commands and
  selection mismatch do not retain agent camera ownership.
- Repeat for `perform_context_action`: first verify the exact target/action pair
  is present in `world_targets`, then issue that pair and confirm keyed
  `context_task_started`, plausible movement/task behavior in a resulting
  frame, fresh advancing telemetry, and final pause.
- Repeat the full resource transaction: prove `produce_resource_output` adopts
  matching work without reissue and reaches `resource_output_ready`; prove the
  exact contextual inventory opens; then prove equal output loss and selected
  inventory gain after one exact-cell transfer.
- Move a character and verify position and movement speed change plausibly.
- Compare squad count and names against the UI.
- Leave the game running for ten minutes and inspect `kenshi.log` for plugin
  errors or hitches.

Do not enable live Python input until these checks pass. The source is based on
the pinned maintained headers and compiles as a VS2010 SP1 `Release | x64` DLL.
Protocol `0.3.0` passed its load/two-hertz telemetry smoke test and one
supervised stale-rejection/exact-target completion proof. The additive `0.5.0`
split-lifecycle build passed a supervised 1920x1080 title canary, semantic
load-to-pause, generic dialogue/trade chain, UI surveys, and later long-form
runs with inventory/trading/movement telemetry. Alternate-resolution, broader
identity transitions, repeated interruption/ownership trials, and multi-hour
stability remain open in the broader checklist.

Protocol `0.6.0` is the additive command-identity correction built and installed
on 2026-07-25. Its first live targetless probe exposed the paused-start timing
defect. Protocol `0.6.1` replaces tick-count cancellation with a resettable
wall-clock pause window and adds production-tested destination-plane completion.
The final 202,240-byte installed DLL has SHA-256
`0f30b245382210b5a0e7c3c347d22f3c320eae17142808cea1a44ae49f214afb`;
it passed the guarded loaded/paused health window and the exact live completion
run named above.

Protocol `0.7.0` adds reported squad indoor membership, the parameterless
`exit_current_building` request, and shared no-progress movement
terminalization. The plugin resolves one valid unlocked door and its outside
point from the selected character's current building. The current 205,824-byte
installed DLL has SHA-256
`2110dcf73421a5919e5c3f0efb44cdd9929946a0902aa09d8662191cd94ba8d9`;
run `20260726Tnative-building-exit-live-04` completed the exact keyed exit with
`outside_door_destination_reached` and a safe final pause.

Protocol `1.0.0` removes undocumented task-probability fields from the wire.
Structural mining identity determines presence; an advertised context action
authorizes only a bounded exact-target attempt. The bridge revalidates the
target and completes only on the exact AI task and subject. See
[`ADR_CONTEXT_ACTION_AUTHORITY`](../../docs/ADR_CONTEXT_ACTION_AUTHORITY.md) for
the observability and prospecting boundary.

Protocol `1.1.0` adds completeness markers for bounded visible controls and
squad inventory, exact contextual-inventory ownership, retained resource
production, and exact inventory opening. Its C++ conformance target builds, but
the DLL has not yet been live-loaded or supervised in Kenshi.
