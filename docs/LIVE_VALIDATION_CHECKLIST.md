# Live validation checklist

When the human explicitly hands the whole desktop to the agent, pass
`--exclusive-input-session` together with `--execute-live-actions`. That mode
keeps Kenshi foreground and leaves the guest cursor in place so the run is
observable on a single display. Omit it during normal shared-computer use; the
polite input lease then waits for idle input and restores the previous context.
For a `native_assisted` profile, also pass
`--acknowledge-native-assisted-control`; do not use that acknowledgement for an
interface-only evidence run.

Record evidence for every item. Do not mark an item complete based on code
inspection alone.

## Native loading

- [x] Exact Kenshi executable version recorded.
- [x] Exact RE_Kenshi release recorded.
- [x] Exact KenshiLib dependency commit or release recorded.
- [x] Plugin built Release x64 with the required v100 toolset.
- [x] Plugin loads without an error in the RE_Kenshi/Kenshi logs.
- [x] `plugin_status.json` reaches `ready`.
- [ ] Uninstalling the plugin returns Kenshi to its prior behavior.

## Telemetry

- [x] Snapshot parses against `schemas/telemetry.schema.json`.
- [x] Sequence increases at approximately two hertz.
- [x] Capture timestamp is UTC and fresh.
- [ ] Pause toggles match the UI for at least 20 trials.
- [ ] Speed changes match the UI for at least 20 trials.
- [ ] Squad count and names match after recruit, dismiss, reorder, KO, and death.
- [ ] Selection identity follows portrait selection.
- [ ] Character position moves plausibly and does not jump on zone transitions.
- [ ] Saving/loading and returning to the title screen do not crash or retain
      stale pointers.
- [x] Missing capabilities remain absent rather than fabricated.

### Protocol 0.5 title-screen semantic lifecycle

- [x] First 1920x1080 load reached a responsive title screen without the
      optional RE_Kenshi settings panel. The 185,344-byte candidate
      (`a1ea4c2a3c6c6e596b3bc8654b901511da1808979d49758d49e852bd0ad6da24`)
      reported fresh `ready`, but retained the prior telemetry snapshot because
      `PlayerInterface::update` does not run before a save is loaded.
- [x] The semantic launcher failed closed after its bounded timeout with zero
      title pointer input. A read-only capture showed the Continue, New Game,
      Load Game, Import Game, Options, Credits, and Exit title controls at
      1920x1080; no gameplay state was loaded or changed.
- [x] Reject the direct MyGUI exported-function detour. Its pinned Release x64
      output was 186,368 bytes, SHA-256
      `ace964357eaa93c8844d1b564447bf85650dba97434f67f7875cdb03f1de88d5`.
      It reported plug-in `ready`, then crashed during startup. The exact dump,
      matching DLL/PDB, logs, configs, and screenshot are retained under
      `runs/p0-title-telemetry-frame-hook-crash-20260723T224758Z/`.
- [x] Preserve the complete pre-hotfix package under
      `runs/p0-title-telemetry-hotfix-preinstall-20260723T224713Z/`, close the
      reporter without submitting it, and restore the installed DLL to
      `a1ea4c2a3c6c6e596b3bc8654b901511da1808979d49758d49e852bd0ad6da24`.
- [x] Build and test the replacement that uses MyGUI's supported
      `eventFrameStart` subscription, retains native-command monitoring on
      `PlayerInterface::update`, and fails closed if MyGUI is unavailable.
      Full offline tests are 211 passing; Ruff, mypy, compile, and doctor pass.
      Pinned Release x64 output is 189,440 bytes, SHA-256
      `6bb2af414406cfd708635b74ecb8e742233a556dcb70724ef916e058a5c5da0c`.
- [x] Back up the restored package under
      `runs/p0-title-telemetry-event-subscription-preinstall-20260723T225933Z/`,
      install the exact event-subscription candidate while Kenshi is stopped,
      and verify its hash.
- [x] Reject the event-subscription candidate after it reproduced the immediate
      startup crash without publishing title telemetry. Preserve its dump,
      matching DLL/PDB, logs, configs, and screenshot under
      `runs/p0-title-telemetry-event-subscription-crash-20260723T230002Z/`;
      terminate the crashed process and restore the installed DLL to
      `a1ea4c2a3c6c6e596b3bc8654b901511da1808979d49758d49e852bd0ad6da24`.
