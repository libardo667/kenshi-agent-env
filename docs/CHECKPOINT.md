# Checkpoint: native dialogue affordances and long-run readiness

This slice builds on native pause, blocking-interface cleanup, and the timed
Prospecting lifecycle by making every current dialogue reply an exact native
affordance. The planner now sees a plural ordered reply surface and can select
one only through the current dialogue target, zero-based index, and exact
caption. No pointer, click, cursor movement, or key is involved.

## Repository and authority

```text
parent commit          078d74e142340a448f530efd4695431c4a5c8146
integration branch     main
starting remote        origin/main at 1d53e57e787309975e75b710eba96b22d1feb12d
starting tree          clean
producer protocol      2.0.0
request schema         1.6
declared capabilities  52 loaded-world + 4 title-screen
```

The current authorities are `native/KenshiAgentTelemetry/KenshiAgentTelemetry.cpp`,
`native/KenshiAgentTelemetry/WorldTargetProtocol.cpp`, and
`src/kenshi_agent/core/telemetry.py`. Protocol 2.0 remains
strict: there is no reader for `TelemetrySnapshot.squad`, `native_control`,
`NativeControlState.active_command_id`, or the former `acknowledgements` shape.
Every consumer reads plural `controller_commands.commands`. The native producer
may retain at most one record only until **2026-09-20**; that temporary
producer-side exception must be deleted by the deadline.

## Removed probability oracle

- The producer no longer iterates all task vocabulary values through
  `getPlayerTaskProbability`. The API has no project call site and supplies no
  Protocol 2.0 evidence.
- `AdvertisedTaskSource` now admits only `menu`; generated schemas reject
  `odds`, and the old source has no compatibility reader.
- Generic character orders, first aid, discovered target actions, and nearby
  entity actions all use `ProbeMenuOrders`. Dispatch revalidates the exact
  task against a fresh menu probe.
- Resource operation remains available through exact resource type, capacity,
  and current-operator state. Contextual menu orders remain supplementary
  evidence, not capacity authority.
- The reduced crash artifact under
  `game_sources/research/context_menu_orders/live_evidence/` records the exact
  dump signature, decisive telemetry, binaries, request and acknowledgement,
  omitted-file hashes, replications, and final disposition.

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
  Continue invokes Kenshi's title handler directly. The request schema is 1.6
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

## Native safety-cleanup contract

- A healthy loaded session pauses through the Protocol 2.0 `pause` command,
  requires terminal `completed` / `world_paused`, and then observes a causally
  later fresh paused frame. A returned write or stale pause bit is not proof.
- Environment close and safety-supervisor handoff route through that same
  native command. Their receipts report no primitive input, and tests reject a
  hidden keyboard fallback when native identity is available.
- `./dev stop` and `./dev recover` close Prospecting, dialogue, inventory,
  trade, stats, management, message-box, and other blocking surfaces through
  one `close_active_interface` request. Success requires terminal `completed`
  / `active_interface_closed` plus later engine-owned interface state.
- The old safe-close inventory coordinate calculation, pointer click,
  controller input lease, and interrupted-state keyboard cleanup are deleted.
- Physical pause remains available only when telemetry cannot supply a fresh
  native identity. That is an explicit degraded/emergency boundary, not a
  compatibility reader or an ordinary production path.

## Native dialogue-affordance contract

- Protocol 1.6 adds `select_dialogue_option` with an exact dialogue target,
  zero-based reply index, and untruncated caption. Every other command must
  retain the strict `-1` / empty defaults for those two wire fields.
- The planner receives one `reply_N` affordance for every caption in the
  complete current ordered list. Native return-to-world remains available at
  the same time; opening dialogue no longer traps the agent between closing and
  reopening it.
- Binding, input-boundary revalidation, and game-thread dispatch independently
  require the same open target, in-range index, and exact caption. A reordered,
  replaced, missing, stale, or overlong option fails closed before selection.
- Native dispatch calls the public `Dialogue::replyClicked(int)` once. It is
  terminal only after a later game update proves `dialogue_closed`,
  `dialogue_target_changed`, or `dialogue_options_changed`.
