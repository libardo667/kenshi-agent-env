# KenshiAgentTelemetry native plugin

This DLL is the telemetry and narrowly bounded control bridge from Kenshi to the
Python environment. It hooks `PlayerInterface::update`, calls the original
function first, samples on that same game/UI thread at two hertz, and atomically
replaces `telemetry.latest.json`.

It currently exports only fields that have a relatively clear KenshiLib surface:
pause, speed, money, camera position, selected character, squad names, basic
state, position, movement speed, food-item count, modal UI state, and bounded
nearby-character telemetry. Nearby roles keep anatomy, platoon commerce,
leadership, dialogue, Kenshi's native talk-task probability, and exact
`ShopTrader::getTrader()` ownership separate. Exact ownership comes from a
bounded registry maintained by `ShopTrader` constructor/destructor hooks
installed before save load; Kenshi's spatial query does not enumerate these
wrappers. A `GameWorld::resetGame` hook clears that registry and prior native
command acknowledgements before Kenshi constructs a new or loaded session, since
the plugin DLL remains resident across those transitions.
Protocol `0.2.0` also derives opaque entity IDs from validated Kenshi handles
plus process/session generations. These IDs survive squad/nearby list
reordering and distinguish duplicate names without serializing addresses.
`identity_session_id` changes across process or game-session lifetimes.
`selected_character_ids` reports the full player-character selection set, while
the singular ID identifies its active member.
It also recognizes a private `Ctrl+Shift+F10` bridge for
`approach_confirmed_vendor`. Before issuing anything, the plugin re-enumerates
nearby characters and requires a conscious, non-hostile humanoid who has a
vendor list, leads that platoon, and has dialogue. It then uses Kenshi's own
`PLAYER_TALK_TO` player order with the exact handle and indoor destination.
`native_control` reports the legacy command sequence/result plus both the target
display name and stable target ID. It still lacks a caller command ID,
revision/selection fence, and completion revision, so it is not yet a causal
command protocol.
This makes the DLL a native-assisted control bridge, not a globally read-only
plugin. The Python runtime exposes this command only in `native_assisted` mode;
`interface_only` filters the capability/state and rejects the marked skill.
The bounded nearby query uses a 400-world-unit town-local radius, which includes
the Hub Barman from the default Wanderer spawn without encoding his identity or
coordinates.
It explicitly warns that hunger, wounds, getting-eaten state, detailed
inventory, and click-target occlusion remain unimplemented. KenshiLib's raw
`isGettingEaten` byte is not exported because live validation found it set on a
healthy new character.

## Build

See the [Windows native setup guide](../../docs/WINDOWS_NATIVE_SETUP.md) for exact
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
   intermediate/output directories.
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
```

Set `KENSHI_AGENT_TELEMETRY_DIR` before launching Kenshi to override the folder.
The parent of an override must already exist; the plugin creates only the final
folder component.

## Verification sequence

- Launch to the title screen and confirm `plugin_status.json` says `ready`.
- Enter a disposable save and confirm telemetry sequence numbers increase.
- Pause/unpause and verify the field changes.
- Select different squad members and verify the singular ID, complete selected
  ID set, and squad `selected` flags agree.
- Reorder a squad and change the camera/nearby presentation; verify entity IDs
  remain attached to handles rather than list positions or names.
- Load a disposable save and verify `identity_session_id` changes without
  retaining old selection, nearby, or native target IDs.
- Move a character and verify position and movement speed change plausibly.
- Compare squad count and names against the UI.
- Leave the game running for ten minutes and inspect `kenshi.log` for plugin
  errors or hitches.

Do not enable live Python input until these checks pass. The source is based on
the pinned maintained headers, compiles as a VS2010 SP1 `Release | x64` DLL,
and passed its initial load/two-hertz telemetry smoke test in the user's Kenshi
installation. The broader checklist remains intentionally incomplete.