- [x] Build the split-lifecycle replacement: hook Kenshi's pinned
      `TitleScreen::_NV_update`, emit a minimal title-only snapshot, and use
      `PlayerInterface::update` only after `GameWorld::initialized`. It must not
      modify MyGUI's function or delegate machinery. Pinned Release x64 output
      is 188,416 bytes, SHA-256
      `33e54224f4b4729ba5b96c85db8b8f81137b5e153a7a97b3d4b8125813a89a7c`.
- [x] Back up the restored package under
      `runs/p0-title-player-split-preinstall-20260723T231348Z/`, install the
      exact split-lifecycle candidate while Kenshi is stopped, and verify its
      installed hash byte-for-byte.
- [x] Repeat 1920x1080 startup and prove fresh advancing title telemetry,
      unique semantic Continue/save controls, a loaded squad, and causally
      confirmed pause. The no-Continue canary advanced title sequence
      28→46→134, exposed one `CONTINUE` button at normalized bounds
      `(0.2604167, 0.1388889)–(0.4166667, 0.2027778)`, and closed normally. The
      full launcher then loaded Hep, advanced world sequence 36→245, and
      returned paused without issuing a native command.
- [x] Reconfirm the reduced graphics profile after both launches and inspect
      RE_Kenshi/Kenshi logs, Windows Application events, process memory, and
      crash artifacts. One informational `RADAR_PRE_LEAK_64` event appeared
      during world load, but memory subsequently fell while the process stayed
      responsive; no Application Error or fresh crash dump appeared. GPU-local
      process accounting was unavailable and remains an evidence gap.
- [x] Separate the later Steam DLL alert from launcher input classification.
      Steam logged the local client off as `Logged In Elsewhere`; the user
      moved the mouse to close the alert, and both launcher attempts correctly
      cancelled before sending startup input. After local Steam reached
      `Logged On`, semantic startup again loaded the save and confirmed pause.
- [x] Preserve the fast authenticated `BAD STUFF` recurrence independently of
      the Steam incident. At 16:55:28, `kenshi.log` reported
      `DXGI_ERROR_DEVICE_REMOVED` / `DXGI_ERROR_DRIVER_INTERNAL_ERROR` while
      rendering `waterDistant`, despite the persisted reduced profile. Exact
      evidence is under
      `runs/p0-steam-recovery-device-reset-20260723T235528Z/`. No gameplay or
      native command was issued, and the crashed process was stopped.
- [x] Install and exactly verify the reversible `iris-xe-stability-v2`
      candidate while Kenshi is stopped. Its timestamped pre-profile backup is
      recorded in the stability incident document. A real no-launch preflight
      confirmed Steam `Logged On`, at least 4096 MiB free physical memory, and
      an exact installed profile.
- [x] Add portable rejection coverage for profile drift, low memory, Steam
      present-but-not-logged-on, duplicate Kenshi launch, unfiltered
      `BAD STUFF`/Steam DLL dialogs, and non-advancing post-load telemetry.
      Launcher success now requires a configured 45-second fresh loaded-paused
      health window.
- [x] Run one supervised no-gameplay smoke with `iris-xe-stability-v2`. It
      **failed**. Launch cleared the 45-second post-load health window, but a
      zero-input sampling soak reproduced `BAD STUFF` at 141 seconds while the
      game was paused. Private memory was flat at 3.741 GiB across every
      sample and free physical memory held near 1.8 GiB. Evidence is under
      `runs/p0-iris-xe-v2-smoke-20260724T005717Z/`; the profile was verified
      still installed after the failure and the crashed process was stopped.
- [x] Compare survival across the three measured profiles: ~40 minutes,
      46 seconds, and ~3.7 minutes under progressively more aggressive
      reduction. Graphics reduction is not the operative variable, so the
      settings hypothesis is falsified and no further tuning slice is planned.
- [ ] Decide the next stability experiment at the operator level, not as a
      repository slice: Intel driver `32.0.101.6737` has never been changed,
      Windows `TdrDelay`/`TdrLevel` are unset defaults, host headroom is
      untested with the machine otherwise idle, and a discrete-GPU host would
      bypass the fault entirely. Do not re-open live gameplay gates until one
      of these produces a host that survives a bounded run.
- [ ] Repeat the semantic startup boundary at one alternate resolution, then
      restore and re-verify the intended 1920x1080 profile.

### Evidence from 2026-07-22 smoke test

