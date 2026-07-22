# KenshiAgentTelemetry native plugin

This DLL is the read-only bridge from Kenshi to the Python environment. It hooks
`PlayerInterface::update`, calls the original function first, samples on that
same game/UI thread at two hertz, and atomically replaces
`telemetry.latest.json`.

It currently exports only fields that have a relatively clear KenshiLib surface:
pause, speed, money, camera position, selected character, squad names, basic
state, position, movement speed, and food-item count. It explicitly warns that
hunger, wounds, detailed inventory, modal UI state, and nearby entities remain
unimplemented.

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

The staged layout follows the current upstream HelloWorld example: an empty
`.mod` marker, `RE_Kenshi.json`, and the plugin DLL in one Kenshi mod folder.

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
- Select different squad members and verify `selected_character_id` changes.
- Move a character and verify position and movement speed change plausibly.
- Compare squad count and names against the UI.
- Leave the game running for ten minutes and inspect `kenshi.log` for plugin
  errors or hitches.

Do not enable live Python input until these checks pass. The source is based on
the pinned maintained headers and now compiles as a VS2010 SP1 `Release | x64`
DLL. It has not yet been loaded or field-tested inside the user's Kenshi
installation.