- The earlier `PLAYER_TALK_TO` false negative is also closed: context actions
  expecting `PLAYER_TALK_TO` may terminate on the exact later dialogue target,
  even after the transient task goal has already cleared.
- Mock and replay ports implement the same operation surface. Strict fixtures,
  generated schemas/docs, registry reachability, and capability-to-wire mapping
  all include the command; there is no click or compatibility fallback.

## Evidence lanes

Source- and test-proven: every supported start source selects one of the three
strict native title commands; the Win32 handoff requires an exact visible
button/id pair; title/global commands accept empty selection; request fields are
exclusive; stale title telemetry is awaited; loaded sessions require their
cross-session acknowledgement; and `_perform_launch` contains no click, key,
hotkey, primitive-input, or input-lease delivery.

Build- and installed-proven: the Release x64 native fixture executable reported
`Native protocol fixtures and semantics passed.` The replacement build, staged
mod, installed mod, and preserved replacement have identical SHA-256. The old
installed DLL contains the decorated `getPlayerTaskProbability` import string;
the replacement DLL does not. The pre-removal DLL is preserved separately for
exact rollback.

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

The bounded regression run
`squin-probability-removal-regression-20260810-r1` then travelled natively from
The Hub to Squin. Travel completed at telemetry sequence 296 with 16 nearby
characters; the run reached 17, completed an exact character-target interaction
at sequence 353, and continued publishing through sequence 448 without a crash.
Final cleanup confirmed pause at 449 before deliberate close. Its reduced
artifact preserves the requests, acknowledgements, decisive frames, installed
binary, omitted-file hashes, run-level cleanup caveat, and final disposition.

The definitive interface-lifecycle regression is
`native-cleanup-interface-regression-20260810-r7`. Survey command
`cmd-b76743394dbe495cb5eb4d9b8ef9d110` was accepted at telemetry sequence 175
while the native command temporarily advanced the paused world, then completed
as `resource_survey_published` at 176 only after the real Prospecting widget had
populated, its five rows were copied, it remained hidden, and pause was
restored. The historical survey record says the concrete widget was visible at
capture; later telemetry and frame 2 say it was no longer open.

Character order `cmd-5642a486cd044d3595e6d8f7c8344015` then started
`PLAYER_TALK_TO` natively with zero primitives. Sequence 222 and frame 4 prove
the exact Barman dialogue opened with no Prospecting overlay. Native interface
close completed at 231, native pause completed at 245, and final sequence 262
was paused on the unobstructed world screen. The run had no rejected or aborted
plans; final cleanup reported `input_attempted=false` and
`input_executed=false`. The committed reduced artifact is
`game_sources/research/prospecting_window/live_evidence/prospecting-dialogue-lifecycle-20260810.json`.

The first clean 120-turn attempt,
`protocol-2-native-survival-soak-20260810-r2`, exercised that corrected survey
lifecycle and began resource work without a native crash. It stopped at turn 2
for a deterministic host-side contract failure: planner offer prose embedded
the complete engine operator-ID list, and two normal Protocol 2.0 stable IDs
made `AffordanceOffer.description` exceed its 500-character bound. Resource
offers now render only exact occupied/capacity counts; the typed operator IDs
remain in telemetry and are no longer duplicated into prose. A regression uses
two 187-character IDs, requires both resource operations to remain offered,
requires both descriptions to fit the bound, and rejects either ID appearing
in either description. This is test-proven soak readiness, not evidence of a
completed long run; a fresh 120-turn attempt remains required.

The bounded native dialogue regression is
`protocol-2-native-dialogue-regression-20260810-r1`. The exact-load fixture was
attested and the Mercenary Captain conversation exposed three plural reply
affordances plus native return-to-world. Command
`cmd-c533cd40cb084f2c989a009a409c78af` selected exact index 0 / “I'm looking to
hire some bodyguards” with zero primitives and completed only when the complete
list changed to one-day, two-day, and decline terms. Command
`cmd-b81386a9cdad4ac0b399201b4179ccc4` then selected exact “1 day [c.2,000]”
with zero primitives. Later engine state changed money from c.20,000 to
c.18,000, closed dialogue, returned to the world, and frame 5 rendered “Paid
c.2000”. This proves exact native selection, payment, and closure; it does not
prove the duration or conduct of the hired mercenaries.