- RE_Kenshi logged `KenshiAgentTelemetry -> KenshiAgentTelemetry.dll`, then the
  plugin logged `telemetry hook installed` without a plugin error.
- A snapshot parsed through `TelemetrySnapshot`; sequence advanced from 23 to
  31 in four seconds and its UTC timestamps advanced with it.
- The disposable Wanderer `Sand` appeared as selected with 1,000 cats. A manual
  move produced movement speed 53.15 and a 7.89-unit position change. A manual
  pause produced `paused: true` and speed multiplier 0.
- The position check remains open because zone transitions were not tested.
- KenshiLib's raw `isGettingEaten` byte was true on the healthy new character.
  That unvalidated field was removed, a warning now names the omission, and a
  clean rebuild/load confirmed it absent.
- A zero-byte `.mod` marker was rejected as invalid and produced the first-run
  crash. The staging script now emits the exact 46-byte stub shared by current
  upstream examples, guarded by a hash regression test. The corrected run
  loaded cleanly.
- A later unsigned telemetry rebuild with nearby-character and UI fields was
  found by RE_Kenshi but blocked before load by Smart App Control enforcement.
  Code Integrity events 3033 and 3077 named `KenshiAgentTelemetry.dll`; the
  file had no `Zone.Identifier`, so downloaded-file unblocking was not the
  remedy. The game was immediately paused and the signing/development-host
  decision remains open.

## Capture and input

- [x] Window title filter identifies only Kenshi.
- [x] Client screenshot excludes unrelated desktop applications.
- [x] Screenshot dimensions match calibration.
- [ ] Controller and Kenshi run at equal integrity levels.
- [ ] F12 prevents the next primitive action.
- [x] Dry-run logs proposed actions without sending input.
- [x] One key action works in a disposable save.
- [ ] A supervised launcher interruption emits no further input and does not
      reclaim focus. Portable zero-input tests pass; Windows revalidation is
      pending after the 1280x720 focus-retry incident.
- [ ] At 1920x1080 and one alternate client size, the video launcher advances
      by Enter, RE_Kenshi does not open its settings panel, and exact current
      `Continue`/save semantic controls load the save without a fixed startup
      coordinate.
- [ ] Human input during a continuous live run produces a visible
      `human_control` banner and confirmed pause. After three quiet seconds the
      five-second takeover countdown appears; new input resets it, F12 disarms
      it, and a completed countdown causes a fresh replan rather than resuming
      the cancelled plan.
- [x] A mismatched calibrated client size blocks pointer input inside the
      acquired input lease in portable tests.
- [ ] One calibrated click works for 50 repeated trials without drift.
- [ ] Loss of foreground focus aborts safely.

## Agent loop

- [x] Planner receives fresh telemetry and the matching screenshot.
- [x] Planner output validates without repair.
- [ ] A failed action is not reported as successful before observation.
- [ ] Duplicate failed procedures are detected.
- [ ] Stale telemetry triggers pause or stop.
- [ ] Every episode produces a complete JSONL log and final summary.

### Evidence from 2026-07-23 food-procurement proof

This historical proof used the native vendor command and is classified
`native_assisted`; its older run header did not yet record that field.

- Camera-relative bearing was validated against reciprocal Q/E orbits. A
  negative off-screen bearing moves toward zero with `orbit_camera_right`; the
  live orbit step is about 13 degrees.
- The native `approach_confirmed_vendor` command acknowledged sequence 1 with
  result `issued` and target `Barman`. One two-second pulse reduced distance
  from 237.48 to 90.87; the next opened dialogue.
- Selecting the calibrated `Show me your goods` option created exactly one
  `ShopTrader`. Lifecycle telemetry mapped its owner pointer to Barman and set
  `shop_inventory_owner: true`.
- A hover at normalized `(0.316, 0.357)` visibly identified a Meatwrap as
  `[Food]`, 50 nutrition, value 649 cats. One purchase changed money from 1,000
  to 351 and selected food items from 0 to 1. The game was confirmed paused.

### Evidence from 2026-07-23 stable-identity boundary

- Protocol `0.2.0` loaded through RE_Kenshi, reached a fresh `ready` status,
  parsed strictly, loaded the current save, and ended paused.
- The first live pass exposed a real selection mismatch: Kenshi's overloaded
  handle equality returned false even though the selected-set and squad handles
  serialized to the same components. Strict validation rejected the snapshot.
  Direct component equality replaced that operator; the pinned VS2010 build and
  second live load passed.
