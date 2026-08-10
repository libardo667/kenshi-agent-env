# Checkpoint: fully native launch and save load

This slice removes the supported launcher's mouse and keyboard machinery. The
installed Kenshi bootstrap, its native settings dialog, the title screen, exact
save/Game Start transitions, post-load pause, and causal evidence now form one
strict launch path. Pointer-based gameplay fallbacks remain deleted; emergency
stop and final host-safety fallback remain a deliberately separate boundary.

## Repository and authority

```text
parent commit          344b39291f6db80f9dea762e9c9a1c10c7d80cb7
integration branch     main
starting remote        origin/main at 1d53e57e787309975e75b710eba96b22d1feb12d
starting tree          clean
producer protocol      2.0.0
request schema         1.5
declared capabilities  50 loaded-world + 4 title-screen
```

The current authorities are `src/kenshi_agent/tooling/live_dev.py`,
`src/kenshi_agent/core/transport.py`, and
`native/KenshiAgentTelemetry/KenshiAgentTelemetry.cpp`. Protocol 2.0 remains
strict: there is no reader for `TelemetrySnapshot.squad`, `native_control`,
`NativeControlState.active_command_id`, or the former `acknowledgements` shape.
Every consumer reads plural `controller_commands.commands`. The native producer
may retain at most one record only until **2026-09-20**; that temporary
producer-side exception must be deleted by the deadline.

## Native launch contract

- The supported process target is the installed
  `C:\Program Files (x86)\Steam\steamapps\common\Kenshi\kenshi_x64.exe`.
  Desktop shortcuts, `KENSHI_AGENT_SHORTCUT`, and direct selection of the
  archived `RE_Kenshi\Kenshi_x64.exe` are deleted from launch authority.
- Kenshi's small native settings window is resolved as one exact visible Win32
  `Button` named `OK` with control id `1003`. The launcher routes `WM_COMMAND`
  through the dialog's own MFC handler. It does not focus the window, move the
  cursor, synthesize a click, or send a key.
- Atomic replacement of `native_command.request.json` is the sole plug-in
  dispatch signal. The Ctrl+Shift+F10 trigger and its native polling branch are
  deleted.
- The strict title surface exposes `continue_game`, `load_game`, and
  `new_game`. Exact load carries only `save_name`; exact new game carries only
  `game_start_id`; Continue carries neither. All title requests have an empty
  recipient selection.
- `SaveManager::load` and `SaveManager::newGame` own exact transitions.
  Continue invokes Kenshi's title handler directly. The request schema is 1.5
  in Python, C++, fixtures, schema output, and the diagnostic dispatcher.
- A title transition changes identity sessions. Its exact acknowledgement is
  preserved across `GameWorld::resetGame`, becomes terminal as
  `world_session_loaded` in the first loaded frame, and is then retired. The
  launcher refuses a loaded session without that explicit cross-session record.
- Post-load pause is another request-file command with a causally later
  `world_paused` acknowledgement. No launch branch acquires an input lease.
- Fresh title authority is awaited independently of plug-in status so early
  startup cannot race a stale telemetry file.
- Scenario verification already consumes complete plural selection. It now
  anchors environment/danger evidence to the selected primary rather than
  incorrectly requiring exactly one selected character.

## Deleted launcher machinery

The old launcher no longer contains semantic control clicking, MyGUI startup
coordinates, carousel stepping, Enter-to-launch, desktop-shortcut discovery,
startup control labels, startup input takeover/countdown state, hotkey trigger
delivery, or launch input leases. Ordinary recovery UI and emergency/final
safety logic remain isolated from `_perform_launch`; they are not a launch
fallback.

## Evidence lanes

Source- and test-proven: every supported start source selects one of the three
strict native title commands; the Win32 handoff requires an exact visible
button/id pair; title/global commands accept empty selection; request fields are
exclusive; stale title telemetry is awaited; loaded sessions require their
cross-session acknowledgement; and `_perform_launch` contains no click, key,
hotkey, primitive-input, or input-lease delivery.

Build- and installed-proven: the Release x64 native fixture executable reported
`Native protocol fixtures and semantics passed.` The preserved final DLL,
current build, and installed mod have identical SHA-256. The pre-cutover DLL is
preserved separately for exact rollback.

Live-proven: `./dev launch --title --timeout 120` reached a fresh Protocol 2.0
title without physical input. A separate fresh Continue run loaded a new world
session and paused natively. The definitive exact-save run was:

```text
./dev launch --scenario native-launch-exact-load-20260810 --timeout 180
Kenshi launched, loaded, and paused. Scenario 'native-launch-exact-load-20260810' was fixture-attested.
```

The reduced committed artifact
`docs/reconstruction/native_launch_20260810.json` contains its manifest, exact
title/load/pause frames, request, terminal cross-session acknowledgement,
scenario attestation, hashes for omitted raw files, and final disposition.

## DLL artifacts and exact reinstall commands

```text
pre-cutover DLL sha256   f68f63889ad29bcb63a267bdbd746f56f200357dda193e58fee608223fa68913
pre-cutover DLL size     425472
native-launch DLL sha256 c8e3da7572b2074db55c941acd1ff26bdc4d302a6b8c8f62bd20b10e9b55e083
native-launch DLL size   430080
installed parity         YES
conformance exe sha256   c0138500b6105beebda3e95eab920ba8495d707db272ae1ee69611aa9e7c3ab2
conformance exe size     311808
```

Build:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_native.ps1
```

Install the current build directly:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\build\native\bin\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Reinstall the preserved native-launch artifact:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-native-launch\native-launch-final\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Rollback this slice to the preserved pre-cutover Protocol 2.0 DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-native-launch\pre-cutover\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

## Withheld and named follow-on work

- **By 2026-09-20:** replace the native singleton publication bridge with the
  full retained-command registry and delete its `at most one` exception.
- True total-party-loss body-shift recovery remains unproven. Elective nearby
  shifting and empty-roster authoring do not upgrade that claim.
- Alternate host configurations and a 100+ turn open-ended run require their
  own evidence. This launch proof establishes startup/save-load behavior, not
  long-duration agent stability.

## Verification

The final candidate passed the portable gate on 2026-08-10 from the repository
root with:

```bash
UV_CACHE_DIR=/tmp/kae-uv-cache ./dev verify-portable
```

It covers locked dependency sync, Ruff, strict mypy, research-package
validation, schema and generated-document freshness, the complete pytest suite,
and `git diff --check`.