The capability proof passed before the run was deliberately interrupted.
Automatic cleanup timed out because an unrelated mining command was already in
flight; a separate supported `./dev stop --timeout 30` then reported `Kenshi
closed from a fresh paused idle state.` The committed reduced artifact records
both the successful dialogue chain and that cleanup caveat at
`game_sources/research/dialogue_options/live_evidence/mercenary-hiring-dialogue-20260810.json`.

## DLL artifacts and exact reinstall commands

```text
pre-removal DLL sha256   c8e3da7572b2074db55c941acd1ff26bdc4d302a6b8c8f62bd20b10e9b55e083
pre-removal DLL size     430080
replacement DLL sha256   51226226dd80710ff16e5ef86708b750b6594bf0576b620d73b630f1775dfdf3
replacement DLL size     429056
replacement PDB sha256   a2fe570e8a76129cd597c4382e75c4a6d3d1545e07558d5a895ea8a6a06c6ec2
replacement PDB size     11127808
installed/staged parity  YES
conformance exe sha256   637cf2c0951f35caddd8f405f331301be7ed5e439a074564a5a686a91288700e
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

Reinstall the preserved probability-oracle-removal artifact:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-probability-oracle-removal\replacement\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Rollback this slice to the preserved crash-reproducing Protocol 2.0 DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-probability-oracle-removal\pre-cutover\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

The final native cleanup and Prospecting lifecycle artifacts supersede the
intermediate binaries above for the installed test candidate:

```text
pre-fix DLL sha256       51226226dd80710ff16e5ef86708b750b6594bf0576b620d73b630f1775dfdf3
pre-fix DLL size         429056
final DLL sha256         aecc380c672eeeda9203227cbe483ac5313736e3d5a52d8d9e681b6075aa00c1
final DLL size           430080
final PDB sha256         077052e0574f4d6bc885db0af878e08e7512acc74d4065b69b6aff12b7524545
final PDB size           11127808
installed/staged parity  YES
conformance exe sha256   0572c5eafb0801846a8db3fe5280f0158a478da6a33582ad560e12d224364daf
conformance exe size     311808
```

Reinstall the preserved final cleanup/lifecycle DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-prospecting-render-closure\replacement-final\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Rollback specifically to the preserved pre-fix DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-prospecting-render-closure\pre-fix\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

The installed Protocol 1.6 dialogue-affordance candidate and its immediate
pre-dialogue rollback are preserved separately:

```text
pre-dialogue DLL sha256  aecc380c672eeeda9203227cbe483ac5313736e3d5a52d8d9e681b6075aa00c1
pre-dialogue DLL size    430080
pre-dialogue PDB sha256  077052e0574f4d6bc885db0af878e08e7512acc74d4065b69b6aff12b7524545
pre-dialogue PDB size    11127808
dialogue DLL sha256      033cf6e489816644f5310eb38d90ffc4e625e4812f8ed41d79aac05d58e4dfdd
dialogue DLL size        435712
dialogue PDB sha256      9976a2e536e838ecc635ed2b29504b38fd9f686475530ab185c1c5ddaa2fd4d6
dialogue PDB size        11127808
installed parity         YES
conformance exe sha256   88e5535518c2633355dcea17d050f95313e4c6106664ee73c2a3172a149f1f37
conformance exe size     317440
```

Reinstall the live-proven dialogue-affordance DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-dialogue-affordances\replacement\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
```

Rollback exactly to the preserved pre-dialogue DLL:

```powershell
Copy-Item -LiteralPath 'C:\Users\levib\AppData\Local\KenshiAgent\backups\native\20260810-dialogue-affordances\pre-dialogue\KenshiAgentTelemetry.dll' -Destination 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi\mods\KenshiAgentTelemetry\KenshiAgentTelemetry.dll' -Force
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