- `selected_character_id`, the one-member `selected_character_ids` set, and the
  one true squad `selected` flag all named the same stable ID.
- All 18 nearby characters had distinct IDs. Four characters named
  `Ninja Guard` had four IDs, proving display names were not identity keys.
- A paused camera orbit changed camera state while the identity session,
  selection, and complete nearby ID set stayed unchanged across later
  snapshots. The native query retained its order, so this run does not claim a
  live list-reordering observation.
- The installed identity-test DLL SHA-256 was
  `2227f3d97124149917d1c5736fb69bf29100b4ac1d6af4badcb76455ff478e16`.
  The prior installed plugin was backed up under
  `%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T1008-stable-identity`.
- Later in the same process, Kenshi reported
  `DXGI_ERROR_DEVICE_REMOVED` with
  `DXGI_ERROR_DRIVER_INTERNAL_ERROR`. The prior DLL reproduced the identical
  renderer failure during a controlled ten-minute baseline and normal-exit
  test, so the crash is not unique to stable identity.
- After changing texture quality from Medium to Low and water reflections from
  Everything to Disabled, the identity DLL held fresh telemetry for more than
  ten minutes and exited normally. See the
  [live stability incident](LIVE_STABILITY_INCIDENT_20260723.md) for exact
  hashes, memory ranges, rollback path, and the limits of that conclusion.

### Protocol 0.3 causal-command boundary

- [x] Portable strict request/acknowledgement, old-ack isolation, exact
      rejection, dispatch propagation, replay metrics, and source-contract
      tests pass.
- [x] The pinned VS2010 SP1 Release x64 project builds offline.
- [x] Install the exact built DLL and record its SHA-256 plus rollback copy.
- [x] Load protocol `0.3.0`, confirm fresh strict telemetry, and keep Kenshi
      paused before command probes.
- [x] Send one stale-revision request and record a later keyed
      `stale_revision` rejection with no movement.
- [x] Send one current exact-target request and record the request command ID,
      based-on revision, identity session, exact selected/target IDs, matching
      acceptance, final terminal status, and confirmed pause.
- [x] Confirm no new renderer error or plugin error during the bounded run and
      close Kenshi normally.

Evidence from the supervised 2026-07-23 run:

- Installed DLL SHA-256 was
  `9bbeea1826216365c5492ee94db4b692848a105fbb36bc794b02723e953a293b`.
  The prior `0.2.0` DLL was copied to
  `%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T184326Z-p5-causal`.
- RE_Kenshi loaded `KenshiAgentTelemetry.dll`, the plugin logged
  `telemetry hook installed`, protocol `0.3.0` parsed strictly, and the loaded
  world began paused with an empty acknowledgement ring.
- Identity session
  `session-c28ef640df456a20-0000000000000002` bound selected Hep ID
  `entity-c28ef640df456a20-0000000000000002-00000001-00000001-4e74e700-00000001-b1298c00`
  to Barman target ID
  `entity-c28ef640df456a20-0000000000000002-00000001-00000009-7994cd00-00000001-53ed9c80`.
- Stale command `cmd-fc8d78b68bf54babb8d6f360a14f4bbc` used basis
  sequence 168 while the current snapshot was 169. Telemetry 170 rejected it
  as `stale_revision`; selected position remained exactly
  `(-51061.47, 1524.116, 2981.53)`, no command became active, and pause remained
  true.
- Current command `cmd-77f7735532484c11b0be9cb46fb29081` used basis 248
  and received exact-target acceptance at sequence 249. The initial bounded
  four-second pulse reduced target distance from 329.2628 to 107.3233 and
  confirmed pause. Two supervised continuation pulses advanced the already
  issued Kenshi task without another command/hotkey; the second auto-paused as
  exact Barman dialogue opened.
- Telemetry 423 completed that same command with
  `exact_dialogue_target_open`, cleared `active_command_id`, and remained
  paused. Telemetry 475 retained both exact acknowledgements, the dialogue
  screen, and the final 25.65993 target distance.
- The continuation pulses were explicit operator test intervention, not new
  planner commands. They are recorded so the proof is not misrepresented as
  one uninterrupted four-second action.
- Kenshi accepted a normal window-close request and exited. The session's
  Kenshi/RE_Kenshi logs and recent Windows Application events contained no
  plugin error, `DXGI_ERROR_DEVICE_REMOVED`, driver-internal error, BAD STUFF
  message, or crash event.

### Evidence from 2026-07-22 live dry-run

- The initial title filter `Kenshi` also matched a terminal opened in the
  `kenshi-agent-env` checkout. `Kenshi 1.0.` uniquely selected the game, first
  as a 482x543 launcher and then as a 1920x1080 client.
- Client-area capture excluded the desktop and other applications. The bounded
  episode wrote five 1920x1080 PNG frames with five distinct SHA-256 hashes.
- All five observations had fresh telemetry; sequence advanced from 298 to 318.
  The heuristic planner produced four valid `set_speed` decisions.
- All four receipts recorded `accepted: true`, `executed: false`, and
  `dry_run: true` with `Live action withheld by the dry-run safety gate.` Kenshi
  remained at speed multiplier 1 throughout and after the run.
- The episode wrote `run_started`, five observations, four decisions, four
  receipts, and `run_finished` to one JSONL file. Broader repeated-episode
  validation remains open.
- The first attempt exposed SQLite WAL locking failure on the WSL UNC path.
  Live memory now defaults to Windows-local `%LOCALAPPDATA%\KenshiAgent\state`;
  screenshots and JSONL artifacts remain in the ignored repo `runs` directory.

### Evidence from 2026-07-22 active burn-in

- The initial active run exposed two false-positive success paths: desktop
  capture recorded a foreground Chrome window, and virtual-key `SendInput`
  events were accepted by Windows but ignored by Kenshi.
- Capture now focuses the uniquely matched Kenshi window before `ImageGrab`.
  A validation frame showed Lekko and the Kenshi client rather than Chrome.
- Keyboard input now uses hardware scan codes. A controlled Space probe changed
  plugin telemetry from `paused: false` to `true`; a second probe restored it to
  `false`.
- Run `20260722T185241.001241Z` executed `open_inventory` and `open_map`, then
  safely rejected Terra's raw Escape request. Escape was subsequently added as
  the audited `close_overlay` skill instead of allowing arbitrary keys.
- Run `20260722T185358.928132Z` completed six active steps: pause, three overlay
  closes, map open, and map close. Every receipt was executed and non-dry-run;
  no raw pointer, movement, combat, purchasing, or save action was permitted.
- A paused 1920x1080 world frame and a controlled map-open frame were used to
  calibrate separate right-click envelopes. Fine movement is limited to
  normalized `x=0.15..0.85`, `y=0.15..0.65`; map travel is inset to
  `x=0.30..0.68`, `y=0.16..0.69`. The map was closed again after capture.
- The guard now enforces those per-skill envelopes and direct clicks remain
  blocked.
- A supervised fine-movement trial reached speed 54.0 and moved Lekko about
  26.7 world units toward nearby visible terrain before the game was re-paused.
- A later regression showed that zero-duration `Mouse2` down/up events could be
  accepted by Windows but missed by Kenshi's per-frame command polling. A
  120 ms held right-click queued `Goal: Move order` while paused, and a guarded
  live probe moved Lekko about 43 world units before telemetry-confirmed pause.
  Run `held-right-click-runtime-proof-20260723` then validated the production
  macro path with another roughly 39-unit move and fresh paused telemetry.
- The first map trial exposed a real cursor race: physical mouse movement could
  interleave between separate synthetic move and button calls, sending the
  destination to the prior pointer location. The controller now submits the
  absolute move and right-button events as one `SendInput` batch.
- The corrected coarse-map trial placed the pointer at the requested pixel,
  reached speed 72.6, and moved Lekko about 165 world units toward a destination
  southeast of The Hub before re-pausing. The map was closed and subsequent
  telemetry confirmed the final position remained stable.
- These trials validate the two paths once, not the 50 repetitions required by
  the calibrated-click checklist item, so that item remains open.
- Run `20260722T201337.162004Z` validated the autonomous coarse-pulse contract:
  one map skill clicked the bounded destination, closed the map, confirmed
  unpause, advanced exactly 2.00 seconds, and confirmed re-pause before returning
  its receipt. Lekko moved about 114 world units and the final frame showed the
  closed-map world view with `paused: true` telemetry.
- Run `20260722T201615.824597Z` completed 30 Terra decisions: 14 fine movement
  pulses, two coarse map pulses, 12 `focus_selected` recoveries, and two map
  opens. It produced 30 executed receipts, zero safety rejections, zero
  environment errors, and `paused: true` in every observation. Lekko moved a
  net 969 world units while the objective kept exploration within The Hub.

### Protocol 0.4 conditional food chain

- [x] Deterministic live-shaped tests cover the three-action
      approach/dialogue/inspection plan, the later one-action purchase plan,
      exact target and tooltip binding, exact deltas, and default-disabled
      policy.
- [x] Full Python tests, lint, types, compile, and schema export pass.
- [x] The pinned VS2010 SP1 Release x64 project builds with the required MyGUI
      import library. Offline DLL is 182,784 bytes, SHA-256
      `64a3cf3c22fc4ee04152c6a70a143f16cb59e82ebb8d62e5a2cc885acfb77cfe`.
- [x] Back up the full installed protocol `0.3.0` plug-in at
      `%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T193734Z-p6-protocol-0.4`,
      install that exact `0.4.0` artifact, and verify the installed hash before
      launch. The backup DLL is 175,104 bytes with SHA-256
      `9bbeea1826216365c5492ee94db4b692848a105fbb36bc794b02723e953a293b`;
      the installed DLL is 182,784 bytes with the pinned `0.4.0` hash above.
- [x] Launch Kenshi, confirm plugin `ready`, strict fresh protocol `0.4.0`,
      increasing sequence, authoritative `game.time`, and paused state.
      Read-only samples advanced 127 -> 130 -> 132; a strict CLI parse at 168
      reported `elapsed_minutes: 2063.742`, `paused: true`, and all new
      dialogue/tooltip capabilities.
- [x] Recover the native snapshot stream after the first long live session
      stopped at sequence 3985 while Kenshi remained foreground, responsive,
      paused, and visually intact. The safety supervisor cancelled the pending
      planner call before any action. The replaced protocol `0.4.0` plug-in is
      backed up at
      `%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T202819Z-p6-stream-hotfix`.
      The exception-safe/retry-bounded hotfix is 183,296 bytes, SHA-256
      `0096082215cbc1f842a8947291570328481c78cab9c23b8ae00a4dcdf6e888a3`;
      it loaded ready and fresh samples advanced 61 -> 69 -> 78 after relaunch.
- [ ] Confirm closed dialogue reports null target/options; open exact Barman
      dialogue reports his stable target ID and option zero exactly
      `Show me your goods.`.
- [ ] Confirm the trade screen reports one exact shop owner. Hover one food
      item and compare tooltip text plus normalized source bounds with the
      visible UI.
- [ ] Run the conditional continuous chain with all existing live/native gates
      plus `--acknowledge-continuous-live`; require one response to drive
      approach/dialogue/inspection and one later response to buy at most once.
      Seven no-input hosted preflights sent zero native commands and left money,
      food, and pause unchanged. `xhigh` exceeded the 90-second timeout; `high`
      returned after 62.70 seconds but used unsupported shorthand condition
      paths. Medium run
      `p6-live-continuous-dry-medium-20260723T201345Z` returned in 31.45
      seconds and narrowed the remaining schema gap to the unused conditional
      shape of `exists`; that operator is now absent from the schema. The
      following medium call was cancelled after 18.91 seconds by the independent
      sequence-stall supervisor; the native stream hotfix above was installed
      before execution is retried. Run
      `p6-live-continuous-dry-medium-20260723T203207Z` then returned a strict
      typed plan in 23.95 seconds with the repaired stream healthy; policy
      rejected redundant global target annotations, an undersized game-time
      budget, and its naturally advanced exact revision. Those shapes are now
      canonicalized/enforced, and only the live food policy can rebase
      sequence-only latency when its complete phase fence is unchanged. The
      follow-up contract uses medium reasoning, dynamic output-token ceilings,
      a schema-enumerated condition vocabulary, and bounded planner failure
      logging. Run `p6-live-continuous-dry-medium-20260723T204004Z` returned a
      structurally correct phase plan in 25.20 seconds and was stopped only
      because transient client capture dimensions changed from 1920x1080 to
      null; policy-authoritative UI state was identical. The fence now excludes
      those dimensions, and trusted policy code compiles canonical safety
      scaffolding after the proposed action structure, target, and arguments
      match.
- [ ] Record exact pre/post money, selected food count, pause, action count,
      strategic-call count, and final plan lifecycle.
- [ ] Exercise one safe preemption (F12 or human input), confirm no repeated
      sensitive action, and confirm cleanup/final pause.
- [ ] Inspect `kenshi.log`, Windows Application events, and the BAD STUFF/
      renderer incident surfaces before calling the slice live-validated.
